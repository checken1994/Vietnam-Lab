#!/usr/bin/env python3
"""Workflow contract gate: catch CI-config faults that local YAML parsing hides.

Why this exists (ci-release-gate lane, 2026-10-31): ``scp-release-gate.yml`` failed
on GitHub for 12 days (24/10 -> 31/10, every push) with "workflow file issue"
because commit e12283ca put a SECOND ``with:`` key on the same checkout step.
PyYAML (``yaml.safe_load``) silently keeps the last duplicate key, so every local
check and contract test that parsed the file said "fine", while GitHub's parser
rejected the whole workflow and no job ever ran. The same audit found the CI
environment had drifted away from the product it tests:

* workflows still installed bun although the zero-bun eradication (d5385c96,
  d0125739) made ``shutil.which("bun") is None`` a test invariant -> 15 reds;
* workflows pinned Node 20 although the Dockerfile moved to Node 24 because the
  dashboard tests run ``node --experimental-strip-types`` (needs >= 22.6);
* full-suite jobs checked out shallow history although T00 fail-closes when the
  trusted base ``main``/``origin/main`` is missing;
* ``container-publish.yml`` built the Dockerfile's LAST stage (``audit``) and
  pushed it to GHCR although the Dockerfile marks that stage LOCAL-ONLY because
  it carries ``.git`` history.

Each check below is a floor, not proof: it only proves the workflow files are
well-formed and consistent with the product's declared runtime. It does not
prove a GitHub run is green (that needs a push).

Exit codes: 0 = ok, 1 = contract violation, 2 = tool/usage error (fail-closed).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Minimum Node major for ``node --experimental-strip-types`` (added in 22.6).
STRIP_TYPES_MIN_MAJOR = 22
# Dockerfile stages that must never be published (they carry repo history/tests).
LOCAL_ONLY_STAGES = {"audit"}


class DuplicateKeyError(ValueError):
    """Raised when a YAML mapping repeats a key (GitHub rejects; PyYAML hides it)."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of last-wins."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    seen: dict[Any, int] = {}
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        line = key_node.start_mark.line + 1
        if key in seen:
            raise DuplicateKeyError(
                f"duplicate key {key!r} at line {line} (first at line {seen[key]})"
            )
        seen[key] = line
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def strict_load(text: str) -> Any:
    return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - SafeLoader subclass


def _triggers(data: dict) -> Any:
    # PyYAML (YAML 1.1) reads the bare key ``on`` as boolean True.
    return data.get("on", data.get(True))


def _steps(job: dict) -> list[dict]:
    steps = job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _uses(step: dict) -> str:
    return str(step.get("uses", ""))


def _run(step: dict) -> str:
    return str(step.get("run", ""))


def dockerfile_node_major(root: Path) -> int | None:
    """Node major used by the Dockerfile's node stage (single source of truth)."""
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        return None
    match = re.search(r"^FROM\s+node:(\d+)", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    return int(match.group(1)) if match else None


def _is_full_suite(run: str) -> bool:
    """A step that runs the whole pytest suite / T00 (needs git history)."""
    if "scripts.run_system_audit_strict" in run or "scripts/enforce_baseline.py" in run:
        return True
    if "tools/t00_meta_audit.py" in run:
        return True
    for line in run.replace(">-", "").splitlines():
        if re.search(r"python -m pytest\b", line) and "tests/" not in line:
            # bare ``python -m pytest -q --tb=short`` = whole suite
            if not re.search(r"\S+\.py\b", line.split("pytest", 1)[1]):
                return True
    return False


def check_workflow(path: Path, node_major: int | None) -> list[str]:
    name = path.name
    try:
        data = strict_load(path.read_text(encoding="utf-8"))
    except DuplicateKeyError as exc:
        return [f"{name}: {exc} — GitHub rejects the workflow ('workflow file issue'), no job runs"]
    except yaml.YAMLError as exc:
        return [f"{name}: YAML parse error: {exc}"]
    if not isinstance(data, dict):
        return [f"{name}: top level is not a mapping"]

    errors: list[str] = []
    if _triggers(data) is None:
        errors.append(f"{name}: no 'on:' trigger")
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return errors + [f"{name}: no jobs"]

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            errors.append(f"{name}:{job_id}: job is not a mapping")
            continue
        steps = _steps(job)
        where = f"{name}:{job_id}"

        # I1 zero-bun: the product forbids bun on PATH (eradication invariant tests).
        for step in steps:
            if _uses(step).startswith("oven-sh/setup-bun"):
                errors.append(
                    f"{where}: installs bun ({_uses(step).split('@')[0]}) but zero-bun "
                    "eradication requires shutil.which('bun') is None"
                )

        # I2/I3 Node version: >= strip-types floor, equal to Dockerfile, verify step agrees.
        node_versions: list[str] = []
        for step in steps:
            if _uses(step).startswith("actions/setup-node@"):
                version = str((step.get("with") or {}).get("node-version", ""))
                node_versions.append(version)
                major_match = re.match(r"(\d+)", version)
                if not major_match:
                    errors.append(f"{where}: setup-node without a pinned numeric node-version")
                    continue
                major = int(major_match.group(1))
                if major < STRIP_TYPES_MIN_MAJOR:
                    errors.append(
                        f"{where}: node-version {version} < {STRIP_TYPES_MIN_MAJOR}; tests run "
                        "node --experimental-strip-types (needs >= 22.6)"
                    )
                if node_major is not None and major != node_major:
                    errors.append(
                        f"{where}: node-version {version} != Dockerfile node:{node_major} "
                        "(CI must test the runtime the product ships)"
                    )
        for step in steps:
            text = _run(step) + " " + str(step.get("name", ""))
            for pinned in re.findall(r"process\.versions\.node\.split\('\.'\)\[0\] !== '(\d+)'", text):
                if pinned not in [v.split(".")[0] for v in node_versions]:
                    errors.append(f"{where}: Node verify step pins {pinned} but setup-node provides {node_versions}")
            for listed in re.findall(r"\[((?:'\d+',?\s*)+)\]\.includes\(process\.versions\.node", text):
                allowed = re.findall(r"'(\d+)'", listed)
                provided = [v.split(".")[0] for v in node_versions]
                if not set(provided) <= set(allowed):
                    errors.append(f"{where}: Node verify allows {allowed} but setup-node provides {provided}")
            name_pin = re.match(r"Verify Node (\d+) runtime", str(step.get("name", "")))
            if name_pin and name_pin.group(1) not in [v.split(".")[0] for v in node_versions]:
                errors.append(f"{where}: step '{step.get('name')}' does not match setup-node {node_versions}")

        # I4 full-suite / T00 jobs need full git history (T00 trusted base main/origin/main).
        if any(_is_full_suite(_run(s)) for s in steps):
            for step in steps:
                if _uses(step).startswith("actions/checkout@"):
                    depth = (step.get("with") or {}).get("fetch-depth")
                    if str(depth) != "0":
                        errors.append(
                            f"{where}: runs the full suite/T00 but checkout fetch-depth={depth!r}; "
                            "T00 fail-closes without trusted base main/origin/main (need 0)"
                        )

        # I5 publish only the runtime stage, never a LOCAL-ONLY stage.
        job_text = "\n".join(_run(s) for s in steps)
        if "docker push" in job_text:
            # command lines only (a comment saying "docker builds ..." is not a build);
            # follow backslash continuations so flags on later lines are seen.
            for build in re.findall(r"(?m)^[ \t]*docker (?:buildx )?build\b(?:[^\n]*\\\n)*[^\n]*", job_text):
                target = re.search(r"--target[ =](\S+)", build)
                if not target:
                    errors.append(
                        f"{where}: 'docker build' without --target in a publishing job builds the "
                        f"Dockerfile's LAST stage (LOCAL-ONLY {sorted(LOCAL_ONLY_STAGES)} carries .git)"
                    )
                elif target.group(1) in LOCAL_ONLY_STAGES:
                    errors.append(f"{where}: publishes LOCAL-ONLY stage {target.group(1)!r}")
    return errors


def check_repo(root: Path) -> dict:
    workflow_dir = root / ".github" / "workflows"
    files = sorted(list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml")))
    if not files:
        raise FileNotFoundError(f"no workflow files under {workflow_dir}")
    node_major = dockerfile_node_major(root)
    errors: list[str] = []
    if node_major is None:
        errors.append("Dockerfile: no 'FROM node:<major>' stage found (node runtime source of truth)")
    for path in files:
        errors.extend(check_workflow(path, node_major))
    return {
        "status": "PASS_WITHIN_SCOPE" if not errors else "FAIL",
        "scope": "static workflow contract (not a GitHub run)",
        "workflows_checked": [p.name for p in files],
        "dockerfile_node_major": node_major,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = check_repo(args.root.resolve())
    except Exception as exc:  # fail-closed: a broken tool is never a pass
        print(f"workflow_contract: TOOL ERROR (fail-closed): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for err in report["errors"]:
            print(f"workflow_contract: VIOLATION {err}")
        print(
            f"workflow_contract: status={report['status']} workflows={len(report['workflows_checked'])} "
            f"violations={len(report['errors'])} dockerfile_node={report['dockerfile_node_major']}"
        )
    return 0 if report["status"] == "PASS_WITHIN_SCOPE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
