"""
Unit & Contract Tests for Commercial DataSource Config Loader.
=============================================================
Kiểm chứng 100% các nhánh nhân quả của scp/data_sources/config_loader.py:
  - Nạp tự động từ .env và hỗ trợ cú pháp export, inline comments
  - Chuẩn hóa tên nguồn dữ liệu (hyphen, space, aliases)
  - Fail-closed khi thiếu key hoặc key mang các pattern placeholder
  - Thứ tự ưu tiên nạp key (custom env dict > os.environ > .env file)
  - Không leak secret trong thông báo lỗi
  - Tiện ích module-level và quản lý singleton loader
  - Xác thực nhiều nguồn thương mại
"""
import os
import pytest
from pathlib import Path

from scp.data_sources.config_loader import (
    COMMERCIAL_SOURCE_KEYS,
    DataSourceConfigLoader,
    MissingApiKeyError,
    get_api_key,
    has_api_key,
    mask_secret,
    parse_env_file,
    require_api_key,
    reset_default_loader,
    set_default_loader,
)


def test_parse_env_file(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "# Comment line\n"
        "ALPHAVANTAGE_API_KEY=alpha_secret_123\n"
        "FRED_API_KEY=\"fred_secret_456\"\n"
        "EMPTY_KEY=\n"
        "SPACED_KEY = 'spaced_secret_789' \n",
        encoding="utf-8",
    )
    parsed = parse_env_file(env_file)
    assert parsed["ALPHAVANTAGE_API_KEY"] == "alpha_secret_123"
    assert parsed["FRED_API_KEY"] == "fred_secret_456"
    assert parsed["SPACED_KEY"] == "spaced_secret_789"
    assert "EMPTY_KEY" not in parsed or parsed["EMPTY_KEY"] == ""


def test_parse_env_file_export_and_inline_comments(tmp_path):
    env_file = tmp_path / ".env.export_comments"
    env_file.write_text(
        "export FRED_API_KEY=\"secret_fred_val\" # fred production key\n"
        "export COURTLISTENER_TOKEN='token_single_quotes' # comment\n"
        "NEWSAPI_API_KEY=raw_key_no_quotes # inline comment\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(env_file)
    assert parsed["FRED_API_KEY"] == "secret_fred_val"
    assert parsed["COURTLISTENER_TOKEN"] == "token_single_quotes"
    assert parsed["NEWSAPI_API_KEY"] == "raw_key_no_quotes"


def test_mask_secret():
    assert mask_secret("") == "<empty>"
    assert mask_secret("123") == "***"
    assert mask_secret("abcdef123456") == "abc...56"


def test_resolve_env_var_name_normalization():
    loader = DataSourceConfigLoader(env_dict={}, auto_load_env=False)
    # Hyphenated, space-separated, và aliases
    assert loader.resolve_env_var_name("court-listener") == "COURTLISTENER_TOKEN"
    assert loader.resolve_env_var_name("court_listener") == "COURTLISTENER_TOKEN"
    assert loader.resolve_env_var_name("alpha-vantage") == "ALPHAVANTAGE_API_KEY"
    assert loader.resolve_env_var_name("google factcheck") == "GOOGLE_FACT_CHECK_API_KEY"
    assert loader.resolve_env_var_name("case-law") == "CASE_LAW_API_KEY"
    # Nguồn mới chưa có trong mapping chuẩn -> Chuẩn hóa hợp lệ sang _API_KEY
    assert loader.resolve_env_var_name("custom-service-x") == "CUSTOM_SERVICE_X_API_KEY"


def test_config_loader_valid_key():
    loader = DataSourceConfigLoader(
        env_dict={"ALPHAVANTAGE_API_KEY": "valid_alpha_token_999"},
        auto_load_env=False,
    )
    key = loader.get_api_key("alphavantage")
    assert key == "valid_alpha_token_999"
    assert loader.has_api_key("alphavantage") is True


def test_config_loader_fail_closed_missing():
    loader = DataSourceConfigLoader(env_dict={}, auto_load_env=False)
    # Required=True (mặc định) -> Ném MissingApiKeyError
    with pytest.raises(MissingApiKeyError) as exc_info:
        loader.get_api_key("alphavantage")
    assert "alphavantage" in str(exc_info.value)
    assert "ALPHAVANTAGE_API_KEY" in str(exc_info.value)
    assert "FAIL-CLOSED" in str(exc_info.value)

    # require_api_key cũng phải ném lỗi
    with pytest.raises(MissingApiKeyError):
        loader.require_api_key("alphavantage")


def test_config_loader_fail_closed_placeholder():
    placeholders = [
        "demo",
        "placeholder",
        "TODO",
        "your_api_key_here",
        "dummy",
        "  ",
        "<your_api_key>",
        "your-api-key",
        "insert_key_here",
        "my_dummy_token",
        "test",
        "undefined",
    ]
    for ph in placeholders:
        loader = DataSourceConfigLoader(
            env_dict={"FRED_API_KEY": ph},
            auto_load_env=False,
        )
        with pytest.raises(MissingApiKeyError):
            loader.get_api_key("fred", required=True)
        assert loader.has_api_key("fred") is False


def test_config_loader_optional_missing_returns_empty():
    loader = DataSourceConfigLoader(env_dict={}, auto_load_env=False)
    # required=False -> Không ném lỗi, trả về chuỗi rỗng
    key = loader.get_api_key("newsapi", required=False)
    assert key == ""
    assert loader.has_api_key("newsapi") is False


def test_config_loader_get_configured_sources():
    loader = DataSourceConfigLoader(
        env_dict={
            "ALPHAVANTAGE_API_KEY": "real_alpha_key",
            "FRED_API_KEY": "real_fred_key",
            "NEWSAPI_API_KEY": "demo",  # placeholder -> bỏ qua
        },
        auto_load_env=False,
    )
    configured = loader.get_configured_sources()
    assert "alphavantage" in configured
    assert "fred" in configured
    assert "newsapi" not in configured


def test_config_loader_validate_sources():
    loader = DataSourceConfigLoader(
        env_dict={
            "ALPHAVANTAGE_API_KEY": "real_alpha_key",
        },
        auto_load_env=False,
    )
    # fail_closed=False -> trả về dict trạng thái
    res = loader.validate_sources(["alphavantage", "fred"], fail_closed=False)
    assert res == {"alphavantage": True, "fred": False}

    # fail_closed=True -> ném lỗi trên nguồn thiếu
    with pytest.raises(MissingApiKeyError):
        loader.validate_sources(["alphavantage", "fred"], fail_closed=True)


def test_custom_env_file_loading(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text("COURTLISTENER_TOKEN=court_secret_token_abc\n", encoding="utf-8")
    loader = DataSourceConfigLoader(env_path=env_file, auto_load_env=True)
    assert loader.get_api_key("courtlistener") == "court_secret_token_abc"


def test_precedence_rules(tmp_path, monkeypatch):
    env_file = tmp_path / "precedence.env"
    env_file.write_text("FRED_API_KEY=file_key_111\nALPHAVANTAGE_API_KEY=file_alpha_222\n", encoding="utf-8")

    # os.environ ghi đè file .env
    monkeypatch.setenv("FRED_API_KEY", "env_override_key_999")
    loader = DataSourceConfigLoader(env_path=env_file, auto_load_env=True)
    assert loader.get_api_key("fred") == "env_override_key_999"
    assert loader.get_api_key("alphavantage") == "file_alpha_222"

    # custom env_dict tường minh có ưu tiên cao nhất
    custom_loader = DataSourceConfigLoader(
        env_dict={"FRED_API_KEY": "custom_dict_key_888"},
        auto_load_env=False,
    )
    assert custom_loader.get_api_key("fred") == "custom_dict_key_888"


def test_module_level_helpers(monkeypatch):
    reset_default_loader()
    mock_loader = DataSourceConfigLoader(
        env_dict={
            "ALPHAVANTAGE_API_KEY": "module_level_alpha_key",
        },
        auto_load_env=False,
    )
    set_default_loader(mock_loader)

    try:
        assert get_api_key("alphavantage") == "module_level_alpha_key"
        assert require_api_key("alphavantage") == "module_level_alpha_key"
        assert has_api_key("alphavantage") is True
        assert has_api_key("fred") is False
        with pytest.raises(MissingApiKeyError):
            require_api_key("fred")
    finally:
        reset_default_loader()


def test_config_loader_error_message_masks_placeholder():
    loader = DataSourceConfigLoader(
        env_dict={"FRED_API_KEY": "demo_long_placeholder_1234567890"},
        auto_load_env=False,
    )
    with pytest.raises(MissingApiKeyError) as exc_info:
        loader.get_api_key("fred", required=True)
    msg = str(exc_info.value)
    assert "demo_long_placeholder_1234567890" not in msg
    assert "dem...90" in msg
