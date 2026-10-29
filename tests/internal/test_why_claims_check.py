"""W0: the repo must not call the (regex, advisory) WHY Gate a hard gate until W1 is implemented."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_why_claims", ROOT / "tools" / "check_why_claims.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_has_no_hard_gate_claim_about_why():
    hits = _load_checker().find_claims(ROOT)
    assert hits == [], "WHY is a regex ADVISORY gate today (W0); remove or mark these claims: " + repr(hits)


def test_checker_flags_each_claim_form_and_honours_the_historical_marker(tmp_path):
    (tmp_path / "scp").mkdir()
    (tmp_path / "scp" / "x.py").write_text(
        "# v9.0: WHY = CHỐT\n"
        "# WHY Primary Control Gate here\n"
        "# WHY-gate (fail-closed) on transition\n"
        "# WHY = CHỐT [W0-historical]\n"
        "# regex ADVISORY gate\n",
        encoding="utf-8",
    )
    hits = _load_checker().find_claims(tmp_path)
    assert [h[1] for h in hits] == [1, 2, 3]
