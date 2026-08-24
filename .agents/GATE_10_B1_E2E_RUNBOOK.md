# Gate 10-B1 — Local Real WhatsApp / WUZAPI E2E Execution Runbook

> **GOVERNANCE STATUS**:
> **P1–P8 — PHYSICAL CLEAN REHEARSAL COMPLETE / PROVEN E2E / REHEARSAL CLOSURE AUDITED**
> **P9 — AUTHORIZATION HOLD (FINAL CLEANUP / SESSION TEARDOWN REQUIRES SEPARATE AUTHORIZATION)**

---

## Executive Summary

Gate 10-B1 validates the application's real messaging integration locally using:
- A real WUZAPI instance running in local Docker (built from exact pinned commit `9487eca9a40f292d19953a44983979c85d91ccce`);
- A dedicated WhatsApp test number owned by the tester;
- Real Gemini document extraction (bounded to max 5 physical calls; exactly 2 real calls executed across clean rehearsal history);
- Local disposable PostgreSQL databases (`g10b1_postgres`, `platform_g10b1_clean`).

All staging, VPS, Dokploy, Traefik edge, and production infrastructure remain **NOT AUTHORIZED**.

---

## Phase Breakdown & Checkpoint Authorization Matrix

| Phase | Description | Status | Technical Requirement / Command |
|---|---|---|---|
| **P1** | Offline Harness & Preflight Validation | **COMPLETE** | `python scripts/operations/gate10_b1_e2e_runner.py preflight` |
| **P2** | Stack Preparation & Startup | **COMPLETE** | `$env:G10_B1_AUTHORIZED_PHASE="P2"`; `prepare-wuzapi`, `up`, `bootstrap` |
| **P3** | Human QR Code Scan & Session Auth | **COMPLETE** | Operator scanned QR code via `http://127.0.0.1:18080/session/qr` using physical test SIM |
| **P4** | Real Inbound/Outbound Text Smoke Test | **COMPLETE** | Operator sent test message; verified WUZAPI and Orchestrator routing |
| **P5** | Real Media + Gemini Expense Happy Path | **COMPLETE** | Ingestion & physical extraction verified (2 Gemini calls executed) |
| **P6** | Clarification Loops & `/empreendimento` | **COMPLETE** | Synthetic enterprise created & chat binding established via WhatsApp `/empreendimento` |
| **P7** | FIFO Burst, Duplicate Replay, Worker Recovery | **COMPLETE** | Closure audited; isolation from FIFO preserved |
| **P8** | Business Persistence, DB Writer & Final ACK | **COMPLETE** | Direction resolved (`2` -> `expense`), Writer `COMMITTED`, `write_ledger` (1), `financial_records` (1), `COMPLETED`, final WhatsApp ACK verified |
| **P9** | Final Cleanup & Session Teardown | **HOLD** | `python scripts/operations/gate10_b1_e2e_runner.py down` (or `cleanup` for volume removal) — requires explicit user authorization |

---

## Runner Command Semantics

- `down`: Stops `g10b1_` containers and networks while **PRESERVING** session and data volumes (`g10b1_wuzapi_data`, `g10b1_postgres_data`). Safe for restart tests without losing QR login.
- `cleanup`: Destructively stops containers and removes owned volumes (`docker compose -p g10b1 down -v`).
- `prepare-wuzapi`: Clones `asternic/wuzapi` commit `9487eca9a40f292d19953a44983979c85d91ccce` and builds local image `g10b1-wuzapi:9487eca9a40f292d19953a44983979c85d91ccce`.

---

## Technical Phase Authorization Guard

Commands modifying Docker stack state or sending network traffic require explicit phase authorization:

```powershell
$env:G10_B1_AUTHORIZED_PHASE="P2"
```

Without this variable set, stack commands exit immediately with `PHASE_NOT_AUTHORIZED`.

---

## P1 — Offline Harness Validation Commands

### 1. Run Preflight Check
```powershell
python scripts/operations/gate10_b1_e2e_runner.py preflight
```

### 2. Run Opt-In Harness Invariant Unit Tests
```powershell
$env:G10_B1_REAL_E2E="1"
uv run pytest tests/test_platform_gate10_b1_real_e2e.py -v
```

---

## Environment & Secret Reference (`.env.g10b1.local`)

Secrets are supplied strictly via an ignored local environment file (`.env.g10b1.local`):

```ini
G10_B1_WUZAPI_TOKEN=secret_wuzapi_admin_token_g10b1
G10_B1_WUZAPI_WEBHOOK_SECRET=secret_wuzapi_webhook_secret_g10b1
G10_B1_TEST_WHATSAPP_NUMBER=5511999999999
G10_B1_GEMINI_API_KEY=AIzaSy...
```

> [!CAUTION]
> Never commit `.env.g10b1.local` or print secret values in logs or reports. `.gitignore` explicitly excludes `.env.g10b1.local`.

---

## Gemini Call Boundary

- **Max Physical Gemini Calls Contract**: **5** across all P5–P7 test scenarios.
- **P1 Gemini Calls**: **0** (Offline validation only).
