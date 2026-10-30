#!/usr/bin/env python3
"""WHY-CHAIN gate: every change must carry a "Chuỗi Tại Sao" (owner mandate, 2026-10-30).

Why this exists: the owner's WHY concept had no process enforcement at all - 20+ commits
merged with only "what was done + tests green". This tool is the *floor* that makes a
missing or empty chain a hard failure at three points:

  1. ``--commit-msg FILE``  git commit-msg hook (pre-commit cannot see the message).
  2. ``--range HEAD [--base BASE]``  CI: every commit in the pushed / PR range.
  3. ``FILE [--against-dir DIR]``  done files (council inbox *.md, outside the repo).

Epistemic rule (owner, binding): the chain is a LANTERN, not a THRONE. A link is only a
HYPOTHESIS until Reality verifies it independently; chain depth is NOT confidence (five
links at 0.9 each is ~59%); causes form a NETWORK, so parallel branches must be examined;
"I do not know" (UNKNOWN_WITH_REASON) is an honorable output. Therefore this gate never
rewards length or depth: there is no maximum, no bonus and no extra links beyond the five
parts. It only checks that claims are LABELED and that "verified" carries a pointer.

Required block (heading on its own line, then five labelled parts)::

    Chuỗi Tại Sao:
    1. GỐC: <nguyên nhân gốc; NGUYÊN NHÂN SONG SONG đã xét và loại trừ bằng gì>
    2. SỬA-ĐÚNG: <VERIFIED: con trỏ bằng chứng cụ thể (lệnh/output/file/test id/SHA)>
    3. TIN-BẰNG-CHỨNG: <VERIFIED: nguồn độc lập nào | HYPOTHESIS: chưa kiểm độc lập>
    4. ĐỔI-QUYẾT-ĐỊNH: <điều gì sẽ làm quyết định này đổi>
    5. UNKNOWN_WITH_REASON: <chỗ nào chưa biết và VÌ SAO chưa biết>

Status tags (UPPER-CASE identifiers, free text after them): ``VERIFIED:`` (must be followed
by a concrete pointer: digit, `backtick` command, path, test id ``::`` or sha),
``HYPOTHESIS:`` (not yet independently checked), ``UNKNOWN_WITH_REASON:`` (reason required,
a short honest reason is accepted - never rejected for being short).

WHAT THIS CHECKER CAN JUDGE (a floor, mechanical): the block exists; all five parts exist;
none is empty / a placeholder / template text; parts 1-3 (causal/evidence claims) carry a status tag; every
VERIFIED tag has a concrete pointer; part 2 has a VERIFIED pointer or an explicit
UNKNOWN_WITH_REASON; part 1 mentions the parallel causes examined; parts are not copies
of each other, of the commit subject, or of recent earlier chains (verbatim reuse).

WHAT IT CANNOT JUDGE: whether the text is TRUE, whether the root cause is the real root
cause, whether the cited evidence exists, or whether the "independent lineage" is really
independent. A well-formed lie passes. The CHECKER agent grades
each link's evidence (DNA #22: PASS != TRUE), never the smoothness of the prose; this tool
only removes the cheapest way of skipping the question.

Scope (strict): EVERY commit that is not an ancestor of ``epoch_sha``
(spec/why_chain_policy.json) must carry a chain. The only exemption is a clean,
git-generated two-parent merge (default "Merge ..." subject AND tree identical to what an
automatic merge of its two parents produces), because the merged commits are themselves
in range and checked. Reverts, fixups, bot commits and hand-written merge messages are NOT
exempt.

Exit codes: 0 = ok, 1 = violation found, 2 = tool/config/usage error (fail-closed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

HEADING_FOLDED = "CHUOI TAI SAO"

# part index -> accepted folded label phrases (prefix match on the label before the first colon)
PART_LABELS: dict[int, tuple[str, ...]] = {
    1: ("GOC", "NGUYEN NHAN GOC", "TAI SAO LOI TON TAI"),
    2: ("SUA DUNG", "CACH SUA DUNG", "TAI SAO CACH SUA DUNG", "TAI SAO SUA DUNG", "BANG CHUNG REALITY"),
    3: ("TIN BANG CHUNG", "TAI SAO TIN BANG CHUNG", "TAI SAO TIN", "TIN"),
    4: ("DOI QUYET DINH", "DIEU GI SE LAM DOI QUYET DINH", "DIEU GI LAM DOI QUYET DINH"),
    5: ("UNKNOWN WITH REASON", "UNKNOWN_WITH_REASON"),
}
PART_NAMES = {
    1: "1 GOC (tai sao loi ton tai)",
    2: "2 SUA-DUNG (bang chung Reality)",
    3: "3 TIN-BANG-CHUNG (lineage doc lap)",
    4: "4 DOI-QUYET-DINH (dieu gi se lam doi quyet dinh)",
    5: "5 UNKNOWN_WITH_REASON",
}

# Strictness floors. tests/T00_integrity/test_why_chain_gate.py pins these so they cannot be
# lowered silently.
MIN_CHARS = 30
MIN_WORDS = 6
MIN_UNKNOWN_REASON_WORDS = 3  # an explicit UNKNOWN_WITH_REASON may be short, but must give a reason
MIN_UNIQUE_WORDS = 5
MIN_UNIQUE_RATIO = 0.5
MAX_INTRA_SIMILARITY = 0.8   # two parts of one chain
MAX_REUSE_SIMILARITY = 0.9   # a part vs an earlier commit's part

PLACEHOLDER_PHRASES = frozenset({
    "n a", "na", "none", "nil", "null", "no", "nothing", "tbd", "tbc", "tba", "todo", "etc",
    "khong co", "chua co", "chua biet", "khong ap dung", "khong", "chua",
    "xem tren", "nhu tren", "giong tren", "same as above", "see above", "as above",
    "not applicable", "unknown", "to be determined", "lorem ipsum", "placeholder",
    "test", "abc", "xxx", "ok", "done", "tot", "da xong",
})
FILLER_TOKENS = frozenset({"tbd", "tbc", "tba", "todo", "none", "nil", "null", "xxx", "lorem",
                           "ipsum", "placeholder", "etc", "abc"})

_TRAILER_RE = re.compile(
    r"^(co-authored-by|signed-off-by|reviewed-by|acked-by|tested-by|reported-by|fixes|refs|cc)\s*:",
    re.IGNORECASE,
)
_ANGLE_RE = re.compile(r"<[^<>\n]{0,200}>")
_ANCHOR_RE = re.compile(r"[0-9`/\\]|::")
_TAG_RE = re.compile(r"\b(VERIFIED|HYPOTHESIS|UNKNOWN_WITH_REASON)\b")
_PARALLEL_KEYWORDS = ("SONG SONG", "LOAI TRU")  # folded; part 1 must show competing causes were considered
GIT_MERGE_SUBJECT_RE = re.compile(
    r"^Merge (?:branch(?:es)? |tag |commit |remote-tracking branch(?:es)? |pull request #\d+ "
    r"|changes from |'|\")",
)
ZERO_SHA_RE = re.compile(r"^0{40}$")


def fold(text: str) -> str:
    """Upper-case, diacritic-free (đ -> D) form used for every comparison."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _tokens(text: str) -> list[str]:
    """Lower-cased folded word tokens; angle-bracket template spans and n/a removed."""
    text = _ANGLE_RE.sub(" ", text)
    text = re.sub(r"\bn\s*/\s*a\b", " ", fold(text), flags=re.IGNORECASE)
    return [t.lower() for t in re.findall(r"[A-Z0-9_]+", text.upper())]


def _norm(text: str) -> str:
    return " ".join(_tokens(text))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ChainParse:
    heading_found: bool = False
    parts: dict[int, str] = field(default_factory=dict)


def _strip_decor(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^>+\s*", "", s)
    return s


def _is_heading_line(line: str, mode: str) -> bool:
    s = _strip_decor(line)
    if mode == "message" and s.startswith("#"):
        return False  # git strips '#' lines from edited messages: never count them
    s = s.lstrip("#").strip()
    s = s.strip("*_` \t").rstrip(":").strip("*_` \t")
    return fold(s) == HEADING_FOLDED


def _parse_part_line(line: str) -> tuple[int, str] | None:
    s = _strip_decor(line)
    s = re.sub(r"^[-*+]\s+", "", s)
    s = re.sub(r"^\d+\s*[.)]\s*", "", s)
    s = s.replace("**", "").replace("__", "")
    m = re.match(r"^([^:：]{1,90})[:：]\s*(.*)$", s)
    if not m:
        return None
    label = re.sub(r"[^A-Z0-9_]+", " ", fold(m.group(1))).strip()
    for idx, phrases in PART_LABELS.items():
        for ph in phrases:
            if label == ph or label.startswith(ph + " "):
                return idx, m.group(2).strip()
    return None


def parse_chain(text: str, mode: str = "file") -> list[ChainParse]:
    """Return one ChainParse per 'Chuỗi Tại Sao' heading found (mode: 'file' | 'message')."""
    lines = text.lstrip("﻿").splitlines()
    chains: list[ChainParse] = []
    i = 0
    while i < len(lines):
        if mode == "message" and lines[i].lstrip().startswith("#"):
            i += 1
            continue
        if not _is_heading_line(lines[i], mode):
            i += 1
            continue
        chain = ChainParse(heading_found=True)
        i += 1
        current: int | None = None
        buf: list[str] = []

        def flush() -> None:
            nonlocal current, buf
            if current is not None and current not in chain.parts:
                chain.parts[current] = " ".join(b for b in buf if b).strip()
            current, buf = None, []

        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if mode == "message" and stripped.startswith("#"):
                i += 1
                continue
            if mode == "file" and stripped.startswith("#"):
                break  # next markdown heading ends the section
            if _is_heading_line(raw, mode):
                break
            if _TRAILER_RE.match(stripped):
                flush()
                i += 1
                continue
            part = _parse_part_line(raw)
            if part is not None:
                flush()
                current, first = part
                buf = [first]
            elif not stripped:
                flush()  # a blank line ends the current part (one paragraph per part)
            elif current is not None:
                buf.append(stripped)
            i += 1
        flush()
        chains.append(chain)
    return chains


def _tag_segments(clean: str) -> list[tuple[str, str]]:
    """[(tag, text up to the next tag)] for the status tags in ``clean``."""
    matches = list(_TAG_RE.finditer(clean))
    segs = []
    for n, m in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(clean)
        segs.append((m.group(1), clean[m.end():end]))
    return segs


def _real_words(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in FILLER_TOKENS]


def _unknown_reason_ok(reason: str) -> bool:
    toks = _tokens(reason)
    return len(_real_words(reason)) >= MIN_UNKNOWN_REASON_WORDS and " ".join(toks) not in PLACEHOLDER_PHRASES


def _part_errors(idx: int, text: str) -> tuple[list[str], bool]:
    """(errors, declares_valid_unknown). Never rewards length: only floors and labels."""
    name = PART_NAMES[idx]
    clean = _ANGLE_RE.sub(" ", text)
    toks = _tokens(text)
    if not toks:
        return [f"{name}: rong (hoac chi la template/placeholder trong ngoac nhon)"], False
    norm = " ".join(toks)
    if norm in PLACEHOLDER_PHRASES or all(t in FILLER_TOKENS for t in toks):
        return [f"{name}: placeholder khong duoc chap nhan ('{text[:40]}')"], False
    errors: list[str] = []
    segs = _tag_segments(clean)
    good_unknown = False
    good_verified = False
    for tag, seg in segs:
        if tag == "VERIFIED":
            if _ANCHOR_RE.search(seg):
                good_verified = True
            else:
                errors.append(f"{name}: VERIFIED phai kem con tro bang chung cu the (lenh `backtick`, duong dan, test id ::, sha, so)")
        elif tag == "UNKNOWN_WITH_REASON":
            if _unknown_reason_ok(seg):
                good_unknown = True
            else:
                errors.append(f"{name}: UNKNOWN_WITH_REASON phai co ly do (>= {MIN_UNKNOWN_REASON_WORDS} tu thuc, khong phai placeholder)")
    if idx in (1, 2, 3) and not segs:  # causal / evidence claims; part 4 is a forward condition
        errors.append(f"{name}: chua gan nhan VERIFIED / HYPOTHESIS / UNKNOWN_WITH_REASON cho cac mat xich")
    if idx == 2 and not (good_verified or good_unknown):
        errors.append(f"{name}: can it nhat 1 VERIFIED co con tro bang chung, hoac khai UNKNOWN_WITH_REASON co ly do (HYPOTHESIS khong phai bang chung)")
    if idx == 1 and not good_unknown and not any(k in fold(clean) for k in _PARALLEL_KEYWORDS):
        errors.append(f"{name}: nguyen nhan la MANG - neu ro nguyen nhan SONG SONG da xet va LOAI TRU bang gi (hoac UNKNOWN_WITH_REASON)")
    if not good_unknown:
        real = _real_words(text)
        if len(re.sub(r"\s+", " ", clean).strip()) < MIN_CHARS:
            errors.append(f"{name}: qua ngan (< {MIN_CHARS} ky tu) - neu khong biet, khai UNKNOWN_WITH_REASON kem ly do")
        if len(real) < MIN_WORDS:
            errors.append(f"{name}: qua it noi dung (< {MIN_WORDS} tu thuc) - neu khong biet, khai UNKNOWN_WITH_REASON kem ly do")
        uniq = set(real)
        if len(uniq) < MIN_UNIQUE_WORDS or (real and len(uniq) / len(real) < MIN_UNIQUE_RATIO):
            errors.append(f"{name}: lap tu / khong du da dang (>= {MIN_UNIQUE_WORDS} tu khac nhau, ty le >= {MIN_UNIQUE_RATIO})")
    return errors, good_unknown


def validate_chain(
    text: str,
    *,
    mode: str = "file",
    subject: str | None = None,
    priors: list[tuple[str, list[set[str]]]] | None = None,
) -> tuple[list[str], dict[int, str]]:
    """Validate ``text``. Returns (errors, parts_of_best_chain). Empty errors = OK."""
    chains = parse_chain(text, mode)
    if not chains:
        hint = " (khong dung dong bat dau bang '#' trong commit message: git se xoa no)" if mode == "message" else ""
        return [f"thieu muc 'Chuoi Tai Sao' (dong tieu de rieng){hint}"], {}
    best_errors: list[str] | None = None
    best_parts: dict[int, str] = {}
    for chain in chains:
        errors = _validate_one(chain, subject=subject, priors=priors or [])
        if not errors:
            return [], chain.parts
        if best_errors is None:
            best_errors, best_parts = errors, chain.parts
    return best_errors or ["chuoi khong hop le"], best_parts


def _validate_one(chain: ChainParse, *, subject: str | None, priors) -> list[str]:
    errors: list[str] = []
    unknown_parts: set[int] = set()
    for idx in range(1, 6):
        if idx not in chain.parts:
            errors.append(f"thieu phan {PART_NAMES[idx]}")
            continue
        part_errors, is_unknown = _part_errors(idx, chain.parts[idx])
        errors.extend(part_errors)
        if is_unknown:
            unknown_parts.add(idx)
    # Copy/reuse checks compare substantive prose only: a short honest UNKNOWN reason may
    # legitimately resemble another (it is never penalised for being short or repeated).
    present = [i for i in range(1, 6) if i in chain.parts and i not in unknown_parts]
    sets = {i: set(_tokens(chain.parts[i])) for i in present}
    for a in present:
        for b in present:
            if a < b and sets[a] and _jaccard(sets[a], sets[b]) >= MAX_INTRA_SIMILARITY:
                errors.append(f"phan {a} va phan {b} gan nhu giong het nhau (copy)")
    if subject:
        subj = set(_tokens(subject))
        for i in present:
            if subj and _jaccard(sets[i], subj) >= MAX_INTRA_SIMILARITY:
                errors.append(f"phan {i} chi lap lai tieu de commit")
    for source, prior_sets in priors:
        for i in present:
            for ps in prior_sets:
                if sets[i] and _jaccard(sets[i], ps) >= MAX_REUSE_SIMILARITY:
                    errors.append(f"phan {i} dung lai nguyen van WHY cua {source} (phai viet lai cho thay doi nay)")
                    break
    return errors


def part_token_sets(parts: dict[int, str]) -> list[set[str]]:
    return [s for s in (set(_tokens(t)) for t in parts.values()) if s]


# ----------------------------------------------------------------------------- git helpers

_GIT_CTX_VARS = (
    "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class ToolError(Exception):
    """Configuration / git failure (exit code 2, fail-closed)."""


def _git(repo: Path, args: list[str], *, scrub: bool = True, ok_codes: tuple[int, ...] = (0,)) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not (scrub and k in _GIT_CTX_VARS)}
    env["LC_ALL"] = "C"
    try:
        res = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env)
    except OSError as exc:
        raise ToolError(f"cannot run git: {exc}") from exc
    out = res.stdout.decode("utf-8", errors="replace")
    if res.returncode not in ok_codes:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"git {' '.join(args)} failed (rc={res.returncode}): {err}")
    return res.returncode, out


def load_policy(policy_path: Path) -> dict:
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"policy {policy_path} unreadable: {exc}") from exc
    epoch = data.get("epoch_sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", str(epoch)):
        raise ToolError(f"policy {policy_path}: epoch_sha must be a full 40-hex sha")
    window = data.get("recent_window", 20)
    if not isinstance(window, int) or window < 1:
        raise ToolError(f"policy {policy_path}: recent_window must be a positive int")
    return data


def default_policy_path() -> Path:
    return Path(__file__).resolve().parents[1] / "spec" / "why_chain_policy.json"


def _commit_exists(repo: Path, sha: str) -> bool:
    rc, _ = _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"], ok_codes=(0, 1, 128))
    return rc == 0


def read_commit(repo: Path, sha: str) -> tuple[list[str], str, str]:
    """(parents, tree, message) read from the raw commit object (locale independent)."""
    res = subprocess.run(
        ["git", "cat-file", "commit", sha], cwd=repo, capture_output=True,
        env={k: v for k, v in os.environ.items() if k not in _GIT_CTX_VARS},
    )
    if res.returncode != 0:
        raise ToolError(f"git cat-file commit {sha} failed: {res.stderr.decode('utf-8', 'replace').strip()}")
    head, _, body = res.stdout.partition(b"\n\n")
    parents, tree = [], ""
    for line in head.decode("utf-8", errors="replace").splitlines():
        if line.startswith("parent "):
            parents.append(line.split()[1])
        elif line.startswith("tree "):
            tree = line.split()[1]
    return parents, tree, body.decode("utf-8", errors="replace")


def subject_of(message: str) -> str:
    for line in message.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            return line.strip()
    return ""


def is_clean_default_merge(repo: Path, subject: str, parents: list[str], tree: str, *, scrub: bool = True) -> bool:
    """The ONLY exemption: git-default merge subject + two parents + tree == automatic merge.

    A hand-written merge message, an octopus merge, a conflict resolution or any manual
    edit (evil merge) makes the tree differ from ``git merge-tree`` and loses the exemption.
    """
    if len(parents) != 2 or not GIT_MERGE_SUBJECT_RE.match(subject) or not tree:
        return False
    rc, out = _git(repo, ["merge-tree", "--write-tree", "--no-messages", parents[0], parents[1]],
                   scrub=scrub, ok_codes=(0, 1, 128, 129))
    if rc != 0:
        return False
    first = out.splitlines()[0].strip() if out.strip() else ""
    return bool(first) and first == tree


def collect_priors(repo: Path, tip: str, window: int) -> list[tuple[str, list[set[str]]]]:
    try:
        _, out = _git(repo, ["rev-list", "-n", str(window), "--first-parent", tip], ok_codes=(0, 128))
    except ToolError:
        return []
    priors: list[tuple[str, list[set[str]]]] = []
    for sha in out.split():
        _, _, msg = read_commit(repo, sha)
        chains = parse_chain(msg, "message")
        for ch in chains:
            sets = part_token_sets(ch.parts)
            if sets:
                priors.append((f"commit {sha[:8]}", sets))
    return priors


# ----------------------------------------------------------------------------- modes

def check_message_file(path: Path, repo: Path, policy_path: Path) -> int:
    policy = load_policy(policy_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read commit message file {path}: {exc}") from exc
    subject = subject_of(text)
    merge_head = repo_git_path(repo, "MERGE_HEAD")
    if merge_head is not None and merge_head.exists() and GIT_MERGE_SUBJECT_RE.match(subject):
        _, head = _git(repo, ["rev-parse", "HEAD"])
        mh = merge_head.read_text(encoding="utf-8", errors="replace").split()[0]
        _, tree = _git(repo, ["write-tree"], scrub=False)
        if is_clean_default_merge(repo, subject, [head.strip(), mh], tree.strip(), scrub=False):
            print("why_chain: EXEMPT clean git-default merge (merged commits are checked in CI range)")
            return 0
    priors = collect_priors(repo, "HEAD", policy["recent_window"])
    errors, _ = validate_chain(text, mode="message", subject=subject, priors=priors)
    if errors:
        _print_failure("commit message", errors)
        return 1
    print("why_chain: commit message OK (floor only - substance is judged by the CHECKER)")
    return 0


def repo_git_path(repo: Path, name: str) -> Path | None:
    try:
        _, out = _git(repo, ["rev-parse", "--git-path", name])
    except ToolError:
        return None
    p = Path(out.strip())
    return p if p.is_absolute() else repo / p


def check_range(head: str, base: str | None, repo: Path, policy_path: Path) -> int:
    policy = load_policy(policy_path)
    epoch = policy["epoch_sha"]
    if not _commit_exists(repo, epoch):
        raise ToolError(f"epoch_sha {epoch} not found in this repository (shallow clone? use fetch-depth: 0)")
    if not _commit_exists(repo, head):
        raise ToolError(f"head {head} not found in this repository")
    if base and (ZERO_SHA_RE.match(base) or not _commit_exists(repo, base)):
        print(f"why_chain: base {base[:12]} unusable (new branch / force push) - checking everything after the epoch")
        base = None
    not_refs = [epoch] + ([base] if base else [])
    _, out = _git(repo, ["rev-list", "--topo-order", "--reverse", head, "--not", *not_refs])
    shas = out.split()
    priors = collect_priors(repo, base or epoch, policy["recent_window"])
    failed = exempt = 0
    for sha in shas:
        parents, tree, msg = read_commit(repo, sha)
        subject = subject_of(msg)
        if is_clean_default_merge(repo, subject, parents, tree):
            exempt += 1
            print(f"why_chain: {sha[:8]} EXEMPT clean git-default merge")
            continue
        errors, parts = validate_chain(msg, mode="message", subject=subject, priors=priors)
        if errors:
            failed += 1
            _print_failure(f"commit {sha[:8]} ({subject[:70]})", errors)
        else:
            sets = part_token_sets(parts)
            if sets:
                priors.append((f"commit {sha[:8]}", sets))
    print(f"why_chain_commits_checked={len(shas)} failed={failed} exempt={exempt} epoch={epoch[:8]}")
    return 1 if failed else 0


def check_done_file(path: Path, against_dir: Path | None) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc
    priors: list[tuple[str, list[set[str]]]] = []
    if against_dir is not None:
        if not against_dir.is_dir():
            raise ToolError(f"--against-dir {against_dir} is not a directory")
        for other in sorted(against_dir.glob("*.md")):
            if other.resolve() == path.resolve():
                continue
            try:
                other_text = other.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for ch in parse_chain(other_text, "file"):
                sets = part_token_sets(ch.parts)
                if sets:
                    priors.append((f"file {other.name}", sets))
    errors, _ = validate_chain(text, mode="file", priors=priors)
    if errors:
        _print_failure(f"done file {path.name}", errors)
        return 1
    print(f"why_chain: {path.name} OK (floor only - substance is judged by the CHECKER)")
    return 0


def _print_failure(what: str, errors: list[str]) -> None:
    print(f"why_chain: REJECT {what}")
    for e in errors:
        print(f"  - {e}")
    print("  Mau: xem docs/governance/WHY_CHAIN.md (tieu de 'Chuỗi Tại Sao:' + 5 phan GỐC / SỬA-ĐÚNG / "
          "TIN-BẰNG-CHỨNG / ĐỔI-QUYẾT-ĐỊNH / UNKNOWN_WITH_REASON; gan nhan VERIFIED:/HYPOTHESIS:/UNKNOWN_WITH_REASON:)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WHY-CHAIN gate ('Chuỗi Tại Sao')")
    ap.add_argument("file", nargs="?", help="done file (markdown) to check")
    ap.add_argument("--against-dir", help="reject parts reused verbatim from other *.md in this dir")
    ap.add_argument("--commit-msg", metavar="FILE", help="git commit-msg hook mode")
    ap.add_argument("--range", dest="head", metavar="HEAD", help="CI mode: check every commit reachable from HEAD after the epoch")
    ap.add_argument("--base", help="CI mode: exclude commits reachable from BASE")
    ap.add_argument("--policy", help="policy json (default: spec/why_chain_policy.json next to this tool)")
    ap.add_argument("--repo", help="git repository to inspect (default: cwd)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    modes = [bool(args.file), bool(args.commit_msg), bool(args.head)]
    if sum(modes) != 1:
        print("why_chain: give exactly one of FILE | --commit-msg FILE | --range HEAD", file=sys.stderr)
        return 2
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    policy_path = Path(args.policy) if args.policy else default_policy_path()
    try:
        if args.commit_msg:
            return check_message_file(Path(args.commit_msg), repo, policy_path)
        if args.head:
            return check_range(args.head, args.base, repo, policy_path)
        return check_done_file(Path(args.file), Path(args.against_dir) if args.against_dir else None)
    except ToolError as exc:
        print(f"why_chain: TOOL ERROR (fail-closed): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
