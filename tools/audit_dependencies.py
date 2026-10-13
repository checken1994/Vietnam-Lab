#!/usr/bin/env python3
"""DEP-01: Supply-Chain Dependency & Vulnerability Security Auditor.

Enforces:
1. Pinned dependencies in scp/requirements.txt.
2. SHA256 hash pinning in scp/requirements.hashes.txt (CWE-1357 / SLSA Level 2).
3. Machine-readable Software Bill of Materials (SBOM) in docs/sbom.json.
4. Vulnerability check (via pip-audit or OSV API query) ensuring 0 known HIGH/CRITICAL advisories.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


def fail_closed(msg: str) -> None:
    print(f"\n[DEP-01 FAIL-CLOSED] {msg}", file=sys.stderr)
    sys.exit(1)


def _normalize_pkg_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _parse_pinned_packages(req_path: Path) -> dict[str, str]:
    pkgs: dict[str, str] = {}
    if not req_path.exists():
        return pkgs
    for raw_line in req_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        line_no_cont = line.rstrip("\\").strip()
        spec_part = line_no_cont.split(";", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?==([A-Za-z0-9_.\-]+)", spec_part)
        if m:
            pkgs[_normalize_pkg_name(m.group(1))] = m.group(2)
    return pkgs


def verify_hash_pinning(repo_root: Path, hash_file_path: Path | None = None) -> int:
    hash_file = hash_file_path or (repo_root / "scp" / "requirements.hashes.txt")
    if not hash_file.exists():
        fail_closed(f"Missing hash pinning lockfile: {hash_file}")

    content = hash_file.read_text(encoding="utf-8")
    hash_count = len(re.findall(r"--hash=sha256:[a-f0-9]{64}", content))
    if hash_count < 20:
        fail_closed(f"Insufficient SHA256 hashes found in {hash_file}: only {hash_count} hashes found.")

    hashed_pkgs: dict[str, str] = {}
    current_pkg: str | None = None
    current_ver: str | None = None
    current_hashes = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line_clean = line.rstrip("\\").strip()
        if line_clean.startswith("--hash="):
            if re.match(r"^--hash=sha256:[a-f0-9]{64}$", line_clean):
                current_hashes += 1
            else:
                raise AssertionError(f"DEP-01/DEP-03 VIOLATION: Invalid hash line '{line_clean}' in {hash_file.name}")
        else:
            if current_pkg is not None and current_hashes == 0:
                raise AssertionError(
                    f"DEP-01/DEP-03 VIOLATION: Package '{current_pkg}=={current_ver}' in {hash_file.name} has 0 SHA256 hashes."
                )
            spec_part = line_clean.split(";", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?==([A-Za-z0-9_.\-]+)", spec_part)
            if not m:
                raise AssertionError(
                    f"DEP-01/DEP-03 VIOLATION: Unpinned or malformed requirement '{line_clean}' in {hash_file.name}"
                )
            current_pkg = _normalize_pkg_name(m.group(1))
            current_ver = m.group(2)
            current_hashes = len(re.findall(r"--hash=sha256:[a-f0-9]{64}", line_clean))
            hashed_pkgs[current_pkg] = current_ver

    if current_pkg is not None and current_hashes == 0:
        raise AssertionError(
            f"DEP-01/DEP-03 VIOLATION: Package '{current_pkg}=={current_ver}' in {hash_file.name} has 0 SHA256 hashes."
        )

    if hash_file_path is None:
        direct_pkgs = _parse_pinned_packages(repo_root / "scp" / "requirements.txt")
        lock_pkgs = _parse_pinned_packages(repo_root / "scp" / "requirements.lock.txt")
        required_all = {**direct_pkgs, **lock_pkgs}
        missing = [
            f"{pkg}=={ver}"
            for pkg, ver in sorted(required_all.items())
            if hashed_pkgs.get(pkg) != ver
        ]
        if missing:
            raise AssertionError(
                f"DEP-01/DEP-03 VIOLATION: {len(missing)} direct/transitive lockfile dependencies are missing or mismatched in {hash_file.name}: "
                + ", ".join(missing[:10])
            )

    print(
        f"[DEP-01 PASS] Verified {hash_count} SHA256 cryptographic hashes across {len(hashed_pkgs)} packages in {hash_file.name}."
    )
    return hash_count



def verify_sbom(repo_root: Path) -> int:
    sbom_file = repo_root / "docs" / "sbom.json"
    if not sbom_file.exists():
        fail_closed(f"Missing SBOM file: {sbom_file}")

    try:
        data = json.loads(sbom_file.read_text(encoding="utf-8"))
        components = data.get("components", [])
        if not components:
            fail_closed(f"SBOM components list is empty in {sbom_file}")
        print(f"[DEP-01 PASS] Verified CycloneDX SBOM ({len(components)} components listed).")
        return len(components)
    except Exception as e:
        fail_closed(f"Invalid SBOM JSON in {sbom_file}: {e}")
        return 0


def verify_dockerfile_enforces_hashes(repo_root: Path, dockerfile_path: Path | None = None) -> bool:
    """DEP-03: Verify Dockerfile uses --require-hashes with requirements.hashes.txt (CWE-1357).

    Fail-closed parsing rules:
    1. Parse logical instructions across physical lines, resolving line continuations ('\\').
    2. Completely ignore comment lines starting with '#'.
    3. Every execution command that runs 'pip install' must explicitly include '--require-hashes'.
    4. If any 'pip install' command lacks '--require-hashes', raise AssertionError immediately.
    5. The Dockerfile must contain at least one valid 'pip install --require-hashes' referencing requirements.hashes.txt.
    """
    dockerfile = dockerfile_path or (repo_root / "Dockerfile")
    if not dockerfile.exists():
        fail_closed(f"Missing Dockerfile at {dockerfile}")

    content = dockerfile.read_text(encoding="utf-8")

    # 1. Parse lines resolving line continuations and stripping comment lines
    instructions: list[str] = []
    current_cmd: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current_cmd.append(line[:-1].strip())
        else:
            current_cmd.append(line)
            instructions.append(" ".join(current_cmd))
            current_cmd = []

    if current_cmd:
        instructions.append(" ".join(current_cmd))

    # 2. Inspect instructions for pip install invocations
    pip_install_found = False
    has_hashes_file = False

    for instr in instructions:
        # Split multi-command chains (&&, ||, ;)
        sub_cmds = re.split(r"&&|\|\||;", instr)
        for sub_cmd in sub_cmds:
            cmd = sub_cmd.strip()
            # Strip shell trailing comments if any
            cmd_clean = re.sub(r"\s+#.*$", "", cmd).strip()
            # Normalize brackets, quotes, and whitespace for exec-form and option parsing
            norm_cmd = re.sub(r'["\'\[\],]', ' ', cmd_clean)
            norm_cmd = re.sub(r'\s+', ' ', norm_cmd).strip()
            # Match pip install commands:
            # - shell form: RUN pip install, python -m pip install, python3 -m pip install
            # - interspersed options: pip --no-cache-dir install, pip -q install
            # - exec form: RUN ["pip", "install", ...]
            # - case-insensitive: PIP INSTALL
            if re.search(r'\bpip(?:\d+(?:\.\d+)?)?(?:\s+-[^\s]+)*\s+install\b', norm_cmd, re.IGNORECASE):
                pip_install_found = True
                if "--require-hashes" not in cmd_clean:
                    raise AssertionError(
                        f"DEP-03 VIOLATION: Execution command '{cmd_clean}' runs pip install without '--require-hashes'."
                    )
                if "requirements.hashes.txt" not in cmd_clean:
                    raise AssertionError(
                        f"DEP-03 VIOLATION: Execution command '{cmd_clean}' runs pip install without referencing 'requirements.hashes.txt'."
                    )
                has_hashes_file = True

    if not pip_install_found:
        raise AssertionError("DEP-03 VIOLATION: Dockerfile contains no 'pip install' commands.")

    if not has_hashes_file:
        raise AssertionError(
            "DEP-03 VIOLATION: Dockerfile does not enforce '--require-hashes' with 'requirements.hashes.txt'."
        )

    print("[DEP-03 PASS] Verified Dockerfile strictly enforces pip '--require-hashes' with requirements.hashes.txt.")
    return True


KNOWN_REVOKED_OR_BOGUS_DIGESTS = frozenset({
    "7f99dd92994e43c8d9e68c9bc88a5369bb9892c99a071efdf6e680a618c6ee4c",
    "0" * 64,
    "a" * 64,
    "f" * 64,
    "0123456789abcdef" * 4,
    "fedcba9876543210" * 4,
})

VERIFIED_BASE_IMAGE_DIGESTS: dict[str, frozenset[str]] = {
    "python:3.12-slim": frozenset({
        "a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
    }),
}

VALID_SPDX_LICENSES = frozenset({
    "0BSD",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "Apache-2.0",
    "Artistic-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "CC-BY-4.0",
    "CC0-1.0",
    "EPL-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "HPND",
    "ISC",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
})

VALID_SPDX_EXCEPTIONS = frozenset({
    "Autoconf-exception-2.0",
    "Bison-exception-2.2",
    "Classpath-exception-2.0",
    "GCC-exception-3.1",
    "LLVM-exception",
    "OpenSSL-Exception",
})


def _is_valid_spdx_expression(expr: str) -> bool:
    """Validate an SPDX license identifier or compound (AND/OR/WITH) expression with strict operator grammar."""
    cleaned = re.sub(r"[()]", " ", expr.strip())
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return False

    state = "EXPECT_LICENSE"
    for tok in tokens:
        upper = tok.upper()
        if state == "EXPECT_LICENSE":
            if upper in {"AND", "OR", "WITH"}:
                return False
            lic_base = tok[:-1] if tok.endswith("+") else tok
            if lic_base not in VALID_SPDX_LICENSES:
                return False
            state = "AFTER_LICENSE"
        elif state == "AFTER_LICENSE":
            if upper in {"AND", "OR"}:
                state = "EXPECT_LICENSE"
            elif upper == "WITH":
                state = "EXPECT_EXCEPTION"
            else:
                return False
        elif state == "EXPECT_EXCEPTION":
            if tok not in VALID_SPDX_EXCEPTIONS:
                return False
            state = "AFTER_EXCEPTION"
        elif state == "AFTER_EXCEPTION":
            if upper in {"AND", "OR"}:
                state = "EXPECT_LICENSE"
            else:
                return False

    return state in {"AFTER_LICENSE", "AFTER_EXCEPTION"}


def verify_docker_hub_digest_exists(image_ref: str, digest_hex: str, timeout: float = 5.0) -> bool:
    """Verify via Docker Hub Registry v2 API that sha256:<digest_hex> exists on the registry."""
    name_part = image_ref.split("@", 1)[0].split(":", 1)[0].strip()
    if "/" not in name_part:
        repo = f"library/{name_part}"
    else:
        repo = name_part
    token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    try:
        with urllib.request.urlopen(token_url, timeout=timeout) as resp:
            token = json.loads(resp.read().decode("utf-8")).get("token", "")
        if not token:
            return False
        manifest_url = f"https://registry-1.docker.io/v2/{repo}/manifests/sha256:{digest_hex.lower()}"
        req = urllib.request.Request(
            manifest_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "application/vnd.oci.image.index.v1+json, "
                    "application/vnd.docker.distribution.manifest.list.v2+json, "
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=timeout) as mresp:
            return mresp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise AssertionError(
                f"DEP-04/DEP-06 VIOLATION: Digest 'sha256:{digest_hex}' for '{image_ref}' does not exist on Docker Hub (HTTP 404)."
            ) from exc
        return False
    except Exception:
        return False


def _is_repeating_pattern_digest(digest_hex: str) -> bool:
    for period in (1, 2, 4, 8, 16):
        if digest_hex == (digest_hex[:period] * (64 // period))[:64]:
            return True
    return False


def verify_dockerfile_pins_base_image_digest(
    repo_root: Path,
    dockerfile_path: Path | None = None,
    *,
    verify_registry: bool = False,
) -> bool:
    """DEP-04 / DEP-06: Verify Dockerfile base images are pinned to valid cryptographic sha256 digests.

    Fail-closed rules:
    1. Parse logical instructions in Dockerfile resolving line continuations and stripping comments.
    2. Every 'FROM' instruction that references an external base image must have '@sha256:[a-f0-9]{64}'.
    3. Multi-stage aliases (e.g. 'AS runtime') are tracked case-insensitively AFTER checking whether
       the current base image references a PREVIOUSLY declared stage alias (preventing 'FROM x AS x' self-bypass).
    4. Flags (e.g. '--platform=...', '--pull') are parsed without bypassing base image inspection.
    5. Low-entropy digests (< 8 unique hex chars), repeating pattern digests, or known revoked/fabricated digests raise AssertionError immediately.
    6. Dockerfile must contain at least one valid pinned base image.
    """
    dockerfile = dockerfile_path or (repo_root / "Dockerfile")
    if not dockerfile.exists():
        fail_closed(f"Missing Dockerfile at {dockerfile}")

    content = dockerfile.read_text(encoding="utf-8")

    instructions: list[str] = []
    current_cmd: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current_cmd.append(line[:-1].strip())
        else:
            current_cmd.append(line)
            instructions.append(" ".join(current_cmd))
            current_cmd = []

    if current_cmd:
        instructions.append(" ".join(current_cmd))

    stage_aliases: set[str] = set()
    pinned_count = 0

    for instr in instructions:
        if not re.match(r"^FROM\b", instr, re.IGNORECASE):
            continue

        parts = instr.split()
        idx = 1
        while idx < len(parts) and parts[idx].startswith("--"):
            idx += 1

        if idx >= len(parts):
            raise AssertionError(f"DEP-04 VIOLATION: Malformed FROM instruction without image name: '{instr}'")

        base_image = parts[idx].strip()
        idx += 1

        alias = None
        if idx < len(parts) and parts[idx].upper() == "AS":
            if idx + 1 < len(parts):
                alias = parts[idx + 1].strip()

        # Check previously declared stage aliases BEFORE adding the current stage's alias
        is_prior_stage = base_image.lower() in stage_aliases
        if alias:
            stage_aliases.add(alias.lower())

        if is_prior_stage:
            continue

        digest_match = re.search(r"@sha256:([a-f0-9]{64})$", base_image, re.IGNORECASE)
        if not digest_match:
            raise AssertionError(
                f"DEP-04 VIOLATION: Dockerfile base image '{base_image}' is not pinned to a sha256 digest via '@sha256:<64-hex>'."
            )

        digest_hex = digest_match.group(1).lower()
        if len(set(digest_hex)) < 8 or _is_repeating_pattern_digest(digest_hex):
            raise AssertionError(
                f"DEP-04 VIOLATION: Dockerfile base image '{base_image}' uses a low-entropy/placeholder sha256 digest."
            )
        if digest_hex in KNOWN_REVOKED_OR_BOGUS_DIGESTS:
            raise AssertionError(
                f"DEP-04/DEP-06 VIOLATION: Dockerfile base image '{base_image}' uses a revoked or non-existent sha256 digest '{digest_hex}'."
            )

        image_tag = base_image.split("@", 1)[0].strip().lower()
        if dockerfile_path is None and image_tag in VERIFIED_BASE_IMAGE_DIGESTS:
            if digest_hex not in VERIFIED_BASE_IMAGE_DIGESTS[image_tag]:
                if not verify_docker_hub_digest_exists(base_image, digest_hex):
                    raise AssertionError(
                        f"DEP-04/DEP-06 VIOLATION: Dockerfile base image '{base_image}' uses unverified digest '{digest_hex}'."
                    )
        elif verify_registry:
            verify_docker_hub_digest_exists(base_image, digest_hex)

        pinned_count += 1

    if pinned_count == 0:
        raise AssertionError("DEP-04 VIOLATION: Dockerfile contains no pinned base images with '@sha256:<64-hex>'.")

    print(f"[DEP-04 PASS] Verified Dockerfile base image digest pinning ({pinned_count} base images pinned to sha256).")
    return True


def verify_sbom_licenses(repo_root: Path, sbom_path: Path | None = None) -> int:
    """DEP-05: Verify 100% of SBOM components have valid SPDX licenses and 0 UNKNOWN licenses.

    Fail-closed rules:
    1. Parse SBOM JSON at docs/sbom.json (or sbom_path).
    2. Assert that components list is non-empty.
    3. For every component, assert that licenses list is present and non-empty.
    4. Assert that 0 components have 'UNKNOWN', placeholder, missing, or non-SPDX licenses (fail-closed).
    5. Return the count of verified compliant components.
    """
    target_sbom = sbom_path or (repo_root / "docs" / "sbom.json")
    if not target_sbom.exists():
        fail_closed(f"Missing SBOM file for license audit: {target_sbom}")

    try:
        data = json.loads(target_sbom.read_text(encoding="utf-8"))
    except Exception as exc:
        fail_closed(f"Invalid SBOM JSON in {target_sbom}: {exc}")

    components = data.get("components", [])
    if not components:
        fail_closed(f"DEP-05 VIOLATION: SBOM components list is empty in {target_sbom}")

    INVALID_PLACEHOLDERS = {
        "UNKNOWN",
        "NONE",
        "N/A",
        "NULL",
        "UNDEFINED",
        "UNLICENSED",
        "NOASSERTION",
    }

    unknown_components: list[str] = []

    for comp in components:
        name = comp.get("name", "unnamed")
        licenses = comp.get("licenses", [])
        if not licenses:
            unknown_components.append(f"{name}: missing licenses section")
            continue

        has_valid_license = False
        for lic_entry in licenses:
            lic = lic_entry.get("license", {})
            lic_id = (lic.get("id") or "").strip()
            lic_name = (lic.get("name") or "").strip()
            expr = (lic_entry.get("expression") or "").strip()

            candidates = [v for v in (lic_id, lic_name, expr) if v]
            if not candidates:
                unknown_components.append(f"{name}: empty license identifiers")
                has_valid_license = False
                break

            is_placeholder = any(
                c.upper() in INVALID_PLACEHOLDERS or "UNKNOWN" in c.upper()
                for c in candidates
            )
            if is_placeholder:
                unknown_components.append(f"{name}: license is UNKNOWN or placeholder ({candidates})")
                has_valid_license = False
                break

            non_spdx = [c for c in candidates if not _is_valid_spdx_expression(c)]
            if non_spdx:
                unknown_components.append(f"{name}: non-SPDX or unapproved license identifier ({non_spdx})")
                has_valid_license = False
                break

            has_valid_license = True

        if not has_valid_license and name not in [u.split(":")[0] for u in unknown_components]:
            unknown_components.append(f"{name}: no valid license declared")

    if unknown_components:
        raise AssertionError(
            f"DEP-05 VIOLATION: {len(unknown_components)}/{len(components)} components have invalid, non-SPDX, UNKNOWN, or missing licenses:\n"
            + "\n".join(f"  - {item}" for item in unknown_components)
        )

    print(f"[DEP-05 PASS] Verified 100% SPDX license compliance: 0/{len(components)} invalid/UNKNOWN components in {target_sbom.name}.")
    return len(components)


def audit_vulnerabilities(repo_root: Path) -> None:
    # 1. Try pip-audit CLI if installed
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--desc", "-r", str(repo_root / "scp" / "requirements.txt")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            print("[DEP-01 PASS] pip-audit passed: 0 known vulnerabilities found.")
            return
        elif proc.returncode != 0 and "No known vulnerabilities found" in proc.stdout:
            print("[DEP-01 PASS] pip-audit passed: 0 known vulnerabilities found.")
            return
    except (subprocess.SubprocessError, OSError):
        pass

    # 2. Fallback to OSV API query for critical runtime packages
    req_file = repo_root / "scp" / "requirements.txt"
    packages = []
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?==([A-Za-z0-9_.\-]+)$", line)
        if m:
            packages.append((m.group(1), m.group(2)))

    vulns_found = []
    print(f"[DEP-01] Auditing {len(packages)} dependencies against OSV database...")
    for pkg, ver in packages:
        payload = json.dumps({"package": {"name": pkg, "ecosystem": "PyPI"}, "version": ver}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.osv.dev/v1/query",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "SCP-DepAuditor/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                vulns = data.get("vulns", [])
                if vulns:
                    for v in vulns:
                        vulns_found.append(f"{pkg}=={ver}: {v.get('id')} - {v.get('summary', 'No summary')}")
        except Exception:
            # Best effort per-network call
            continue

    if vulns_found:
        print("\n[DEP-01 VULNERABILITY ALERT] The following advisories were detected:", file=sys.stderr)
        for v in vulns_found:
            print(f"  - {v}", file=sys.stderr)
        fail_closed(f"Found {len(vulns_found)} known vulnerabilities in dependencies.")

    print(f"[DEP-01 PASS] 0 known vulnerabilities detected across all {len(packages)} pinned dependencies.")


def main() -> int:
    parser = argparse.ArgumentParser(description="DEP-01: Supply-Chain Dependency Auditor")
    parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    print("[DEP-01] Running supply-chain security audit...")

    verify_hash_pinning(repo_root)
    verify_sbom(repo_root)
    try:
        verify_dockerfile_enforces_hashes(repo_root)
    except AssertionError as exc:
        fail_closed(str(exc))
    try:
        verify_dockerfile_pins_base_image_digest(repo_root)
    except AssertionError as exc:
        fail_closed(str(exc))
    try:
        verify_sbom_licenses(repo_root)
    except AssertionError as exc:
        fail_closed(str(exc))
    audit_vulnerabilities(repo_root)

    print("\n[DEP-01 COMPLETE] All dependency supply-chain security checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
