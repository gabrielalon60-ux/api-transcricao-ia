# Gate 7 Contract Closure and Production Destination Discovery

> **Status**: APPROVED / IMPLEMENTED / VERIFIED / COMPLETE
> **Gate 4**: APPROVED / COMPLETE / FROZEN
> **Gate 5**: APPROVED / COMPLETE / PUSHED
> **G5-APPROVED**: true
> **Gate 6**: APPROVED / COMPLETE / PUSHED
> **G6-APPROVED**: true
> **Gate 7**: APPROVED / COMPLETE
> **G7-APPROVED**: true
> **Gate 8**: NOT STARTED
> **Migrations**: CREATED / EXECUTED IN DISPOSABLE POSTGRESQL 15 VERIFICATION ONLY / PERSISTENT EXECUTION NOT AUTHORIZED
> **Database access during this review**: NONE

This document closes every Gate 7 contract that can be derived from the repository and the client-approved MVP business model without connecting to a real DF database. Gate 7 Phase A is implemented, verified, and formally approved; production-client Phase B remains outside the approved scope. The existing `df_business_records` table remains a Gate 4 test adapter/mock and is not the local MVP or production DF schema.

## 1. Authority and Scope

Authoritative inputs:

- `.agents/TASKS_TESTS_GATES.md`: operational Gate 7 tasks `G7-T01` through `G7-T12` and acceptance cases `G7-X01` through `G7-X09`;
- `.agents/PRD.md`: Database Writer ownership, `DatabaseWriteRequest`, idempotency, retry, secret-isolation, TLS, least-privilege, and unresolved `TBD-001` destination schema;
- `.agents/IMPLEMENTATION_PLAN_GATE_4.md`: frozen persistence state machine, idempotency identity, Writer outcome vocabulary, and reconciliation boundary;
- `.agents/IMPLEMENTATION_PLAN_GATE_5.md`: frozen financial fact semantics;
- `.agents/IMPLEMENTATION_PLAN_GATE_6.md`: frozen runtime handoff to `PersistenceService`;
- current source and tests under `apps/db_writer`, `apps/orchestrator`, `packages/db`, and `tests/test_platform_gate4f_*`.

The local MVP logical schema and runtime contract are closed for expenses, income exclusion, suppliers, enterprises, and cross-protocol interaction ownership. Production adaptation remains distinct: the client financial-record and supplier tables do not yet exist, while the enterprise table exists but its real schema has not been supplied. Gate 7 remains unauthorized until explicit user implementation approval.

## 1.1 Approved MVP Business Contract — 2026-08-11

### Expense-only destination

- WhatsApp creates expenses only.
- Frozen Gate 5/6 direction handling remains `income|expense|ambiguous|unknown`.
- Only effective `expense` may proceed to enterprise resolution and expense persistence.
- Effective `income` causes zero expense Writer POST, `financial_records` row, supplier lookup, amount question, or enterprise resolution/question.
- Effective `income` atomically becomes terminal `IGNORED` with non-error reason `INCOME_OUT_OF_SCOPE`; it releases same-conversation FIFO and is never reclaimed, recovered, retried, reconciled, cancelled, or replayed into active work.
- Gate 7 sends no final notification. Gate 8 will idempotently map `IGNORED / INCOME_OUT_OF_SCOPE` to the approved informational message.

### Income terminal contract and durable representation

The smallest correct durable representation is a nullable `processing_items.outcome_reason` column. Existing `ProcessingItem.error_code` is explicitly an error/audit field, while `external_operation_status` belongs to Writer persistence; neither is semantically suitable for a valid out-of-destination item. The additive Platform migration must:

- add `IGNORED` to `ck_processing_items_status_valid` and every terminal/capacity predicate;
- add nullable `outcome_reason` plus a constraint requiring the exact pair `status='IGNORED' AND outcome_reason='INCOME_OUT_OF_SCOPE'`, and requiring `outcome_reason IS NULL` for all other current states;
- leave `error_code`, `external_operation_status`, persistence counters, and frozen historical rows unchanged;
- keep `IGNORED` outside active/lease/expiration/persistence indexes.

The guard belongs in the BOT DF coordinator, at the owned `VALIDATING` transition immediately after `evaluate_and_persist_validating_item` materializes the effective direction and before `_process_validating_item` dispatches `decision.question_type` or calls PersistenceService. The Gate 7 coordinator must atomically lock the still-owned `VALIDATING` row and transition known `income` to `IGNORED / INCOME_OUT_OF_SCOPE`, clear business claim/lease/heartbeat, set `completed_at`, and write one idempotent non-failure execution checkpoint. To avoid even constructing an amount question for known income, the Gate 7 decision composition must prioritize the destination guard after effective direction resolution and before amount requirements; Gate 5 evaluator code and Gate 6 answer provenance remain unchanged.

Gate 8 future informational text:

```text
ℹ️ Entrada identificada.

No momento, os lançamentos via WhatsApp registram apenas despesas.
Este documento não foi gravado.
```

### Local MVP destination objects

```text
financial_records
  id
  transaction_date NOT NULL
  expense_type_id NULL for WhatsApp
  enterprise_id NOT NULL
  amount NOT NULL and positive
  supplier_id nullable
  supplier_cnpj_snapshot nullable
  comments NULL for WhatsApp
  is_deleted NOT NULL default false
  deleted_at NULL for WhatsApp
  origin NOT NULL: WHATSAPP | SITE
  processing_item_id
  created_at
  updated_at

suppliers
  id
  cnpj UNIQUE
  name
  email
  contact
  created_at
  updated_at

enterprises
  id
  name
  address
  created_at
  updated_at
```

UUID technical IDs are preferred for the local MVP. `transaction_date` reuses the exact Gate 5 effective `DOCUMENT`/`MESSAGE_TIMESTAMP` semantics. The local enterprise table is a development contract only and must later be adapted to the client's existing production enterprise table.

### Supplier ownership

Supplier matching is Writer/DF-adapter-owned because only the Writer has DF credentials and the supplier row participates in destination validation. The Writer normalizes and reads CNPJ inside the same transaction as the financial write and ledger:

- exactly one match -> `supplier_id` is the matched ID;
- zero matches -> `supplier_id = NULL`;
- more than one match -> data-integrity/configuration failure, zero financial INSERT;
- snapshot is preserved regardless of match;
- Writer/WhatsApp never inserts, updates, or deletes suppliers.

### Enterprise ownership and precedence

The BOT/Orchestrator must resolve `enterprise_id` before the Writer call. The Writer may validate the referenced enterprise read-only but must not create, update, or delete it.

```text
persistent chat binding
-> otherwise one-document enterprise clarification
-> otherwise zero Writer POST
```

`/empreendimento` is the sole persistent-selection command. It emits a deterministic dynamic list, persists the exact `position -> enterprise_id` snapshot, and always adds `N+1 - Limpar seleção`. The final option deletes the Platform-side binding. A one-document answer never creates a persistent binding.

The current Platform schema has no binding table, no separate durable external `chat_id`, no `ProcessingItem.enterprise_id`, and a `UserInteraction.question_type` CHECK limited to `transaction_direction`, `transaction_amount`, and `document_classification`. Therefore enterprise support requires separately approved additive Platform schema work. For the supported 1:1 MVP, chat identity is the existing conversation tuple `(organization_id, instance_id, user_id)`; group chats remain outside the current contract.

### Runtime governance boundary for enterprise resolution

Planning contract, not implementation:

- ownership belongs to the BOT DF business coordinator at the current authenticated/routed Orchestrator runtime boundary, never to Database Writer;
- `/empreendimento` is intercepted as a command before document ingestion and uses a dedicated command-selection session because frozen `UserInteraction` requires a `processing_item_id`;
- the command session must durably store tenant/conversation identity, generation/status, exact ordered `position -> enterprise_id` JSON mapping, clear-option position, stable outbound identity, inbound answer identity, and one-hour expiry;
- repeated command/event delivery replays the same generation/mapping; an expired/cancelled command session leaves the previous binding unchanged and mutates no ProcessingItem state/sequence, while an OPEN session temporarily blocks same-conversation business-interaction advancement;
- per-document selection extends the existing Gate 4E lifecycle additively with exact `question_type = enterprise_selection`, a durable option mapping on the interaction, and `ProcessingItem.enterprise_id`;
- unresolved per-document selection remains `WAITING_USER_INPUT`, blocks only the same conversation, uses the existing one-hour TTL, and follows existing cancellation/expiration semantics (`CANCELLED`/`EXPIRED`) until answered;
- an APPLIED enterprise answer materializes the actual `enterprise_id` on that ProcessingItem, returns it to `VALIDATING`, and is preserved across resume/recovery; it never updates the chat binding;
- a persistent binding is read before creating a per-document interaction and materializes the bound `enterprise_id` directly on the item;
- additive Platform migrations are required for binding, item enterprise ID, interaction option mapping/question type, and the command session; migration creation/execution requires separate authorization.

This design extends rather than redefines Gate 4E: existing financial question types, generation, outbound identity, ambiguity handling, FIFO, and recovery remain frozen.

### Cross-protocol interaction ownership — final HOLD closure

The single-human-interaction invariant spans both tables: an OPEN `UserInteraction` and an OPEN `EnterpriseCommandSession` must never coexist for `(organization_id, instance_id, user_id)`.

Repository inspection found no advisory-lock use. The smallest existing exact conversation authority is `conversation_queue_counters`, whose composite primary key is the conversation tuple and whose `lock_or_create_conversation_counter` path already uses race-safe `INSERT ... ON CONFLICT DO NOTHING` followed by `FOR UPDATE`. Both `dispatch_user_prompt` reservation and `open_enterprise_command_session` must acquire that row first, inspect both tables, and create/replay exactly one owner before commit. Locking `User` would be unnecessarily broader than an instance conversation. Independent partial unique indexes remain useful within each table but cannot enforce a cross-table invariant.

If a document interaction is OPEN, `/empreendimento` creates no command session, does not modify the document item or TTL, and sends one informational busy response keyed idempotently by the inbound Event ID:

```text
⚠️ Existe um lançamento aguardando sua resposta.

Conclua a pergunta atual antes de alterar o empreendimento deste chat.
```

Recognized commands are parsed before generic answers. `/empreendimento` therefore cannot be parsed as amount/direction input. Non-command text routes first to an OPEN command session, otherwise to an OPEN UserInteraction, otherwise to existing free-text handling. A numeric answer has exactly one owner.

An OPEN command session is a temporary claim barrier, not a queue item: media may be received, transcribed, normalized, sequenced, and reach READY, but `claim_next_ready_item` excludes conversations with an OPEN session and rechecks under the shared counter lock before `READY -> ACTIVE`. Other conversations continue. Closing the session as ANSWERED/EXPIRED/CANCELLED releases the earliest work without changing sequence; selected binding is then observed, while clear uses per-document fallback. A prompt-opening race lost to a command creates no UserInteraction and uses an idempotent deferred-VALIDATING checkpoint/resume path after the barrier closes.

Command reservation stores mapping/outbound identity before send. A durable dispatch checkpoint precedes WUZAPI; timeout becomes `OUTBOUND_OUTCOME_UNKNOWN` with no blind resend or regenerated mapping. A valid answer may resolve WAITING or OUTBOUND_OUTCOME_UNKNOWN. Dedicated `EnterpriseCommandAnswer` rows—not ProcessingItem-bound `UserAnswer`—record unique inbound Event identity and `APPLIED|REJECTED|LATE`; invalid answers preserve the session and original TTL, late answers do not reopen/change bindings, and row locking makes concurrent valid answers change binding at most once.

## 2. Boundary Separation

Two contracts must remain distinct:

```text
Orchestrator
  -> frozen internal HTTP request
Database Writer
  -> validated Writer DTO
DF destination adapter
  -> real DF database mapping (external schema input required)
```

### 2.1 Orchestrator -> Writer HTTP contract

This boundary already exists and is frozen by Gate 4. It carries platform identity, persistence identity, correlation identity, and currently available business facts. Gate 7 should preserve it unless the real DF schema proves that a required, non-derivable destination value is absent.

### 2.2 Writer -> real DF database mapping

This boundary is unresolved. Table names, column names, constraints, foreign keys, record-ID type, triggers, RLS, and write mechanics belong behind a Writer-owned adapter. They must never leak into Orchestrator logic or request routing.

## 3. Current POST `/internal/write` Request

The current `DBWriterClient.write` sends this shape:

```json
{
  "idempotency_key": "write_<processing_item_id>",
  "processing_item_id": "string",
  "organization_id": "string",
  "instance_id": "string",
  "user_id": "string",
  "correlation_id": "string",
  "document_type": "string",
  "payload": {
    "amount": "1200.00",
    "direction": "expense",
    "document_date": "2026-08-05",
    "document_type": "string-or-null",
    "instance_id": "string",
    "organization_id": "string",
    "processing_item_id": "string",
    "user_id": "string",
    "schema_version": "1.0"
  },
  "schema_version": "1.0"
}
```

Both models reject extra fields.

| Field | Current type | Required | Current source | Meaning and validation | Gate 7 disposition |
|---|---|---:|---|---|---|
| `idempotency_key` | `str` | yes | `ProcessingItem.writer_idempotency_key`, frozen as `write_{item.id}` | 1..512 characters; durable write identity | Preserve |
| `processing_item_id` | `str` | yes | `ProcessingItem.id` | Platform source item identity; not currently UUID-validated | Preserve; validate format and equality with payload |
| `organization_id` | `str` | yes | `ProcessingItem.organization_id` | Tenant/organization identity; not currently UUID-validated | Preserve; validate format and equality with payload |
| `instance_id` | `str` | yes | `ProcessingItem.instance_id` | Receiving instance identity; not currently UUID-validated | Preserve; validate format and equality with payload |
| `user_id` | `str` | yes | `ProcessingItem.user_id` | Conversation user identity; not currently UUID-validated | Preserve; validate format and equality with payload |
| `correlation_id` | `str` | yes | `ProcessingItem.correlation_id` | End-to-end trace identity; not currently UUID-validated | Preserve; validate bounded format |
| `document_type` | `str` | yes | `ProcessingItem.document_type` or client fallback `unknown` | Business document classification | Preserve conditionally; final accepted values depend on DF schema |
| `payload` | `WriteRequestPayload` | yes | Materialized `ProcessingItem` facts | Nested business and identity data | Preserve boundary; strengthen validation |
| `schema_version` | `str`, default `1.0` | no on wire | Client/default | HTTP contract version; only exact `1.0` accepted | Preserve |
| `payload.amount` | `Any | None`, default `None` | no | `str(item.amount)` | Manually parsed via `Decimal(str(value))`; must be `> 0` | Preserve field; replace `Any` with a strict decimal contract during implementation |
| `payload.direction` | `str | None` | no | `ProcessingItem.direction` | Must be `income` or `expense` | Preserve |
| `payload.document_date` | `str | None` | no | `ProcessingItem.document_date` | No current syntax/date validation | Preserve conditionally; validate ISO date when present |
| `payload.document_type` | `str | None` | no | `ProcessingItem.document_type` | Duplicate of envelope field; equality not currently enforced | Preserve for v1 compatibility; require canonical equality (`None` is compatible only with outer `unknown`) |
| `payload.instance_id` | `str` | yes | `ProcessingItem.instance_id` | Duplicate identity; equality not currently enforced | Preserve; require equality |
| `payload.organization_id` | `str` | yes | `ProcessingItem.organization_id` | Duplicate identity; equality not currently enforced | Preserve; require equality |
| `payload.processing_item_id` | `str` | yes | `ProcessingItem.id` | Duplicate identity; equality not currently enforced | Preserve; require equality |
| `payload.user_id` | `str` | yes | `ProcessingItem.user_id` | Duplicate identity; equality not currently enforced | Preserve; require equality |
| `payload.schema_version` | `str`, default `1.0` | no on wire | Client/default | Must equal outer version and supported version | Preserve; require equality |

The current request does **not** transmit `ProcessingItem.transaction_date`, `date_source`, resolved `enterprise_id`, or supplier CNPJ provenance. The newly approved local contract requires all four concepts. Therefore HTTP schema v1.0 is insufficient for Gate 7 and must not be silently reinterpreted.

The planned Gate 7 boundary uses an explicitly versioned v2.0 payload with:

- required effective `direction = expense`;
- required positive `amount`;
- required timezone-aware effective `transaction_date` plus `date_source`;
- required resolved `enterprise_id`;
- optional normalized `supplier_cnpj_snapshot`;
- system-controlled `origin = WHATSAPP`;
- stable Platform/tenant/idempotency identities.

The Writer itself applies `expense_type_id = NULL`, `comments = NULL`, `is_deleted = false`, and `deleted_at = NULL`; these values are not user-controlled request inputs.

The PRD example includes an `operation` field, but the implemented frozen Gate 4 request does not. Gate 7 has only one operation, `create_financial_entry`; adding the field provides no current capability and is not required unless production schema discovery identifies multiple approved write operations.

## 4. Current Writer Response and Orchestrator Outcome Contract

The HTTP response model is:

```json
{
  "status": "string",
  "idempotency_key": "string",
  "processing_item_id": "string",
  "committed_record_id": "string-or-null",
  "error_code": "string-or-null",
  "error_message": "string-or-null"
}
```

| Outcome | Current representation | Orchestrator interpretation | Gate 7 contract |
|---|---|---|---|
| `COMMITTED` | HTTP 200; record ID populated | `ProcessingItem -> COMPLETED` | Preserve exactly; record ID must be the real DF record identity serialized as a string |
| `REJECTED` | HTTP 200 for current business validation; client also maps HTTP 400/422 | `ProcessingItem -> PERSISTENCE_FAILED` | Preserve; deterministic, non-retryable request/business rejection |
| `RETRYABLE_FAILURE` | Recognized by client in a schema-valid HTTP 200 body; current Writer handler does not emit it | `ProcessingItem -> PERSIST_RETRYABLE` | Gate 7 may emit it only when non-commit is known |
| `OUTCOME_UNKNOWN` | Synthetic Orchestrator result for timeout, malformed/unknown response, transport uncertainty, or unrecognized status | `ProcessingItem -> PERSIST_OUTCOME_UNKNOWN` | Preserve; do not claim safe retry when commit may have happened |
| `NOT_FOUND` | GET endpoint HTTP 404 mapped by client | Reconciliation remains unresolved and makes no POST | Preserve frozen Gate 4 behavior |

Additional current HTTP behaviors:

- 401 for missing/invalid internal Bearer token;
- 400 for unsupported schema version or malformed status key;
- 409 for same idempotency key with different canonical payload;
- 413 for declared content length above 1 MiB;
- 422 for request-shape validation;
- generic 500 response is sanitized.

`error_message` should remain optional and must never contain raw database exception text. Stable `error_code` is the machine contract.

## 5. Destination Adapter Boundary

Gate 7 can and should isolate schema knowledge behind a Database Writer adapter, conceptually `db_writer/df_adapter.py`.

### 5.1 Input

An immutable, validated Writer DTO containing only:

- `processing_item_id`;
- `organization_id`;
- `instance_id`;
- `user_id`;
- `document_type`;
- `amount: Decimal`;
- `direction: expense` for this destination;
- `document_date: date | None`;
- effective `transaction_date` and `date_source`;
- resolved `enterprise_id`;
- optional normalized `supplier_cnpj_snapshot`;
- system-controlled `origin = WHATSAPP`.

The adapter must not accept a database URL, table name, schema name, raw SQL, credentials, or a user-selected destination.

### 5.2 Output

```text
DestinationWriteResult(committed_record_id: str)
```

The record ID is the real DF primary key or the authoritative identifier returned by an approved stored procedure. It is converted losslessly to a string at the HTTP boundary and stored in the Writer ledger.

### 5.3 Transaction ownership

The Writer application service owns the SQLAlchemy connection and one local PostgreSQL transaction. It:

1. acquires/serializes the idempotency key;
2. checks the canonical payload hash;
3. invokes the adapter with the same connection/transaction;
4. obtains the record ID;
5. writes the durable idempotency outcome;
6. commits exactly once.

The adapter performs no independent commit and opens no second connection.

### 5.4 Error contract

The adapter returns success or raises typed internal errors classified by the Writer service. Raw `DBAPIError`, DSN, SQL text, bound parameters, or provider error messages never cross the boundary.

### 5.5 One or multiple destination tables

The adapter may execute one or multiple statements/tables only if the real DF contract requires them. Every statement and the Writer ledger outcome must remain in the same database transaction. The HTTP caller remains unaware of the table count.

## 6. Idempotency Ledger Placement and Atomicity

### Decision

The durable Writer ledger must reside in the **same PostgreSQL database** as the real DF destination and must be written through the **same database connection and local transaction** as the business DML.

PostgreSQL local transactions do not atomically span separate databases. Therefore:

- a ledger in the Platform database plus business DML in the DF database is insufficient;
- a ledger in a separate Writer database plus business DML in the DF database is insufficient;
- an independent commit by the adapter is forbidden;
- no distributed transaction, outbox, or compensating architecture exists in the repository and none is introduced by Gate 7.

If production DF tables are in a different database from the current `write_ledger`, an equivalent ledger must be provisioned in the real DF database before Gate 7 can operate. Required logical fields are:

- globally unique `idempotency_key`;
- canonical payload hash;
- platform source/tenant identities;
- final status (`COMMITTED` or deterministic `REJECTED` if rejections are durably replayed);
- committed DF record ID;
- sanitized error code;
- attempt/audit timestamps.

The exact table name and DDL may preserve the existing `write_ledger` or use an owner-approved equivalent. Provisioning/migration ownership remains external input. No production object is created by this review.

## 7. Phase 1 Database Routing

### Closed contract

Phase 1 uses:

```text
one Database Writer deployment
  -> one configured DF_DATABASE_URL
  -> one real DF PostgreSQL database
```

The PRD describes a singular Writer secret and singular DF/Supabase destination. No authoritative requirement exists for selecting credentials or databases dynamically by organization. Organization identity is validated and stored/mapped inside the data model; it does not select a connection.

Dynamic per-organization database routing is out of Gate 7. Introducing it would require a credential registry, tenant-to-destination authority, per-destination connection pools, ledger uniqueness per destination, cross-destination reconciliation, secret rotation, and new isolation tests.

## 8. `DF_DATABASE_URL` Ownership

### Closed production contract

- `DF_DATABASE_URL` is mandatory for Database Writer production startup.
- Production has no usable default or localhost fallback.
- Only the Database Writer process receives it.
- BOT DF and Orchestrator settings must not define, load, log, or receive it.
- It is never included in `/internal/write`, responses, execution checkpoints, or business payloads.
- Tests inject a disposable URL through environment/fixture override only.
- Development may use an explicit disposable/local value, but the application must never silently synthesize one for staging/production.
- Configuration validation must fail startup with a sanitized code such as `DF_DATABASE_CONFIGURATION_INVALID`, not the raw value.

## 9. TLS Contract

### Architectural requirement

- Production and staging require PostgreSQL TLS with certificate and hostname verification.
- `sslmode=verify-full` is the production minimum.
- `sslmode=require` is insufficient because it encrypts without necessarily authenticating the server hostname.
- The connection must fail closed if TLS, CA validation, or hostname validation fails.
- There is no insecure production fallback to `prefer`, `allow`, `require`, or `disable`.
- The URL must use a DNS hostname matching the certificate SAN/CN; an IP is permitted only if the certificate explicitly covers it.

PostgreSQL documents that `verify-full` validates both the trusted CA chain and the requested hostname and recommends it for security-sensitive deployments: [PostgreSQL SSL Support](https://www.postgresql.org/docs/current/libpq-ssl.html).

### CA contract

- The deployment supplies either an infrastructure/provider CA bundle or an approved system trust store.
- The CA is mounted/injected into the Writer only and referenced by `sslrootcert`/driver connect arguments.
- The literal path is deployment-specific and does not block source implementation.
- Missing/unreadable CA material or hostname mismatch is a startup/readiness failure.
- CA contents, paths that reveal secret mount structure, and certificate private keys are never logged.

### Disposable development/test contract

- PostgreSQL 15 disposable tests may explicitly use `sslmode=disable` when the disposable server has no TLS.
- The insecure mode is permitted only under an explicit development/test environment and disposable database URL.
- Configuration tests must prove that staging/production rejects every mode weaker than `verify-full`.

## 10. Least-Privilege Writer Role

The runtime Writer role must be a non-owner, non-superuser login role with no role administration and no database creation capability.

Minimum privileges, narrowed after schema discovery:

- `CONNECT` on the one DF database;
- `USAGE` on the approved schema;
- `SELECT` and `INSERT` on the Writer idempotency ledger;
- `UPDATE` on the ledger only if the final algorithm explicitly uses a reserved-row lifecycle rather than a final-row insert;
- `SELECT`/`INSERT` only on approved destination tables/columns;
- `USAGE` and, only if required, `SELECT` on sequences used for generated identifiers;
- `EXECUTE` only on an approved stored procedure if the destination owner mandates a procedure boundary.

`RETURNING` may require `SELECT` on returned columns, so record-ID privileges must be verified against the chosen mapping.

PostgreSQL exposes `has_database_privilege`, `has_schema_privilege`, `has_table_privilege`, and `has_sequence_privilege` for explicit evidence: [PostgreSQL privilege inquiry functions](https://www.postgresql.org/docs/current/functions-info.html).

### Explicitly prohibited

- superuser, database owner, schema owner, table owner;
- `BYPASSRLS`, role membership with broader inherited rights, `SET ROLE` into an owner/admin role;
- `CREATEDB`, `CREATEROLE`, replication;
- database or schema `CREATE`;
- `DROP`, `ALTER`, `TRUNCATE`, `DELETE`;
- `UPDATE` except the specifically approved ledger columns if required;
- `REFERENCES`, `TRIGGER`, `MAINTAIN`, or DDL-like function execution unless separately justified;
- access to unrelated schemas, tables, sequences, routines, or secrets;
- grant option/admin option on any runtime privilege.

### G7-X09 disposable proof

An administrator fixture in disposable PostgreSQL 15 creates an isolated schema, destination objects, ledger, and restricted runtime role. A separate connection authenticated as that role must prove:

- permitted transaction, idempotency lookup/insert, destination insert, and record-ID return succeed;
- `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `DELETE`, unauthorized `UPDATE`, role creation, database creation, and unrelated-table reads fail;
- no privilege is held with grant option;
- RLS behavior matches the approved destination contract;
- all test changes are rolled back or disposable resources are destroyed afterward.

No production role or grant is created during Gate 7 implementation/testing.

## 11. Timeout and Retry Contract

Current frozen Orchestrator HTTP timeout: **10.0 seconds**.

Closed default timing budget:

```text
DB connect_timeout = 2 seconds
DB lock_timeout = 1 second
DB statement_timeout = 5 seconds per statement
Writer request-handling deadline = 8 seconds total
Orchestrator HTTP timeout = 10 seconds
```

`lock_timeout` is intentionally shorter than `statement_timeout`; PostgreSQL notes that a lock timeout equal to or above statement timeout is ineffective. These settings should be per Writer session/connection rather than global server defaults: [PostgreSQL client connection defaults](https://www.postgresql.org/docs/current/runtime-config-client.html).

Timing rules:

- connection acquisition plus one bounded transaction must leave at least two seconds for application classification, serialization, and HTTP return;
- the Writer enforces an eight-second monotonic request deadline; it applies the five-second statement timeout as a maximum bounded by the remaining request budget and starts no new database statement after the deadline is exhausted;
- Gate 7 does not add an internal loop that repeatedly executes the business transaction inside one HTTP request;
- known non-commit transient failures return `RETRYABLE_FAILURE`; the frozen Orchestrator owns bounded backoff/retry with the same key;
- any failure during commit or after a commit request where the outcome cannot be proven returns/causes `OUTCOME_UNKNOWN`;
- the Writer must never label an ambiguous commit as safely retryable;
- deployment may choose smaller values but must preserve the ordering `DB budget < Writer budget < 10-second Orchestrator timeout`; larger values require a separately approved coordinated contract change.

## 12. Database Error Classification

| Event | Classification | Durable/transaction rule | Safe error code |
|---|---|---|---|
| Connection refused before transaction | `RETRYABLE_FAILURE` | No transaction or DML | `DF_CONNECT_REFUSED` |
| Connect timeout | `RETRYABLE_FAILURE` | No transaction or DML | `DF_CONNECT_TIMEOUT` |
| Authentication failure | Startup/readiness configuration failure | Accept no writes | `DF_AUTH_CONFIGURATION_INVALID` |
| TLS verification/hostname failure | Startup/readiness configuration failure | Accept no writes; no insecure fallback | `DF_TLS_VERIFICATION_FAILED` |
| Lock timeout before DML/commit | `RETRYABLE_FAILURE` | Transaction rolled back; non-commit proven | `DF_LOCK_TIMEOUT` |
| Statement timeout with rollback/non-commit proven | `RETRYABLE_FAILURE` | Entire transaction rolled back | `DF_STATEMENT_TIMEOUT` |
| Statement timeout during/around commit with uncertain outcome | `OUTCOME_UNKNOWN` | Reconcile by same key | `DF_COMMIT_OUTCOME_UNKNOWN` |
| Serialization failure (`40001`) | `RETRYABLE_FAILURE` | Transaction aborted by PostgreSQL | `DF_SERIALIZATION_FAILURE` |
| Deadlock detected (`40P01`) | `RETRYABLE_FAILURE` | Transaction aborted by PostgreSQL | `DF_DEADLOCK` |
| Known approved FK violation caused by request/reference | `REJECTED` | Full rollback; no retry | `DF_REFERENCE_REJECTED` |
| Known approved CHECK violation | `REJECTED` | Full rollback; no retry | `DF_CHECK_REJECTED` |
| Known approved NOT NULL violation | `REJECTED` | Full rollback; no retry | `DF_REQUIRED_FIELD_REJECTED` |
| Unique violation on approved business key | Schema-dependent; default `REJECTED` | Never treat as idempotency replay | `DF_BUSINESS_UNIQUE_CONFLICT` |
| Exact idempotency-key unique race | Replay winner if hash matches; HTTP 409 if hash differs | Only SQLSTATE `23505` plus exact approved ledger constraint name | Existing frozen contract |
| Unknown/unapproved constraint name | Configuration/schema contract failure | Full rollback; fail closed; do not expose names externally | `DF_SCHEMA_CONTRACT_VIOLATION` |
| Connection loss before commit, non-commit proven | `RETRYABLE_FAILURE` | Same-key retry permitted | `DF_CONNECTION_LOST_PRE_COMMIT` |
| Connection loss during commit | `OUTCOME_UNKNOWN` | Reconcile by same key | `DF_COMMIT_OUTCOME_UNKNOWN` |
| Commit response lost | `OUTCOME_UNKNOWN` | GET ledger status; no blind POST | `DF_COMMIT_OUTCOME_UNKNOWN` |
| Malformed/unsupported business data detected before DML | `REJECTED` | Optional durable rejection replay; no business row | `INVALID_BUSINESS_PAYLOAD` |

Constraint classification must use SQLSTATE plus an allowlisted constraint identity learned from the approved schema. Error-message substring matching is forbidden. The exact idempotency race remains the existing fail-closed rule: SQLSTATE `23505` **and** the exact ledger unique constraint name.

## 13. Sanitization Contract

The following must never appear in HTTP responses, application logs, propagated exception text, metrics labels, or checkpoints:

- `DF_DATABASE_URL` or any raw DSN;
- database username/password or host credentials;
- internal service tokens;
- CA private material, client private keys, or secret file contents;
- raw SQL containing values or bound-parameter dumps;
- raw driver exception text when it may contain host, username, SQL, DSN, constraint data, or values;
- full business payloads, CPF/CNPJ, or document contents when unnecessary;
- environment dumps or settings object representations.

Safe structured identifiers:

- `processing_item_id`;
- `idempotency_key` (not a secret under the current contract);
- `organization_id` when operationally required;
- `correlation_id`;
- Writer operation name;
- internal sanitized error class/code;
- allowlisted SQLSTATE;
- attempt number and duration;
- outcome classification.

Logging uses structured fields and a fixed message. It does not interpolate raw exception objects. HTTP errors return stable error codes and generic messages only.

## 14. Request Validation

### 14.1 Closed pre-schema invariants

- outer and payload schema versions are both exactly `1.0` and equal;
- outer and payload `processing_item_id`, `organization_id`, `instance_id`, and `user_id` are equal;
- platform identity fields use the repository's authoritative UUID-compatible format;
- `idempotency_key` is non-empty, at most 512 characters, and equals the frozen source identity supplied by Orchestrator;
- `correlation_id` is present and bounded;
- `amount` is parsed without a `float` intermediary, finite, quantizable to two decimal places, and `> Decimal("0.00")`;
- Gate 5/6 direction remains canonical `income|expense`, but the expense-destination request accepts only `expense`; `income` is stopped before Writer dispatch;
- `document_date`, when present, is a valid ISO `YYYY-MM-DD` calendar date;
- effective `transaction_date` is present, timezone-aware, and paired with `date_source = DOCUMENT|MESSAGE_TIMESTAMP` under frozen Gate 5 semantics;
- `enterprise_id` is present and was resolved by the Platform/BOT before dispatch;
- `supplier_cnpj_snapshot`, when present, contains normalized CNPJ digits only;
- `origin` is generated by the trusted WhatsApp path as `WHATSAPP`, never accepted from untrusted user text;
- outer and payload `document_type` agree after the frozen v1 compatibility normalization (`payload.document_type is None` is equivalent only to outer `document_type == "unknown"`);
- extra fields are forbidden;
- request and payload contain no destination selector, table/schema identifier, SQL, database URL, username, password, token, CA, or credential material;
- request size remains bounded;
- authentication occurs before business processing;
- validation failure performs zero destination DML and is non-retryable.

### 14.2 Schema-dependent validation

External schema input is required to close:

- accepted destination `document_type` representation;
- direction representation if not `income|expense` in the DF table;
- target numeric precision/scale and maximum amount;
- whether `document_date`, effective `transaction_date`, and `date_source` are required;
- tenant/source identity storage and foreign-key/reference validity;
- required DF-specific fields, lookup IDs, business status/defaults, cost center/account/category, or ownership references;
- allowed business-key uniqueness;
- trigger/stored-procedure preconditions;
- RLS session context or policy predicates;
- record-ID type and generation.

## 15. HTTP and Schema Version

The current request carries `schema_version = "1.0"` at envelope and payload levels. The approved MVP now proves that v1 lacks non-derivable required facts: effective `transaction_date`, `date_source`, resolved `enterprise_id`, and supplier-CNPJ provenance. Gate 7 therefore requires explicit HTTP schema **v2.0** rather than silently changing v1 semantics.

The v2 change must:

- add the required fields under the new version;
- keep Gate 4 outcome/state semantics unchanged;
- do not expose real table or column names;
- reject unsupported/mismatched versions deterministically;
- update Orchestrator and Writer together only after HOLD and migration approval.

Database schema revision/adoption version is separate from the HTTP contract version.

## 16. Local MVP vs Final Client Database

### Local MVP — architecture closed, implementation not authorized

Likely separately approved local migrations will create:

- `financial_records` from the logical contract in section 1.1;
- `suppliers` with unique normalized CNPJ;
- minimal local `enterprises`;
- same-database Writer ledger, adapting/replacing the Gate 4 mock ledger as formally planned;
- Platform `whatsapp_chat_enterprise_bindings`;
- Platform `ProcessingItem.enterprise_id`;
- an additive per-document enterprise interaction contract (`enterprise_selection` plus durable option mapping);
- a separate durable command-selection session because current `UserInteraction` requires a `ProcessingItem` and cannot represent `/empreendimento` without fabricating one.

No migration is created or executed by this closure. The exact migration split and rollback/adoption strategy require formal plan/HOLD approval.

### Final client database — production adaptation blocked

- `financial_records` does not yet exist;
- `suppliers` does not yet exist;
- the enterprise table exists, but its schema/PK/type/active filter/FKs/RLS/grants are unknown;
- local names, UUID types, and relationships are not assumed to match production;
- production DDL/adoption scripts follow only after sanitized enterprise metadata is supplied;
- production migration execution remains separately unauthorized.

## 17. Exact External DF Schema Information Required

The database owner must provide the following without credentials or production data.

### 17.1 Database

- PostgreSQL server major/minor version: establishes supported SQL, identity, generated-column, RLS, and driver behavior.
- Database name: identifies the atomic transaction boundary; a sanitized placeholder is acceptable if the real name is sensitive.
- Schema/namespace name: required for explicit qualification and least-privilege `USAGE` grants.

### 17.2 Target object

- exact table, partitioned table, view, or procedure name;
- whether one logical write touches one or multiple objects;
- primary-key column(s), SQL type, generation method, and authoritative value to return;
- table/object owner;
- whether direct DML or a stored procedure is the supported interface.

### 17.3 Column mapping questionnaire

For every applicable concept, provide exact column name, SQL type, nullable flag, default, generated/identity behavior, accepted values/check, foreign key, and timezone semantics:

| Business concept | Current available source | Owner must answer |
|---|---|---|
| Organization identity | `organization_id` | Stored? Which column/type/FK? If not, how is ownership represented? |
| Instance identity | `instance_id` | Stored or intentionally omitted? Column/type/FK? |
| User identity | `user_id` | Stored or intentionally omitted? Column/type/FK? |
| Processing/source identity | `processing_item_id` | Stored? Unique/business key? |
| External/source reference | idempotency key and correlation ID available | Which reference is required and unique? |
| Direction | `income|expense` | Column/type; accepted values; mapping to debit/credit or entry/expense |
| Amount | positive `Decimal(18,2)` in Platform | Column precision/scale; currency; rounding policy; limits |
| Document date | ISO date or null | Column type; required/null; timezone not applicable if `date` |
| Effective transaction date | Exists in Platform but not current Writer request | Required column? `date` or `timestamptz`; DOCUMENT vs MESSAGE timestamp semantics |
| Date source | `DOCUMENT|MESSAGE_TIMESTAMP`, not current Writer request | Stored? Column/type/accepted values? |
| Document type | Gate 3 canonical label | Column/type/lookup/accepted values? |
| Created timestamp | Writer/DB time available | DB default or supplied; `timestamp` vs `timestamptz`; timezone |
| Mandatory DF-specific fields | Unknown | List every non-null/no-default input and authoritative source |

Also answer:

- currency fixed to BRL or stored explicitly;
- sign convention: positive amount plus direction, or signed amount;
- accounting category/account/cost center/company/entity requirements;
- soft-delete/status/approval fields and defaults;
- whether CPF/CNPJ or another DF identity must be written;
- whether source values require lookup/reference rows.

### 17.4 Constraints and behavior

- primary, unique, exclusion, and business-key constraints;
- all foreign keys and referenced object expectations;
- all CHECK/domain/enum constraints;
- indexes relevant to insertion/idempotency;
- triggers and their side effects;
- RLS enabled/forced state and policies;
- generated/identity columns and sequences;
- mandatory lookup/reference rows, supplied only as sanitized identifiers/allowed values;
- partition routing rules;
- table/procedure ownership and grants;
- whether triggers write additional tables.

### 17.5 Write semantics

- INSERT-only, UPSERT, stored procedure, or trigger-driven operation;
- conflict target and expected behavior if UPSERT is required;
- whether an update follows insert;
- required statement order for multiple objects;
- required transaction isolation beyond PostgreSQL `READ COMMITTED`, if any;
- advisory/application lock requirements, if any;
- returned record identity and whether it is available through `RETURNING` or procedure output;
- whether a preexisting business row may be treated as success;
- expected deterministic rejection codes.

## 18. Production Schema Questionnaire

The database owner can answer this compact questionnaire:

1. What PostgreSQL version, database, and schema contain the destination?
2. What exact object receives a financial entry: table, view, or stored procedure?
3. Does one entry touch one object or multiple objects/triggers?
4. What is the primary/returned record key and how is it generated?
5. For organization, instance, user, source item, direction, amount, document date, effective transaction date, date source, document type, source reference, and created timestamp: what are the column, type, nullability, default, accepted values, FK, and timezone semantics?
6. What mandatory fields have no default and are not in the current Writer request?
7. What PK, unique, FK, CHECK/domain/enum, exclusion, and partition constraints apply?
8. What triggers, generated columns, sequences, RLS policies, and required lookup rows apply?
9. Is the operation INSERT, UPSERT, procedure call, or multiple ordered statements?
10. What isolation/locking/conflict behavior is required?
11. Does an idempotency ledger already exist in this same database? If yes, provide sanitized DDL and status semantics.
12. Who owns creation/adoption of any missing ledger object: DF DBA or application migration?
13. What CA chain/hostname mechanism is provided for `verify-full`?
14. What runtime role/grants are approved for the Writer?

## 19. Read-Only Discovery Queries for the Database Owner

These queries are prepared for the database owner to run in a read-only session. Replace `target_schema` and `target_table` locally. Do not send credentials or a DSN.

Recommended session guard:

```sql
BEGIN READ ONLY;
```

### 18.1 Server/database identity

```sql
SELECT
    current_database() AS database_name,
    current_setting('server_version') AS server_version,
    current_setting('server_version_num') AS server_version_num;
```

### 18.2 Columns, defaults, identity, and generated expressions

```sql
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_schema,
    udt_name,
    character_maximum_length,
    numeric_precision,
    numeric_scale,
    datetime_precision,
    is_nullable,
    column_default,
    is_identity,
    identity_generation,
    is_generated,
    generation_expression
FROM information_schema.columns
WHERE table_schema = 'target_schema'
  AND table_name = 'target_table'
ORDER BY ordinal_position;
```

### 18.3 Primary, unique, foreign-key, CHECK, and exclusion constraints

```sql
SELECT
    con.conname AS constraint_name,
    con.contype AS constraint_type,
    con.condeferrable,
    con.condeferred,
    pg_get_constraintdef(con.oid, true) AS definition
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS rel ON rel.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS nsp ON nsp.oid = rel.relnamespace
WHERE nsp.nspname = 'target_schema'
  AND rel.relname = 'target_table'
ORDER BY con.contype, con.conname;
```

### 18.4 Key columns in declared order

```sql
SELECT
    tc.constraint_type,
    tc.constraint_name,
    kcu.ordinal_position,
    kcu.column_name,
    kcu.position_in_unique_constraint
FROM information_schema.table_constraints AS tc
LEFT JOIN information_schema.key_column_usage AS kcu
  ON kcu.constraint_catalog = tc.constraint_catalog
 AND kcu.constraint_schema = tc.constraint_schema
 AND kcu.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'target_schema'
  AND tc.table_name = 'target_table'
ORDER BY tc.constraint_type, tc.constraint_name, kcu.ordinal_position;
```

### 18.5 Foreign-key target columns and actions

```sql
SELECT
    tc.constraint_name,
    kcu.column_name,
    ccu.table_schema AS referenced_schema,
    ccu.table_name AS referenced_table,
    ccu.column_name AS referenced_column,
    rc.match_option,
    rc.update_rule,
    rc.delete_rule
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON kcu.constraint_catalog = tc.constraint_catalog
 AND kcu.constraint_schema = tc.constraint_schema
 AND kcu.constraint_name = tc.constraint_name
JOIN information_schema.referential_constraints AS rc
  ON rc.constraint_catalog = tc.constraint_catalog
 AND rc.constraint_schema = tc.constraint_schema
 AND rc.constraint_name = tc.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_catalog = rc.unique_constraint_catalog
 AND ccu.constraint_schema = rc.unique_constraint_schema
 AND ccu.constraint_name = rc.unique_constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'target_schema'
  AND tc.table_name = 'target_table'
ORDER BY tc.constraint_name, kcu.ordinal_position;
```

### 18.6 Indexes

```sql
SELECT indexname, indexdef
FROM pg_catalog.pg_indexes
WHERE schemaname = 'target_schema'
  AND tablename = 'target_table'
ORDER BY indexname;
```

### 18.7 Non-internal triggers

```sql
SELECT
    trg.tgname AS trigger_name,
    trg.tgenabled,
    pg_get_triggerdef(trg.oid, true) AS definition
FROM pg_catalog.pg_trigger AS trg
JOIN pg_catalog.pg_class AS rel ON rel.oid = trg.tgrelid
JOIN pg_catalog.pg_namespace AS nsp ON nsp.oid = rel.relnamespace
WHERE nsp.nspname = 'target_schema'
  AND rel.relname = 'target_table'
  AND NOT trg.tgisinternal
ORDER BY trg.tgname;
```

### 18.8 Owned/dependent sequences

```sql
SELECT
    seq_ns.nspname AS sequence_schema,
    seq.relname AS sequence_name,
    tbl_ns.nspname AS table_schema,
    tbl.relname AS table_name,
    att.attname AS column_name
FROM pg_catalog.pg_class AS seq
JOIN pg_catalog.pg_namespace AS seq_ns ON seq_ns.oid = seq.relnamespace
JOIN pg_catalog.pg_depend AS dep
  ON dep.objid = seq.oid
 AND dep.deptype IN ('a', 'i')
JOIN pg_catalog.pg_class AS tbl ON tbl.oid = dep.refobjid
JOIN pg_catalog.pg_namespace AS tbl_ns ON tbl_ns.oid = tbl.relnamespace
JOIN pg_catalog.pg_attribute AS att
  ON att.attrelid = tbl.oid
 AND att.attnum = dep.refobjsubid
WHERE seq.relkind = 'S'
  AND tbl_ns.nspname = 'target_schema'
  AND tbl.relname = 'target_table'
ORDER BY seq_ns.nspname, seq.relname;
```

### 18.9 Table/schema owner and RLS state

```sql
SELECT
    nsp.nspname AS schema_name,
    nsp_owner.rolname AS schema_owner,
    rel.relname AS object_name,
    rel_owner.rolname AS object_owner,
    rel.relkind,
    rel.relrowsecurity AS rls_enabled,
    rel.relforcerowsecurity AS rls_forced
FROM pg_catalog.pg_class AS rel
JOIN pg_catalog.pg_namespace AS nsp ON nsp.oid = rel.relnamespace
JOIN pg_catalog.pg_roles AS rel_owner ON rel_owner.oid = rel.relowner
JOIN pg_catalog.pg_roles AS nsp_owner ON nsp_owner.oid = nsp.nspowner
WHERE nsp.nspname = 'target_schema'
  AND rel.relname = 'target_table';
```

### 18.10 RLS policies

```sql
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check
FROM pg_catalog.pg_policies
WHERE schemaname = 'target_schema'
  AND tablename = 'target_table'
ORDER BY policyname;
```

PostgreSQL stores policy metadata in `pg_policy`, and policies apply when row security is enabled on the table: [PostgreSQL `pg_policy`](https://www.postgresql.org/docs/current/catalog-pg-policy.html).

### 18.11 Table, column, sequence, routine, schema, and database grants

```sql
SELECT *
FROM information_schema.role_table_grants
WHERE table_schema = 'target_schema'
  AND table_name = 'target_table'
ORDER BY grantee, privilege_type;

SELECT *
FROM information_schema.role_column_grants
WHERE table_schema = 'target_schema'
  AND table_name = 'target_table'
ORDER BY grantee, column_name, privilege_type;

SELECT *
FROM information_schema.usage_privileges
WHERE object_schema = 'target_schema'
ORDER BY grantee, object_type, object_name, privilege_type;

SELECT *
FROM information_schema.routine_privileges
WHERE routine_schema = 'target_schema'
ORDER BY grantee, routine_name, privilege_type;

SELECT
    datname,
    datacl
FROM pg_catalog.pg_database
WHERE datname = current_database();

SELECT
    nsp.nspname AS schema_name,
    nsp.nspacl AS schema_acl
FROM pg_catalog.pg_namespace AS nsp
WHERE nsp.nspname = 'target_schema';
```

Complete the guarded session with:

```sql
ROLLBACK;
```

These are metadata reads only. Catalog visibility is limited by the metadata reader's privileges.

## 20. Safer Discovery Artifacts

Preferred input, in order:

1. sanitized `CREATE TABLE`/`CREATE TYPE`/`CREATE SEQUENCE`/trigger/RLS DDL for only the destination and any required ledger object;
2. output of the read-only queries in section 19;
3. schema-only dump restricted to relevant objects, for example:

```text
pg_dump --schema-only --no-owner --no-privileges --table='target_schema.target_table' <database selected through an owner-controlled local service/profile>
```

If triggers, sequences, enum/domain types, referenced lookup tables, or the ledger are required, include their sanitized schema-only definitions as separate restricted objects.

Do not provide:

- passwords or password-bearing connection strings;
- production credentials, tokens, `.pgpass`, private keys, or client certificates;
- private CA key material;
- production rows or business payloads;
- public IPs/hostnames if considered sensitive;
- role password hashes;
- unrelated schemas, functions, policies, or grants.

Redact credential-bearing options, owners/role names if sensitive, hostnames, comments containing business data, and unrelated object definitions. Preserve structural column names/types/defaults/constraints, sanitized role capability categories, and required accepted values.

## 21. Gate 7 Contract Decision Table

| Decision | Status | Contract | Rationale | Implementation impact |
|---|---|---|---|---|
| Local MVP destination | CLOSED | `financial_records`, read-only `suppliers`, read-only `enterprises`; mock is not destination | Client approved conceptual MVP | Enables local implementation after HOLD approval |
| Production destination objects | EXTERNAL INPUT REQUIRED | Financial/supplier objects must be created; enterprise object must be adapted | Enterprise table exists but DDL is unavailable | Blocks production adapter/deployment, not local contract |
| Column mapping | EXTERNAL INPUT REQUIRED FOR PRODUCTION | Map each production concept using section 17; local MVP mapping is closed | Local contract does not prove client columns | Blocks final production adapter only |
| Local primary/record key | CLOSED | UUID preferred; return as string | Matches project conventions without binding production | Local implementation input |
| Production primary/record key | EXTERNAL INPUT REQUIRED | Return authoritative client identifier as string | Local UUID is not production evidence | Blocks production adaptation |
| Adapter boundary | CLOSED | Writer-owned adapter; Orchestrator knows no table details | Preserves frozen Gate 4 boundary | Local adapter after implementation HOLD; production mapping after schema input |
| HTTP request v1 | SUPERSEDED FOR GATE 7 DESTINATION | Preserve for frozen regressions only | Missing enterprise/date/supplier facts | Do not reinterpret v1 |
| HTTP request v2 | CLOSED FOR PLANNING | Expense-only payload with transaction date/source, enterprise ID, supplier snapshot, origin | Approved MVP requires non-derivable facts | Coordinated Orchestrator/Writer change after HOLD |
| HTTP outcome vocabulary | CLOSED | `COMMITTED`, `REJECTED`, `RETRYABLE_FAILURE`, synthetic `OUTCOME_UNKNOWN`, GET `NOT_FOUND` | Frozen Gate 4 semantics | No state-machine change |
| Ledger placement | CLOSED | Same real DF database, connection, and transaction as business DML | Required atomicity | Ledger object is a production prerequisite |
| Local ledger DDL/name | CLOSED CONCEPTUALLY | Same local DF DB; preserve/adapt `write_ledger` semantics | Atomicity already frozen | Exact migration pending approval |
| Production ledger DDL/name | EXTERNAL INPUT REQUIRED | Existing `write_ledger` or approved equivalent | Must know client objects/ownership | Determines adoption/deployment script |
| Local migration ownership | APPROVAL REQUIRED | Separate Gate 7 local MVP + Platform migration set | No migration authorized by documentation | Formal implementation plan/HOLD required |
| Production migration ownership | EXTERNAL INPUT REQUIRED | Client DBA or separately authorized adoption script | Client objects differ from local | No production execution authorized |
| One DB vs per-organization | CLOSED | One Writer deployment -> one `DF_DATABASE_URL` | Singular Phase 1 architecture; no dynamic routing requirement | No credential registry/router |
| Secret ownership | CLOSED | `DF_DATABASE_URL` only in Writer; mandatory production; no default | PRD SEC-005 and G7-X06/X07 | Writer config and isolation tests |
| TLS mode | CLOSED | Production/staging `verify-full` only | Encrypts and verifies server identity | Fail startup/readiness on weaker mode |
| CA policy | CLOSED ARCHITECTURALLY | Trusted provider/system CA supplied to Writer; fail closed | Literal CA delivery is infrastructure-specific | Path/value supplied at deployment |
| CA path/value | EXTERNAL INPUT REQUIRED | Owner supplies approved trust mechanism, not secrets here | Infrastructure-specific | Deployment configuration only |
| Hostname verification | CLOSED | DNS/IP must match certificate SAN under `verify-full` | Prevents MITM/wrong-server connection | Connection validation test |
| Writer role | CLOSED ARCHITECTURALLY | Non-owner/non-superuser restricted runtime role | G7-T05/G7-X09 | Disposable privilege suite |
| Exact grants/object names | EXTERNAL INPUT REQUIRED | CONNECT/USAGE and object-minimum grants only | Object names/procedures/sequences unknown | Final grant evidence after schema input |
| Connect timeout | CLOSED | 2 seconds | Leaves margin under 10-second caller timeout | Driver connection setting |
| Statement timeout | CLOSED | 5 seconds per statement | Bounded known failures under caller timeout | Per-session setting |
| Lock timeout | CLOSED | 1 second | Must be below statement timeout | Per-session setting |
| Retry owner | CLOSED | Writer classifies; frozen Orchestrator performs bounded same-key retry | Avoids nested retry loops and preserves FIFO | No Orchestrator policy change |
| Ambiguous commit | CLOSED | `OUTCOME_UNKNOWN`, reconcile by key, no blind POST | Commit may have happened | Preserve reconciliation |
| Request invariants | CLOSED FOR LOCAL PLANNING | Section 14.1 plus expense/enterprise/date/supplier invariants | Approved MVP closes local facts | Production mapping still adapter-specific |
| Schema version | CLOSED | Gate 7 destination uses explicit v2.0; v1 remains frozen regression | Required facts are absent from v1 | Versioned coordinated implementation |
| Expense-only destination | CLOSED | Only effective `expense` reaches Writer | Client-approved MVP | Pre-Writer eligibility guard required |
| `income` durable outcome | CLOSED | Early `IGNORED / INCOME_OUT_OF_SCOPE`; zero amount/enterprise question, supplier lookup, Writer/row/retry/final Gate 7 message; FIFO released | Explicit product approval | Add state/reason schema and coordinator guard |
| Financial defaults | CLOSED | expense type/comments null; false/null soft-delete; origin WHATSAPP | Client-approved MVP | Writer-owned defaults |
| Supplier lookup | CLOSED | Writer read-only exact normalized-CNPJ lookup; duplicate fail closed | Same DB/transaction and credential boundary | Adapter responsibility |
| Enterprise required | CLOSED | BOT resolves before Writer; Writer may validate read-only | Questioning does not belong in Writer | Platform prerequisite |
| `/empreendimento` | CLOSED FOR PLANNING | Dynamic deterministic list, durable mapping, last option clears persistent binding | Client-approved command | New command interaction storage required |
| Chat identity | CLOSED FOR MVP 1:1 | `(organization_id, instance_id, user_id)`; logical chat subject | Matches frozen conversation identity | Groups explicitly out of scope |
| Persistent binding | CLOSED CONCEPTUALLY | Platform-side unique binding, idempotent upsert/delete | Operational configuration, not finance | New Platform migration likely |
| Per-document enterprise | CLOSED CONCEPTUALLY | New `enterprise_selection`; answer materializes only item; no binding | Approved precedence | Extend Gate 4E schema/lifecycle additively |
| Cross-protocol interaction owner | CLOSED | Shared `conversation_queue_counters` `FOR UPDATE` lock plus cross-table check; per-table partial indexes retained | Enforces one human interaction under concurrency | Update prompt, command, claim, and resume transactions |
| Command routing/answers | CLOSED | Commands before generic answers; dedicated idempotent command answers; stable outbound mapping and no blind resend | Prevents numeric/command ambiguity | Router, session service, answer model/tests |
| Error vocabulary | CLOSED | Stable sanitized codes in section 12 | Prevents secret/SQL leakage and retry ambiguity | Typed internal exceptions/classifier |
| Final success notification | CLOSED / OUT OF SCOPE | Gate 8 only, after durable `COMMITTED` | Frozen Gate 6 contract | No Gate 7 WUZAPI change |

## 22. Remaining Input and Blockers

For production adaptation, the smallest sufficient next input is one sanitized artifact from the DF database owner:

- preferred: relevant sanitized DDL, including destination object, types/domains, constraints, sequences, triggers, RLS, referenced lookup definitions, and any existing Writer ledger;
- alternative: complete output from section 19 plus answers to section 18;
- alternative: restricted schema-only `pg_dump` for those objects.

The owner must also state:

- supported write method and record-ID output;
- mandatory fields and business value mappings;
- whether a same-database idempotency ledger exists;
- who owns provisioning/adoption of a missing ledger;
- approved production CA delivery mechanism and matching hostname category;
- approved runtime role/grant categories.

Until this is received:

- the production enterprise mapping, final adapter, exact production grants, and production deployment/adoption script remain blocked;
- local MVP architecture has no unresolved decision and no local blocker; explicitly authorized Phase A implementation is approved and complete;
- `.agents/IMPLEMENTATION_PLAN_GATE_7.md` is the approved and implemented local Phase A plan;
- Gate 7 is `APPROVED / COMPLETE`; implementation is complete, verification passed, final review is approved, and `G7-APPROVED = true`;
- Gate 8 remains `NOT STARTED`;
- no persistent/staging/production/remote migration or database execution is authorized.

## 23. Closure Result

Closed architectural contracts:

- local expense-only financial/supplier/enterprise contracts and defaults;
- early `income -> IGNORED / INCOME_OUT_OF_SCOPE`, non-blocking FIFO, non-recovery, and Gate 8 informational-message ownership;
- Writer HTTP v2 requirement while preserving v1 regression behavior;
- enterprise precedence, persistent binding, one-document fallback, and Writer prohibition while unresolved;
- Writer-owned supplier lookup and read-only supplier/enterprise access;
- Writer-owned destination adapter;
- same-database/same-transaction ledger atomicity;
- one Writer deployment and one Writer-owned DF URL for Phase 1;
- Writer-only mandatory production secret;
- `verify-full`, trusted CA, hostname verification, and fail-closed TLS;
- least-privilege role categories and prohibited privileges;
- 2s/1s/5s connection/lock/statement timeout budget below the frozen 10s HTTP timeout;
- deterministic error/outcome and sanitization rules;
- stable pre-schema validation invariants;
- HTTP schema version policy;
- exact read-only production discovery procedure.

External DF schema input remains required for production adaptation only. There is no unresolved local architecture decision and no local implementation blocker. Gate 7 Phase A is `APPROVED / COMPLETE`, `G7-APPROVED = true`, Gate 8 remains `NOT STARTED`, and no persistent/staging/production/remote migration or database execution is authorized.

## 24. Phase A implementation closure evidence

The authorized local MVP Phase A implementation passed formal review and is approved and complete. It implements the expense-only guard, `IGNORED / INCOME_OUT_OF_SCOPE`, enterprise command/binding/document resolution, shared cross-protocol serialization, Writer HTTP v2, local DF adapter, supplier lookup, atomic Writer idempotency, and both approved additive migrations.

Verification evidence:

- Gate 7 Correction Pass 2 focused suite: **126 passed, 0 skipped, 0 failed, 0 errors**; exact direct G7-X01 through G7-X41 evidence and evidence types are recorded in `TASKS_TESTS_GATES.md`;
- regressions: Gate 4 **210 passed**, Gate 5 **63 passed**, Gate 6 **64 passed**, all with zero skips/failures/errors;
- complete suite: **565 passed, 0 skipped, 0 failed, 0 errors**;
- compileall, Ruff, mypy, and `git diff --check`: **PASS**;
- PostgreSQL 15 disposable environment only; both Gate 7 migrations exercised there and all disposable resources removed afterward;
- no persistent, staging, production, Supabase, or remote database accessed;
- Gate 5 evaluator and WuzapiClient implementation unchanged; Gate 4 persistence behavior preserved by regression; no final notification and no Gate 8 work.

Production-client Phase B remains NOT IMPLEMENTED and blocked on the external inputs in section 22. Gate 7 final review is APPROVED and `G7-APPROVED = true`; Gate 8 remains NOT STARTED.
