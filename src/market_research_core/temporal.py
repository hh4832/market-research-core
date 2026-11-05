"""Label-agnostic research timing; raw labels remain UNKNOWN, never inferred."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from datetime import time

import pandas as pd

from .ibkr_tsm import apply_tsm_session_mask, NYSE_CALENDAR_VERSION


@dataclass(frozen=True)
class TemporalPolicy:
    policy_id: str = 'CONSERVATIVE_LABEL_AGNOSTIC'
    version: str = '1.0.0'
    source_minutes: int = 1
    signal_start_et: str = '10:00'
    signal_end_et: str = '15:30'
    calendar_version: str = NYSE_CALENDAR_VERSION
    assumption: str = ('UNKNOWN source label; END completes at t, START at t+1m; '
                       'availability is the later bound, not measured publication latency; '
                       'execution requires the earliest possible candidate open strictly later; '
                       'RTH only, no closing auction, no ETH, no gap filling')

    def __post_init__(self):
        if self.policy_id != 'CONSERVATIVE_LABEL_AGNOSTIC' or self.version != '1.0.0' or self.source_minutes != 1:
            raise ValueError('unsupported temporal policy')
        if self.calendar_version != NYSE_CALENDAR_VERSION:
            raise ValueError('calendar version mismatch')
        if not time.fromisoformat(self.signal_start_et) < time.fromisoformat(self.signal_end_et):
            raise ValueError('invalid signal observation window')
        if self.signal_start_et < '10:00' or self.signal_end_et > '15:30':
            raise ValueError('initial TSM observation window cannot be widened')

    @property
    def sha256(self):
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


CONSERVATIVE_POLICY = TemporalPolicy()


def prepare_conservative_bars(source: pd.DataFrame, *, bar_minutes: int = 1,
                              policy: TemporalPolicy = CONSERVATIVE_POLICY) -> pd.DataFrame:
    """Build uncertainty envelopes from native 1m rows (1 or 15 source-label buckets).

    For source labels t0..tn the possible start is [t0-1m,t0], completion
    [tn,tn+1m]. Execution bar_start is the EARLIEST possible open, bar_end /
    available_at the LATEST possible completion. A 15-source-bar bucket is NOT
    a claim of a confirmed exchange wall-clock 15m interval (envelope is 16m).
    Incomplete buckets are retained but ineligible. Never resample/fill prices.
    """
    if bar_minutes not in (1, 15):
        raise ValueError('only native 1m or complete 15-source-bar aggregation supported')
    required = {'source_timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol', 'contract'}
    if not required <= set(source):
        raise ValueError(f'missing source fields: {sorted(required-set(source))}')
    raw = source.copy()
    if raw.empty:
        raise ValueError('empty conservative source')
    if raw[['symbol', 'contract']].isna().any().any() or len(raw[['symbol', 'contract']].drop_duplicates()) != 1:
        raise ValueError('conservative frame requires one explicit symbol/contract')
    if raw['symbol'].iloc[0] != 'TSM':
        raise ValueError('initial conservative temporal scope is TSM only')
    if any(pd.Timestamp(t).tzinfo is None for t in raw.source_timestamp):
        raise ValueError('source timestamps must be timezone-aware')
    raw['source_timestamp'] = pd.to_datetime(raw.source_timestamp, utc=True)
    if raw.source_timestamp.isna().any() or not raw.source_timestamp.dt.floor('min').eq(raw.source_timestamp).all():
        raise ValueError('source timestamps must be exact finite minute labels')
    raw = raw.sort_values('source_timestamp').reset_index(drop=True)
    if raw.source_timestamp.duplicated().any():
        raise ValueError('duplicate source timestamp')
    prices = raw[['open', 'high', 'low', 'close', 'volume']].astype(float)
    import numpy as np
    if not np.isfinite(prices.to_numpy()).all() or (prices.volume < 0).any() or (prices[['open','high','low','close']] <= 0).any().any():
        raise ValueError('invalid OHLCV')
    if ((prices.high < prices[['open','close','low']].max(axis=1)) |
            (prices.low > prices[['open','close','high']].min(axis=1))).any():
        raise ValueError('inconsistent OHLC envelope')
    raw[prices.columns] = prices
    masks = [apply_tsm_session_mask(raw.rename(columns={'source_timestamp':'timestamp'}),
                                   timestamp_label=label, availability_policy='BAR_END_STRICT_LATER')
             for label in ('START','END')]
    rows = []
    groups = raw.groupby(raw.source_timestamp.dt.floor(f'{bar_minutes}min'), sort=True)
    for bucket, group in groups:
        first, last = group.iloc[0], group.iloc[-1]
        complete = len(group) == bar_minutes and list(group.source_timestamp) == list(pd.date_range(bucket, periods=bar_minutes, freq='min'))
        idx = group.index
        both_rth = all(mask.loc[idx, 'session'].eq('RTH').all() for mask in masks)
        session_open = masks[0].loc[idx[0], 'session_open']
        session_close = masks[0].loc[idx[0], 'session_close']
        earliest_open = first.source_timestamp - pd.Timedelta(minutes=1)
        latest_complete = last.source_timestamp + pd.Timedelta(minutes=1)
        # No source interval ending at the unconfirmed closing auction boundary.
        safe = complete and both_rth and pd.notna(session_close) and latest_complete < session_close
        volume_ok = group.volume.gt(0).all()
        local_available = latest_complete.tz_convert('America/New_York')
        observation = time.fromisoformat(policy.signal_start_et) <= local_available.time() <= time.fromisoformat(policy.signal_end_et)
        reason = ('INCOMPLETE_SOURCE_BUCKET' if not complete else 'OUTSIDE_CONSERVATIVE_RTH' if not both_rth
                  else 'CLOSING_AUCTION_EXCLUDED' if not safe else 'ZERO_VOLUME_BAR' if not volume_ok else '')
        source_rows = []
        for r in group.to_dict('records'):
            source_rows.append({k: r[k].isoformat() if k == 'source_timestamp' else r[k] for k in required})
        rows.append(dict(source_timestamp=first.source_timestamp, source_timestamp_last=last.source_timestamp,
                         source_timestamp_label='UNKNOWN', timestamp_label='UNKNOWN',
                         completion_earliest=last.source_timestamp, completion_latest=latest_complete,
                         possible_open_latest=first.source_timestamp, modeled_available_at=latest_complete,
                         bar_start=earliest_open, bar_end=latest_complete, available_at=latest_complete,
                         available_at_policy=policy.policy_id, temporal_policy_id=policy.policy_id,
                         temporal_policy_version=policy.version, temporal_policy_sha256=policy.sha256,
                         bar_minutes=bar_minutes, timezone='America/New_York', session='RTH' if both_rth else 'OUTSIDE_SESSION',
                         session_open=session_open, session_close=session_close,
                         symbol=first.symbol, market='IBKR_EQUITY', contract=first.contract,
                         open=first.open, high=group.high.max(), low=group.low.min(), close=last.close,
                         volume=group.volume.sum(), source_count=len(group), complete_source_bucket=complete,
                         data_exists=True, research_eligible=safe, backtest_eligible=safe,
                         signal_eligible=safe and volume_ok and observation,
                         buy_execution_eligible=safe and volume_ok, sell_execution_eligible=safe and volume_ok,
                         execution_rejection_reason=reason, execution_eligibility_scope='RESEARCH_ONLY',
                         source_rows=source_rows))
    out = pd.DataFrame(rows)
    out.attrs['temporal_policy'] = asdict(policy)
    return out


def validate_conservative_bars(frame: pd.DataFrame, policy: TemporalPolicy) -> None:
    """Recompute from carried native rows; reject edited timing, masks or aggregation."""
    needed = {'source_rows', 'bar_minutes', 'modeled_available_at', 'signal_eligible', 'temporal_policy_sha256'}
    if not needed <= set(frame) or len(set(frame.bar_minutes)) != 1:
        raise ValueError('missing conservative source evidence')
    native = [row for group in frame.source_rows for row in group]
    expected = prepare_conservative_bars(pd.DataFrame(native), bar_minutes=int(frame.bar_minutes.iloc[0]), policy=policy)
    actual = frame.sort_values('bar_start').reset_index(drop=True)
    for column in expected.columns:
        if column == 'source_rows':
            continue
        if column not in actual or not actual[column].equals(expected[column]):
            raise ValueError(f'conservative frame evidence mismatch: {column}')
