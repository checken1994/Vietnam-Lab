"""Ad-hoc localhost probe script (kept for manual use only).

[B1 AUDIT-20260909] This file previously executed urllib.request at MODULE
IMPORT to probe http://127.0.0.1:8000. Because pytest imports every
``test_*.py`` it collects, whole-suite collection (tools/t00_meta_audit.py,
scp_guardrails.yml) failed closed with a URLError whenever no local server
was listening — a collection-time network dependency, not a test. No test
function or assertion existed here and none was removed; the probe body is
now guarded so importing is side-effect-free, while running
``python tests/test_api.py`` manually keeps the exact original behavior.
"""
import urllib.request, urllib.error
from urllib.request import urlopen as _url_open

if __name__ == "__main__":
    req = urllib.request.Request('http://127.0.0.1:8000/v3/web/status', method='GET')
    try:
        print(_url_open(req).getcode())
    except urllib.error.HTTPError as e:
        print(e.code)
