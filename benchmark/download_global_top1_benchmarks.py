"""Download GSM8K test split and a 10-question smoke sample (one-off utility).

[audit-r2 2026-10-01 fixes]
- Path derivation: this script lives INSIDE benchmark/ — the old
  parent/"benchmark"/... pointed at a nonexistent double-subdir
  benchmark/benchmark/ and every write failed with FileNotFoundError.
- Network: raw urllib.request.urlretrieve (B310) replaced with
  scp.security.url_safety.safe_urlopen (egress gate + scheme/private-IP
  validation), streamed to disk via shutil.copyfileobj.
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


def _safe_download(url: str, out_path: str) -> None:
    # [SEC-S4/B310] safe_urlopen enforces the egress policy and validates the
    # scheme + destination IP before any byte is fetched.
    with safe_urlopen(url, timeout=60) as resp, open(out_path, "wb") as f_out:
        shutil.copyfileobj(resp, f_out)


def download_gsm8k_sample():
    print("Downloading GSM8K (Middle School Math) - Global TOP 1% Standard...")

    out_path = str(Path(__file__).resolve().parent / "gsm8k_test_top1.jsonl")
    sample_path = str(Path(__file__).resolve().parent / "gsm8k_sample_10.jsonl")
    # [SEC-S4] Containment check before writing anything
    if not (_contained_in_repo(out_path) and _contained_in_repo(sample_path)):
        raise SystemExit("SEC-S4: derived path escapes repository benchmark tree")
    try:
        _safe_download(GSM8K_URL, out_path)
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
