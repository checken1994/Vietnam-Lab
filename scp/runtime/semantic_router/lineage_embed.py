"""GĐ2 Semantic Router — Lineage 1 adapter (embedding) — Lane B.

Tham chiếu: claude-review-gd2-spec.md §3.1 (hạ tầng thực tế), §6 Lane B
("Không tải mạng lúc chạy; thiếu model thì ra DEGRADED"), NV1 ag-tasks-4.md
(owner decision C1-i, ràng buộc nguyên văn: "cho phép, nhưng không ảnh hưởng
tới PC của tôi") và arbitration.py của Lane A (Protocol `L1Verdict`).

Cam kết owner được máy hoá trong file này:
- Model ONNX int8 đa ngôn ngữ nằm trong CACHE DIR NGOÀI REPO
  (env ``SCP_GD2_MODEL_CACHE``; mặc định ``~/.cache/scp-gd2-models``).
- KHÔNG TẢI MẠNG LÚC CHẠY — chỉ đọc file có sẵn do
  ``tools/gd2_provision_model.py`` tải MỘT LẦN lúc setup.
- ``sha256`` + ``size_bytes`` của model phải khớp ``config/gd2_models.lock.json``
  TRƯỚC KHI NẠP; sai/thiếu/lỗi → ``L1EmbedVerdict(degraded=True)``, KHÔNG raise
  (lineage chết không được kéo sập router).
- Lazy-load: KHÔNG import ``onnxruntime``/``tokenizers``/``numpy`` ở mức module
  top — process không bật L1 không chịu chi phí nạp thư viện.
- Inference CPU: session options ``intra_op_num_threads=1``,
  ``inter_op_num_threads=1``, sequential, arena tắt (RAM cap); batch LUÔN 1
  (một câu hỏi mỗi lần gọi).
- Giải phóng session qua :meth:`LineageEmbed.close` (idempotent) + ``atexit``.

Trường model chưa provision (repo vừa clone): lock là TEMPLATE placeholder
``config/gd2_models.lock.json`` → mọi ``classify()`` trả DEGRADED — đúng hành
vi "thiếu model → DEGRADED", không crash, không fallback mở.

GIỚI HẠN ĐÃ BIẾT (không che giấu):
- Temperature softmax mặc định 1.0 CHƯA calibrate — review §3.3(4) yêu cầu fit
  temperature trên held-out + báo ECE; việc đó thuộc Lane D (eval) / E1, không
  thuộc adapter này.
- Centroid (4 lớp) không do Lane B sinh (chống phrase-listing trong không gian
  vector, review §2e): adapter chỉ NẠP ``centroids.npy`` +
  ``centroids.labels.json`` từ cache dir — do Lane D/E huấn luyện từ seed có
  provenance. Thiếu centroid → DEGRADED.
- Test happy-path trong repo dùng session GIẢ (FA-04): không claim gì về độ
  chính xác của model thật cho tới khi eval Lane D chạy với model đã provision.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Hằng số công khai — 4 lớp của L1 (khớp bảng ánh xạ nhãn của Lane A:
# arbitration.map_label_to_lane: SEC/CHAT/FACT ánh xạ lane, REAS → None).
# ---------------------------------------------------------------------------
CLASS_SECURITY: str = "SEC"
CLASS_CHATBOT: str = "CHAT"
CLASS_FACTUAL: str = "FACT"
CLASS_REASONING: str = "REAS"
CLASSES: tuple[str, ...] = (CLASS_SECURITY, CLASS_CHATBOT, CLASS_FACTUAL, CLASS_REASONING)

#: Env trỏ cache dir NGOÀI REPO (owner C1-i). Thiết lập bởi provision tool.
MODEL_CACHE_ENV: str = "SCP_GD2_MODEL_CACHE"
#: Env chọn model id (đệm lên ``active`` của lock file).
MODEL_ID_ENV: str = "SCP_GD2_L1_MODEL"

#: Owner ràng buộc "không ảnh hưởng tới PC" (NV1): model vendored ≤ 150MB.
MAX_MODEL_BYTES: int = 150 * 1024 * 1024

#: Cache dir mặc định khi env không đặt — luôn NGOÀI repo (trong home user).
DEFAULT_CACHE_DIR: Path = Path.home() / ".cache" / "scp-gd2-models"

#: Repo root: .../scp/runtime/semantic_router/lineage_embed.py → parents[3].
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_LOCK_PATH: Path = REPO_ROOT / "config" / "gd2_models.lock.json"

#: Tên file trong <cache>/<model_id>/ — cố định hoá để lock + tool + adapter
#: cùng nói về một bố cục.
MODEL_FILENAME: str = "model.onnx"
TOKENIZER_FILENAME: str = "tokenizer.json"
CENTROIDS_FILENAME: str = "centroids.npy"
CENTROID_LABELS_FILENAME: str = "centroids.labels.json"

#: Softmax temperature mặc định (chưa calibrate — xem module docstring).
DEFAULT_TEMPERATURE: float = 1.0
#: Số token tối đa mỗi câu (truncate ở tokenizer — chống phình RAM).
MAX_INPUT_TOKENS: int = 512

_SHA256_HEX_LEN: int = 64

#: [L1-MODEL 2026-10-31] Mọi artifact cạnh model PHẢI có sha256 trong lock
#: (``models.<id>.artifacts_sha256``). Trước đây chỉ model.onnx được hash ⇒
#: tokenizer/centroid bị tráo vẫn được nạp (fail-open). Thiếu/lệch ⇒ DEGRADED.
LOCK_ARTIFACTS: tuple[str, ...] = (
    TOKENIZER_FILENAME,
    CENTROIDS_FILENAME,
    CENTROID_LABELS_FILENAME,
)
#: Nhãn khi L1 sống nhưng KHÔNG đủ chắc để bỏ phiếu (dưới ``vote_floor`` /
#: ``min_cosine`` đã calibrate trong lock). Nhãn này không ánh xạ được sang lane
#: nào trong arbitration ⇒ đường đi giống hệt khi L1 chết (không mở thêm gì).
ABSTAIN_LABEL: str = "ABSTAIN"
#: Input ONNX mà adapter biết cấp dữ liệu (BERT-style). Input lạ ⇒ DEGRADED.
_SUPPORTED_INPUTS: tuple[str, ...] = ("input_ids", "attention_mask", "token_type_ids")


class _LoadError(Exception):
    """Lỗi nạp nội bộ — LUÔN bị classify() nuốt và đổi thành DEGRADED."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Verdict — thoả mãn Protocol L1Verdict của Lane A (label + confidence) BÊN CẠNH
# API mà ask Lane B yêu cầu: labels: dict[4 lớp] + degraded: bool.
#
# Review §L1 (LOW): "frozen dataclass chứa dict vẫn mutable" → KHÔNG lưu dict
# trực tiếp; lưu tuple-of-pairs và trả BẢN SAO dict qua property.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class L1EmbedVerdict:
    """Kết quả classify của Lineage 1 (embedding).

    - ``labels`` (property): dict copy {SEC, CHAT, FACT, REAS} → xác suất
      softmax. Khi degraded: 4 khoá đều 0.0 (không có thông tin — không bỏ phiếu).
    - ``label`` (property): khoá argmax, hoặc ``"DEGRADED"`` khi degraded
      (nhãn này KHÔNG ánh xạ được sang lane nào trong arbitration — lineage
      hỏng không được bỏ phiếu).
    - ``confidence`` (property): max(labels) clamp [0,1]; degraded → 0.0.
    """

    pairs: tuple[tuple[str, float], ...] = ()
    degraded: bool = False
    reason: str = ""
    model_id: str = ""
    abstained: bool = False

    @staticmethod
    def degraded_verdict(reason: str, model_id: str = "") -> "L1EmbedVerdict":
        return L1EmbedVerdict(
            pairs=tuple((c, 0.0) for c in CLASSES),
            degraded=True,
            reason=reason,
            model_id=model_id,
        )

    @property
    def labels(self) -> dict[str, float]:
        return dict(self.pairs)

    @property
    def label(self) -> str:
        if self.degraded or not self.pairs:
            return "DEGRADED"
        if self.abstained:
            return ABSTAIN_LABEL
        return max(self.pairs, key=lambda kv: kv[1])[0]

    @property
    def confidence(self) -> float:
        if self.degraded or not self.pairs:
            return 0.0
        return min(max(max(v for _, v in self.pairs), 0.0), 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": self.labels,
            "label": self.label,
            "confidence": self.confidence,
            "degraded": self.degraded,
            "abstained": self.abstained,
            "reason": self.reason,
            "model_id": self.model_id,
        }


# ---------------------------------------------------------------------------
# Helpers thuần (không I/O ngoài đối số truyền vào)
# ---------------------------------------------------------------------------
def resolve_cache_dir(env: dict[str, str] | None = None) -> Path:
    """Cache dir: env SCP_GD2_MODEL_CACHE thắng; mặc định home (ngoài repo)."""
    source = os.environ if env is None else env
    raw = str(source.get(MODEL_CACHE_ENV, "") or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CACHE_DIR


def _sha256_hex_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LEN
        and all(c in "0123456789abcdef" for c in value.lower())
    )


def _softmax(scores: dict[str, float], temperature: float) -> dict[str, float]:
    """Softmax ổn định (trừ max). T<=0 bị coi là 1.0 (chống chia 0)."""
    t = temperature if temperature and temperature > 0 else 1.0
    mx = max(scores.values())
    exps = {k: math.exp((v - mx) / t) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return {k: e / total for k, e in exps.items()}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
@dataclass
class _Runtime:
    """Artifact đã nạp (session/tokenizer/centroid). Trong RAM một chỗ duy nhất."""

    session: Any
    tokenizer: Any
    centroids: Any  # numpy ndarray (k, dim) — các row ĐÃ normalize (k >= 4)
    centroid_order: tuple[str, ...]  # nhãn theo thứ tự row của centroids
    input_name: str
    output_name: str
    input_prefix: str
    input_names: tuple[str, ...] = ()
    temperature: float = DEFAULT_TEMPERATURE
    vote_floor: tuple[tuple[str, float], ...] = ()
    min_cosine: tuple[tuple[str, float], ...] = ()
    disabled_votes: tuple[str, ...] = ()


class LineageEmbed:
    """Lineage 1 — classifier embedding ONNX int8, lazy, fail-closed.

    Vòng đời:
      idle --classify()--> load (verify lock+sha256, nạp session) --> ready
      load lỗi --> failed (classify trả DEGRADED, KHÔNG raise, KHÔNG tự retry)
      close() --> idle (thử nạp lại ở lần classify kế tiếp)

    Không tự retry khi failed: tránh hash lại đĩa mỗi request khi model
    thiếu/hỏng (owner: "không ảnh hưởng tới PC"). Muốn thử lại sau khi
    provision: ``close()`` hoặc tạo instance mới.
    """

    def __init__(
        self,
        cache_dir: str | os.PathLike[str] | None = None,
        lock_path: str | os.PathLike[str] | None = None,
        model_id: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self._cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._lock_path = Path(lock_path).expanduser() if lock_path else None
        self._model_id = model_id
        self._temperature = temperature
        self._runtime: _Runtime | None = None
        self._failed_reason: str = ""
        self._resolved_model_id: str = ""
        self._tokenizer_json: str | None = None
        self._max_tokens: int = MAX_INPUT_TOKENS
        self._atexit_registered = False
        # KHÔNG nạp gì ở đây — kể cả đọc lock file (lazy toàn phần).
        atexit.register(self.close)

    # -- trạng thái ---------------------------------------------------------
    @property
    def state(self) -> str:
        if self._runtime is not None:
            return "ready"
        return "failed" if self._failed_reason else "idle"

    def close(self) -> None:
        """Giải phóng session + mọi artifact. IDEMPOTENT — gọi nhiều lần OK.

        Sau close, classify() sẽ nạp lại từ đầu (state idle).
        """
        rt = self._runtime
        self._runtime = None
        self._failed_reason = ""
        if rt is not None:
            # onnxruntime session tự giải phóng khi GC; buộc tham chiếu tung
            # ngay để RAM (owner cap) trả về sớm thay vì chờ GC.
            rt.session = None
            rt.tokenizer = None

    # -- API chính ----------------------------------------------------------
    def classify(self, text: str) -> L1EmbedVerdict:
        """Phân loại 1 câu → L1EmbedVerdict. KHÔNG BAO GIỜ raise.

        batch=1: mỗi call xử lý đúng 1 chuỗi (owner ràng buộc CPU/PC).
        """
        if not isinstance(text, str):
            return L1EmbedVerdict.degraded_verdict("invalid_input_type")
        try:
            rt = self._ensure_runtime()
        except _LoadError as exc:
            self._failed_reason = exc.reason
            return L1EmbedVerdict.degraded_verdict(
                exc.reason, model_id=str(getattr(self, "_resolved_model_id", "") or "")
            )
        except Exception as exc:  # noqa: BLE001 - lỗi lạ vẫn KHÔNG raise (fail-closed)
            self._failed_reason = "load_error_unexpected"
            return L1EmbedVerdict.degraded_verdict(
                f"load_error_unexpected:{type(exc).__name__}"
            )
        try:
            scores = self._cosine_scores(rt, text)
            labels = _softmax(scores, rt.temperature)
            # Cổng bỏ phiếu đã calibrate (lock.calibration): nhãn thắng phải
            # đạt cả xác suất tối thiểu VÀ cosine thô tối thiểu; không đạt ⇒
            # ABSTAIN (không lane nào) — softmax là TƯƠNG ĐỐI, một mẩu text lạ
            # vẫn có thể "gần CHAT nhất" dù cosine thấp.
            top = max(CLASSES, key=lambda c: labels[c])
            floor = dict(rt.vote_floor).get(top, 0.0)
            min_cos = dict(rt.min_cosine).get(top)
            abstain = (
                top in rt.disabled_votes
                or labels[top] < floor
                or (min_cos is not None and scores[top] < min_cos)
            )
            return L1EmbedVerdict(
                pairs=tuple((c, float(labels[c])) for c in CLASSES),
                degraded=False,
                reason="abstain_below_calibrated_floor" if abstain else "ok",
                model_id=getattr(self, "_resolved_model_id", "") or "",
                abstained=abstain,
            )
        except _LoadError as exc:  # lỗi inference giữ nguyên fail-closed
            self._runtime = None
            self._failed_reason = exc.reason
            return L1EmbedVerdict.degraded_verdict(exc.reason)
        except Exception as exc:  # noqa: BLE001 - LUÔN nuốt, không kéo sập router
            self._runtime = None
            self._failed_reason = "inference_error"
            return L1EmbedVerdict.degraded_verdict(f"inference_error:{type(exc).__name__}")

    def status(self) -> dict[str, str]:
        """Trạng thái THẬT cho /ready: thử nạp (verify lock + sha256 mọi
        artifact + session) nếu chưa nạp; KHÔNG raise, KHÔNG tải mạng.

        ``state``: ``ready`` | ``unavailable``; ``reason`` = lý do degraded.
        """
        try:
            self._ensure_runtime()
        except _LoadError as exc:
            self._failed_reason = exc.reason
            return {"state": "unavailable", "reason": exc.reason,
                    "model_id": str(self._resolved_model_id or "")}
        except Exception as exc:  # noqa: BLE001 — fail-closed, không kéo sập /ready
            self._failed_reason = "load_error_unexpected"
            return {"state": "unavailable", "reason": f"load_error_unexpected:{type(exc).__name__}",
                    "model_id": str(self._resolved_model_id or "")}
        return {"state": "ready", "reason": "ok", "model_id": str(self._resolved_model_id or "")}

    # -- nạp (lazy) ----------------------------------------------------------
    def _ensure_runtime(self) -> _Runtime:
        if self._runtime is not None:
            return self._runtime
        if self._failed_reason:
            raise _LoadError(self._failed_reason)
        rt = self._load()
        self._runtime = rt
        return rt

    def _load(self) -> _Runtime:  # noqa: PLR0911, PLR0912 - mỗi nhánh = 1 reason
        lock = self._read_lock()
        model_id = self._resolve_model_id(lock)
        self._resolved_model_id = model_id
        entry = self._lock_entry(lock, model_id)
        sha_expected = entry.get("sha256")
        if not _is_sha256_hex(sha_expected):
            # Bắt cả TEMPLATE placeholder ("PLACEHOLDER_64_HEX") ở đây.
            raise _LoadError("lock_bad_sha256", str(sha_expected))
        size_expected = entry.get("size_bytes")
        if not isinstance(size_expected, int) or isinstance(size_expected, bool) or size_expected <= 0:
            raise _LoadError("lock_bad_size", repr(size_expected))
        cache = self._cache_dir or resolve_cache_dir()
        model_path = cache / model_id / MODEL_FILENAME
        if not model_path.is_file():
            raise _LoadError("model_file_missing", str(model_path))
        # Đọc 1 lần, hash đúng byte sắp nạp → không có cửa sổ TOCTOU giữa
        # verify và load (session tạo từ chính buffer này).
        model_bytes = model_path.read_bytes()
        if len(model_bytes) != size_expected:
            raise _LoadError(
                "size_mismatch", f"lock={size_expected} actual={len(model_bytes)}"
            )
        if len(model_bytes) > MAX_MODEL_BYTES:
            raise _LoadError(
                "model_exceeds_owner_cap", f"{len(model_bytes)} > {MAX_MODEL_BYTES}"
            )
        sha_actual = _sha256_hex_of(model_bytes)
        if sha_actual != str(sha_expected).lower():
            raise _LoadError(
                "sha256_mismatch", f"lock={sha_expected} actual={sha_actual}"
            )
        tok_path = cache / model_id / TOKENIZER_FILENAME
        if not tok_path.is_file():
            raise _LoadError("tokenizer_file_missing", str(tok_path))
        np_path = cache / model_id / CENTROIDS_FILENAME
        labels_path = cache / model_id / CENTROID_LABELS_FILENAME
        if not np_path.is_file() or not labels_path.is_file():
            raise _LoadError("centroids_missing", str(np_path))
        # [L1-MODEL] Đọc MỘT lần + hash đúng byte sắp dùng (không TOCTOU).
        artifacts = self._verified_artifacts(entry, cache / model_id)
        temperature, vote_floor, min_cosine = self._parse_calibration(entry)
        disabled_votes = self._parse_disabled_votes(entry)
        centroids, centroid_order = self._load_centroids(
            cache / model_id,
            npy_bytes=artifacts[CENTROIDS_FILENAME],
            labels_bytes=artifacts[CENTROID_LABELS_FILENAME],
        )
        self._tokenizer_json = artifacts[TOKENIZER_FILENAME].decode("utf-8", errors="strict")
        self._max_tokens = self._parse_max_tokens(entry)
        try:
            session, tokenizer = self._load_runtime(model_bytes)
        finally:
            self._tokenizer_json = None
        try:
            inputs = [str(i.name) for i in session.get_inputs()]
            output_name = session.get_outputs()[-1].name
        except Exception as exc:  # noqa: BLE001
            raise _LoadError("session_io_error", repr(exc)) from exc
        unsupported = [n for n in inputs if n not in _SUPPORTED_INPUTS]
        if not inputs or unsupported or "input_ids" not in inputs:
            raise _LoadError("session_input_unsupported", ",".join(unsupported or inputs))
        return _Runtime(
            session=session,
            tokenizer=tokenizer,
            centroids=centroids,
            centroid_order=centroid_order,
            input_name=inputs[0],
            output_name=str(output_name),
            input_prefix=str(entry.get("input_prefix", "") or ""),
            input_names=tuple(inputs),
            temperature=temperature,
            vote_floor=vote_floor,
            min_cosine=min_cosine,
            disabled_votes=disabled_votes,
        )

    @staticmethod
    def _verified_artifacts(entry: dict[str, Any], model_dir: Path) -> dict[str, bytes]:
        """Đọc + kiểm sha256 của MỌI artifact trong ``LOCK_ARTIFACTS``.

        Lock thiếu ``artifacts_sha256`` / hash không hợp lệ / byte lệch ⇒
        ``_LoadError`` ⇒ DEGRADED. Trả bytes đã verify để bước nạp dùng đúng
        các byte đó (không đọc đĩa lần hai).
        """
        expected = entry.get("artifacts_sha256")
        if not isinstance(expected, dict):
            raise _LoadError("lock_missing_artifact_hashes")
        out: dict[str, bytes] = {}
        for name in LOCK_ARTIFACTS:
            want = expected.get(name)
            if not _is_sha256_hex(want):
                raise _LoadError("lock_bad_artifact_sha256", name)
            try:
                data = (model_dir / name).read_bytes()
            except OSError as exc:
                raise _LoadError("artifact_missing", name) from exc
            got = _sha256_hex_of(data)
            if got != str(want).lower():
                raise _LoadError("artifact_sha256_mismatch", f"{name}: lock={want} actual={got}")
            out[name] = data
        return out

    @staticmethod
    def _parse_disabled_votes(entry: dict[str, Any]) -> tuple[str, ...]:
        """``calibration.disabled_votes``: lớp mà L1 KHÔNG được bỏ phiếu (luôn
        ABSTAIN khi lớp đó thắng). Dùng khi độ chính xác của lớp chưa được
        chứng minh — vd SEC: một phiếu SEC của L1 một mình đủ rẽ SECURITY
        (arbitration Rule 1) nên phiếu sai = câu lành bị chặn."""
        cal = entry.get("calibration")
        raw = cal.get("disabled_votes", []) if isinstance(cal, dict) else []
        if not isinstance(raw, list) or any(str(x) not in CLASSES for x in raw):
            raise _LoadError("calibration_invalid", "disabled_votes")
        return tuple(sorted({str(x) for x in raw}))

    @staticmethod
    def _parse_max_tokens(entry: dict[str, Any]) -> int:
        """``max_input_tokens`` (lock) — độ dài cắt dùng lúc dựng prototype và
        calibrate; lệch độ dài cắt ⇒ embedding câu dài khác lúc calibrate."""
        raw = entry.get("max_input_tokens", MAX_INPUT_TOKENS)
        if isinstance(raw, bool) or not isinstance(raw, int) or not (1 <= raw <= MAX_INPUT_TOKENS):
            raise _LoadError("lock_bad_max_input_tokens", repr(raw))
        return raw

    def _parse_calibration(
        self, entry: dict[str, Any]
    ) -> tuple[float, tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]]:
        """``calibration`` trong lock (fit trên tập DEV, ngoài repo).

        Không có ⇒ hành vi cũ (temperature của constructor, không cổng phiếu).
        Có nhưng sai kiểu/miền ⇒ DEGRADED (không đoán giá trị thay thế).
        """
        cal = entry.get("calibration")
        if cal is None:
            return self._temperature, (), ()
        if not isinstance(cal, dict):
            raise _LoadError("calibration_invalid", "not an object")

        def _num(v: object) -> float | None:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            f = float(v)
            return f if math.isfinite(f) else None

        t = _num(cal.get("temperature"))
        if t is None or not (0.0 < t <= 1.0):
            raise _LoadError("calibration_invalid", "temperature")

        def _per_class(key: str, lo: float, hi: float) -> tuple[tuple[str, float], ...]:
            raw = cal.get(key, {})
            if not isinstance(raw, dict):
                raise _LoadError("calibration_invalid", key)
            pairs = []
            for cls, val in raw.items():
                f = _num(val)
                if str(cls) not in CLASSES or f is None or not (lo <= f <= hi):
                    raise _LoadError("calibration_invalid", f"{key}.{cls}")
                pairs.append((str(cls), f))
            return tuple(sorted(pairs))

        return t, _per_class("vote_floor", 0.0, 1.0), _per_class("min_cosine", -1.0, 1.0)

    # -- các bước nạp nhỏ (tách để test mock từng bước không kéo cả onnxruntime)
    def _resolve_model_id(self, lock: dict[str, Any] | None = None) -> str:
        mid = self._model_id or os.environ.get(MODEL_ID_ENV, "") or ""
        mid = str(mid).strip()
        if mid:
            return mid
        active = str((lock or self._read_lock()).get("active", "") or "").strip()
        if active:
            return active
        raise _LoadError("model_id_unset", f"env {MODEL_ID_ENV} và lock.active đều trống")

    def _read_lock(self) -> dict[str, Any]:
        path = self._lock_path or DEFAULT_LOCK_PATH
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise _LoadError("lock_missing", str(path)) from exc
        except (OSError, ValueError) as exc:
            raise _LoadError("lock_invalid_json", f"{path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise _LoadError("lock_invalid_json", "root không phải object")
        return raw

    @staticmethod
    def _lock_entry(lock: dict[str, Any], model_id: str) -> dict[str, Any]:
        models = lock.get("models")
        if not isinstance(models, dict) or model_id not in models:
            raise _LoadError("model_not_in_lock", model_id)
        entry = models[model_id]
        if not isinstance(entry, dict):
            raise _LoadError("lock_entry_invalid", model_id)
        return entry

    def _load_centroids(
        self,
        model_dir: Path,
        npy_bytes: bytes | None = None,
        labels_bytes: bytes | None = None,
    ) -> tuple[Any, tuple[str, ...]]:
        """Nạp prototype (centroids.npy + centroids.labels.json) — numpy LAZY.

        Hỗ trợ k >= 4 row (nhiều prototype mỗi lớp; điểm lớp = cosine MAX
        trên các row của lớp đó — với k == 4 trùng hành vi centroid cũ).
        ``labels`` là list dài k (nhãn của từng row), phải phủ đủ 4 lớp.
        Khi caller đưa bytes đã verify sha256 thì KHÔNG đọc đĩa lại.
        """
        np_path = model_dir / CENTROIDS_FILENAME
        labels_path = model_dir / CENTROID_LABELS_FILENAME
        if npy_bytes is None or labels_bytes is None:
            if not np_path.is_file() or not labels_path.is_file():
                raise _LoadError("centroids_missing", str(np_path))
        try:
            import numpy as np  # lazy — process không bật L1 không nạp numpy
        except ImportError as exc:
            raise _LoadError("numpy_not_installed") from exc
        try:
            import io

            arr = np.load(
                io.BytesIO(npy_bytes) if npy_bytes is not None else np_path,
                allow_pickle=False,
            )
            labels = json.loads(
                labels_bytes.decode("utf-8") if labels_bytes is not None
                else labels_path.read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            raise _LoadError("centroids_invalid", repr(exc)) from exc
        if not isinstance(labels, list):
            raise _LoadError("centroid_labels_invalid", str(type(labels)))
        order = tuple(str(x).upper() for x in labels)
        if set(order) != set(CLASSES):
            raise _LoadError("centroid_labels_invalid", str(labels)[:200])
        arr = np.asarray(arr, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[0] != len(order):
            raise _LoadError("centroids_invalid_shape", str(arr.shape))
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        if not np.all(np.isfinite(arr)) or float(norms.min()) <= 0.0:
            raise _LoadError("centroids_invalid_values")
        return arr / norms, order  # row-normalized → dot = cosine

    def _load_runtime(self, model_bytes: bytes) -> tuple[Any, Any]:
        """Nạp onnxruntime session + tokenizer — CỤM IMPORT NẶNG DUY NHẤT.

        Tách hàm để (a) không import ở module top, (b) test hermetic monkeypatch
        đúng chỗ này mà vẫn chạy thật toàn bộ cổng verify lock/sha256 phía trên.
        """
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise _LoadError("onnxruntime_not_installed") from exc
        try:
            import tokenizers
        except ImportError as exc:
            raise _LoadError("tokenizers_not_installed") from exc
        try:
            so = ort.SessionOptions()
            # Owner: CPU threads=1 — không giành core với pipeline khác.
            so.intra_op_num_threads = 1
            so.inter_op_num_threads = 1
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            # Owner RAM cap: tắt mem arena (đổi arena lớn lấy RAM ở multi-thread).
            so.enable_cpu_mem_arena = False
            session = ort.InferenceSession(
                model_bytes,
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )
            tok_json = getattr(self, "_tokenizer_json", None)
            if tok_json is not None:
                # [L1-MODEL] dùng đúng bytes đã verify sha256 (không đọc lại).
                tokenizer = tokenizers.Tokenizer.from_str(tok_json)
            else:
                tokenizer = tokenizers.Tokenizer.from_file(
                    str((self._cache_dir or resolve_cache_dir())
                        / self._resolved_model_id / TOKENIZER_FILENAME)
                )
            tokenizer.enable_truncation(
                max_length=int(getattr(self, "_max_tokens", MAX_INPUT_TOKENS) or MAX_INPUT_TOKENS)
            )
        except _LoadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _LoadError("session_error", repr(exc)) from exc
        return session, tokenizer

    # -- inference -----------------------------------------------------------
    def _cosine_scores(self, rt: _Runtime, text: str) -> dict[str, float]:
        """Embed 1 câu (batch=1) → cosine tới 4 centroid → dict điểm."""
        import numpy as np  # lazy

        enc = rt.tokenizer.encode(rt.input_prefix + text)
        ids = list(getattr(enc, "ids", []) or [])
        if not ids:
            raise _LoadError("tokenizer_empty_ids")
        mask = list(getattr(enc, "attention_mask", None) or [1] * len(ids))
        type_ids = list(getattr(enc, "type_ids", None) or [0] * len(ids))
        # [L1-MODEL] Cấp ĐỦ input mà session khai báo (BERT: ids + mask +
        # token_type_ids). Trước đây chỉ cấp input đầu tiên ⇒ model thật
        # 3-input luôn lỗi "missing input" ⇒ L1 không bao giờ sống.
        by_name = {"input_ids": ids, "attention_mask": mask, "token_type_ids": type_ids}
        names = rt.input_names or (rt.input_name,)
        feeds = {n: np.asarray([by_name[n]], dtype=np.int64) for n in names}
        out = rt.session.run([rt.output_name], feeds)[0]
        emb = np.asarray(out, dtype=np.float64)
        if emb.ndim == 3:  # (1, seq, dim) → mean pooling theo attention mask
            m = np.asarray(mask, dtype=np.float64)[:, None]
            summed = (emb[0] * m).sum(axis=0)
            denom = float(m.sum()) or 1.0
            emb = summed / denom
        elif emb.ndim == 2:
            emb = emb[0]
        else:
            raise _LoadError("embedding_shape_unexpected", str(emb.shape))
        dim = emb.shape[-1] if emb.ndim == 1 else -1
        if dim != rt.centroids.shape[1]:
            raise _LoadError(
                "embedding_dim_mismatch", f"emb={dim} centroid={rt.centroids.shape[1]}"
            )
        norm = float(np.linalg.norm(emb))
        if not math.isfinite(norm) or norm <= 0.0:
            return {c: 0.0 for c in CLASSES}
        cos = (rt.centroids @ (emb / norm)).astype(np.float64)
        # Thứ tự row của centroids = rt.centroid_order (đã validate phủ đủ
        # CLASSES lúc nạp). Điểm lớp = cosine MAX trên các prototype của lớp.
        return {
            cls: float(max(cos[i] for i, lab in enumerate(rt.centroid_order) if lab == cls))
            for cls in CLASSES
        }


# ---------------------------------------------------------------------------
# Singleton + API mức module (ask: classify(text) -> L1Verdict)
# ---------------------------------------------------------------------------
_default: LineageEmbed | None = None


def get_default() -> LineageEmbed:
    """Instance mặc định đọc env/lock chuẩn — tái dùng across calls."""
    global _default
    if _default is None:
        _default = LineageEmbed()
    return _default


def reset_default() -> None:
    """reset singleton (test/ops muốn nạp lại sau khi provision)."""
    global _default
    if _default is not None:
        _default.close()
    _default = None


def classify(text: str) -> L1EmbedVerdict:
    """API mức module — luôn trả verdict, không bao giờ raise."""
    return get_default().classify(text)


def readiness() -> dict[str, str]:
    """[L1-MODEL] Trạng thái L1 thật cho /ready (xem ``LineageEmbed.status``)."""
    try:
        return get_default().status()
    except Exception as exc:  # noqa: BLE001
        return {"state": "unavailable", "reason": f"status_error:{type(exc).__name__}", "model_id": ""}


__all__ = [
    "ABSTAIN_LABEL",
    "CLASSES",
    "CLASS_CHATBOT",
    "CLASS_FACTUAL",
    "CLASS_REASONING",
    "CLASS_SECURITY",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_LOCK_PATH",
    "DEFAULT_TEMPERATURE",
    "L1EmbedVerdict",
    "LOCK_ARTIFACTS",
    "LineageEmbed",
    "MAX_INPUT_TOKENS",
    "MAX_MODEL_BYTES",
    "MODEL_CACHE_ENV",
    "MODEL_ID_ENV",
    "REPO_ROOT",
    "classify",
    "get_default",
    "readiness",
    "reset_default",
    "resolve_cache_dir",
]
