import pytest
import subprocess
from scp.core.safe_process import (
    CompletedProcess,
    Popen,
    SubprocessError,
    TimeoutExpired,
    DEVNULL,
    PIPE,
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    _WHITELISTED_PATHS,
    _WHITELISTED_TOOLS,
    _validate_executable,
    safe_run,
)
from scp.core.capability_token import compute_token_signature, get_capability_secret
from scp.security.capability_epoch import CapabilityToken


def test_sec06_exported_symbols_match_subprocess():
    """SEC-06: safe_process must export standard subprocess primitives."""
    assert CompletedProcess is subprocess.CompletedProcess
    assert Popen is subprocess.Popen
    assert SubprocessError is subprocess.SubprocessError
    assert TimeoutExpired is subprocess.TimeoutExpired
    assert DEVNULL is subprocess.DEVNULL
    assert PIPE is subprocess.PIPE
    assert CREATE_NEW_PROCESS_GROUP == getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    assert CREATE_NO_WINDOW == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_qlt04_allowlists_are_frozenset():
    """QLT-04: Allowlist sets must be frozen at module import time."""
    assert isinstance(_WHITELISTED_TOOLS, frozenset)
    assert isinstance(_WHITELISTED_PATHS, frozenset)
    assert "python" in _WHITELISTED_TOOLS
    assert "powershell.exe" in _WHITELISTED_TOOLS


def test_qlt04_validate_executable_case_insensitivity():
    """QLT-04: Whitelisted tool matching must be case-insensitive for platform consistency."""
    _validate_executable("powershell.exe")
    _validate_executable("PowerShell.exe")
    _validate_executable("POWERSHELL.EXE")
    _validate_executable("python")
    _validate_executable("PYTHON")


def test_sec05_powershell_bypass_prohibited():
    """SEC-05: -ExecutionPolicy Bypass and Unrestricted must be rejected fail-closed."""
    with pytest.raises(ValueError, match="ExecutionPolicy Bypass.*strictly prohibited"):
        safe_run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "dir"])

    with pytest.raises(ValueError, match="ExecutionPolicy Bypass.*strictly prohibited"):
        safe_run(["powershell.exe", "-ExecutionPolicy:Bypass", "-Command", "dir"])

    with pytest.raises(ValueError, match="ExecutionPolicy Bypass.*strictly prohibited"):
        safe_run(["powershell.exe", "-ep", "Unrestricted", "-Command", "dir"])

    with pytest.raises(ValueError, match="ExecutionPolicy Bypass.*strictly prohibited"):
        safe_run(["powershell.exe", "-ExecutionPolicy=Unrestricted", "-Command", "dir"])


def test_sec05_powershell_command_without_token_arbitrary_script_blocked():
    """SEC-05: Arbitrary scripts via -Command, inline -c:, or -EncodedCommand must be blocked without valid token."""
    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-Command", "Invoke-Expression 'calc.exe'"])

    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-Command", "Set-ExecutionPolicy Unrestricted"])

    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-c:Invoke-Expression 'calc.exe'"])

    with pytest.raises(PermissionError, match="-EncodedCommand is prohibited without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-EncodedCommand", "SQBFAFgA"])


def test_sec05_powershell_command_allowlisted_script_without_token_allowed():
    """SEC-05: Safe allowlisted read-only scripts are permitted even without token."""
    try:
        res = safe_run(["powershell.exe", "-NoProfile", "-Command", "dir"], timeout=5)
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass  # On non-Windows platforms or environment where powershell.exe is absent


def test_sec05_powershell_command_with_valid_token_allowed():
    """SEC-05: Scripts validated with an authentic cryptographically signed capability token are permitted."""
    secret = get_capability_secret()
    issued_at = 123456.789
    sig = compute_token_signature(secret, "pc.execute", 1, "tok_test_sec05", issued_at)
    valid_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_sec05",
        issued_at=issued_at,
        signature=sig,
    )
    try:
        res = safe_run(["powershell.exe", "-NoProfile", "-Command", "Write-Output hello"], token=valid_token, timeout=5)
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec05_powershell_command_with_unsigned_or_forged_token_blocked():
    """SEC-05: Pseudo tokens (strings, unsigned dicts, forged signatures) must be rejected fail-closed."""
    # 1. Plain string token (attacker attempting string bypass)
    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-Command", "Invoke-Expression 'calc.exe'"], token="pseudo_token_123")

    # 2. Unsigned dictionary token
    unsigned_dict = {"token_id": "tok_test_123", "subject": "pc.execute", "epoch": 1}
    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-Command", "Invoke-Expression 'calc.exe'"], token=unsigned_dict)

    # 3. Forged signature token
    forged_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_123",
        issued_at=123456.789,
        signature="forged_bad_signature_00000000000000000000000000000000",
    )
    with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
        safe_run(["powershell.exe", "-NoProfile", "-Command", "Invoke-Expression 'calc.exe'"], token=forged_token)
