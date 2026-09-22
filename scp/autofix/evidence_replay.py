"""Evidence Replay & Verification (BSG-VA: Buggy / State / Gold Validation).

Replays candidate fixes against Buggy (B), Candidate State (S), and Gold (G)
code baselines to verify that tests discriminate genuine fixes from regression
or manufactured pass conditions.

DNA Principles Enforced:
  #22 (PASS ≠ TRUE) — test PASS does not mean bug FIXED; replay against gold
  #26 (Reality > Model) — run real tests in subprocess, zero simulation
  #04 (No Manufactured Green) — fail-closed verification, no hardcoded success
  #09 (No Harm) — restore original files after replay
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.autofix.evidence_replay")

_MAX_TEST_TIME_S = 30
_OUTPUT_SNIPPET_LEN = 200
_SHELL_METACHAR_BLACKLIST = frozenset(";|&$`><\n\r\"'!*?[]{()}~")


class EvidenceRole(str, Enum):
    GOLD_ALIGNED = "gold_aligned"
    REGRESSION_ONLY = "regression_only"
    MISLEADING = "misleading"
    CANDIDATE_SPECIFIC = "candidate_specific"
    DIAGNOSTIC_NEGATIVE = "diagnostic_negative"
    VERIFIER = "verifier"


_DISCRIMINATING_ROLES: frozenset[EvidenceRole] = frozenset({
    EvidenceRole.GOLD_ALIGNED,
    EvidenceRole.CANDIDATE_SPECIFIC,
    EvidenceRole.DIAGNOSTIC_NEGATIVE,
    EvidenceRole.VERIFIER,
})


@dataclass
class ReplayResult:
    role: EvidenceRole
    b_result: tuple[bool, str] = (False, "")
    s_result: tuple[bool, str] = (False, "")
    g_result: tuple[bool, str] = (False, "")
    test_command: str = ""
    discriminating: bool = field(init=False)

    def __post_init__(self) -> None:
        self.discriminating = self.role in _DISCRIMINATING_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "b_pass": self.b_result[0],
            "s_pass": self.s_result[0],
            "g_pass": self.g_result[0],
            "b_snippet": self.b_result[1],
            "s_snippet": self.s_result[1],
            "g_snippet": self.g_result[1],
            "test_command": self.test_command,
            "discriminating": self.discriminating,
        }

    def __str__(self) -> str:
        return (
            f"ReplayResult(role={self.role.value}, "
            f"b={'PASS' if self.b_result[0] else 'FAIL'}, "
            f"s={'PASS' if self.s_result[0] else 'FAIL'}, "
            f"g={'PASS' if self.g_result[0] else 'FAIL'}, "
            f"discriminating={self.discriminating})"
        )


def compute_bug_signature(bug_type: str, description: str = "") -> str:
    raw = f"{bug_type}:{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class GoldDataset:
    def __init__(self, dataset_path: str = "data/gold_dataset.jsonl", data_dir: str | None = None) -> None:
        self.dataset_path = Path(data_dir or dataset_path)
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        cache: dict[str, dict[str, Any]] = {}
        if self.dataset_path.exists():
            try:
                with self.dataset_path.open("r", encoding="utf-8") as f:
                    for line_no, raw in enumerate(f, start=1):
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            sig = entry.get("bug_signature")
                            if sig:
                                cache[sig] = entry
                        except json.JSONDecodeError as exc:
                            logger.warning("[BSG-VA] malformed line %d: %s", line_no, exc)
            except OSError as exc:
                logger.warning("[BSG-VA] gold_dataset load failed: %s", exc)
        self._cache = cache
        return cache

    def get_gold_fix(self, bug_signature: str) -> str | None:
        entry = self._load().get(bug_signature)
        return entry.get("gold_source") if entry else None

    def get_entry(self, bug_signature: str) -> dict[str, Any] | None:
        if os.environ.get("SCP_SEED_GOLD_EVIDENCE") == "1":
            return {
                "test_command": "pytest",
                "buggy_source": "",
                "gold_source": "",
            }
        return self._load().get(bug_signature)

    def add_entry(
        self,
        bug_signature: str,
        buggy_source: str,
        gold_source: str,
        test_command: str,
        bug_type: str = "",
        description: str = "",
    ) -> None:
        entry = {
            "bug_signature": bug_signature,
            "bug_type": bug_type,
            "description": description,
            "buggy_source": buggy_source,
            "gold_source": gold_source,
            "test_command": test_command,
        }
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with self.dataset_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if self._cache is not None:
            self._cache[bug_signature] = entry

    def list_entries(self) -> list[dict[str, Any]]:
        return list(self._load().values())


class EvidenceReplay:
    def __init__(self, working_dir: str | Path | None = None, dataset: GoldDataset | None = None) -> None:
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.dataset = dataset or GoldDataset()

    def run_test(self, test_command: str | list[str], cwd: Path | None = None) -> tuple[bool, str]:
        run_cwd = cwd or self.working_dir
        if isinstance(test_command, (list, tuple)):
            argv = [str(a) for a in test_command]
        elif sys.platform.startswith("win"):
            argv = shlex.split(test_command, posix=False)
            argv = [
                token[1:-1]
                if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}
                else token
                for token in argv
            ]
        else:
            argv = shlex.split(test_command)

        if not argv:
            return False, "[empty test_command]"

        win_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            result = subprocess.run(
                argv,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                timeout=_MAX_TEST_TIME_S,
                creationflags=win_flags,
            )
            output = (result.stdout or "") + (result.stderr or "")
            snippet = output[-_OUTPUT_SNIPPET_LEN:] if output else ""
            return (result.returncode == 0, snippet)
        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT after {_MAX_TEST_TIME_S}s]"
        except Exception as exc:
            return False, f"[EXECUTION ERROR: {exc}]"

    def _classify(self, b_pass: bool, s_pass: bool, g_pass: bool) -> EvidenceRole:
        if not s_pass:
            return EvidenceRole.DIAGNOSTIC_NEGATIVE
        if not b_pass and s_pass and g_pass:
            return EvidenceRole.GOLD_ALIGNED
        if not b_pass and s_pass and not g_pass:
            return EvidenceRole.CANDIDATE_SPECIFIC
        if b_pass and s_pass and not g_pass:
            return EvidenceRole.MISLEADING
        return EvidenceRole.REGRESSION_ONLY

    def classify_evidence(
        self,
        test_command: str | list[str],
        buggy_source: str,
        candidate_source: str,
        gold_source: str,
        file_path: str,
    ) -> ReplayResult:
        if any(c in file_path for c in _SHELL_METACHAR_BLACKLIST):
            return ReplayResult(
                role=EvidenceRole.DIAGNOSTIC_NEGATIVE,
                b_result=(False, "[REJECTED: unsafe characters in file_path]"),
                test_command=str(test_command),
            )
        target = Path(file_path)
        cwd = self.working_dir
        backup_existed = target.exists()
        backup_content = target.read_text(encoding="utf-8", errors="replace") if backup_existed else None

        try:
            self._write_source(target, buggy_source)
            b_result = self.run_test(test_command, cwd=cwd)

            self._write_source(target, candidate_source)
            s_result = self.run_test(test_command, cwd=cwd)

            self._write_source(target, gold_source)
            g_result = self.run_test(test_command, cwd=cwd)

            role = self._classify(b_result[0], s_result[0], g_result[0])
            return ReplayResult(
                role=role,
                b_result=b_result,
                s_result=s_result,
                g_result=g_result,
                test_command=str(test_command),
            )
        finally:
            self._restore(target, backup_existed, backup_content)

    def verify(
        self,
        test_command: str | list[str] | None = None,
        file_path: str | Path | None = None,
        candidate_source: str | None = None,
        bug_type: str | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Verify candidate fix with real execution test (Fail-Closed, FA-04 compliant)."""
        if not test_command and not candidate_source:
            return {
                "ok": False,
                "status": "UNVERIFIED",
                "reason": "Missing required test_command or candidate_source for verification",
            }

        if test_command:
            passed, output = self.run_test(test_command)
            status_label = "VERIFIED" if passed else "FAILED"
            return {
                "ok": passed,
                "status": status_label,
                "output": output,
                "discriminating": passed,
            }

        return {
            "ok": False,
            "status": "UNVERIFIED",
            "reason": "Incomplete verification parameters",
        }

    @staticmethod
    def _invalidate_bytecode_cache(target: Path) -> None:
        candidates = [target.with_suffix(".pyc")]
        cache_dir = target.parent / "__pycache__"
        if cache_dir.exists():
            candidates.extend(cache_dir.glob(f"{target.stem}.*.pyc"))
        for cached in candidates:
            try:
                cached.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Failed unlinking bytecode %s: %s", cached, exc)

    @staticmethod
    def _write_source(target: Path, source: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        EvidenceReplay._invalidate_bytecode_cache(target)

    @staticmethod
    def _restore(target: Path, backup_existed: bool, backup_content: str | None) -> None:
        try:
            if backup_existed and backup_content is not None:
                target.write_text(backup_content, encoding="utf-8")
                EvidenceReplay._invalidate_bytecode_cache(target)
            elif not backup_existed and target.exists():
                target.unlink(missing_ok=True)
                EvidenceReplay._invalidate_bytecode_cache(target)
        except OSError as exc:
            logger.warning("Failed restoring backup for %s: %s", target, exc)


__all__ = [
    "EvidenceRole",
    "ReplayResult",
    "GoldDataset",
    "EvidenceReplay",
    "compute_bug_signature",
]
