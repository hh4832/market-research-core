"""Offline official-byte and known-answer history checks; no strategy or market data."""
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
from market_research_core.dated_costs import DatedCostSchedule, SCENARIOS

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT/'docs/evidence/phase5c1_official_rates'
DOC = json.loads((ROOT/'configs/research/tsm_official_dated_costs_2017_2024.json').read_text())
INVENTORY = json.loads((EVIDENCE/'source_inventory.json').read_text())['sources']
PIN = (EVIDENCE/'SCHEDULE_SHA256').read_text().strip()
SEC = [('2017-01-01','0.0000218'), ('2017-07-04','0.0000231'), ('2018-05-22','0.000013'),
       ('2019-04-16','0.0000207'), ('2020-02-18','0.0000221'), ('2021-02-25','0.0000051'),
       ('2022-05-14','0.0000229'), ('2023-02-27','0.000008'), ('2024-05-22','0.0000278')]
TAF = [('2017-01-01','0.000119'), ('2022-01-01','0.000130'),
       ('2023-01-01','0.000145'), ('2024-01-01','0.000166')]


def load(scenario='BASE', files=None):
    required = {r['source_document_sha256'] for k in ('sec_rates','taf_rates') for r in DOC[k]}
    sources = {s['sha256']: EVIDENCE/s['path'] for s in INVENTORY if s['sha256'] in required}
    return DatedCostSchedule.from_document(DOC, expected_sha256=PIN, scenario=scenario,
                                         source_documents=sources if files is None else files)


def test_all_original_evidence_and_offline_rebuild():
    for s in INVENTORY:
        assert sha256((EVIDENCE/s['path']).read_bytes()).hexdigest() == s['sha256']
    spec = importlib.util.spec_from_file_location('official_builder', ROOT/'scripts/build_official_cost_schedule.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build() == DOC
    assert [(r['effective_from'],r['rate']) for r in DOC['sec_rates']] == SEC
    assert [(r['effective_from'],r['rate']) for r in DOC['taf_rates']] == TAF
    review = json.loads((EVIDENCE/'rate_review.json').read_text())['rates']
    ids = {s['id'] for s in INVENTORY}
    for r in review:
        assert r['source_id'] in ids and set(r['cross_checks']) <= ids
        expected = Decimal(r['original_rate']) / (1000000 if r['component']=='SEC' else 1)
        assert Decimal(r['rate']) == expected
        assert r['publication_date'] <= r['original_effective_from']
    # Historical FINRA rule bodies independently confirm every rate and intermediate revision.
    expected = {'524551':'0.000119','1207221':'0.000119','1207251':'0.000130',
                '1803996':'0.000145','1803986':'0.000145','1880951':'0.000166'}
    by_id = {s['id']:s for s in INVENTORY}
    for revision, value in expected.items():
        data = json.loads((EVIDENCE/by_id['finra-'+revision]['path']).read_bytes())
        assert '$'+value+' per share' in data[0]['body']


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_every_day_and_boundary_preserves_research_assumptions(scenario):
    schedule = load(scenario)
    day = date(2017,1,1)
    while day <= date(2024,12,31):
        iso = day.isoformat()
        for key in ('sec_rates','taf_rates'):
            assert sum(r['effective_from'] <= iso <= r['effective_to'] for r in DOC[key]) == 1
        day += timedelta(days=1)
    boundaries = {d for d,v in SEC+TAF} | {'2024-12-31'}
    for iso in boundaries:
        p = schedule.profile_for(pd.Timestamp(iso+' 12:00',tz='America/New_York'))
        sec = next(v for d,v in reversed(SEC) if d<=iso)
        taf = next(v for d,v in reversed(TAF) if d<=iso)
        assert Decimal(str(p.sell_regulatory_fee_rate)) == Decimal(sec)
        assert Decimal(str(p.regulatory_fee_per_unit)) == Decimal(taf)+Decimal(SCENARIOS[scenario]['cat_per_share'])
        m = p.configuration_metadata['phase4a_cost_model']
        assert (m['taf_side'],m['taf_cap'],m['cat_side'],m['sec_side']) == ('BOTH',None,'BOTH','SELL')
        assert p.equity_slippage_bps == SCENARIOS[scenario]['slippage_bps']
        assert p.approval_status == 'DRAFT' and not m['formal_cost_approval']
    with pytest.raises(ValueError,match='source document bytes'):
        load(files={})
    with pytest.raises(ValueError,match='coverage'):
        schedule.profile_for(pd.Timestamp('2025-01-01 12:00',tz='America/New_York'))
