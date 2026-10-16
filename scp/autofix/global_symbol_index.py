"""Global AST Symbol Index for SCP AutoFix and Code Evolution.

[SCP-DNA #1, #19, #25] TẠI SAO module này tồn tại:
  Khi AutoFix gửi mã nguồn của một file bị lỗi cho LLM (ví dụ: scp/autofix/llm_fix_parts/_call_openrouter.py),
  linter/AST scanner báo lỗi F821 (PossiblyUndefinedName) cho các biến như `_validate_openrouter_base_url`
  hoặc `_SIMPLE_BUG_TYPES`.
  Tuy nhiên, các ký hiệu này KHÔNG hề vắng mặt trong hệ thống: chúng được inject động tại runtime
  qua `_wire_parts()` trong `scp/autofix/llm_fix.py`.
  Khi LLM đọc toàn bộ file `_call_openrouter.py`, nó không thấy định nghĩa nên ngộ nhận là mã lỗi/hàm rác
  và cố tình xóa bỏ hoặc tạo stub rỗng bypass cơ chế an toàn.

  Global AST Symbol Index lập chỉ mục toàn bộ ký hiệu (hàm, lớp, hằng số, biến toàn cục) trên toàn bộ
  codebase `scp/`. Khi quét hoặc sinh bản vá cho bất kỳ file nào, module này truy vết nguồn gốc xuất xứ
  (Symbol Provenance) và đưa vào prompt để chỉ dẫn LLM giữ nguyên các ranh giới bảo mật và hàm được inject.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scp.autofix.path_guard import sanitize_storage_path

logger = logging.getLogger("scp.autofix.global_symbol_index")

DEFAULT_INDEX_CACHE = "data/global_symbol_index.json"
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "data"}


@dataclass
class SymbolLocation:
    name: str
    file_path: str
    line: int
    kind: str  # function, async_function, class, constant, variable, injected
    signature: str | None = None
    docstring: str | None = None
    wiring_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GlobalSymbolIndex:
    """Incremental Global AST Symbol Index across the SCP codebase."""

    def __init__(self, root_dir: Path | str | None = None, cache_file: str = DEFAULT_INDEX_CACHE):
        self.root = Path(root_dir or Path(__file__).resolve().parent.parent.parent)
        self.cache_file = self.root / cache_file
        self._lock = threading.Lock()
        # symbol_name -> list of SymbolLocation
        self._symbols: dict[str, list[SymbolLocation]] = {}
        # file_path_rel -> sha256_prefix
        self._file_hashes: dict[str, str] = {}
        # Special dynamic wiring registry: target_rel_path -> {symbol_name: source_rel_path}
        self._dynamic_wiring: dict[str, dict[str, str]] = {}
        self._loaded = False

    def load_cache(self) -> bool:
        """Load index from cache file if available."""
        if not self.cache_file.exists():
            return False
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self._file_hashes = data.get("file_hashes", {})
            self._symbols = {}
            for name, locs in data.get("symbols", {}).items():
                self._symbols[name] = [
                    SymbolLocation(**loc) for loc in locs if isinstance(loc, dict)
                ]
            self._dynamic_wiring = data.get("dynamic_wiring", {})
            self._loaded = True
            return True
        except Exception as exc:
            logger.warning("[global_symbol_index] Failed to load cache (%s); will rebuild", exc)
            return False

    def save_cache(self) -> None:
        """Save index to cache file atomically."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            export_symbols: dict[str, list[dict[str, Any]]] = {
                name: [loc.to_dict() for loc in locs]
                for name, locs in self._symbols.items()
            }
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "file_hashes": self._file_hashes,
                "dynamic_wiring": self._dynamic_wiring,
                "symbols": export_symbols,
            }
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.cache_file)
        except Exception as exc:
            logger.debug("[global_symbol_index] Save cache failed (fail-open): %s", exc)

    def _hash_file(self, path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except Exception:
            return ""

    def scan_file(self, file_path: Path) -> list[SymbolLocation]:
        """Extract top-level symbols from a Python file via AST."""
        symbols: list[SymbolLocation] = []
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(file_path))
        except Exception as exc:
            logger.debug("[global_symbol_index] Could not parse %s: %s", file_path, exc)
            return symbols

        try:
            rel_path = str(file_path.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            rel_path = str(file_path).replace("\\", "/")

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                doc = ast.get_docstring(node)
                symbols.append(
                    SymbolLocation(
                        name=node.name,
                        file_path=rel_path,
                        line=node.lineno,
                        kind="function",
                        signature=f"def {node.name}(...)",
                        docstring=doc[:120] if doc else None,
                    )
                )
            elif isinstance(node, ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                symbols.append(
                    SymbolLocation(
                        name=node.name,
                        file_path=rel_path,
                        line=node.lineno,
                        kind="async_function",
                        signature=f"async def {node.name}(...)",
                        docstring=doc[:120] if doc else None,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                symbols.append(
                    SymbolLocation(
                        name=node.name,
                        file_path=rel_path,
                        line=node.lineno,
                        kind="class",
                        signature=f"class {node.name}",
                        docstring=doc[:120] if doc else None,
                    )
                )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        kind = "constant" if name.isupper() or name.startswith("_") else "variable"
                        symbols.append(
                            SymbolLocation(
                                name=name,
                                file_path=rel_path,
                                line=node.lineno,
                                kind=kind,
                            )
                        )
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    name = node.target.id
                    kind = "constant" if name.isupper() or name.startswith("_") else "variable"
                    symbols.append(
                        SymbolLocation(
                            name=name,
                            file_path=rel_path,
                            line=node.lineno,
                            kind=kind,
                        )
                    )

        # Detect known split-module dynamic wiring
        if rel_path == "scp/autofix/llm_fix.py":
            # llm_fix injects all its module globals into llm_fix_parts/*
            parts_dir = file_path.parent / "llm_fix_parts"
            if parts_dir.exists():
                for part_file in parts_dir.glob("*.py"):
                    if part_file.name == "__init__.py":
                        continue
                    part_rel = str(part_file.relative_to(self.root)).replace("\\", "/")
                    if part_rel not in self._dynamic_wiring:
                        self._dynamic_wiring[part_rel] = {}
                    for sym in symbols:
                        self._dynamic_wiring[part_rel][sym.name] = rel_path

        return symbols

    def build_index(self, force: bool = False) -> None:
        """Build or refresh symbol index incrementally."""
        with self._lock:
            if not force and not self._loaded:
                self.load_cache()

            scp_dir = self.root / "scp"
            if not scp_dir.exists():
                return

            changed_files: list[Path] = []
            current_files_rel: set[str] = set()

            for root, dirs, files in os.walk(scp_dir):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for file in files:
                    if file.endswith(".py"):
                        p = Path(root) / file
                        rel = str(p.relative_to(self.root)).replace("\\", "/")
                        current_files_rel.add(rel)
                        sha = self._hash_file(p)
                        if force or rel not in self._file_hashes or self._file_hashes[rel] != sha:
                            changed_files.append(p)
                            self._file_hashes[rel] = sha

            # Clean removed files
            removed_files = set(self._file_hashes.keys()) - current_files_rel
            for rem in removed_files:
                self._file_hashes.pop(rem, None)
                self._dynamic_wiring.pop(rem, None)
                for sym_list in self._symbols.values():
                    sym_list[:] = [loc for loc in sym_list if loc.file_path != rem]

            if not changed_files and not removed_files and self._loaded:
                return

            for p in changed_files:
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                # Remove existing symbols for this file
                for sym_list in self._symbols.values():
                    sym_list[:] = [loc for loc in sym_list if loc.file_path != rel]

                file_symbols = self.scan_file(p)
                for sym in file_symbols:
                    self._symbols.setdefault(sym.name, []).append(sym)

            # Clean empty symbol keys
            empty_keys = [k for k, v in self._symbols.items() if not v]
            for k in empty_keys:
                self._symbols.pop(k, None)

            self._loaded = True
            self.save_cache()
            logger.info(
                "[global_symbol_index] Index updated: %d symbols across %d files",
                len(self._symbols),
                len(self._file_hashes),
            )

    def lookup_symbol(self, name: str) -> list[SymbolLocation]:
        """Lookup a symbol name across all indexed files."""
        if not self._loaded:
            self.build_index()
        return self._symbols.get(name, [])

    def find_provenance_for_file(
        self, file_path: str | Path, candidate_names: list[str] | None = None
    ) -> list[SymbolLocation]:
        """Find provenances for symbols referenced in a file but defined in other modules."""
        if not self._loaded:
            self.build_index()

        try:
            rel_path = (
                str(Path(file_path).relative_to(self.root)).replace("\\", "/")
                if Path(file_path).is_absolute()
                else str(file_path).replace("\\", "/")
            )
        except ValueError:
            rel_path = str(file_path).replace("\\", "/")

        provenances: list[SymbolLocation] = []
        file_p = self.root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)

        # If candidate names not given, extract undefined names via AST
        if candidate_names is None:
            candidate_names = self.detect_undefined_names(file_p)

        # Check explicit dynamic wiring first
        file_wiring = self._dynamic_wiring.get(rel_path, {})
        for name in candidate_names:
            if name in file_wiring:
                source_file = file_wiring[name]
                locs = [l for l in self.lookup_symbol(name) if l.file_path == source_file]
                if locs:
                    loc = locs[0]
                    loc.wiring_note = f"Injected into this module at runtime by `_wire_parts()` in `{source_file}`"
                    provenances.append(loc)
                    continue

            # General cross-module lookup
            locs = self.lookup_symbol(name)
            external_locs = [l for l in locs if l.file_path != rel_path]
            if external_locs:
                loc = external_locs[0]
                loc.wiring_note = f"Defined in `{loc.file_path}:{loc.line}` (available via project namespace)"
                provenances.append(loc)

        return provenances

    def detect_undefined_names(self, file_path: Path) -> list[str]:
        """Detect potentially undefined names in a Python file via AST."""
        p = self.root / file_path if not file_path.is_absolute() else file_path
        if not p.exists():
            return []
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(p))
        except Exception:
            return []

        defined_names: set[str] = set()
        loaded_names: set[str] = set()

        import builtins
        builtin_names = set(dir(builtins))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined_names.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    defined_names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    defined_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, (ast.Store, ast.Param)):
                    defined_names.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    loaded_names.add(node.id)

        unresolved = [
            n for n in loaded_names
            if n not in defined_names and n not in builtin_names and not n.startswith("__")
        ]
        return unresolved

    def build_provenance_prompt_section(
        self, file_path: str | Path, bug: Any = None
    ) -> str:
        """Construct a structured markdown section describing symbol provenances for LLM prompt."""
        try:
            candidate_names: list[str] = []
            if bug:
                # If bug specifies undefined name in description
                desc = getattr(bug, "description", "") or ""
                bug_type = getattr(bug, "bug_type", "") or ""
                if "PossiblyUndefinedName" in bug_type or "F821" in desc or "undefined" in desc.lower():
                    # Extract quoted or tokenized symbol names
                    import re
                    words = re.findall(r"['`]([a-zA-Z_][a-zA-Z0-9_]*)['`]|name '([a-zA-Z_][a-zA-Z0-9_]*)'", desc)
                    for w1, w2 in words:
                        cand = w1 or w2
                        if cand:
                            candidate_names.append(cand)

            provenances = self.find_provenance_for_file(
                file_path, candidate_names=candidate_names if candidate_names else None
            )
            if not provenances:
                return ""

            lines = [
                "\n[GLOBAL AST SYMBOL PROVENANCE — MANDATORY FIDELITY GUARD]",
                "The following symbols referenced in this file originate from other modules and are provided dynamically at runtime:",
            ]
            for prov in provenances:
                note = f" ({prov.wiring_note})" if prov.wiring_note else ""
                lines.append(
                    f"- `{prov.name}` [{prov.kind}]: defined in `{prov.file_path}:{prov.line}`{note}"
                )
            lines.extend([
                "RULES ON PROVENANCE SYMBOLS:",
                "1. DO NOT remove, delete, comment out, or bypass calls/references to these symbols.",
                "2. DO NOT replace them with stubbed constants, dummy functions, or fail-open passes.",
                "3. These symbols are guaranteed to be populated by the application wiring framework at startup.",
            ])
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("[global_symbol_index] build_provenance_prompt_section error: %s", exc)
            return ""


_GLOBAL_INDEX: GlobalSymbolIndex | None = None
_INDEX_LOCK = threading.Lock()


def get_global_symbol_index(root_dir: Path | str | None = None) -> GlobalSymbolIndex:
    global _GLOBAL_INDEX
    with _INDEX_LOCK:
        if _GLOBAL_INDEX is None:
            _GLOBAL_INDEX = GlobalSymbolIndex(root_dir=root_dir)
            _GLOBAL_INDEX.build_index()
        return _GLOBAL_INDEX
