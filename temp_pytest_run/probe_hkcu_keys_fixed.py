"""Probe HKCU user env-var keys through the SCP gateway, respecting egress policy."""
import os
import sys

import winreg

sys.path.insert(0, r"C:\Users\check\Downloads\scp")

# Candidate providers → (key_env, base_url_env, model_env)
CANDIDATES = [
    {
        "key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    {
        "key_env": "GOOGLE_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",           # git repo .env đã có GEMINI_BASE_URL
        "model_env": "GEMINI_MODEL",                # git repo .env đã có GEMINI_MODEL
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    {
        "key_env": "GEMINI_API_KEY",                # bạn đã set trong HKCU và tôi đã đưa vào scp/.env
        "base_url_env": "GEMINI_BASE_URL",
        "model_env": "GEMINI_MODEL",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
]

# Cần đặt allowlist cho các host đã chặn ở bước trước (đã prune)
ALLOWLIST = "api.deepseek.com,generativelanguage.googleapis.com"


def read_hkcu(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _ = winreg.QueryValueEx(k, name)
            v = str(val).strip()
            return v or None
    except OSError:
        return None


def probe(name: str, **kwargs) -> dict:
    from scp.llm_gateway.client import EnvCompatProvider
    from scp.llm_gateway.egress_policy import install_egress_guard
    import asyncio

    # Ensure HKCU key is visible in os.environ for provider __init__ reading
    key_env = kwargs["key_env"]
    key = kwargs.get("_stored_key")
    if key:
        os.environ[key_env] = key

    try:
        p = EnvCompatProvider(
            name=name,
            task="chat",
            **kwargs,
        )
        install_egress_guard(type(p))

        async def one_call():
            answer, label = await p._call_model_once("Reply OK")
            return answer, label

        ans, lbl = asyncio.run(one_call())
        return {"alive": ans is not None, "answer": ans[:40] if ans else None, "label": lbl}
    except Exception as e:
        return {"alive": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def main():
    # Set LLM-specific allowlist
    os.environ["SCP_LLM_EGRESS_ALLOWLIST"] = ALLOWLIST
    os.environ["SCP_EGRESS_MODE"] = "allowlist"

    results = []
    for cfg in CANDIDATES:
        key_env = cfg["key_env"]
        key = read_hkcu(key_env) or os.environ.get(key_env)
        cfg_copy = cfg.copy()
        cfg_copy["_stored_key"] = key
        if not key:
            results.append({"name": key_env, "key_found": False})
            continue

        # Use provider-friendly name
        name = key_env.replace("_API_KEY", "")
        result = probe(name, **cfg)
        result["name"] = key_env
        result["key_found"] = True
        results.append(result)

    print("=== Probe HKCU env‑var keys (no secret values) ===")
    for r in results:
        if not r["key_found"]:
            print(f"{r['name']}: KEY‑NOT‑FOUND")
        elif r.get("alive", False):
            a = r.get("answer", "")
            print(f"{r['name']}: ALIVE → {a!r}")
        else:
            e = r.get("error", "")
            if e:
                print(f"{r['name']}: ERROR → {e}")
            else:
                print(f"{r['name']}: DEAD → {r.get('label', 'no answer')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
