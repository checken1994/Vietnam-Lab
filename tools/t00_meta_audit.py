#!/usr/bin/env python3
"""SCP T00 Meta-Audit & Test-Integrity Authority (L2/L3)

This script enforces SCP's test integrity policies by strictly monitoring
test modifications, skips, deletions, and manufactured evidence against
a trusted baseline.

Violations already existing in the baseline are tracked as BASELINE_DEBT.
New violations added by the candidate branch are REJECTED.
Delta is computed using a finding-set (Counter) to prevent spoofing.
"""
import sys
import os
import argparse
import yaml
import subprocess
import ast
import logging
import re
import tempfile
import shutil
from pathlib import Path
from collections import Counter

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = PROJECT_ROOT / "spec" / "guardrail_policy.yaml"

def fail_closed(msg):
    print(f"\n[T00 FAIL-CLOSED] {msg}")
    sys.exit(1)

def load_policy():
    if not POLICY_FILE.exists():
        fail_closed("Policy file spec/guardrail_policy.yaml is missing.")
    try:
        with open(POLICY_FILE, "r", encoding="utf-8") as f:
            policy = yaml.safe_load(f)
            if not policy:
                fail_closed("Policy file is empty or invalid YAML.")
            if "enforcement_context" not in policy:
                fail_closed("Policy missing 'enforcement_context'.")
            return policy
    except Exception as e:
        logger.debug("policy parse failed", exc_info=e)
        fail_closed(f"Failed to parse policy: {e}")

def run_git_cmd(args, check=False):
    try:
        # [HARNESS-FIX 2026-10-01, pre-commit worktree ENOENT] Git exports its
        # operation context to hook processes — notably GIT_INDEX_FILE as the
        # RELATIVE ".git/index". Child git commands launched from the hook
        # resolve that relative path against their OWN cwd: `git worktree add`
        # then tries to write <new-worktree>/.git/index.lock where `.git` is a
        # file (worktree pointer), fails with ENOENT, and this fail-closed gate
        # blocked EVERY commit in hook context (reproduced 4x; proven via
        # instrumented hook env dump + minimal subprocess repro). Scrub the
        # parent operation's git context so the audit observes the repository
        # state; no audit rule, threshold or assertion is changed.
        child_env = {
            key: value for key, value in os.environ.items()
            if key not in (
                "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            )
        }
        # [HARNESS-FIX 2026-10-30] decode git output as UTF-8 explicitly. text=True used the
        # locale codec (cp1258/cp1252 on Windows), so Vietnamese commit subjects made the
        # reader thread fail, stdout became None and this fail-closed gate stopped every
        # commit for the wrong reason. errors=replace never hides a check: only decoding.
        res = subprocess.run(["git"] + args, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=PROJECT_ROOT, env=child_env)
        if check and res.returncode != 0:
            fail_closed(f"Git command failed: {' '.join(args)}\n{res.stderr}")
        if res.returncode != 0:
            # [HARNESS-DIAG 2026-10-07] check=False callers treat non-zero rc
            # as a soft signal but only ever see the stdout return — the real
            # stderr evidence (git fatal message) was swallowed, leaving the
            # CI worktree-scrub test failure undiagnosable ("stderr tail: ''").
            # Debug level only: no behavioral change, no hook/CI log noise.
            logger.debug(
                "git command %s failed (rc=%s): %s",
                args, res.returncode, res.stderr.strip(),
            )
        return res.stdout.strip()
    except Exception as e:
        if check:
            fail_closed(f"Git execution error: {e}")
        logger.debug("git command %s failed (check=False)", args, exc_info=e)
        return ""

def get_git_content(ref, path):
    try:
        out = subprocess.check_output(
            ["git", "show", f"{ref}:{path}"], 
            stderr=subprocess.DEVNULL, cwd=PROJECT_ROOT
        )
        return out.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError:
        return None

def get_local_content(path):
    p = PROJECT_ROOT / path
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("local content read failed for %s", path, exc_info=e)
        return None

SKIP_MARKS = ('skip', 'xfail', 'skipif')

class AuditVisitor(ast.NodeVisitor):
    def __init__(self):
        self.findings = Counter()
        self.current_func = "<module>"
        self.current_class = None
        self.func_depth = 0
        self.in_test_func = False

    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node):
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._handle_function(node)

    def _is_fixture(self, node):
        for dec in node.decorator_list:
            name = None
            if isinstance(dec, ast.Name):
                name = dec.id
            elif isinstance(dec, ast.Attribute):
                name = dec.attr
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    name = dec.func.id
                elif isinstance(dec.func, ast.Attribute):
                    name = dec.func.attr
            if name and "fixture" in name.lower():
                return True
        return False

    def _is_test_function(self, node):
        if self._is_fixture(node):
            return False
        if node.name.startswith("test_") or node.name.startswith("test"):
            if self.func_depth == 1:
                if self.current_class is None:
                    return True
                if self.current_class.startswith("Test"):
                    return True
        return False

    def _is_empty_body(self, body):
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                val = stmt.value.value
                if isinstance(val, str) or val is ... or val is None:
                    continue
            return False
        return True

    def _has_any_assertion(self, func_node):
        stack = list(ast.iter_child_nodes(func_node))
        while stack:
            current = stack.pop()
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(current, ast.Assert):
                return True
            if isinstance(current, ast.With):
                for item in current.items:
                    if self._is_verification_call(item.context_expr):
                        return True
            if isinstance(current, ast.Call):
                if self._is_verification_call(current):
                    return True
            stack.extend(ast.iter_child_nodes(current))
        return False

    def _is_verification_call(self, call_node):
        if not isinstance(call_node, ast.Call):
            return False
        name = ""
        if isinstance(call_node.func, ast.Name):
            name = call_node.func.id
        elif isinstance(call_node.func, ast.Attribute):
            name = call_node.func.attr
            if isinstance(call_node.func.value, ast.Name) and call_node.func.value.id == "pytest":
                if name in ("raises", "warns", "fail"):
                    return True

        name_lower = name.lower()
        if (
            name_lower.startswith("assert")
            or name_lower.startswith("verify")
            or name_lower.endswith("_verify")
            or name_lower.startswith("check")
            or name_lower.endswith("_check")
            or name_lower.startswith("expect")
            or name_lower.startswith("test_")
            or "assert" in name_lower
        ):
            return True
        return False

    def _is_placebo_assert(self, test_node):
        # 1. Direct constant truthy: assert True, assert 1, assert "msg"
        if isinstance(test_node, ast.Constant):
            if bool(test_node.value) is True:
                return True

        # 2. Unary not of falsy constant: assert not False, assert not 0, assert not None, assert not ""
        if isinstance(test_node, ast.UnaryOp) and isinstance(test_node.op, ast.Not):
            if isinstance(test_node.operand, ast.Constant):
                if bool(test_node.operand.value) is False:
                    return True

        # 3. Constant comparisons: assert 1 == 1, assert 'a' == 'a', assert 0 != 1, assert True is True
        if isinstance(test_node, ast.Compare):
            if isinstance(test_node.left, ast.Constant) and all(isinstance(c, ast.Constant) for c in test_node.comparators):
                try:
                    cur_val = test_node.left.value
                    is_true = True
                    for op, comp in zip(test_node.ops, test_node.comparators):
                        v2 = comp.value
                        res = False
                        if isinstance(op, ast.Eq): res = (cur_val == v2)
                        elif isinstance(op, ast.NotEq): res = (cur_val != v2)
                        elif isinstance(op, ast.Is): res = (cur_val is v2)
                        elif isinstance(op, ast.IsNot): res = (cur_val is not v2)
                        elif isinstance(op, ast.Lt): res = (cur_val < v2)
                        elif isinstance(op, ast.LtE): res = (cur_val <= v2)
                        elif isinstance(op, ast.Gt): res = (cur_val > v2)
                        elif isinstance(op, ast.GtE): res = (cur_val >= v2)
                        else:
                            is_true = False
                            break
                        if not res:
                            is_true = False
                            break
                        cur_val = v2
                    if is_true:
                        return True
                except Exception:
                    pass

            # 4. Tautological comparisons: assert x == x, assert x is x
            if isinstance(test_node.left, ast.Name) and len(test_node.comparators) == 1:
                comp = test_node.comparators[0]
                op = test_node.ops[0]
                if isinstance(comp, ast.Name) and test_node.left.id == comp.id:
                    if isinstance(op, (ast.Eq, ast.Is)):
                        return True

        # 5. Non-empty tuple: assert (x == 1, "msg")
        if isinstance(test_node, ast.Tuple) and len(test_node.elts) > 0:
            return True

        return False

    def _is_swallowed_body(self, body):
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                continue
            return False
        return True

    def _handle_function(self, node):
        old_func = self.current_func
        self.current_func = node.name
        self.func_depth += 1
        old_in_test = self.in_test_func

        is_test = self._is_test_function(node)
        self.in_test_func = is_test

        for dec in node.decorator_list:
            attr_name = None
            if isinstance(dec, ast.Attribute):
                attr_name = dec.attr
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Attribute):
                    attr_name = dec.func.attr
                elif isinstance(dec.func, ast.Name):
                    attr_name = dec.func.id
                    
            if attr_name in ('skip', 'xfail', 'skipif'):
                self.findings[f"{attr_name} in {self.current_func}"] += 1

        if is_test:
            if self._is_empty_body(node.body):
                self.findings[f"empty_test_body in {self.current_func}"] += 1
            if not self._has_any_assertion(node):
                self.findings[f"zero_assert_test in {self.current_func}"] += 1

        self.generic_visit(node)
        self.func_depth -= 1
        self.current_func = old_func
        self.in_test_func = old_in_test

    def _skip_mark_names(self, value):
        """Names of pytest.mark.{skip,xfail,skipif} referenced in a pytestmark value."""
        marks = set()
        if value is None:
            return marks
        for node in ast.walk(value):
            attr_node = node.func if isinstance(node, ast.Call) else node
            if (
                isinstance(attr_node, ast.Attribute)
                and attr_node.attr in SKIP_MARKS
                and isinstance(attr_node.value, ast.Attribute)
                and attr_node.value.attr == 'mark'
                and isinstance(attr_node.value.value, ast.Name)
                and attr_node.value.value.id == 'pytest'
            ):
                marks.add(attr_node.attr)
        return marks

    def _check_pytestmark_target(self, target, value):
        # A module- or class-level `pytestmark = pytest.mark.skip(...)` silently
        # skips whole files/classes without any skip call or function decorator.
        if isinstance(target, ast.Name) and target.id == 'pytestmark':
            for mark in sorted(self._skip_mark_names(value)):
                self.findings[f"pytestmark {mark} in {self.current_func}"] += 1

    def visit_Assign(self, node):
        for target in node.targets:
            self._check_pytestmark_target(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._check_pytestmark_target(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._check_pytestmark_target(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            if getattr(node.func.value, 'id', '') == 'pytest' and node.func.attr in ('skip', 'xfail', 'importorskip'):
                self.findings[f"pytest.{node.func.attr}() in {self.current_func}"] += 1
        self.generic_visit(node)

    def visit_Assert(self, node):
        if self._is_placebo_assert(node.test):
            self.findings[f"placebo_assert in {self.current_func}"] += 1
        self.generic_visit(node)

    def visit_Try(self, node):
        if self.in_test_func:
            for handler in node.handlers:
                if self._is_swallowed_body(handler.body):
                    self.findings[f"swallowed_exception in {self.current_func}"] += 1
        self.generic_visit(node)

def get_fa01_signatures(code: str | ast.AST):
    if isinstance(code, ast.AST):
        tree = code
    else:
        if not code: return Counter()
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return Counter()
    visitor = AuditVisitor()
    visitor.visit(tree)
    return visitor.findings

def get_fa04_signatures(code: str) -> Counter:
    findings = Counter()
    if not code: return findings
    for i, line in enumerate(code.splitlines()):
        content = line.strip()
        if re.search(r'["\']simulated\s+verifi(ed|cation)["\']', content, re.IGNORECASE):
            findings[f"simulated verification: {content}"] += 1
        if re.search(r'return\s*\{.*["\']status["\'].*["\']VERIFIED["\']', content, re.IGNORECASE):
            findings[f"hardcoded VERIFIED: {content}"] += 1
    return findings

def audit_content(candidate_code: str, baseline_code: str, path: str):
    new_violations = []
    debts = []
    
    # FA-01
    if (path.startswith("tests/") or path.startswith("scp/tests/")) and path.endswith(".py"):
        c_fa01 = get_fa01_signatures(candidate_code)
        b_fa01 = get_fa01_signatures(baseline_code)
        
        delta = c_fa01 - b_fa01
        for sig, count in delta.items():
            new_violations.append(f"FA-01: {path} -> {sig} ({count} new instances)")
            
        debt = c_fa01 & b_fa01
        for sig, count in debt.items():
            debts.append(f"FA-01: {path} -> {sig} ({count} historical instances)")
            
    # FA-04
    if path.startswith("scp/") and path.endswith(".py"):
        c_fa04 = get_fa04_signatures(candidate_code)
        b_fa04 = get_fa04_signatures(baseline_code)
        
        delta = c_fa04 - b_fa04
        for sig, count in delta.items():
            new_violations.append(f"FA-04: {path} -> {sig} ({count} new instances)")
            
        debt = c_fa04 & b_fa04
        for sig, count in debt.items():
            debts.append(f"FA-04: {path} -> {sig} ({count} historical instances)")
            
    return new_violations, debts

def parse_nodeids(output: str) -> set:
    nodeids = set()
    for line in output.splitlines():
        line = line.strip()
        if (line.startswith("tests/") or line.startswith("scp/tests/")) and "::" in line:
            nodeids.add(line)
    return nodeids

def get_real_nodeids(cwd: Path) -> set:
    targets = []
    if (cwd / "tests").exists(): targets.append("tests/")
    if (cwd / "scp" / "tests").exists(): targets.append("scp/tests/")
    if (cwd / "tests" / "internal").exists(): targets.append("tests/internal/")
    if not targets: return set()
    
    cmd = [sys.executable, "-m", "pytest"] + targets + ["--collect-only", "-q"]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode not in (0, 5): # 0 is success, 5 is no tests collected
        fail_closed(f"Pytest collection failed in {cwd}:\n{res.stdout}\n{res.stderr}")
    return parse_nodeids(res.stdout)

def get_baseline_nodeids(trusted_base: str) -> set:
    tmpdir = tempfile.mkdtemp()
    try:
        run_git_cmd(["worktree", "add", "-d", tmpdir, trusted_base], check=True)
        # Neutralize historical module-level network call during pytest collection if present in baseline
        hist_test_api = Path(tmpdir) / "tests" / "test_api.py"
        if hist_test_api.exists():
            try:
                content = hist_test_api.read_text(encoding="utf-8", errors="ignore")
                if "_url_open(req).getcode()" in content and "def test_api_status():" not in content:
                    hist_test_api.write_text("def test_api_status(): pass\n", encoding="utf-8")
            except Exception as e:
                logger.debug("historical test_api.py normalization skipped", exc_info=e)
        return get_real_nodeids(Path(tmpdir))
    finally:
        run_git_cmd(["worktree", "remove", "-f", tmpdir], check=False)
        shutil.rmtree(tmpdir, ignore_errors=True)

def check_real_test_deletion(trusted_base: str):
    print(f"[T00 Meta-Audit] Collecting baseline pytest nodeids ({trusted_base})...")
    b_nodeids = get_baseline_nodeids(trusted_base)
    # Map scp/tests/ to tests/internal/ in baseline nodeids to support moving the directory
    mapped_b_nodeids = set()
    for n in b_nodeids:
        if n.startswith("scp/tests/"):
            mapped_b_nodeids.add(n.replace("scp/tests/", "tests/internal/", 1))
        else:
            mapped_b_nodeids.add(n)
    
    print(f"[T00 Meta-Audit] Collecting candidate pytest nodeids...")
    c_nodeids = get_real_nodeids(PROJECT_ROOT)
    
    missing = mapped_b_nodeids - c_nodeids
    violations = []
    for m in sorted(missing):
        violations.append(f"FA-02: Deleted test nodeid: {m}")
    return violations

def check_code_owner_violations(policy):
    changed_files = run_git_cmd(["diff", "--cached", "--name-only"]).splitlines()
    if not changed_files:
        changed_files = run_git_cmd(["diff", "--name-only"]).splitlines()

    protected = policy.get("protected_paths", [])
    violations = []
    for f in changed_files:
        if not f: continue
        for p in protected:
            if f.startswith(p.strip('/')):
                violations.append(f"L4 Protected Path Modified: {f}")
                break
    return violations

def run_stale_code_tripwire_check():
    """T00-extension (S25, 2026): stale-code tripwire.

    Detects code-drifted-from-reality automatically: blueprint modules
    missing/unimportable, imports of non-existent symbols/modules, duplicated
    judge prompts, and metric drift (print()/silent-except > +5% vs baseline).

    Fail-closed: a tripwire tool crash is itself a violation. First run seeds
    data/governance/tripwire_baseline.json (metrics + known findings as
    BASELINE_DEBT, same philosophy as FA-01/FA-04 above); later runs reject
    only NEW findings, so pre-existing debt never turns the current tree red
    while genuine new drift does.
    """
    try:
        import importlib.util
        tool_path = PROJECT_ROOT / "tools" / "stale_code_tripwire.py"
        if not tool_path.exists():
            return [f"TRIPWIRE: tool file missing: {tool_path}"]
        spec = importlib.util.spec_from_file_location("stale_code_tripwire", tool_path)
        mod = importlib.util.module_from_spec(spec)
        # Register before exec_module: @dataclass in the tool resolves
        # annotations through sys.modules[cls.__module__].
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.run_for_t00(PROJECT_ROOT)
    except Exception as e:  # fail-closed: a broken gate must block, never pass
        logger.debug("tripwire tool failed to run", exc_info=e)
        return [f"TRIPWIRE: tool failed to run (fail-closed): {type(e).__name__}: {e}"]

def run_why_claims_check(root=None):
    """T00-extension (WHY W0): no CURRENT doc/code may call the regex-advisory WHY Gate a hard gate.

    tools/check_why_claims.py existed on main but nothing called it. Absolute check (not a
    delta): main has 0 claims, so any hit is new. Fail-closed: a missing/broken tool is a
    violation, never a silent pass.
    """
    root = Path(root) if root is not None else PROJECT_ROOT
    try:
        import importlib.util
        tool_path = root / "tools" / "check_why_claims.py"
        if not tool_path.exists():
            return [f"WHY-CLAIMS: tool file missing: {tool_path}"]
        spec = importlib.util.spec_from_file_location("check_why_claims_t00", tool_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return [f"WHY-CLAIMS: {rel}:{lineno}: {line}" for rel, lineno, line in mod.find_claims(root)]
    except Exception as e:  # fail-closed: a broken gate must block, never pass
        logger.debug("why-claims tool failed to run", exc_info=e)
        return [f"WHY-CLAIMS: tool failed to run (fail-closed): {type(e).__name__}: {e}"]

def main():
    parser = argparse.ArgumentParser(description="T00 Test-Integrity Meta-Audit Authority")
    parser.add_argument("--file", "-f", dest="target_file", default=None, help="Audit an individual file in isolation")
    args, remaining = parser.parse_known_args()

    policy = load_policy()
    trusted_base = policy["enforcement_context"].get("trusted_base", "origin/main")
    
    res = run_git_cmd(["rev-parse", "--verify", trusted_base])
    if not res and not trusted_base.startswith("origin/"):
        remote_base = f"origin/{trusted_base}"
        if run_git_cmd(["rev-parse", "--verify", remote_base]):
            trusted_base = remote_base
            res = True
    if not res:
        fail_closed(f"Trusted base '{trusted_base}' is invalid or missing. Run 'git fetch'.")
        
    print(f"[T00 Meta-Audit] Starting Test-Integrity Regression Authority...")
    print(f"[T00 Meta-Audit] Trusted Base: {trusted_base}")
    print("\n--- SCOPE & LIMITATIONS ---")
    print(" * FA-01 (Semantic Weakening): ENFORCED (skips/xfails, empty test body, placebo assertions, swallowed exceptions, zero-assert tests).")
    print(" * FA-02: ENFORCED for regressions in collected pytest nodeids")
    print(" * FA-03 (Same-SHA Evidence): NOT ENFORCED by T00 (Requires dedicated evidence tool).")
    print(" * FA-04 (Manufactured Green): Regex-based. Complex AST tracking requires L4 human review.")
    print(" * FA-05 (Self-Granting Auth): NOT ENFORCED by T00 (Requires capability scanner).")
    print(" * T00-extension WHY-CLAIMS: ENFORCED (no doc/comment may call the regex-advisory WHY Gate a hard gate; commit-message chain is checked by commit-msg hook + CI, see tools/check_why_chain.py).")
    print(" * T00-extension stale-code tripwire: blueprint-vs-code, unresolved imports, duplicated prompt logic, metric drift (delta vs data/governance/tripwire_baseline.json).")
    
    if args.target_file:
        file_path = Path(args.target_file).resolve()
        if not file_path.exists():
            fail_closed(f"Target file not found: {args.target_file}")
        try:
            rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel_path = file_path.name
        if not (rel_path.startswith("tests/") or rel_path.startswith("scp/")):
            rel_path = f"tests/{rel_path}"

        c_code = file_path.read_text(encoding="utf-8", errors="replace")
        b_code = get_git_content(trusted_base, rel_path) or ""
        new_v, debts = audit_content(c_code, b_code, rel_path)

        if debts:
            print("\n--- BASELINE_DEBT (Tracked, Not Blocking) ---")
            for debt in sorted(debts):
                print(f" [DEBT] {debt}")

        if new_v:
            print("\n" + "="*60)
            print("T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED")
            print("="*60)
            for v in sorted(new_v):
                print(f" [FAIL] {v}")
            print("\nFix violations before proceeding.")
            sys.exit(1)

        print(f"\n[T00 Meta-Audit] File {rel_path} passed integrity check (0 new regressions).")
        sys.exit(0)

    all_new_violations = []
    all_debts = []
    
    # FA-02 Real nodeid check
    all_new_violations.extend(check_real_test_deletion(trusted_base))
    
    # FA-01 and FA-04
    all_paths = set()
    for p in PROJECT_ROOT.rglob("*.py"):
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith("tests/") or rel.startswith("scp/"):
            all_paths.add(rel)
            
    out = run_git_cmd(["ls-tree", "-r", "--name-only", trusted_base])
    for line in out.splitlines():
        if line.endswith(".py") and (line.startswith("tests/") or line.startswith("scp/")):
            all_paths.add(line)
            
    # Normalize all paths to tests/internal/ for deduplication
    normalized_paths = set()
    for p in all_paths:
        if p.startswith("scp/tests/"):
            normalized_paths.add(p.replace("scp/tests/", "tests/internal/", 1))
        else:
            normalized_paths.add(p)
            
    for norm_path in normalized_paths:
        base_path = norm_path
            
        c_code = get_local_content(norm_path)
        b_code = get_git_content(trusted_base, base_path)
        new_v, debts = audit_content(c_code, b_code or "", norm_path)
        all_new_violations.extend(new_v)
        all_debts.extend(debts)
        
    l4_violations = check_code_owner_violations(policy)

    # --- T00-extension: stale-code tripwire (S25) ---
    all_new_violations.extend(run_stale_code_tripwire_check())

    # --- T00-extension: WHY claims (W0 truth check, wired 2026-10-30) ---
    all_new_violations.extend(run_why_claims_check())

    if all_debts:
        print("\n--- BASELINE_DEBT (Tracked, Not Blocking) ---")
        for debt in sorted(all_debts):
            print(f" [DEBT] {debt}")
            
    if l4_violations:
        print("\n--- L4 CODEOWNERS (Warning) ---")
        for v in l4_violations:
            print(f" [L4] {v}")
        print("Note: L4 is VERIFIED only by GitHub Server-Side Ruleset. This is a local warning.")

    if all_new_violations:
        print("\n" + "="*60)
        print("T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED")
        print("="*60)
        for v in sorted(all_new_violations):
            print(f" [FAIL] {v}")
        print("\nFix violations before proceeding.")
        sys.exit(1)
        
    print("\n[T00 Meta-Audit] All integrity checks passed (0 new regressions).")
    sys.exit(0)

if __name__ == "__main__":
    main()
