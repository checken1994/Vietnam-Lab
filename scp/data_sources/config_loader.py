"""
[OPT-20] Commercial DataSource Config Loader — Fail-Closed API Key Management.
=============================================================================
Tự động nạp API Keys từ .env cho các nguồn dữ liệu thương mại/ngoại vi.
Nguyên lý DNA SCP:
  - Fail-Closed: Thiếu hoặc sai API key -> Từ chối (MissingApiKeyError), không chạy ngầm hoặc bịa đặt.
  - Zero Hardcoded Paths: Đường dẫn .env được giải quyết động từ repo root.
  - Zero Secrets Leak: Không in hoặc ghi log raw API keys ra màn hình hay exceptions.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.data_sources.config_loader")

# Mapping giữa tên nguồn dữ liệu (chuẩn hóa chữ thường) và tên biến môi trường tương ứng
COMMERCIAL_SOURCE_KEYS: dict[str, str] = {
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "alpha_vantage": "ALPHAVANTAGE_API_KEY",
    "fred": "FRED_API_KEY",
    "newsapi": "NEWSAPI_API_KEY",
    "news_api": "NEWSAPI_API_KEY",
    "google_factcheck": "GOOGLE_FACT_CHECK_API_KEY",
    "google_fact_check": "GOOGLE_FACT_CHECK_API_KEY",
    "googlefactcheck": "GOOGLE_FACT_CHECK_API_KEY",
    "courtlistener": "COURTLISTENER_TOKEN",
    "court_listener": "COURTLISTENER_TOKEN",
    "usda": "USDA_API_KEY",
    "agriculture": "USDA_API_KEY",
    "ncbi": "NCBI_API_KEY",
    "pubmed": "PUBMED_API_KEY",
    "biology": "NCBI_API_KEY",
    "medical": "PUBMED_API_KEY",
    "nvd": "NVD_API_KEY",
    "cybersecurity": "NVD_API_KEY",
    "eia": "EIA_API_KEY",
    "energy": "EIA_API_KEY",
    "eric": "ERIC_API_KEY",
    "caselaw": "CASE_LAW_API_KEY",
    "case_law": "CASE_LAW_API_KEY",
    "legal": "CASE_LAW_API_KEY",
    "noaa": "NOAA_API_KEY",
    "wikiart": "WIKIART_API_KEY",
    "wiki_art": "WIKIART_API_KEY",
}

# Các giá trị placeholder không hợp lệ (fail-closed nếu phát hiện)
INVALID_PLACEHOLDERS: set[str] = {
    "demo",
    "placeholder",
    "changeme",
    "todo",
    "none",
    "null",
    "your_api_key_here",
    "your_key_here",
    "your-api-key-here",
    "your_api_key",
    "your-key-here",
    "dummy",
    "test_key",
    "invalid",
    "xxx",
    "api_key",
    "api-key",
    "insert_key_here",
    "replace_me",
    "replace-me",
    "fake",
    "fake_key",
    "undefined",
    "false",
    "0",
    "sample",
    "test",
    "testing",
}


class ConfigLoaderError(Exception):
    """Lỗi cơ bản trong quá trình nạp cấu hình nguồn dữ liệu."""
    pass


class MissingApiKeyError(ConfigLoaderError, ValueError):
    """Lỗi fail-closed khi thiếu hoặc không hợp lệ API Key cho nguồn thương mại."""
    pass


def mask_secret(secret: str) -> str:
    """Che giấu secret để bảo vệ thông tin nhạy cảm trong logs/thông báo."""
    if not secret:
        return "<empty>"
    if len(secret) <= 6:
        return "***"
    return f"{secret[:3]}...{secret[-2:]}"


def parse_env_file(env_path: Path) -> dict[str, str]:
    """Phân tích cú pháp file .env an toàn với fallback đa mã hóa và chuẩn POSIX."""
    if not env_path.is_file():
        return {}

    # Thử dùng dotenv.dotenv_values nếu có sẵn
    try:
        from dotenv import dotenv_values
        raw_vals = dotenv_values(env_path)
        if raw_vals:
            parsed: dict[str, str] = {}
            for k, v in raw_vals.items():
                if k and v is not None:
                    # Strip 'export ' nếu dotenv chưa bóc
                    clean_k = k[7:].strip() if k.startswith("export ") else k.strip()
                    parsed[clean_k] = str(v)
            if parsed:
                return parsed
    except Exception as exc:
        logger.debug("[config_loader] dotenv_values fallback to internal parser: %s", exc)

    # Internal fallback parser hỗ trợ inline comments, export, và nhiều bảng mã
    parsed = {}
    content = ""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            content = env_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if not content:
        return parsed

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, _, rest = line.partition("=")
        key = key.strip()
        rest = rest.strip()
        if not key:
            continue

        # Xử lý chuỗi được bao trong ngoặc kép hoặc ngoặc đơn
        if rest.startswith('"'):
            # Tìm dấu nháy kép đóng
            end_quote = rest.find('"', 1)
            if end_quote != -1:
                val = rest[1:end_quote]
            else:
                val = rest.strip('"')
        elif rest.startswith("'"):
            # Tìm dấu nháy đơn đóng
            end_quote = rest.find("'", 1)
            if end_quote != -1:
                val = rest[1:end_quote]
            else:
                val = rest.strip("'")
        else:
            # Không có ngoặc: cắt bỏ comment phía sau nếu có ' #'
            val = rest.split(" #")[0].strip()

        parsed[key] = val

    return parsed


class DataSourceConfigLoader:
    """Quản lý và nạp cấu hình API Keys cho các data sources thương mại.
    
    Tuân thủ nguyên tắc Fail-Closed:
    - Nếu nguồn thương mại được yêu cầu (required=True) mà thiếu key -> ném MissingApiKeyError.
    - Placeholder (demo, dummy, todo...) bị từ chối triệt để.
    """

    def __init__(
        self,
        env_path: Path | str | None = None,
        env_dict: dict[str, str] | None = None,
        auto_load_env: bool = True,
    ) -> None:
        self._env_dict: dict[str, str] = dict(env_dict) if env_dict is not None else {}
        self._custom_env = env_dict is not None

        if env_path is not None:
            self._env_path = Path(env_path).resolve()
        else:
            override = os.environ.get("SCP_ENV_FILE")
            if override:
                self._env_path = Path(override).expanduser().resolve()
            else:
                # Dynamic resolution từ vị trí file tới root repo
                self._env_path = Path(__file__).resolve().parents[2] / ".env"

        if auto_load_env and not self._custom_env:
            self.load_env()

    def load_env(self) -> None:
        """Nạp các biến môi trường từ .env vào cache và os.environ nếu chưa có."""
        if self._env_path.is_file():
            parsed = parse_env_file(self._env_path)
            for k, v in parsed.items():
                if k not in self._env_dict:
                    self._env_dict[k] = v
                if k not in os.environ:
                    os.environ[k] = v

    def resolve_env_var_name(self, source_name: str) -> str:
        """Xác định tên biến môi trường cho một data source.
        
        Tự động chuẩn hóa dấu gạch ngang, dấu cách và các bí danh thông dụng.
        """
        raw = str(source_name).strip().lower()
        # Chuẩn hóa dấu gạch ngang và khoảng trắng thành dấu gạch dưới
        clean_name = re.sub(r"[\s\-]+", "_", raw).strip("_")

        # 1. Tra cứu trực tiếp theo tên chuẩn hóa
        if clean_name in COMMERCIAL_SOURCE_KEYS:
            return COMMERCIAL_SOURCE_KEYS[clean_name]

        # 2. Tra cứu sau khi bỏ toàn bộ dấu gạch dưới (vd 'courtlistener')
        condensed = clean_name.replace("_", "")
        for k, v in COMMERCIAL_SOURCE_KEYS.items():
            if k.replace("_", "") == condensed:
                return v

        # 3. Fallback: chuyển đổi sang chữ hoa hợp lệ
        valid_ident = re.sub(r"[^A-Za-z0-9_]", "_", clean_name).upper()
        return f"{valid_ident}_API_KEY"

    def is_valid_key_value(self, value: str | None) -> bool:
        """Kiểm tra giá trị key có hợp lệ hay không (không rỗng, không phải placeholder)."""
        if not value:
            return False
        stripped = value.strip().strip("'\"").strip()
        if not stripped:
            return False
        lowered = stripped.lower()

        # Kiểm tra danh sách từ khóa placeholder
        if lowered in INVALID_PLACEHOLDERS:
            return False

        # Kiểm tra pattern dạng <your_key_here> hoặc <api_key>
        if stripped.startswith("<") and stripped.endswith(">"):
            return False

        # Kiểm tra pattern prefix/suffix điển hình
        if lowered.startswith(("your_", "your-", "insert_")) or lowered.endswith(("_placeholder", "-placeholder")):
            return False

        if "placeholder" in lowered or "dummy" in lowered or "changeme" in lowered:
            return False

        return True

    def get_api_key(self, source_name: str, required: bool = True) -> str:
        """Lấy API key cho nguồn dữ liệu.
        
        Args:
            source_name: Tên nguồn dữ liệu (vd 'alphavantage', 'fred', 'newsapi')
            required: Nếu True, fail-closed khi thiếu key hoặc key là placeholder.
            
        Returns:
            API key hợp lệ nếu có.
            
        Raises:
            MissingApiKeyError: Nếu required=True và key không hợp lệ hoặc bị thiếu.
        """
        env_var = self.resolve_env_var_name(source_name)

        # Thứ tự ưu tiên:
        # 1. Custom env dict được truyền tường minh vào constructor (cô lập hoàn toàn)
        # 2. Biến môi trường thực tế os.environ của tiến trình
        # 3. Cache từ file .env
        raw_value: str | None = None
        if self._custom_env:
            raw_value = self._env_dict.get(env_var)
        elif env_var in os.environ:
            raw_value = os.environ[env_var]
        else:
            raw_value = self._env_dict.get(env_var)

        key_value = (raw_value or "").strip()

        if not self.is_valid_key_value(key_value):
            if required:
                reason = "bị thiếu" if not key_value else f"là placeholder không hợp lệ ({mask_secret(key_value)})"
                raise MissingApiKeyError(
                    f"Nguồn dữ liệu thương mại '{source_name}' yêu cầu API Key qua biến môi trường '{env_var}' "
                    f"nhưng key {reason}. [FAIL-CLOSED: Thao tác bị chặn]"
                )
            return ""

        return key_value

    def require_api_key(self, source_name: str) -> str:
        """Lấy API key với cơ chế bắt buộc fail-closed."""
        return self.get_api_key(source_name, required=True)

    def has_api_key(self, source_name: str) -> bool:
        """Kiểm tra nguồn dữ liệu đã có key hợp lệ chưa."""
        try:
            val = self.get_api_key(source_name, required=False)
            return bool(val)
        except Exception:
            return False

    def get_configured_sources(self) -> list[str]:
        """Danh sách các nguồn thương mại đã được cấu hình key hợp lệ."""
        configured = []
        for src in sorted(COMMERCIAL_SOURCE_KEYS.keys()):
            if self.has_api_key(src) and src not in configured:
                configured.append(src)
        return configured

    def validate_sources(self, sources: list[str], fail_closed: bool = True) -> dict[str, bool]:
        """Xác thực danh sách nguồn thương mại.
        
        Nếu fail_closed=True, ném MissingApiKeyError ngay khi gặp nguồn đầu tiên thiếu key.
        """
        results: dict[str, bool] = {}
        for src in sources:
            is_ok = self.has_api_key(src)
            results[src] = is_ok
            if not is_ok and fail_closed:
                # Kích hoạt lỗi qua get_api_key với required=True
                self.get_api_key(src, required=True)
        return results


# Module-level singleton
_DEFAULT_LOADER: DataSourceConfigLoader | None = None


def get_default_loader() -> DataSourceConfigLoader:
    global _DEFAULT_LOADER
    if _DEFAULT_LOADER is None:
        _DEFAULT_LOADER = DataSourceConfigLoader()
    return _DEFAULT_LOADER


def reset_default_loader() -> None:
    """Xóa singleton default loader (phục vụ test cách ly)."""
    global _DEFAULT_LOADER
    _DEFAULT_LOADER = None


def set_default_loader(loader: DataSourceConfigLoader | None) -> None:
    """Gán tường minh singleton default loader (phục vụ test)."""
    global _DEFAULT_LOADER
    _DEFAULT_LOADER = loader


def get_api_key(source_name: str, required: bool = True) -> str:
    """Hàm tiện ích lấy API Key từ cấu hình mặc định (Fail-Closed)."""
    return get_default_loader().get_api_key(source_name, required=required)


def require_api_key(source_name: str) -> str:
    """Hàm tiện ích yêu cầu API Key hợp lệ (Fail-Closed)."""
    return get_default_loader().require_api_key(source_name)


def has_api_key(source_name: str) -> bool:
    """Hàm tiện ích kiểm tra nguồn dữ liệu có API Key hợp lệ."""
    return get_default_loader().has_api_key(source_name)
