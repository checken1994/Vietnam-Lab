"""Download GSM8K test split and a 10-question smoke sample (one-off utility).

[audit-r2 2026-10-01 fixes]
- Path derivation: this script lives INSIDE benchmark/ — the old
  parent/"benchmark"/... pointed at a nonexistent double-subdir
  benchmark/benchmark/ and every write failed with FileNotFoundError.
- Network: raw urllib.request.urlretrieve (B310) replaced with
  scp.security.url_safety.safe_urlopen (egress gate + scheme/private-IP
  validation), streamed to disk via shutil.copyfileobj.
- Point-of-use traversal guard (Mimosa HIGH 2026-10-01): the download
  destination filename is a hardcoded bare literal — never derived from the
  URL — and is re-validated inside _safe_download immediately before any
  file is opened (bare name only, no '..'/':', .jsonl whitelist, benchmark
  tree containment).
"""
import os
import shutil
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scp.security.url_safety import safe_urlopen  # noqa: E402

GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"


def _contained_in_repo(p: str) -> bool:
    """[SEC-S4] Containment guard: every file this script touches must resolve
    inside the repository benchmark tree (paths are __file__-derived; this
    blocks traversal if the derivation is ever made configurable)."""
    return Path(p).resolve().is_relative_to(Path(__file__).resolve().parent.parent)


_ALLOWED_DOWNLOAD_SUFFIXES = (".jsonl",)


def _safe_download(url: str, filename: str) -> str:
    """[SEC-S4] Download `url` into benchmark/<filename> under traversal guards.

    [Mimosa HIGH fix 2026-10-01] The destination filename is a hardcoded bare
    literal at the call site — never derived from the URL — and is re-validated
    at the point of use, immediately before any file is opened:
      - bare name only: no directory separators, not absolute, no '..' / ':';
      - extension whitelisted to .jsonl;
      - the resolved destination must stay inside the repository benchmark tree.
    Returns the resolved output path.
    """
    normalized = filename.replace("\\", "/")
    name = os.path.basename(normalized)
    if (
        not name
        or name != normalized
        or os.path.isabs(filename)
        or ".." in name
        or ":" in name
        or not name.endswith(_ALLOWED_DOWNLOAD_SUFFIXES)
    ):
        raise SystemExit("SEC-S4: download filename must be a bare whitelisted .jsonl name")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not _contained_in_repo(out_path):
        raise SystemExit("SEC-S4: download target escapes repository benchmark tree")
    # [SEC-S4/B310] safe_urlopen enforces the egress policy and validates the
    # scheme + destination IP before any byte is fetched.
    with safe_urlopen(url, timeout=60) as resp, open(out_path, "wb") as f_out:
        shutil.copyfileobj(resp, f_out)
    return out_path


def download_gsm8k_sample():
    print("Downloading GSM8K (Middle School Math) - Global TOP 1% Standard...")

    sample_path = str(Path(__file__).resolve().parent / "gsm8k_sample_10.jsonl")
    # [SEC-S4] Containment check before writing anything
    if not _contained_in_repo(sample_path):
        raise SystemExit("SEC-S4: derived path escapes repository benchmark tree")
    try:
        # Filename is a hardcoded bare literal (never URL-derived); the
        # point-of-use guards in _safe_download re-validate it before open().
        out_path = _safe_download(GSM8K_URL, "gsm8k_test_top1.jsonl")
        print(f"Success! Saved to: {out_path}")

        # Extract 10 questions for a quick smoke test
        with open(out_path, "r", encoding="utf-8") as f:
            lines = [next(f) for _ in range(10)]

        with Path(sample_path).open("w", encoding="utf-8") as f_out:
            f_out.writelines(lines)
        print(f"Created 10-question sample file: {sample_path}")

    except Exception as e:
        print(f"Error downloading GSM8K: {e}")


if __name__ == "__main__":
    download_gsm8k_sample()
