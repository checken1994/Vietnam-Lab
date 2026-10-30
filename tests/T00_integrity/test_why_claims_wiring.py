"""WHY wiring: check_why_claims + check_why_chain must be CALLED (T00 / hook / CI), not just present.

Before this lane tools/check_why_claims.py existed on main but nothing invoked it (grep: 0 callers),
and no gate looked at commit messages. Anti-Goodhart (W1.6): each wiring below has a test that
turns RED when the wiring is removed (mutants are recorded in the lane's done file).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
T00 = ROOT / "tools" / "t00_meta_audit.py"


def _load_t00():
    spec = importlib.util.spec_from_file_location("t00_wiring_under_test", T00)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ T00 runs the claims check

def test_t00_main_calls_run_why_claims_check_and_feeds_violations():
    tree = ast.parse(T00.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    fed = False
    for node in ast.walk(main):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "extend"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "all_new_violations"
                and node.args and isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Name) and node.args[0].func.id == "run_why_claims_check"):
            fed = True
    assert fed, "T00 main() must extend all_new_violations with run_why_claims_check()"


def test_run_why_claims_check_flags_a_hard_gate_claim_in_a_tree(tmp_path):
    t00 = _load_t00()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "check_why_claims.py").write_text((ROOT / "tools" / "check_why_claims.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "scp").mkdir()
    (tmp_path / "scp" / "x.py").write_text("# v9.0: WHY = CHỐT\n", encoding="utf-8")
    violations = t00.run_why_claims_check(tmp_path)
    assert len(violations) == 1 and violations[0].startswith("WHY-CLAIMS: scp/x.py:1:")
    (tmp_path / "scp" / "x.py").write_text("# regex ADVISORY gate\n", encoding="utf-8")
    assert t00.run_why_claims_check(tmp_path) == []


def test_run_why_claims_check_is_fail_closed_when_the_tool_is_missing_or_broken(tmp_path):
    t00 = _load_t00()
    missing = t00.run_why_claims_check(tmp_path)
    assert len(missing) == 1 and "tool file missing" in missing[0]
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "check_why_claims.py").write_text("def find_claims(:\n", encoding="utf-8")
    broken = t00.run_why_claims_check(tmp_path)
    assert len(broken) == 1 and "fail-closed" in broken[0]


def test_current_tree_has_no_hard_gate_claim_about_why():
    assert _load_t00().run_why_claims_check() == []


# ------------------------------------------------------------------ CI runs both checks

def _guardrail_steps():
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "scp_guardrails.yml").read_text(encoding="utf-8"))
    return data, data["jobs"]["t00-meta-audit"]["steps"]


def test_ci_runs_claims_and_chain_checks_fail_closed():
    data, steps = _guardrail_steps()
    triggers = data.get("on") or data.get(True)
    assert {"push", "pull_request", "schedule"} <= set(triggers)
    claims = [s for s in steps if "tools/check_why_claims.py" in str(s.get("run", ""))]
    chain = [s for s in steps if "tools/check_why_chain.py" in str(s.get("run", ""))]
    assert len(claims) == 1 and len(chain) == 1
    for step in claims + chain:
        assert "continue-on-error" not in step and "if" not in step, "WHY steps must be unconditional and blocking"
    run = chain[0]["run"]
    assert "--range" in run and "--base" in run
    assert "pull_request" in run and "push" in run, "PR and push ranges must both be handled"
    branches = [ln.strip() for ln in run.splitlines() if re.match(r"\s*(pull_request|push|\*)\)", ln)]
    assert len(branches) == 3, branches
    for branch in branches:
        assert "tools/check_why_chain.py" in branch and "--range" in branch, f"event branch does not run the gate: {branch}"
    assert "WHY_PR_BASE" in chain[0]["env"] and "WHY_PR_HEAD" in chain[0]["env"] and "WHY_PUSH_BEFORE" in chain[0]["env"]
    assert "${{" not in run, "event values must pass through env, never be interpolated into the script"
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, "the range check needs full history"


def test_ci_still_runs_t00_which_includes_the_claims_check():
    _, steps = _guardrail_steps()
    assert any(str(s.get("run", "")).strip() == "python tools/t00_meta_audit.py" for s in steps)


# ------------------------------------------------------------------ policy / ownership

def test_policy_file_is_well_formed_and_protected():
    pol = json.loads((ROOT / "spec" / "why_chain_policy.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{40}", pol["epoch_sha"])
    assert isinstance(pol["recent_window"], int) and pol["recent_window"] >= 20
    guard = yaml.safe_load((ROOT / "spec" / "guardrail_policy.yaml").read_text(encoding="utf-8"))
    for needed in ("tools/check_why_chain", "tools/check_why_claims", "tools/install_git_hooks"):
        assert needed in guard["protected_paths"]
    owners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "/tools/check_why_chain*" in owners and "/spec/" in owners


# ------------------------------------------------------------------ run_git_cmd decoding (env issue)

def test_run_git_cmd_decodes_git_output_as_utf8_explicitly(monkeypatch):
    t00 = _load_t00()
    captured = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(t00.subprocess, "run", fake_run)
    t00.run_git_cmd(["log", "-1"])
    assert captured.get("encoding") == "utf-8" and captured.get("errors") == "replace"


def test_run_git_cmd_survives_a_legacy_locale_with_vietnamese_commit_subjects(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    env = {"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.invalid",
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    subject = "fix: Łódź ỗ ợ chuỗi tại sao"  # U+0141 encodes to C5 81: byte 0x81 is undefined in cp1252/cp1258
    for args in (["init", "-q"], ["config", "core.hooksPath", str(repo / "nohooks")]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=repo, env=env, input=subject.encode("utf-8"),
                   check=True, capture_output=True)
    script = (
        "import importlib.util,sys\n"
        f"spec=importlib.util.spec_from_file_location('t', r'{T00}')\n"
        "m=importlib.util.module_from_spec(spec); sys.modules['t']=m; spec.loader.exec_module(m)\n"
        f"from pathlib import Path; m.PROJECT_ROOT=Path(r'{repo}')\n"
        "print(m.run_git_cmd(['log','-1','--format=%s']).encode('utf-8').hex())\n"
    )
    child_env = dict(env, PYTHONUTF8="0", PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-X", "utf8=0", "-c", script], env=child_env, capture_output=True, cwd=ROOT)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    assert bytes.fromhex(res.stdout.decode().strip()).decode("utf-8") == subject
