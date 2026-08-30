# Gate 10 Phase B2 — Dokploy-Native Deployment Runbook

## 1. Overview & Architecture

This runbook specifies the procedure for deploying the **API Transcrição IA** platform to a shared host managed via **Dokploy UI** without requiring VPS root/SSH access.

### Core Principles
- **Zero Shared Host Impact**: Existing Dokploy projects and Traefik routers must never be stopped, pruned, modified, or restarted.
- **Unique Namespace**: All resources (containers, networks, volumes) are strictly scoped to the project namespace `api-transcricao-staging`.
- **Zero Published Host Ports**: No container publishes ports to the host network (`ports:` omitted); all communication is internal.
- **Strict TLS Verification**: PostgreSQL requires `sslmode=verify-full` with SAN `DNS:platform-db` and public CA verification.
- **CA Private Key Isolation**: `ca.key` resides strictly on operator workstation storage and is **NEVER** uploaded to Dokploy or stored on the shared host.

---

## 2. Certificate Preparation (Workstation Local)

The operator generates dedicated staging TLS certificates locally before deployment:

```bash
uv run python scripts/operations/generate_postgres_tls.py \
  --runtime-dir ./staging_tls_runtime \
  --ca-private-dir ./staging_ca_private \
  --san platform-db
```

### Encode for Dokploy Transport:
```bash
# Linux/macOS
export POSTGRES_CA_CERT_B64=$(base64 -w 0 ./staging_tls_runtime/ca.crt)
export POSTGRES_SERVER_CERT_B64=$(base64 -w 0 ./staging_tls_runtime/server.crt)
export POSTGRES_SERVER_KEY_B64=$(base64 -w 0 ./staging_tls_runtime/server.key)

# Windows PowerShell
$POSTGRES_CA_CERT_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("staging_tls_runtime/ca.crt"))
$POSTGRES_SERVER_CERT_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("staging_tls_runtime/server.crt"))
$POSTGRES_SERVER_KEY_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("staging_tls_runtime/server.key"))
```

> [!WARNING]
> Base64 is an ASCII transport encoding, **not encryption**. Do not commit these values to Git. Enter them directly into the Dokploy UI encrypted environment secrets store.

---

## 3. Dokploy Project Setup

1. **Create Project**: In Dokploy UI, create a project named `api-transcricao-staging`.
2. **Create Compose Service**: Select **Compose** service pointing to:
   - **Repository**: `https://github.com/gabrielalon60-ux/api-transcricao-ia.git`
   - **Branch / Commit**: `bdc0a4fc63a3907e9cc7d533bbef4c3981deeabd`
   - **Compose Path**: `deploy/compose.dokploy.yml`
3. **Environment Variables**: Configure the required project-scoped variables:
   - `APP_ENV=staging`
   - `RELEASE_IMAGE=<pinned-digest>`
   - `POSTGRES_IMAGE=<pinned-digest>`
   - `WUZAPI_IMAGE=<pinned-digest>`
   - `DB_USER=app`
   - `DB_PASSWORD=<staging-secret>`
   - `DB_NAME=platform`
   - `DATABASE_URL=postgresql://app:<password>@platform-db:5432/platform?sslmode=verify-full&sslrootcert=/run/secrets/postgres-tls/ca.crt`
   - `DF_DATABASE_URL=postgresql://app:<password>@platform-db:5432/platform?sslmode=verify-full&sslrootcert=/run/secrets/postgres-tls/ca.crt`
   - `GEMINI_MODEL=gemini-2.5-flash`
   - `GEMINI_API_KEY=<provider-issued-staging-key>`
   - `POSTGRES_CA_CERT_B64=<base64-ca-cert>`
   - `POSTGRES_SERVER_CERT_B64=<base64-server-cert>`
   - `POSTGRES_SERVER_KEY_B64=<base64-server-key>`
   - (All internal tokens: `API_KEY_HASH_SECRET`, `WUZAPI_WEBHOOK_SECRET`, `REGISTRATION_SECRET_PEPPER`, `LOG_PII_HASH_KEY`, `ORCHESTRATOR_TO_BOT_TOKEN`, `BOT_TO_TRANSCRIPTION_TOKEN`, `DB_WRITER_INTERNAL_TOKEN`, `WUZAPI_ADMIN_TOKEN`, `WUZAPI_TOKEN`, `DF_HOLDING_IDENTIFIERS`)

---

## 4. Preflight & Provisioning Sequence

1. **Pre-Deploy Preflight (Workstation)**:
   ```bash
   uv run python scripts/operations/gate10_preflight.py \
     --env-file staging.env \
     --compose-file deploy/compose.dokploy.yml \
     --target dokploy
   ```
2. **Deploy via Dokploy**: Trigger deployment in Dokploy UI.
   - `tls-provisioner` runs first, unpacks base64 secrets into named volumes (`postgres-server-tls-data` and `postgres-ca-data`), enforces `0600` permissions on `server.key`, validates SAN `DNS:platform-db`, and exits with code 0.
   - `platform-db` starts with TLS enabled using the populated volume.
   - Application containers start with `postgres-ca-data` mounted read-only at `/run/secrets/postgres-tls/ca.crt`.

---

## 5. Security Invariants & Auditing

- **Unobservable Host Checks**: Global daemon or host-level filesystem checks that cannot be inspected by project members without root access must be reported as `NOT_OBSERVABLE_WITH_PROJECT_ACCESS` rather than assumed `PASS`.
- **WUZAPI Initial B2**: Runs in isolated synthetic mode with zero WhatsApp phone session pairing.
- **Rollback Boundary**: In case of issues, rollback operations must ONLY remove resources inside the `api-transcricao-staging` project. Never issue `docker system prune` or global network commands.

---

## 6. Compose Source-of-Truth & Generated File Policy

- **Canonical Source of Truth**: `deploy/compose.release.yml` is the sole authoritative definition of service topologies, images, healthchecks, security profiles, and resource limits.
- **Generated Derivative**: `deploy/compose.dokploy.yml` is deterministically generated from `deploy/compose.release.yml` via:
  ```bash
  uv run python scripts/operations/render_dokploy_compose.py
  ```
- **Prohibition on Manual Edits**: Operators and developers must **NEVER** edit `deploy/compose.dokploy.yml` manually.
- **Drift Verification**: Continuous integration and pre-commit checks enforce parity using:
  ```bash
  uv run python scripts/operations/render_dokploy_compose.py --check
  ```
