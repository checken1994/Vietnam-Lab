from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any


_SECRET_WORDS=("secret", "token", "password", "cookie", "api_key", "authorization", "private_key")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(w in str(k).lower() for w in _SECRET_WORDS) else _redact(v) for k,v in value.items()}
    if isinstance(value, list): return [_redact(v) for v in value]
    if isinstance(value, str) and any(w in value.lower() for w in _SECRET_WORDS): return "[REDACTED_STRING]"
    return value


def _hash(obj: Any) -> str:
    return "sha256:"+hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()


class TraceLedger:
    def __init__(self, path: str | Path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
    def append(self, **fields: Any) -> dict[str, Any]:
        lines=self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        prev=json.loads(lines[-1]) if lines else None
        entry={"trace_id":fields.pop("trace_id",None) or "trace_"+secrets.token_hex(8),"seq":len(lines)+1,"prev_hash":prev.get("hash") if prev else None,"fields":_redact(fields)}
        entry["hash"]=_hash(entry)
        with self.path.open("a",encoding="utf-8") as f:f.write(json.dumps(entry,ensure_ascii=False,sort_keys=True)+"\n")
        return entry
    def verify(self) -> dict[str, Any]:
        lines=self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        errors=[];prev=None
        for i,line in enumerate(lines,1):
            e=json.loads(line)
            if e.get("seq")!=i: errors.append(f"seq:{i}")
            if e.get("prev_hash")!=prev: errors.append(f"prev_hash:{i}")
            body={k:v for k,v in e.items() if k!="hash"}
            if _hash(body)!=e.get("hash"): errors.append(f"hash:{i}")
            serialized=json.dumps(e,ensure_ascii=False)
            if any(w in serialized.lower() and "[redacted" not in serialized.lower() for w in _SECRET_WORDS): errors.append(f"secret:{i}")
            prev=e.get("hash")
        return {"entries":len(lines),"hash_chain_valid":not errors,"errors":errors}
    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        if not self.path.exists(): return None
        for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
            if not line.strip(): continue
            try:
                e = json.loads(line)
                if e.get("trace_id") == trace_id or e.get("fields", {}).get("task_id") == trace_id or e.get("fields", {}).get("trace_id") == trace_id:
                    return e
            except Exception:
                continue
        return None
