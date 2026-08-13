# Gate 7 — Formal Implementation Plan

> **Status**: APPROVED / IMPLEMENTED / VERIFIED / COMPLETE
> **Gate 4**: APPROVED / COMPLETE / FROZEN
> **Gate 5**: APPROVED / COMPLETE / PUSHED
> **Gate 6**: APPROVED / COMPLETE / PUSHED
> **Gate 7**: APPROVED / COMPLETE
> **G7-APPROVED**: true
> **Gate 8**: NOT STARTED
> **Migrations**: CREATED / EXECUTED IN DISPOSABLE POSTGRESQL 15 VERIFICATION ONLY
> **Persistent, staging, production, or remote DB execution**: NOT AUTHORIZED

## 1. Objective and authority

Implement the local PostgreSQL 15 expense-only destination after a separate explicit HOLD approval. Gate 7 adds an early income destination guard, enterprise resolution, Writer HTTP v2, a Writer-owned local DF adapter, and same-transaction idempotent expense persistence. It does not modify the frozen Gate 5 evaluator, reinterpret Gate 6 answer history, deliver final user outcomes, or claim production readiness.

This plan is authoritative with `.agents/PRD.md`, `.agents/TASKS_TESTS_GATES.md`, and `.agents/GATE_7_CONTRACT_CLOSURE.md`. A conflict stops implementation for governance review.

## 2. Phase boundaries

### Phase A — local MVP

Executable only after implementation authorization and only against disposable PostgreSQL 15. It comprises:

1. additive Platform migration and ORM state;
2. early `income` guard and FIFO/recovery closure;
3. enterprise listing, command selection, binding, and per-document selection;
4. additive local DF migration and least-privilege fixture;
5. Writer v2 request, local adapter, supplier lookup, transaction, and ledger;
6. focused, regression, static, and full-suite verification.

### Phase B — production-client adaptation

Blocked on the client's enterprise DDL/PK/types/FKs/RLS/grants, financial/supplier object adoption inputs, ledger decision, approved hostname/CA delivery, and runtime grants. Phase B maps the adapter and deployment artifacts; it does not change the Phase A business contract. No production DDL or execution is authorized.

## 3. Expense-only destination guard

### 3.1 Exact integration point

The smallest safe integration point is the BOT DF coordinator in `apps/orchestrator/src/orchestrator/fifo_worker.py::_process_validating_item`, immediately after the owned `VALIDATING` evaluation materializes effective direction and before either `dispatch_user_prompt` or `transition_validating_to_persisting`.

`apps/orchestrator/src/orchestrator/services/fifo_worker_service.py::evaluate_and_persist_validating_item` continues to consume frozen Gate 5 output plus durable Gate 6 APPLIED answers. Gate 7 changes only decision composition: after effective direction is chosen, it reports the destination outcome before constructing amount requirements. It does not change `BusinessRulesEvaluatorService`.

Ordering:

```text
direction unresolved -> existing transaction_direction clarification
direction = income    -> atomic IGNORED / INCOME_OUT_OF_SCOPE
direction = expense   -> amount requirement -> enterprise requirement -> Writer
```

Known income cannot produce `transaction_amount` or `enterprise_selection`. No facts are requested for an unsupported destination.

### 3.2 Atomic transition

Add a service operation `ignore_income_out_of_scope` that locks the row and verifies `status=VALIDATING`, current worker ownership, live lease, and effective `direction=income`. In one Platform transaction it:

- sets `status=IGNORED` and `outcome_reason=INCOME_OUT_OF_SCOPE`;
- sets `completed_at` and clears `claimed_by`, `heartbeat_at`, and `lease_expires_at`;
- leaves persistence identity/status/counters unset and creates no interaction;
- inserts one idempotent successful execution checkpoint `INCOME_OUT_OF_SCOPE`, with no error code;
- commits before the worker removes its in-memory claim.

A repeat sees the terminal pair and returns it without another checkpoint or side effect.

## 4. `IGNORED` state and durable reason

Existing `ProcessingItem.error_code` is failure-only and `external_operation_status` is Writer-only. Neither is appropriate. Add nullable `ProcessingItem.outcome_reason` and enforce:

```text
(status = 'IGNORED' AND outcome_reason = 'INCOME_OUT_OF_SCOPE')
OR
(status <> 'IGNORED' AND outcome_reason IS NULL)
```

Add `IGNORED` to the status CHECK, ORM terminal collection, queue capacity predicate, FIFO earlier-item terminal predicate, and terminal documentation. Keep it out of the active partial unique index, lease recovery index, blocking states, claim statuses, cancellation candidates, interaction candidates, persistence states, retry states, and reconciliation states.

Consequences:

- sequence 3 `IGNORED` does not block READY sequence 4;
- READY/resume claims cannot select it;
- ACTIVE/VALIDATING stale recovery cannot reopen it;
- startup claim recovery cannot track it;
- replay returns durable terminal state without re-evaluation;
- cancellation and waiting-input sweepers do not select it;
- Writer dispatch/retry/reconciliation cannot select it;
- Gate 7 sends no final success or informational notification.

## 5. Enterprise support

### 5.1 Identity and precedence

Supported chat identity is `(organization_id, instance_id, user_id)` for 1:1 conversations. Groups remain out of scope.

```text
valid persistent binding -> materialize enterprise_id on item
otherwise APPLIED document answer -> materialize only on item
otherwise enterprise_selection -> WAITING_USER_INPUT
unresolved enterprise -> zero Writer POST
```

`ProcessingItem.enterprise_id` is a nullable string external destination identifier in Platform DB; there is no cross-database FK. It becomes mandatory for expense Writer v2 dispatch.

### 5.2 `/empreendimento`

Intercept the exact command at the authenticated/routed Orchestrator command boundary before document ingestion. The Orchestrator obtains a read-only enterprise list through an authenticated Writer endpoint; only Writer owns `DF_DATABASE_URL`.

Sort deterministically by normalized display name then stable enterprise ID. Persist the exact ordered `position -> {enterprise_id, display_name}` snapshot before outbound dispatch. Always append `N+1 -> Limpar seleção`. The user answer is interpreted only against that stored generation.

A dedicated `EnterpriseCommandSession` is required because frozen `UserInteraction` requires a ProcessingItem. It stores conversation identity, generation, status, option snapshot, clear position, outbound message identity, waiting/resolution timestamps, and one-hour expiry. A unique partial index allows one open command session per conversation. Replay reuses the generation; selecting an enterprise idempotently upserts the binding; selecting `N+1` idempotently deletes it. Expiration/cancellation leaves the previous binding unchanged. It has no ProcessingItem or sequence mutation, but an OPEN session is a temporary same-conversation business-interaction barrier.

### 5.3 Cross-protocol interaction serialization

The authoritative invariant is at most one active human interaction per conversation across both protocols:

```text
OPEN UserInteraction XOR OPEN EnterpriseCommandSession
```

Use the existing `conversation_queue_counters` row, keyed exactly by `(organization_id, instance_id, user_id)`, as the shared transaction lock authority. `lock_or_create_conversation_counter` already performs `INSERT ... ON CONFLICT DO NOTHING` followed by `SELECT ... FOR UPDATE`, so `/empreendimento` also works before the first document without allocating or changing a sequence.

Both `dispatch_user_prompt` Boundary 1 and `open_enterprise_command_session` must acquire this row lock before checking either table and before inserting an OPEN owner. Under the same transaction:

- prompt creation rejects/defer-resumes when an OPEN command session exists;
- command creation returns the busy outcome when an OPEN document interaction exists;
- each path checks/replays its own existing generation before creating another;
- commit releases the lock only after the chosen OPEN owner is durable.

The existing per-item UserInteraction partial index and proposed per-conversation command-session partial index remain necessary defense-in-depth for same-protocol races. They are insufficient alone because PostgreSQL unique indexes cannot express mutual exclusion across two tables. The shared row lock serializes the cross-table check-and-insert under concurrent workers/processes.

The READY candidate query adds `NOT EXISTS` for OPEN command sessions, and its inline revalidation acquires the conversation counter lock and repeats that check before `READY -> ACTIVE`. If the command is already OPEN, the item remains READY with its sequence and attempt count unchanged. Other conversations remain claimable. If a command wins after a document was claimed but before a prompt is reserved, prompt Boundary 1 must not create a UserInteraction: it writes an idempotent `ENTERPRISE_COMMAND_BARRIER_DEFERRED` checkpoint, clears the business claim while preserving `VALIDATING`, sequence, and attempt count, and becomes eligible through an additive resume-claim predicate only after no OPEN command session exists. This closes the polling race without changing Gate 6 answer provenance.

Closing a command as `ANSWERED`, `EXPIRED`, or `CANCELLED` removes the barrier. The earliest READY item, or a barrier-deferred VALIDATING item, resumes under existing FIFO order. A selected enterprise binding is observed at evaluation; a cleared binding causes normal per-document fallback. Document TTL starts only when a document prompt is actually dispatched, so it does not run while the command barrier prevents dispatch.

### 5.4 Persistent binding

`WhatsappChatEnterpriseBinding` has the conversation tuple as a unique key, an `enterprise_id`, timestamps, and audit source/session identity. The Platform owns it. Enterprise validity is read-only; neither Orchestrator nor Writer may mutate master data. A document-specific answer never changes this binding.

### 5.5 Per-document selection

Extend `UserInteraction.question_type` with `enterprise_selection` and add nullable JSONB `option_mapping`. Store a deterministic enterprise snapshot before dispatch. Existing one-hour TTL, WAITING FIFO blocking, cancellation, expiration, outbound-unknown, durable APPLIED answer, atomic resume claim, and generation-aware stale recovery apply. The parser stores the selected real ID and materializes only `ProcessingItem.enterprise_id`; it does not upsert a binding.

### 5.6 Inbound routing and command lifecycle

Deterministic text routing order:

1. parse recognized commands before generic answers;
2. `/empreendimento`: under the shared counter lock, return the busy response if a UserInteraction is OPEN, otherwise open/replay the command session;
3. non-command text: route only to an OPEN command session when present, otherwise only to an OPEN UserInteraction, otherwise use existing unsupported/free-text behavior.

Therefore `/empreendimento` can never become `INVALID_AMOUNT_FORMAT` or `INVALID_DIRECTION_CHOICE`, and a numeric text can be consumed by only one protocol.

When a document interaction is OPEN, `/empreendimento` creates no session, generation, TTL change, cancellation, answer, or ProcessingItem mutation. Freeze this exact response:

```text
⚠️ Existe um lançamento aguardando sua resposta.

Conclua a pergunta atual antes de alterar o empreendimento deste chat.
```

Its deterministic outbound identity is derived from the inbound Event ID. Before sending, commit one `ENTERPRISE_COMMAND_BUSY_RESPONSE_DISPATCHED` Execution with `operation_idempotency_key = <event_id>:ENTERPRISE_COMMAND_BUSY_RESPONSE`; duplicate webhook delivery reuses the Event and checkpoint and performs no blind resend. An ambiguous send is recorded against that same identity and does not create an interactive generation.

Command sessions use `RESERVED`, `WAITING`, `OUTBOUND_OUTCOME_UNKNOWN`, `ANSWERED`, `EXPIRED`, and `CANCELLED`; durable `COMMAND_LIST_DISPATCHED` is an Execution checkpoint rather than a session state. Reservation stores the stable mapping and outbound ID before dispatch; the dispatched checkpoint commits before WUZAPI; finalization moves to WAITING or OUTBOUND_OUTCOME_UNKNOWN. Recovery may dispatch only when no dispatched checkpoint exists. A dispatched/ambiguous generation is never regenerated or blindly resent, and a valid answer may resolve WAITING or OUTBOUND_OUTCOME_UNKNOWN.

Add `EnterpriseCommandAnswer` rather than reusing `UserAnswer`, whose ProcessingItem FK is semantically wrong. It has a unique `inbound_event_id`, session FK, sanitized input, parsing result, `APPLIED|REJECTED|LATE`, error code, and timestamps. Under the counter then session row locks: a valid in-range selection creates APPLIED and performs the binding UPSERT or clear DELETE in the same transaction; invalid/non-numeric/out-of-range input creates REJECTED while preserving the same session, mapping, and original TTL; an expired/closed session creates or returns LATE with no binding change or reopen. Duplicate same-event delivery returns the committed answer. Concurrent distinct answers serialize on the session; only the first valid answer can close it, so binding changes at most once.

## 6. Proposed Platform migration

Exact proposed file (not created by this planning task):

`packages/db/alembic/versions/b7c8d9e0f1a2_gate7_income_enterprise_platform.py`

Single additive revision from the current Platform head:

- add `processing_items.outcome_reason VARCHAR NULL`;
- replace `ck_processing_items_status_valid` with the same set plus `IGNORED`;
- add `ck_processing_items_ignored_reason_valid` for the exact state/reason pair;
- add `processing_items.enterprise_id VARCHAR NULL`;
- replace capacity partial-index predicate so `IGNORED` is terminal;
- extend `ck_user_interactions_question_type_valid` with `enterprise_selection`;
- add `user_interactions.option_mapping JSONB NULL` and a constraint requiring a non-empty object for enterprise selection;
- create `whatsapp_chat_enterprise_bindings` with unique conversation identity and audit timestamps/source;
- create `enterprise_command_sessions` with generation/status, exact JSONB mapping, clear position, outbound/inbound identities, TTL/resolution timestamps, uniqueness, validity CHECKs, and one-open-session partial index;
- create `enterprise_command_answers` with session FK, unique inbound Event identity, parsing result, `APPLIED|REJECTED|LATE` CHECKs, and answer timestamps;
- retain `conversation_queue_counters` as the shared cross-protocol `FOR UPDATE` authority; no new guard table/column is required and command locking must not increment `last_sequence`;
- downgrade removes only Gate 7 objects/columns/constraints after refusing unsafe data loss where rows exist.

No migration is created or executed until authorized.

## 7. Local DF MVP schema and migration

Exact proposed file (not created here):

`apps/db_writer/alembic/versions/b7c8d9e0f1a3_gate7_local_df_mvp.py`

It creates or additively adapts:

```text
enterprises(id UUID PK, name TEXT NOT NULL, address TEXT NULL, created_at, updated_at)
suppliers(id UUID PK, cnpj VARCHAR(14) NOT NULL UNIQUE, name, email, contact, created_at, updated_at)
financial_records(
  id UUID PK,
  transaction_date TIMESTAMPTZ NOT NULL,
  expense_type_id UUID NULL,
  enterprise_id UUID NOT NULL REFERENCES enterprises(id),
  amount NUMERIC(18,2) NOT NULL CHECK amount > 0,
  supplier_id UUID NULL REFERENCES suppliers(id),
  supplier_cnpj_snapshot VARCHAR(14) NULL,
  comments TEXT NULL,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  deleted_at TIMESTAMPTZ NULL,
  origin VARCHAR NOT NULL CHECK origin IN ('WHATSAPP','SITE'),
  processing_item_id VARCHAR NOT NULL UNIQUE,
  created_at,
  updated_at
)
```

The existing `write_ledger` is retained as the idempotency ledger and is extended only if v2 canonical request/record reference storage requires additive columns. The Gate 4 `df_business_records` adapter remains for v1 regression and is not silently renamed or treated as the MVP destination.

## 8. Writer HTTP v2

Preserve v1 behavior unchanged. Add explicit v2 schemas and routing:

```json
{
  "schema_version": "2.0",
  "idempotency_key": "stable ProcessingItem write identity",
  "processing_item_id": "platform item id",
  "correlation_id": "trace id",
  "organization_id": "tenant id",
  "payload": {
    "schema_version": "2.0",
    "direction": "expense",
    "amount": "1200.00",
    "transaction_date": "effective RFC3339 instant",
    "date_source": "DOCUMENT|MESSAGE_TIMESTAMP",
    "enterprise_id": "resolved destination id",
    "supplier_cnpj_snapshot": "14 digits or null",
    "origin": "WHATSAPP"
  }
}
```

Reject any v2 direction other than exact `expense`, missing/invalid enterprise, non-positive/non-canonical amount, invalid effective date/source, invalid CNPJ snapshot, wrong origin, or identity mismatch before DML. Responses preserve Gate 4 outcome vocabulary and return the authoritative record ID string on `COMMITTED`.

Add authenticated read-only `GET /internal/enterprises` for deterministic command/document choices and optional `GET /internal/enterprises/{id}` validation. It exposes only stable ID and display label needed by the Platform.

## 9. Writer adapter, supplier resolution, and transaction

Create a local adapter behind a protocol so HTTP handlers contain no table-specific SQL. For v2, one database transaction:

1. lock/read ledger by idempotency key;
2. return the prior matching outcome or reject payload mismatch;
3. validate enterprise exists read-only;
4. normalize supplier CNPJ to 14 digits when present;
5. lookup supplier read-only: one match sets `supplier_id`, zero sets NULL, multiple fail closed;
6. insert `financial_records` with approved defaults;
7. insert/finalize the ledger with canonical request hash and record ID;
8. commit once.

Any partial failure rolls back both business row and ledger. Ambiguous commit remains `OUTCOME_UNKNOWN` and is reconciled by the same key; no blind resend. Never create, edit, or delete suppliers or enterprises.

## 10. Security and operations

- `DF_DATABASE_URL` exists only in Database Writer configuration/environment; BOT and Orchestrator must not contain it.
- Production/staging contract requires TLS `verify-full`, matching certificate SAN, and an approved system/provider CA path.
- Connection/lock/statement timeouts remain 2s/1s/5s, below the frozen caller budget.
- Runtime role is non-owner/non-superuser: CONNECT/USAGE, SELECT suppliers/enterprises, INSERT/required SELECT on financial_records and ledger, required sequence usage only.
- Explicitly deny master-data DML, financial UPDATE/DELETE, DDL, TRUNCATE, role/database administration, bypass RLS, and unrelated schemas.
- Errors use stable codes and sanitized messages; no SQL, DSN, credentials, host details, or raw driver exception reaches response/logs.
- Local privilege verification uses disposable PostgreSQL only; no production role/grant is created.

## 11. Exact proposed source files

Modify:

- `packages/db/src/db/models.py`
- `apps/orchestrator/src/orchestrator/fifo_worker.py`
- `apps/orchestrator/src/orchestrator/services/fifo_worker_service.py`
- `apps/orchestrator/src/orchestrator/repositories/queue_repository.py`
- `apps/orchestrator/src/orchestrator/services/stale_recovery_service.py`
- `apps/orchestrator/src/orchestrator/services/user_interaction_service.py`
- `apps/orchestrator/src/orchestrator/services/waiting_input_sweeper.py`
- `apps/orchestrator/src/orchestrator/services/cancel_command_handler.py`
- `apps/orchestrator/src/orchestrator/services/persistence_service.py`
- `apps/orchestrator/src/orchestrator/db_writer_client.py`
- `apps/orchestrator/src/orchestrator/main.py`
- `apps/db_writer/src/db_writer/models.py`
- `apps/db_writer/src/db_writer/main.py`
- `apps/db_writer/src/db_writer/config.py`

Create:

- `apps/orchestrator/src/orchestrator/services/enterprise_resolution_service.py`
- `apps/orchestrator/src/orchestrator/services/enterprise_command_service.py`
- `apps/db_writer/src/db_writer/df_adapter.py`
- the two proposed migration files in sections 6 and 7.

No Gate 5 evaluator file, WuzapiClient implementation, Transcription source, dependency manifest, or Gate 8 notifier is in scope unless the HOLD explicitly revises this plan.

## 12. Exact proposed test files

Create:

- `tests/test_platform_gate7_income_guard_unit.py`
- `tests/test_platform_gate7_income_guard_disposable_postgres.py`
- `tests/test_platform_gate7_enterprise_unit.py`
- `tests/test_platform_gate7_enterprise_disposable_postgres.py`
- `tests/test_platform_gate7_db_writer_unit.py`
- `tests/test_platform_gate7_db_writer_disposable_postgres.py`
- `tests/test_platform_gate7_migrations.py`
- `tests/test_platform_gate7_security.py`
- `tests/test_platform_gate7_cross_protocol_interaction_unit.py`
- `tests/test_platform_gate7_cross_protocol_interaction_disposable_postgres.py`

Extend only for frozen regression assertions:

- `tests/test_platform_gate4_fifo_worker_unit.py`
- `tests/test_platform_gate4_fifo_worker_disposable_postgres.py`
- `tests/test_platform_gate4f_orchestrator_persistence_postgres.py`
- `tests/test_platform_gate4f_db_writer_unit.py`
- `tests/test_platform_gate4f_db_writer_migrations.py`
- `tests/test_platform_gate4f_db_writer_disposable_postgres.py`
- `tests/test_platform_gate6_interaction_unit.py`
- `tests/test_platform_gate6_interaction_disposable_postgres.py`

## 13. Task mapping

| Tasks | Implementation coverage |
|---|---|
| G7-T01, T13, T15, T24 | Sections 6–7 local schemas and production separation |
| G7-T02, T06, T12, T22 | v2 schema, validation, outcome, record ID |
| G7-T03, T04, T05, T23 | Writer-only secret, TLS, least privilege |
| G7-T07, T08 | one transaction and ledger idempotency |
| G7-T09, T10, T11 | timeout, frozen retry/reconciliation, sanitization |
| G7-T14 | Writer-owned supplier lookup |
| G7-T16, T17 | command session, mapping, binding/clear |
| G7-T18, T19, T20 | per-document selection, precedence, Platform storage |
| G7-T21 | early income guard and terminal state/reason |
| G7-T25 | shared counter-row lock and cross-protocol mutual exclusion |
| G7-T26 | deterministic command-first inbound router and busy response idempotency |
| G7-T27 | command answer/lifecycle/outbound-ambiguity durability |

All G7-T tasks are complete based on the verified Phase A implementation evidence recorded in section 17. Formal Gate 7 approval is complete.

## 14. Acceptance mapping

| Acceptance IDs | Required proof |
|---|---|
| G7-X01–X05, X10 | happy write, same-key single row, bounded retry, validation, rollback, returned ID |
| G7-X06–X09, X26 | credential isolation, sanitized logs, least privilege, zero unauthorized DML/DDL |
| G7-X11, X27–X30 | income state/reason, zero side effects/questions/notification, FIFO release, no recovery, Gate 8 mapping |
| G7-X12–X15, X23 | supplier one/zero/duplicate and approved financial defaults; no supplier writes |
| G7-X16–X19 | command select/clear, durable mapping, persistent future resolution |
| G7-X20–X22, X25 | document selection FIFO/TTL/cancel/resume, item-only materialization, unresolved zero Writer |
| G7-X24 | zero enterprise master-data writes |
| G7-X31–X41 | cross-protocol owner races, READY barrier/resume, routing disambiguation, duplicate/ambiguous command answers |

PostgreSQL 15 disposable tests must exercise both migrations, status/index constraints, concurrency and SKIP LOCKED behavior, command mapping stability, binding idempotency, per-document provenance, supplier lookup, v2 idempotency, rollback, and grants. Unit tests prove no Writer client, supplier lookup, enterprise call, interaction, persistence retry, or notifier occurs for income.

## 15. Verification and regression gates

Before any implementation, record a clean baseline. Final required evidence:

- Gate 4 regression: 210 passed, 0 failed, 0 errors;
- Gate 5 regression: 63 passed, 0 failed, 0 errors;
- Gate 6 regression: 64 passed, 0 failed, 0 errors;
- current full baseline: 439 passed, 0 skipped, 0 failed, 0 errors;
- all Gate 7 focused tests pass in disposable PostgreSQL 15;
- final complete suite has 0 failures and 0 errors;
- `compileall`, Ruff, mypy, and `git diff --check` pass;
- disposable databases/roles/resources are removed afterward;
- no persistent, staging, production, or remote database is touched.

## 16. Stop conditions and governance

Stop and return to governance if local enterprise IDs cannot be represented as stable strings, one-transaction ledger/business DML cannot be guaranteed, the command cannot be routed before generic answers, the shared counter-row lock cannot wrap both cross-table check-and-create paths, a known income path would create an amount/enterprise interaction, v1 regression would change, or implementation needs a file outside section 11.

There are no unresolved local architecture decisions and no local implementation blocker. Production-only blockers remain the external inputs in Phase B. Phase A implementation was explicitly authorized and is now complete and under review. This document does not authorize persistent/staging/production/remote migration execution, a commit, a push, Phase B, or Gate 8 work.

## 17. Phase A implementation and verification evidence

Status: **APPROVED / COMPLETE**. Implementation is **COMPLETE**, verification **PASSED**, final review **APPROVED**, and `G7-APPROVED = true`.

Implemented source scope:

- Platform models plus the approved additive Platform migration for `IGNORED / INCOME_OUT_OF_SCOPE`, enterprise materialization, persistent bindings, command sessions/answers, durable option mappings, constraints, and indexes;
- Orchestrator FIFO eligibility/barrier logic, income early guard, enterprise resolution, command lifecycle, shared conversation serialization lock, deterministic inbound routing, cancellation, Writer v2 client/persistence routing, and frozen retry/reconciliation ownership;
- Database Writer configuration, v2 HTTP contract, minimal read-only enterprise endpoint, Writer-owned local DF adapter, supplier lookup, atomic financial-record/ledger persistence, and the approved local DF migration;
- ten exact Gate 7 test modules plus the frozen Gate 4 Database Writer migration fixture update required for the new linear head and clean disposable-schema isolation.

The planned `stale_recovery_service.py` and `waiting_input_sweeper.py` required no source edit: their existing eligibility predicates already exclude `IGNORED`; focused and full regression tests verify non-reopening behavior. No source file outside the approved bounded areas was required.

Verification evidence (PostgreSQL 15 disposable environment only):

- Gate 7 Correction Pass 2 focused suite: **126 passed, 0 skipped, 0 failed, 0 errors**;
- G7-X01 through G7-X41: **PASS** through the exact concrete mapping in `TASKS_TESTS_GATES.md`, including the correction-pass routing, fail-closed boundary, recovery, strict Writer, concurrency, physical privilege, downgrade-refusal, and stale-binding proofs;
- Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 5 regression: **63 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 6 regression: **64 passed, 0 skipped, 0 failed, 0 errors**;
- complete project suite: **565 passed, 0 skipped, 0 failed, 0 errors**;
- `compileall`: PASS; Ruff: PASS; mypy: PASS; `git diff --check`: PASS.

Both Gate 7 migrations were exercised only in an ephemeral PostgreSQL 15 container against named disposable databases, including upgrade/downgrade/re-upgrade and physical contract checks. No persistent, staging, production, Supabase, or remote database was accessed; disposable resources were removed after verification. The migrations are created but are not authorized for execution outside disposable tests.

Scope integrity: `business_rules_evaluator.py` and the WuzapiClient implementation are unchanged; Gate 4 persistence semantics and v1 Writer regressions pass; Gate 7 sends no final WhatsApp outcome notification. Production-client Phase B remains blocked on external schema/deployment inputs, and Gate 8 remains NOT STARTED.

### 17.1 Bounded correction closure

The correction pass retained the approved architecture and closed implementation defects only: normal-text routing no longer references duplicate-path state; strict Gate 7 persistence eligibility is explicitly requested by the Gate 7 coordinator while the frozen Gate 4 service contract remains regression-compatible; terminal items cannot create prompts, while idempotent replays of an already-open WAITING interaction remain valid; `/empreendimento` checks the shared interaction owner before any Writer list call; RESERVED command generations recover the same mapping/outbound identity or expire; recent EXPIRED/CANCELLED command answers reach durable `LATE` routing.

Correction Pass 2 retained that architecture and closed the remaining review blockers only: a new VALIDATING prompt requires the authoritative worker claim and live lease; v2 semantic validation and canonical normalization occur before hashing or database access; commit-time DBAPI ambiguity is OUTCOME_UNKNOWN; every post-race lookup is remaining-budget guarded; real webhook tests directly cover duplicate text outcomes, CANCELLED late answers, and command precedence during an amount question; focused PostgreSQL evidence directly covers stale binding fallback, other-conversation FIFO progress, barrier invariants, and binding observation on resume. The authoritative G7-X01 through G7-X41 mapping now records exact test functions and evidence types.

Writer v2 now enforces canonical positive two-decimal text amounts, aware datetimes, duplicated identity/document-type equality, exact `WHATSAPP`, monotonic request budgeting, bounded PostgreSQL statement timeout, pre-business-DML advisory serialization by idempotency key, and sanitized deterministic DB outcome classification. TLS validation parses the DSN and requires exact `verify-full` in staging/production; missing/insecure defaults fail closed. Physical restricted-role tests, downgrade data preflights, date round-trip tests, concurrent idempotency tests, and current/stale binding tests all pass in disposable PostgreSQL 15.
