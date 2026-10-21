import json
from pathlib import Path
from datetime import datetime
from trademind.config import T0ExperimentConfig


DATASET = Path(__file__).parent / 'fixtures/prompt_eval/cases.json'


def test_case_manifest_has_distinct_safe_labels():
    data = json.loads(DATASET.read_text(encoding='utf-8'))
    cases = data['cases']
    assert len(cases) >= 24
    assert len({c['category'] for c in cases}) >= 10
    assert len({c['case_id'] for c in cases}) == len(cases)
    assert len({json.dumps(c['input'], sort_keys=True) for c in cases}) == len(cases)
    for case in cases:
        assert case['dataset_version'] == data['dataset_version']
        assert case['source'] == 'synthetic'
        assert datetime.fromisoformat(case['input']['as_of']).utcoffset() is not None
        assert not set(case['expected']['allowed_actions']) & set(case['expected']['forbidden_actions'])
        assert case['expected']['reason'] and case['expected']['assertions']
        if 'SELL' in case['expected']['allowed_actions']:
            assert case['input']['account']['sellable'] > 0
        if case['category'] == 'duplicate':
            assert case['expected']['max_orders'] == 1
    cfg = T0ExperimentConfig()
    assert str(cfg.evidence_yes_floor) == data['policy']['evidence_yes_floor']
    assert str(cfg.action_confidence_floor) == data['policy']['action_confidence_floor']
    assert any(c['expected']['allowed_actions'] == ['BUY'] for c in cases)
