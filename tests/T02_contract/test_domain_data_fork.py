"""Contract tests for DomainDataFork integration (Wave Local Domain Data Sources).

Kiểm chứng hành vi thực tế của tầng dữ liệu chuyên ngành cục bộ:
  - Thiên văn học (Astronomy)
  - Hóa học (Chemistry)
  - Vật lý (Physics)
  - Địa lý (Geography)
  - Toán học (Math)
  - E2E QuestionRouter integration & Fail-Closed fallback
"""
from __future__ import annotations

import pytest

from scp.runtime.domain_data_fork import resolve_domain_data_lookup
from scp.runtime.question_router import resolve_lookup_data


class TestDomainDataFork:
    """Kiểm tra từng bộ phận của domain_data_fork."""

    def test_astronomy_mars_moons(self) -> None:
        result = resolve_domain_data_lookup("Sao Hỏa có bao nhiêu mặt trăng?")
        assert result is not None
        assert "2 vệ tinh" in result["text"] or "2" in result["text"]
        assert "AstronomyDataSource" in result["api_name"]

    def test_astronomy_universe_age(self) -> None:
        result = resolve_domain_data_lookup("Tuổi của vũ trụ là bao nhiêu?")
        assert result is not None
        assert "13.8" in result["text"]

    def test_chemistry_molar_mass_water(self) -> None:
        result = resolve_domain_data_lookup("Khối lượng phân tử của H2O?")
        assert result is not None
        assert "18.015" in result["text"]
        assert "ChemistryDataSource" in result["api_name"]

    def test_chemistry_molar_mass_acid(self) -> None:
        result = resolve_domain_data_lookup("Phân tử khối của H2SO4 là bao nhiêu?")
        assert result is not None
        assert "98.07" in result["text"] or "98.0" in result["text"]

    def test_chemistry_element_iron(self) -> None:
        result = resolve_domain_data_lookup("Số hiệu nguyên tử của Fe là bao nhiêu?")
        assert result is not None
        assert "26" in result["text"]
        assert "55.845" in result["text"]

    def test_physics_speed_of_light(self) -> None:
        result = resolve_domain_data_lookup("Tốc độ ánh sáng trong chân không là bao nhiêu?")
        assert result is not None
        assert "299,792,458" in result["text"]
        assert "PhysicsDataSource" in result["api_name"]

    def test_physics_planck_constant(self) -> None:
        result = resolve_domain_data_lookup("Hằng số Planck bằng bao nhiêu?")
        assert result is not None
        assert "6.626" in result["text"]

    def test_physics_avogadro_number(self) -> None:
        result = resolve_domain_data_lookup("Số Avogadro có giá trị là bao nhiêu?")
        assert result is not None
        assert "6.022" in result["text"]

    def test_geography_capital_france(self) -> None:
        result = resolve_domain_data_lookup("Thủ đô của Pháp là thành phố nào?")
        assert result is not None
        assert "Paris" in result["text"]
        assert "GeographyDataSource" in result["api_name"]

    def test_geography_capital_vietnam(self) -> None:
        result = resolve_domain_data_lookup("Thủ đô của Việt Nam là gì?")
        assert result is not None
        assert "Hà Nội" in result["text"]

    def test_geography_capital_japan(self) -> None:
        result = resolve_domain_data_lookup("Thủ đô của Nhật Bản là gì?")
        assert result is not None
        assert "Tokyo" in result["text"]

    def test_math_pi_constant(self) -> None:
        result = resolve_domain_data_lookup("Giá trị của số pi là bao nhiêu?")
        assert result is not None
        assert "3.14159" in result["text"]
        assert "MathDataSource" in result["api_name"]

    def test_math_euler_number(self) -> None:
        result = resolve_domain_data_lookup("Số e bằng bao nhiêu?")
        assert result is not None
        assert "2.718" in result["text"]

    def test_usgs_historical_valdivia(self) -> None:
        result = resolve_domain_data_lookup("Trận động đất mạnh nhất lịch sử là trận nào?")
        assert result is not None
        assert "Valdivia" in result["text"]
        assert "9.5" in result["text"]
        assert "USGSDataSource" in result["api_name"]

    def test_usgs_historical_sumatra(self) -> None:
        result = resolve_domain_data_lookup("Trận động đất Sumatra năm 2004 có độ lớn bao nhiêu?")
        assert result is not None
        assert "Sumatra" in result["text"]
        assert "9.1" in result["text"]

    def test_usgs_recent_metadata_extraction_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scp.runtime import domain_data_fork

        class DummyUSGS:
            def query(self, q: str):
                return {
                    "metadata": {
                        "time_window_days": 7,
                        "result_count": 8,
                        "top_events": [
                            {"magnitude": 6.8, "place": "Near Coast of Central Chile", "time": 1234567890}
                        ],
                    }
                }

        monkeypatch.setattr(domain_data_fork, "_get_usgs", lambda: DummyUSGS())
        result = domain_data_fork.resolve_domain_data_lookup("động đất gần đây")
        assert result is not None
        assert "M6.8" in result["text"]
        assert "Central Chile" in result["text"]
        assert "MNone tại None" not in result["text"]

    def test_usgs_egress_host_declared(self) -> None:
        from scp.data_sources.usgs import USGS_EGRESS_HOST
        assert USGS_EGRESS_HOST == "earthquake.usgs.gov"

    def test_biology_codon_lookup(self) -> None:
        result = resolve_domain_data_lookup("Mã di truyền TTT mã hóa cho axit amin nào?")
        assert result is not None
        assert "Phe" in result["text"] or "Phenylalanine" in result["text"]
        assert "BiologyDataSource" in result["api_name"]

    def test_biology_start_codon(self) -> None:
        result = resolve_domain_data_lookup("Codon ATG mã hóa cho amino acid gì?")
        assert result is not None
        assert "Met" in result["text"] or "Methionine" in result["text"]

    def test_biology_amino_acid_lookup(self) -> None:
        result = resolve_domain_data_lookup("Axit amin Alanine có ký hiệu là gì?")
        assert result is not None
        assert "Ala" in result["text"]

    def test_biology_organelle_mitochondria(self) -> None:
        result = resolve_domain_data_lookup("Bào quan ty thể có chức năng gì trong tế bào?")
        assert result is not None
        assert "ATP" in result["text"] or "mitochondria" in result["text"]

    def test_biology_organelle_ribosome(self) -> None:
        result = resolve_domain_data_lookup("Chức năng của ribosome là gì?")
        assert result is not None
        assert "Protein" in result["text"] or "ribosome" in result["text"]

    def test_biology_human_chromosomes(self) -> None:
        result = resolve_domain_data_lookup("Con người có bao nhiêu nhiễm sắc thể?")
        assert result is not None
        assert "46" in result["text"]

    def test_biology_human_genome_size(self) -> None:
        result = resolve_domain_data_lookup("Kích thước bộ gen của con người là bao nhiêu?")
        assert result is not None
        assert "3.2" in result["text"]

    def test_biology_config_loader_integration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scp.data_sources.biology import BiologyDataSource
        from scp.data_sources.config_loader import get_api_key

        # Key is not required, returns "" when absent or placeholder
        monkeypatch.setenv("NCBI_API_KEY", "demo")
        bio = BiologyDataSource()
        assert bio._ncbi_api_key == ""
        assert get_api_key("biology", required=False) == ""

        monkeypatch.setenv("NCBI_API_KEY", "valid_secret_ncbi_key_12345")
        bio2 = BiologyDataSource()
        assert bio2._ncbi_api_key == "valid_secret_ncbi_key_12345"

    def test_biology_registered_in_registry(self) -> None:
        from scp.data_sources import register_all_sources
        reg = register_all_sources()
        assert "BiologyDataSource" in reg.list_sources()

    def test_geology_mohs_scale_diamond(self) -> None:
        result = resolve_domain_data_lookup("Độ cứng của kim cương theo thang Mohs là bao nhiêu?")
        assert result is not None
        assert "10" in result["text"]
        assert "GeologyDataSource" in result["api_name"]

    def test_geology_mohs_scale_talc(self) -> None:
        result = resolve_domain_data_lookup("Khoáng vật talc có độ cứng thang mohs bằng mấy?")
        assert result is not None
        assert "1" in result["text"]

    def test_geology_mohs_scale_quartz(self) -> None:
        result = resolve_domain_data_lookup("Độ cứng của thạch anh theo thang Mohs?")
        assert result is not None
        assert "7" in result["text"]

    def test_geology_earth_layer_crust(self) -> None:
        result = resolve_domain_data_lookup("Cấu tạo của vỏ trái đất như thế nào?")
        assert result is not None
        assert "Vỏ Trái Đất" in result["text"] or "Crust" in result["text"]

    def test_geology_earth_layer_mantle(self) -> None:
        result = resolve_domain_data_lookup("Lớp manti có đặc điểm gì?")
        assert result is not None
        assert "Manti" in result["text"] or "Mantle" in result["text"]

    def test_geology_rock_types(self) -> None:
        res1 = resolve_domain_data_lookup("Đá magma được hình thành như thế nào?")
        assert res1 is not None and "magma" in res1["text"]
        res2 = resolve_domain_data_lookup("Đá trầm tích được hình thành từ đâu?")
        assert res2 is not None and "trầm tích" in res2["text"]
        res3 = resolve_domain_data_lookup("Đặc điểm của đá biến chất?")
        assert res3 is not None and "biến chất" in res3["text"]

    def test_fallback_fail_closed_unrelated(self) -> None:
        result = resolve_domain_data_lookup("Thời tiết ngày mai mưa hay nắng?")
        # Domain data fork không quản lý thời tiết -> phải trả None để nhường cho weather fork
        assert result is None

    def test_astronomy_earth_moons_and_metrics(self) -> None:
        # Trái Đất có 1 mặt trăng
        res_moon = resolve_domain_data_lookup("Trái Đất có bao nhiêu mặt trăng?")
        assert res_moon is not None
        assert "1 vệ tinh" in res_moon["text"] or "Mặt Trăng" in res_moon["text"]

        # Khối lượng Trái Đất
        res_mass = resolve_domain_data_lookup("Khối lượng của Trái Đất là bao nhiêu?")
        assert res_mass is not None
        assert "5.972e+24" in res_mass["text"]

        # Bán kính Trái Đất
        res_radius = resolve_domain_data_lookup("Bán kính của Trái Đất là bao nhiêu?")
        assert res_radius is not None
        assert "6,371" in res_radius["text"]

    def test_geography_false_positive_guard(self) -> None:
        # 'Thủ đô của nước nào có ý nghĩa lớn nhất?' không bị match vào Ý
        result = resolve_domain_data_lookup("Thủ đô của nước nào có ý nghĩa lớn nhất?")
        assert result is None

        # Từ ngắn 'anh' trong 'bức tranh' không bị match vào Anh
        result_anh = resolve_domain_data_lookup("Thủ đô của bức tranh này là gì?")
        assert result_anh is None

        # Thủ đô của Ý vẫn trả về Roma
        result_y = resolve_domain_data_lookup("Thủ đô của Ý là gì?")
        assert result_y is not None
        assert "Roma" in result_y["text"]

        # Các cụm từ đồng âm / ngữ cảnh sai của từ ngắn quốc gia không bị match nhầm
        assert resolve_domain_data_lookup("Thủ đô của nơi có ý thức cao là gì?") is None
        assert resolve_domain_data_lookup("Thủ đô của anh trai bạn là gì?") is None
        assert resolve_domain_data_lookup("Thủ đô của người có đạo đức là gì?") is None
        assert resolve_domain_data_lookup("Thủ đô của ngành mỹ thuật là gì?") is None
        assert resolve_domain_data_lookup("Thủ đô của nơi có gió lào là gì?") is None

    def test_geology_granite_not_hijacked_by_usgs(self) -> None:
        from scp.data_sources.geology import GeologyDataSource
        geo = GeologyDataSource()
        q_res = geo.query("Đá granite là gì?")
        assert q_res.get("found") is False
        assert q_res.get("answer") is None

        result = resolve_domain_data_lookup("Đá granite là gì?")
        assert result is None

    def test_chemistry_vitamin_c_k_not_elements(self) -> None:
        res_c = resolve_domain_data_lookup("Số hiệu nguyên tử của vitamin C là bao nhiêu?")
        assert res_c is None

        res_k = resolve_domain_data_lookup("Nguyên tử khối của vitamin K là bao nhiêu?")
        assert res_k is None

        # 'nước nào' không bị match nhầm vào nước (H2O)
        res_nuoc_nao = resolve_domain_data_lookup("Phân tử khối của nước nào lớn nhất?")
        assert res_nuoc_nao is None

    def test_math_pixel_pin_not_pi(self) -> None:
        res_pixel = resolve_domain_data_lookup("Thông số pixel của màn hình là gì?")
        assert res_pixel is None

        res_pin = resolve_domain_data_lookup("Mã số pin của thẻ ATM là gì?")
        assert res_pin is None

        # Hyphenated words không bị word-boundary match nhầm vào Số e / Số Pi
        assert resolve_domain_data_lookup("Số e-mail của bạn là gì?") is None
        assert resolve_domain_data_lookup("Thông số pi-ta-go là gì?") is None

    def test_biology_codon_aug_start_codon(self) -> None:
        res_aug = resolve_domain_data_lookup("Codon AUG mã hóa cho axit amin nào?")
        assert res_aug is not None
        assert "Met" in res_aug["text"] or "Methionine" in res_aug["text"]

        res_stop = resolve_domain_data_lookup("Bộ ba UAA là gì?")
        assert res_stop is not None
        assert "Stop codon" in res_stop["text"]

    def test_astronomy_distance_and_multi_planet_guard(self) -> None:
        # Khoảng cách từ Trái Đất đến Mặt Trời
        res_dist = resolve_domain_data_lookup("Khoảng cách từ Trái Đất đến Mặt Trời là bao nhiêu?")
        assert res_dist is not None
        assert "149.6" in res_dist["text"] or "1.496e+11" in res_dist["text"]

        # Multi-planet comparative queries fail-closed
        assert resolve_domain_data_lookup("Mặt trăng lớn nhất của Trái Đất và Sao Hỏa là gì?") is None
        assert resolve_domain_data_lookup("Khoảng cách giữa Trái Đất và Sao Hỏa là bao nhiêu?") is None

        # Canonical name_en check
        from scp.data_sources.astronomy import AstronomyDataSource
        astro = AstronomyDataSource()
        fetched = astro.fetch("planet_info", "Trái Đất")
        assert fetched is not None
        assert fetched["metadata"]["name_en"] == "earth"

    def test_shard_domain_keywords_conflict_resolution(self) -> None:
        from scp.core.partition.shard import DOMAIN_KEYWORDS, detect_domain
        # Khoáng sản đã bị xóa khỏi chemistry
        assert "khoáng sản" not in DOMAIN_KEYWORDS["chemistry"]
        assert "khoáng sản" in DOMAIN_KEYWORDS["geology"]
        assert detect_domain("Khai thác khoáng sản") == "geology"

        # Các từ khóa sinh học mới
        for kw in ["axit amin", "axit nucleic", "mã di truyền", "codon"]:
            assert kw in DOMAIN_KEYWORDS["biology"]
        assert detect_domain("Các loại axit amin thiết yếu") == "biology"
        assert detect_domain("Bảng mã di truyền và codon") == "biology"

    def test_fallback_fail_closed_reasoning(self) -> None:
        result = resolve_domain_data_lookup("Làm sao để sống hạnh phúc và bình an?")
        assert result is None


class TestQuestionRouterE2EDomainFork:
    """Kiểm tra việc tích hợp hoàn chỉnh vào hàm resolve_lookup_data của router."""

    def test_router_resolves_chemistry(self) -> None:
        ans = resolve_lookup_data("Phân tử khối của H2O là bao nhiêu?")
        assert ans is not None
        assert "18.015" in ans["text"]
        assert "ChemistryDataSource" in ans["api_name"]

    def test_router_resolves_physics(self) -> None:
        ans = resolve_lookup_data("Tốc độ ánh sáng là bao nhiêu?")
        assert ans is not None
        assert "299,792,458" in ans["text"]

    def test_router_resolves_geography(self) -> None:
        ans = resolve_lookup_data("Thủ đô của Pháp là gì?")
        assert ans is not None
        assert "Paris" in ans["text"]

    def test_router_resolves_usgs(self) -> None:
        ans = resolve_lookup_data("Trận động đất mạnh nhất lịch sử là trận nào?")
        assert ans is not None
        assert "Valdivia" in ans["text"]
        assert "USGSDataSource" in ans["api_name"]

    def test_router_resolves_biology(self) -> None:
        ans = resolve_lookup_data("Mã di truyền TTT mã hóa cho axit amin nào?")
        assert ans is not None
        assert "Phe" in ans["text"]
        assert "BiologyDataSource" in ans["api_name"]

    def test_router_resolves_geology(self) -> None:
        ans = resolve_lookup_data("Độ cứng của kim cương theo thang Mohs là bao nhiêu?")
        assert ans is not None
        assert "10" in ans["text"]
        assert "GeologyDataSource" in ans["api_name"]
