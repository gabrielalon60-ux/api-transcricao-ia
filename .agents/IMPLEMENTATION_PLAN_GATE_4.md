# Gate 4 Implementation Plan — Persistent Queue & FIFO Sequencing

> **STATUS:** FINAL REVISED ARCHITECTURE PLAN (APPROVED IN PRINCIPLE — READY FOR HOLD 2 FORMAL APPROVAL)
> **Gate 3 Status:** APPROVED and COMPLETE (August 4, 2026)
> **Gate 4 Status:** NOT STARTED (Planning Phase Only)

---

## 1. Objective

Implement a persistent, restart-safe FIFO processing queue in Platform PostgreSQL for the DF Holding WhatsApp document-processing platform. Gate 4 guarantees that documents sent within a conversation are executed strictly in their original receive order (1 -> 2 -> 3 -> 4 -> 5), regardless of out-of-order AI extraction completion times, while preserving physical **one-ACTIVE-item per conversation** database constraints and durable execution effect tracking.

---

## 2. Authority Order

In accordance with project governance rules:

1. Current user instructions in active interaction.
2. `.agents/PRD.md`.
3. `.agents/TASKS_TESTS_GATES.md`.
4. Approved gate decisions recorded in the repository.
5. `.agents/IMPLEMENTATION_PLAN_GATE_4.md`.
6. `.agents/gate4_queue_schema_mapping.md`.
7. Existing source code.

---

## 3. Approved Architecture & Scope

- **Platform PostgreSQL Schema Expansion:** Create `conversation_queue_counters`, `processing_items`, `executions`, and `service_usage` tables in `packages/db` via Alembic migration (`packages/db/alembic/versions/`).
- **Atomic Ingestion Ownership (Design A):** Orchestrator creates `events` and `processing_items` atomically in a single Platform DB transaction. WUZAPI receives HTTP 200 OK **only after** commit.
- **Sequence Allocation:** Atomic sequence counter upsert (`conversation_queue_counters`) inside the ingestion transaction. Duplicate webhooks do NOT consume sequence numbers.
- **Physical One-Active Constraint:** Partial unique index `uq_processing_items_one_active_per_conversation` enforcing 1 active blocking item per conversation at the DB kernel level.
- **Execution Effect Ledger (`executions`):** Serves as the durable idempotent effect and checkpoint ledger for user prompts, outbound messages (`outbound_message_id`), and external calls.
- **Durable Capacity Rejection (Option A):** Excess items persisted as `FAILED` (`QUEUE_CAPACITY_EXCEEDED`, `sequence = NULL`), 0 calls to Transcription/Gemini, 1 user warning prompt, HTTP 200 to WUZAPI. High-volume rejected messages do NOT consume valid active sequence numbers.
- **Database Writer Advisory Lock Architecture:** `db_writer_idempotency` table stored physically in the **DF Holding database**. Business DML and idempotency record committed in a **single local transaction** serialized by `pg_advisory_xact_lock`. Status query `GET /writes/{key}` reads from DF database idempotency store.
- **Lease & Heartbeat Recovery Engine:** Worker lease (`claimed_by`, `lease_expires_at`, `heartbeat_at`) with state-by-state recovery sweeper backed by `executions` effect ledger.
- **PostgreSQL Worker Engine:** Polling (1.0s interval) as durable correctness authority; `LISTEN/NOTIFY` as ephemeral performance optimization. Full DB scan on worker startup/reconnect.

---

## 4. Media Ownership & Lifecycle Boundaries

- **Transcription Temporary Validation Copy:** Deleted in `finally` block **ONLY AFTER** extraction result or terminal extraction error is durably committed in the **Transcription database** (Gate 3 contract). Transcription does NOT wait for, query, or receive callbacks from Platform PostgreSQL.
- **Orchestrator / Bot DF Download Buffer:** Retained until extraction result or terminal extraction failure is durably committed to Platform PostgreSQL (`processing_items.status` set to `EXTRACTED` or `EXTRACTION_FAILED`).
- **WUZAPI Original Media:** External production-operational retention policy. Bot DF re-downloads media if worker restarts during `EXTRACTING` and local buffer was lost. If WUZAPI media has expired, item transitions to `EXTRACTION_FAILED`.

---

## 5. Database Writer Transaction Ownership & Concurrency Rules

- **Database Storage Location:** `db_writer_idempotency` table resides physically inside the **DF Holding database** alongside business DML tables (`despesas`, `entradas`).
- **Transaction-Scoped Advisory Lock:** `SELECT pg_advisory_xact_lock(hashtextextended(:idempotency_key, 0));` serializes concurrent first requests for absent keys. Different keys execute concurrently.
- **Single Local Transaction Atomicity:** One database connection and one local transaction perform:
  1. Transaction-scoped advisory lock acquisition;
  2. Idempotency table lookup;
  3. Deterministic business validation (Python side);
  4. Business DML execution (or `PERMANENT_ERROR` marker insert if validation failed);
  5. Idempotency marker insertion (`status = 'CONFIRMED_SUCCESS'`, `df_record_id`);
  6. Single transaction commit.
- **Error Semantics & Rollback:**
  - *Permanent Errors:* Validated before DML. Persisted as `PERMANENT_ERROR` in `db_writer_idempotency`, committed, and returns 400 Bad Request. Payload hash mismatch returns HTTP 409 without modifying stored outcome.
  - *Retryable Infrastructure Errors:* Unexpected DB disconnects, timeouts, or SQL failures abort the transaction, trigger `ROLLBACK`, and return retryable failures without persisting permanent markers.
- **Status Endpoint & Uncertainty Reconciliation:** `GET /writes/{idempotency_key}` queries `db_writer_idempotency` in DF database:
  - `CONFIRMED_SUCCESS`: Returns stored successful result + `df_record_id`.
  - `PERMANENT_ERROR`: Returns stored permanent business error.
  - `NOT_FOUND`: Indicates request was never received or transaction rolled back. Bot DF retries status lookup with bounded backoff before re-dispatching POST.

---

## 6. Outbound Message Transport & Idempotency Limitations

- **WUZAPI Transport Limitation:** WUZAPI HTTP outbound transport does NOT support deterministic client-side message ID deduplication if HTTP connection drops during message dispatch.
- **Achievable Guarantee:** At-Least-Once Delivery with Local Checkpoint Deduplication (`executions.outbound_message_id`).
- **Recovery Policy:** If WUZAPI drops connection during prompt send, execution is marked `OUTBOUND_OUTCOME_UNKNOWN`. Sweeper retries prompt send. Rare duplicate WhatsApp prompts may occur during network drops.

---

## 7. Explicit State Machine & Recovery Matrix (16 States)

| State | Owner | Entry Condition | Exit Condition | Blocks Queue? | Lease? | Status Type | Recovery & Checkpoint Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `RECEIVED` | Orchestrator | Webhook ingested & sequence allocated. | Extraction HTTP request dispatched. | YES | NO | Non-Terminal | Resend to Transcription if worker restarted and media recoverable. |
| `EXTRACTING` | Bot DF Client | Dispatched to Transcription. | Transcription returns result or error. | YES | NO | Non-Terminal | Re-download source media from WUZAPI if buffer lost, or mark `EXTRACTION_FAILED`. |
| `EXTRACTED` | Bot DF Client | Transcription returned success payload. | Payload validated & item marked ready. | YES | NO | Non-Terminal | Sweeper moves valid items to `READY`. |
| `READY` | Bot DF Worker | Extraction complete & valid. | Claimed by worker engine. | YES | NO | Non-Terminal | Eligible for worker claim via `SKIP LOCKED`. |
| `ACTIVE` | Bot DF Worker | Claimed by worker (`SKIP LOCKED`). | Business rule evaluation starts. | **YES (Index)** | **YES** | Non-Terminal | Check `executions` ledger. If no unrecorded effect -> reset to `READY`. |
| `VALIDATING` | Bot DF Worker | Business rules being checked. | All fields valid OR question needed. | **YES (Index)** | **YES** | Non-Terminal | Check `executions` for prompt status. Re-run validation safely. |
| `WAITING_USER_INPUT`| Bot DF Interaction| Ambiguous field (amount/direction). | User answers OR TTL 1h expires. | **YES (Index)** | NO (TTL) | Non-Terminal | Sweeper checks `expires_at`. If expired -> transition to `EXPIRED`. |
| `PERSISTING` | Bot DF Client | Dispatching to Database Writer. | Writer returns HTTP response. | **YES (Index)** | **YES** | Non-Terminal | Lease expired -> transition to `PERSIST_OUTCOME_UNKNOWN` and query Writer status. |
| `PERSIST_RETRYABLE`| Bot DF Worker | Network connection failed before request. | Retry request dispatched. | **YES (Index)** | **YES** | Non-Terminal | Worker retries dispatch if attempts < MAX. |
| `PERSIST_OUTCOME_UNKNOWN`| Sweeper | Timeout after request sent. | Status query `/writes/{id}` returns state. | **YES (Index)** | NO | Non-Terminal | Sweeper queries Database Writer status endpoint `GET /writes/{key}`. |
| `COMPLETED` | System | Database Writer confirmed success. | None. | NO | NO | Terminal | Final success. |
| `EXTRACTION_FAILED`| System | Extraction failed definitively. | None. | NO | NO | Terminal | Queue unblocked for next sequence item. |
| `PERSISTENCE_FAILED`| System | Database Writer rejected payload. | None. | NO | NO | Terminal | Queue unblocked for next sequence item. |
| `FAILED` | System | Internal system/validation error. | None. | NO | NO | Terminal | Queue unblocked for next sequence item. |
| `EXPIRED` | System | User interaction timed out (1 hour). | None. | NO | NO | Terminal | Queue unblocked for next sequence item. |
| `CANCELLED` | System | User sent `/cancelar` during wait. | None. | NO | NO | Terminal | Queue unblocked for next sequence item. |

---

## 8. End-to-End Idempotency Matrix

| Pipeline Touchpoint | Idempotency Key | Storage Location | Duplicate Handling Strategy |
| :--- | :--- | :--- | :--- |
| **1. WUZAPI Webhook Ingest** | `external_message_id` | `events` table constraint | DB rejects duplicate `external_message_id`; updates `duplicate_count`; returns 200 OK. |
| **2. Processing Item Creation** | `event_id` | `processing_items.event_id` UNIQUE | Single Orchestrator transaction prevents orphan events. Duplicate webhooks do NOT consume sequence. |
| **3. Transcription Request** | `request_id = processing_item.id` | `transcription.requests` table | Gate 3 idempotency engine returns existing extraction result without re-calling Gemini. |
| **4. Extraction Result Return** | `processing_item.id` + status check | `processing_items` | Atomic `UPDATE ... WHERE id = :id AND status = 'EXTRACTING'` prevents duplicate state transition. |
| **5. Worker Claim** | `processing_item.id` + status | `processing_items` | `SELECT FOR UPDATE SKIP LOCKED` + partial unique index prevents double claims. |
| **6. Outbound User Message** | `outbound_message_id = msg_{item_id}_{qtype}`| `executions` table | Checkpoint ledger prevents sending duplicate WhatsApp prompts during worker recovery. |
| **7. Database Writer Dispatch** | `writer_idempotency_key = write_{processing_item.id}` | DF Database (`db_writer_idempotency`) | Advisory lock (`pg_advisory_xact_lock`) & single local transaction guarantee atomic DML and idempotency record. |
| **8. Writer Status Recovery** | `writer_idempotency_key` | Database Writer status query | `/writes/{key}` returns `CONFIRMED_SUCCESS`, `NOT_FOUND`, or `PERMANENT_ERROR`. |

---

## 9. Configurable Operational Defaults

- `WORKER_LEASE_DURATION_SECONDS=60`
- `WORKER_HEARTBEAT_INTERVAL_SECONDS=15`
- `STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS=30`
- `WAITING_USER_INPUT_TTL_SECONDS=3600` (1 hour)
- `MAX_QUEUE_ITEMS_PER_CONVERSATION=10`
- `MAX_CONCURRENT_EXTRACTIONS_PER_SERVICE=5`

---

## 10. Verification Plan & Acceptance Matrix

### Acceptance Tests (`tests/test_gate4_queue.py`)
- **G4-X01:** 5 files received get contiguous sequence 1..5 via `conversation_queue_counters`.
- **G4-X02:** Out-of-order extraction completion (5, 3, 2, 4, 1) -> Business processing executes strictly 1 -> 2 -> 3 -> 4 -> 5.
- **G4-X03:** Concurrency test: 2 workers attempt `SKIP LOCKED` claim -> exactly 1 succeeds.
- **G4-X04:** Physical constraint test: Attempting to update 2 items to `ACTIVE` in same conversation raises `UniqueViolation`.
- **G4-X05:** Conversation A processing does not block Conversation B.
- **G4-X06:** Process restart preserves `READY` items and sequence.
- **G4-X07:** Process restart during `ACTIVE` triggers worker lease recovery sweeper backed by `executions` effect ledger.
- **G4-X08:** Option A capacity rejection at 10 items rejects 11th item (`sequence = NULL`), 0 calls to Transcription, HTTP 200 to WUZAPI.
- **G4-X09:** Item 1 `EXTRACTION_FAILED` unblocks Item 2.
- **G4-X10:** `PERSIST_OUTCOME_UNKNOWN` status query to Database Writer `/writes/{key}` correctly recovers lost network responses.
- **G4-X11:** Duplicate webhook re-send updates `duplicate_count` and does NOT consume sequence counter.
- **G4-X12:** Advisory lock (`pg_advisory_xact_lock`) serializes concurrent first requests for absent idempotency keys in DF database.
- **G4-X13:** Concurrent duplicate request for same key replays stored `CONFIRMED_SUCCESS` result with 0 duplicate DML executions.
- **G4-X14:** Same key with different payload returns HTTP 409 `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH` without mutating original outcome.
- **G4-X15:** Permanent contract validation error is committed as `PERMANENT_ERROR` in DF DB and replayed on duplicates.
- **G4-X16:** Retryable infrastructure failure (SQL error / disconnect) triggers `ROLLBACK` with 0 `PERMANENT_ERROR` markers.
- **G4-X17:** Immediate status lookup `GET /writes/{key}` during in-flight transaction returning `NOT_FOUND` does not cause duplicate DML.
- **G4-X18:** Concurrent requests for different idempotency keys execute simultaneously without blocking each other.

---

## 11. Hold Points & Reconciled Implementation Phases

- **HOLD 1:** Gate 3 closure confirmation. (`PASSED`)
- **HOLD 2:** Gate 4 architecture approval. (**APPROVED on 2026-08-04**)
- **HOLD 3:** Schema and migration review. (**APPROVED on 2026-08-04**)
- **Phase 4B:** Durable Orchestrator ingestion path. (**APPROVED on 2026-08-04**)
- **Phase 4C:** Concurrent Extraction Dispatch & READY Transition. (**IMPLEMENTATION COMPLETE — PENDING PHASE 4C REVIEW**)
- **HOLD 4:** FIFO claim and recovery algorithm review. (**NOT REACHED — Reserved for Phase 4D FIFO worker review**)
- **Phase 4D:** FIFO Worker Claim Engine & Monotonic Blocked Execution. (**NOT STARTED**)
- **Phase 4E:** Lease Recovery Sweeper, Heartbeat & User Input Expiration. (**NOT STARTED**)
- **Phase 4F:** Database Writer Integration & End-to-End Gate 4 Integration Suite (`G4-X01`..`G4-X18`). (**NOT STARTED**)
- **HOLD 5:** General implementation authorization.
- **HOLD 6:** Persistent database migration execution authorization.
- **HOLD 7:** Remote-resource integration-test authorization.
- **HOLD 8:** Gate 4 formal review.
- **HOLD 9:** Explicit Gate 4 completion approval by user.

> **READY FOR PHASE 4C REVIEW.**
