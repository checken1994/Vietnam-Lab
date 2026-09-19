import pytest
import os
import tempfile
import platform
from unittest.mock import patch, MagicMock
from scp.security.os_sandbox import ProcessIsolationEnvironment, isolation_capability
from scp.security.capability_epoch import CapabilityAuthority

def test_isolation_capability_reporting():
    with patch("platform.system", return_value="Linux"):
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            caps = isolation_capability()
            assert caps["bwrap"] is True
            assert caps["level"] == "bwrap"
            
    with patch("platform.system", return_value="Darwin"):
        with patch.dict("sys.modules", {"resource": MagicMock()}):
            caps = isolation_capability()
            assert caps["rlimit"] is True
            assert caps["level"] == "rlimit_only_not_a_sandbox"


def test_sandbox_linux_bwrap_execution():
    with tempfile.TemporaryDirectory() as tmp:
        authority = CapabilityAuthority(state_path=os.path.join(tmp, "caps.sqlite3"))
        token = authority.issue("sandbox-verify")
        
        pie = ProcessIsolationEnvironment(authority)
        pie.is_windows = False
        
        with patch("platform.system", return_value="Linux"):
            with patch("shutil.which", return_value="/usr/bin/bwrap"):
                mock_run = MagicMock()
                mock_run.returncode = 0
                mock_run.stdout = "bwrap-success"
                
                with patch("subprocess.run", return_value=mock_run) as mock_sub:
                    result = pie.execute_bounded(token, ["echo", "test"])
                    assert result.stdout == "bwrap-success"
                    
                    # Verify bwrap args were injected
                    called_args = mock_sub.call_args[0][0]
                    assert called_args[0] == "bwrap"
                    assert "--unshare-all" in called_args
                    assert "--die-with-parent" in called_args
                    assert called_args[-2:] == ["echo", "test"]


def test_sandbox_linux_bwrap_fail_closed_if_missing():
    with tempfile.TemporaryDirectory() as tmp:
        authority = CapabilityAuthority(state_path=os.path.join(tmp, "caps.sqlite3"))
        token = authority.issue("sandbox-verify")
        
        pie = ProcessIsolationEnvironment(authority)
        pie.is_windows = False
        
        with patch("platform.system", return_value="Linux"):
            with patch("shutil.which", return_value=None):  # bwrap missing
                with pytest.raises(RuntimeError) as exc:
                    pie.execute_bounded(token, ["echo", "test"])
                assert "bwrap is missing on Linux. Fail-closed" in str(exc.value)


def test_sandbox_mac_rlimit_fallback_execution():
    with tempfile.TemporaryDirectory() as tmp:
        authority = CapabilityAuthority(state_path=os.path.join(tmp, "caps.sqlite3"))
        token = authority.issue("sandbox-verify")
        
        pie = ProcessIsolationEnvironment(authority)
        pie.is_windows = False
        
        with patch("platform.system", return_value="Darwin"):
            mock_run = MagicMock()
            mock_run.returncode = 0
            mock_run.stdout = "mac-success"
            
            with patch("subprocess.run", return_value=mock_run) as mock_sub:
                with patch.dict("sys.modules", {"resource": MagicMock()}):
                    result = pie.execute_bounded(token, ["echo", "test"])
                    assert result.stdout == "mac-success"
                    
                    # Verify preexec_fn was injected for rlimits
                    kwargs = mock_sub.call_args[1]
                    assert kwargs["preexec_fn"] is not None
                    
                    # Verify proxy blocking variables in env
                    assert kwargs["env"]["HTTP_PROXY"] == "http://127.0.0.1:1"
