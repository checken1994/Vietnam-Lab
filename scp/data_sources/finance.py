"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
FinanceDataSource - Data source cho Tài chính
Bao gồm: tỷ giá hối đoái (Frankfurter), crypto (CoinGecko), giá vàng.
"""
import json
import logging
import warnings
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from scp.interfaces.data_source import IDataSource, shared_health_ping
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]

logger = logging.getLogger(__name__)

# [AUDIT-20260909 SSRF-S1] Hosts cố định — literal duy nhất của builders.
_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
_FRANKFURTER_LATEST_URL = "https://api.frankfurter.dev/v1/latest"

# [AUDIT-20260909 SSRF-S1] coin_id / currency code dạng ràng buộc.
_CURRENCY_CODE_RE = re.compile(r"^[A-Za-z]{3,10}$")
_COIN_ID_RE = re.compile(r"^[a-z0-9\-]{1,32}$")


def build_coingecko_price_url(coin_id: str) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — coin_id PHẢI khớp
    ^[a-z0-9-]{1,32}$ (CoinGecko id format); input xấu → ValueError TRƯỚC
    KHI fetch. Host cố định api.coingecko.com."""
    coin = str(coin_id or "")
    if not _COIN_ID_RE.fullmatch(coin):
        raise ValueError(f"invalid_coin_id:{coin[:32]!r}")
    query = urllib.parse.urlencode({
        "ids": coin,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    })
    return f"{_COINGECKO_PRICE_URL}?{query}"


def build_frankfurter_rate_url(from_curr: str, to_curr: str) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — from/to PHẢI là mã tiền
    3-10 chữ cái; input xấu → ValueError TRƯỚC KHI fetch. Host cố định
    api.frankfurter.dev."""
    src = str(from_curr or "")
    dst = str(to_curr or "")
    if not _CURRENCY_CODE_RE.fullmatch(src) or not _CURRENCY_CODE_RE.fullmatch(dst):
        raise ValueError(f"invalid_currency_code:{src[:16]!r},{dst[:16]!r}")
    query = urllib.parse.urlencode({"from": src, "to": dst})
    return f"{_FRANKFURTER_LATEST_URL}?{query}"


class FinanceDataSource(IDataSource):
    """Data source cho các câu hỏi tài chính."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}
        self._cache_timestamp: dict[str, float] = {}

        # Major currencies
        self._currencies = {}

        # Major cryptocurrencies (emptied for Tabula Rasa)
        self._cryptos = {}

        # Reference gold/silver prices (approximate)
        self._precious_metals_static = {}

    @property
    def name(self) -> str:
        return "FinanceDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 300  # 5 minutes - prices change frequently

    def get_supported_intents(self) -> list[str]:
        return [
            'exchange_rate',
            'crypto_price',
            'precious_metal',
            'currency_info',
            'stock_price',
        ]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            if entity_lower in self._currencies:
                return True
            if entity_lower in self._cryptos:
                return True
            # Crypto symbol detection
            for c in self._cryptos.values():
                if entity_lower == c['symbol'].lower():
                    return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Currency info (static)
        if entity_lower in self._currencies:
            data = self._currencies[entity_lower]
            return {
                'value': data['symbol'],
                'source': 'Local Finance Database',
                'metadata': data
            }

        # Crypto info
        if entity_lower in self._cryptos:
            crypto = self._cryptos[entity_lower]
            # Try CoinGecko API
            result = self._fetch_crypto_price(crypto['id'])
            if result:
                return result
            return {
                'value': None,
                'source': 'Local Finance Database',
                'metadata': {**crypto, 'note': 'Live price unavailable'}
            }

        # Crypto by symbol
        for c in self._cryptos.values():
            if entity_lower == c['symbol'].lower():
                result = self._fetch_crypto_price(c['id'])
                if result:
                    return result

        # Currency pair (e.g., "usd vnd", "usd to vnd")
        import re
        pair_match = re.match(r'(\w{3})\s*(?:to|->|/|\s)\s*(\w{3})', entity_lower)
        if pair_match:
            from_curr, to_curr = pair_match.group(1), pair_match.group(2)
            if from_curr in self._currencies and to_curr in self._currencies:
                result = self._fetch_exchange_rate(from_curr, to_curr)
                if result:
                    return result

        return None

    def _fetch_crypto_price(self, coin_id: str) -> dict[str, Any] | None:
        """Lấy giá crypto từ CoinGecko."""
        cache_key = f"crypto:{coin_id}"
        if cache_key in self._cache and time.time() - self._cache_timestamp.get(cache_key, 0) < self.ttl:
            return self._cache[cache_key]
        try:
            # [AUDIT-20260909 SSRF-S1] build URL (validate + encode coin_id)
            # tách khỏi fetch; fetch qua safe_urlopen thay raw requests.get.
            url = build_coingecko_price_url(coin_id)
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Finance/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=8) as response:
                if getattr(response, "status", 200) != 200:
                    return None
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            if coin_id in data:
                price_usd = data[coin_id].get('usd', 0)
                change_24h = data[coin_id].get('usd_24h_change', 0)
                result = {
                    'value': price_usd,
                    'source': 'CoinGecko API',
                    'metadata': {
                        'coin_id': coin_id,
                        'price_usd': price_usd,
                        'change_24h_pct': change_24h,
                        'currency': 'USD',
                        'method': 'coingecko',
                    }
                }
                self._cache[cache_key] = result
                self._cache_timestamp[cache_key] = time.time()
                return result
        except Exception as e:
            logger.warning(f"[Finance] CoinGecko fetch failed: {e}", exc_info=True)
        return None

    def _fetch_exchange_rate(self, from_curr: str, to_curr: str) -> dict[str, Any] | None:
        """Lấy tỷ giá từ Frankfurter (free, no key)."""
        cache_key = f"fx:{from_curr}:{to_curr}"
        if cache_key in self._cache and time.time() - self._cache_timestamp.get(cache_key, 0) < self.ttl:
            return self._cache[cache_key]
        try:
            # [AUDIT-20260909 SSRF-S1] build URL (validate mã tiền) rồi fetch
            # qua safe_urlopen thay raw requests.get.
            url = build_frankfurter_rate_url(from_curr, to_curr)
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Finance/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=8) as response:
                if getattr(response, "status", 200) != 200:
                    return None
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            rate = data.get('rates', {}).get(to_curr.upper())
            if rate:
                result = {
                    'value': rate,
                    'source': 'Frankfurter API',
                    'metadata': {
                        'from': from_curr.upper(),
                        'to': to_curr.upper(),
                        'rate': rate,
                        'date': data.get('date'),
                        'method': 'frankfurter',
                    }
                }
                self._cache[cache_key] = result
                self._cache_timestamp[cache_key] = time.time()
                return result
        except Exception as e:
            logger.warning(f"[Finance] Frankfurter fetch failed: {e}", exc_info=True)
        return None

    def health_check(self) -> bool:
        """[V104.31 #4] Real ping — CoinGecko + Frankfurter.

        [V104.32] Kết quả ping đi qua shared negative-result cache
        cross-instance (`shared_health_ping`): trong TTL
        (SCP_HEALTH_NEG_CACHE_TTL, default 60s) các lần gọi sau trả lại quan
        sát gần nhất — kể cả False (đó là reality gần nhất, không fake health)
        — mà KHÔNG re-ping."""
        import time
        healthy = shared_health_ping(type(self).__name__, self._live_ping)
        self._cache['_health_cache'] = healthy
        self._cache['_health_cache_ts'] = time.time()
        return healthy

    def _live_ping(self) -> bool:
        """[V104.32] Ping thật (1-2 network round-trips) — chỉ gọi qua
        shared_health_ping để được dedupe theo TTL."""
        api_ok = False
        try:
            # [AUDIT-20260909 SSRF-S1] safe_urlopen cho health pings (URL cố định).
            with safe_urlopen("https://api.coingecko.com/api/v3/ping", timeout=3) as r:
                if getattr(r, "status", 200) == 200:
                    api_ok = True
        except Exception as e:
            logger.warning(f"Silent except: {e}", exc_info=True)
        if not api_ok:
            try:
                with safe_urlopen("https://api.frankfurter.dev/v1/latest?from=USD&to=EUR", timeout=3) as r:
                    if getattr(r, "status", 200) == 200:
                        api_ok = True
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
        # [AUDIT-FIX low-4] Fail-closed: health = tín hiệu live ping THẬT, KHÔNG
        # OR với dict local (từng là `api_ok or bool(self._currencies)` → luôn
        # True kể cả khi egress denied). Dữ liệu cache vẫn trả lời được query
        # nhưng là trạng thái DEGRADED — báo qua log, không báo healthy.
        healthy = api_ok
        if not api_ok and bool(self._currencies):
            logger.warning(
                "[Finance] health_check: live API ping failed; local currency "
                "dataset vẫn có (degraded) — báo unhealthy theo contract fail-closed"
            )
        return healthy
