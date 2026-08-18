#!/usr/bin/env python3
"""Fail-closed local validation of the Gate 10 release configuration contract."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"^[^\s@]+(?:/[^\s@]+)*@sha256:[0-9a-f]{64}$")
PROTECTED_ENVS = {"staging", "production"}
REQUIRED = {
    "APP_ENV",
    "RELEASE_IMAGE",
    "POSTGRES_IMAGE",
    "WUZAPI_IMAGE",
    "RELEASE_HOST",
    "EDGE_NETWORK",
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


def validate(values: dict[str, str], env_file: Path, compose_file: Path) -> list[str]:
    errors: list[str] = []
    missing = sorted(name for name in REQUIRED if not values.get(name))
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
        if len(value) < 32 or any(part in value.lower() for part in PLACEHOLDER_PARTS):
            errors.append(f"{name} is not safely configured")
    for name in ("DATABASE_URL", "DF_DATABASE_URL"):
        value = values.get(name, "")
        if "sslmode=verify-full" not in value:
            errors.append(f"{name} must require sslmode=verify-full")
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
    args = parser.parse_args(argv)
    try:
        if not args.env_file.is_file():
            raise ValueError("release environment file does not exist")
        errors = validate(parse_env(args.env_file), args.env_file, args.compose_file)
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
