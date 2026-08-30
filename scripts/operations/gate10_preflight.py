#!/usr/bin/env python3
"""Fail-closed local validation of the Gate 10 release configuration contract."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

from cryptography import x509

DIGEST_RE = re.compile(r"^[^\s@]+(?:/[^\s@]+)*@sha256:[0-9a-f]{64}$")
PROTECTED_ENVS = {"staging", "production"}
STANDALONE_REQUIRED = {
    "APP_ENV",
    "RELEASE_IMAGE",
    "POSTGRES_IMAGE",
    "WUZAPI_IMAGE",
    "RELEASE_HOST",
    "EDGE_NETWORK",
    "POSTGRES_TLS_DIR",
    "DATABASE_URL",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
    "API_KEY_HASH_SECRET",
    "WUZAPI_WEBHOOK_SECRET",
    "REGISTRATION_SECRET_PEPPER",
    "LOG_PII_HASH_KEY",
    "ORCHESTRATOR_TO_BOT_TOKEN",
    "BOT_TO_TRANSCRIPTION_TOKEN",
    "DB_WRITER_INTERNAL_TOKEN",
    "GEMINI_API_KEY",
    "WUZAPI_ADMIN_TOKEN",
    "WUZAPI_TOKEN",
    "DF_DATABASE_URL",
    "DF_HOLDING_IDENTIFIERS",
}
DOKPLOY_REQUIRED = {
    "APP_ENV",
    "RELEASE_IMAGE",
    "POSTGRES_IMAGE",
    "WUZAPI_IMAGE",
    "DATABASE_URL",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
    "API_KEY_HASH_SECRET",
    "WUZAPI_WEBHOOK_SECRET",
    "REGISTRATION_SECRET_PEPPER",
    "LOG_PII_HASH_KEY",
    "ORCHESTRATOR_TO_BOT_TOKEN",
    "BOT_TO_TRANSCRIPTION_TOKEN",
    "DB_WRITER_INTERNAL_TOKEN",
    "GEMINI_API_KEY",
    "WUZAPI_ADMIN_TOKEN",
    "WUZAPI_TOKEN",
    "DF_DATABASE_URL",
    "DF_HOLDING_IDENTIFIERS",
    "POSTGRES_CA_CERT_B64",
    "POSTGRES_SERVER_CERT_B64",
    "POSTGRES_SERVER_KEY_B64",
}
SECRET_NAMES = {
    "DB_PASSWORD",
    "API_KEY_HASH_SECRET",
    "WUZAPI_WEBHOOK_SECRET",
    "REGISTRATION_SECRET_PEPPER",
    "LOG_PII_HASH_KEY",
    "ORCHESTRATOR_TO_BOT_TOKEN",
    "BOT_TO_TRANSCRIPTION_TOKEN",
    "DB_WRITER_INTERNAL_TOKEN",
    "GEMINI_API_KEY",
    "WUZAPI_ADMIN_TOKEN",
    "WUZAPI_TOKEN",
    "POSTGRES_SERVER_KEY_B64",
}
PLACEHOLDER_PARTS = ("placeholder", "change-me", "example", "<secret", "invalid")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment syntax at line {number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in values:
            raise ValueError(f"invalid or duplicate environment name at line {number}")
        values[name] = value.strip()
    return values


def validate(
    values: dict[str, str],
    env_file: Path,
    compose_file: Path,
    target: str = "standalone",
    tls_volume_dir: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    target_mode = target.lower() if target else "standalone"
    if target_mode not in {"standalone", "dokploy"}:
        errors.append(f"unknown deployment target: {target} (supported: standalone, dokploy)")
        target_mode = "standalone"

    required_names = DOKPLOY_REQUIRED if target_mode == "dokploy" else STANDALONE_REQUIRED
    missing = sorted(name for name in required_names if not values.get(name))
    if missing:
        errors.append("missing required names: " + ", ".join(missing))
    environment = values.get("APP_ENV", "").lower()
    if environment not in PROTECTED_ENVS:
        errors.append("APP_ENV must be staging or production")
    for name in ("RELEASE_IMAGE", "POSTGRES_IMAGE", "WUZAPI_IMAGE"):
        if values.get(name) and not DIGEST_RE.fullmatch(values[name]):
            errors.append(f"{name} must be an immutable name@sha256 reference")
    for name in sorted(SECRET_NAMES):
        value = values.get(name, "")
        if value and (len(value) < 32 or any(part in value.lower() for part in PLACEHOLDER_PARTS)):
            errors.append(f"{name} is not safely configured")
    for name in ("DATABASE_URL", "DF_DATABASE_URL"):
        value = values.get(name, "")
        if "sslmode=verify-full" not in value:
            errors.append(f"{name} must require sslmode=verify-full")
        if "sslrootcert=" not in value:
            errors.append(f"{name} must specify sslrootcert")

    if target_mode == "standalone":
        tls_dir_str = values.get("POSTGRES_TLS_DIR", "")
        if tls_dir_str:
            tls_dir = Path(tls_dir_str)
            if tls_dir.is_dir():
                if not (tls_dir / "ca.crt").is_file():
                    errors.append("POSTGRES_TLS_DIR is missing ca.crt")
                if not (tls_dir / "server.crt").is_file():
                    errors.append("POSTGRES_TLS_DIR is missing server.crt")
                if not (tls_dir / "server.key").is_file():
                    errors.append("POSTGRES_TLS_DIR is missing server.key")
                if (tls_dir / "ca.key").exists():
                    errors.append("ca.key must not reside in runtime POSTGRES_TLS_DIR")
                if (tls_dir / "server.crt").is_file():
                    try:
                        cert_data = (tls_dir / "server.crt").read_bytes()
                        cert = x509.load_pem_x509_certificate(cert_data)
                        san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                        san_dns_names = san_ext.value.get_values_for_type(x509.DNSName)  # type: ignore[attr-defined]
                        if "platform-db" not in san_dns_names:
                            errors.append("server.crt SAN does not contain DNS:platform-db")
                    except Exception as exc:
                        errors.append(f"unable to validate server.crt SAN: {exc}")
    elif target_mode == "dokploy":
        # Validate base64 certificate format in dokploy pre-deploy mode
        import base64
        srv_b64 = values.get("POSTGRES_SERVER_CERT_B64", "")
        if srv_b64:
            try:
                raw_srv = base64.b64decode(srv_b64, validate=True)
                cert = x509.load_pem_x509_certificate(raw_srv)
                san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                san_dns_names = san_ext.value.get_values_for_type(x509.DNSName)  # type: ignore[attr-defined]
                if "platform-db" not in san_dns_names:
                    errors.append("POSTGRES_SERVER_CERT_B64 SAN does not contain DNS:platform-db")
            except Exception as exc:
                errors.append(f"invalid POSTGRES_SERVER_CERT_B64: {exc}")

        # If runtime volume directory is provided, validate runtime files on disk
        if tls_volume_dir and tls_volume_dir.is_dir():
            if not (tls_volume_dir / "server.crt").is_file():
                errors.append("TLS volume is missing server.crt")
            if not (tls_volume_dir / "server.key").is_file():
                errors.append("TLS volume is missing server.key")
            if (tls_volume_dir / "ca.key").exists():
                errors.append("ca.key must not reside in runtime TLS volume")

    identifiers = [part.strip() for part in values.get("DF_HOLDING_IDENTIFIERS", "").split(",")]
    if not identifiers or any(not item.isdigit() or len(item) not in {11, 14} for item in identifiers):
        errors.append("DF_HOLDING_IDENTIFIERS must contain only comma-separated CPF/CNPJ digits")
    if not compose_file.is_file():
        errors.append("release compose file does not exist")
    if os.name != "nt":
        mode = stat.S_IMODE(env_file.stat().st_mode)
        if mode & 0o077:
            errors.append("release environment file must not be group/world accessible")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--compose-file", required=True, type=Path)
    parser.add_argument("--target", default=os.environ.get("DEPLOYMENT_TARGET", "standalone"), choices=["standalone", "dokploy"])
    parser.add_argument("--tls-volume-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            raise ValueError("release environment file does not exist")
        errors = validate(
            parse_env(args.env_file),
            args.env_file,
            args.compose_file,
            target=args.target,
            tls_volume_dir=args.tls_volume_dir,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"PREFLIGHT FAILED: {error}", file=sys.stderr)
        return 3
    print("PREFLIGHT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
