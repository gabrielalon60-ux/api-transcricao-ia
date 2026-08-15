# Gate 8 Implementation Plan — E2E Final Outcomes

> **Status**: APPROVED / COMPLETE
> **Gate 4**: APPROVED / COMPLETE / FROZEN
> **Gate 5**: APPROVED / COMPLETE / PUSHED
> **Gate 6**: APPROVED / COMPLETE / PUSHED
> **Gate 7**: APPROVED / COMPLETE / PUSHED
> **Gate 8 Product Contract**: CLOSED
> **Gate 8 Architecture Contract**: CLOSED
> **Gate 8 Planning**: APPROVED
> **Gate 8 Implementation HOLD**: APPROVED
> **Gate 8 Implementation**: COMPLETE
> **Gate 8 Verification**: PASSED
> **Gate 8 Correction Pass 1**: COMPLETE
> **Gate 8 Correction Pass 2**: COMPLETE
> **Gate 8 Final Approval Review**: PASSED / APPROVED
> **G8-APPROVED**: true
> **Migration Decision**: NO GATE 8 MIGRATION REQUIRED
> **Database Execution**: DISPOSABLE POSTGRESQL 15 VERIFICATION COMPLETE; PERSISTENT/STAGING/PRODUCTION/SUPABASE/REMOTE NOT AUTHORIZED
> **Production Phase B**: NOT IMPLEMENTED / OUT OF SCOPE

## 1. Objective

Gate 8 integrates durable final WhatsApp outcomes with the already-frozen Gates 4–7 pipeline and proves the complete local flow through deterministic E2E tests. It adds no new financial decision, interaction semantics, persistence outcome, Writer behavior, production schema adaptation, operational dashboard, or release claim.

The four bounded final-notification types are:

- `EXPENSE_COMMITTED`;
- `INCOME_OUT_OF_SCOPE`;
- `EXTRACTION_FAILED`;
- `PERSISTENCE_FAILED`.

Each eligible ProcessingItem creates one logical notification intent and authorizes at most one local WUZAPI outbound attempt. Notification transport begins only after the business item is durably terminal, is restart-safe, and never reacquires or blocks business FIFO.

## 2. Authority and frozen dependencies

Authority order for implementation:

1. the explicit Gate 8 product decisions recorded on 2026-08-14;
2. `.agents/PRD.md`;
3. `.agents/TASKS_TESTS_GATES.md`;
4. approved Gate 4–7 contracts and plans;
5. this plan;
6. existing source and tests.

Gate 8 must preserve:

- Gate 4 persistent FIFO, terminal/nonterminal semantics, Writer idempotency, bounded retry, outcome reconciliation, no blind persistence resend, and owner boundaries;
- Gate 5 financial amount/date/direction semantics, `America/Sao_Paulo` display rules, and `format_success_message` implementation unchanged;
- Gate 6 durable interaction generation, APPLIED provenance, WAITING/TTL/resume lifecycle, outbound ambiguity, and no blind prompt resend;
- Gate 7 expense-only persistence, `IGNORED / INCOME_OUT_OF_SCOPE`, enterprise resolution, cross-protocol lock, Writer v2, supplier lookup, committed record identity, and zero Gate 7 final outcome delivery.

Database Writer never sends WhatsApp messages. Transcription never sends business outcomes. `WuzapiClient` should remain unchanged.

## 3. Product-level terminal notification matrix

| ProcessingItem state | Additional evidence | Notification type | Gate 8 action |
|---|---|---|---|
| `COMPLETED` | `external_operation_status=COMMITTED`; direction `expense`; amount/date present; successful direct or reconciled persistence Execution with non-empty committed record ID | `EXPENSE_COMMITTED` | Send exact Gate 5-formatted success |
| `IGNORED` | `outcome_reason=INCOME_OUT_OF_SCOPE` | `INCOME_OUT_OF_SCOPE` | Send exact informational message |
| `EXTRACTION_FAILED` | Terminal status is sufficient; provider error detail is neither required nor exposed | `EXTRACTION_FAILED` | Send exact sanitized processing-failure message |
| `PERSISTENCE_FAILED` | Terminal status is sufficient; internal Writer/error vocabulary is neither required nor exposed | `PERSISTENCE_FAILED` | Send exact sanitized persistence-failure message |
| `FAILED` | Any reason | none | No new Gate 8 notification |
| `EXPIRED` | Any reason | none | No Gate 8 notification |
| `CANCELLED` | Existing cancellation lifecycle | none | Preserve existing cancellation acknowledgement; send no additional Gate 8 message |
| Any nonterminal state | Includes `RECEIVED`, `EXTRACTING`, `EXTRACTED`, `READY`, `ACTIVE`, `VALIDATING`, `WAITING_USER_INPUT`, `PERSISTING`, `PERSIST_RETRYABLE`, `PERSIST_OUTCOME_UNKNOWN` | none | Categorically ineligible |

Terminal notification selection must fail closed. Exactly one of the four types may be derived from an eligible item. Unknown state/reason combinations create no intent and emit a sanitized audit log only.

## 4. Exact eligibility contracts

### 4.1 `EXPENSE_COMMITTED`

All conditions are mandatory:

```text
ProcessingItem.status = COMPLETED
AND ProcessingItem.external_operation_status = COMMITTED
AND ProcessingItem.direction = expense
AND ProcessingItem.amount IS NOT NULL AND amount > 0
AND ProcessingItem.transaction_date IS NOT NULL
AND successful Execution exists where
    operation IN (PERSISTENCE_COMMITTED, PERSISTENCE_RECONCILED_COMMITTED)
    status = SUCCESS
    external_reference IS NOT NULL AND external_reference <> ''
```

The matching persistence Execution is the durable committed-record-ID proof. Direct and reconciled commits are equally valid. No other persistence state can produce success.

### 4.2 `INCOME_OUT_OF_SCOPE`

Exact pair only:

```text
ProcessingItem.status = IGNORED
AND ProcessingItem.outcome_reason = INCOME_OUT_OF_SCOPE
```

The item has no Writer business effect and no committed record ID. Gate 8 does not change the Gate 7 state or reason.

### 4.3 `EXTRACTION_FAILED`

`ProcessingItem.status = EXTRACTION_FAILED` is the durable eligibility fact. Current extraction paths do not reliably materialize provider error details on the ProcessingItem; Gate 8 must not require, copy, infer, log, or expose them. A terminal item created before notifier startup remains discoverable.

### 4.4 `PERSISTENCE_FAILED`

`ProcessingItem.status = PERSISTENCE_FAILED` is the durable eligibility fact. The notification does not depend on, display, or log Writer status vocabulary, SQL, record IDs, retry counts, or `error_code`.

`PERSIST_RETRYABLE` and `PERSIST_OUTCOME_UNKNOWN` are never eligible. A retry or reconciliation that becomes `COMPLETED` produces one success and zero failure notifications. An ambiguous result produces neither success nor failure until Gate 4 reconciliation durably resolves it.

## 5. Exact user-facing messages

### `EXPENSE_COMMITTED`

Reuse frozen Gate 5 `format_success_message("expense", amount, display_date)` unchanged. Example:

```text
✅ Gravado com sucesso.

Despesa de R$ 1.200,00 realizada em 29/07/2026.
```

The adapter derives `display_date` from durable item fields using frozen Gate 5 date semantics. Enterprise, supplier, committed record ID, database details, and internal state names are excluded.

### `INCOME_OUT_OF_SCOPE`

```text
ℹ️ Entrada identificada.

No momento, os lançamentos via WhatsApp registram apenas despesas.
Este documento não foi gravado.
```

### `EXTRACTION_FAILED`

```text
⚠️ Não foi possível processar este documento.

Tente enviá-lo novamente em alguns instantes.
```

### `PERSISTENCE_FAILED`

```text
⚠️ Não foi possível gravar este lançamento.

Nenhuma confirmação de gravação foi enviada. Tente novamente mais tarde.
```

No final message or log may expose provider identity, Gemini, SQL, DB host, credentials, stack traces, error codes, Writer vocabulary, retry internals, or record IDs.

## 6. Existing Execution schema capability review

The current `Execution` model provides:

- mandatory `event_id` and `correlation_id` plus nullable `processing_item_id`;
- unconstrained operation name for bounded application-defined checkpoints;
- nullable `outbound_message_id` protected by partial unique index `uq_executions_outbound_msg`;
- nullable `operation_idempotency_key` up to 512 characters protected by partial unique index `uq_executions_operation_idempotency_key`;
- status vocabulary `PENDING|RUNNING|SUCCESS|RETRYING|FAILED`;
- effect vocabulary `DISPATCHED|ACKNOWLEDGED|OUTBOUND_OUTCOME_UNKNOWN|FAILED`;
- external reference, attempt, timestamps, duration, and sanitized error fields.

No message payload column is required. Notification content is deterministic from the bounded type and immutable durable ProcessingItem facts. No provider-side idempotency capability is invented.

The schema supports the required physical guarantees:

1. one logical intent: unique reservation operation key;
2. one dispatch owner: unique dispatch operation key plus unique outbound ID;
3. dispatch-before-I/O: dispatch checkpoint commits before WUZAPI invocation;
4. mutually exclusive finalization: ACK and UNKNOWN compete for the same unique finalization key;
5. concurrent worker safety: `FOR UPDATE SKIP LOCKED`, savepoints, and unique-conflict loss with zero outbound call by losers;
6. durable recovery: checkpoint-set queries reconstruct state without memory.

Therefore: **NO GATE 8 MIGRATION REQUIRED**.

## 7. Notification vocabulary and identities

Bounded `notification_type`:

```text
EXPENSE_COMMITTED
INCOME_OUT_OF_SCOPE
EXTRACTION_FAILED
PERSISTENCE_FAILED
```

Stable outbound identity:

```text
final_<processing_item_id>_<notification_type-lowercase>
```

Checkpoint idempotency keys:

```text
<item_id>:FINAL_NOTIFICATION_RESERVED:<notification_type>
<item_id>:FINAL_NOTIFICATION_DISPATCHED:<notification_type>
<item_id>:FINAL_NOTIFICATION_FINAL:<notification_type>
```

ACK and UNKNOWN deliberately use the same `FINAL_NOTIFICATION_FINAL` key. The unique operation-key index makes the final outcomes mutually exclusive under concurrency. Current UUID-based item IDs and bounded notification types remain far below the 512-character operation-key limit; implementation must validate the limit and fail closed before persistence.

The outbound ID is a local durable identity. It is not claimed to be a provider idempotency key because the current WUZAPI endpoint accepts no such field.

## 8. Exact state machine and transaction boundaries

The notification lifecycle is derived from durable checkpoint sets:

```text
ELIGIBLE TERMINAL ITEM
  -> FINAL_NOTIFICATION_RESERVED
  -> FINAL_NOTIFICATION_DISPATCHED (commit before I/O)
  -> FINAL_NOTIFICATION_ACKNOWLEDGED
     or FINAL_NOTIFICATION_OUTCOME_UNKNOWN
```

### Boundary 1 — reserve

1. Scan at most `FINAL_NOTIFICATION_BATCH_SIZE = 100` eligible terminal items.
2. Lock candidates with `FOR UPDATE SKIP LOCKED`.
3. Re-evaluate eligibility under lock.
4. Skip any item/type with a final ACK/UNKNOWN checkpoint.
5. Insert `FINAL_NOTIFICATION_RESERVED` using the stable reservation key and outbound identity as `external_reference`.
6. Commit before any outbound work.

Unique-conflict losers roll back only the savepoint and perform zero WUZAPI calls.

### Boundary 2 — acquire dispatch ownership

1. Select a reserved intent with no dispatch and no final checkpoint.
2. Lock its reservation Execution with `FOR UPDATE SKIP LOCKED`.
3. Insert `FINAL_NOTIFICATION_DISPATCHED` with the stable `outbound_message_id`, dispatch key, `status=SUCCESS`, and `effect_status=DISPATCHED`.
4. Commit.
5. Only the transaction that inserted the dispatch checkpoint may invoke WUZAPI.

### Boundary 3 — outbound I/O

Resolve `User.phone_number`, construct the exact deterministic message, and call the existing WUZAPI text sender through an explicit synchronous worker bridge. A missing configuration, exception, timeout, non-confirmable result, or process loss cannot be treated as ACK.

### Boundary 4 — finalize

1. Lock the dispatch Execution.
2. Check for the shared finalization key.
3. Confirmed WUZAPI HTTP completion inserts `FINAL_NOTIFICATION_ACKNOWLEDGED`, `status=SUCCESS`, `effect_status=ACKNOWLEDGED`.
4. Any unconfirmed result inserts `FINAL_NOTIFICATION_OUTCOME_UNKNOWN`, `status=FAILED`, `effect_status=OUTBOUND_OUTCOME_UNKNOWN`, with sanitized error metadata.
5. Both outcomes use the same unique finalization key, so at most one commits.

Notification finalization never writes ProcessingItem status, reason, persistence fields, claims, sequence, or FIFO counters.

## 9. Recovery, scheduling isolation, and ambiguity

### 9.1 Current worker structure and required isolation

The current `run_fifo_worker_loop` is synchronous. One `while running` loop serially performs heartbeat renewal, stale/expiration/persistence/command sweepers, business READY/VALIDATING claims, and polling sleeps. Its existing WUZAPI bridges use `asyncio.run(...)`, so calling a final-notification sender inline from that loop would delay later business claims for the duration of WUZAPI I/O. That design is prohibited.

Gate 8 keeps one existing runtime/container/process but gives it two independent execution loops:

```text
run_fifo_worker_loop supervisor/entrypoint
├── business FIFO loop — existing main worker thread
└── final-notification loop — one bounded daemon thread
```

No new service, process, container, dependency, or deployment configuration is introduced. `run_fifo_worker_loop` remains the public entrypoint, installs signal handlers in the main thread, creates the shared thread-safe SQLAlchemy engine/session factory, starts and monitors the notifier thread, and executes the existing business loop without awaiting notifier work.

The notifier thread has its own `threading.Event` shutdown signal and creates independent SQLAlchemy Sessions. Reservation/dispatch ownership commits and closes its session before WUZAPI I/O. Final ACK/UNKNOWN uses a fresh session. No DB connection, transaction, terminal-item row lock, conversation counter lock, or business claim lock is held during outbound I/O.

The business loop performs only a nonblocking notifier-health check at its normal iteration boundary. If the notifier thread exited unexpectedly while the runtime is still running, it logs a sanitized failure and starts a replacement thread. Durable checkpoints make restart idempotent. The business loop never joins, waits for, or executes notifier sends during normal processing.

### 9.2 Bounded notifier loop

Frozen scheduling values:

```text
FINAL_NOTIFICATION_BATCH_SIZE = 100
FINAL_NOTIFICATION_DISPATCH_CONCURRENCY = 1
FINAL_NOTIFICATION_POLL_INTERVAL_SECONDS = 1.0
FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS = 60
FINAL_NOTIFICATION_SHUTDOWN_JOIN_SECONDS = 1.0
```

One scan may reserve at most 100 eligible terminal candidates, but the notifier authorizes at most one outbound dispatch at a time. A batch is never sent inline or drained before business processing continues. Each notifier iteration handles bounded database work and at most one send, catches `Exception`, logs sanitized context, and waits with `shutdown_event.wait(1.0)` rather than an uninterruptible sleep.

The existing synchronous `asyncio.run(WuzapiClient.send_text_message(...))` bridge pattern is reused inside the notifier thread. It may block that thread for the outbound attempt, but the main business thread does not await it. No uncontrolled async task fan-out or executor pool is added.

### 9.3 Business independence proof

- Slow or blocking final WUZAPI: only the notifier thread waits; the business thread continues READY/VALIDATING claims.
- WUZAPI timeout: notifier finalizes UNKNOWN; business polling and unrelated conversations continue during the timeout.
- Notifier exception: caught inside the notifier iteration; if the thread nevertheless exits, the business loop remains alive and restarts it.
- One hundred pending notifications: the scan is bounded and isolated; the business loop need not wait for any notification to finish.
- Concurrent loops: notifier locks terminal ProcessingItems/Execution rows only and never mutates sequence, claim, lease, heartbeat, interaction, persistence, or conversation-counter state.
- Business-loop exception: existing iteration isolation remains; it neither rolls back nor corrupts committed notification checkpoints in the other thread.

### 9.4 Durable recovery

- No reservation: a later scan may reserve the eligible terminal item.
- Reserved with no dispatch: recover the same type and outbound ID; safe to dispatch.
- Dispatched with ACK/UNKNOWN: terminal; skip forever automatically.
- Dispatched with no final checkpoint: never resend. After `FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS = 60`—longer than the current 30-second WUZAPI timeout—lock the dispatch row and insert UNKNOWN using the shared finalization key.
- Crash before reservation commit: no durable intent; later scan starts normally.
- Crash after reservation commit but before dispatch commit: safe reserved recovery.
- Crash after dispatch commit, before/during/after send: UNKNOWN after grace, with no blind resend.
- Application restart and inbound webhook replay regenerate neither notification type nor outbound identity.

`OUTBOUND_OUTCOME_UNKNOWN` is terminal for automatic Gate 8 sending because WUZAPI exposes no authoritative idempotency or outbound-status reconciliation interface. This limitation does not alter the terminal business result.

### 9.5 Shutdown and cancellation

The signal handler sets both the existing business `running` flag and the notifier shutdown Event. The notifier stops before acquiring new work. The main runtime performs a bounded one-second join; the notifier is a daemon thread so a slow/blocked WUZAPI call cannot prevent process shutdown.

- Shutdown while only `RESERVED`: no dispatch checkpoint exists, so restart safely resumes the same intent and outbound identity.
- Shutdown after `DISPATCHED`: never infer ACK. Restart recovery waits for the frozen grace, finalizes `OUTBOUND_OUTCOME_UNKNOWN`, and performs zero resend.
- Shutdown after confirmed send but before ACK commit: treated identically as UNKNOWN; no false ACK and no blind resend.
- Cancellation never rewrites the already-terminal ProcessingItem business state.

## 10. FIFO independence and ordering

Business terminalization releases same-conversation FIFO before final-notification completion. Final notification state is represented only by Execution checkpoints and does not add a ProcessingItem blocking state.

Consequences:

- a notification failure never reverts `COMPLETED`, `IGNORED`, `EXTRACTION_FAILED`, or `PERSISTENCE_FAILED`;
- no notification condition changes the committed financial record;
- later ProcessingItems remain claimable under the frozen business FIFO rules;
- Gate 8 does not add a second per-conversation FIFO for visual WhatsApp outcome ordering;
- each eligible item independently receives one logical intent, at most one local attempt, and one durable ACK/UNKNOWN outcome.

G8-X06 proves business processing and persistence order, not strict ordering of already-terminal final messages.

## 11. Runtime ownership and exact source scope

### Required

- **NEW** `apps/orchestrator/src/orchestrator/services/final_notification_service.py`
  - type/eligibility mapping;
  - exact message selection and Gate 5 formatter adapter;
  - reservation, dispatch ownership, finalization, recovery, and bounded sweep;
  - privacy-safe audit metadata.
- **MODIFY** `apps/orchestrator/src/orchestrator/fifo_worker.py`
  - preserve `run_fifo_worker_loop` as the public runtime/supervisor entrypoint;
  - keep the existing business FIFO loop in the main worker thread;
  - supervise one bounded independent final-notification daemon thread/loop;
  - reuse the existing synchronous `asyncio.run` WUZAPI bridge inside the notifier thread only;
  - use independent short DB sessions before/after outbound I/O;
  - isolate notifier failure, restart, and shutdown from business claims and other sweepers.

### Must remain unchanged unless a later HOLD explicitly proves necessity

- `apps/orchestrator/src/orchestrator/services/business_rules_evaluator.py`;
- `apps/orchestrator/src/orchestrator/wuzapi.py`;
- `apps/orchestrator/src/orchestrator/main.py`;
- `apps/orchestrator/src/orchestrator/services/persistence_service.py`;
- Database Writer source;
- Transcription source;
- `packages/db/src/db/models.py`;
- all migrations, dependencies, and runtime/deployment configuration.

## 12. Actual verified test files and functions

### ACTUAL NEW `tests/test_platform_gate8_final_notifications_unit.py`

- `test_terminal_notification_type_matrix`
- `test_completed_requires_committed_record_evidence`
- `test_nonterminal_and_ambiguous_states_are_ineligible`
- `test_success_formatter_adapter_reuses_frozen_gate5_formatter`
- `test_final_user_messages_are_exact_and_sanitized`
- `test_stable_outbound_identity_is_deterministic_and_bounded`
- `test_notifier_bounds_are_frozen`
- `test_slow_final_sender_does_not_delay_next_business_claim`
- `test_final_sender_timeout_does_not_stop_business_loop`
- `test_notifier_exception_does_not_stop_business_loop`
- `test_notification_backlog_does_not_starve_business_claims`
- `test_business_and_notifier_loops_preserve_fifo_sequence`

### ACTUAL NEW `tests/test_platform_gate8_final_notifications_disposable_postgres.py`

- `test_concurrent_reservation_creates_one_logical_intent`
- `test_concurrent_dispatch_creates_one_owner_and_one_outbound_attempt`
- `test_dispatch_checkpoint_commits_before_sender_invocation`
- `test_reserved_without_dispatch_is_restart_recoverable`
- `test_dispatched_without_finalization_becomes_unknown_without_resend`
- `test_acknowledged_and_unknown_share_one_finalization_identity`
- `test_terminal_items_created_before_notifier_startup_are_discovered`
- `test_notification_outcome_does_not_change_business_state_or_fifo`
- `test_retryable_and_persistence_outcome_unknown_create_no_intent`
- `test_shutdown_after_dispatched_recovers_unknown_without_resend`
- `test_shutdown_at_reserved_recovers_same_intent`

### ACTUAL NEW `tests/test_platform_gate8_e2e_disposable_postgres.py`

- `test_g8_x01_pix_expense_commits_and_sends_one_success`
- `test_g8_x02_pix_income_is_ignored_and_sends_one_information_message`
- `test_g8_x03_ambiguous_direction_answer_commits_and_sends_success`
- `test_g8_x04_missing_amount_answer_commits_and_sends_success`
- `test_g8_x05_missing_date_uses_timestamp_and_sends_success`
- `test_g8_x06_five_documents_preserve_business_fifo_without_notification_barrier`
- `test_g8_x07_two_users_progress_independently`
- `test_g8_x08_webhook_replay_has_one_write_and_one_notification_intent`
- `test_g8_x09_extraction_unavailable_sends_one_sanitized_failure`
- `test_g8_x10_retryable_then_committed_sends_one_success`
- `test_g8_x10_outcome_unknown_waits_for_reconciliation`
- `test_g8_x10_terminal_persistence_failure_sends_one_failure`
- `test_g8_x11_outbound_unavailable_becomes_unknown_without_resend`
- `test_g8_x12_correlation_id_reconstructs_complete_chain`

### ACTUAL NEW `tests/test_platform_gate8_real_webhook_disposable_postgres.py`

- `test_real_webhook_expense_replay_has_one_final_logical_outcome`
- `test_real_webhook_direction_clarification_reaches_one_success_outcome`
- `test_real_webhook_amount_clarification_reaches_one_success_outcome`
- `test_real_webhook_income_reaches_one_informational_outcome`

These tests use deterministic `threading.Event`, `Barrier`, and injected clocks/sender callbacks. They do not use real 30/60-second sleeps or external I/O.

The earlier planning draft proposed a fifth `tests/test_platform_gate8_notifier_scheduling_unit.py`, but that file does not exist in the actual working tree. Under the later explicit four-file implementation authorization, scheduling tests were incorporated into `test_platform_gate8_final_notifications_unit.py` and `test_platform_gate8_final_notifications_disposable_postgres.py`. This is a harmless test-layout plan deviation; no test or evidence was omitted and no fifth file is invented.

| Required scheduling evidence | Actual file |
|---|---|
| `test_slow_final_sender_does_not_delay_next_business_claim` | `tests/test_platform_gate8_final_notifications_unit.py` |
| `test_final_sender_timeout_does_not_stop_business_loop` | `tests/test_platform_gate8_final_notifications_unit.py` |
| `test_notifier_exception_does_not_stop_business_loop` | `tests/test_platform_gate8_final_notifications_unit.py` |
| `test_notification_backlog_does_not_starve_business_claims` | `tests/test_platform_gate8_final_notifications_unit.py` |
| `test_business_and_notifier_loops_preserve_fifo_sequence` | `tests/test_platform_gate8_final_notifications_unit.py` |
| `test_shutdown_after_dispatched_recovers_unknown_without_resend` | `tests/test_platform_gate8_final_notifications_disposable_postgres.py` |
| `test_shutdown_at_reserved_recovers_same_intent` | `tests/test_platform_gate8_final_notifications_disposable_postgres.py` |

All Gate 8 tests use real local routing and deterministic fakes. No external internet, cellphone, external WUZAPI, Gemini, persistent database, Supabase, staging, production, remote database, or real client DF schema is required.

## 13. G8-T01 through G8-T12 completion/evidence mapping

| Task | Completed implementation/evidence |
|---|---|
| G8-T01 | Sections 3–4 terminal mapping and fail-closed selector |
| G8-T02 | Reservation Execution and stable reservation key in sections 6–8 |
| G8-T03 | Candidate/reservation locks, savepoints, unique constraints, independent notifier-thread ownership, and concurrency tests |
| G8-T04 | Bounded type vocabulary, stable outbound ID, dispatch checkpoint, and worker bridge |
| G8-T05 | Shared unique finalization key; ACK/UNKNOWN outcomes; no resend |
| G8-T06 | Reserved-before-dispatch recovery and pre-start terminal discovery |
| G8-T07 | Frozen Gate 5 formatter adapter and committed-record proof |
| G8-T08 | Exact ignored-income pair and message |
| G8-T09 | Status-based extraction/persistence failure eligibility and exact sanitized messages |
| G8-T10 | Negative eligibility matrix for every nonterminal/ambiguous state |
| G8-T11 | Four Gate 8 test files, scheduling-isolation evidence, and G8-X01–X12 evidence map |
| G8-T12 | Focused, regression, complete-suite, static, diff, DB-safety, and governance closure evidence |

All G8-T01 through G8-T12 implementation and verification tasks are complete. The final independent approval review passed; Gate 8 is APPROVED / COMPLETE and `G8-APPROVED = true`.

## 14. G8-X01 through G8-X12 exact evidence mapping

| Acceptance | Actual verified test function(s) and file(s) | Evidence type |
|---|---|---|
| G8-X01 | `test_g8_x01_pix_expense_commits_and_sends_one_success` — `test_platform_gate8_e2e_disposable_postgres.py` | LOCAL E2E / DISPOSABLE POSTGRES |
| G8-X02 | `test_g8_x02_pix_income_is_ignored_and_sends_one_information_message` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_real_webhook_income_runs_guard_with_physical_zero_writer_rows` — `test_platform_gate8_real_webhook_disposable_postgres.py` | REAL BUSINESS / REAL WEBHOOK / PHYSICAL ZERO-WRITER PROOF |
| G8-X03 | `test_g8_x03_ambiguous_direction_answer_commits_and_sends_success` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_real_webhook_direction_clarification_reaches_writer_committed` — `test_platform_gate8_real_webhook_disposable_postgres.py` | SIGNED WEBHOOK / APPLIED ANSWER / ACTUAL WRITER COMMITTED |
| G8-X04 | `test_g8_x04_missing_amount_answer_commits_and_sends_success` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_real_webhook_amount_clarification_reaches_writer_committed` — `test_platform_gate8_real_webhook_disposable_postgres.py` | SIGNED WEBHOOK / APPLIED ANSWER / ACTUAL WRITER COMMITTED |
| G8-X05 | `test_g8_x05_missing_date_uses_timestamp_and_sends_success` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_success_formatter_adapter_reuses_frozen_gate5_formatter` — `test_platform_gate8_final_notifications_unit.py` | UNIT / LOCAL E2E / DISPOSABLE POSTGRES |
| G8-X06 | `test_g8_x06_five_documents_preserve_business_fifo_without_notification_barrier` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_notification_backlog_does_not_starve_business_claims` and `test_business_and_notifier_loops_preserve_fifo_sequence` — `test_platform_gate8_final_notifications_unit.py` | LOCAL E2E / DISPOSABLE POSTGRES / SCHEDULING |
| G8-X07 | `test_slow_final_sender_does_not_delay_next_business_claim` — `test_platform_gate8_final_notifications_unit.py` | TWO USERS / BLOCKED REAL NOTIFIER / REAL BUSINESS CLAIM AND COMPLETION |
| G8-X08 | `test_g8_x08_original_webhook_replay_has_one_full_effect` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_real_original_webhook_expense_replay_has_one_final_logical_outcome` — `test_platform_gate8_real_webhook_disposable_postgres.py` | ORIGINAL SIGNED WEBHOOK REPLAY / ACTUAL WRITER / DISPOSABLE POSTGRES |
| G8-X09 | `test_g8_x09_extraction_unavailable_sends_one_sanitized_failure` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_final_user_messages_are_exact_and_sanitized` — `test_platform_gate8_final_notifications_unit.py` | FAILURE / LOCAL E2E / UNIT |
| G8-X10 | `test_g8_x10_actual_retryable_then_committed`, `test_g8_x10_actual_unknown_reconciles_committed`, and `test_g8_x10_actual_writer_rejection_sends_failure` — `test_platform_gate8_e2e_disposable_postgres.py`; `test_retryable_and_persistence_outcome_unknown_create_no_intent` — final-notifications PostgreSQL file | ACTUAL PERSISTENCE / ACTUAL WRITER / RECONCILIATION |
| G8-X11 | `test_g8_x11_outbound_unknown_is_not_resent` — E2E file; `test_final_sender_timeout_does_not_stop_business_loop` and `test_notifier_exception_does_not_stop_business_loop` — unit file; `test_shutdown_after_dispatched_recovers_unknown_without_resend` — final-notifications PostgreSQL file | OUTBOUND AMBIGUITY / REAL RUNTIME RECOVERY / SCHEDULING |
| G8-X12 | `test_g8_x12_physical_correlation_chain` — `test_platform_gate8_e2e_disposable_postgres.py` | PHYSICAL PLATFORM + WRITER AUDIT CHAIN |

## 15. Correlation and audit proof

G8-X12 reconstructs, with deterministic queries and no dashboard:

```text
Event.correlation_id
-> ProcessingItem(event_id, correlation_id)
-> UserInteraction(processing_item_id) / UserAnswer(inbound_event_id), when applicable
-> persistence Execution(processing_item_id, correlation_id, writer identity)
-> Writer write_ledger(processing_item_id, idempotency_key, committed_record_id), when applicable
-> final-notification Executions(processing_item_id, event_id, correlation_id, outbound identity)
```

For `IGNORED` and extraction failure, the chain correctly has no Writer business record. Logs remain sanitized and do not substitute for durable proof. Metrics, dashboards, token/cost operations, runbooks, staging evidence, and production readiness remain Gates 9–10.

## 16. Verification and implementation evidence

Verified implementation source scope:

- NEW `apps/orchestrator/src/orchestrator/services/final_notification_service.py`;
- MODIFIED `apps/orchestrator/src/orchestrator/fifo_worker.py`;
- NEW exactly four Gate 8 test files listed in section 12;
- governance documentation updates only outside that source/test scope.

Final evidence:

- Gate 8 focused suite after Correction Pass 1: **46 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 5 regression: **63 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 6 regression: **64 passed, 0 skipped, 0 failed, 0 errors**;
- Gate 7 regression: **126 passed, 0 skipped, 0 failed, 0 errors**;
- complete project suite: **611 passed, 0 skipped, 0 failed, 0 errors**, above the pre-Gate-8 baseline of 565;
- `compileall`: **PASS**;
- Ruff on every modified Gate 8 source/test file: **PASS**;
- mypy on both modified Gate 8 source files: **PASS**;
- `git diff --check`: **PASS**.

Correction Pass 2 closed the sole remaining evidence blocker without source changes. The strengthened `test_shutdown_after_dispatched_recovers_unknown_without_resend` now starts the production notifier runtime for the original durable dispatch, simulates process loss before ACK/UNKNOWN, and then starts new notifier runtimes with fresh sessions at injected +59-second and +60-second clocks. The first restart proves no finalization and no resend; the second proves exactly one shared-key UNKNOWN, no ACK, no resend, one total outbound attempt, and immutable ProcessingItem business state. No recovery helper is called directly as the post-restart acceptance action. Targeted evidence: **1 passed, 0 skipped, 0 failed, 0 errors**. Gate 8 focused and complete-suite counts remain **46** and **611**, respectively, with zero skips, failures, or errors.

Physical tests used PostgreSQL 15 disposable resources only. Platform, local Writer, and Gate 3 disposable profiles were local to the same disposable container; all disposable resources were removed afterward. No persistent/staging/production/Supabase/remote database, external WUZAPI, Gemini, cellphone, or client database was accessed. No Gate 8 migration exists or was executed.

Scheduling evidence directly covers slow sender, timeout, notifier exception, backlog, business FIFO sequence, RESERVED restart recovery, and DISPATCHED-to-UNKNOWN no-resend recovery with deterministic events and injected time. No frozen Gate 4–7 test fixture was modified.

## 17. Migration decision

**NO GATE 8 MIGRATION REQUIRED.**

No logical object, column, constraint, or index is proposed. The existing Execution schema provides the durable intent, unique identities, checkpoint vocabulary, concurrency primitives, correlation, and sanitized audit storage required by this plan.

## 18. Production and external-environment boundary

Local Gate 8 approval does not require a real cellphone, external network, real WUZAPI, Gemini, or real client DF database. Real external acceptance is separately authorized later environment/release work.

Production Phase B remains NOT IMPLEMENTED and blocked on external client schema/deployment inputs. Gate 8 does not modify the Gate 7 adapter or claim production readiness. Gate 9 retains operations/observability/runbooks; Gate 10 retains security/release/staging/cutover work.

## 19. Implementation sequence after explicit HOLD approval

1. Add unit tests for the frozen type, eligibility, identity, formatter, and message contracts.
2. Implement the dedicated final-notification service using existing Execution rows only.
3. Add disposable-PostgreSQL reservation/concurrency/recovery tests.
4. Add the independent notifier thread/loop, worker bridge, bounded recovery sweep, health supervision, and shutdown handling without placing WUZAPI final sends on the business loop.
5. Add local E2E and real-local-webhook evidence for G8-X01–X12.
6. Run Gate 8 focused, Gate 4–7 regression, complete-suite, and static verification.
7. Perform independent final review and governance closure before any commit/push authorization. **COMPLETE: final review PASSED and governance approval was recorded.**

## 20. Stop conditions

Stop implementation and return for explicit review if any of the following occurs:

- existing Execution uniqueness cannot enforce the planned single intent/dispatch/finalization identities;
- implementation would require modifying a Gate 4–7 frozen business contract;
- a WUZAPI resend/status/idempotency capability is assumed but not actually available;
- success could be emitted without durable COMMITTED record-ID proof;
- success/failure could be emitted while persistence is retryable or ambiguous;
- notification transport would alter or block ProcessingItem business state/FIFO;
- final WUZAPI I/O would execute inline on or be awaited by the business claim loop;
- notifier work would hold a DB connection/transaction/lock during WUZAPI I/O;
- source outside the exact allowlist becomes necessary;
- a migration, dependency, runtime/deployment configuration, real external network, or non-disposable database becomes necessary;
- production Phase B, Gate 9, or Gate 10 work becomes entangled with Gate 8.

Implementation and disposable-database verification were performed under explicit Gate 8 authorization. The subsequent independent final approval review passed, and explicit governance/commit authorization closed Gate 8 as APPROVED / COMPLETE. Push remains separately authorized and was not authorized by this closure; production Phase B, Gate 9, Gate 10, migrations, external services, and persistent/staging/production/Supabase/remote database access remain unauthorized.
