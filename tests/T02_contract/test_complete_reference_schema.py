import sys
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SPEC_PATH = ROOT / 'spec' / 'complete_scp_reference.yaml'


def _reference() ->dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding='utf-8'))


def test_reference_declares_canonical_verdicts_and_levels():
    data = _reference()
    assert data['verdicts'] == ['VERIFIED', 'CONTRADICTED', 'INSUFFICIENT',
        'UNKNOWN']
    assert data['evidence_levels'] == {'A': 'static', 'B': 'integration',
        'C': 'end_to_end', 'D': 'recovery'}
