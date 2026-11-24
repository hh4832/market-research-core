"""Volume-participation basis: known volume at the candidate's earliest open (synthetic data only).

Under CONSERVATIVE_LABEL_AGNOSTIC a 1m row labelled L has bar_start L-1m and modeled_available_at L+1m, so the
reference bar of a candidate labelled L is the row labelled L-2m. An order with signal/order time 10:01 first fills
on the row labelled 10:03 (bar_start 10:02), whose reference is the row labelled 10:01.
"""
import random

import pandas as pd
import pytest

from market_research_core.execution import ExecutionConfig, ExecutionEngine
from test_m3a4_final import config, order, profile, source

DAY = '2024-01-02'
FILL, REF, NEXT_FILL, NEXT_REF = f'{DAY} 10:03', f'{DAY} 10:01', f'{DAY} 10:04', f'{DAY} 10:02'
KNOWN = 'LAST_COMPLETED_AT_FILL_OPEN'


def ts(label):
    return pd.Timestamp(label, tz='America/New_York')


def bars(volumes=None, drop=()):
    from market_research_core.temporal import prepare_conservative_bars
    raw = source()
    for label, v in (volumes or {}).items():
        raw.loc[raw.source_timestamp.eq(ts(label)), 'volume'] = float(v)
    raw = raw[~raw.source_timestamp.isin([ts(d) for d in drop])]
    return prepare_conservative_bars(raw)


def run(frame, qty, basis=KNOWN, cap=.01, tif='NEXT_BAR'):
    return ExecutionEngine().run(frame, [order(f'{DAY} 10:01', qty=qty, time_in_force=tif)],
                                 cost_profile=profile(volume_participation_cap=cap),
                                 config=config(participation_volume_basis=basis))


def test_default_is_legacy_and_flagged():
    r = run(bars(), 1, basis='FILL_BAR_REALIZED')
    assert ExecutionConfig.__dataclass_fields__['participation_volume_basis'].default == 'FILL_BAR_REALIZED'
    assert r.provenance['participation_volume_basis'] == 'FILL_BAR_REALIZED'
    assert r.provenance['participation_lookahead'] == 'FILL_BAR_REALIZED_VOLUME_USED'
    assert 'participation_volume_basis' not in r.fills.columns  # legacy fill rows unchanged


def test_lookahead_case_fixed_quiet_reference_busy_fill_bar():
    frame = bars({REF: 100, FILL: 10_000})
    legacy = run(frame, 8, basis='FILL_BAR_REALIZED')
    assert len(legacy.fills) == 1 and legacy.fills.exec_ts.iloc[0] == ts(FILL) - pd.Timedelta(minutes=1)
    known = run(frame, 8)
    assert known.fills.empty
    rej = known.rejections.iloc[0]
    assert rej.reason == 'KNOWN_VOLUME_PARTICIPATION_EXCEEDED'
    assert rej.participation_reference_volume == 100 and rej.participation_cap == .01
    assert rej.participation_reference_modeled_available_at == ts(REF) + pd.Timedelta(minutes=1)


def test_reverse_case_busy_reference_quiet_fill_bar():
    frame = bars({REF: 10_000, FILL: 100})
    assert set(run(frame, 8, basis='FILL_BAR_REALIZED').rejections.reason) == {'VOLUME_PARTICIPATION_EXCEEDED'}
    known = run(frame, 8)
    assert len(known.fills) == 1 and known.fills.exec_ts.iloc[0] == ts(FILL) - pd.Timedelta(minutes=1)
    f = known.fills.iloc[0]
    assert f.participation_volume_basis == KNOWN and f.participation_reference_volume == 10_000
    assert known.provenance['participation_contract'] == 'known-volume-participation-v1'
    assert 'participation_lookahead' not in known.provenance


def test_boundary_is_inclusive():
    frame = bars({REF: 800})
    assert len(run(frame, 8).fills) == 1
    assert run(frame, 8.0001).rejections.iloc[0].reason == 'KNOWN_VOLUME_PARTICIPATION_EXCEEDED'


def test_missing_reference_minute_rejects_without_fallback():
    r = run(bars(drop=[REF]), 1)
    assert r.fills.empty
    rej = r.rejections.iloc[0]
    assert rej.reason == 'KNOWN_VOLUME_UNAVAILABLE' and rej.participation_reference_volume is None


def test_day_order_moves_to_next_candidate_and_expires_otherwise():
    r = run(bars({REF: 100}), 8, tif='DAY')
    assert list(r.rejections.reason) == ['KNOWN_VOLUME_PARTICIPATION_EXCEEDED']
    assert r.fills.exec_ts.iloc[0] == ts(NEXT_FILL) - pd.Timedelta(minutes=1)
    assert r.fills.participation_reference_volume.iloc[0] == 1000
    quiet = {f'{DAY} 10:{m:02d}': 100 for m in range(10)}
    r = run(bars(quiet), 8, tif='DAY')
    assert r.fills.empty and set(r.rejections.reason) == {'KNOWN_VOLUME_PARTICIPATION_EXCEEDED', 'ORDER_EXPIRED'}
    assert run(bars({REF: 100}), 8, tif='NEXT_BAR').rejections.shape[0] == 1


def test_fill_bar_own_volume_never_changes_the_outcome():
    rng = random.Random(20261124)
    for ref in (100, 800, 799, 5000):
        outcomes = set()
        for _ in range(12):
            r = run(bars({REF: ref, FILL: rng.choice([1, 50, 799, 800, 10**6])}), 8)
            outcomes.add((len(r.fills), tuple(r.rejections.reason) if len(r.rejections) else ()))
        assert len(outcomes) == 1, (ref, outcomes)


def test_no_cap_means_no_participation_check():
    r = run(bars({REF: 1}), 8, cap=None)
    assert len(r.fills) == 1 and 'participation_volume_basis' not in r.fills.columns


@pytest.mark.parametrize('policy', ['OBSERVED', 'BAR_END_STRICT_LATER', 'UNKNOWN'])
def test_known_basis_requires_conservative_mode(policy):
    with pytest.raises(ValueError, match='CONSERVATIVE_LABEL_AGNOSTIC'):
        ExecutionConfig(initial_cash=1, bar_label='START', session_semantics='CONFIRMED',
                        timestamp_timezone='America/New_York', available_at_policy=policy,
                        participation_volume_basis=KNOWN)


def test_unknown_basis_refused():
    with pytest.raises(ValueError, match='unsupported participation_volume_basis'):
        config(participation_volume_basis='FILL_BAR_PREVIOUS')


def test_dated_cost_schedule_cap_is_used_per_fill_date(tmp_path):
    import copy
    from dataclasses import asdict
    from market_research_core.tsm_research_costs import build_tsm_research_profile
    from test_dated_costs import SCENARIOS, cost_records, rate, schedule, sealed
    sec, taf, pins = cost_records(tmp_path)
    base = build_tsm_research_profile(sec, taf, **pins)
    assert base.volume_participation_cap == .01
    doc = sealed(dict(contract='tsm-dated-trading-costs-v1', schedule_id='SYNTHETIC-TSM-COSTS', version='1.0.0',
                      schedule_sha256=None, scope='RESEARCH_ONLY', evidence_kind='SYNTHETIC_FIXTURE', symbol='TSM',
                      market='IBKR_EQUITY', timezone='America/New_York', coverage={'start': DAY, 'end': DAY},
                      scenarios=copy.deepcopy(SCENARIOS), base_profile=asdict(base), base_profile_sha256=base.sha256,
                      sec_rates=[rate('SEC', DAY, DAY, '0.00001')], taf_rates=[rate('TAF', DAY, DAY, '0.0002')]))
    s = schedule(doc)
    frame = bars({REF: 100, FILL: 10_000})
    out = {}
    for basis in ('FILL_BAR_REALIZED', KNOWN):
        out[basis] = ExecutionEngine().run(
            frame, [order(f'{DAY} 10:01', qty=8, time_in_force='NEXT_BAR')], cost_profile=s.base_profile,
            cost_schedule=s, config=config(participation_volume_basis=basis, dataset_snapshot_id='SYNTHETIC_KNOWN_VOLUME'))
    assert len(out['FILL_BAR_REALIZED'].fills) == 1
    rej = out[KNOWN].rejections.iloc[0]
    assert rej.reason == 'KNOWN_VOLUME_PARTICIPATION_EXCEEDED' and rej.participation_cap == .01
