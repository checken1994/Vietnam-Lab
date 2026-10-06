"""Independent re-count of battery bundle claims from raw JSON (Zero-Trust) v2.

Read-only probe for the 2026-10-06 gates/claims audit. Handles both bundle
layouts:
  - wave10..wave13: battery/run_a.json, run_b.json, run_c.json
  - wave6_re/wave7: battery/after_ref.json (A), after_run1.json (B),
    after_run2.json (C)
Recounts Option-A answer-rate (PASS-thuan + ABSTAIN-delivered, HTTP 200),
KILL-class governance, q07 weather content (wave13), LLM census totals from
llm_call_compare.csv, PASS-with-0-LLM-calls cases, gate verdict, and
sha256-sum spot-checks against bundle_hashes.txt (hash *file format).
Exit 0 iff every recomputed number matches the GA.md B1b-audited values.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

CI = Path("D:/scp/reports/scp_acceptance_ci")

EXPECTED_RATES = {
    "wave13_battery": (14, 15, 14),
    "wave12_battery": (14, 12, 14),
    "wave11_battery": (14, 12, 13),
    "wave10_battery": (12, 13, 14),
    "wave7_battery": (15, 13, 15),
    "wave6_re": (13, 15, 15),
}
EXPECTED_CENSUS = {
    "wave13_battery": (89, 88, 89),
    "wave12_battery": (76, 71, 93),
    "wave11_battery": (104, 102, 92),
    "wave10_battery": (93, 93, 105),
    "wave7_battery": (91, 102, 94),
    "wave6_re": (53, 53, 51),
}
RUN_FILES = {
    "wave6_re": ("after_ref.json", "after_run1.json", "after_run2.json"),
    "wave7_battery": ("after_ref.json", "after_run1.json", "after_run2.json"),
}
DEFAULT_RUN_FILES = ("run_a.json", "run_b.json", "run_c.json")
# PASS-with-0-LLM-calls allowed only for data-API fork probes (documented).
FORK_PROBES = {"q02", "q07", "q14"}


def classify_probe(entry: dict) -> str:
    resp = entry.get("response", {}) or {}
    http_ok = entry.get("httpStatus") == 200
    verdict = resp.get("verdict")
    gov = resp.get("governance_decision")
    run_status = resp.get("run_status")
    answer = str(resp.get("final_answer", ""))
    if verdict == "PASS" and run_status == "SUCCESS" and http_ok:
        return "PASS"
    if verdict == "ABSTAIN" and gov == "ABSTAIN" and "[unverified" in answer.lower() and http_ok:
        return "ABSTAIN_DELIVERED"
    if str(gov).upper() == "KILL":
        return "KILL"
    return "OTHER"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_bundle(bundle: str) -> dict:
    out = {"bundle": bundle, "expected_rate": EXPECTED_RATES[bundle],
           "expected_census": EXPECTED_CENSUS[bundle], "runs": [], "kill_total": 0,
           "gate": None, "census": None, "pass0calls_cases": [],
           "pass0calls_undocumented": [], "mismatches": []}
    names = RUN_FILES.get(bundle, DEFAULT_RUN_FILES)
    for tag, fname in zip("ABC", names):
        fpath = CI / bundle / "battery" / fname
        if not fpath.exists():
            out["mismatches"].append(f"missing {fpath}")
            continue
        probes = json.loads(fpath.read_text(encoding="utf-8"))
        counts = {"PASS": 0, "ABSTAIN_DELIVERED": 0, "KILL": 0, "OTHER": 0}
        per_probe = {}
        for p in probes:
            cls = classify_probe(p)
            counts[cls] += 1
            per_probe[p.get("id")] = cls
        out["kill_total"] += counts["KILL"]
        out["runs"].append({"run": tag, "answer_rate": counts["PASS"] + counts["ABSTAIN_DELIVERED"],
                            "pass": counts["PASS"], "abstain_delivered": counts["ABSTAIN_DELIVERED"],
                            "kill": counts["KILL"], "other": counts["OTHER"]})
        if bundle == "wave13_battery" and tag in "ABC":
            q07 = next((p for p in probes if p.get("id") == "q07"), None)
            if q07:
                resp = q07.get("response", {})
                ans = str(resp.get("final_answer", ""))
                cls = resp.get("v98_classification", {}) or {}
                out.setdefault("q07", {})[tag] = {
                    "class": per_probe.get("q07"),
                    "temp": (re.findall(r"-?\d+[.,]\d+\s*°C", ans) or [None])[0],
                    "humidity_pct": (re.findall(r"độ ẩm[^\d]*(\d+)\s*%", ans, re.I) or [None])[0],
                    "open_meteo": ("Open-Meteo" in ans) or ("open-meteo" in str(cls.get("api_url", ""))),
                    "route": cls.get("route"),
                    "api_name": cls.get("api_name"),
                    "answer_head": ans[:90],
                }

    csv_path = CI / bundle / "battery" / "llm_call_compare.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if rows:
            cols = list(rows[0].keys())
            run_cols = [c for c in cols if re.fullmatch(r"run_[abc]_count", c)] or \
                       [c for c in cols if re.fullmatch(r"[abc]", c)]
            pid_col = next((c for c in cols if c.lower() in ("probe_id", "probe", "id")), cols[0])
            if run_cols:
                totals = {k: 0 for k in "ABC"}
                for r in rows:
                    for i, c in enumerate(run_cols):
                        try:
                            totals["ABC"[i]] += int(float(r.get(c, "0") or 0))
                        except ValueError:
                            pass
                    pid = str(r.get(pid_col, "")).strip()
                    counts_zero = [str(r.get(c, "0") or 0) == "0" for c in run_cols]
                    for i, zero in enumerate(counts_zero):
                        if zero and pid:
                            out["pass0calls_cases"].append(("ABC"[i], pid))
                            if pid not in FORK_PROBES:
                                out["pass0calls_undocumented"].append(("ABC"[i], pid))
                out["census"] = totals

    gate_path = CI / bundle / "battery" / "gate_check_output.txt"
    if gate_path.exists():
        txt = gate_path.read_text(encoding="utf-8")
        m = re.search(r'"verdict":\s*"(PASS|FAIL)"', txt)
        out["gate"] = {"verdict": m.group(1) if m else "UNKNOWN", "exit0_marker": "EXIT=0" in txt}

    hashes_path = CI / bundle / "bundle_hashes.txt"
    checked = []
    if hashes_path.exists():
        htxt = hashes_path.read_text(encoding="utf-8")
        recorded = dict(re.findall(r"^([0-9a-f]{64}) \*(\S+)$", htxt, re.M))
        recorded = {v: k for k, v in recorded.items()}
        for rel in ("run_a.json", "after_ref.json", "gate_check_output.txt",
                    "stability_checks.txt", "option_a_counts.txt"):
            f = CI / bundle / "battery" / rel
            if f.exists() and rel in recorded:
                actual = sha256(f)
                checked.append({"file": rel, "match": recorded[rel] == actual,
                                "recorded": recorded[rel], "actual": actual})
    out["hash_check"] = checked

    rates = tuple(r["answer_rate"] for r in out["runs"]) if len(out["runs"]) == 3 else None
    if rates != EXPECTED_RATES[bundle]:
        out["mismatches"].append(f"answer-rate {rates} != expected {EXPECTED_RATES[bundle]}")
    if out["kill_total"] != 0:
        out["mismatches"].append(f"KILL total {out['kill_total']} != 0")
    if out["census"] is not None:
        cen = tuple(out["census"].get(k, 0) for k in "ABC")
        if cen != EXPECTED_CENSUS[bundle]:
            out["mismatches"].append(f"census {cen} != expected {EXPECTED_CENSUS[bundle]}")
    if out["pass0calls_undocumented"]:
        out["mismatches"].append(f"PASS-with-0-calls outside documented fork set: {out['pass0calls_undocumented']}")
    return out


def main() -> int:
    results = [audit_bundle(b) for b in EXPECTED_RATES]
    ok = all(not r["mismatches"] for r in results)
    print(json.dumps({"all_match": ok, "bundles": results}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
