"""
[OPT-22] Admin Knowledge & Commercial Data Source Management Routes.
====================================================================
Quản trị tri thức vùng cách ly, xem xét bài học vi phạm kháng thể và cấu hình
các nguồn dữ liệu thương mại SCP:
  - GET  /api/scp/v3/knowledge/review       -> Danh sách tệp cách ly & bài học vi phạm
  - POST /api/scp/v3/knowledge/review/action -> APPROVE / REJECT một tri thức
  - GET  /api/scp/v3/config/sources          -> 17 nguồn dữ liệu thương mại kèm masked_key
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from scp.api._shared import verify_admin
from scp.data_sources.config_loader import (
    COMMERCIAL_SOURCE_KEYS,
    get_api_key,
    get_default_loader,
    has_api_key,
    mask_secret,
)
from scp.knowledge.inbox_watcher import safe_move_file

logger = logging.getLogger("scp.api.admin_knowledge_routes")

router = APIRouter(tags=["admin_knowledge"])

# 17 nguồn dữ liệu thương mại chuẩn hoá
COMMERCIAL_SOURCES_DEFINITIONS: list[dict[str, str]] = [
    {"source": "agriculture", "env_var": "USDA_API_KEY", "description": "USDA NASS Agriculture QuickStats"},
    {"source": "alphavantage", "env_var": "ALPHAVANTAGE_API_KEY", "description": "AlphaVantage Financial Market Data"},
    {"source": "biology", "env_var": "NCBI_API_KEY", "description": "NCBI E-utilities Taxonomy & Biology"},
    {"source": "courtlistener", "env_var": "COURTLISTENER_TOKEN", "description": "CourtListener Legal Opinions & Cases"},
    {"source": "cybersecurity", "env_var": "NVD_API_KEY", "description": "NVD National Vulnerability Database"},
    {"source": "energy", "env_var": "EIA_API_KEY", "description": "EIA U.S. Energy Information Administration"},
    {"source": "eric", "env_var": "ERIC_API_KEY", "description": "ERIC Education Research Information Center"},
    {"source": "fred", "env_var": "FRED_API_KEY", "description": "Federal Reserve Economic Data (FRED)"},
    {"source": "google_factcheck", "env_var": "GOOGLE_FACT_CHECK_API_KEY", "description": "Google Fact Check Tools API"},
    {"source": "legal", "env_var": "CASE_LAW_API_KEY", "description": "Case.law Caselaw Access Project"},
    {"source": "medical", "env_var": "PUBMED_API_KEY", "description": "PubMed Medical Citations & Research"},
    {"source": "newsapi", "env_var": "NEWSAPI_API_KEY", "description": "NewsAPI Headlines and News Search"},
    {"source": "noaa", "env_var": "NOAA_API_KEY", "description": "NOAA Climate & Weather Data Service"},
    {"source": "wikiart", "env_var": "WIKIART_API_KEY", "description": "WikiArt Visual Arts Encyclopedia"},
    {"source": "usda", "env_var": "USDA_API_KEY", "description": "USDA Department of Agriculture Direct API"},
    {"source": "ncbi", "env_var": "NCBI_API_KEY", "description": "NCBI National Center for Biotechnology Information"},
    {"source": "pubmed", "env_var": "PUBMED_API_KEY", "description": "PubMed Direct Search API"},
]


def _get_quarantine_dir() -> Path:
    override = os.environ.get("SCP_QUARANTINE_DIR")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2] / "data" / "inbox_knowledge" / "quarantine"


def _get_archive_dir() -> Path:
    override = os.environ.get("SCP_PROCESSED_DIR")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2] / "data" / "inbox_knowledge" / "processed"


def _get_learning_db_path() -> Path:
    override = os.environ.get("SCP_LEARNING_DB_PATH")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2] / "data" / "cognitive" / "learning.sqlite"


class ReviewActionRequest(BaseModel):
    claim: str = Field(..., description="ID hoặc nội dung tri thức / câu hỏi cần duyệt")
    action: str = Field(..., description="Hành động: APPROVE hoặc REJECT")


@router.get("/api/scp/v3/knowledge/review", dependencies=[Depends(verify_admin)])
@router.get("/api/v3/knowledge/review", dependencies=[Depends(verify_admin)])
@router.get("/v3/knowledge/review", dependencies=[Depends(verify_admin)])
async def get_knowledge_review():
    """Liệt kê các tri thức trong vùng cách ly (quarantine) hoặc các bài học từ learning.sqlite kèm vi phạm kháng thể."""
    quarantine_dir = _get_quarantine_dir()
    quarantined_files: list[dict[str, Any]] = []

    if quarantine_dir.exists():
        for f in sorted(quarantine_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                stat = f.stat()
                file_info: dict[str, Any] = {
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                }
                # Thử đọc một đoạn preview nội dung an toàn
                try:
                    full_content = f.read_text(encoding="utf-8", errors="replace")
                    file_info["preview"] = full_content[:500]
                    if f.suffix.lower() == ".json":
                        try:
                            file_info["data_preview"] = json.loads(full_content)
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            file_info["preview_error"] = str(exc)
                            logger.debug("[admin_knowledge_routes] json decode error: %s", exc)
                except (OSError, UnicodeDecodeError, ValueError) as read_err:
                    file_info["preview_error"] = str(read_err)
                quarantined_files.append(file_info)

    # Đọc từ learning.sqlite với conn.close() fail-closed trên Windows
    db_path = _get_learning_db_path()
    open_questions: list[dict[str, Any]] = []
    antibody_violations: list[dict[str, Any]] = []

    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM open_questions WHERE status IN ('OPEN', 'REJECTED') "
                "ORDER BY created_at DESC LIMIT 100"
            )
            rows = cursor.fetchall()
            for r in rows:
                item = dict(r)
                open_questions.append(item)
                if item.get("trigger") == "CONTRADICTION" or "Kháng thể" in str(item.get("title", "")):
                    antibody_violations.append(item)
        except (sqlite3.Error, OSError, ValueError) as db_err:
            logger.warning("[admin_knowledge_routes] Lỗi khi đọc LearningDB: %s", db_err)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error as close_err:
                    logger.debug("[admin_knowledge_routes] SQLite close error: %s", close_err, exc_info=True)

    return {
        "status": "success",
        "quarantined_files": quarantined_files,
        "open_questions": open_questions,
        "antibody_violations": antibody_violations,
        "total_quarantined": len(quarantined_files),
        "total_open_questions": len(open_questions),
        "total_antibody_violations": len(antibody_violations),
    }


@router.post("/api/scp/v3/knowledge/review/action", dependencies=[Depends(verify_admin)])
@router.post("/api/v3/knowledge/review/action", dependencies=[Depends(verify_admin)])
@router.post("/v3/knowledge/review/action", dependencies=[Depends(verify_admin)])
async def review_knowledge_action(req: ReviewActionRequest):
    """Nhận body {'claim': '...', 'action': 'APPROVE' | 'REJECT'} để cập nhật trạng thái."""
    action_upper = req.action.strip().upper()
    if action_upper not in ("APPROVE", "REJECT"):
        raise HTTPException(
            status_code=400,
            detail=f"Hành động không hợp lệ: '{req.action}'. Chỉ chấp nhận 'APPROVE' hoặc 'REJECT'.",
        )

    claim_target = req.claim.strip()
    if not claim_target:
        raise HTTPException(status_code=400, detail="Trường 'claim' không được để trống.")

    new_status = "APPROVED" if action_upper == "APPROVE" else "REJECTED"
    updated_records = 0
    db_path = _get_learning_db_path()

    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Cập nhật trong open_questions: ID chính xác, title/question chính xác, hoặc claim ref trong json
            cursor.execute(
                "UPDATE open_questions SET status = ? WHERE question_id = ? "
                "OR title = ? OR question = ? OR related_claim_refs_json LIKE ?",
                (
                    new_status,
                    claim_target,
                    claim_target,
                    claim_target,
                    f'%"{claim_target}"%',
                ),
            )
            updated_records += cursor.rowcount

            # Cập nhật trong hypotheses nếu có (chính xác ID hoặc exact match)
            cursor.execute(
                "UPDATE hypotheses SET status = ? WHERE hypothesis_id = ? "
                "OR question_ref = ? OR hypothesis = ?",
                (
                    new_status,
                    claim_target,
                    claim_target,
                    claim_target,
                ),
            )
            updated_records += cursor.rowcount
            conn.commit()
        except (sqlite3.Error, OSError) as db_err:
            logger.error("[admin_knowledge_routes] Lỗi cập nhật LearningDB: %s", db_err)
            raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {db_err}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error as close_err:
                    logger.debug("[admin_knowledge_routes] SQLite close error: %s", close_err, exc_info=True)

    # Nếu action là APPROVE và có file cách ly tương ứng -> Di chuyển sang processed/ an toàn
    file_action = "none"
    quarantine_dir = _get_quarantine_dir()
    archive_dir = _get_archive_dir()
    if quarantine_dir.exists():
        for target_file in list(quarantine_dir.iterdir()):
            if target_file.is_file() and (target_file.name == claim_target or target_file.stem == claim_target):
                if action_upper == "APPROVE":
                    try:
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        dest = archive_dir / target_file.name
                        final_dest = safe_move_file(target_file, dest)
                        file_action = f"moved_to_processed: {final_dest.name}"
                    except (OSError, shutil.Error) as move_err:
                        logger.error("[admin_knowledge_routes] Lỗi di chuyển tệp %s: %s", target_file.name, move_err)
                        file_action = f"move_failed: {move_err}"
                else:
                    file_action = "kept_in_quarantine"

    return {
        "status": "success",
        "action": action_upper,
        "claim": claim_target,
        "new_status": new_status,
        "updated_records": updated_records,
        "file_action": file_action,
        "message": f"Đã thực hiện {action_upper} cho '{claim_target}'. (Cập nhật {updated_records} bản ghi)",
    }


@router.get("/api/scp/v3/config/sources", dependencies=[Depends(verify_admin)])
@router.get("/api/v3/config/sources", dependencies=[Depends(verify_admin)])
@router.get("/v3/config/sources", dependencies=[Depends(verify_admin)])
async def get_config_sources():
    """Liệt kê 17 nguồn dữ liệu thương mại, trạng thái (Enabled/Disabled) kèm key đã che giấu (mask_secret)."""
    loader = get_default_loader()
    sources_info: list[dict[str, Any]] = []
    enabled_count = 0

    for item in COMMERCIAL_SOURCES_DEFINITIONS:
        source_name = item["source"]
        env_var = item["env_var"]
        desc = item["description"]

        is_enabled = loader.has_api_key(source_name)
        raw_key = loader.get_api_key(source_name, required=False)
        masked_val = mask_secret(raw_key) if is_enabled else "<empty>"
        status_label = "Enabled" if is_enabled else "Disabled"

        if is_enabled:
            enabled_count += 1

        sources_info.append({
            "source": source_name,
            "env_var": env_var,
            "status": status_label,
            "enabled": is_enabled,
            "masked_key": masked_val,
            "description": desc,
        })

    return {
        "sources": sources_info,
        "total": len(sources_info),
        "count": len(sources_info),
        "enabled_count": enabled_count,
        "disabled_count": len(sources_info) - enabled_count,
    }
