# SCP CIRCUIT: M06 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M06-closure.json)
"""SCP V14 PREDICTIVE helpers — DB init, DataCrawler, QuestionGenerator, Predictor, Verifier."""
from __future__ import annotations

import hashlib
import logging
import os
import random
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from scp.core.api_utils import fetch_with_retry
from scp.core.db_manager import db_exec, db_query_all

logger = logging.getLogger("scp.prediction")

# [Mimosa B311-fix 2026-09-30] Sinh giá trị "AI dự đoán" giả lập cho câu hỏi
# dự đoán (nhiệt độ, giá crypto, tỉ giá, số tiểu hành tinh) — sampling phi
# mật mã: không token, không secret, không ID/nonce cần unguessable; đây là
# nhiễu ±% quanh giá trị hiện tại để tạo training data. Dùng instance Random
# RIÊNG (SystemRandom, seed từ os.urandom) thay cho global RNG để (1) tách
# biệt với mọi lời random.seed() của module khác và (2) làm rõ ràng tại call
# site rằng đây là nguồn ngẫu nhiên độc lập, phi bảo mật.
_PREDICT_RNG = random.SystemRandom()


# ============================================================
# PREDICTIONS TABLE
# ============================================================
def init_predictions_db():
    """Tạo bảng predictions trong SQLite."""
    db_exec("""
        CREATE TABLE IF NOT EXISTS predictions (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            domain TEXT,
            predicted_answer TEXT,
            confidence REAL DEFAULT 0.0,
            check_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            actual_answer TEXT,
            verified_at TEXT,
            error_type TEXT,
            error_reason TEXT,
            source TEXT,
            real_value TEXT,
            sha256 TEXT
        )
    """)
    # [M6-FIX schema] Pre-existing databases created by older versions lack the
    # `entity` column while save_prediction() accepts an entity argument —
    # backfill the column additively (NULL default, instant in SQLite) so the
    # documented write path does not silently drop the field.
    cols = {row["name"] for row in db_query_all("PRAGMA table_info(predictions)")}
    if "entity" not in cols:
        db_exec("ALTER TABLE predictions ADD COLUMN entity TEXT")
    db_exec("CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_pred_check ON predictions(check_date)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_pred_domain ON predictions(domain)")


# ============================================================
# DATA CRAWLER — Thu thập dữ liệu thực từ Internet
# ============================================================
class DataCrawler:
    """Crawl dữ liệu thực từ APIs (urllib, không cần requests)."""

    def crawl_crypto(self) -> list[dict]:
        """Lấy giá crypto hiện tại từ CoinGecko."""
        try:
            url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=10"
            data = fetch_with_retry(url, {"User-Agent": "V14-Predictive/1.0"}, timeout=10, max_retries=2)
            if not data:
                return []
            return [{"coin": c.get("id", ""), "name": c.get("name", ""),
                     "price": c.get("current_price", 0),
                     "change_24h": c.get("price_change_percentage_24h", 0)}
                    for c in data[:10]]
        except Exception as e:
            logger.error(f"Crawl crypto error: {e}", exc_info=True)
            return []

    def crawl_weather_forecast(self, city: str = "Hanoi") -> dict:
        """Lấy dự báo thời tiết 3 ngày tới từ Open-Meteo."""
        try:
            # Geocode
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
            geo_data = fetch_with_retry(geo_url, {"User-Agent": "V14-Predictive/1.0"}, timeout=10)
            if not geo_data or not geo_data.get("results"):
                return {}
            lat = geo_data["results"][0]["latitude"]
            lon = geo_data["results"][0]["longitude"]

            # Forecast 3 days
            wurl = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&forecast_days=3&timezone=auto"
            wdata = fetch_with_retry(wurl, {"User-Agent": "V14-Predictive/1.0"}, timeout=10)
            if not wdata:
                return {}
            daily = wdata.get("daily", {})
            return {
                "city": city,
                "dates": daily.get("time", []),
                "temp_max": daily.get("temperature_2m_max", []),
                "temp_min": daily.get("temperature_2m_min", []),
                "precipitation": daily.get("precipitation_sum", []),
            }
        except Exception as e:
            logger.error(f"Crawl weather error: {e}", exc_info=True)
            return {}

    def crawl_exchange_rates(self) -> dict:
        """Lấy tỉ giá hiện tại từ Frankfurter."""
        try:
            url = "https://api.frankfurter.dev/v1/latest?from=USD"
            data = fetch_with_retry(url, {"User-Agent": "V14-Predictive/1.0"}, timeout=10)
            if not data:
                return {}
            return {"rates": data.get("rates", {}), "date": data.get("date", "")}
        except Exception as e:
            logger.error(f"Crawl exchange error: {e}", exc_info=True)
            return {}

    def crawl_asteroids(self) -> dict:
        """Lấy số tiểu hành tinh gần Trái Đất từ NASA."""
        try:
            api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
            url = f"https://api.nasa.gov/neo/rest/v1/feed/today?detailed=false&api_key={api_key}"
            data = fetch_with_retry(url, {"User-Agent": "V14-Predictive/1.0"}, timeout=10)
            if not data:
                return {}
            return {"count": data.get("element_count", 0)}
        except Exception as e:
            logger.error(f"Crawl NASA error: {e}", exc_info=True)
            return {}

    def crawl_all(self) -> dict:
        """Crawl tất cả nguồn."""
        logger.info("  ? Crawling crypto...")
        crypto = self.crawl_crypto()
        logger.info(f"     {len(crypto)} coins crawled")

        logger.info("  ? Crawling weather (Hanoi, Tokyo, London)...")
        weather = {}
        for city in ["Hanoi", "Tokyo", "London"]:
            w = self.crawl_weather_forecast(city)
            if w:
                weather[city] = w
        logger.info(f"     {len(weather)} cities crawled")

        logger.info("  ? Crawling exchange rates...")
        fx = self.crawl_exchange_rates()
        logger.info(f"     {len(fx.get('rates', {}))} rates crawled")

        logger.info("  ? Crawling NASA asteroids...")
        nasa = self.crawl_asteroids()
        logger.info(f"     {nasa.get('count', 0)} asteroids today")

        return {"crypto": crypto, "weather": weather, "fx": fx, "nasa": nasa}


# ============================================================
# QUESTION GENERATOR — Tạo câu hỏi dự đoán từ crawled data
# ============================================================
class QuestionGenerator:
    """Tạo câu hỏi dự đoán từ dữ liệu crawled — CÓ DEDUP."""

    # [V104.34 #63] TẠI SAO: class attr → unbounded + cross-instance contamination
    # Moved to __init__ with cap (see _seen_questions_max)
    _seen_questions_max = 10000

    def __init__(self):
        # [V104.35 #63] TẠI SAO: was class attr (shared across instances, unbounded).
        # Now instance attr with cap — each QuestionGenerator has its own set,
        # capped at _seen_questions_max to prevent OOM in 24/7 loops.
        self._seen_questions = set()

    def _is_duplicate(self, question: str, threshold: float = 0.9) -> bool:
        """
        Kiểm tra trùng lặp:
          - Trùng tuyệt đối -> bỏ
          - Độ tương đồng > 90% -> coi là trùng
        """
        # Trùng tuyệt đối
        if question in self._seen_questions:
            return True
        # Tương đồng > 90%
        from difflib import SequenceMatcher
        for seen in self._seen_questions:
            ratio = SequenceMatcher(None, question.lower(), seen.lower()).ratio()
            if ratio > threshold:
                return True
        return False

    def _add_seen(self, question: str):
        # [V104.35 #63] Cap _seen_questions to prevent unbounded growth in 24/7 loops
        if len(self._seen_questions) >= self._seen_questions_max:
            # Drop oldest 20% — convert to list, slice, back to set
            self._seen_questions = set(list(self._seen_questions)[-int(self._seen_questions_max * 0.8):])
        self._seen_questions.add(question)

    def generate(self, data: dict) -> list[dict]:
        """Sinh câu hỏi dự đoán từ crawled data — KHÔNG trùng."""
        questions = []

        # Weather predictions (ngày mai, ngày kia) — DEDUP
        for city, forecast in data.get("weather", {}).items():
            dates = forecast.get("dates", [])
            temp_max = forecast.get("temp_max", [])
            for i in range(1, min(3, len(dates))):
                check_date = dates[i]
                if i < len(temp_max):
                    today_temp = temp_max[0] if temp_max else 25
                    q = f"Nhiệt độ cao nhất tại {city} vào ngày {check_date} sẽ là bao nhiêu?"
                    if self._is_duplicate(q):
                        continue
                    self._add_seen(q)
                    ai_pred = round(today_temp + _PREDICT_RNG.uniform(-5, 5), 1)
                    questions.append({
                        "question": q,
                        "ai_answer": f"nhiệt độ cao nhất {city} = {ai_pred}",
                        "domain": "weather",
                        "check_date": check_date,
                        "source": "open-meteo-forecast",
                        "entity": city,
                        "current_value": today_temp,
                    })

        # Crypto predictions (ngày mai) — DEDUP
        for coin in data.get("crypto", [])[:5]:
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            current_price = coin.get("price", 0)
            q = f"Giá {coin['coin']} vào ngày {tomorrow} sẽ là bao nhiêu USD?"
            if self._is_duplicate(q):
                continue
            self._add_seen(q)
            ai_pred = round(current_price * _PREDICT_RNG.uniform(0.9, 1.1), 2)
            questions.append({
                "question": q,
                "ai_answer": f"giá {coin['coin']} = {ai_pred}",
                "domain": "crypto",
                "check_date": tomorrow,
                "source": "coingecko",
                "entity": coin["coin"],
                "current_value": current_price,
            })

        # Exchange rate predictions (ngày mai) — DEDUP
        fx = data.get("fx", {})
        rates = fx.get("rates", {})
        for to_curr in ["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "CNY", "KRW"]:
            if to_curr in rates:
                tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                current_rate = rates[to_curr]
                q = f"Tỉ giá USD sang {to_curr} vào ngày {tomorrow} sẽ là bao nhiêu?"
                if self._is_duplicate(q):
                    continue
                self._add_seen(q)
                ai_pred = round(current_rate * _PREDICT_RNG.uniform(0.97, 1.03), 4)
                questions.append({
                    "question": q,
                    "ai_answer": f"1 USD = {ai_pred} {to_curr}",
                    "domain": "finance",
                    "check_date": tomorrow,
                    "source": "frankfurter",
                    "entity": f"USD|{to_curr}",
                    "current_value": current_rate,
                })

        # Asteroid count (hôm nay — verify ngay) — DEDUP
        nasa = data.get("nasa", {})
        if nasa.get("count") is not None:
            today = datetime.now().strftime("%Y-%m-%d")
            q = f"Có bao nhiêu tiểu hành tinh gần Trái Đất hôm nay ({today})?"
            if not self._is_duplicate(q):
                self._add_seen(q)
                ai_pred = nasa["count"] + _PREDICT_RNG.randint(-5, 5)
                questions.append({
                    "question": q,
                    "ai_answer": f"số tiểu hành tinh = {ai_pred}",
                    "domain": "astronomy",
                    "check_date": today,
                    "source": "nasa-neows",
                    "entity": "today",
                    "current_value": nasa["count"],
                })

        # Thêm câu hỏi từ GeneratorKhamPha (5 lý do nghi ngờ, pools lớn)
        try:
            from scp.core.generator import GeneratorKhamPha
            kp = GeneratorKhamPha()
            for _ in range(5):
                spec = kp.sinh_ngau_nhien()
                q = spec["question"]
                # [M6-FIX contract] V90 MINIMAL generator returns only
                # {question, domain}. This loop used to read spec["ai_answer"]
                # → KeyError on the FIRST spec → the whole KhamPha block was
                # swallowed by the outer except → zero local predictions were
                # ever generated offline, silently. Skip per-spec with an
                # observable warning instead of crashing the block.
                ai_answer = spec.get("ai_answer")
                if not ai_answer:
                    logger.warning(
                        "[predictive] KhamPha spec without ai_answer (V90 minimal "
                        "generator contract) — skipped: %r", q,
                    )
                    continue
                if self._is_duplicate(q):
                    continue
                self._add_seen(q)
                today = datetime.now().strftime("%Y-%m-%d")
                questions.append({
                    "question": q,
                    "ai_answer": ai_answer,
                    "domain": spec.get("loai", spec.get("domain", "unknown")),
                    "check_date": today,
                    "source": f"kham_pha_{spec.get('ly_do_nghi', 'random')}",
                    "entity": "",
                    "current_value": spec.get("baseline"),
                })
        except Exception as e:
            logger.error(f"GeneratorKhamPha error: {e}", exc_info=True)

        return questions


# ============================================================
# PREDICTOR — Lưu dự đoán vào SQLite
# ============================================================
class Predictor:
    """Lưu dự đoán vào SQLite để verify sau."""

    def save_prediction(self, question: str, ai_answer: str, domain: str,
                        check_date: str, source: str, entity: str,
                        current_value: Any, confidence: float = 0.5) -> str:
        """Lưu 1 prediction vào DB."""
        # [FIX] Use UUID to prevent UNIQUE constraint collision
        import uuid as _uuid
        pred_id = f"PRED-{_uuid.uuid4().hex[:12]}"
        ts = datetime.now().astimezone().isoformat()
        sha256 = hashlib.sha256(f"{question}|{ai_answer}|{ts}".encode()).hexdigest()

        try:
            # [M6-FIX] Persist `entity` — the parameter was accepted but never
            # inserted (silent data loss on every prediction row).
            db_exec("""
                INSERT INTO predictions
                (id, timestamp, question, domain, predicted_answer, confidence,
                 check_date, status, source, real_value, sha256, entity)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """, (pred_id, ts, question, domain, ai_answer, confidence,
                  check_date, source, str(current_value) if current_value is not None else None,
                  sha256, entity))
        except Exception as e:
            # [FIX] If still collision, retry with different UUID
            if "UNIQUE" in str(e):
                pred_id = f"PRED-{_uuid.uuid4().hex[:16]}"
                db_exec("""
                    INSERT INTO predictions
                    (id, timestamp, question, domain, predicted_answer, confidence,
                     check_date, status, source, real_value, sha256, entity)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """, (pred_id, ts, question, domain, ai_answer, confidence,
                      check_date, source, str(current_value) if current_value is not None else None,
                      sha256, entity))
            else:
                raise

        return pred_id

    def get_pending_predictions(self) -> list[dict]:
        """Lấy tất cả predictions pending đã đến check_date."""
        today = datetime.now().strftime("%Y-%m-%d")
        return db_query_all(
            "SELECT * FROM predictions WHERE status = 'pending' AND check_date <= ? ORDER BY check_date",
            (today,)
        )

    def get_all_predictions(self, limit: int = 100) -> list[dict]:
        """Lấy tất cả predictions."""
        return db_query_all(
            "SELECT * FROM predictions ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

    def update_prediction(self, pred_id: str, status: str, actual_answer: str,
                          error_type: str = "", error_reason: str = ""):
        """Cập nhật prediction sau khi verify."""
        db_exec("""
            UPDATE predictions
            SET status = ?, actual_answer = ?, verified_at = ?,
                error_type = ?, error_reason = ?
            WHERE id = ?
        """, (status, str(actual_answer), datetime.now().astimezone().isoformat(),
              error_type, error_reason, pred_id))


# ============================================================
# VERIFIER — Kiểm chứng predictions với reality
# ============================================================
class Verifier:
    """Kiểm chứng predictions khi đến check_date."""

    def __init__(self):
        self.crawler = DataCrawler()
        self.predictor = Predictor()

    def verify_pending(self, limit: int | None = None) -> list[dict]:
        """Verify tất cả predictions pending đã đến hạn.

        [M6-FIX contract] The /v105/predictions/verify route documents and
        passes a `limit` (1..100) — this method used to reject it with
        TypeError -> the endpoint always answered 500. The limit now bounds
        how many due predictions are processed (None = no bound, as before).
        """
        pending = self.predictor.get_pending_predictions()
        if limit is not None:
            pending = pending[:limit]
        results = []

        for pred in pending:
            actual = self._fetch_actual(pred)
            if actual is None:
                continue  # Chưa có data thực tế

            # So sánh
            is_correct, error_type, error_reason = self._compare(pred, actual)

            status = "verified_correct" if is_correct else "verified_wrong"
            self.predictor.update_prediction(
                pred["id"], status, str(actual), error_type, error_reason
            )

            results.append({
                "id": pred["id"],
                "question": pred["question"],
                "predicted": pred["predicted_answer"],
                "actual": str(actual),
                "status": status,
                "error_type": error_type,
                "error_reason": error_reason,
            })

            if is_correct:
                logger.info(f"  [OK] CORRECT: {pred['question'][:50]}")
                logger.info(f"     Predicted: {pred['predicted_answer'][:50]}")
                logger.info(f"     Actual: {actual}")
            else:
                logger.error(f"  [FAIL] WRONG: {pred['question'][:50]}")
                logger.info(f"     Predicted: {pred['predicted_answer'][:50]}")
                logger.info(f"     Actual: {actual}")
                logger.error(f"     Error: {error_type} — {error_reason}")

        return results

    def _fetch_actual(self, pred: dict) -> Any | None:
        """Lấy kết quả thực tế cho prediction — dict dispatch (AST clean)."""
        domain = pred.get("domain", "")
        pred.get("source", "")
        check_date = pred.get("check_date", "")
        today = datetime.now().strftime("%Y-%m-%d")

        fetchers = {
            "weather": lambda: self._fetch_weather_actual(pred, check_date, today),
            "crypto": lambda: self._fetch_crypto_actual(pred, check_date, today),
            "finance": lambda: self._fetch_finance_actual(pred, check_date, today),
            "astronomy": lambda: self._fetch_nasa_actual(),
        }
        handler = fetchers.get(domain)
        if handler and (check_date <= today or domain == "astronomy"):
            return handler()
        return None

    def _fetch_weather_actual(self, pred, check_date, today):
        import re
        if check_date > today:
            return None
        m = re.search(r'tại\s+(\w+)\s+vào', pred["question"])
        if not m:
            return None
        city = m.group(1)
        try:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
            geo_data = fetch_with_retry(geo_url, {"User-Agent": "V14/1.0"}, timeout=10)
            if not geo_data or not geo_data.get("results"):
                return None
            lat = geo_data["results"][0]["latitude"]
            lon = geo_data["results"][0]["longitude"]
            wurl = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max&start_date={check_date}&end_date={check_date}"
            wdata = fetch_with_retry(wurl, {"User-Agent": "V14/1.0"}, timeout=10)
            if wdata and wdata.get("daily", {}).get("temperature_2m_max"):
                return wdata["daily"]["temperature_2m_max"][0]
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: return None` — swallowed errors
            # silently → prediction verification never knows WHY actual fetch failed.
            logger.warning(f"[predictive] _fetch_weather_actual failed: {e}", exc_info=True)
            return None

    def _fetch_crypto_actual(self, pred, check_date, today):
        import re
        if check_date > today:
            return None
        m = re.search(r'Giá\s+(\w+)\s+vào', pred["question"])
        if not m:
            return None
        coin = m.group(1)
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
            data = fetch_with_retry(url, {"User-Agent": "V14/1.0"}, timeout=10)
            if data and coin in data:
                return data[coin].get("usd")
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: return None` — swallowed errors silently.
            logger.warning(f"[predictive] _fetch_crypto_actual failed: {e}", exc_info=True)
            return None

    def _fetch_finance_actual(self, pred, check_date, today):
        import re
        if check_date > today:
            return None
        m = re.search(r'USD\s+sang\s+(\w+)', pred["question"])
        if not m:
            return None
        to_curr = m.group(1)
        try:
            url = f"https://api.frankfurter.dev/v1/{check_date}?from=USD&to={to_curr}"
            data = fetch_with_retry(url, {"User-Agent": "V14/1.0"}, timeout=10)
            if data and "rates" in data:
                return data["rates"].get(to_curr)
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: return None` — swallowed errors silently.
            logger.warning(f"[predictive] _fetch_finance_actual failed: {e}", exc_info=True)
            return None

    def _fetch_nasa_actual(self):
        try:
            api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
            url = f"https://api.nasa.gov/neo/rest/v1/feed/today?detailed=false&api_key={api_key}"
            data = fetch_with_retry(url, {"User-Agent": "V14/1.0"}, timeout=10)
            if data:
                return data.get("element_count")
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: return None` — swallowed errors silently.
            logger.warning(f"[predictive] _fetch_nasa_actual failed: {e}", exc_info=True)
            return None

    def _compare(self, pred: dict, actual: Any) -> tuple[bool, str, str]:
        """So sánh predicted vs actual — dict dispatch (AST clean)."""
        import re
        pred_text = pred.get("predicted_answer", "")
        pred_nums = re.findall(r'-?\d+(?:\.\d+)?', pred_text)
        if not pred_nums:
            return (False, "parse_error", "Không extract được số từ prediction")

        pred_val = float(pred_nums[-1])
        actual_val = float(actual)
        domain = pred.get("domain", "")

        # Dict dispatch cho tolerance — AST clean
        tolerances = {
            "weather": lambda p, a: (abs(p - a) <= 2.0, f"Lệch {abs(p-a):.1f}°C (tolerance ±2°C)"),
            "astronomy": lambda p, a: (p == a, f"Predicted {p}, actual {a}"),
        }
        handler = tolerances.get(domain)
        if handler:
            is_correct, error_reason = handler(pred_val, actual_val)
        else:
            # Default: 5% relative tolerance
            if actual_val == 0:
                is_correct = pred_val == 0
                error_reason = "Both zero"
            else:
                rel_err = abs(pred_val - actual_val) / abs(actual_val)
                is_correct = rel_err <= 0.05
                error_reason = f"Lệch {rel_err*100:.1f}% (tolerance 5%)"

        error_type = ""
        if not is_correct:
            error_type = "over_predicted" if pred_val > actual_val else "under_predicted"

        return (is_correct, error_type, error_reason)
