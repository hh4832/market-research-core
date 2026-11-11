"""Rebuild the reviewed research schedule offline; never discovers rates or market data."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from market_research_core.costs import CostProfile
from market_research_core.dated_costs import DatedCostSchedule, SCENARIOS, rate_record_sha256, schedule_sha256

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/evidence/phase5c1_official_rates'
OUTPUT = ROOT / 'configs/research/tsm_official_dated_costs_2017_2024.json'


def build():
    inventory = json.loads((EVIDENCE / 'source_inventory.json').read_text())['sources']
    sources = {s['id']: s for s in inventory}
    for s in inventory:
        assert sha256((EVIDENCE / s['path']).read_bytes()).hexdigest() == s['sha256']
    review = json.loads((EVIDENCE / 'rate_review.json').read_text())
    base = json.loads((ROOT / 'configs/cost_profiles/ibkr_fixed_equity.json').read_text())['profile']
    base['profile_id'] = 'tsm_official_history_research_base'
    base['source_evidence'] = 'USER_PHASE4A research Fixed commission assumptions; official dated rates selected separately'
    base['configuration_metadata'] = {'scope': 'RESEARCH_ONLY',
        'date_convention': 'NY_FILL_DATE_RESEARCH_CONVENTION_NOT_UNIVERSAL_SEC_CHARGE_DATE',
        'sec_broker_charge_date_mapping': 'DATA_PENDING', 'formal_cost_approval': False}
    base = CostProfile(**base)
    doc = dict(contract='tsm-dated-trading-costs-v1', schedule_id='TSM-OFFICIAL-HISTORY-2017-2024',
        version='1.0.0', schedule_sha256=None, scope='RESEARCH_ONLY', evidence_kind='VERIFIED_SOURCE_RECORDS',
        symbol='TSM', market='IBKR_EQUITY', timezone='America/New_York',
        coverage={'start':'2017-01-01','end':'2024-12-31'}, scenarios=SCENARIOS,
        base_profile=asdict(base), base_profile_sha256=base.sha256, sec_rates=[], taf_rates=[])
    for r in review['rates']:
        s = sources[r['source_id']]
        assert r['verification_status'] == 'VERIFIED'
        assert r['source_document_sha256'] == s['sha256']
        row = dict(version=r['source_id']+'-'+r['effective_from'], effective_from=r['effective_from'],
            effective_to=r['effective_to'], rate=r['rate'], unit=r['unit'], source_ref=s['url'],
            source_basis='OFFICIAL', verification_status='VERIFIED', source_document_sha256=s['sha256'],
            record_sha256=None)
        row['record_sha256'] = rate_record_sha256(row)
        doc['sec_rates' if r['component']=='SEC' else 'taf_rates'].append(row)
    doc['schedule_sha256'] = schedule_sha256(doc)
    required = {r['source_document_sha256'] for k in ('sec_rates','taf_rates') for r in doc[k]}
    files = {s['sha256']: EVIDENCE/s['path'] for s in inventory if s['sha256'] in required}
    for scenario in SCENARIOS:
        DatedCostSchedule.from_document(doc, expected_sha256=doc['schedule_sha256'],
            scenario=scenario, source_documents=files)
    return doc


if __name__ == '__main__':
    doc = build()
    OUTPUT.write_text(json.dumps(doc, indent=2)+'\n')
    (EVIDENCE/'SCHEDULE_SHA256').write_text(doc['schedule_sha256']+'\n')
    print(doc['schedule_sha256'])
