"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
AstronomyDataSource - Data source cho Thiên văn học
Bao gồm: Hệ Mặt Trời (8 hành tinh + Mặt Trời + Mặt Trăng + các vệ tinh),
         các ngôi sao, thiên hà, constellations, astronomical constants.
"""
import logging
from typing import Any

from scp.core.wikipedia_client import fetch_summary as _wiki_fetch_summary  # [G3-CONSOLIDATE RE-05]
from scp.data_sources._matching import _token_boundary_match
from scp.interfaces.data_source import IDataSource, shared_health_ping

logger = logging.getLogger(__name__)
# [V104.32 #5] word-boundary matching for short keys


class AstronomyDataSource(IDataSource):
    """
    Data source cho các câu hỏi Thiên văn.
    Hỗ trợ: hành tinh, sao, thiên hà, vệ tinh, hằng số thiên văn.
    """

    def __init__(self):
        self._cache: dict[str, Any] = {}

        # ---------------- Astronomical constants ----------------
        self._constants: dict[str, float] = {}
        self._planets: dict[str, dict[str, Any]] = {}
        self._stars: dict[str, dict[str, Any]] = {}
        self._galaxies: dict[str, dict[str, Any]] = {}
        self._moons: dict[str, dict[str, Any]] = {}
        self._constellations: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "AstronomyDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 2592000  # 30 days - astronomical data very stable

    def get_supported_intents(self) -> list[str]:
        return [
            'planet_info',
            'star_info',
            'galaxy_info',
            'moon_info',
            'constellation_info',
            'astronomical_constant',
            'distance_info',
            'orbit_info',
        ]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            if entity_lower in self._constants:
                return True
            if entity_lower in self._planets:
                return True
            if entity_lower in self._stars:
                return True
            if entity_lower in self._galaxies:
                return True
            if entity_lower in self._moons:
                return True
            if entity_lower in self._constellations:
                return True
            # Partial match
            for key in list(self._planets.keys()) + list(self._stars.keys()) + list(self._galaxies.keys()):
                if _token_boundary_match(key, entity_lower):
                    return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        """Lấy dữ liệu thiên văn."""
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Prioritize planets lookup when intent == "planet_info" or exact match in planets
        if intent == "planet_info" or entity_lower in self._planets:
            if entity_lower in self._planets:
                data = self._planets[entity_lower]
                return {
                    'value': data['mass_kg'],
                    'source': 'NASA Planetary Fact Sheet (Local)',
                    'metadata': {
                        'name_en': data.get('name_en', entity_lower),
                        'vi_name': data['vi_name'],
                        'type': data['type'],
                        'mass_kg': data['mass_kg'],
                        'radius_m': data['radius_m'],
                        'semi_major_axis_m': data['semi_major_axis_m'],
                        'orbital_period_s': data['orbital_period_s'],
                        'mean_temp_k': data['mean_temp_k'],
                        'moons': data['moons'],
                        'position': data['position'],
                        'unit': 'kg',
                    }
                }
            if intent == "planet_info":
                for key, data in self._planets.items():
                    if _token_boundary_match(key, entity_lower):
                        return {
                            'value': data['mass_kg'],
                            'source': 'NASA Planetary Fact Sheet (Local)',
                            'metadata': {
                                'name_en': data.get('name_en', key),
                                'vi_name': data['vi_name'],
                                'type': data['type'],
                                'mass_kg': data['mass_kg'],
                                'radius_m': data['radius_m'],
                                'semi_major_axis_m': data['semi_major_axis_m'],
                                'orbital_period_s': data['orbital_period_s'],
                                'mean_temp_k': data['mean_temp_k'],
                                'moons': data['moons'],
                                'position': data['position'],
                                'unit': 'kg',
                            }
                        }

        # Try constants
        if entity_lower in self._constants:
            value = self._constants[entity_lower]
            return {
                'value': value,
                'source': 'NASA / IAU (Local)',
                'metadata': {
                    'constant_name': entity_lower,
                    'method': 'lookup',
                }
            }
        for key, value in self._constants.items():
            if _token_boundary_match(key, entity_lower):
                return {
                    'value': value,
                    'source': 'NASA / IAU (Local)',
                    'metadata': {'constant_name': key, 'method': 'lookup'}
                }

        # Try planets (fallback if not already checked)
        if entity_lower in self._planets:
            data = self._planets[entity_lower]
            return {
                'value': data['mass_kg'],
                'source': 'NASA Planetary Fact Sheet (Local)',
                'metadata': {
                    'name_en': data.get('name_en', entity_lower),
                    'vi_name': data['vi_name'],
                    'type': data['type'],
                    'mass_kg': data['mass_kg'],
                    'radius_m': data['radius_m'],
                    'semi_major_axis_m': data['semi_major_axis_m'],
                    'orbital_period_s': data['orbital_period_s'],
                    'mean_temp_k': data['mean_temp_k'],
                    'moons': data['moons'],
                    'position': data['position'],
                    'unit': 'kg',
                }
            }
        # Partial planet match
        for key, data in self._planets.items():
            if _token_boundary_match(key, entity_lower):
                return {
                    'value': data['mass_kg'],
                    'source': 'NASA Planetary Fact Sheet (Local)',
                    'metadata': {
                        'name_en': data.get('name_en', key),
                        'vi_name': data['vi_name'],
                        'type': data['type'],
                        'mass_kg': data['mass_kg'],
                        'radius_m': data['radius_m'],
                        'semi_major_axis_m': data['semi_major_axis_m'],
                        'orbital_period_s': data['orbital_period_s'],
                        'mean_temp_k': data['mean_temp_k'],
                        'moons': data['moons'],
                        'position': data['position'],
                        'unit': 'kg',
                    }
                }

        # Try stars
        if entity_lower in self._stars:
            data = self._stars[entity_lower]
            return {
                'value': data['apparent_mag'],
                'source': 'Yale Bright Star Catalog (Local)',
                'metadata': data
            }
        # Try galaxies
        if entity_lower in self._galaxies:
            data = self._galaxies[entity_lower]
            return {
                'value': data['stars'],
                'source': 'NASA Extragalactic Database (Local)',
                'metadata': data
            }
        # Try moons
        if entity_lower in self._moons:
            data = self._moons[entity_lower]
            return {
                'value': data['mass_kg'],
                'source': 'NASA Planetary Fact Sheet (Local)',
                'metadata': data
            }
        # Try constellations
        if entity_lower in self._constellations:
            data = self._constellations[entity_lower]
            return {
                'value': data['stars_visible'],
                'source': 'IAU Constellation Catalog (Local)',
                'metadata': data
            }

        # Fallback: Wikipedia
        return self._fetch_from_wikipedia(entity)

    def _fetch_from_wikipedia(self, entity: str) -> dict[str, Any] | None:
        """Fallback: Lấy từ Wikipedia.

        [G3-CONSOLIDATE RE-05] Now delegates to scp.core.wikipedia_client
        (fetch_summary). Same return shape, same error→None contract.
        Previously this method made a raw requests.get() with timeout=5s
        and constructed the REST URL manually; now it goes through the
        canonical client which provides timeout=10s, 1 req/sec rate limit,
        LRU cache (256 entries), and proper User-Agent header.
        """
        # [G3-CONSOLIDATE RE-05] Now delegates to scp.core.wikipedia_client
        try:
            result = _wiki_fetch_summary(entity, lang="en")
            if result and result.get("extract"):
                return {
                    'value': result['extract'],
                    'source': 'Wikipedia',
                    'metadata': {
                        'title': result.get('title', ''),
                        'method': 'wikipedia'
                    }
                }
        except Exception as e:
            logger.warning(f"[Astronomy] Wikipedia fetch failed: {e}", exc_info=True)
        return None

    def health_check(self) -> bool:
        """[AUDIT-FIX low-4] Fail-closed: ping MediaWiki API của en.wikipedia.org
        (siteinfo — endpoint mà wikipedia_client fetch() thực sự dùng, không cần
        key). Trước đây hardcode `return True` — fail-open, không có
        bằng chứng. Bất kỳ HTTP response nào chứng minh service sống; exception
        (egress denied, DNS, timeout) → False.

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
        """[V104.32] Ping thật (1 network round-trip) — chỉ gọi qua
        shared_health_ping để được dedupe theo TTL."""
        api_ok = False
        try:
            from scp.security.url_safety import safe_urlopen  # [AUDIT-FIX low-4]
            # [SSRF-S1] safe_urlopen cho health ping (URL cố định).
            with safe_urlopen(
                "https://en.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json",
                timeout=3,
            ):
                api_ok = True
        except Exception as e:
            logger.warning(f"[Astronomy] health ping failed: {e}", exc_info=True)
        if not api_ok:
            logger.warning(
                "[Astronomy] health_check: Wikipedia endpoint unreachable — báo "
                "unhealthy (fail-closed)"
            )
        return api_ok
