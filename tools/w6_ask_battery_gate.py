"""W6 /ask battery gate-check (fail-closed).

Usage:
    python tools/w6_ask_battery_gate.py <baseline_ref.json> <run1.json> <run2.json>
    python tools/w6_ask_battery_gate.py --self-test

Gate rules — PASS (exit 0) only when BOTH runs satisfy ALL of:
  R1. exactly 16 probes (a JSON list of 16, or an object {"probes": [...16]}).
  R2. every probe has httpStatus == 200.
  R3. at least 14 of 16 probes are PASS probes. A probe is a PASS probe iff
      verdict == 'PASS' AND run_status == 'SUCCESS'. Both fields are read
      from probe-level keys first, then from probe['response']; run_status
      additionally falls back to a run-level 'run_status' key (wrapper shape).
      A field that cannot be found counts as NOT PASS (fail-closed).
  R4. zero PASS probes in a run -> FAIL (explicit regression-vs-baseline rule).
  R5. baseline regression: any probe that is a PASS probe in baseline_ref but
      not a PASS probe in a run -> FAIL (PASS->FAIL flip vs baseline).

Missing/unreadable files, invalid JSON, or wrong shape -> exit != 0.
Exit codes: 0 PASS, 1 gate FAIL, 2 shape/IO error. All failures are final.

Self-test (--self-test) builds fake fixtures in a temp dir and asserts:
  S1. run2 has 13/16 PASS          -> exit != 0
  S2. both runs exactly 14/16 PASS -> exit 0
  S3. a required file is missing   -> exit != 0
Self-test exits 0 iff all three scenarios behave as specified.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REQUIRED_PROBES = 16
MIN_PASS_PROBES = 14


class GateError(Exception):
    """Shape/IO error — fail-closed, distinct exit code 2."""


def _load_run(path_str: str) -> tuple[list, str | None]:
    """Load a run file. Returns (probes, run_level_status). Fail-closed."""
    path = Path(path_str)
    if not path.is_file():
        raise GateError(f"missing run file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"unreadable/invalid JSON: {path}: {exc}") from exc
    run_status: str | None = None
    if isinstance(data, list):
        probes = data
    elif isinstance(data, dict):
        probes = data.get("probes")
        run_status = data.get("run_status")
        if not isinstance(probes, list):
            raise GateError(f"object run file has no 'probes' list: {path}")
    else:
        raise GateError(f"run file must be a list or object: {path}")
    if len(probes) != REQUIRED_PROBES:
        raise GateError(
            f"{path}: expected {REQUIRED_PROBES} probes, got {len(probes)}"
        )
    return probes, run_status


def _field(probe: dict, name: str, run_status: str | None):
    if isinstance(probe, dict):
        if name in probe:
            return probe[name]
        response = probe.get("response")
        if isinstance(response, dict) and name in response:
            return response[name]
    if name == "run_status" and run_status is not None:
        return run_status
    return None


def _probe_pass(probe: dict, run_status: str | None) -> bool:
    return (
        _field(probe, "verdict", run_status) == "PASS"
        and _field(probe, "run_status", run_status) == "SUCCESS"
    )


def _evaluate(label: str, baseline_probes: list, run_path: str) -> tuple[bool, dict]:
    probes, run_status = _load_run(run_path)
    for i, probe in enumerate(probes):
        if not isinstance(probe, dict):
            raise GateError(f"{run_path}: probe #{i} is not an object")
        if probe.get("httpStatus") != 200:
            print(
                f"GATE-FAIL[{label}]: probe {probe.get('id', i)} "
                f"httpStatus={probe.get('httpStatus')!r} != 200"
            )
            return False, {"pass_probes": 0, "reason": f"httpStatus!=200@{probe.get('id', i)}"}
    pass_flags = [_probe_pass(p, run_status) for p in probes]
    pass_count = sum(pass_flags)
    if pass_count == 0:
        print(f"GATE-FAIL[{label}]: 0/16 probes PASS (regression vs baseline)")
        return False, {"pass_probes": 0, "reason": "zero-pass"}
    for i, (b_probe, r_flag) in enumerate(zip(baseline_probes, pass_flags)):
        if _probe_pass(b_probe, None) and not r_flag:
            print(
                f"GATE-FAIL[{label}]: probe {probes[i].get('id', i)} was PASS in "
                "baseline but is not PASS in this run"
            )
            return False, {"pass_probes": pass_count, "reason": f"baseline-regression@{probes[i].get('id', i)}"}
    if pass_count < MIN_PASS_PROBES:
        print(
            f"GATE-FAIL[{label}]: only {pass_count}/{REQUIRED_PROBES} PASS probes "
            f"(need >= {MIN_PASS_PROBES})"
        )
        return False, {"pass_probes": pass_count, "reason": "below-threshold"}
    print(f"GATE-OK[{label}]: {pass_count}/{REQUIRED_PROBES} PASS probes, all httpStatus==200")
    return True, {"pass_probes": pass_count, "reason": "ok"}


def run_gate(baseline_path: str, run1_path: str, run2_path: str) -> int:
    try:
        baseline_probes, _ = _load_run(baseline_path)
    except GateError as exc:
        print(f"GATE-ERROR(baseline): {exc}")
        return 2
    for label, run_path in (("run1", run1_path), ("run2", run2_path)):
        try:
            ok, info = _evaluate(label, baseline_probes, run_path)
        except GateError as exc:
            print(f"GATE-ERROR[{label}]: {exc}")
            return 2
        if label == "run1":
            ok1, info1 = ok, info
        else:
            ok2, info2 = ok, info
    verdict = {
        "verdict": "PASS" if (ok1 and ok2) else "FAIL",
        "run1": info1,
        "run2": info2,
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return 0 if verdict["verdict"] == "PASS" else 1


# ----------------------------- self-test ------------------------------------


def _fixture(count_pass: int, count_http_ok: int = 16) -> dict:
    probes = []
    for i in range(REQUIRED_PROBES):
        pid = f"q{i + 1:02d}"
        passed = i < count_pass
        probes.append(
            {
                "id": pid,
                "httpStatus": 200 if i < count_http_ok else 500,
                "response": {
                    "verdict": "PASS" if passed else "UNKNOWN",
                    "run_status": "SUCCESS" if passed else "FAILED",
                },
            }
        )
    return {"run_status": "SUCCESS", "probes": probes}


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="w6_gate_selftest_"))
    results: list[tuple[str, bool, int]] = []

    # S1: run2 has only 13/16 PASS -> must NOT pass.
    base_p = tmp / "s1_baseline.json"
    r1_p = tmp / "s1_run1.json"
    r2_p = tmp / "s1_run2.json"
    _write(base_p, _fixture(14))
    _write(r1_p, _fixture(14))
    _write(r2_p, _fixture(13))
    rc = run_gate(str(base_p), str(r1_p), str(r2_p))
    results.append(("S1 run2=13/16 -> exit!=0", rc != 0, rc))

    # S2: both runs exactly 14/16 PASS, consistent with baseline -> PASS.
    base_p = tmp / "s2_baseline.json"
    r1_p = tmp / "s2_run1.json"
    r2_p = tmp / "s2_run2.json"
    _write(base_p, _fixture(14))
    _write(r1_p, _fixture(14))
    _write(r2_p, _fixture(14))
    rc = run_gate(str(base_p), str(r1_p), str(r2_p))
    results.append(("S2 both=14/16 -> exit==0", rc == 0, rc))

    # S3: missing file -> fail-closed exit != 0.
    base_p = tmp / "s3_baseline.json"
    _write(base_p, _fixture(14))
    rc = run_gate(str(base_p), str(tmp / "s3_missing_run1.json"), str(tmp / "s3_run2.json"))
    results.append(("S3 missing file -> exit!=0", rc != 0, rc))

    all_ok = True
    print("--- w6_ask_battery_gate self-test ---")
    for name, ok, rc in results:
        print(f"{'OK ' if ok else 'FAIL'} {name} (exit={rc})")
        all_ok = all_ok and ok
    print(f"SELF-TEST {'PASS' if all_ok else 'FAIL'} (fixtures in {tmp})")
    return 0 if all_ok else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 3:
        print(
            "usage: w6_ask_battery_gate.py <baseline_ref.json> <run1.json> <run2.json>\n"
            "       w6_ask_battery_gate.py --self-test",
            file=sys.stderr,
        )
        return 2
    return run_gate(argv[0], argv[1], argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
