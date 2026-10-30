"""Who is the client? — declared proxy trust for X-Forwarded-For (esc-auto slice 4).

TẠI SAO module này tồn tại:
  Mọi rate limit của SCP (verify_admin 5 lỗi/60s, middleware tier/IP bucket,
  slowapi ``get_remote_address``) khoá theo ``request.client.host``. Giá trị đó
  do uvicorn quyết định, và uvicorn 0.44 mặc định ``proxy_headers=True`` +
  ``forwarded_allow_ips="127.0.0.1"`` (hoặc env ``FORWARDED_ALLOW_IPS``):
  BẤT KỲ peer loopback nào gửi ``X-Forwarded-For`` đều đổi được danh tính
  client. ``python -m scp`` không ghi đè ⇒ tin cậy NGẦM, không ai khai báo:
    * host-run (mọi client là 127.0.0.1): một tiến trình cục bộ xoay XFF mỗi
      request là né hẳn bộ chặn brute-force (mỗi lần một "IP" mới);
    * TLS overlay (Caddy chung netns, peer 127.0.0.1): per-client chỉ đúng
      nhờ may mắn của default uvicorn — đổi default là mọi client dồn về một
      bucket và một người lạ khoá được admin.

  Fix: SCP tự khai báo proxy tin cậy bằng MỘT biến ``SCP_TRUSTED_PROXY_IPS``
  (IP/CIDR, phân tách dấu phẩy). Rỗng (mặc định) ⇒ không tin XFF từ ai cả
  (``proxy_headers=False``) ⇒ client = TCP peer. Có giá trị ⇒ uvicorn chỉ tin
  XFF khi peer thuộc danh sách, và lấy hop KHÔNG tin cậy ngoài cùng bên phải
  (người lạ sau proxy không chèn được hop giả để mượn danh tính người khác).
  ``*`` / ``0.0.0.0/0`` / ``::/0`` / giá trị không phải IP ⇒ BỊ BỎ (fail-closed):
  tin mọi peer = ai cũng giả được IP.

  Không có pattern câu hỏi, không phụ thuộc ngôn ngữ; chỉ là danh tính mạng.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any

logger = logging.getLogger("scp.security.client_identity")

TRUSTED_PROXY_ENV = "SCP_TRUSTED_PROXY_IPS"
_MAX_ENTRIES = 32


def trusted_proxy_entries(raw: str | None = None) -> tuple[str, ...]:
    """Validated, normalised trusted-proxy IPs/CIDRs (never a wildcard)."""
    value = os.environ.get(TRUSTED_PROXY_ENV, "") if raw is None else raw
    accepted: list[str] = []
    for item in (part.strip() for part in str(value).split(",")):
        if not item:
            continue
        try:
            if "/" in item:
                net = ipaddress.ip_network(item, strict=False)
                if net.prefixlen == 0:
                    raise ValueError("network covers every address")
                normalised = str(net)
            else:
                normalised = str(ipaddress.ip_address(item))
        except ValueError as exc:
            logger.error("%s: ignoring untrustworthy entry %r (%s) — fail-closed",
                         TRUSTED_PROXY_ENV, item[:64], exc)
            continue
        if normalised not in accepted:
            accepted.append(normalised)
        if len(accepted) >= _MAX_ENTRIES:
            break
    return tuple(accepted)


def uvicorn_proxy_settings(raw: str | None = None) -> dict[str, Any]:
    """Keyword args for ``uvicorn.run`` / ``uvicorn.Config`` — the ONE trust decision.

    Always explicit, so uvicorn's implicit ``127.0.0.1`` / ``FORWARDED_ALLOW_IPS``
    default can never apply behind SCP's back.
    """
    entries = trusted_proxy_entries(raw)
    if not entries:
        return {"proxy_headers": False, "forwarded_allow_ips": ""}
    return {"proxy_headers": True, "forwarded_allow_ips": ",".join(entries)}


__all__ = ["TRUSTED_PROXY_ENV", "trusted_proxy_entries", "uvicorn_proxy_settings"]
