# Gate 4 — Architectural Decisions & Deliverables

This document summarizes approved architectural decisions, configuration defaults, and explicit Gate 4 deliverables.

---

## Approved Architectural Decisions (In Principle)

1. **Atomic Sequence Counter:** `conversation_queue_counters` upsert inside Orchestrator single ingestion transaction (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`).
2. **Orchestrator Ingestion Ownership (Design A):** Orchestrator creates `events` and `processing_items` atomically before returning HTTP 200 OK to WUZAPI.
3. **16-State Lifecycle Model:** Explicit non-terminal, recovery, and terminal state coverage.
4. **Physical Partial Unique Index:** Database kernel protection enforcing 1 active item per conversation (`uq_processing_items_one_active_per_conversation`).
5. **Sequential `WAITING_USER_INPUT` Blocking:** Interaction states block later sequence items; 1h TTL (`expires_at`) transitions to `EXPIRED`; user `/cancelar` transitions to `CANCELLED`.
6. **Deterministic Global Fairness:** FIFO ordering across conversations (`ORDER BY message_received_at ASC, org_id ASC, inst_id ASC, user_id ASC, sequence ASC`).
7. **Database Writer Transaction-Scoped Advisory Lock:** `pg_advisory_xact_lock` serializes concurrent first requests in DF database before querying `db_writer_idempotency`.
8. **Lease / Heartbeat Recovery Engine:** Worker lease (`claimed_by`, `lease_expires_at`, `heartbeat_at`) with state-by-state recovery sweeper backed by `executions` effect ledger.
9. **Durable Capacity Rejection (Option A):** Excess items persisted as `FAILED` (`QUEUE_CAPACITY_EXCEEDED`, `sequence = NULL`), 0 calls to Transcription/Gemini, 1 user warning prompt, HTTP 200 to WUZAPI. High-volume rejected messages do NOT consume valid active sequence numbers.
10. **PostgreSQL Worker Engine:** Polling (1.0s interval) as durable correctness fallback; `LISTEN/NOTIFY` as ephemeral performance optimization. Full DB scan on startup/reconnect.

---

## Media Ownership & Boundary Re-alignment

1. **Transcription Temporary Copy:** Deleted in `finally` block **ONLY AFTER** extraction result or terminal extraction error is durably committed in the **Transcription database**, strictly following the approved Gate 3 request lifecycle. Transcription does NOT wait for, query, or receive callbacks from Platform PostgreSQL.
2. **Bot DF / Orchestrator Source Copy (Download Buffer):** Retained until extraction result or terminal extraction failure is durably committed to Platform PostgreSQL (`processing_items.status` = `EXTRACTED` or `EXTRACTION_FAILED`).
3. **WUZAPI Original Media:** External production-operational retention policy. Bot DF re-downloads media if worker restarts during `EXTRACTING` and local buffer was lost. If WUZAPI retention has expired, item transitions to `EXTRACTION_FAILED`.

---

## Database Writer Single Local Transaction Architecture & Concurrency Rules

- **Database Location:** `db_writer_idempotency` table resides physically inside the **DF Holding database** alongside business DML tables (`despesas`, `entradas`).
- **Transaction-Scoped Advisory Lock:** `SELECT pg_advisory_xact_lock(hashtextextended(:idempotency_key, 0));` serializes concurrent first requests for absent keys. Different keys execute concurrently.
- **Single Local Transaction Atomicity:** One database connection and one local transaction perform:
  1. Transaction-scoped advisory lock acquisition;
  2. Idempotency table lookup;
  3. Deterministic business validation (Python side);
  4. Business DML execution (or `PERMANENT_ERROR` marker insert if validation failed);
  5. Idempotency marker insertion (`status = 'CONFIRMED_SUCCESS'`, `df_record_id`);
  6. Transaction commit.
- **Error Semantics & Rollback:**
  - *Permanent Errors:* Validated before DML. Persisted as `PERMANENT_ERROR` in `db_writer_idempotency`, committed, and returns 400 Bad Request. Payload hash mismatch returns HTTP 409 without modifying stored outcome.
  - *Retryable Infrastructure Errors:* Unexpected DB disconnects, timeouts, or SQL failures abort the transaction, trigger `ROLLBACK`, and return retryable failures without persisting permanent markers.
- **Status Endpoint & Uncertainty Reconciliation:** `GET /writes/{idempotency_key}` queries `db_writer_idempotency` in DF database:
  - `CONFIRMED_SUCCESS`: Returns stored successful result + `df_record_id`.
  - `PERMANENT_ERROR`: Returns stored permanent business error.
  - `NOT_FOUND`: Indicates request was never received or transaction rolled back. Bot DF retries status lookup with bounded backoff before re-dispatching POST.

---

## Outbound Message Transport Limitations

- **WUZAPI Capability Boundary:** WUZAPI HTTP outbound transport does NOT support deterministic client-side message ID deduplication if HTTP connection drops during message dispatch.
- **Best Achievable Guarantee:** At-Least-Once Delivery with Local Checkpoint Deduplication (`executions.outbound_message_id`).
- **Recovery Policy:** If WUZAPI drops connection during prompt send, execution is marked `OUTBOUND_OUTCOME_UNKNOWN`. Sweeper retries prompt send. Rare duplicate WhatsApp prompts may occur during network drops.
