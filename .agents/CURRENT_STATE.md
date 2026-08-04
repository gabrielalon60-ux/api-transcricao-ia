# Current Project State

> Convenience summary only. `TASKS_TESTS_GATES.md` is the operational source of truth.

## Current Gate
**Gate 4 — Fila persistente (NOT STARTED)**

## Last User-Approved Gate
**Gate 3 — Transcrição (Approved on 2026-08-04, America/Sao_Paulo)**

The standalone Transcription IA 1.0 was validated before this platform initiative, but the new architecture begins at Gate 0.

## Current Status
- Gate 3 — Transcription was explicitly approved by the user on 2026-08-04 at 00:20:08 -03:00 (America/Sao_Paulo), based on the formal application review result `REVIEW PASSED WITH FOLLOW-UPS`.
- Gate 3 application implementation: APPROVED.
- Gate 3: COMPLETE.
- Production deployment: NOT PERFORMED.
- Production database adoption: NOT PERFORMED.
- WUZAPI production original-media retention verification: PENDING OPERATIONAL FOLLOW-UP outside Gate 3 application completion.
- Gate 4: NOT STARTED.
- Gate 2 approved by user.
- Gate 3 (Transcrição) official technical plan consolidated into single authoritative document: [.agents/IMPLEMENTATION_PLAN_GATE_3.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/IMPLEMENTATION_PLAN_GATE_3.md).
- Schema mapping resolved (`.agents/transcription_schema_mapping.md`).
- ORM model implementation completed and locally validated.
- Alembic architecture approved (dedicated Transcription environment with `alembic_version_transcription`).
- Gate 3 migration source files created and source-validated: dedicated Transcription Alembic environment, baseline revision, Gate 3 revision, read-only Profile A/Profile B/post-state verifiers, explicit Profile B reconciliation source, and isolated source tests.
- Gate 3 migration sources validated against disposable PostgreSQL 15 infrastructure only (`localhost:55432`, database `transcription_gate3_test`, container `transcription_gate3_migration_test`, volume `transcription_gate3_migration_test_data`). Disposable infrastructure was removed after validation.
- Validation covered fresh database migration, historical baseline, canonical baseline-to-Gate-3 upgrade with legacy data, Profile A adoption, Profile B reconciliation/adoption, unsupported drift rejection, enum ordering, cost conversion, attempt-number backfill, and uniqueness replacement.
- Preserved PostgreSQL Profile B adoption runbook and evidence template prepared and safety-audited: `.agents/GATE_3_PROFILE_B_ADOPTION_RUNBOOK.md` and `.agents/GATE_3_PROFILE_B_ADOPTION_EVIDENCE_TEMPLATE.md`.
- No physical migration, stamp, downgrade, online upgrade, or Profile B reconciliation has been executed against the preserved database.
- Physical database not modified; previous read-only inspection confirmed Profile B drift.
- Gate 3 application implementation is complete and approved. Current source includes the internal `/internal/extract` route, internal Bearer token dependency, metadata schemas, document validation service, two-transaction internal extraction service, provider attempt logging support, declared Transcription DB configuration, and removal of startup `Base.metadata.create_all()`.
- Latest local verification for application work: compileall PASS; Ruff PASS for Transcription source and focused Gate 3 test; mypy PASS for touched Gate 3 source files; focused Gate 3 application/source tests PASS (37 passed); safe existing suite PASS excluding destructive PostgreSQL migration integration (61 passed).
- Second-pass isolated Supabase integration used only `.env.gate3.local:GATE3_PRESERVED_DATABASE_URL`, sanitized target `db.btdkssnuwdtjnmcpfxjm.supabase.co:5432/postgres`, confirmed `alembic_version_transcription = gate3_schema`, inserted 3 test request UUID rows with a fake provider, created 1 extraction row and 4 usage rows, then removed all 3 request IDs with 0 remaining request rows.
- Third-pass clarification recorded the approved current internal metadata contract (`request_id`, `bot_instance_id`, `correlation_id`, `received_at`, `source=WHATSAPP`) in the Gate 3 plan and schema mapping, superseding older `event_id`/`organization_id`/`instance_id`/`user_id` references for the current route contract.
- Third-pass verification added legacy `/extract` HTTP regression coverage and startup/config safety checks. Latest local verification: compileall PASS; Ruff PASS; mypy PASS for touched Gate 3 modules; focused Gate 3 + migration-source tests PASS (42 passed); safe full suite PASS excluding destructive PostgreSQL migration integration (66 passed).
- Third-pass isolated Supabase integration PASS: sanitized target `db.btdkssnuwdtjnmcpfxjm.supabase.co:5432/postgres`, `alembic_version_transcription = gate3_schema`, initial table counts all 0, Transaction A visibility observed `PROCESSING` with no extraction before provider release, same-ID race returned `[200, 409]` with exactly 1 provider call, physical replays for `PROCESSING`, `FAILED`, `PERSISTENCE_FAILED`, and `SUCCEEDED` performed with 0 provider calls, inserted scoped counts `(requests=6, extractions=3, usage_logs=2)`, deleted `(requests=6, extractions=3, usage_logs=2)`, final scoped counts 0 and final total table counts all 0.
- Fourth-pass Transaction B/compensation defect fixed: compensation now uses a separate SQLAlchemy session/transaction and Transaction B explicitly flushes before commit. FAILED replay now maps from persisted `error_code` to HTTP status and retryability.
- Fourth-pass isolated Supabase Transaction B evidence PASS using fake providers and UUID-scoped rows: deterministic pre-commit failures for success extraction insert, success usage insert, success flush, handled-failure usage insert, and handled-failure flush all rolled back Transaction B with no extraction/usage remnants and compensation committed `PERSISTENCE_FAILED`. Compensation flush/commit failures left physical status `PROCESSING` with no extraction/usage remnants. FAILED replay reconstruction covered `UNSUPPORTED_FILE_TYPE`, `PROVIDER_TEMPORARY_ERROR`, `INTERNAL_ERROR`, and `PERSISTENCE_ERROR` with zero provider calls. Scoped inserts before cleanup `(requests=11, extractions=0, usage_logs=0)`; cleanup removed 11 requests and final total table counts were all 0.
- Fourth-pass sanitization fix: compensation failure logging no longer emits tracebacks. Rechecked compensation failure against Supabase: response 500 `PERSISTENCE_ERROR`, physical status `PROCESSING`, no extraction/usage rows, scoped cleanup complete.
- Fifth-pass document-validation acceptance work is complete within the narrow scope: prompt loading now uses the package-relative prompt by default, supports explicit `SYSTEM_PROMPT_PATH`, includes `prompts/*.md` as Transcription package data, and missing-prompt errors do not expose local absolute paths. PDF active-content detection now relies on structured object traversal instead of raw byte markers, so harmless text containing `/JS` is allowed while structural `/OpenAction`, `/AA`, `/JavaScript`, `/JS`, `/Launch`, and `/EmbeddedFiles` entries are rejected. Subprocess validation now rejects malformed IPC payloads. Latest verification: compileall PASS; Ruff PASS; mypy PASS; focused Gate 3 internal extraction suite PASS (46 passed); full safe suite PASS (77 passed, 12 skipped). Supabase disposable test fixture PASS on sanitized target `db.btdkssnuwdtjnmcpfxjm.supabase.co:5432/postgres`: four prompt-defined document types (`pix_receipt`, `commercial_document`, `invoice`, `bank_receipt`) persisted as `succeeded` with fake providers, scoped cleanup returned owned row counts to 0, and no non-owned table set changed. Source mismatch remains: the Gate 3 plan mentions six business categories, while the authoritative central prompt currently defines four extraction schemas plus `unknown`; no additional fixture types were invented.
- Document-type reconciliation review found no repository or Git-history evidence of two additional fully defined runtime schemas. The six business categories are named in PRD/Gate planning/tracker as Nota fiscal, Comprovante PIX, Boleto, Cupom fiscal, Pedido, and Orçamento. All current and historical prompt files found define only four technical extraction labels (`pix_receipt`, `commercial_document`, `invoice`, `bank_receipt`) plus fallback `unknown`. Evidence suggests `commercial_document` intentionally covers Pedido/Orçamento-style documents and `bank_receipt` covers Boleto-style receipts, while Cupom fiscal has no explicit current prompt schema or label. Because PRD/tracker still name six business categories, this is an unresolved product/contract decision, not a safe documentation-only correction to four labels.
- Gate 3 document-type contract resolved by explicit product decision: six supported business categories are represented by four runtime labels. Mapping is Nota fiscal -> `invoice`; Cupom fiscal -> `invoice`; Comprovante PIX -> `pix_receipt`; Boleto -> `bank_receipt`; Pedido -> `commercial_document`; Orçamento -> `commercial_document`; `unknown` is fallback-only. Prompt text was updated to make this mapping explicit without adding labels or changing JSON response shapes. Six separate fake-provider business acceptance fixtures now pass locally and against the isolated Supabase test database. Latest verification: compileall PASS; Ruff PASS; mypy PASS; document/prompt/PDF/subprocess/temp-cleanup slice PASS (14 passed); focused Gate 3 internal extraction suite PASS (52 passed); migration-source tests PASS (7 passed); safe full suite PASS (83 passed, 12 skipped). Supabase scoped fixture evidence on sanitized target `db.btdkssnuwdtjnmcpfxjm.supabase.co:5432/postgres`: inserted `(requests=6, extractions=6, usage_logs=6)`, replayed all six with provider call count remaining 1 per fixture, deleted `(requests=6, extractions=6, usage_logs=6)`, and final counts returned to `(requests=0, extractions=0, usage_logs=0)`.
- Gate 3 prompt-size blocker resolved by explicit product decision and implementation: `MAX_SYSTEM_PROMPT_SIZE_BYTES=262144` raw bytes (256 KiB), inclusive boundary, strict UTF-8, empty/whitespace-only invalid, startup validation required, shared runtime defensive validation required, validated prompt cached once per process, restart required for prompt/path/size changes. Startup validation logs only sanitized `SYSTEM_PROMPT_INVALID`, source classification, and reason category; it does not create schema or require DB connectivity. Runtime defensive failure maps to `SYSTEM_PROMPT_INVALID`, HTTP 503, `retryable=false`; `/internal/extract` preserves Transaction A and persists terminal `FAILED` with `error_code=SYSTEM_PROMPT_INVALID` if the prompt becomes invalid after Transaction A. Latest verification after prompt implementation: compileall PASS; Ruff PASS; mypy PASS; focused prompt/fixture/lifespan slice PASS (13 passed); focused Gate 3 internal extraction suite PASS (58 passed); migration-source tests PASS (7 passed); safe full suite PASS (89 passed, 12 skipped). No Supabase or Gemini interaction in this pass.
- Final Gate 3 application-readiness reconciliation completed. G3-T13 wording was corrected by explicit authority-resolution: `importlib.resources` is not frozen; the requirement is package-aware prompt loading independent of repository CWD, package-data inclusion, explicit prompt path support, startup/runtime shared validation, and no repository-relative dependency. G3-T13 is marked PASS. The Gate 3 one-worker policy is frozen because validation concurrency is process-local; Docker Compose now starts Transcription with explicit `uvicorn ... --workers 1`, and a static regression test verifies the committed command. G3-T37 is marked PASS. WUZAPI media retention remains external/production-operational: Transcription owns only its received bytes and validation temp files; original WUZAPI media retention must be verified/configured before production release, but it does not block Gate 3 application review. Final verification: compileall PASS; Ruff PASS; mypy PASS; focused Gate 3 internal extraction suite PASS (59 passed); migration-source tests PASS (7 passed); safe full suite PASS (90 passed, 12 skipped). The 12 skipped tests are disposable PostgreSQL migration validations requiring `GATE3_DISPOSABLE_DATABASE_URL`; prior approved disposable PostgreSQL evidence covers them. No Supabase or Gemini interaction occurred in this final pass. Diff/secret inspection found no committed secret values.

## Open integration TBDs:
- TBD-WUZAPI-HMAC-ENCODING
- TBD-WUZAPI-UNKNOWN-INSTANCE-OUTBOUND

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

No blocker prevents Gate 3 closure; Gate 3 is complete.

Database Writer final implementation is blocked until the DF destination schema is defined.

Production release remains blocked until real CPF/CNPJ configuration and Security Gate requirements are complete.

## Recommended Next Action
Prepare a clean Gate 3 commit from the reconciled worktree. Do not start Gate 4 until the user separately authorizes Gate 4 work.
