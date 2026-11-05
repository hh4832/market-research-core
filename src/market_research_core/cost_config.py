"""Strict versioned JSON/YAML cost configuration; no implicit approvals or missing rates."""
from __future__ import annotations

import copy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from .costs import CostProfile


def configuration_sha256(config: dict) -> str:
    value = copy.deepcopy(config)
    value['configuration_sha256'] = None
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_cost_profile(path: str | Path, *, symbol: str, as_of: str,
                      daytrade_evidence: dict | None = None) -> CostProfile:
    """Explicit trade date, versioned rates and qualified daytrade evidence required.

    A date-specific profile must be used only for fills in that effective interval.
    No market data, account approval or broker rate is discovered by this loader.
    """
    path = Path(path)
    if path.suffix.lower() in ('.yaml','.yml'):
        import yaml
        config = yaml.safe_load(path.read_text())
    else:
        config = json.loads(path.read_text())
    required = {'schema_version','configuration_sha256','profile','applicability','regulatory_schedule'}
    if not isinstance(config, dict) or set(config) != required or config['schema_version'] != '1.0.0':
        raise ValueError('unsupported or incomplete cost configuration')
    pin = configuration_sha256(config)
    if config['configuration_sha256'] != pin:
        raise ValueError('cost configuration SHA mismatch')
    app = copy.deepcopy(config['applicability'])
    fields = {'symbols','market','currency','effective_from','effective_to','assumption_status',
              'evidence_ref','verification_status','stress_slippage','listed_commission_rate',
              'qualified_daytrade_tax_rate','daytrade_conditions'}
    if set(app) != fields or not app['evidence_ref'] or app['assumption_status'] not in ('ASSUMED','EVIDENCED'):
        raise ValueError('explicit applicability / evidence required')
    day = date.fromisoformat(as_of)
    if symbol not in app['symbols'] or day < date.fromisoformat(app['effective_from']) or (
            app['effective_to'] and day > date.fromisoformat(app['effective_to'])):
        raise ValueError('symbol/date outside cost configuration applicability')
    values = copy.deepcopy(config['profile'])
    if app['verification_status'] != 'VERIFIED' or app['assumption_status'] != 'EVIDENCED':
        if values['approval_status'] != 'DRAFT':
            raise ValueError('unverified or assumed configuration cannot claim APPROVED')
    if app['market'] not in ('IBKR_EQUITY','IBKR_FUTURES','TW_EQUITY','TXF'):
        raise ValueError('unsupported configured market')
    if values['instrument_type'] != ('FUTURES' if app['market'] in ('TXF','IBKR_FUTURES') else 'EQUITY'):
        raise ValueError('market / instrument type mismatch')
    effective_from, effective_to = app['effective_from'], app['effective_to']
    schedule = config['regulatory_schedule']
    # Missing dated regulatory assumptions are not silently replaced by zero.
    if schedule:
        fields = {'version','effective_from','effective_to','rate','basis','evidence_ref','approval_status'}
        if any(set(r) != fields or not r['version'] or not r['evidence_ref'] for r in schedule):
            raise ValueError('invalid regulatory schedule metadata')
        intervals = sorted(schedule, key=lambda r: r['effective_from'])
        for left, right in zip(intervals, intervals[1:]):
            if left['effective_to'] is None or date.fromisoformat(left['effective_to']) >= date.fromisoformat(right['effective_from']):
                raise ValueError('overlapping regulatory schedule intervals')
        selected = [r for r in schedule if date.fromisoformat(r['effective_from']) <= day and
                    (r['effective_to'] is None or day <= date.fromisoformat(r['effective_to']))]
        if len(selected) != 1 or selected[0]['rate'] is None:
            raise ValueError('missing regulatory rate for requested date; configure an explicit versioned assumption')
        rate = selected[0]
        values['sell_regulatory_fee_rate'] = rate['rate']
        values['component_basis']['sell_regulatory_fee_rate'] = rate['basis']
        values['component_evidence']['sell_regulatory_fee_rate'] = rate['evidence_ref']
        values['component_approval_status']['sell_regulatory_fee_rate'] = rate['approval_status']
        effective_from = max(effective_from, rate['effective_from'])
        ends = [x for x in (effective_to, rate['effective_to']) if x]
        effective_to = min(ends) if ends else None
    elif app['market'] == 'IBKR_EQUITY':
        raise ValueError('IBKR equity requires a dated regulatory schedule')
    if daytrade_evidence is not None:
        if app['market'] != 'TW_EQUITY' or app['qualified_daytrade_tax_rate'] is None:
            raise ValueError('qualified daytrade tax unavailable for this market')
        if set(daytrade_evidence) != {'same_day_offset','instrument_eligible','account_eligible','evidence_ref'} or not daytrade_evidence['evidence_ref'] or any(
                daytrade_evidence[k] is not True for k in ('same_day_offset','instrument_eligible','account_eligible')):
            raise ValueError('qualified daytrade needs same-day, instrument and account evidence')
        values['sell_tax_rate'] = app['qualified_daytrade_tax_rate']
    values['configuration_metadata'] = dict(configuration_sha256=pin, applicability=app, as_of=as_of,
                                             effective_from=effective_from, effective_to=effective_to,
                                             symbol=symbol, regulatory_version=selected[0]['version'] if schedule else None,
                                             daytrade_evidence=daytrade_evidence)
    return CostProfile(**values)
