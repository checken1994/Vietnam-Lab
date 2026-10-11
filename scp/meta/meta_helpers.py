"""Standalone helper sub-engines for SCP Meta-Cognition (WorldModel, AbstractionEngine, Identity).

Extracted from `scp/meta/meta.py` for modularity (<= 780 LOC) and re-exported
from `scp.meta.meta` for 100% backward compatibility.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime

from scp.core.db_manager import db_exec, db_query_all, db_query_one

logger = logging.getLogger("scp.meta")


# ============================================================
# 3. WORLD MODEL — Graph quan hệ
# ============================================================
class WorldModel:
    """
    World Model — Graph quan hệ giữa các entities.

    Không chỉ lưu fact riêng lẻ. Lưu quan hệ:
        CO2 -> is_a -> gas
        CO2 -> affects -> climate
        gas -> type_of -> matter
        climate -> influences -> weather

    Cho phép suy luận nhiều bước:
        CO2 -> affects -> climate -> influences -> weather
        -> "CO2 ảnh hưởng thời tiết"
    """

    def add_relation(self, subject: str, relation: str, obj: str,
                     confidence: float = 0.5, source: str = "") -> bool:
        """Thêm 1 quan hệ vào graph."""
        ts = datetime.now().astimezone().isoformat()
        try:
            # Upsert — nếu quan hệ đã tồn tại, tăng times_verified + confidence
            existing = db_query_one(
                "SELECT * FROM meta_world_model WHERE subject = ? AND relation = ? AND object = ?",
                (subject.lower(), relation, obj.lower())
            )
            if existing:
                new_conf = min(1.0, existing["confidence"] + 0.1)
                db_exec("""
                    UPDATE meta_world_model
                    SET confidence = ?, times_verified = ?, timestamp = ?
                    WHERE subject = ? AND relation = ? AND object = ?
                """, (new_conf, existing["times_verified"] + 1, ts,
                      subject.lower(), relation, obj.lower()))
                return False  # Updated, not new
            else:
                db_exec("""
                    INSERT INTO meta_world_model (subject, relation, object, confidence, source, timestamp, times_verified)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (subject.lower(), relation, obj.lower(), confidence, source, ts))
                return True  # New relation
        except Exception as e:
            logger.error(f"WorldModel add_relation error: {e}", exc_info=True)
            return False

    def get_relations(self, subject: str | None = None, relation: str | None = None,
                      obj: str | None = None) -> list[dict]:
        """Truy vấn quan hệ."""
        conditions = []
        params = []
        if subject:
            conditions.append("subject = ?")
            params.append(subject.lower())
        if relation:
            conditions.append("relation = ?")
            params.append(relation)
        if obj:
            conditions.append("object = ?")
            params.append(obj.lower())

        where = " AND ".join(conditions) if conditions else "1=1"
        return db_query_all(f"SELECT * FROM meta_world_model WHERE {where} ORDER BY confidence DESC", tuple(params))  # nosec B608 — input validated by SCP whitelist  # noqa: S608

    def get_neighbors(self, entity: str) -> dict[str, list[str]]:
        """Lấy hàng xóm của 1 entity (graph traversal)."""
        neighbors = defaultdict(list)
        rows = db_query_all(
            "SELECT relation, object FROM meta_world_model WHERE subject = ? AND confidence >= 0.5",
            (entity.lower(),)
        )
        for row in rows:
            neighbors[row["relation"]].append(row["object"])
        return dict(neighbors)

    def infer_chain(self, start: str, max_depth: int = 3) -> list[list[str]]:
        """
        Suy luận nhiều bước: start -> ? -> ? -> ?
        Trả về list of chains.
        """
        chains = [[start.lower()]]
        for _ in range(max_depth):
            new_chains = []
            for chain in chains:
                last = chain[-1]
                neighbors = self.get_neighbors(last)
                for relation, objects in neighbors.items():
                    for obj in objects:
                        if obj not in chain:  # Avoid cycles
                            new_chains.append(chain + [f"--{relation}-->", obj])
            if not new_chains:
                break
            chains = new_chains[:20]  # Limit
        return chains

    def auto_build_from_knowledge(self):
        """Tự xây graph từ knowledge table."""
        try:
            rows = db_query_all("SELECT DISTINCT entity, attribute FROM knowledge WHERE entity IS NOT NULL")
            for row in rows:
                entity = row["entity"]
                attr = row["attribute"]
                self.add_relation(entity, "has_property", attr, confidence=0.9, source="knowledge")
                # Classify entity
                if attr in ("molecular_weight",): self.add_relation(entity, "is_a", "chemical_compound", confidence=0.9, source="auto")
                elif attr in ("temperature",): self.add_relation(entity, "is_a", "location", confidence=0.9, source="auto")
                elif attr in ("price_usd",): self.add_relation(entity, "is_a", "cryptocurrency", confidence=0.9, source="auto")
                elif attr in ("result",): self.add_relation(entity, "is_a", "math_expression", confidence=0.9, source="auto")
        except Exception as e:
            logger.error(f"WorldModel auto_build error: {e}", exc_info=True)

    def get_stats(self) -> dict:
        try:
            total = db_query_one("SELECT COUNT(*) as cnt FROM meta_world_model")["cnt"]
            by_relation = {}
            for r in db_query_all("SELECT relation, COUNT(*) as cnt FROM meta_world_model GROUP BY relation"):
                by_relation[r["relation"]] = r["cnt"]
            return {"total_relations": total, "by_relation": by_relation}
        except Exception as exc:
            logger.warning("meta: world-model stats query failed, reporting zeros: %s", exc, exc_info=True)
            return {"total_relations": 0, "by_relation": {}}


# ============================================================
# 4. ABSTRACTION — Lesson -> Principle -> Policy
# ============================================================
class AbstractionEngine:
    """
    Abstraction Engine — Nâng bài học thành nguyên lý.

    Lesson (cụ thể) -> Principle (trừu tượng) -> Policy (áp dụng)

    Ví dụ:
        Lesson: "CoinGecko timeout 5 lần trong 1 giờ"
        Principle: "API unstable -> ưu tiên cache, giảm call frequency"
        Policy: "CoinGecko: cache_ttl=600s, max_retries=1"

     Cải thiện:
    - MIN_SAMPLES_FOR_PRINCIPLE = 10 (was 50, too high for early cycles)
    -  Reduced from 50 → 10 so principles appear faster
    - With 50, need 50 PASS/FAIL per domain before any principle → too slow
    - With 10, principles appear after ~10 samples (1-2 cycles)
    - Domain tagging theo frame thực tế (math/chemistry/geography/...)
    """

    # Ngưỡng tối thiểu samples để rút principle — tránh overfitting từ vài mẫu
    MIN_SAMPLES_FOR_PRINCIPLE = 10  #  was 50, reduced for faster principle abstraction

    def __init__(self):
        pass

    def abstract_from_lessons(self) -> list[dict]:
        """
         Read calibration_history (BOTH PASS+FAIL) → abstract principles.

        V27 bug: error_history chỉ log FAIL → fail_rate luôn 100% → principles sai.
        V40 fix: dùng calibration_history (has PASS + FAIL) → correct fail_rate.

        V40 dedup: check if similar principle exists before INSERT.
        """
        principles = []
        try:
            #  Use calibration_history (has both PASS + FAIL)
            # thay vì error_history (chỉ có FAIL → fail_rate luôn 100%)
            lessons = db_query_all("""
                SELECT
                    domain as lesson_type,
                    'calibration' as policy_target,
                    actual_verdict as policy_value,
                    confidence,
                    question
                FROM calibration_history
                WHERE actual_verdict IN ('FAIL', 'PASS', 'UNKNOWN', 'PARTIAL', 'CONFLICT')
                ORDER BY timestamp DESC
                LIMIT 5000
            """)

            if not lessons:
                return principles

            # Group by lesson_type (domain)
            by_type = defaultdict(list)
            for lesson in lessons:
                ltype = lesson.get("lesson_type", "unknown") or "unknown"
                by_type[ltype].append(lesson)

            # Abstract mỗi nhóm — chỉ nếu đủ samples (>= MIN_SAMPLES_FOR_PRINCIPLE)
            for ltype, group in by_type.items():
                if len(group) >= self.MIN_SAMPLES_FOR_PRINCIPLE:
                    #  Dedup — check if similar principle already exists
                    if not self._principle_exists(ltype, len(group)):
                        principle = self._create_principle(ltype, group)
                        if principle:
                            principles.append(principle)
        except Exception as e:
            logger.error(f"Abstraction error: {e}", exc_info=True)

        return principles

    def _principle_exists(self, domain: str, sample_count: int) -> bool:
        """ Check if a principle with same domain + similar sample_count already exists."""
        try:
            existing = db_query_all(
                "SELECT id, principle FROM meta_principles WHERE domain = ? ORDER BY id DESC LIMIT 5",
                (f"{domain}_processing",)
            )
            if not existing:
                return False
            # If any existing principle has similar sample_count (±10), skip
            for row in existing:
                p = row.get("principle", "")
                m = re.search(r'(\d+)\s+samples', p)
                if m:
                    existing_count = int(m.group(1))
                    if abs(existing_count - sample_count) <= 10:
                        return True  # Too similar, skip
            return False
        except Exception as exc:
            # Fail-open (return False = not similar) is the current dedup contract; visible now.
            logger.warning("meta: question dedup check failed, treating as dissimilar: %s", exc, exc_info=True)
            return False

    def _create_principle(self, lesson_type: str, lessons: list[dict]) -> dict | None:
        """
         Tạo/cập nhật principle rule — versioning + executable rules.
        Thay vì INSERT mới mỗi lần → UPDATE existing (version++).
        """
        try:
            from scp.meta.principle_rules import PrincipleRuleEngine
            rule_engine = PrincipleRuleEngine()

            total = len(lessons)
            fail_count = sum(1 for lesson in lessons if (lesson.get("policy_value") or "").upper() == "FAIL")
            pass_count = sum(1 for lesson in lessons if (lesson.get("policy_value") or "").upper() == "PASS")
            unknown_count = sum(1 for lesson in lessons if (lesson.get("policy_value") or "").upper() in ("UNKNOWN", "PARTIAL"))
            fail_rate = fail_count / total if total else 0
            pass_rate = pass_count / total if total else 0
            unknown_rate = unknown_count / total if total else 0

            evidence = {
                "samples": total,
                "fail_rate": fail_rate,
                "pass_rate": pass_rate,
                "unknown_rate": unknown_rate,
                "sources": "calibration",
            }

            # Update or create rule (versioning, not append)
            success = rule_engine.update_rule(lesson_type, evidence)
            if not success:
                return None

            rule = rule_engine.get_rule(lesson_type)
            if rule:
                return {
                    "principle": rule.get("principle", ""),
                    "domain": rule.get("domain", f"{lesson_type}_processing"),
                    "confidence": rule.get("confidence", 0.5),
                    "derived_from_count": total,
                    "version": rule.get("version", 1),
                    "rule_condition": rule.get("rule_condition", ""),
                    "rule_action": rule.get("rule_action", ""),
                    "fallback_chain": rule.get("fallback_chain", "[]"),
                }
        except Exception as e:
            logger.error(f"V41 principle rule error: {e}", exc_info=True)

        return None

    def get_active_principles(self) -> list[dict]:
        """Lấy all principles."""
        return db_query_all("SELECT * FROM meta_principles ORDER BY confidence DESC")

    def get_stats(self) -> dict:
        try:
            total = db_query_one("SELECT COUNT(*) as cnt FROM meta_principles")["cnt"]
            avg_conf = db_query_one("SELECT AVG(confidence) as avg FROM meta_principles")["avg"] or 0
            return {"total_principles": total, "avg_confidence": round(avg_conf, 2)}
        except Exception as exc:
            logger.warning("meta: principles stats query failed, reporting zeros: %s", exc, exc_info=True)
            return {"total_principles": 0, "avg_confidence": 0}


# ============================================================
# 5. IDENTITY — Mission, boundaries, values
# ============================================================
class Identity:
    """
    Identity — Định nghĩa SCP là ai, phục vụ ai, giới hạn gì.

    Trả lời câu hỏi:
      - Mission: SCP phục vụ mục đích gì?
      - Boundary: SCP làm gì, KHÔNG làm gì?
      - Values: SCP ưu tiên gì khi xung đột?
      - Forbidden: SCP tuyệt đối không làm gì?
    """

    def get(self, key: str) -> str:
        row = db_query_one("SELECT value FROM meta_identity WHERE key = ?", (key,))
        return row["value"] if row else ""

    def set(self, key: str, value: str):
        ts = datetime.now().astimezone().isoformat()
        db_exec("""
            INSERT OR REPLACE INTO meta_identity (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, ts))

    def get_mission(self) -> str:
        return self.get("mission")

    def get_boundaries(self) -> str:
        return self.get("boundary")

    def get_values(self) -> list[str]:
        rows = db_query_all("SELECT value FROM meta_identity WHERE key LIKE 'value_%' ORDER BY key")
        return [r["value"] for r in rows]

    def get_forbidden(self) -> list[str]:
        rows = db_query_all("SELECT value FROM meta_identity WHERE key LIKE 'forbidden_%' ORDER BY key")
        return [r["value"] for r in rows]

    def get_long_term_goal(self) -> str:
        return self.get("long_term_goal")

    def get_all(self) -> dict:
        rows = db_query_all("SELECT key, value FROM meta_identity")
        return {r["key"]: r["value"] for r in rows}


__all__ = ["WorldModel", "AbstractionEngine", "Identity"]
