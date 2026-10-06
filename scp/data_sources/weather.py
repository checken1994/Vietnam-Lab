"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
WeatherDataSource - Data source cho Thời tiết & Khí hậu
Bao gồm: local climate data + API fallbacks (Open-Meteo, OpenWeatherMap).
"""
import json
import logging
import math
import re
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

from scp.interfaces.data_source import IDataSource, reachability_ping, shared_health_ping
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]

logger = logging.getLogger(__name__)

# [AUDIT-20260909 SSRF-S1] Host cố định cho Open-Meteo fetch.
_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# [W13] Host forecast duy nhất được owner duyệt cho weather lookup (egress
# approval W13) — KHÔNG wildcard, KHÔNG subdomain. Nguồn allowlist chính vẫn
# là SCP_EGRESS_ALLOWLIST (config env); hằng số này là scoped grant trong code
# áp đúng cho weather fetch (extra_allowed_hosts — EgressPolicy vẫn chặn
# metadata/loopback và DENY mode vẫn thắng).
OPEN_METEO_EGRESS_HOST = "api.open-meteo.com"

# [W14] Host geocoding được owner duyệt (egress approval W14) — dùng CHỈ cho
# geocode fallback khi city không nằm trong bảng local (`_cities`); fetch
# geocode giữ scoped grant RIÊNG của host này (không mượn grant forecast).
OPEN_METEO_GEOCODE_EGRESS_HOST = "geocoding-api.open-meteo.com"
_OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# [W14] Inject-guard: candidate tên thành phố tối đa 80 ký tự (hợp đồng
# sanitize — dài hơn → fail-closed, không truncate để tránh mangled query).
_CITY_NAME_MAX_LEN = 80

# Ký tự cho phép trong tên thành phố: unicode letters (\w sau khi đã loại
# digit/underscore), khoảng trắng, apostrophe (thẳng + cong), chấm, gạch
# ngang. Mọi ký tự URL/HTML/điều khiển khác (& = / % ; ? # " < > \ ... và
# CRLF) bị loại TRƯỚC KHI vào URL; urlencode percent-encode là lớp kế tiếp.
_CITY_NAME_ALLOWED_RE = re.compile(r"[^\w\s.'\-’]", re.UNICODE)

# [W14] Stopwords của candidate extraction (danh sách ĐÓNG, so khớp sau khi
# strip diacritics + lowercase): từ weather/question filler vi|en. Token còn
# lại được coi là ứng viên tên thành phố. Không có trong danh sách ĐÓNG này
# nghĩa là token có thể là một phần tên thành phố (conservative giữ lại).
_CITY_CANDIDATE_STOPWORDS = frozenset({
    # vi — weather domain + time + filler
    "thoi", "tiet", "nhiet", "do", "du", "bao", "doam", "gio", "hom", "nay",
    "mai", "qua", "hien", "tai", "bay", "nao", "nhu", "ra",
    "sao", "nhiu", "o", "cua", "va", "cho", "voi",
    "xin", "vui", "long", "co", "phai", "khong", "la", "gi", "may", "ngay",
    "dem", "sang", "chieu", "toi", "tinh", "huyen", "khuvuc", "vung",
    # en — weather domain + time + filler
    "weather", "temperature", "forecast", "humidity", "wind", "rain",
    "today", "tomorrow", "yesterday", "now", "currently", "latest", "like",
    "what", "how", "is", "are", "the", "a", "an", "in", "at", "on", "of",
    "for", "please", "tell", "me", "right", "and", "or", "it", "its",
})
# [W14] Token candidate tối thiểu 2 ký tự (sau strip diacritics) — chặn
# token rác 1 ký tự vào geocode (conservative: ít fetch, không thêm).
_CITY_CANDIDATE_MIN_TOKEN_LEN = 2

# [W13] Open-Meteo echo lại latitude/longitude trong payload. Lệch > 1°
# (~111km) so với toạ độ yêu cầu → payload không thuộc location đã hỏi
# (mismatch/CDN lạ) → fail-closed None. Làm tròn echo của API nằm trong ngưỡng.
_LOCATION_ECHO_TOLERANCE_DEG = 1.0

# [W13] WMO weather interpretation codes (chuẩn WMO 4677 — bảng diễn giải tĩnh,
# KHÔNG phải dữ liệu thời tiết; code không có trong bảng → bỏ mô tả, không bịa).
_WMO_WEATHER_TEXT: dict[int, tuple[str, str]] = {
    0: ("trời quang", "clear sky"),
    1: ("chủ yếu quang", "mainly clear"),
    2: ("có mây từng phần", "partly cloudy"),
    3: ("nhiều mây", "overcast"),
    45: ("sương mù", "fog"),
    48: ("sương mù đá đóng băng", "depositing rime fog"),
    51: ("mưa phùn nhẹ", "light drizzle"),
    53: ("mưa phùn", "moderate drizzle"),
    55: ("mưa phùn dày", "dense drizzle"),
    56: ("mưa phùn đá lạnh nhẹ", "light freezing drizzle"),
    57: ("mưa phùn đá lạnh dày", "dense freezing drizzle"),
    61: ("mưa nhẹ", "slight rain"),
    63: ("mưa", "moderate rain"),
    65: ("mưa to", "heavy rain"),
    66: ("mưa đá lạnh nhẹ", "light freezing rain"),
    67: ("mưa đá lạnh", "heavy freezing rain"),
    71: ("tuyết nhẹ", "slight snowfall"),
    73: ("tuyết", "moderate snowfall"),
    75: ("tuyết dày", "heavy snowfall"),
    77: ("hạt tuyết", "snow grains"),
    80: ("mưa rào nhẹ", "slight rain showers"),
    81: ("mưa rào", "moderate rain showers"),
    82: ("mưa rào dữ dội", "violent rain showers"),
    85: ("mưa tuyết rào nhẹ", "slight snow showers"),
    86: ("mưa tuyết rào nặng", "heavy snow showers"),
    95: ("dông", "thunderstorm"),
    96: ("dông kèm mưa đá nhẹ", "thunderstorm with slight hail"),
    99: ("dông kèm mưa đá nặng", "thunderstorm with heavy hail"),
}


def _wmo_text(code: Any, lang: str) -> str:
    """Diễn giải WMO code thành text (vi|en). Code lạ/rỗng → '' (không bịa)."""
    try:
        entry = _WMO_WEATHER_TEXT.get(int(code))
    except (TypeError, ValueError):
        return ""
    if not entry:
        return ""
    return entry[0] if lang == "vi" else entry[1]


def _strip_vi_diacritics(text: str) -> str:
    """Fold Vietnamese diacritics về ASCII ('hà nội' → 'ha noi', 'đ' → 'd').

    Giữ nguyên độ dài cho ký tự NFC (mỗi precomposed char → 1 ASCII char) —
    match theo word-boundary không phụ thuộc index gốc.
    """
    lowered = (text or "").lower().translate(str.maketrans("đĐ", "dD"))
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def build_open_meteo_url(lat: float, lon: float) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — lat/lon PHẢI là số hữu hạn
    (float) và được urlencode vào query. Input xấu → ValueError TRƯỚC KHI
    fetch. Host cố định api.open-meteo.com.

    [W13] Bổ sung daily forecast ngắn (2 ngày: max/min/code) + timezone=auto —
    cùng host api.open-meteo.com, không thêm host mới nào."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        raise ValueError(f"invalid_coordinates:{lat!r},{lon!r}")
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        raise ValueError(f"non_finite_coordinates:{lat!r},{lon!r}")
    query = urllib.parse.urlencode({
        "latitude": lat_f,
        "longitude": lon_f,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "forecast_days": 2,
        "timezone": "auto",
    })
    return f"{_OPEN_METEO_FORECAST_URL}?{query}"


def _sanitize_city_name(raw: str) -> str:
    """[W14] Inject-guard cho city name trước khi vào URL geocode.

    Chỉ chấp nhận ký tự tên thành phố (unicode letters, space, apostrophe,
    chấm, gạch ngang) — mọi ký tự URL/HTML/điều khiển (& = / % ; ? # " < > \\
    và CRLF/tab) bị LOẠI; whitespace collapse về 1 space (chặn header/CRLF
    injection); chữ số + underscore loại (tên thành phố thuần chữ —
    conservative); rỗng sau sanitize HOẶC dài quá _CITY_NAME_MAX_LEN (80)
    → '' (fail-closed, không truncate tránh mangled query).

    Lớp sau: `build_geocode_url` percent-encode giá trị vào query value; host
    + path cố định — URL không thể chứa giá trị chưa sanitize."""
    text = str(raw or "")
    text = re.sub(r"[0-9_]", "", text)
    text = _CITY_NAME_ALLOWED_RE.sub("", text)
    text = " ".join(text.split())
    if not text or len(text) > _CITY_NAME_MAX_LEN:
        return ""
    return text


def _extract_city_candidate(question: str) -> str:
    """[W14] Trích ứng viên tên thành phố từ câu hỏi weather (khi bảng local
    miss): loại ký tự không-letter thành space, tokenize, bỏ stopwords vi|en
    (danh sách ĐÓNG) và token < 2 ký tự, nối phần còn lại.

    Trả candidate THÔ (chưa sanitize) — `_geocode_city` luôn sanitize lại
    trước khi fetch. Không còn token nào → '' (fail-closed, không geocode)."""
    q = str(question or "")
    if not q.strip():
        return ""
    cleaned = re.sub(r"[^\w'’.\-]+", " ", q, flags=re.UNICODE)
    kept: list[str] = []
    for token in cleaned.split():
        norm = _strip_vi_diacritics(token.lower())
        if len(norm) < _CITY_CANDIDATE_MIN_TOKEN_LEN:
            continue
        if norm in _CITY_CANDIDATE_STOPWORDS:
            continue
        kept.append(token)
    return " ".join(kept)


def build_geocode_url(name: str) -> str:
    """[W14] Pure URL builder cho geocode search — host + path CỐ ĐỊNH
    (geocoding-api.open-meteo.com/v1/search), name nằm trong query value sau
    urlencode. Caller PHẢI đã qua `_sanitize_city_name` (hàm này encode, không
    lọc ký tự — lớp sanitize là rào trước)."""
    query = urllib.parse.urlencode({"name": str(name or ""), "count": 1, "language": "vi"})
    return f"{_OPEN_METEO_GEOCODE_URL}?{query}"


def geocode_host_allowed(url: str) -> bool:
    """[W14] Egress dry-check scoped cho geocode fallback (host GEOCODE).

    Đối xứng với dry-check forecast của question_router._weather_host_allowed:
    CHỈ host được owner duyệt W14 (OPEN_METEO_GEOCODE_EGRESS_HOST =
    geocoding-api.open-meteo.com) — so khớp CHÍNH XÁC hostname, không
    wildcard/suffix/userinfo spoof. Hai dry-check tách biệt: gate forecast
    KHÔNG mở host geocode và ngược lại (least-privilege — mỗi fetch chỉ
    authorize host của nó; fetch thật vẫn qua safe_urlopen với scoped grant
    riêng, EgressPolicy chặn metadata/loopback, DENY mode vẫn thắng)."""
    from scp.security.url_safety import enforce_egress_policy

    host = (urllib.parse.urlsplit(url).hostname or "").strip().strip("[]").lower().rstrip(".")
    if host != OPEN_METEO_GEOCODE_EGRESS_HOST:
        return False
    try:
        enforce_egress_policy(url, extra_allowed_hosts=frozenset({OPEN_METEO_GEOCODE_EGRESS_HOST}))
        return True
    except Exception as e:
        logger.debug("[W14] egress blocked geocode host %s: %s", host, e, exc_info=True)
        return False


class WeatherDataSource(IDataSource):
    """Data source cho các câu hỏi thời tiết."""

    def __init__(self):
        self._cache: dict[str, Any] = {}

        # Major Vietnamese cities - lat/lon for API calls
        self._cities = {
            'hà nội': {'lat': 21.0285, 'lon': 105.8542, 'country': 'VN'},
            'hanoi': {'lat': 21.0285, 'lon': 105.8542, 'country': 'VN'},
            'hồ chí minh': {'lat': 10.8231, 'lon': 106.6297, 'country': 'VN'},
            'ho chi minh': {'lat': 10.8231, 'lon': 106.6297, 'country': 'VN'},
            'sài gòn': {'lat': 10.8231, 'lon': 106.6297, 'country': 'VN'},
            'saigon': {'lat': 10.8231, 'lon': 106.6297, 'country': 'VN'},
            'đà nẵng': {'lat': 16.0544, 'lon': 108.2022, 'country': 'VN'},
            'da nang': {'lat': 16.0544, 'lon': 108.2022, 'country': 'VN'},
            'hải phòng': {'lat': 20.8449, 'lon': 106.6881, 'country': 'VN'},
            'cần thơ': {'lat': 10.0452, 'lon': 105.7469, 'country': 'VN'},
            'huế': {'lat': 16.4637, 'lon': 107.5909, 'country': 'VN'},
            'nha trang': {'lat': 12.2388, 'lon': 109.1967, 'country': 'VN'},
            'đà lạt': {'lat': 11.9404, 'lon': 108.4583, 'country': 'VN'},
            'vũng tàu': {'lat': 10.9827, 'lon': 107.0831, 'country': 'VN'},
            'quy nhơn': {'lat': 13.7820, 'lon': 109.2193, 'country': 'VN'},
            # International
            'tokyo': {'lat': 35.6762, 'lon': 139.6503, 'country': 'JP'},
            'new york': {'lat': 40.7128, 'lon': -74.0060, 'country': 'US'},
            'london': {'lat': 51.5074, 'lon': -0.1278, 'country': 'UK'},
            'paris': {'lat': 48.8566, 'lon': 2.3522, 'country': 'FR'},
            'singapore': {'lat': 1.3521, 'lon': 103.8198, 'country': 'SG'},
            'beijing': {'lat': 39.9042, 'lon': 116.4074, 'country': 'CN'},
            'sydney': {'lat': -33.8688, 'lon': 151.2093, 'country': 'AU'},
            'moscow': {'lat': 55.7558, 'lon': 37.6173, 'country': 'RU'},
        }

        # Climate normals (long-term averages)
        self._climate_normals = {
            'hà nội': {'annual_temp_c': 23.6, 'annual_rain_mm': 1676, 'humidity_avg': 79},
            'hồ chí minh': {'annual_temp_c': 27.0, 'annual_rain_mm': 1949, 'humidity_avg': 79},
            'đà nẵng': {'annual_temp_c': 25.5, 'annual_rain_mm': 2505, 'humidity_avg': 83},
            'tokyo': {'annual_temp_c': 15.4, 'annual_rain_mm': 1529, 'humidity_avg': 65},
            'london': {'annual_temp_c': 11.3, 'annual_rain_mm': 601, 'humidity_avg': 75},
            'new york': {'annual_temp_c': 12.7, 'annual_rain_mm': 1199, 'humidity_avg': 64},
        }

        # Beaufort scale
        self._beaufort = {
            0: {'name': 'Calm', 'vi_name': 'Lặng gió', 'wind_kmh': '< 1'},
            1: {'name': 'Light air', 'vi_name': 'Gió nhẹ', 'wind_kmh': '1-5'},
            2: {'name': 'Light breeze', 'vi_name': 'Hơi thổi', 'wind_kmh': '6-11'},
            3: {'name': 'Gentle breeze', 'vi_name': 'Gió dịu', 'wind_kmh': '12-19'},
            4: {'name': 'Moderate breeze', 'vi_name': 'Gió vừa', 'wind_kmh': '20-28'},
            5: {'name': 'Fresh breeze', 'vi_name': 'Gói tươi', 'wind_kmh': '29-38'},
            6: {'name': 'Strong breeze', 'vi_name': 'Gió mạnh', 'wind_kmh': '39-49'},
            7: {'name': 'High wind', 'vi_name': 'Gió cao', 'wind_kmh': '50-61'},
            8: {'name': 'Gale', 'vi_name': 'Gió bão', 'wind_kmh': '62-74'},
            9: {'name': 'Strong gale', 'vi_name': 'Bão mạnh', 'wind_kmh': '75-88'},
            10: {'name': 'Storm', 'vi_name': 'Bão', 'wind_kmh': '89-102'},
            11: {'name': 'Violent storm', 'vi_name': 'Bão dữ', 'wind_kmh': '103-117'},
            12: {'name': 'Hurricane', 'vi_name': 'Cuồng phong', 'wind_kmh': '> 117'},
        }

    @property
    def name(self) -> str:
        return "WeatherDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 600  # 10 minutes - weather changes frequently

    def get_supported_intents(self) -> list[str]:
        return [
            'current_weather',
            'forecast',
            'climate_normal',
            'beaufort_scale',
            'temperature',
            'humidity',
            'wind_speed',
        ]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            if entity_lower in self._cities:
                return True
            if entity_lower in self._climate_normals:
                return True
            for key in list(self._cities.keys()) + list(self._climate_normals.keys()):
                if key in entity_lower or entity_lower in key:
                    return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Beaufort
        if entity_lower.startswith('beaufort') and entity_lower.split()[-1].isdigit():
            b = int(entity_lower.split()[-1])
            if b in self._beaufort:
                data = self._beaufort[b]
                return {
                    'value': data['wind_kmh'],
                    'source': 'Beaufort Scale (Local)',
                    'metadata': data
                }

        # Climate normals
        if entity_lower in self._climate_normals:
            data = self._climate_normals[entity_lower]
            return {
                'value': data.get('annual_temp_c'),
                'source': 'Climate Normals (Local)',
                'metadata': data
            }

        # Live weather - try Open-Meteo (no API key required)
        city = self._cities.get(entity_lower)
        if not city:
            # Partial match
            for key, c in self._cities.items():
                if key in entity_lower or entity_lower in key:
                    city = c
                    break

        if city:
            result = self._fetch_from_open_meteo(city['lat'], city['lon'])
            if result:
                return result

        return None

    # ------------------------------------------------------------------
    # [W13] Adapter cho question_router weather tier (wire S24 lookup fork).
    # ------------------------------------------------------------------
    def _match_city(self, question: str) -> tuple[str | None, str]:
        """Tìm thành phố trong bảng `_cities` theo câu hỏi tự nhiên.

        Match không phân biệt dấu ('Ha Noi' khớp 'hà nội', 'đ' = 'd') với
        word-boundary (chặn 'paris' trong 'comparison'). Trả (city_key,
        display) — display là DẠNG người dùng viết trong câu hỏi nếu tìm thấy
        trực tiếp (giữ nguyên dạng để relevance gate so terms câu hỏi), ngược
        lại là key gốc của bảng.
        """
        q_lower = (question or "").lower()
        if not q_lower:
            return None, ""
        q_norm = _strip_vi_diacritics(q_lower)
        for key in self._cities:
            key_norm = _strip_vi_diacritics(key)
            pattern = rf"(?<![a-z0-9]){re.escape(key_norm)}(?![a-z0-9])"
            if not re.search(pattern, q_norm):
                continue
            display_match = re.search(pattern, q_lower)
            display = q_lower[display_match.start():display_match.end()] if display_match else key
            return key, display
        return None, ""

    def _geocode_city(self, name: str) -> tuple[float, float, str] | None:
        """[W14] Geocode fallback qua Open-Meteo Geocoding API (host được
        owner duyệt W14) — CHỈ chạy khi city không nằm trong bảng local.

        Pipeline: sanitize (inject-guard) → URL build (host/path cố định,
        name vào query value sau urlencode) → fetch qua safe_urlopen với
        scoped grant CHỈ host geocode → parse results[0]. Fail-closed:
        candidate rỗng/quá dài, fetch lỗi, HTTP != 200, payload lệch shape,
        toạ độ không parse được / không hữu hạn, 0 result → None. Trả
        (lat, lon, canonical_name) — lat/lon sau đó fetch forecast như bảng
        local (echo-location check của forecast vẫn giữ nguyên)."""
        sanitized = _sanitize_city_name(name)
        if not sanitized:
            logger.debug("[Weather] geocode candidate empty after sanitize — fail-closed")
            return None
        url = build_geocode_url(sanitized)
        # Dry-check scoped (W14): policy từ chối host geocode → bỏ sớm, không
        # đốt timeout cho một fetch chắc chắn bị chặn (đối xứng _weather_lookup).
        if not geocode_host_allowed(url):
            return None
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Weather/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(
                req,
                timeout=8,
                # [W14] Scoped grant riêng cho host geocode — không mượn grant
                # forecast; EgressPolicy vẫn chặn metadata/loopback, DENY thắng.
                extra_allowed_hosts=frozenset({OPEN_METEO_GEOCODE_EGRESS_HOST}),
            ) as response:
                if getattr(response, "status", 200) != 200:
                    return None
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as e:
            logger.warning(f"[Weather] geocode fetch failed: {e}", exc_info=True)
            return None
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or not results:
            logger.debug("[Weather] geocode 0 result — fail-closed")
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        try:
            lat = float(first.get("latitude"))
            lon = float(first.get("longitude"))
        except (TypeError, ValueError):
            logger.warning("[Weather] geocode payload missing/malformed coordinates — fail-closed")
            return None
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        canonical = str(first.get("name") or "").strip()
        return lat, lon, canonical

    def answer_from_question(self, question: str) -> dict[str, Any] | None:
        """[W13/W14] Parse câu hỏi thời tiết tự nhiên → text trả lời từ Open-Meteo.

        Pipeline: match location trong bảng thành phố local → MISS → geocode
        fallback [W14]: extract candidate từ question → sanitize (inject-guard:
        chỉ ký tự tên thành phố, ≤ 80) → geocoding-api.open-meteo.com (host
        được owner duyệt W14, scoped grant riêng) → lat/lon → fetch current +
        daily forecast ngắn (host api.open-meteo.com, approval W13) → compose
        1 câu duy nhất (vi|en theo câu hỏi). MỌI giá trị số trong text đến từ
        payload API — không bịa, không nội suy. Fail-closed: candidate rỗng /
        geocode 0 result / fetch lỗi / payload thiếu nhiệt độ / echo location
        lệch → None (hành vi cũ cho mọi nhánh không thể chứng minh).
        """
        q = (question or "").strip()
        if not q:
            return None
        city_key, display = self._match_city(q)
        lat: float | None = None
        lon: float | None = None
        if city_key is not None:
            city = self._cities[city_key]
            lat, lon = city['lat'], city['lon']
        else:
            # [W14] Geocode fallback — city ngoài bảng local.
            candidate = _extract_city_candidate(q)
            geo = self._geocode_city(candidate) if candidate else None
            if geo is None:
                logger.debug("[Weather] no known location and geocode miss — fail-closed")
                return None
            lat, lon, _canonical = geo
            display = _sanitize_city_name(candidate) or _canonical or "vị trí đã geocode"
            city_key = f"geocode:{_canonical or display}"
        result = self._fetch_from_open_meteo(lat, lon)
        if not result:
            return None
        meta = result.get('metadata') or {}
        lang = self._detect_question_language(q)
        temperature = meta.get('temperature_c')
        humidity = meta.get('humidity_pct')
        wind = meta.get('wind_speed_kmh')
        condition = _wmo_text(meta.get('weather_code'), lang)
        daily_max = [v for v in (meta.get('daily_max_c') or []) if v is not None]
        daily_min = [v for v in (meta.get('daily_min_c') or []) if v is not None]
        if temperature is None:
            return None
        display_title = display.title() if display else city_key.title()
        if lang == "vi":
            text = f"Thời tiết {display_title} hiện tại: {temperature}°C"
            if humidity is not None:
                text += f", độ ẩm {humidity}%"
            if wind is not None:
                text += f", gió {wind} km/h"
            if condition:
                text += f" ({condition})"
            if daily_max and daily_min:
                text += f"; dự báo hôm nay {daily_min[0]}–{daily_max[0]}°C"
        else:
            text = f"Current weather in {display_title}: {temperature}°C"
            if humidity is not None:
                text += f", humidity {humidity}%"
            if wind is not None:
                text += f", wind {wind} km/h"
            if condition:
                text += f" ({condition})"
            if daily_max and daily_min:
                text += f"; forecast today {daily_min[0]}-{daily_max[0]}°C"
        text = text.strip()
        if not text:
            return None
        return {
            "text": text,
            "location": display_title,
            "city_key": city_key,
            "temperature_c": temperature,
            "api_url": result.get('url') or _OPEN_METEO_FORECAST_URL,
        }

    @staticmethod
    def _detect_question_language(question: str) -> str:
        """'vi' khi câu hỏi có dấu tiếng Việt hoặc cụm weather tiếng Việt
        không dấu; ngược lại 'en' (chỉ chọn NGÔN NGỮ compose, không phải
        routing — route vẫn do question_router quyết)."""
        q = question or ""
        if any("\u00c0" <= ch <= "\u1ef9" for ch in q):
            return "vi"
        q_norm = _strip_vi_diacritics(q)
        if any(phrase in q_norm for phrase in ("thoi tiet", "nhiet do", "du bao")):
            return "vi"
        return "en"

    def _fetch_from_open_meteo(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Lấy thời tiết hiện tại + forecast ngắn từ Open-Meteo.

        [W13] Fail-closed mở rộng:
        - fetch qua safe_urlopen với extra_allowed_hosts CHỈ host được duyệt
          (OPEN_METEO_EGRESS_HOST) — EgressPolicy vẫn chặn metadata/loopback,
          DENY mode vẫn thắng, SSRF validation vẫn chạy.
        - payload phải echo latitude/longitude khớp toạ độ yêu cầu trong ngưỡng
          (_LOCATION_ECHO_TOLERANCE_DEG) — chặn trả dữ liệu của location khác.
        - thiếu temperature hiện tại → None (không compose answer thiếu số liệu).
        """
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            return None
        try:
            # [AUDIT-20260909 SSRF-S1] URL build (encode + validate) tách khỏi
            # fetch; fetch qua safe_urlopen thay raw requests.get.
            url = build_open_meteo_url(lat_f, lon_f)
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Weather/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(
                req,
                timeout=8,
                # [W13] Scoped grant — chỉ host được owner duyệt cho weather.
                extra_allowed_hosts=frozenset({OPEN_METEO_EGRESS_HOST}),
            ) as response:
                if getattr(response, "status", 200) != 200:
                    return None
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as e:
            logger.warning(f"[Weather] Open-Meteo fetch failed: {e}", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        # [W13] Echo-location check: Open-Meteo trả lại latitude/longitude —
        # lệch quá ngưỡng → payload không thuộc location đã hỏi → None.
        try:
            resp_lat = float(data.get("latitude"))
            resp_lon = float(data.get("longitude"))
        except (TypeError, ValueError):
            logger.warning("[Weather] Open-Meteo payload missing lat/lon echo — fail-closed")
            return None
        if (
            abs(resp_lat - lat_f) > _LOCATION_ECHO_TOLERANCE_DEG
            or abs(resp_lon - lon_f) > _LOCATION_ECHO_TOLERANCE_DEG
        ):
            logger.warning(
                "[Weather] Open-Meteo payload location mismatch "
                "(requested %s,%s got %s,%s) — fail-closed",
                lat_f, lon_f, resp_lat, resp_lon,
            )
            return None
        cur = data.get('current', {})
        temperature = cur.get('temperature_2m')
        if temperature is None:
            logger.warning("[Weather] Open-Meteo payload missing current temperature — fail-closed")
            return None
        daily = data.get('daily') or {}
        return {
            'value': temperature,
            'source': 'Open-Meteo API',
            'url': url,
            'observed_at': cur.get('time'),
            'metadata': {
                'temperature_c': temperature,
                'humidity_pct': cur.get('relative_humidity_2m'),
                'wind_speed_kmh': cur.get('wind_speed_10m'),
                'weather_code': cur.get('weather_code'),
                'method': 'open_meteo',
                'daily_time': list(daily.get('time') or [])[:2],
                'daily_max_c': list(daily.get('temperature_2m_max') or [])[:2],
                'daily_min_c': list(daily.get('temperature_2m_min') or [])[:2],
                'daily_code': list(daily.get('weather_code') or [])[:2],
            }
        }

    def health_check(self) -> bool:
        """[V104.32] Fail-closed honest ping — reachability của endpoint mà
        fetch() thực sự dùng (api.open-meteo.com). Trước đây hardcode `return True`
        — hardcode, không có live evidence (fail-open). Bất kỳ HTTP
        response nào (kể cả 4xx do thiếu key/tham số) chứng minh service
        sống; exception (egress denied, DNS, timeout) → False.

        Kết quả đi qua shared negative-result cache cross-instance
        (`shared_health_ping`): trong TTL (SCP_HEALTH_NEG_CACHE_TTL,
        default 60s) các lần gọi sau trả lại quan sát gần nhất — kể cả
        False (đó là reality gần nhất, không fake health) — mà KHÔNG
        re-ping."""
        return shared_health_ping(type(self).__name__, self._live_ping)

    def _live_ping(self) -> bool:
        """[V104.32] Ping thật — 1 network round-trip tới URL cố định,
        không chứa key; HTTP response nào cũng = endpoint sống."""
        return reachability_ping('https://api.open-meteo.com/v1/forecast?latitude=21.03&longitude=105.85&current_weather=true')
