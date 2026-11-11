"""Pinned, immutable dated SEC/TAF research costs; no market data or network discovery."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pandas as pd

from .costs import CostProfile

SCENARIOS = {
    'BASE': {'slippage_bps': 2, 'cat_per_share': '0.00005'},
    'LOW': {'slippage_bps': 1, 'cat_per_share': '0.00005'},
    'STRESS': {'slippage_bps': 4, 'cat_per_share': '0.00005'},
    'CAT_STRESS': {'slippage_bps': 2, 'cat_per_share': '0.00010'},
}
_SCENARIO_JSON = json.dumps(SCENARIOS, sort_keys=True)
_FIELDS = {'contract', 'schedule_id', 'version', 'schedule_sha256', 'scope', 'evidence_kind',
           'symbol', 'market', 'timezone', 'coverage', 'scenarios', 'base_profile',
           'base_profile_sha256', 'sec_rates', 'taf_rates'}
_RATE_FIELDS = {'version', 'effective_from', 'effective_to', 'rate', 'unit',
                'source_ref', 'source_basis', 'verification_status', 'source_document_sha256', 'record_sha256'}


def _read_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate cost schedule JSON field: '+key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def _digest(doc, field):
    value = copy.deepcopy(doc)
    value[field] = None
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def rate_record_sha256(record):
    return _digest(record, 'record_sha256')


def schedule_sha256(document):
    return _digest(document, 'schedule_sha256')


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('explicit ISO cost date required')
    return date.fromisoformat(value)


def _amount(value):
    if not isinstance(value, str):
        raise ValueError('explicit decimal-string rate required (never null or implicit zero)')
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or Decimal(str(float(amount))) != amount:
            raise ValueError('rate must be finite, nonnegative and losslessly representable by CostProfile')
    except (InvalidOperation, OverflowError) as e:
        raise ValueError('invalid decimal rate') from e
    return amount


def new_york_fill_date(timestamp):
    ts = pd.Timestamp(timestamp)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError('timezone-aware execution timestamp required')
    return ts.tz_convert('America/New_York').date().isoformat()


def _validate_rates(rows, kind, evidence_kind, coverage):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f'{kind} historical rates missing: DATA_PENDING')
    for row in rows:
        if not isinstance(row, dict) or set(row) != _RATE_FIELDS:
            raise ValueError(f'incomplete {kind} rate record')
        if (not isinstance(row['version'], str) or not row['version'].strip()
                or not isinstance(row['source_ref'], str) or not row['source_ref']
                or any(ord(c) < 32 for c in row['source_ref'])
                or row['record_sha256'] != rate_record_sha256(row)):
            raise ValueError(f'{kind} source/version/record SHA mismatch')
        a, b = _day(row['effective_from']), _day(row['effective_to'])
        if a > b or row['unit'] != ('USD_PER_NOTIONAL' if kind == 'SEC' else 'USD_PER_SHARE'):
            raise ValueError(f'{kind} invalid interval or unit')
        _amount(row['rate'])
        if not re.fullmatch('[0-9a-f]{64}', str(row['source_document_sha256'])):
            raise ValueError('source document SHA required')
        if evidence_kind == 'VERIFIED_SOURCE_RECORDS':
            ref = urlsplit(row['source_ref'])
            hosts = ('sec.gov', 'www.sec.gov') if kind == 'SEC' else ('finra.org', 'www.finra.org')
            if (row['source_basis'] != 'OFFICIAL' or row['verification_status'] != 'VERIFIED'
                    or ' ' in row['source_ref'] or ref.scheme != 'https' or ref.netloc not in hosts or not ref.path or ref.fragment):
                raise ValueError(f'{kind} trustworthy official evidence required')
        elif (row['source_basis'] != 'ASSUMED' or row['verification_status'] != 'SYNTHETIC_FIXTURE'
              or not row['source_ref'].startswith('SYNTHETIC_FIXTURE:')):
            raise ValueError('synthetic rates must be explicitly labelled')
    ordered = sorted(rows, key=lambda r: r['effective_from'])
    for left, right in zip(ordered, ordered[1:]):
        next_day = _day(left['effective_to']) + timedelta(days=1)
        if _day(right['effective_from']) != next_day:
            raise ValueError(f'{kind} rate gap or overlap')
    if ordered[0]['effective_from'] > coverage['start'] or ordered[-1]['effective_to'] < coverage['end']:
        raise ValueError(f'{kind} rates do not cover the declared interval')


def _validate(document, pin):
    if (not isinstance(document, dict) or set(document) != _FIELDS
            or document['contract'] != 'tsm-dated-trading-costs-v1'
            or not isinstance(document['schedule_id'], str) or not document['schedule_id'].strip()
            or not isinstance(document['version'], str) or not document['version'].strip()):
        raise ValueError('unsupported dated cost schedule contract')
    if not re.fullmatch('[0-9a-f]{64}', str(pin)) or document['schedule_sha256'] != pin or schedule_sha256(document) != pin:
        raise ValueError('dated cost schedule SHA mismatch')
    if (document['scope'], document['symbol'], document['market'], document['timezone']) != (
            'RESEARCH_ONLY', 'TSM', 'IBKR_EQUITY', 'America/New_York'):
        raise ValueError('dated costs are scoped to TSM research-only NY execution')
    if document['evidence_kind'] not in ('VERIFIED_SOURCE_RECORDS', 'SYNTHETIC_FIXTURE'):
        raise ValueError('untrusted or pending historical rates')
    c = document['coverage']
    if not isinstance(c, dict) or set(c) != {'start', 'end'} or _day(c['start']) > _day(c['end']):
        raise ValueError('invalid schedule coverage')
    if document['scenarios'] != json.loads(_SCENARIO_JSON):
        raise ValueError('only the four unchanged research scenarios are supported')
    base = CostProfile(**document['base_profile'])
    if base.sha256 != document['base_profile_sha256']:
        raise ValueError('base profile SHA mismatch')
    if (base.instrument_type, base.commission_scheme, base.equity_commission_per_share,
            base.minimum_commission, base.commission_cap_rate, base.half_spread_bps,
            base.contract_multiplier, base.approval_status, base.basis, base.tick_size, base.fee_rounding, base.tax_rounding) != (
                'EQUITY', 'FIXED', .005, 1, .01, 0, 1, 'DRAFT', 'ASSUMED', .01, 'HALF_UP', 'HALF_UP'):
        raise ValueError('base must preserve the existing research-only Fixed commission assumptions')
    if (any((base.exchange_fee_per_unit, base.clearing_fee_per_unit, base.sell_tax_rate,
             base.equity_commission_rate, base.slippage_ticks)) or base.fee_mode == 'ALL_IN'
            or base.configuration_metadata.get('daytrade_evidence')):
        raise ValueError('conflicting or duplicate cost model')
    for key, kind in (('sec_rates', 'SEC'), ('taf_rates', 'TAF')):
        _validate_rates(document[key], kind, document['evidence_kind'], c)
    # Refuse combined TAF+CAT values that the legacy float-valued profile cannot represent exactly.
    for row in document['taf_rates']:
        for scenario in document['scenarios'].values():
            _amount(str(_amount(row['rate']) + Decimal(scenario['cat_per_share'])))


def _verify_source_documents(document, source_files):
    if document['evidence_kind'] == 'SYNTHETIC_FIXTURE':
        if source_files:
            raise ValueError('synthetic cost fixtures must not open external source files')
        return
    files = dict(source_files)
    required = {r['source_document_sha256'] for key in ('sec_rates', 'taf_rates') for r in document[key]}
    if len(files) != len(source_files) or set(files) != required:
        raise ValueError('all pinned official source document bytes are required')
    for expected, path in files.items():
        digest = sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError('official source document SHA mismatch')


@dataclass(frozen=True)
class DatedCostSchedule:
    """An immutable canonical document, not nested mutable caller-owned profiles.

    Each selection constructs a fresh resolved CostProfile. Pins include all rate
    records, their evidence, base profile, coverage and all four fixed scenarios.
    """
    _document_json: str
    _expected_sha256: str
    scenario: str = 'BASE'
    _source_files: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        _validate(_read_json(self._document_json), self._expected_sha256)
        if self.scenario not in self.document['scenarios']:
            raise ValueError('unsupported dated cost scenario')
        _verify_source_documents(self.document, self._source_files)

    @classmethod
    def from_document(cls, document, *, expected_sha256, scenario='BASE', source_documents=None):
        files = tuple(sorted((pin, str(path)) for pin, path in (source_documents or {}).items()))
        return cls(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False), expected_sha256, scenario, files)

    @property
    def sha256(self):
        return self._expected_sha256

    @property
    def document(self):
        return json.loads(self._document_json)

    @property
    def base_profile(self):
        return CostProfile(**self.document['base_profile'])

    def require_coverage(self, start, end):
        a, b = _day(start), _day(end)
        c = self.document['coverage']
        if not _day(c['start']) <= a <= b <= _day(c['end']):
            raise ValueError('cost date outside pinned schedule coverage')

    def profile_for(self, execution_timestamp):
        day = new_york_fill_date(execution_timestamp)
        self.require_coverage(day, day)
        doc = self.document
        sec = next(r for r in doc['sec_rates'] if r['effective_from'] <= day <= r['effective_to'])
        taf = next(r for r in doc['taf_rates'] if r['effective_from'] <= day <= r['effective_to'])
        start = max(doc['coverage']['start'], sec['effective_from'], taf['effective_from'])
        end = min(doc['coverage']['end'], sec['effective_to'], taf['effective_to'])
        base = self.base_profile
        assumptions = doc['scenarios'][self.scenario]
        cat = Decimal(assumptions['cat_per_share'])
        combined = _amount(taf['rate']) + cat
        basis = dict(base.component_basis)
        evidence = dict(base.component_evidence)
        approval = dict(base.component_approval_status)
        for field, origin, ref in (
                ('sell_regulatory_fee_rate', sec['source_basis'], sec['source_ref']),
                ('regulatory_fee_per_unit', 'ASSUMED', taf['source_ref'] + '; BOTH/no-cap TAF + assumed CAT'),
                ('equity_slippage_bps', 'ASSUMED', 'USER_PHASE4A research slippage')):
            basis[field], evidence[field], approval[field] = origin, ref, 'PENDING'
        model = dict(model='CONSERVATIVE_TAF_BOUND', taf_rate_usd_per_share=taf['rate'], taf_source=taf,
            taf_side='BOTH', taf_cap=None, cat_rate_usd_per_share=str(cat), cat_side='BOTH',
            cat_scenario='STRESS' if self.scenario == 'CAT_STRESS' else 'BASE',
            slippage_scenario='BASE' if self.scenario == 'CAT_STRESS' else self.scenario,
            slippage_bps=assumptions['slippage_bps'], half_spread_bps=0, sec_side='SELL',
            cat_source='USER_APPROVED_PHASE4A_RESEARCH_ASSUMPTION',
            cat_broker_passthrough_confirmed=False, cat_actual_upper_bound_confirmed=False,
            research_assumption_approval='USER_PHASE4A', formal_cost_approval=False,
            cost_schedule_sha256=self.sha256, sec_rate_record_sha256=sec['record_sha256'],
            taf_rate_record_sha256=taf['record_sha256'])
        metadata = copy.deepcopy(base.configuration_metadata)
        metadata.update(effective_from=start, effective_to=end, as_of=start, symbol='TSM',
            applicability={**metadata.get('applicability', {}), 'market':'IBKR_EQUITY'},
            regulatory_version=sec['version'], phase4a_cost_model=model,
            cost_schedule_sha256=self.sha256, cost_schedule_scenario=self.scenario,
            base_profile_sha256=doc['base_profile_sha256'])
        return replace(base, profile_id=doc['schedule_id']+'_'+self.scenario.lower(), version=doc['version'],
            sell_regulatory_fee_rate=float(_amount(sec['rate'])), regulatory_fee_per_unit=float(combined),
            equity_slippage_bps=float(assumptions['slippage_bps']), fee_mode='COMPONENTIZED',
            component_basis=basis, component_evidence=evidence, component_approval_status=approval,
            configuration_metadata=metadata)


def load_dated_cost_schedule(path, *, expected_sha256, scenario='BASE', source_documents=None):
    return DatedCostSchedule.from_document(_read_json(Path(path).read_text()), expected_sha256=expected_sha256,
                                         scenario=scenario, source_documents=source_documents)


def dated_tsm_cost_diagnostics(result, schedule):
    """Reuse the original per-fill debit reconciliation/TAF-CAT allocation, never charge again."""
    from types import SimpleNamespace
    from .tsm_research_costs import tsm_cost_diagnostics
    doc = schedule.document
    if (result.provenance.get('cost_schedule_id') != doc['schedule_id']
            or result.provenance.get('cost_schedule_version') != doc['version']
            or result.provenance.get('cost_schedule_timezone') != doc['timezone']
            or result.provenance.get('cost_schedule_coverage') != doc['coverage']
            or result.provenance.get('cost_configuration', {}).get('dated_cost_schedule') != doc
            or result.provenance.get('cost_schedule_sha256') != schedule.sha256
            or result.provenance.get('cost_schedule_scenario') != schedule.scenario
            or result.provenance.get('cost_profile_sha256') != schedule.base_profile.sha256):
        raise ValueError('result/schedule provenance mismatch')
    rows = []
    keys = ('sec','taf','cat','taf_unrounded','cat_unrounded','per_share_rounding_delta',
            'regulatory_total','commission','explicit_cost','price_adjustment_cost')
    for fill in result.fills.to_dict('records'):
        profile = schedule.profile_for(fill['exec_ts'])
        model = profile.configuration_metadata['phase4a_cost_model']
        if (fill.get('cost_schedule_id') != doc['schedule_id']
                or fill.get('cost_schedule_version') != doc['version']
                or fill.get('cost_profile_id') != profile.profile_id
                or fill.get('cost_profile_version') != profile.version
                or fill.get('sec_rate_record_sha256') != model['sec_rate_record_sha256']
                or fill.get('taf_rate_record_sha256') != model['taf_rate_record_sha256']
                or fill.get('cost_schedule_sha256') != schedule.sha256
                or fill.get('cost_schedule_scenario') != schedule.scenario
                or fill.get('cost_effective_date') != new_york_fill_date(fill['exec_ts'])):
            raise ValueError('fill schedule/date provenance mismatch')
        if (fill['fee'] != fill['commission'] + fill['exchange_fee'] + fill['clearing_fee']
                or fill['tax'] != fill['transaction_tax']
                or fill['explicit_cost'] != fill['fee'] + fill['tax'] + fill['regulatory_fee']):
            raise ValueError('fill debit aliases do not reconcile')
        part = tsm_cost_diagnostics(SimpleNamespace(fills=pd.DataFrame([fill]), provenance={
            'cost_profile_sha256':profile.sha256, 'cost_multiplier':result.provenance['cost_multiplier']}), profile)
        rows.append({**part['rows'][0], 'exec_ts':fill['exec_ts'],
            'cost_effective_date':fill['cost_effective_date'], 'cost_schedule_sha256':schedule.sha256,
            'cost_profile_sha256':profile.sha256,
            'sec_rate_record_sha256':profile.configuration_metadata['phase4a_cost_model']['sec_rate_record_sha256'],
            'taf_rate_record_sha256':profile.configuration_metadata['phase4a_cost_model']['taf_rate_record_sha256']})
    totals = {key:sum((r[key] for r in rows), Decimal(0)) for key in keys}
    if sorted({r['cost_profile_sha256'] for r in rows}) != result.provenance.get('applied_cost_profile_sha256'):
        raise ValueError('applied profile inventory mismatch')
    return dict(cost_profile_sha256=schedule.base_profile.sha256, cost_schedule_sha256=schedule.sha256, cost_schedule_scenario=schedule.scenario,
        model=dict(model='DATED_CONSERVATIVE_TAF_BOUND', taf_side='BOTH', taf_cap=None, cat_side='BOTH', sec_side='SELL',
                   formal_cost_approval=False, cat_broker_passthrough_confirmed=False, cat_actual_upper_bound_confirmed=False),
        rows=rows, totals=totals, scope='RESEARCH_ONLY', price_adjustment_already_in_fill_price=True,
        allocation='PRO_RATA_OF_CORE_ROUNDED_COMBINED_TAF_CAT; TAF_12_DECIMAL_ATTRIBUTION; CAT_EXACT_RESIDUAL',
        cross_rate_switching='CONTINUOUS_EXECUTION_BY_NEW_YORK_FILL_DATE')
