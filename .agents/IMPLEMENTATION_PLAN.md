# Implementation Plan – Transcription Alembic Architecture (Gate 3)

## Status
Gate 3 — Transcription is formally APPROVED and COMPLETE by explicit user approval on 2026-08-04 (America/Sao_Paulo), after formal application review result `REVIEW PASSED WITH FOLLOW-UPS`. The authoritative Gate 3 plan remains `.agents/IMPLEMENTATION_PLAN_GATE_3.md`.

Migration source files now exist for the dedicated Transcription Alembic environment, Version 1.0 baseline, Gate 3 schema migration, verifiers, explicit Profile B reconciliation, and isolated source tests. They have passed disposable PostgreSQL 15 validation and formal application review. No production/preserved-database migration, stamp, downgrade, or reconciliation has been executed.

## Goal
Create a dedicated Alembic migration environment for the Transcription service, including configuration files, environment script, and two migrations:
1. `transcription_1_0_baseline` – baseline reflecting the original Version 1.0 ORM schema.
2. `gate3_schema` – deterministic migration matching the approved Gate 3 ORM model.

The work must **not** modify the database, run any migrations, or touch other services.

---

## User Review Required
> [!IMPORTANT]
> Verify that the proposed file locations and names conform to your repository conventions. If you prefer alternative paths or filenames, let me know before proceeding.

---

## Open Questions
- **Migration filenames**: Should the migration script names include a timestamp prefix (e.g., `20230801_01_transcription_1_0_baseline.py`), or follow Alembic's default auto‑generated naming?
- **Version table name**: The plan uses `alembic_version_transcription`. Confirm this is the exact name you want, or propose a different one.
- **Baseline content**: Do you want the baseline migration to contain explicit `op.create_table` statements for **all** transcription tables, or rely on `Base.metadata.create_all()` via a `declare_model` step? (Both are supported.)

---

## Proposed Changes
### 1. Configuration
- **[NEW]** `apps/transcription/alembic.ini`
- **[NEW]** `apps/transcription/alembic/` directory with `env.py` and `script.py.mako` (copied from platform Alembic but adjusted).

### 2. Environment script (`env.py`)
- Set `version_table = "alembic_version_transcription"` for both online and offline contexts.
- Import the transcription `Base` metadata to autogenerate migrations.

### 3. Baseline migration
- **[NEW]** `apps/transcription/alembic/versions/<revision_id>_transcription_1_0_baseline.py`
- Contains `upgrade()` that creates all tables/constraints as they existed in the original 1.0 model.
- `downgrade()` drops those tables.

### 4. Gate 3 migration
- **[NEW]** `apps/transcription/alembic/versions/<revision_id>_gate3_schema.py`
- Implements the deterministic changes introduced in Gate 3 (e.g., composite `UniqueConstraint` on `usage_logs`, enum additions, column defaults).

---

## Verification Plan
### Automated Tests
- Run `python -m compileall apps/transcription/alembic` to ensure syntax validity.
- Execute `alembic -c apps/transcription/alembic.ini heads --verbose` to confirm the revision graph is correct.
- Run `alembic -c apps/transcription/alembic.ini history --verbose` and inspect the output.

### Manual Verification
- The user (or CI) can run a dry‑run upgrade on a fresh SQLite DB: `alembic -c apps/transcription/alembic.ini upgrade head --sql` to view generated SQL.

---

*Source files have been created for review. Database adoption/migration remains a separate approval step.*


## GATE 0 — Congelamento do desenho

**Entregas:** PRD, ADR, contratos v1, estados, autenticação interna, estratégia migrations, ambientes.

**Aprovação:** nenhuma implementação de negócio antes de contratos/limites de responsabilidade estarem coerentes.

## FASE/GATE 1 — Fundação

Implementar:

- monorepo;
- quatro apps;
- packages `contracts/security/observability`;
- Platform PostgreSQL;
- migrations;
- models iniciais;
- health/readiness;
- correlation middleware;
- logs estruturados;
- Compose local.

Gate: clone limpo sobe, migrations passam, DB persiste, sem secrets.

## FASE/GATE 2 — Orquestrador / identidade / cadastro

Implementar:

- webhook WUZAPI;
- auth/assinatura;
- normalização;
- idempotência;
- instance→organization→bot;
- users;
- `/cadastro`;
- hash/HMAC;
- rate limit;
- outbound WUZAPI.

Gate: desconhecido não chama IA; cadastro/roteamento/replay corretos.

## FASE/GATE 3 — Transcrição IA

Adaptar 1.0 para serviço interno:

- contrato;
- auth;
- validação image/PDF;
- seis tipos de documento;
- normalized_data;
- flags de qualidade;
- usage/tokens;
- timeout/retries;
- cleanup;
- SHA-256;
- teste WUZAPI retention.

Gate: fixtures dos seis tipos, cleanup, tokens, falhas controladas.

## FASE/GATE 4 — Extração paralela + fila FIFO

Implementar:

- processing_items;
- sequence transacional;
- estados;
- concorrência IA;
- READY;
- worker FIFO;
- um ACTIVE por conversa;
- lock transacional;
- limite de fila;
- recovery após restart.

Gate obrigatório: cinco arquivos, extração fora de ordem, execução 1→5, sem concorrência indevida.

## FASE/GATE 5 — BOT DF / regras financeiras

Implementar:

- máquina de estados;
- amount;
- date/fallback;
- lista CPF/CNPJ;
- payer/receiver;
- expense/income/unknown/ambiguous;
- mensagem final.

Gate: regras determinísticas passam e item completo não pede confirmação.

## FASE/GATE 6 — Interação

Implementar:

- WAITING_USER_INPUT;
- pergunta direction;
- pergunta amount;
- parse resposta;
- TTL 1h;
- expiração;
- novo arquivo durante espera;
- continuação automática.

Gate: terceiro de cinco pausa, resolve/expira e fila continua.

## FASE/GATE 7 — Database Writer

Pré-requisito: schema DF.

Implementar:

- contrato;
- secret só no Writer;
- TLS;
- usuário mínimo;
- transação;
- idempotency key;
- timeout/retries;
- sanitização.

Gate: retry não duplica e credencial não vaza.

## FASE/GATE 8 — E2E

Integrar fluxo completo e mensagens finais.

Gate: celular real → DB DF → resposta real, auditável por correlation ID.

## FASE/GATE 9 — Operação / observabilidade

Implementar:

- executions;
- service_usage;
- custos/tokens;
- métricas;
- backup/restore;
- runbooks.

Gate: investigar erro e restaurar banco em ambiente limpo.

## FASE/GATE 10 — Segurança / Release Candidate

P0:

- HTTPS;
- fechar serviços internos;
- restringir WUZAPI admin/SSH;
- webhook HMAC;
- rate limits;
- secrets novos;
- DB Platform privado;
- DB DF TLS/min privilege;
- upload validation;
- logs sanitizados;
- versões pinadas;
- dependências auditadas;
- hardening de containers;
- backup/restore;
- rollback.

Gate: staging E2E + testes de segurança + restore + checklist de produção.

---

# 20. Estratégia de deploy

## Local
Docker Compose + PostgreSQL local.

## Staging
Topologia equivalente à produção, com WhatsApp/DB/secrets de teste.

## Produção — Dokploy/VPS

```text
Traefik / HTTPS
       │
       ├── WUZAPI (admin restrito)
       └── edge necessário

Rede interna:
Orchestrator
Bot DF
Transcription
Database Writer
Platform PostgreSQL
```

- migrations forward-only em produção;
- feature flags para regras futuras;
- cada gate gera checkpoint/tag;
- rollback documentado.
