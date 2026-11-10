"""Notebook safety and executable Python syntax; Drive execution remains external evidence."""
import ast
import json
from pathlib import Path
import re


def test_development_notebook_is_pinned_nonconsuming_and_clear():
    notebook=json.loads((Path(__file__).resolve().parents[1]/'notebooks/TSM_Development_Snapshot_Preparation.ipynb').read_text())
    calls=[];source=''
    for cell in notebook['cells']:
        if cell['cell_type']!='code':continue
        assert cell['outputs']==[] and cell['execution_count'] is None
        text=''.join(cell['source']);source+=text
        tree=ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                calls.append(node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else '')
    assert len(re.findall(r'(?:CORE_SHA|AGENT_SHA) = "[0-9a-f]{40}"',source))==2
    assert {'inventory_isolated_sources','plan_conversion','produce_development_snapshot',
            'preflight_development_snapshot','prepare_development_proposal','renew_development_receipt'}<=set(calls)
    assert not {'consume','signoff','run_research','run_backtest'} & set(calls)
    assert 'ResearchOnlyRunner' not in source and 'ExecutionEngine' not in source
    assert "if plan['requires_research_lead_approval']:" in source
    assert "elif plan['plan_sha256'] != APPROVED_PLAN_SHA256:" in source
    assert "confirmation == 'APPROVE ' + APPROVED_PLAN_SHA256" in source
    assert "if not plan['requires_research_lead_approval'] or approval is not None:" in source
    assert "assert AFTER == BEFORE" in source and 'old_uses() == USES_BEFORE == 0' in source


def test_long_notebook_has_no_default_approval_or_agent_and_pins_v12():
    notebook=json.loads((Path(__file__).resolve().parents[1]/'notebooks/TSM_Long_Development_Snapshot_Preparation.ipynb').read_text())
    source=''
    for cell in notebook['cells']:
        if cell['cell_type']!='code':continue
        assert cell['outputs']==[] and cell['execution_count'] is None
        text=''.join(cell['source']);ast.parse(text);source+=text
    assert re.search(r'CORE_SHA = "[0-9a-f]{40}"',source)
    assert 'window=LONG_WINDOW' in source
    assert 'APPROVED_PLAN_SHA256' not in source
    assert 'input(' in source and "raise RuntimeError('STOP: no personal exact-plan approval" in source
    assert "assert approval is not None" in source
    assert source.index('confirmation = input') < source.index('bundle = produce_development_snapshot')
    assert 'renew_development_receipt(bundle)' in source
    assert 'strategy_agent' not in source and 'prepare_development_proposal' not in source
    assert "open('x')" in source and 'exist_ok=False' in source


def test_recovery_notebook_first_cell_has_all_human_settings_and_no_conversion():
    notebook=json.loads((Path(__file__).resolve().parents[1]/'notebooks/TSM_Development_Snapshot_Recovery.ipynb').read_text())
    code=[c for c in notebook['cells'] if c['cell_type']=='code']
    calls=[]
    for cell in code:
        assert cell['outputs']==[] and cell['execution_count'] is None
        tree=ast.parse(''.join(cell['source']))
        calls.extend(n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
                     for n in ast.walk(tree) if isinstance(n,ast.Call))
    first=''.join(code[0]['source']);source=''.join(''.join(c['source']) for c in code)
    for setting in ('drive.mount','CORE_SHA','EXECUTION_MODE','RECOVERY_CASES','SOURCE_PARENT','LOCAL_PARENT',
                    'ARCHIVE_PARENT','SELECTED_CASE','CONFIRM_RECOVERY_PATHS_AND_EVIDENCE',
                    'binding_sha256','plan_sha256','producer_sha','approval_path'):
        assert setting in first
    assert re.search(r'CORE_SHA = "[0-9a-f]{40}"',first)
    assert 'CONFIRM_RECOVERY_PATHS_AND_EVIDENCE = False' in first
    assert 'RECOVER_EXISTING' in first
    assert 'input' not in calls
    assert 'recover_development_snapshot' in calls
    assert not {'plan_conversion','produce_development_snapshot','read_row_groups','signoff',
                'prepare_development_proposal','run_backtest','run_research'} & set(calls)
    assert 'strategy_agent' not in source
    assert "assert file_sha(BINDING_PATH) == CASE['binding_sha256']" in source
    assert "expected_plan_sha256=CASE['plan_sha256']" in source
    assert "expected_producer_sha=CASE['producer_sha']" in source
    assert 'conversion_approval=original_approval' in source
    assert 'RECOVERY_VERIFIED.json' in source
    # Later cells must not reconfigure manual selections or mount Drive.
    for cell in code[1:]:
        tree=ast.parse(''.join(cell['source']))
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign):
                assert not any(isinstance(t,ast.Name) and t.id in {'CORE_SHA','EXECUTION_MODE','SOURCE_PARENT',
                    'LOCAL_PARENT','ARCHIVE_PARENT','RECOVERY_CASES','SELECTED_CASE',
                    'CONFIRM_RECOVERY_PATHS_AND_EVIDENCE'} for t in node.targets)
