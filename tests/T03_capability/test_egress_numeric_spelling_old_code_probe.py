# [Agent1 old-code-fail probe] Loads egress.py from a chosen file path (the HEAD
# copy previously extracted to .openclaw/tmp/egress_head.py) WITHOUT touching
# the worktree, then demonstrates that the pre-fix code ALLOWED a decimal-
# integer spelling of the cloud metadata IP in OPEN mode. Read-only vs the repo.
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HEAD_COPY = REPO / ".openclaw" / "tmp" / "egress_head.py"


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("egress_under_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["egress_under_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not HEAD_COPY.is_file(), reason="HEAD copy not extracted")
def test_old_code_allowed_decimal_metadata_spelling():
    """OLD-CODE-FAIL: pre-fix is_cloud_metadata returns False and enforce()
    ALLOWS the host 2852039166 (== 169.254.169.254) in OPEN mode.

    Runs the HEAD copy via importlib (worktree untouched). If this probe ever
    becomes inconclusive (e.g. an equivalent upstream fix landed on HEAD), it
    FAILS LOUDLY rather than passing vacuously.
    """
    old = _load_module(HEAD_COPY)

    policy = old.EgressPolicy(mode=old.EgressMode.OPEN, production_mode=False)
    assert policy.is_cloud_metadata("2852039166") is False, (
        "probe copy unexpectedly blocks the decimal spelling - probe inconclusive"
    )
    # OLD-CODE-FAIL confirmation: enforce() must ALLOW the decimal spelling
    # (that is exactly the vulnerability); no exception is the expected old
    # behavior. If a copy starts denying, the probe is inconclusive and must
    # fail loudly rather than pass vacuously.
    try:
        policy.enforce("http://2852039166/latest/meta-data")
    except old.EgressDeniedError:
        pytest.fail(
            "probe inconclusive: the archived pre-fix copy denies the decimal "
            "spelling; the archived HEAD reference needs a refresh"
        )
    pytest.fail(
        "OLD-CODE-FAIL CONFIRMED: pre-fix egress ALLOWED the decimal spelling "
        "of the cloud-metadata IP in OPEN mode (archived 9d1efc81 copy; the "
        "failure ABOVE is the probe's intended loud record, not a regression)."
    )


def test_new_code_blocks_decimal_metadata_spelling():
    """NEW-CODE-PASS: worktree module blocks numeric spellings in all modes."""
    from scp.policy.egress import (
        EgressDeniedError,
        EgressMode,
        EgressPolicy,
        _numeric_host_to_ip,
    )

    assert _numeric_host_to_ip("2852039166") == "169.254.169.254"
    for mode in (EgressMode.DENY, EgressMode.ALLOWLIST, EgressMode.OPEN):
        policy = EgressPolicy(mode=mode, production_mode=False)
        with pytest.raises(EgressDeniedError):
            policy.enforce("http://2852039166/latest/meta-data")
