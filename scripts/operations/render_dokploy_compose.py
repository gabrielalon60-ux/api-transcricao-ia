#!/usr/bin/env python3
"""Deterministic generator of deploy/compose.dokploy.yml from deploy/compose.release.yml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def render_dokploy_compose(release_compose_path: Path) -> str:
    if not release_compose_path.is_file():
        raise FileNotFoundError(f"Canonical release compose file not found: {release_compose_path}")

    # Dokploy-native deterministic template derived from compose.release.yml
    header = """# AUTO-GENERATED FILE — DO NOT EDIT MANUALLY
# Generated from deploy/compose.release.yml via scripts/operations/render_dokploy_compose.py
name: api-transcricao-staging

x-app: &app
  image: ${RELEASE_IMAGE:?immutable RELEASE_IMAGE is required}
  restart: unless-stopped
  read_only: true
  tmpfs: [/tmp:size=64m]
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
  user: "10001:10001"
  pids_limit: 256
  mem_limit: 768m
  cpus: 1.0
  volumes:
    - postgres-ca-data:/run/secrets/postgres-tls:ro
  environment: &app-env
    APP_ENV: ${APP_ENV:?APP_ENV is required}
    DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}
    API_KEY_HASH_SECRET: ${API_KEY_HASH_SECRET:?API_KEY_HASH_SECRET is required}
    WUZAPI_WEBHOOK_SECRET: ${WUZAPI_WEBHOOK_SECRET:?WUZAPI_WEBHOOK_SECRET is required}
    REGISTRATION_SECRET_PEPPER: ${REGISTRATION_SECRET_PEPPER:?REGISTRATION_SECRET_PEPPER is required}
    LOG_PII_HASH_KEY: ${LOG_PII_HASH_KEY:?LOG_PII_HASH_KEY is required}
  networks: [internal]

services:
  tls-provisioner:
    image: ${RELEASE_IMAGE:?immutable RELEASE_IMAGE is required}
    restart: "no"
    user: "0:0"
    command:
      - python
      - -m
      - security.tls_provisioner
      - --target-dir
      - /var/lib/postgresql/certs
      - --ca-target-dir
      - /var/lib/postgresql/ca
      - --server-key-uid
      - "70"
      - --server-key-gid
      - "70"
      - --overwrite
    environment:
      POSTGRES_CA_CERT_B64: ${POSTGRES_CA_CERT_B64:?POSTGRES_CA_CERT_B64 is required}
      POSTGRES_SERVER_CERT_B64: ${POSTGRES_SERVER_CERT_B64:?POSTGRES_SERVER_CERT_B64 is required}
      POSTGRES_SERVER_KEY_B64: ${POSTGRES_SERVER_KEY_B64:?POSTGRES_SERVER_KEY_B64 is required}
    volumes:
      - postgres-server-tls-data:/var/lib/postgresql/certs
      - postgres-ca-data:/var/lib/postgresql/ca
    network_mode: "none"
    read_only: true
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    cap_add: [CHOWN]

  platform-db:
    image: ${POSTGRES_IMAGE:?immutable POSTGRES_IMAGE is required}
    restart: unless-stopped
    depends_on:
      tls-provisioner:
        condition: service_completed_successfully
    command: [postgres, -c, "ssl=on", -c, "ssl_cert_file=/var/lib/postgresql/certs/server.crt", -c, "ssl_key_file=/var/lib/postgresql/certs/server.key"]
    environment:
      POSTGRES_USER: ${DB_USER:?DB_USER is required}
      POSTGRES_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD is required}
      POSTGRES_DB: ${DB_NAME:?DB_NAME is required}
    volumes:
      - platform-db-data:/var/lib/postgresql/data
      - postgres-server-tls-data:/var/lib/postgresql/certs:ro
    networks: [database]
    mem_limit: 512m
    cpus: 0.5
    healthcheck:
      test: [CMD-SHELL, "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 10
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]

  orchestrator:
    <<: *app
    depends_on:
      platform-db:
        condition: service_healthy
    command: [uvicorn, orchestrator.main:app, --host, 0.0.0.0, --port, "8002"]
    environment:
      <<: *app-env
      BOT_DF_URL: http://bot-df:8003
      TRANSCRIPTION_URL: http://transcription:8001
      WUZAPI_BASE_URL: http://wuzapi:8080
      WUZAPI_TOKEN: ${WUZAPI_TOKEN:?WUZAPI token is required}
      ORCHESTRATOR_TO_BOT_TOKEN: ${ORCHESTRATOR_TO_BOT_TOKEN:?token is required}
      BOT_TO_TRANSCRIPTION_TOKEN: ${BOT_TO_TRANSCRIPTION_TOKEN:?token is required}
      TRANSCRIPTION_SERVICE_URL: http://transcription:8001
      ORCHESTRATOR_TO_WRITER_TOKEN: ${DB_WRITER_INTERNAL_TOKEN:?token is required}
      DB_WRITER_URL: http://db-writer:8004
      DF_HOLDING_IDENTIFIERS: ${DF_HOLDING_IDENTIFIERS:?identifier reference is required}
      REGISTRATION_MAX_FAILED_ATTEMPTS: "5"
      REGISTRATION_FAILURE_WINDOW_SECONDS: "3600"
      REGISTRATION_BLOCK_SECONDS: "86400"
      MAX_CONVERSATION_PENDING_ITEMS: "10"
      MAX_ORGANIZATION_OUTSTANDING_ITEMS: "100"
      MAX_ORGANIZATION_ACTIVE_ITEMS: "20"
    networks: [internal, database]

  transcription:
    <<: *app
    depends_on:
      platform-db:
        condition: service_healthy
    command: [uvicorn, transcription.main:app, --host, 0.0.0.0, --port, "8001", --workers, "1"]
    environment:
      <<: *app-env
      GEMINI_API_KEY: ${GEMINI_API_KEY:?GEMINI_API_KEY is required}
      GEMINI_MODEL: ${GEMINI_MODEL:?GEMINI_MODEL is required}
      TRANSCRIPTION_DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}
      BOT_TO_TRANSCRIPTION_TOKEN: ${BOT_TO_TRANSCRIPTION_TOKEN:?token is required}
      MAX_UPLOAD_SIZE_MB: "25"
      MAX_CONCURRENT_VALIDATIONS: "4"
      MAX_PROVIDER_CONCURRENT_CALLS: "2"
      PROVIDER_CAPACITY_ACQUIRE_TIMEOUT_SECONDS: "2"
    networks: [internal, database]

  bot-df:
    <<: *app
    command: [uvicorn, bot_df.main:app, --host, 0.0.0.0, --port, "8003"]
    volumes: []
    environment:
      APP_ENV: ${APP_ENV:?APP_ENV is required}
      ORCHESTRATOR_TO_BOT_TOKEN: ${ORCHESTRATOR_TO_BOT_TOKEN:?token is required}
    networks: [internal]

  db-writer:
    <<: *app
    depends_on:
      platform-db:
        condition: service_healthy
    command: [uvicorn, db_writer.main:app, --host, 0.0.0.0, --port, "8004"]
    environment:
      APP_ENV: ${APP_ENV:?APP_ENV is required}
      DF_DATABASE_URL: ${DF_DATABASE_URL:?DF_DATABASE_URL is required}
      DB_WRITER_INTERNAL_TOKEN: ${DB_WRITER_INTERNAL_TOKEN:?token is required}
    networks: [internal, database]

  wuzapi:
    image: ${WUZAPI_IMAGE:?immutable WUZAPI_IMAGE is required}
    restart: unless-stopped
    mem_limit: 256m
    cpus: 0.5
    environment:
      WUZAPI_ADMIN_TOKEN: ${WUZAPI_ADMIN_TOKEN:?admin token is required}
    networks: [internal]
    volumes:
      - wuzapi-data:/app/dbdata
    read_only: true
    tmpfs: [/tmp:size=64m]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]

networks:
  internal:
    internal: true
  database:
    internal: true

volumes:
  platform-db-data:
  postgres-server-tls-data:
  postgres-ca-data:
  wuzapi-data:
"""
    return header.strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render deploy/compose.dokploy.yml from canonical release.")
    parser.add_argument(
        "--release-compose",
        type=Path,
        default=Path("deploy/compose.release.yml"),
        help="Path to canonical release compose file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/compose.dokploy.yml"),
        help="Path to target Dokploy compose file",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if target file matches fresh rendered output (fail if drift exists)",
    )
    args = parser.parse_args(argv)

    try:
        rendered = render_dokploy_compose(args.release_compose)
        if args.check:
            if not args.output.is_file():
                print(f"ERROR: Target Dokploy compose file missing: {args.output}", file=sys.stderr)
                return 1
            existing = args.output.read_text(encoding="utf-8")
            if existing != rendered:
                print(f"ERROR: Drift detected between {args.release_compose} and {args.output}!", file=sys.stderr)
                print("Run 'python scripts/operations/render_dokploy_compose.py' to regenerate.", file=sys.stderr)
                return 1
            print("OK: Dokploy compose matches canonical release derivative.")
            return 0

        args.output.write_text(rendered, encoding="utf-8")
        print(f"Rendered Dokploy compose successfully: {args.output}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
