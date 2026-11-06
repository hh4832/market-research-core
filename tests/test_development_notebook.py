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
    assert "plan['requires_research_lead_approval'] and not PRODUCER_APPROVAL_PATH" in source
    assert "assert AFTER == BEFORE" in source and 'old_uses() == USES_BEFORE == 0' in source
