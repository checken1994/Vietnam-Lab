# SCP CIRCUIT: M06 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M06-closure.json)
"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V14 PREDICTIVE — Dự đoán -> Chờ -> Kiểm chứng -> Tự học.

Luồng chính:
    Internet -> Crawl -> Tạo câu hỏi dự đoán -> Dự đoán (SLM) ->
    Lưu prediction (pending) -> Chờ đến check_date ->
    Kiểm chứng với reality -> Học từ lỗi -> Cải thiện

Khác V14 hiện tại:
    V14 hiện tại: verify NGAY (AI answer vs real data cùng lúc)
    V14 Predictive: DỰ ĐOÁN tương lai -> chờ -> verify SAU

Chạy:
    python v14_predictive.py                    # Chạy 1 cycle
    python v14_predictive.py --lien-tuc          # 24/7 liên tục
    python v14_predictive.py --verify-only       # Chỉ verify predictions cũ
    python v14_predictive.py --bao-cao           # Xem báo cáo học tập
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import logging

from scp.core.api_utils import fetch_with_retry
from scp.core.db_manager import db_exec, db_query_all, db_query_one, init_db

logger = logging.getLogger("scp.prediction")
# Removed circular import: SCPV14, RealityJudge  # was causing circular import

from scp.prediction.predictive_helpers import (
    _PREDICT_RNG,
    DataCrawler,
    Predictor,
    QuestionGenerator,
    Verifier,
    init_predictions_db,
)


# ============================================================
# SELF LEARNER — Học từ predictions sai
# ============================================================
class SelfLearner:
    """Tự học từ predictions sai — cập nhật classifier.

    [V104.35 #56 FIX] TẠI SAO: V104.34 marked this as TODO — local SCPV13() instance
    was GC'd, production judge kept frozen weights forever → "self-correcting" was a lie.

    Fix: SelfLearner now accepts an optional `judge` reference. If provided, retrains
    judge.v13.classifier (the PRODUCTION classifier). If not provided, falls back to
    a local SCPV13() instance (legacy behavior, persists TRAINING_DATA class attr
    so next boot picks it up — better than nothing but not real self-correction).

    Callers should pass the production judge:
        learner = SelfLearner(judge=get_judge())
        learner.learn_from_errors()
    """

    def __init__(self, judge=None):
        # [V104.35 #56] Production judge reference (was: None → trained throwaway instance)
        self._judge = judge

    def _get_engine(self):
        """Get the classifier engine to retrain.

        Priority:
          1. Production judge's v13 (self._judge.v13) — REAL self-correction
          2. RealityClassifier-backed local engine — fallback (persists
             TRAINING_DATA class attr so next boot picks it up)

        [M6-FIX contract] The fallback used to instantiate ``SCPV13()``
        (aliased to the ``SCPV14`` shim), which has NO ``.classifier``
        attribute — ``learn_from_errors`` then died on the first
        ``engine.classifier.FRAMES`` access and the generic ``except``
        swallowed it: the "self-correcting" learn phase was a silent no-op.
        The trainable shape contract (``.classifier`` exposing
        ``FRAMES``/``TRAINING_DATA``/``vectorizer``/``partial_fit``) lives in
        ``scp.core.reality_engine.RealityClassifier`` — back the fallback with
        it so learning actually retrains. A production engine is only accepted
        when it really exposes the trainable shape.
        """
        # [V104.35 #56] Try production judge first
        if self._judge is not None:
            judge_engine = getattr(self._judge, 'v13', None)
            judge_classifier = getattr(judge_engine, 'classifier', None)
            if judge_classifier is not None and hasattr(judge_classifier, 'TRAINING_DATA'):
                return judge_engine, True  # True = is_production
        # Fallback: local engine backed by the real trainable classifier
        from scp.core.reality_engine import RealityClassifier

        class _LegacyV13Engine:
            """Minimal engine shape contract: engine.classifier."""

            def __init__(self):
                self.classifier = RealityClassifier()

        return _LegacyV13Engine(), False

    def learn_from_errors(self):
        """Đọc tất cả verified_wrong predictions -> thêm vào training data."""
        wrong_preds = db_query_all(
            "SELECT * FROM predictions WHERE status = 'verified_wrong'"
        )

        if not wrong_preds:
            return {"learned": 0, "message": "Không có prediction sai để học"}

        # Group by domain
        by_domain = {}
        for pred in wrong_preds:
            domain = pred.get("domain", "unknown")
            by_domain[domain] = by_domain.get(domain, 0) + 1

        # Thêm vào V13 classifier training data
        try:
            # [V104.35 #56] Use production judge's v13 if available (was: local SCPV13() GC'd)
            engine, is_production = self._get_engine()
            added = 0
            for pred in wrong_preds:
                question = pred.get("question", "")
                domain = pred.get("domain", "")
                # Map domain -> V13 frame
                frame_map = {
                    "weather": "reality", "crypto": "reality",
                    "finance": "conversion", "astronomy": "reality",
                }
                frame = frame_map.get(domain, "reality")
                if frame in engine.classifier.FRAMES:
                    existing = {q for q, _ in engine.classifier.TRAINING_DATA}
                    if question not in existing:
                        engine.classifier.TRAINING_DATA.append((question, frame))
                        added += 1

            if added > 0:
                # Retrain
                engine.classifier.vectorizer.fit([q for q, _ in engine.classifier.TRAINING_DATA])
                from scp.core.reality_engine import MultiClassPerceptron
                engine.classifier.classifier = MultiClassPerceptron(
                    len(engine.classifier.vectorizer.vocabulary),
                    engine.classifier.FRAMES, 0.15
                )
                for _ in range(200):
                    for q, f in engine.classifier.TRAINING_DATA:
                        engine.classifier.classifier.partial_fit(
                            engine.classifier.vectorizer.transform(q), f
                        )
                prod_label = "PRODUCTION judge" if is_production else "local fallback (GC'd)"
                quality = "REAL self-correction" if is_production else "placeholder only"
                logger.info(
                    f"SelfLearner: retrained with {added} new samples "
                    f"({prod_label}) — {quality}"
                )
        except Exception as e:
            logger.error(f"SelfLearner error: {e}", exc_info=True)

        return {
            "learned": len(wrong_preds),
            "by_domain": by_domain,
            "added_to_training": added if 'added' in dir() else 0,
            "production_retrained": is_production if 'is_production' in dir() else False,
        }

    def get_stats(self) -> dict:
        """Thống kê học tập."""
        total = db_query_one("SELECT COUNT(*) as cnt FROM predictions")["cnt"]
        correct = db_query_one("SELECT COUNT(*) as cnt FROM predictions WHERE status='verified_correct'")["cnt"]
        wrong = db_query_one("SELECT COUNT(*) as cnt FROM predictions WHERE status='verified_wrong'")["cnt"]
        pending = db_query_one("SELECT COUNT(*) as cnt FROM predictions WHERE status='pending'")["cnt"]

        by_domain = {}
        for r in db_query_all("SELECT domain, status, COUNT(*) as cnt FROM predictions GROUP BY domain, status"):
            d = r["domain"]
            if d not in by_domain:
                by_domain[d] = {"correct": 0, "wrong": 0, "pending": 0}
            by_domain[d][r["status"].replace("verified_", "")] = r["cnt"]

        error_types = {}
        for r in db_query_all("SELECT error_type, COUNT(*) as cnt FROM predictions WHERE error_type IS NOT NULL AND error_type != '' GROUP BY error_type"):
            error_types[r["error_type"]] = r["cnt"]

        accuracy = (correct / (correct + wrong) * 100) if (correct + wrong) > 0 else 0

        return {
            "total_predictions": total,
            "correct": correct,
            "wrong": wrong,
            "pending": pending,
            "accuracy": round(accuracy, 1),
            "by_domain": by_domain,
            "error_types": error_types,
        }


# ============================================================
# PREDICTIVE ORCHESTRATOR — Điều phối toàn bộ
# ============================================================
class PredictiveOrchestrator:
    """
    Điều phối: Crawl -> Generate -> Predict -> Verify -> Learn.

    Luồng:
        1. Crawl data từ Internet (crypto, weather, fx, NASA)
        2. Generate câu hỏi dự đoán từ crawled data
        3. Save predictions (pending) vào SQLite
        4. Verify pending predictions (nếu đến check_date)
        5. Learn từ predictions sai
    """

    def __init__(self, judge=None):
        init_db()
        init_predictions_db()
        self.crawler = DataCrawler()
        self.generator = QuestionGenerator()
        self.predictor = Predictor()
        self.verifier = Verifier()
        # [V104.35 #56] Pass production judge to SelfLearner (was: None → frozen weights)
        self.learner = SelfLearner(judge=judge)
        self.cycle_count = 0

    def run_cycle(self) -> dict:
        """Chạy 1 cycle: Crawl -> Generate -> Predict -> Verify -> Learn."""
        self.cycle_count += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"  [LOOP] PREDICTIVE CYCLE {self.cycle_count} — {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"{'='*60}")

        # Step 1: Crawl
        logger.info("\n  ? STEP 1: Crawl dữ liệu từ Internet")
        data = self.crawler.crawl_all()

        # Step 2: Generate questions
        logger.info("\n  ? STEP 2: Tạo câu hỏi dự đoán")
        questions = self.generator.generate(data)
        logger.info(f"     Sinh {len(questions)} câu hỏi dự đoán")

        # Step 3: Save predictions
        logger.info("\n  ? STEP 3: Lưu predictions vào SQLite")
        for q in questions:
            pred_id = self.predictor.save_prediction(
                question=q["question"],
                ai_answer=q["ai_answer"],
                domain=q["domain"],
                check_date=q["check_date"],
                source=q["source"],
                entity=q["entity"],
                current_value=q.get("current_value"),
                confidence=0.5,
            )
            logger.info(f"     [{q['domain']:10s}] {q['question'][:55]}")
            logger.info(f"       -> Predicted: {q['ai_answer'][:50]}")
            logger.info(f"       -> Check date: {q['check_date']} | ID: {pred_id}")

        # Step 4: Verify pending
        logger.info("\n  [OK] STEP 4: Kiểm chứng predictions đã đến hạn")
        verified = self.verifier.verify_pending()
        if verified:
            correct = sum(1 for v in verified if v["status"] == "verified_correct")
            wrong = sum(1 for v in verified if v["status"] == "verified_wrong")
            logger.info(f"     Verified: {len(verified)} ({correct} correct, {wrong} wrong)")
        else:
            logger.info("     Không có prediction nào đến hạn kiểm chứng")

        # Step 5: Learn
        logger.error("\n  [BRAIN] STEP 5: Tự học từ lỗi")
        learn_result = self.learner.learn_from_errors()
        learn_msg = learn_result.get('message', f"Học từ {learn_result.get('learned', 0)} predictions sai")
        logger.info(f"     {learn_msg}")

        # Report
        stats = self.learner.get_stats()
        logger.info("\n  [STATS] BÁO CÁO:")
        logger.info(f"     Total predictions: {stats['total_predictions']}")
        logger.info(f"     Correct: {stats['correct']} | Wrong: {stats['wrong']} | Pending: {stats['pending']}")
        logger.info(f"     Accuracy: {stats['accuracy']}%")
        if stats["error_types"]:
            logger.error(f"     Error types: {stats['error_types']}")
        logger.info(f"\n{'='*60}")

        return stats


# ============================================================
#  AUTO-VERIFY SCHEDULER — verifies pending predictions
# ============================================================
class PredictionScheduler:
    """
    Auto-verify pending predictions when check_date arrives.

    Usage:
        scheduler = PredictionScheduler(judge=judge)
        result = scheduler.verify_pending()  # Run periodically
    """

    def __init__(self, judge=None):
        self.predictor = Predictor()
        self.verifier = Verifier()
        self.judge = judge

    def verify_pending(self, limit: int = 20) -> dict[str, Any]:
        """Verify pending predictions."""
        try:
            pending = self.predictor.get_pending_predictions()
            if not pending:
                return {"verified": 0, "correct": 0, "wrong": 0, "errors": 0}

            verified = 0
            correct = 0
            wrong = 0
            errors = 0

            for pred in pending[:limit]:
                try:
                    pred_dict = dict(pred) if hasattr(pred, 'keys') else pred
                    pred_id = pred_dict.get("id")
                    question = pred_dict.get("question", "")
                    pred_dict.get("domain", "")
                    predicted_answer = pred_dict.get("predicted_answer", "")

                    # Re-judge with same question
                    if self.judge:
                        v = self.judge.judge(question, predicted_answer or "")
                        actual = v.reality_check.get("real_value")
                        actual_str = str(actual) if actual is not None else v.final_answer
                    else:
                        actual = self.verifier._fetch_actual(pred_dict)
                        actual_str = str(actual) if actual is not None else None

                    if actual_str is None:
                        continue

                    # Compare
                    is_correct, error_type, error_reason = self._compare_prediction(pred_dict, actual_str)
                    status = "verified_correct" if is_correct else "verified_wrong"

                    self.predictor.update_prediction(
                        pred_id, status, actual_str, error_type, error_reason
                    )

                    verified += 1
                    if is_correct:
                        correct += 1
                    else:
                        wrong += 1

                except Exception as e:
                    errors += 1
                    logger.warning(f"Prediction verify error: {e}", exc_info=True)

            return {
                "verified": verified,
                "correct": correct,
                "wrong": wrong,
                "errors": errors,
                "accuracy": round(correct / max(1, verified), 3),
            }
        except Exception as e:
            logger.warning(f"Prediction verify cycle failed: {e}", exc_info=True)
            return {"error": str(e), "verified": 0}

    def _compare_prediction(self, pred: dict, actual: str) -> tuple[bool, str, str]:
        """Compare prediction với actual."""
        try:
            predicted = pred.get("predicted_answer", "")
            import re
            pred_nums = re.findall(r'-?\d+\.?\d*', str(predicted))
            actual_nums = re.findall(r'-?\d+\.?\d*', str(actual))

            if pred_nums and actual_nums:
                pred_val = float(pred_nums[-1])
                actual_val = float(actual_nums[-1])
                domain = pred.get("domain", "")
                if domain == "weather":
                    is_correct = abs(pred_val - actual_val) <= 5.0
                else:
                    tolerance = 0.10 if domain in ("finance", "conversion") else 0.05
                    is_correct = abs(pred_val - actual_val) / max(abs(actual_val), 1e-300) <= tolerance
                if is_correct:
                    return (True, "", "Within tolerance")
                else:
                    return (False, "value_mismatch",
                            f"Predicted {pred_val}, actual {actual_val}")
            return (False, "no_numbers", "Cannot extract numbers")
        except Exception as e:
            logger.warning(f"Prediction compare error: {e}", exc_info=True)
            return (False, "compare_error", str(e))


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="SCP V14 Predictive — Dự đoán -> Kiểm chứng -> Tự học")
    parser.add_argument("--lien-tuc", action="store_true", help="Chạy 24/7 liên tục")
    parser.add_argument("--verify-only", action="store_true", help="Chỉ verify predictions cũ")
    parser.add_argument("--bao-cao", action="store_true", help="Xem báo cáo học tập")
    parser.add_argument("--nghi", type=int, default=0, help="Giây nghỉ giữa cycle (mặc định 0 = liên tục)")
    args = parser.parse_args()

    orch = PredictiveOrchestrator()

    if args.bao_cao:
        stats = orch.learner.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
        return

    if args.verify_only:
        print("  [OK] Verifying pending predictions...")
        verified = orch.verifier.verify_pending()
        print(f"  Verified: {len(verified)}")
        return

    if args.lien_tuc:
        # Chạy 24/7
        running = [True]

        def signal_handler(sig, frame):
            print("\n  Đang dừng...")
            running[0] = False

        signal.signal(signal.SIGINT, signal_handler)

        while running[0]:
            orch.run_cycle()
            if running[0] and args.nghi > 0:
                print(f"\n  Nghỉ {args.nghi}s...")
                for _ in range(args.nghi):
                    if not running[0]:
                        break
                    time.sleep(1)

        print(f"\n  Đã dừng. Tổng: {orch.cycle_count} cycles.")
    else:
        # Chạy 1 cycle
        orch.run_cycle()


__all__ = [
    "_PREDICT_RNG",
    "DataCrawler",
    "Predictor",
    "QuestionGenerator",
    "Verifier",
    "init_predictions_db",
    "SelfLearner",
    "PredictiveOrchestrator",
    "PredictionScheduler",
    "main",
    "fetch_with_retry",
    "db_exec",
    "db_query_all",
    "db_query_one",
    "init_db",
]

