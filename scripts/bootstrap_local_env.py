"""Create a first-run .env without shipping weak required secrets.

The script is intentionally create-only: an existing .env is never modified.
It copies the documented root template, generates the required JWT/admin/
capability secrets, and creates the production auth-password secret outside
tracked source under `.private-secrets/`. Secret values are never printed.
"""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def _set_key(lines: list[str], key: str, value: str) -> None:
    active = f"{key}="
    commented = f"# {key}="
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(active) or stripped.startswith(commented):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def _write_private_file(path: Path, content: str, *, exclusive: bool) -> bool:
    """Write a secret-bearing file with restrictive permissions.

    ``O_EXCL`` keeps the create-only bootstrap contract true even when two
    installers race.  ``chmod`` is best-effort on Windows, where the ACL is
    controlled by the user account rather than POSIX mode bits.
    """
    flags = os.O_WRONLY | os.O_CREAT
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except Exception:
        # The descriptor is owned by fdopen after entering the context.  Do not
        # leave a partial secret file when writing fails.
        try:
            path.unlink()
        except OSError:
            pass
        raise
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return True


def bootstrap(template: Path, output: Path, secret_dir: Path) -> bool:
    if output.exists():
        print(f"[bootstrap] keep existing {output}")
        return False
    if not template.is_file():
        raise FileNotFoundError(f"missing env template: {template}")

    output.parent.mkdir(parents=True, exist_ok=True)
    secret_dir.mkdir(parents=True, exist_ok=True)
    try:
        secret_dir.chmod(0o700)
    except OSError:
        pass

    auth_password_path = secret_dir / "scp-auth-password"
    if not auth_password_path.exists():
        try:
            _write_private_file(
                auth_password_path,
                secrets.token_urlsafe(32),
                exclusive=True,
            )
        except FileExistsError:
            # Another first-run process won the race; reuse its file without
            # reading or printing the secret here.
            pass

    lines = template.read_text(encoding="utf-8-sig").splitlines()
    jwt_secret = secrets.token_hex(32)
    admin_key = secrets.token_urlsafe(32)
    capability_secret = secrets.token_hex(32)
    try:
        auth_ref = auth_password_path.relative_to(output.parent).as_posix()
    except ValueError:
        auth_ref = str(auth_password_path.resolve())

    _set_key(lines, "SCP_JWT_SECRET", jwt_secret)
    _set_key(lines, "SCP_ADMIN_KEY", admin_key)
    _set_key(lines, "SCP_CAPABILITY_SECRET", capability_secret)
    _set_key(lines, "SCP_AUTH_PASSWORD_FILE", auth_ref)

    if len(jwt_secret) < 32 or len(admin_key) < 8 or len(capability_secret) < 32:
        raise RuntimeError("generated required secrets violate config contract")

    try:
        _write_private_file(output, "\n".join(lines) + "\n", exclusive=True)
    except FileExistsError:
        # Preserve create-only semantics under a concurrent installer.
        print(f"[bootstrap] keep existing {output}")
        return False
    print(f"[bootstrap] created {output}")
    print(f"[bootstrap] created auth secret {auth_password_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default=".env.example")
    parser.add_argument("--output", default=".env")
    parser.add_argument("--secret-dir", default=".private-secrets")
    args = parser.parse_args()

    bootstrap(Path(args.template), Path(args.output), Path(args.secret_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
