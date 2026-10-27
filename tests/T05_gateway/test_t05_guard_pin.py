"""Pin: guard T05 phải bắn với client mạng THẬT (default transport) cả sync+async.

[ARC-01-FOLLOWUP 2026-10-28] Guard này từng chết ở nhánh async vì
httpx.AsyncHTTPTransport không kế thừa httpx.HTTPTransport. Test pin giữ
hành vi đó không hồi quy: cả hai nhánh đều phải raise AssertionError của
conftest (không được chạm mạng thật - host .invalid không bao giờ resolve).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest


def _reclaim_intentional_probe_records():
    """Gỡ record guard do chính test pin này cố tình bắn ra.

    Guard ghi 'sync'/'async' vào unexpected_http rồi MỚI raise; pytest.raises
    phía trên đã xác minh AssertionError đó (exception được quan sát, không
    phải product code nuốt request), nên teardown `assert unexpected_http == []`
    của isolated_gateway_state không cần thấy record của lần bắn có chủ ý này.
    List nằm trong closure của chính forbidden_sync/forbidden_async đang được
    monkeypatch lên httpx.Client.send / httpx.AsyncClient.send. Nếu guard bị
    đổi cấu trúc và helper không tìm thấy list nào, record còn lại sẽ làm
    teardown tự đỏ (fail-closed) - không có đường nào làm pin xanh giả.
    """
    cleared = False
    for send_fn in (httpx.Client.send, httpx.AsyncClient.send):
        for cell in getattr(send_fn, "__closure__", None) or ():
            contents = cell.cell_contents
            if isinstance(contents, list):
                contents.clear()
                cleared = True
    assert cleared, "T05 guard closure shape changed; pin cannot reclaim its own probe records"


def test_t05_guard_blocks_real_network_sync_and_async():
    with pytest.raises(AssertionError, match="T05 must inject a transport"):
        httpx.Client().get("http://testserver.invalid/pin")          # default HTTPTransport

    async def _probe():
        with pytest.raises(AssertionError, match="T05 must inject a transport"):
            await httpx.AsyncClient().get("http://testserver.invalid/pin")  # default AsyncHTTPTransport

    asyncio.run(_probe())
    _reclaim_intentional_probe_records()
