"""
[OPT-21] InboxWatcher — Knowledge Intake, Claim Extraction, and Epistemic Verification.
========================================================================================
Theo dõi và xử lý tri thức mới rơi vào data/inbox_knowledge/:
  1. Trích xuất claims (ClaimExtractor) từ nội dung tri thức.
  2. Kiểm tra kháng thể theo domain (DomainAntibodySystem) để phát hiện ảo giác/mâu thuẫn.
  3. Ghi nhận bền vững vào LearningDB (open_questions, missing_pieces, hypotheses)
     để áp đặt ranh giới an toàn ở tầng Database, không chỉ ở RAM/Biến tạm.
  4. Quản lý vòng đời file (chuyển sang processed/ hoặc quarantine/).
  5. Tuân thủ Fail-Closed & Zero-Trust:
     - Tệp rỗng hoặc 0 claims không được phép đánh dấu ACCEPTED vào kho tri thức.
     - Tự động bỏ qua tệp ẩn (.gitkeep, .gitignore...) để tránh làm hỏng repo.
     - Khả năng phục hồi và chống xung đột tên/khóa tệp trên Windows.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scp.contracts.time import now_utc_iso
from scp.knowledge.antibody_system import AntibodyResult, DomainAntibodySystem
from scp.knowledge.claim_extractor import Claim, ClaimExtractor
from scp.knowledge.learning_db import LearningDB

logger = logging.getLogger("scp.knowledge.inbox_watcher")


def read_file_safe(file_path: Path) -> str:
    """Đọc tệp văn bản với cơ chế thử đa bảng mã để chống lỗi UnicodeDecodeError."""
    raw = file_path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def safe_move_file(src: Path, dst: Path, max_retries: int = 3, delay: float = 0.05) -> Path:
    """Di chuyển tệp an toàn với retry backoff chống file locking trên Windows."""
    # Nếu tệp đích đã tồn tại, sinh tên duy nhất có nano/uuid để không bao giờ bị đè/xung đột
    if dst.exists():
        unique_suffix = f"_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        dst = dst.parent / f"{dst.stem}{unique_suffix}{dst.suffix}"

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            shutil.move(str(src), str(dst))
            return dst
        except (PermissionError, OSError) as err:
            last_err = err
            time.sleep(delay * (attempt + 1))

    # Fallback cuối cùng nếu shutil.move vẫn bị chặn: copy + unlink
    try:
        shutil.copy2(str(src), str(dst))
        src.unlink(missing_ok=True)
        return dst
    except Exception:
        if last_err:
            raise last_err
        raise


@dataclass
class InboxItemResult:
    """Kết quả xử lý một đơn vị nội dung trong file inbox."""
    item_id: str
    domain: str
    status: str  # ACCEPTED | REJECTED | CORRUPTED | NO_CLAIMS
    claims: list[Claim] = field(default_factory=list)
    antibody_results: list[AntibodyResult] = field(default_factory=list)
    failed_antibodies: list[AntibodyResult] = field(default_factory=list)
    open_question_id: str | None = None
    missing_piece_id: str | None = None
    hypothesis_id: str | None = None
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "domain": self.domain,
            "status": self.status,
            "claims_count": len(self.claims),
            "claims": [c.to_dict() for c in self.claims],
            "failed_antibodies": [a.to_dict() for a in self.failed_antibodies],
            "open_question_id": self.open_question_id,
            "missing_piece_id": self.missing_piece_id,
            "hypothesis_id": self.hypothesis_id,
            "details": self.details,
        }


@dataclass
class InboxFileResult:
    """Kết quả xử lý một file trong inbox_knowledge."""
    file_path: Path
    status: str  # ACCEPTED | REJECTED | CORRUPTED | NO_CLAIMS
    items: list[InboxItemResult] = field(default_factory=list)
    moved_to: Path | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": str(self.file_path),
            "status": self.status,
            "items_count": len(self.items),
            "items": [it.to_dict() for it in self.items],
            "moved_to": str(self.moved_to) if self.moved_to else None,
            "error": self.error,
        }


class InboxWatcher:
    """Dịch vụ theo dõi inbox_knowledge và xác thực tri thức đầu vào.
    
    Tích hợp:
      - ClaimExtractor: Trích xuất các khẳng định số liệu, liên kết, thực thể, URL, DOI.
      - DomainAntibodySystem: Quét mâu thuẫn, quá liều y tế, tỷ lệ tài chính vô lý, ngụy biện...
      - LearningDB: Ghi nhận vi phạm vào open_questions/missing_pieces và tri thức sạch vào hypotheses.
    """

    def __init__(
        self,
        inbox_dir: Path | str | None = None,
        db_path: Path | str | None = None,
        learning_db: LearningDB | None = None,
        archive_dir: Path | str | None = None,
        quarantine_dir: Path | str | None = None,
        claim_extractor: ClaimExtractor | None = None,
        antibody_system: DomainAntibodySystem | None = None,
        archive: bool = True,
    ) -> None:
        # Đường dẫn thư mục inbox
        if inbox_dir is not None:
            self.inbox_dir = Path(inbox_dir).resolve()
        else:
            self.inbox_dir = Path(__file__).resolve().parents[2] / "data" / "inbox_knowledge"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        # Thư mục lưu trữ file hoàn thành / cách ly
        self.archive_dir = Path(archive_dir).resolve() if archive_dir else self.inbox_dir / "processed"
        self.quarantine_dir = Path(quarantine_dir).resolve() if quarantine_dir else self.inbox_dir / "quarantine"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        # LearningDB
        if learning_db is not None:
            self.learning_db = learning_db
        else:
            actual_db_path = Path(db_path).resolve() if db_path else Path(__file__).resolve().parents[2] / "data" / "cognitive" / "learning.sqlite"
            self.learning_db = LearningDB(actual_db_path)

        # Các thành phần cốt lõi
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.antibody_system = antibody_system or DomainAntibodySystem()
        self.archive = archive

        self._running = False
        self._stop_requested = False

    def process_content(
        self,
        text: str,
        domain: str = "general",
        question: str = "",
        item_id: str = "",
        ground_truth: dict[str, Any] | None = None,
        file_ref: str = "",
    ) -> InboxItemResult:
        """Xử lý một đoạn văn bản tri thức: Extract Claims -> Run Antibodies -> Persist LearningDB."""
        if not item_id:
            item_id = f"item_{uuid.uuid4().hex[:8]}"

        domain = (domain or "general").strip().lower()

        # 1. Trích xuất claims
        claims = self.claim_extractor.extract(answer=text, question=question)

        # 2. Kiểm tra kháng thể domain
        ab_results = self.antibody_system.check(
            question=question,
            answer=text,
            domain=domain,
            ground_truth=ground_truth,
        )

        failed_antibodies = [r for r in ab_results if not r.passed]

        # 3. Phán quyết Zero-Trust & Fail-Closed khi có vi phạm kháng thể
        if failed_antibodies:
            first_fail = failed_antibodies[0]
            oq_id = f"oq_{uuid.uuid4().hex[:12]}"
            mp_id = f"mp_{uuid.uuid4().hex[:12]}"
            now_iso = now_utc_iso()

            # Ghi vào open_questions bao gồm toàn bộ chi tiết kháng thể vi phạm
            oq_data = {
                "question_id": oq_id,
                "title": f"Kháng thể cảnh báo: {first_fail.antibody_name} trong domain '{domain}'",
                "question": f"Tri thức inbox '{item_id}' bị từ chối bởi {len(failed_antibodies)} kháng thể: {first_fail.details}",
                "scope_json": json.dumps({"item_id": item_id, "domain": domain, "file": file_ref}),
                "trigger": "CONTRADICTION",
                "related_claim_refs_json": json.dumps([c.claim_id for c in claims]),
                "related_knowledge_refs_json": json.dumps([item_id]),
                "known_evidence_refs_json": json.dumps([a.antibody_name for a in failed_antibodies]),
                "needed_observations_json": json.dumps([a.details for a in failed_antibodies]),
                "needed_capabilities_json": json.dumps(["antibody_reconciliation", "ground_truth_verification"]),
                "status": "OPEN",
                "created_at": now_iso,
            }
            self.learning_db.execute_insert("open_questions", oq_data)

            # Ghi vào missing_pieces
            all_ab_names = ", ".join(a.antibody_name for a in failed_antibodies)
            mp_data = {
                "missing_piece_id": mp_id,
                "question_id": oq_id,
                "kind": "MISSING_EVIDENCE",
                "description": f"Thiếu bằng chứng đối chứng để giải quyết vi phạm từ kháng thể ({all_ab_names})",
                "blocks_claims_json": json.dumps([c.claim_id for c in claims]),
                "blocks_decisions_json": json.dumps(["knowledge_ingestion"]),
                "needed_evidence_json": json.dumps([f"Ground truth verification for {item_id}"]),
                "discovered_by": "inbox_watcher",
                "created_at": now_iso,
            }
            self.learning_db.execute_insert("missing_pieces", mp_data)

            logger.warning(
                "[InboxWatcher] Tri thức '%s' bị từ chối do kháng thể '%s'. Đã tạo open_question: %s",
                item_id, first_fail.antibody_name, oq_id,
            )

            return InboxItemResult(
                item_id=item_id,
                domain=domain,
                status="REJECTED",
                claims=claims,
                antibody_results=ab_results,
                failed_antibodies=failed_antibodies,
                open_question_id=oq_id,
                missing_piece_id=mp_id,
                details=f"Từ chối do {len(failed_antibodies)} kháng thể không vượt qua ({first_fail.antibody_name}: {first_fail.details})",
            )

        # 4. Khi toàn bộ kháng thể đều PASS nhưng KHÔNG có claims nào được trích xuất
        if not claims:
            logger.info(
                "[InboxWatcher] Tri thức '%s' không chứa khẳng định thực tế nào (0 claims). Ghi nhận NO_CLAIMS.",
                item_id,
            )
            return InboxItemResult(
                item_id=item_id,
                domain=domain,
                status="NO_CLAIMS",
                claims=[],
                antibody_results=ab_results,
                failed_antibodies=[],
                details="Không trích xuất được claim nào từ nội dung tri thức.",
            )

        # 5. Khi toàn bộ kháng thể đều PASS và CÓ claims hợp lệ -> Ghi nhận hypotheses
        hyp_id = f"hyp_{uuid.uuid4().hex[:12]}"
        hyp_data = {
            "hypothesis_id": hyp_id,
            "question_ref": item_id,
            "hypothesis": text[:250],
            "mechanism": f"Trích xuất {len(claims)} claims thuộc domain '{domain}', vượt qua toàn bộ kiểm tra kháng thể.",
            "predictions_json": json.dumps([c.to_dict() for c in claims[:10]]),
            "assumptions_json": json.dumps(["antibody_inspected", "zero_trust_validated"]),
            "needed_capabilities_json": json.dumps([]),
            "status": "SUPPORTED",
            "created_at": now_utc_iso(),
        }
        self.learning_db.execute_insert("hypotheses", hyp_data)

        return InboxItemResult(
            item_id=item_id,
            domain=domain,
            status="ACCEPTED",
            claims=claims,
            antibody_results=ab_results,
            failed_antibodies=[],
            hypothesis_id=hyp_id,
            details=f"Chấp thuận: {len(claims)} claims trích xuất, 0 kháng thể vi phạm.",
        )

    def _parse_file_payload(self, file_path: Path) -> list[dict[str, Any]]:
        """Đọc và giải mã nội dung từ file json, txt hoặc md với bảo vệ đa bảng mã."""
        suffix = file_path.suffix.lower()
        content = read_file_safe(file_path).strip()
        if not content:
            return []

        if suffix == ".json":
            data = json.loads(content)
            if isinstance(data, list):
                if not data:
                    return []
                return [dict(item) if isinstance(item, dict) else {"text": str(item)} for item in data]
            if isinstance(data, dict):
                # Hỗ trợ cấu trúc {"items": [...]} hoặc {"documents": [...]}
                if "items" in data and isinstance(data["items"], list):
                    return [dict(x) if isinstance(x, dict) else {"text": str(x)} for x in data["items"]]
                if "documents" in data and isinstance(data["documents"], list):
                    return [dict(x) if isinstance(x, dict) else {"text": str(x)} for x in data["documents"]]
                return [data]
            return [{"text": str(data)}]
        else:
            # TXT hoặc MD: 1 tài liệu toàn văn
            return [{
                "text": content,
                "domain": "general",
                "item_id": file_path.stem,
            }]

    def process_file(self, file_path: Path | str) -> InboxFileResult:
        """Xử lý trọn gói 1 file trong inbox và di chuyển file theo kết quả Fail-Closed."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return InboxFileResult(
                file_path=p,
                status="CORRUPTED",
                error=f"File không tồn tại: {p}",
            )

        logger.info("[InboxWatcher] Đang xử lý file: %s", p.name)

        # 1. Parse payload từ file
        try:
            payloads = self._parse_file_payload(p)
        except Exception as exc:
            logger.error("[InboxWatcher] Không thể giải mã file %s: %s", p.name, exc)
            oq_id = f"oq_{uuid.uuid4().hex[:12]}"
            try:
                self.learning_db.execute_insert("open_questions", {
                    "question_id": oq_id,
                    "title": f"Lỗi giải mã file inbox: {p.name}",
                    "question": f"File {p.name} không thể parse cú pháp: {exc}",
                    "scope_json": json.dumps({"file": p.name}),
                    "trigger": "UNKNOWN",
                    "related_claim_refs_json": "[]",
                    "related_knowledge_refs_json": "[]",
                    "known_evidence_refs_json": "[]",
                    "needed_observations_json": json.dumps([str(exc)]),
                    "needed_capabilities_json": json.dumps(["file_format_repair"]),
                    "status": "OPEN",
                    "created_at": now_utc_iso(),
                })
            except Exception as db_exc:
                logger.warning("Không thể ghi log lỗi vào LearningDB: %s", db_exc)

            dest: Path | None = None
            if self.archive:
                dest = safe_move_file(p, self.quarantine_dir / p.name)

            return InboxFileResult(
                file_path=p,
                status="CORRUPTED",
                moved_to=dest,
                error=str(exc),
            )

        # 2. Fail-Closed nếu file rỗng hoặc không có items
        if not payloads:
            logger.warning("[InboxWatcher] File %s rỗng hoặc không chứa dữ liệu.", p.name)
            oq_id = f"oq_{uuid.uuid4().hex[:12]}"
            try:
                self.learning_db.execute_insert("open_questions", {
                    "question_id": oq_id,
                    "title": f"Tệp rỗng hoặc không có tri thức: {p.name}",
                    "question": f"Tệp {p.name} không chứa payload dữ liệu nào để phân tích.",
                    "scope_json": json.dumps({"file": p.name}),
                    "trigger": "EMPTY_FILE",
                    "related_claim_refs_json": "[]",
                    "related_knowledge_refs_json": "[]",
                    "known_evidence_refs_json": "[]",
                    "needed_observations_json": json.dumps(["File payload is empty"]),
                    "needed_capabilities_json": json.dumps(["data_source_verification"]),
                    "status": "OPEN",
                    "created_at": now_utc_iso(),
                })
            except Exception as db_exc:
                logger.warning("Không thể ghi log file rỗng vào LearningDB: %s", db_exc)

            dest = None
            if self.archive:
                dest = safe_move_file(p, self.quarantine_dir / p.name)

            return InboxFileResult(
                file_path=p,
                status="CORRUPTED",
                moved_to=dest,
                error="Tệp rỗng hoặc không chứa dữ liệu tri thức hợp lệ.",
            )

        # 3. Duyệt từng item với lớp bảo vệ ngoại lệ riêng biệt
        item_results: list[InboxItemResult] = []
        has_rejection = False
        has_corrupted = False
        has_accepted = False

        for idx, item_data in enumerate(payloads):
            text = (
                item_data.get("text")
                or item_data.get("content")
                or item_data.get("answer")
                or item_data.get("body")
                or ""
            )
            question = item_data.get("question", "")
            domain = item_data.get("domain", "general")
            item_id = item_data.get("item_id") or item_data.get("id") or f"{p.stem}_{idx}"
            ground_truth = item_data.get("ground_truth")

            try:
                res = self.process_content(
                    text=text,
                    domain=domain,
                    question=question,
                    item_id=item_id,
                    ground_truth=ground_truth,
                    file_ref=p.name,
                )
            except Exception as item_exc:
                logger.error("[InboxWatcher] Lỗi khi xử lý item %s trong %s: %s", item_id, p.name, item_exc)
                res = InboxItemResult(
                    item_id=item_id,
                    domain=domain,
                    status="CORRUPTED",
                    details=f"Lỗi ngoại lệ trong quá trình xử lý: {item_exc}",
                )
                has_corrupted = True

            item_results.append(res)
            if res.status == "REJECTED":
                has_rejection = True
            elif res.status == "CORRUPTED":
                has_corrupted = True
            elif res.status == "ACCEPTED":
                has_accepted = True

        # 4. Xác định trạng thái tổng thể của file (Fail-Closed)
        if has_rejection or has_corrupted:
            overall_status = "REJECTED"
            target_dir = self.quarantine_dir
        elif not has_accepted:
            # Toàn bộ item đều là NO_CLAIMS -> Không chấp nhận vào processed/
            overall_status = "NO_CLAIMS"
            target_dir = self.quarantine_dir
            # Ghi nhận open_question về việc file không sinh ra được tri thức nào
            try:
                oq_id = f"oq_{uuid.uuid4().hex[:12]}"
                self.learning_db.execute_insert("open_questions", {
                    "question_id": oq_id,
                    "title": f"Tệp không chứa khẳng định thực tế: {p.name}",
                    "question": f"Tệp {p.name} có {len(item_results)} mục nhưng không trích xuất được claim nào.",
                    "scope_json": json.dumps({"file": p.name}),
                    "trigger": "NO_CLAIMS",
                    "related_claim_refs_json": "[]",
                    "related_knowledge_refs_json": json.dumps([it.item_id for it in item_results]),
                    "known_evidence_refs_json": "[]",
                    "needed_observations_json": json.dumps(["No claims extracted from items"]),
                    "needed_capabilities_json": json.dumps(["claim_extraction_enhancement"]),
                    "status": "OPEN",
                    "created_at": now_utc_iso(),
                })
            except Exception as oq_err:
                logger.debug("Không thể ghi open_question cho NO_CLAIMS: %s", oq_err)
        else:
            # Có ít nhất 1 item ACCEPTED và không có item nào REJECTED/CORRUPTED
            overall_status = "ACCEPTED"
            target_dir = self.archive_dir

        dest_path: Path | None = None
        if self.archive:
            dest_path = safe_move_file(p, target_dir / p.name)

        return InboxFileResult(
            file_path=p,
            status=overall_status,
            items=item_results,
            moved_to=dest_path,
        )

    def scan_once(self) -> list[InboxFileResult]:
        """Quét 1 lượt thư mục inbox và xử lý toàn bộ file chưa được chuyển."""
        results: list[InboxFileResult] = []
        if not self.inbox_dir.exists():
            return results

        # Bỏ qua các tệp ẩn (.gitkeep, .gitignore...) và tệp tạm
        for item in sorted(self.inbox_dir.iterdir()):
            if not item.is_file():
                continue
            name = item.name
            if name.startswith(".") or item.suffix.lower() in {".tmp", ".part", ".crdownload", ".swp", ".bak"}:
                continue

            res = self.process_file(item)
            results.append(res)
        return results

    def process_inbox(self) -> list[InboxFileResult]:
        """Bí danh cho scan_once."""
        return self.scan_once()

    def process_directory(self, dir_path: Path | str | None = None) -> list[InboxFileResult]:
        """Xử lý toàn bộ file trong thư mục inbox (mặc định self.inbox_dir hoặc dir_path)."""
        if dir_path is not None:
            old_dir = self.inbox_dir
            old_archive = self.archive_dir
            old_quarantine = self.quarantine_dir
            new_dir = Path(dir_path).resolve()
            self.inbox_dir = new_dir
            if old_archive == old_dir / "processed":
                self.archive_dir = new_dir / "processed"
                self.archive_dir.mkdir(parents=True, exist_ok=True)
            if old_quarantine == old_dir / "quarantine":
                self.quarantine_dir = new_dir / "quarantine"
                self.quarantine_dir.mkdir(parents=True, exist_ok=True)
            try:
                return self.scan_once()
            finally:
                self.inbox_dir = old_dir
                self.archive_dir = old_archive
                self.quarantine_dir = old_quarantine
        return self.scan_once()

    def watch(self, poll_interval: float = 1.0, max_cycles: int | None = None) -> list[InboxFileResult]:
        """Vòng lặp giám sát liên tục thư mục inbox."""
        self._running = True
        self._stop_requested = False
        all_results: list[InboxFileResult] = []
        cycles = 0

        logger.info("[InboxWatcher] Bắt đầu theo dõi inbox tại %s", self.inbox_dir)

        try:
            while not self._stop_requested:
                cycle_results = self.scan_once()
                all_results.extend(cycle_results)
                cycles += 1

                if max_cycles is not None and cycles >= max_cycles:
                    break

                time.sleep(poll_interval)
        finally:
            self._running = False

        return all_results

    def stop(self) -> None:
        """Yêu cầu dừng vòng lặp watch."""
        self._stop_requested = True
