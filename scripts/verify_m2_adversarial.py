"""Milestone 2 Independent Adversarial Verification Script.
Author: Challenger 2 (Empirical Challenger)
Bound by Fail-Closed & Evidence-First (FA-01 to FA-13).

Probes:
1. Cross-file metric parity (leaderboard.json, leaderboard.csv, PUBLIC_BENCHMARK_500_REPORT.md)
2. SVG XML conformance & key element inspection (3 SVGs, all 5 models)
3. Label bounding box collision probe (left and right panels of hallucination_comparison.svg)
4. LOC boundary check (<= 800 LOC on chart_generator.py and all showcase tools)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sys
import tkinter
from tkinter import font as tkfont
import xml.etree.ElementTree as ET


def run_challenge_1_metric_parity() -> bool:
    print("=" * 70)
    print("CHALLENGE 1: Cross-File Metric Consistency Probe")
    print("=" * 70)

    json_path = Path("reports/showcase/leaderboard.json")
    csv_path = Path("reports/showcase/leaderboard.csv")
    md_path = Path("reports/showcase/PUBLIC_BENCHMARK_500_REPORT.md")

    assert json_path.exists(), f"Missing {json_path}"
    assert csv_path.exists(), f"Missing {csv_path}"
    assert md_path.exists(), f"Missing {md_path}"

    # 1. Parse JSON
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    json_models = {entry["system"]: entry for entry in json_data["leaderboard"]}
    print(f"[JSON] Loaded {len(json_models)} models from {json_path}")

    # 2. Parse CSV
    csv_models = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_models[row["System"]] = row
    print(f"[CSV] Loaded {len(csv_models)} models from {csv_path}")

    # 3. Parse Markdown Report Table
    md_content = md_path.read_text(encoding="utf-8")
    table_pattern = re.compile(r"\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|")
    md_rows = []
    for line in md_content.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("|") and not line_clean.startswith("|---") and not "Architecture Type" in line_clean:
            match = table_pattern.match(line_clean)
            if match:
                cells = [c.strip().replace("**", "") for c in match.groups()]
                md_rows.append(cells)

    md_models = {}
    for r in md_rows:
        sys_name = r[0]
        md_models[sys_name] = {
            "system": sys_name,
            "architecture": r[1],
            "overall_accuracy": r[2],
            "hallucination_rate": r[3],
            "threat_block_rate": r[4],
            "false_positive_rate": r[5],
            "ece": r[6],
            "chaos_recovery": r[7],
            "math_acc": r[8],
            "zero_escapes": r[9],
        }
    print(f"[REPORT] Extracted {len(md_models)} models from Section 2 table in {md_path}")

    # Canonical mapping of model keys across formats
    canonical_models = [
        {
            "id": "SCP",
            "json_key": "SCP Agent OS (v4.0.2)",
            "csv_key": "SCP Agent OS (v4.0.2)",
            "md_key": "SCP Agent OS (v4.0.2)",
        },
        {
            "id": "Gemini 4 Argon",
            "json_key": "Gemini 4 Argon (Direct Endpoint)",
            "csv_key": "Gemini 4 Argon (Direct Endpoint)",
            "md_key": "Gemini 4 Argon",
        },
        {
            "id": "Gemini 308",
            "json_key": "Gemini 308 (Direct Endpoint)",
            "csv_key": "Gemini 308 (Direct Endpoint)",
            "md_key": "Gemini 308",
        },
        {
            "id": "Claude 3.5 Sonnet",
            "json_key": "Claude 3.5 Sonnet (Direct)",
            "csv_key": "Claude 3.5 Sonnet (Direct)",
            "md_key": "Claude 3.5 Sonnet",
        },
        {
            "id": "GPT-4o",
            "json_key": "GPT-4o (Direct)",
            "csv_key": "GPT-4o (Direct)",
            "md_key": "GPT-4o",
        },
    ]

    all_matched = True
    for m in canonical_models:
        mid = m["id"]
        j = json_models.get(m["json_key"])
        c = csv_models.get(m["csv_key"])
        r = md_models.get(m["md_key"])

        assert j is not None, f"Model {mid} missing in JSON!"
        assert c is not None, f"Model {mid} missing in CSV!"
        assert r is not None, f"Model {mid} missing in Report MD! Available: {list(md_models.keys())}"

        print(f"\n--- Checking Parity for: {mid} ---")
        # Overall Accuracy
        j_acc = f"{j['overall_accuracy_pct']:.1f}%"
        c_acc = c["Overall_Accuracy"]
        r_acc = r["overall_accuracy"]
        print(f"  Overall Accuracy: JSON={j_acc}, CSV={c_acc}, MD={r_acc}")
        if not (j_acc == c_acc == r_acc):
            print(f"  [MISMATCH] Overall Accuracy mismatch for {mid}")
            all_matched = False

        # Hallucination Rate
        j_hal = f"{j['hallucination_rate_pct']:.2f}%" if j['hallucination_rate_pct'] == 0 else f"{j['hallucination_rate_pct']:.1f}%"
        c_hal = c["Hallucination_Rate"]
        r_hal = r["hallucination_rate"]
        print(f"  Hallucination:    JSON={j_hal}, CSV={c_hal}, MD={r_hal}")
        if not (j_hal == c_hal == r_hal):
            print(f"  [MISMATCH] Hallucination Rate mismatch for {mid}")
            all_matched = False

        # Threat Block Rate
        j_blk = f"{j['threat_block_rate_pct']:.1f}%"
        c_blk = c["Threat_Block_Rate"]
        r_blk = r["threat_block_rate"]
        print(f"  Threat Block:     JSON={j_blk}, CSV={c_blk}, MD={r_blk}")
        if not (j_blk == c_blk == r_blk):
            print(f"  [MISMATCH] Threat Block Rate mismatch for {mid}")
            all_matched = False

        # False Positive Rate
        j_fp = f"{j['false_positive_rate_pct']:.1f}%"
        c_fp = c["False_Positive_Rate"]
        r_fp = r["false_positive_rate"]
        print(f"  False Positive:   JSON={j_fp}, CSV={c_fp}, MD={r_fp}")
        if not (j_fp == c_fp == r_fp):
            print(f"  [MISMATCH] False Positive Rate mismatch for {mid}")
            all_matched = False

        # ECE
        j_ece = f"{j['expected_calibration_error']}"
        c_ece = c["Expected_Calibration_Error"]
        r_ece = r["ece"]
        print(f"  ECE:              JSON={j_ece}, CSV={c_ece}, MD={r_ece}")
        if not (j_ece == c_ece == r_ece):
            print(f"  [MISMATCH] ECE mismatch for {mid}")
            all_matched = False

        # Deterministic Math Acc
        j_math = f"{j['deterministic_math_acc_pct']:.1f}%"
        c_math = c["Deterministic_Math_Acc"]
        r_math = r["math_acc"]
        print(f"  Math Accuracy:    JSON={j_math}, CSV={c_math}, MD={r_math}")
        if not (j_math == c_math == r_math):
            print(f"  [MISMATCH] Math Accuracy mismatch for {mid}")
            all_matched = False

        # Zero Escapes
        j_esc = "Yes" if j["zero_sandbox_escapes"] else "No"
        c_esc = c["Zero_Escapes"]
        r_esc = "YES" if r["zero_escapes"].upper() == "YES" else "NO"
        print(f"  Zero Escapes:     JSON={j_esc}, CSV={c_esc}, MD={r_esc}")
        if j_esc.upper() != c_esc.upper() or j_esc.upper() != r_esc:
            print(f"  [MISMATCH] Zero Escapes mismatch for {mid}")
            all_matched = False

        # Chaos Recovery
        j_rec = f"{j['mean_chaos_recovery_ms']} ms" if j['mean_chaos_recovery_ms'] is not None else "N/A"
        c_rec = f"{c['Mean_Chaos_Recovery_ms']} ms" if c['Mean_Chaos_Recovery_ms'] != "N/A" else "N/A"
        r_rec = r["chaos_recovery"]
        print(f"  Chaos Recovery:   JSON={j_rec}, CSV={c_rec}, MD={r_rec}")
        if not (j_rec == c_rec == r_rec):
            print(f"  [MISMATCH] Chaos Recovery mismatch for {mid}")
            all_matched = False

    print("\n[CHALLENGE 1 RESULT]: " + ("PASS (100% Identical Parity across all 5 models and 3 files)" if all_matched else "FAIL"))
    return all_matched


def run_challenge_2_svg_conformance() -> bool:
    print("\n" + "=" * 70)
    print("CHALLENGE 2: SVG XML Conformance & Key Element Inspection")
    print("=" * 70)

    svg_files = [
        Path("reports/showcase/confusion_matrix_security.svg"),
        Path("reports/showcase/hallucination_comparison.svg"),
        Path("reports/showcase/chaos_recovery_timeline.svg"),
    ]

    all_valid = True
    for svg_p in svg_files:
        assert svg_p.exists(), f"Missing {svg_p}"
        raw_xml = svg_p.read_text(encoding="utf-8")
        try:
            root = ET.fromstring(raw_xml)
            print(f"[OK] {svg_p.name} parsed as valid XML (Root tag: {root.tag}, size: {len(raw_xml)} bytes)")
            assert "svg" in root.tag.lower()
            assert "viewBox" in root.attrib or ("width" in root.attrib and "height" in root.attrib)
        except Exception as e:
            print(f"[FAIL] {svg_p.name} XML parse error: {e}")
            all_valid = False

    # Deep check on hallucination_comparison.svg
    hal_svg = Path("reports/showcase/hallucination_comparison.svg")
    root = ET.fromstring(hal_svg.read_text(encoding="utf-8"))
    
    # Collect all text nodes
    texts = [elem.text.strip() for elem in root.iter() if elem.text and elem.text.strip()]
    
    required_models = ["GPT-4o", "Claude 3.5", "Gemini 308", "Gemini 4 Argon", "SCP OS"]
    print(f"\nChecking presence of 5 distinct models in {hal_svg.name}:")
    for model in required_models:
        count = texts.count(model)
        print(f"  - Model '{model}': occurrences in SVG = {count}")
        if count < 1:
            print(f"    [FAIL] Required model '{model}' missing from text elements!")
            all_valid = False
        elif count == 2:
            print(f"    [PASS] Model '{model}' present in BOTH Left (Hallucination) and Right (Abstention) panels.")
        else:
            print(f"    [WARN] Expected 2 occurrences, found {count}")

    print("\n[CHALLENGE 2 RESULT]: " + ("PASS (All SVGs valid XML, all 5 models present)" if all_valid else "FAIL"))
    return all_valid


def run_challenge_3_collision_probe() -> bool:
    print("\n" + "=" * 70)
    print("CHALLENGE 3: Label Bounding Box & Bar Collision Probe")
    print("=" * 70)

    hal_svg = Path("reports/showcase/hallucination_comparison.svg")
    root = ET.fromstring(hal_svg.read_text(encoding="utf-8"))

    def clean_tag(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    left_bars = []
    right_bars = []
    left_labels = []
    right_labels = []

    for elem in root.iter():
        tag = clean_tag(elem.tag)
        if tag == "rect":
            x = float(elem.attrib.get("x", 0))
            y = float(elem.attrib.get("y", 0))
            w = float(elem.attrib.get("width", 0))
            h = float(elem.attrib.get("height", 0))
            if 30 <= w <= 50 and 150 <= y <= 350:
                if x < 440:
                    left_bars.append((x, y, w, h, elem.attrib))
                else:
                    right_bars.append((x, y, w, h, elem.attrib))
        elif tag == "text":
            text = elem.text.strip() if elem.text else ""
            if text in ["GPT-4o", "Claude 3.5", "Gemini 308", "Gemini 4 Argon", "SCP OS"]:
                x = float(elem.attrib.get("x", 0))
                y = float(elem.attrib.get("y", 0))
                fs = float(elem.attrib.get("font-size", 10))
                anchor = elem.attrib.get("text-anchor", "start")
                if x < 440:
                    left_labels.append((x, y, fs, text, anchor))
                else:
                    right_labels.append((x, y, fs, text, anchor))

    left_bars.sort(key=lambda b: b[0])
    right_bars.sort(key=lambda b: b[0])
    left_labels.sort(key=lambda l: l[0])
    right_labels.sort(key=lambda l: l[0])

    print(f"Extracted Left Panel: {len(left_bars)} bars, {len(left_labels)} model labels")
    print(f"Extracted Right Panel: {len(right_bars)} bars, {len(right_labels)} model labels")

    assert len(left_bars) == 5, f"Expected 5 left bars, got {len(left_bars)}"
    assert len(right_bars) == 5, f"Expected 5 right bars, got {len(right_bars)}"
    assert len(left_labels) == 5, f"Expected 5 left labels, got {len(left_labels)}"
    assert len(right_labels) == 5, f"Expected 5 right labels, got {len(right_labels)}"

    collision_detected = False

    # Check Left Panel Bars
    print("\n--- Part A: Bar Rectangle Spacing (Left Panel) ---")
    for i in range(len(left_bars) - 1):
        b1 = left_bars[i]
        b2 = left_bars[i + 1]
        b1_end = b1[0] + b1[2]
        b2_start = b2[0]
        gap = b2_start - b1_end
        print(f"  Bar {i+1} [x={b1[0]}, w={b1[2]}, right={b1_end}] -> Bar {i+2} [x={b2[0]}, w={b2[2]}]: Gap = {gap:.1f}px")
        if gap <= 0:
            print(f"  [COLLISION] Bar collision between bar {i+1} and {i+2}!")
            collision_detected = True

    # Check Right Panel Bars
    print("\n--- Part B: Bar Rectangle Spacing (Right Panel) ---")
    for i in range(len(right_bars) - 1):
        b1 = right_bars[i]
        b2 = right_bars[i + 1]
        b1_end = b1[0] + b1[2]
        b2_start = b2[0]
        gap = b2_start - b1_end
        print(f"  Bar {i+1} [x={b1[0]}, w={b1[2]}, right={b1_end}] -> Bar {i+2} [x={b2[0]}, w={b2[2]}]: Gap = {gap:.1f}px")
        if gap <= 0:
            print(f"  [COLLISION] Bar collision between bar {i+1} and {i+2}!")
            collision_detected = True

    # Part C: Physical Font Engine Measurement using Tkinter
    print("\n--- Part C: Physical Font Engine Clearance Probe (Tkinter Segoe UI / Arial) ---")
    try:
        tk_root = tkinter.Tk()
        tk_root.withdraw()

        for family in ["Segoe UI", "Arial", "Tahoma"]:
            print(f"\n  Testing Font Family '{family}':")
            # Left Panel
            min_gap_left = 9999.0
            print("    [Left Panel Labels]:")
            for i in range(len(left_labels) - 1):
                l1 = left_labels[i]
                l2 = left_labels[i + 1]
                f1 = tkfont.Font(family=family, size=-round(l1[2]))
                f2 = tkfont.Font(family=family, size=-round(l2[2]))
                w1 = f1.measure(l1[3])
                w2 = f2.measure(l2[3])
                l1_right = l1[0] + w1 / 2.0
                l2_left = l2[0] - w2 / 2.0
                gap = l2_left - l1_right
                min_gap_left = min(min_gap_left, gap)
                status = "CLEAR" if gap > 0 else "OVERLAP"
                print(f"      '{l1[3]}' ({w1}px, right={l1_right:.1f}) -> '{l2[3]}' ({w2}px, left={l2_left:.1f}): Gap = {gap:.1f}px [{status}]")
                if gap <= 0:
                    print(f"      [COLLISION] Font collision on {family} between '{l1[3]}' and '{l2[3]}'")
                    collision_detected = True
            print(f"    --> Minimum clearance in Left Panel ({family}): {min_gap_left:.1f}px")

            # Right Panel
            min_gap_right = 9999.0
            print("    [Right Panel Labels]:")
            for i in range(len(right_labels) - 1):
                l1 = right_labels[i]
                l2 = right_labels[i + 1]
                f1 = tkfont.Font(family=family, size=-round(l1[2]))
                f2 = tkfont.Font(family=family, size=-round(l2[2]))
                w1 = f1.measure(l1[3])
                w2 = f2.measure(l2[3])
                l1_right = l1[0] + w1 / 2.0
                l2_left = l2[0] - w2 / 2.0
                gap = l2_left - l1_right
                min_gap_right = min(min_gap_right, gap)
                status = "CLEAR" if gap > 0 else "OVERLAP"
                print(f"      '{l1[3]}' ({w1}px, right={l1_right:.1f}) -> '{l2[3]}' ({w2}px, left={l2_left:.1f}): Gap = {gap:.1f}px [{status}]")
                if gap <= 0:
                    print(f"      [COLLISION] Font collision on {family} between '{l1[3]}' and '{l2[3]}'")
                    collision_detected = True
            print(f"    --> Minimum clearance in Right Panel ({family}): {min_gap_right:.1f}px")

    except Exception as e:
        print(f"  [ERROR] Tkinter font measurement failed: {e}")
        collision_detected = True

    print("\n[CHALLENGE 3 RESULT]: " + ("PASS (Zero Bar or Label Collisions Detected across both panels)" if not collision_detected else "FAIL"))
    return not collision_detected


def run_challenge_4_loc_ceiling() -> bool:
    print("\n" + "=" * 70)
    print("CHALLENGE 4: ARC-01 Physical LOC Ceiling Verification (<= 800 LOC)")
    print("=" * 70)

    target_dir = Path("tools/showcase_benchmark")
    py_files = sorted(target_dir.glob("*.py"))

    all_pass = True
    print(f"Scanning all Python files in {target_dir} for ARC-01 ceiling (<= 800 LOC):")
    for fpath in py_files:
        lines = fpath.read_text(encoding="utf-8").splitlines()
        loc = len(lines)
        headroom = 800 - loc
        status = "PASS" if loc <= 800 else "VIOLATION"
        print(f"  {fpath.name:<32}: LOC = {loc:>4} / 800 (Headroom: {headroom:>3} lines) [{status}]")
        if loc > 800:
            all_pass = False

    print("\n[CHALLENGE 4 RESULT]: " + ("PASS (All showcase benchmark tools strictly <= 800 LOC)" if all_pass else "FAIL"))
    return all_pass


if __name__ == "__main__":
    c1 = run_challenge_1_metric_parity()
    c2 = run_challenge_2_svg_conformance()
    c3 = run_challenge_3_collision_probe()
    c4 = run_challenge_4_loc_ceiling()

    overall = c1 and c2 and c3 and c4
    print("\n" + "#" * 70)
    print(f"OVERALL ADVERSARIAL VERDICT: {'ALL 4 CHALLENGES PASSED (APPROVE)' if overall else 'FAILURES DETECTED (REQUEST_CHANGES)'}")
    print("#" * 70)
    sys.exit(0 if overall else 1)
