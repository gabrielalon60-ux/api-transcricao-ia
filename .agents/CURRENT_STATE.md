# Current Project State

> Convenience summary only. `TASKS_TESTS_GATES.md` is the operational source of truth.

## Current Gate
**Gate 2 — Orquestrador / Cadastro**

## Last User-Approved Gate
**Gate 1 — Fundação (Approved on 2026-07-31)**

The standalone Transcription IA 1.0 was validated before this platform initiative, but the new architecture begins at Gate 0.

## Current Status
- Gate 1 approved by user.
- Proceeding with Gate 2 (Orquestrador / Cadastro).
- Preparing implementation plan for webhook integrations and registration endpoints.

## Current Architecture

```text
WhatsApp
  ↓
WUZAPI
  ↓
Orchestrator
  ↓
BOT DF Holding
  ├── Transcription Service
  └── Database Writer
          ↓
      DF database
```

Platform PostgreSQL owns platform routing/configuration state, persistent FIFO queue, audit, execution history and usage.

## Key Invariants

- One WUZAPI instance maps to exactly one BOT.
- Organization is identified by the receiving instance/number.
- Sender must be authorized.
- Phase 1 registration uses `/cadastro <fixed secret>`.
- Images/PDFs are transcribed before entering the business queue.
- Original media is discarded after extraction succeeds or definitively fails.
- Only extracted/normalized data waits in the queue.
- FIFO business processing is per conversation.
- Extraction may run concurrently within configured limits.
- Only one active interaction per conversation.
- Interaction TTL is 1 hour.
- Database Writer is the only service with DF database credentials.
- No content-level document deduplication in Phase 1.
- Webhook replay must be idempotent.

## Known TBDs

1. Final DF Holding destination database schema.
2. Real CPF/CNPJ list for DF Holding.
3. Final low-confidence/quality decision mechanism.
4. Exact WUZAPI payload fields for deployed version.
5. WUZAPI media retention behavior and cleanup policy.
6. Operational values for max file size, transcription concurrency, queue limit and rate limits.

## Current Blockers

No blocker prevents Gate 0 review.

Database Writer final implementation is blocked until the DF destination schema is defined.

Production release remains blocked until real CPF/CNPJ configuration and Security Gate requirements are complete.

## Recommended Next Action
Create the implementation plan for Gate 2 (Orquestrador / Cadastro) and obtain user review.
