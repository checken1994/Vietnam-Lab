"""Create or update .env with cryptographically secure required secrets.

Generates 256-bit secrets for SCP_JWT_SECRET, SCP_ADMIN_KEY,
SCP_CAPABILITY_SECRET, SCP_AUTH_TOKEN_SECRET, SCP_AUTH_TOKEN,
SCP_PC_CONTROLLER_TOKEN, SCP_ENCRYPTION_KEY, and creates
the production auth-password secret under `.private-secrets/`.
"""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path

REQUIRED_SECRET_KEYS = (
    "SCP_JWT_SECRET",
    "SCP_ADMIN_KEY",
    "SCP_CAPABILITY_SECRET",
    "SCP_AUTH_TOKEN_SECRET",
    "SCP_AUTH_TOKEN",
    "SCP_PC_CONTROLLER_TOKEN",
    "SCP_ENCRYPTION_KEY",
)

FORBIDDEN_OR_PLACEHOLDER = {
    "",
    "admin",
    "password",
    "123456",
    "secret",
    "test",
    "changeme",
    "super-secret-scp-v3-enterprise-key",
    "<python secrets.token_hex(32)>",
    "<python secrets.token_urlsafe(32)>",
    "sk-or-...",
    "sk-...",
}


def _get_key_value(lines: list[str], key: str) -> str | None:
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def _set_key(lines: list[str], key: str, value: str) -> None:
    active = f"{key}="
    commented = f"# {key}="
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(active) or stripped.startswith(commented):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def _generate_secret_for_key(key: str) -> str:
    if key in ("SCP_ADMIN_KEY", "SCP_ENCRYPTION_KEY"):
        return secrets.token_urlsafe(32)
    return secrets.token_hex(32)


def bootstrap(
    template: Path,
    output: Path,
    secret_dir: Path,
    update: bool = False,
    server: bool = False,
) -> bool:
    secret_dir.mkdir(parents=True, exist_ok=True)
    auth_password_path = secret_dir / "scp-auth-password"
    if not auth_password_path.exists():
        auth_password_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")

    try:
        auth_ref = auth_password_path.relative_to(output.parent).as_posix()
    except ValueError:
        auth_ref = str(auth_password_path.resolve())

    if output.exists():
        if not update:
            print(f"[bootstrap] keep existing {output} (use --update to populate missing secrets)")
            return False

        lines = output.read_text(encoding="utf-8-sig").splitlines()
        updated_any = False

        for k in REQUIRED_SECRET_KEYS:
            curr_val = _get_key_value(lines, k)
            if curr_val is None or curr_val in FORBIDDEN_OR_PLACEHOLDER:
                new_sec = _generate_secret_for_key(k)
                _set_key(lines, k, new_sec)
                updated_any = True

        curr_auth_pwd = _get_key_value(lines, "SCP_AUTH_PASSWORD_FILE")
        if not curr_auth_pwd or curr_auth_pwd in FORBIDDEN_OR_PLACEHOLDER:
            _set_key(lines, "SCP_AUTH_PASSWORD_FILE", auth_ref)
            updated_any = True

        if server:
            curr_host = _get_key_value(lines, "SCP_HOST")
            if curr_host != "0.0.0.0":
                _set_key(lines, "SCP_HOST", "0.0.0.0")
                updated_any = True

        if updated_any:
            output.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"[bootstrap] updated {output} with missing required secrets")
            return True
        else:
            print(f"[bootstrap] {output} already contains all required secrets")
            return False

    if not template.is_file():
        raise FileNotFoundError(f"missing env template: {template}")

    lines = template.read_text(encoding="utf-8-sig").splitlines()

    for k in REQUIRED_SECRET_KEYS:
        _set_key(lines, k, _generate_secret_for_key(k))

    _set_key(lines, "SCP_AUTH_PASSWORD_FILE", auth_ref)

    if server:
        _set_key(lines, "SCP_HOST", "0.0.0.0")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[bootstrap] created {output}")
    print(f"[bootstrap] created auth secret {auth_password_path}")
    print("[bootstrap] Operational Mode: Local Verified Mode active by default (zero external LLM dependencies).")
    print("[bootstrap] Optional: To enable external LLM reasoning, configure any supported API key in .env:")
    print("  - GEMINI_API_KEY (Google Gemini)")
    print("  - DEEPSEEK_API_KEY (DeepSeek)")
    print("  - GROQ_API_KEY (Groq)")
    print("  - OPENAI_API_KEY (OpenAI)")
    print("  - MISTRAL_API_KEY (Mistral)")
    print("  - OPENROUTER_API_KEY (OpenRouter)")
    print("  - or custom via SCP_LLM_FALLBACK_PROVIDERS")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap or update .env with secure random tokens.")
    parser.add_argument("--template", default=".env.example")
    parser.add_argument("--output", default=".env")
    parser.add_argument("--secret-dir", default=".private-secrets")
    parser.add_argument("--update", action="store_true", help="Update existing .env with missing required secrets.")
    parser.add_argument("--server", action="store_true", help="Set SCP_HOST=0.0.0.0 for external server deployment.")
    args = parser.parse_args()

    bootstrap(
        Path(args.template),
        Path(args.output),
        Path(args.secret_dir),
        update=args.update,
        server=args.server,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
