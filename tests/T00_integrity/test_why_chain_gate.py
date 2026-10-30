"""WHY-CHAIN floor: a change without a "Chuỗi Tại Sao" must be rejected (owner mandate 2026-10-30).

Epistemic rule (owner): the chain is a lantern, not a throne. These tests pin that the floor
(a) never rewards length/depth, (b) requires labelled claims (VERIFIED + pointer / HYPOTHESIS /
UNKNOWN_WITH_REASON), (c) always accepts an explicit UNKNOWN_WITH_REASON that gives a reason,
(d) prompts for parallel causes. They also pin that empty boilerplate cannot pass.
The floor cannot judge truth: a well-formed lie passes (see test_floor_cannot_judge_truth).
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "check_why_chain.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_why_chain_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wc = _load()

PARTS = {
    1: "VERIFIED: `grep -rn check_why_claims` cho 0 chỗ gọi nên claim sai lọt qua. "
       "Nguyên nhân song song đã xét: CI bỏ step; loại trừ bằng workflow đọc được tại .github/workflows.",
    2: "VERIFIED: `pytest tests/T00_integrity/x.py::test_a` đỏ 3 failed trên base, xanh 3 passed sau sửa.",
    3: "HYPOTHESIS: chưa có agent độc lập chạy lại; mutant do cùng lineage viết nên chưa đủ độc lập.",
    4: "nếu CHECKER thấy một commit không có chuỗi lọt qua hook thì gỡ commit này ngay.",
    5: "chưa biết hành vi trên runner CI thật vì chưa push nhánh này lên GitHub.",
}
LABELS = {1: "GỐC", 2: "SỬA-ĐÚNG", 3: "TIN-BẰNG-CHỨNG", 4: "ĐỔI-QUYẾT-ĐỊNH", 5: "UNKNOWN_WITH_REASON"}


def chain(parts=None, heading="Chuỗi Tại Sao:", **overrides) -> str:
    merged = dict(PARTS if parts is None else parts)
    for key, val in overrides.items():  # p1=..., p2=... ; None drops the part
        idx = int(key[1:])
        if val is None:
            merged.pop(idx, None)
        else:
            merged[idx] = val
    body = "\n".join(f"{i}. {LABELS[i]}: {merged[i]}" for i in sorted(merged))
    return f"{heading}\n{body}\n"


def msg(subject="fix(x): sửa lỗi bờ cõi", **kw) -> str:
    return f"{subject}\n\nGhi chú thường.\n\n{chain(**kw)}\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"


def errors_of(text, mode="message", **kw):
    return wc.validate_chain(text, mode=mode, **kw)[0]


# --------------------------------------------------------------------------- positive

def test_valid_chain_accepted_in_message_and_file_mode():
    assert errors_of(msg()) == []
    assert errors_of("# Done\n\n## " + chain(heading="Chuỗi Tại Sao"), mode="file") == []


def test_labels_without_diacritics_and_nfd_are_accepted():
    ascii_text = chain(heading="Chuoi Tai Sao:").replace("GỐC", "GOC").replace("SỬA-ĐÚNG", "SUA DUNG") \
        .replace("TIN-BẰNG-CHỨNG", "TIN BANG CHUNG").replace("ĐỔI-QUYẾT-ĐỊNH", "DOI QUYET DINH")
    assert errors_of(ascii_text) == []
    import unicodedata
    assert errors_of(unicodedata.normalize("NFD", msg())) == []


@pytest.mark.parametrize("idx", [1, 2, 3, 4, 5])
def test_explicit_unknown_with_reason_is_an_honorable_answer_for_any_part(idx):
    short_honest = "UNKNOWN_WITH_REASON: chưa có provider độc lập"
    assert errors_of(msg(**{f"p{idx}": short_honest})) == []


def test_all_parts_unknown_with_reason_is_not_rejected_by_the_floor():
    # The floor must never punish honesty; whether to SHIP such a change is the CHECKER's call.
    parts = {i: "UNKNOWN_WITH_REASON: " + t for i, t in {
        1: "chưa xác định gốc vì thiếu trace", 2: "chưa có test chạy lại vì docker tắt",
        3: "chưa có lineage khác vì chỉ một agent", 4: "chưa biết vì thiếu dữ liệu runtime",
        5: "toàn bộ phần còn lại chưa kiểm vì hết thời gian"}.items()}
    assert errors_of(chain(parts=parts)) == []


# --------------------------------------------------------------------------- negative: structure

def test_message_without_chain_is_rejected():
    errs = errors_of("fix(x): chỉ nói đã làm gì\n\nTest xanh.\n")
    assert errs and "Chuoi Tai Sao" in errs[0]


def test_heading_mentioned_in_prose_does_not_count():
    assert errors_of("fix: ghi\n\nTôi chưa viết Chuỗi Tại Sao cho việc này.\n")


@pytest.mark.parametrize("idx", [1, 2, 3, 4, 5])
def test_each_missing_part_is_rejected(idx):
    errs = errors_of(msg(**{f"p{idx}": None}))
    assert any(f"thieu phan {idx} " in e for e in errs), errs


def test_hash_prefixed_heading_is_ignored_in_message_mode_but_ok_in_file_mode():
    text = chain(heading="# Chuỗi Tại Sao")
    assert errors_of("subject\n\n" + text, mode="message")  # git strips '#' lines
    assert errors_of("subject\n\n" + text, mode="file") == []


# --------------------------------------------------------------------------- negative: boilerplate

@pytest.mark.parametrize("junk", ["N/A", "n/a", "none", "TBD", "todo", "...", "…", "-", "xem trên",
                                   "không có", "chưa có", "same as above", "<viết ở đây>", "ok", "test"])
def test_placeholders_are_rejected(junk):
    errs = errors_of(msg(p4=junk))
    assert errs, junk
    # the reason must be the right one: a placeholder / empty part, not an incidental length failure
    assert any(("placeholder" in e) or ("rong" in e) for e in errs), (junk, errs)


def test_copying_the_docs_template_unfilled_is_rejected():
    doc = (ROOT / "docs" / "governance" / "WHY_CHAIN.md").read_text(encoding="utf-8")
    block = re.search(r"```text\n(Chuỗi Tại Sao:.*?)```", doc, re.S).group(1)
    assert errors_of("subject\n\n" + block), "the unfilled template must not pass"


def test_padding_with_repeated_words_is_rejected():
    assert errors_of(msg(p4="ok " * 60))
    assert errors_of(msg(p4="rồi rồi rồi rồi rồi rồi rồi rồi rồi rồi"))


def test_floor_thresholds_cannot_be_silently_lowered():
    assert wc.MIN_CHARS >= 30 and wc.MIN_WORDS >= 6 and wc.MIN_UNIQUE_WORDS >= 5
    assert wc.MIN_UNIQUE_RATIO >= 0.5 and wc.MIN_UNKNOWN_REASON_WORDS >= 3
    assert wc.MAX_INTRA_SIMILARITY <= 0.8 and wc.MAX_REUSE_SIMILARITY <= 0.9
    assert {"n a", "tbd", "none", "khong co"} <= set(wc.PLACEHOLDER_PHRASES)


def test_five_long_words_do_not_satisfy_the_minimum_word_floor():
    five_long_words = "chuyenhoanghiemtrong khongthedungnoi laplaitucnhieulan dasaiyeutoquantrong nhungchihaicauchu"
    errs = errors_of(msg(p4=five_long_words))
    assert any("qua it noi dung" in e for e in errs), errs


def test_part_copies_are_rejected():
    errs = errors_of(msg(p4=PARTS[3]))
    assert any("giong het nhau" in e for e in errs)


def test_part_that_only_repeats_the_subject_is_rejected():
    subject = "fix(verifier): thay thế canonical không phải xác minh khi claim bị bác"
    errs = errors_of(msg(subject=subject, p4=subject + " nên gỡ"), subject=subject)
    assert any("tieu de" in e for e in errs)


# --------------------------------------------------------------------------- negative: epistemic labels

def test_unlabelled_causal_claims_are_rejected():
    for idx in (1, 2, 3):
        plain = "nguyên nhân song song đã xét và loại trừ vì hook không gọi `tool` nào cả trong repo"
        errs = errors_of(msg(**{f"p{idx}": plain}))
        assert any("chua gan nhan" in e for e in errs), (idx, errs)


def test_verified_without_pointer_is_rejected():
    errs = errors_of(msg(p2="VERIFIED: tôi đã kiểm tra kỹ và thấy mọi thứ hoạt động rất tốt rồi"))
    assert any("VERIFIED phai kem con tro" in e for e in errs)


def test_hypothesis_alone_is_not_evidence_for_the_fix():
    errs = errors_of(msg(p2="HYPOTHESIS: có lẽ cách sửa này đúng vì nó trông hợp lý với mã nguồn"))
    assert any("can it nhat 1 VERIFIED" in e for e in errs)


def test_part2_may_declare_unknown_with_reason_instead_of_evidence():
    assert errors_of(msg(p2="UNKNOWN_WITH_REASON: chưa chạy được vì docker đang tắt")) == []


def test_unknown_with_reason_needs_a_real_reason():
    for bad in ("UNKNOWN_WITH_REASON:", "UNKNOWN_WITH_REASON: không biết", "UNKNOWN_WITH_REASON: N/A",
                "UNKNOWN_WITH_REASON: tbd"):
        assert errors_of(msg(p5=bad)), bad


def test_part1_must_mention_parallel_causes_or_declare_unknown():
    no_parallel = "VERIFIED: `grep -rn x` 0 chỗ gọi nên claim sai lọt qua, chỉ có một nguyên nhân được nêu"
    errs = errors_of(msg(p1=no_parallel))
    assert any("SONG SONG" in e for e in errs)
    assert errors_of(msg(p1="VERIFIED: `grep -rn x` 0 chỗ gọi. Loại trừ nhánh khác bằng trace `docker logs` 12 dòng.")) == []


def test_template_prompts_for_parallel_causes():
    doc = (ROOT / "docs" / "governance" / "WHY_CHAIN.md").read_text(encoding="utf-8")
    assert "song song" in doc.lower() and "loại trừ" in doc.lower()
    assert "song song" in wc.__doc__.lower()


def test_no_length_or_depth_reward_exists():
    # A minimal honest chain and a long one are judged by the same floor: the long one gains nothing,
    # the short honest one loses nothing. There is no maximum/bonus knob in the module.
    names = {n for n in dir(wc) if n.isupper()}
    assert not any("BONUS" in n or "MAX_LEN" in n or "MAX_WORDS" in n or "DEPTH" in n for n in names), names
    short = chain(parts={
        1: "UNKNOWN_WITH_REASON: chưa có trace gốc", 2: "UNKNOWN_WITH_REASON: chưa chạy được test",
        3: "UNKNOWN_WITH_REASON: chưa có lineage khác", 4: "UNKNOWN_WITH_REASON: chưa có dữ liệu runtime",
        5: "UNKNOWN_WITH_REASON: chưa kiểm phần nào ngoài đọc mã"})
    assert errors_of(short) == []


def test_floor_cannot_judge_truth():
    # Documented limit: a well-formed but false chain passes. The CHECKER agent grades per-link evidence.
    false_but_wellformed = msg(p2="VERIFIED: `pytest nothing_real::test_zz` xanh 99 passed (điều này là bịa)")
    assert errors_of(false_but_wellformed) == []


# --------------------------------------------------------------------------- reuse across commits / files

def test_verbatim_reuse_from_a_previous_commit_is_rejected():
    first = wc.validate_chain(msg(), mode="message")[1]
    priors = [("commit aaaaaaaa", wc.part_token_sets(first))]
    errs = errors_of(msg(), priors=priors)
    assert any("dung lai nguyen van" in e for e in errs)


def test_rewriting_the_why_for_the_new_change_passes():
    first = wc.validate_chain(msg(), mode="message")[1]
    priors = [("commit aaaaaaaa", wc.part_token_sets(first))]
    fresh = msg(
        p1="VERIFIED: `git grep -n RLock scp/runtime` 4 dòng; song song đã xét: timeout provider, loại trừ bằng log `run-77`.",
        p2="VERIFIED: `pytest tests/T02_contract/test_lock.py::test_reentrant` đỏ 1 rồi xanh 1 sau patch.",
        p3="VERIFIED: checker khác lineage tái chạy cùng lệnh trên worktree sạch, ra cùng kết quả 1 passed.",
        p4="nếu race còn tái hiện với 50 lần chạy liên tiếp thì rollback patch khoá này.",
        p5="chưa biết có deadlock hiếm với 3 luồng vì máy host chỉ có 2 lõi để thử.")
    assert errors_of(fresh, priors=priors) == []


# --------------------------------------------------------------------------- CLI (done files)

def _run(*args, cwd=ROOT, env=None):
    import os
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], cwd=cwd, capture_output=True, env=e)


def test_cli_done_file_accepts_valid_and_rejects_missing(tmp_path):
    good = tmp_path / "good-done.md"
    good.write_text("# DONE\n\nlàm xong.\n\n## Chuỗi Tại Sao\n" + chain(heading="").lstrip("\n"), encoding="utf-8")
    bad = tmp_path / "bad-done.md"
    bad.write_text("# DONE\n\nlàm xong, test xanh.\n", encoding="utf-8")
    assert _run(good).returncode == 0
    r = _run(bad)
    assert r.returncode == 1 and b"REJECT" in r.stdout


def test_cli_does_not_crash_on_a_legacy_codepage_stdout(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("không có chuỗi\n", encoding="utf-8")
    r = _run(bad, env={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
    assert r.returncode == 1 and b"Traceback" not in r.stderr


def test_cli_against_dir_rejects_text_copied_from_another_done_file(tmp_path):
    other = tmp_path / "other-done.md"
    other.write_text("# other\n\n" + chain(), encoding="utf-8")
    mine = tmp_path / "mine-done.md"
    mine.write_text("# mine\n\n" + chain(), encoding="utf-8")
    assert _run(mine).returncode == 0
    r = _run(mine, "--against-dir", tmp_path)
    assert r.returncode == 1 and b"other-done.md" in r.stdout


def test_cli_usage_error_is_nonzero():
    assert _run().returncode == 2
    assert _run("a.md", "--range", "HEAD").returncode == 2
