"""Phase4-A research-only TAF/CAT assumptions over the existing cost engine."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from .cost_config import load_cost_profile
from .costs import calculate_fill_costs

WINDOW_START = '2024-06-03'
WINDOW_END = '2024-06-28'
SLIPPAGE_BPS = {'LOW': 1, 'BASE': 2, 'STRESS': 4}
CAT_PER_SHARE = {'BASE': '0.00005', 'STRESS': '0.00010'}


def rate_record_sha256(record: dict) -> str:
    value = copy.deepcopy(record)
    value['sha256'] = None
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def build_tsm_research_profile(sec_cost_config: str | Path, taf_record_path: str | Path, *,
                                expected_sec_configuration_sha256: str, expected_taf_sha256: str,
                                slippage_scenario: str = 'BASE', cat_scenario: str = 'BASE'):
    """Require existing pinned SEC/TAF evidence. No invented numeric fee default.

    One effective interval only; no forced close/restart or stitched backtest.
    The FINRA rate's scope remains sourced; its BOTH-side/no-cap application is
    explicitly a research assumption, never broker-actual pricing.
    """
    if slippage_scenario not in SLIPPAGE_BPS or cat_scenario not in CAT_PER_SHARE:
        raise ValueError('unsupported research cost scenario')
    base = load_cost_profile(sec_cost_config,symbol='TSM',as_of=WINDOW_START)
    if base.configuration_metadata['configuration_sha256'] != expected_sec_configuration_sha256:
        raise ValueError('SEC configuration pin mismatch')
    meta = base.configuration_metadata
    if meta['effective_from'] > WINDOW_START or (meta['effective_to'] and meta['effective_to'] < WINDOW_END):
        raise ValueError('one SEC interval must cover 2024-06-03 through 2024-06-28')
    if (base.instrument_type,base.commission_scheme,base.equity_commission_per_share,
        base.minimum_commission,base.commission_cap_rate,base.half_spread_bps) != ('EQUITY','FIXED',.005,1,.01,0):
        raise ValueError('base must match selected IBKR Pro Fixed research assumptions without half-spread')
    if base.sell_regulatory_fee_rate is None or not meta.get('regulatory_version'):
        raise ValueError('existing dated SEC rate required')
    if any((base.exchange_fee_per_unit,base.clearing_fee_per_unit,base.regulatory_fee_per_unit,
            base.sell_tax_rate,base.equity_commission_rate,base.slippage_ticks)):
        raise ValueError('base contains conflicting or duplicate costs')
    record=json.loads(Path(taf_record_path).read_text())
    required={'schema_version','sha256','version','effective_from','effective_to','usd_per_share',
              'source_ref','source_basis','verification_status'}
    if not isinstance(record,dict) or set(record)!=required or record['schema_version']!='1.0.0':
        raise ValueError('explicit dated TAF record required')
    pin=rate_record_sha256(record)
    if record['sha256']!=pin or expected_taf_sha256!=pin:
        raise ValueError('TAF record pin mismatch')
    if record['usd_per_share'] is None or not record['source_ref'] or not record['version']:
        raise ValueError('TAF source/rate/version is missing')
    if record['source_basis'] not in ('OFFICIAL','BROKER_ACTUAL','ASSUMED') or record['verification_status'] not in ('RECORDED','VERIFIED','ASSUMED'):
        raise ValueError('explicit TAF evidence classification required')
    if date.fromisoformat(record['effective_from']) > date.fromisoformat(WINDOW_START) or (
            record['effective_to'] and date.fromisoformat(record['effective_to']) < date.fromisoformat(WINDOW_END)):
        raise ValueError('one TAF interval must cover the research window')
    taf=Decimal(str(record['usd_per_share']));cat=Decimal(CAT_PER_SHARE[cat_scenario])
    if not taf.is_finite() or taf<0:
        raise ValueError('invalid TAF per-share rate')
    combined=taf+cat
    # Engine rate fields are float-valued: refuse values not representable by its Decimal(str(float)) contract.
    if Decimal(str(float(combined)))!=combined:
        raise ValueError('combined TAF/CAT rate is not losslessly representable by current CostProfile')
    basis=dict(base.component_basis);evidence=dict(base.component_evidence);approval=dict(base.component_approval_status)
    for name in ('equity_slippage_bps','half_spread_bps','equity_commission_per_share','commission_cap_rate'):
        basis[name]='ASSUMED'
        evidence[name]='USER_PHASE4A approved research assumption; not actual account pricing'
        approval[name]='PENDING'
    basis['regulatory_fee_per_unit']='ASSUMED'
    evidence['regulatory_fee_per_unit']=record['source_ref']+'; user-approved Phase4-A BOTH/no-cap TAF + assumed CAT'
    approval['regulatory_fee_per_unit']='PENDING'
    component_model=dict(model='CONSERVATIVE_TAF_BOUND',taf_rate_usd_per_share=str(taf),
                         taf_source=record,taf_side='BOTH',taf_cap=None,
                         cat_rate_usd_per_share=str(cat),cat_scenario=cat_scenario,cat_side='BOTH',
                         cat_source='USER_APPROVED_PHASE4A_RESEARCH_ASSUMPTION',
                         cat_broker_passthrough_confirmed=False,cat_actual_upper_bound_confirmed=False,
                         slippage_scenario=slippage_scenario,slippage_bps=SLIPPAGE_BPS[slippage_scenario],
                         half_spread_bps=0,sec_side='SELL',sec_configuration_sha256=expected_sec_configuration_sha256,
                         research_assumption_approval='USER_PHASE4A',formal_cost_approval=False)
    meta=copy.deepcopy(meta)
    meta.update(effective_from=WINDOW_START,effective_to=WINDOW_END,phase4a_cost_model=component_model)
    return replace(base,profile_id='tsm_phase4a_'+slippage_scenario.lower()+'_cat_'+cat_scenario.lower(),version='1.0.0',
                   basis='ASSUMED',approval_status='DRAFT',equity_slippage_bps=float(SLIPPAGE_BPS[slippage_scenario]),
                   regulatory_fee_per_unit=float(combined),fee_mode='COMPONENTIZED',
                   component_basis=basis,component_evidence=evidence,component_approval_status=approval,
                   configuration_metadata=meta)


def tsm_cost_diagnostics(result, profile) -> dict:
    """Attribute the Core's ACTUAL rounded regulatory debit, never add a debit.

    Existing engine rounds SELL SEC separately from combined per-share TAF+CAT.
    The latter is allocated pro rata; CAT gets the exact residual. This is a
    disclosure of rounded cost, not an independent fee calculator or a tax return.
    Slippage is already in fill_price and is reported separately, never deducted again.
    """
    model=profile.configuration_metadata.get('phase4a_cost_model')
    if not model:
        raise ValueError('Phase4-A profile required')
    if result.provenance.get('cost_profile_sha256')!=profile.sha256:
        raise ValueError('result/profile provenance mismatch')
    taf_rate=Decimal(model['taf_rate_usd_per_share']);cat_rate=Decimal(model['cat_rate_usd_per_share'])
    sec_only=replace(profile,regulatory_fee_per_unit=None)
    rows=[]
    multiplier=Decimal(str(result.provenance['cost_multiplier']))
    for fill in result.fills.to_dict('records'):
        if fill['cost_profile_sha256']!=profile.sha256:
            raise ValueError('fill/profile pin mismatch')
        expected=calculate_fill_costs(fill['fill_price'],fill['qty'],fill['side'],profile)
        if any(fill[key]!=value*multiplier for key,value in expected.items()):
            raise ValueError('fill cost does not match actual Core debit')
        sec=calculate_fill_costs(fill['fill_price'],fill['qty'],fill['side'],sec_only)['regulatory_fee']*multiplier
        combined=fill['regulatory_fee']-sec
        taf=(combined*taf_rate/(taf_rate+cat_rate)).quantize(Decimal('0.000000000001'))
        cat=combined-taf
        rows.append(dict(order_id=fill['order_id'],side=fill['side'],qty=fill['qty'],sec=sec,taf=taf,cat=cat,
                         taf_unrounded=Decimal(str(fill['qty']))*taf_rate*multiplier,
                         cat_unrounded=Decimal(str(fill['qty']))*cat_rate*multiplier,
                         per_share_rounding_delta=combined-Decimal(str(fill['qty']))*(taf_rate+cat_rate)*multiplier,
                         regulatory_total=fill['regulatory_fee'],commission=fill['commission'],
                         explicit_cost=fill['explicit_cost'],price_adjustment_cost=fill['price_adjustment_cost']))
    totals={key:sum((row[key] for row in rows),Decimal(0)) for key in
            ('sec','taf','cat','taf_unrounded','cat_unrounded','per_share_rounding_delta','regulatory_total','commission','explicit_cost','price_adjustment_cost')}
    if totals['sec']+totals['taf']+totals['cat']!=totals['regulatory_total']:
        raise ValueError('regulatory attribution does not reconcile')
    return dict(cost_profile_sha256=profile.sha256,model=model,rows=rows,totals=totals,
                allocation='PRO_RATA_OF_CORE_ROUNDED_COMBINED_TAF_CAT; TAF_12_DECIMAL_ATTRIBUTION; CAT_EXACT_RESIDUAL',
                price_adjustment_already_in_fill_price=True,scope='RESEARCH_ONLY',
                cross_rate_switching='UNSUPPORTED_NO_FORCED_LIQUIDATION_OR_STITCHING')
