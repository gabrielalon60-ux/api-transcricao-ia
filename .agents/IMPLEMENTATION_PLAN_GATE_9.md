# Gate 9 Implementation Plan — Operations and Observability

> **Status**: APPROVED / COMPLETE
> **Baseline**: Gate 8 APPROVED / COMPLETE / PUSHED at `139f8b441ca1af45a64889221172bce40653e832`
> **G9-D01 Token Authority**: CLOSED
> **G9-D02 Service-Usage Ownership**: CLOSED
> **G9-D03 Business Terminal Timestamp**: CLOSED
> **Gate 9 Implementation**: COMPLETE
> **G9-APPROVED**: true
> **Git closure**: COMPLETE / PUSHED TO `origin/main`
> **Gate 10**: NOT STARTED
> **Production Phase B**: NOT IMPLEMENTED
> **Database Execution in this pass**: DISPOSABLE POSTGRESQL 15 ONLY; DATABASE/CONTAINER/ARTIFACT CLEANUP COMPLETE

This document records the closed Gate 9 contract, implemented package, completed verification evidence, and explicit user approval on 2026-08-15. G9-T01 through G9-T10 and G9-X01 through G9-X06 are complete. `G9-APPROVED = true`.

## 1. Scope and classifications

P0 REQUIRED for Gate 9 remains exactly the operational tracker scope: executions, service usage, token accounting, duration, error lookup, operational queries, Platform DB backup, clean restore, local log retention, and incident runbooks.

P1 RECOMMENDED:

- database-aware readiness endpoints;
- persisted external-I/O latency for operations that currently have checkpoint-only timestamps;
- performance indexes only after disposable query-plan evidence demonstrates need.

POST-GATE / Gate 10:

- VPS execution, scheduling, alerting, HTTPS, firewall, external monitoring, centralized logs, production backup destination/encryption, real service traffic, and release operations.

## 2. Existing observability inventory

| Component | Exact path / owner | Physical storage and fields | Producer / consumer | Gate 9 coverage and gap |
|---|---|---|---|---|
| Shared structured logging | `packages/observability/src/observability/logging.py`; all FastAPI services | stdout/stderr JSON: timestamp, level, message, logger name, optional correlation ID | service logging calls / container runtime | Structured correlation exists; no retention/rotation contract, field allowlist, or central sink |
| Correlation middleware | `packages/observability/src/observability/middleware.py`; shared | request context plus `X-Correlation-ID` response header | Orchestrator, Transcription, Bot DF, Writer / logs and callers | HTTP correlation exists; no request-duration instrumentation |
| Platform Event | `packages/db/src/db/models.py`; Platform DB `events` | IDs, organization/instance/user, status, received/routed/failed timestamps, sanitized error | ingestion/router / routing and operational lookup | Authoritative ingress start and correlation anchor |
| Platform ProcessingItem | same model; Platform DB `processing_items` | lifecycle/status, attempts, leases, business/persistence state, error, created/extracted/activated/completed/updated timestamps | Gates 4–8 services / FIFO, recovery, notification | Sufficient for business state; terminal `completed_at` is not populated consistently for every terminal status |
| Platform Execution | same model; Platform DB `executions` | event/item/correlation, component, operation, idempotency/outbound identities, status/effect, external reference, attempt, timestamps, duration, sanitized error | Orchestrator/BOT services / idempotency, recovery, reconciliation, final notification, tests | Strong checkpoint ledger; most producers do not populate meaningful `duration_ms` and many rows are instantaneous checkpoints |
| Platform service usage | same model; Platform DB `service_usage` | source request/attempt, provider/model, non-null token counts defaulting to zero, cost, duration, linkage | **no runtime producer found** / no runtime consumer found | Schema exists but is not authoritative and cannot represent unknown tokens correctly |
| Transcription request | `apps/transcription/src/transcription/database/models.py`; Transcription DB `requests` | request ID, correlation, instance, status, received/start/completed, processing time, error | internal and legacy extraction / replay and usage joins | Request ID equals ProcessingItem ID for BOT traffic; BOT metadata does not persist organization ID in Transcription |
| Transcription attempt usage | same model; Transcription DB `usage_logs` | request/attempt, provider/model/status, start/end, nullable input/output/total/cached tokens, usage status, Decimal cost, currency/pricing, sanitized error | internal retry service and legacy extraction / legacy `/usage`, tests | Complete authoritative provider-attempt ledger; legacy `/usage` excludes BOT requests because application ID is null |
| Writer audit | `apps/db_writer/src/db_writer/models.py`; Writer DB `write_ledger` and `financial_records` | processing identity, idempotency/hash, status, record ID, error, attempts, timestamps | Database Writer / status reconciliation and tests | Persistence effect is traceable by processing item; correlation ID is carried in HTTP but is not stored in Writer ledger |
| Interaction audit | Platform `user_interactions`, `user_answers`, enterprise command tables | generation, state, wait/resolve timestamps, answer/error evidence | Gate 6/7 services / resume, replay, operator lookup | Supports human-wait derivation and stuck-interaction diagnosis |
| Health endpoints | service `main.py` files | process-only JSON response | HTTP service / manual caller | Liveness only; no DB/config readiness. Compose healthchecks only PostgreSQL |
| Docker health/logging | `docker-compose.yml` | PostgreSQL 15 healthcheck; named volume; default Docker logging | Docker / Compose | No app healthchecks and no explicit log rotation |
| Operational scripts | `scripts/` | legacy provisioning/migration helpers only | manual operator | No current backup, restore, operational report, or incident scripts |
| External metrics stack | manifests and dependency files | none | none | Prometheus/Grafana/Sentry/OpenTelemetry/Loki/ELK are not installed and are not required for Gate 9 |

## 3. Execution capability (G9-T01)

The existing `executions` schema is sufficient for Gate 9 checkpoint and correlation queries. It can answer what happened, which component/operation ran, attempt number, external identity/reference, checkpoint outcome, and sanitized error. Existing operation families include ingestion conflict, business claim/validation/recovery, prompt and answer lifecycle, enterprise command lifecycle, income terminalization, persistence dispatch/retry/unknown/reconciliation/final failure, cancellation/expiry, and final-notification reserve/dispatch/ACK/UNKNOWN.

No new Execution column is required for P0. No new Execution row should be added merely to duplicate existing durable state. Query-only G9-T01 is preferred.

Limit: `duration_ms` is usually null and `started_at == completed_at` for durable checkpoints. Gate 9 must not claim those rows measure external I/O. Precise per-operation I/O latency is P1 unless later contract closure promotes it.

## 4. Closed token and service-usage ownership contract (G9-D01, G9-D02, G9-T02)

**G9-D01 is CLOSED.** Transcription `usage_logs` is the authoritative source of provider token usage. One row represents one real provider attempt, including successful, failed, and retried attempts. Each attempt independently preserves input/output/provider-total tokens, availability, provider/model, cost, timestamp, and sanitized failure metadata where available. Unknown usage remains NULL and never becomes zero.

**G9-D02 is CLOSED.** Gate 9 does not introduce or populate a Platform `service_usage` projection. The existing table may remain without a runtime producer. G9-T02 means operational service-usage reporting from authoritative durable component-owned sources, not population of that table.

Authoritative ownership remains:

- Transcription provider usage: Transcription `usage_logs`;
- Platform lifecycle/operations: Platform `events`, `processing_items`, and `executions`;
- Writer business commit/reconciliation: Writer `write_ledger`;
- final notification: Platform `executions`.

The Gate 9 reporting layer may join/read those sources through explicit read-only connections. It does not copy usage, create a synchronization job, add Writer/WUZAPI usage rows, or create a second authority.

**Migration decision: NO GATE 9 MIGRATION REQUIRED.** Current authoritative schemas already contain the durable P0 facts, and Gate 9 does not project nullable token telemetry into Platform `service_usage`.

## 5. Token accounting definitions (G9-T03, G9-X01, G9-X02)

Document identity is one Platform `ProcessingItem`, which is also the Transcription `Request.id` for BOT traffic. It is not limited to successful documents.

Tokens per document reports:

- ProcessingItem/document ID, correlation ID, organization ID where resolvable, and Transcription request identity;
- include every actual provider attempt for that request, successful or failed;
- show known input, output, and provider total separately;
- aggregate actual attempt rows, never a synthetic summary row;
- expose attempt count, known-usage attempt count, unknown-usage attempt count, known cost sum where available, and `partial_usage`;
- if any attempt is unknown, known sums remain visible but are labeled partial; unknown is never converted to zero.

Tokens per organization:

- map Transcription request ID to Platform ProcessingItem ID, then use `processing_items.organization_id`;
- aggregate documents matching that organization whose authoritative Transcription attempt timestamp is inside the bounded UTC interval;
- show document count, attempt count, known-usage and unknown-usage attempt counts, known input/output/provider-total sums, known cost sum, and `partial_usage`;
- an organization total with unknown attempts is explicitly partial.

Provider `total_tokens` is summed as reported; it is not recomputed from input/output because providers may apply distinct semantics. Estimated cost is summed only where known and retains stored pricing/currency metadata.

## 6. Closed duration contract (G9-D03, G9-T04, G9-X03)

Authoritative E2E business duration is wall-clock time from `events.received_at` to durable business terminalization. It includes queueing, retries, backoff, and human clarification wait. It excludes Gate 8 final-notification delivery.

**G9-D03 is CLOSED.** `ProcessingItem.updated_at` is never used as a generic terminal timestamp. An item has `duration_available=true` only when the frozen lifecycle provides an unequivocal durable terminal timestamp.

Authoritative status/timestamp map from the existing source:

- `COMPLETED`: `processing_items.completed_at`;
- `IGNORED / INCOME_OUT_OF_SCOPE`: `processing_items.completed_at`;
- `PERSISTENCE_FAILED`: terminal persistence Execution `completed_at` from `PERSISTENCE_FAILED_FINAL` or `PERSISTENCE_RECONCILED_REJECTED`;
- `CANCELLED`: `USER_CANCELLED` Execution `completed_at`;
- `EXPIRED`: `USER_INPUT_EXPIRED` Execution `completed_at`;
- `EXTRACTION_FAILED`: unavailable because no unequivocal explicit terminal timestamp exists;
- terminal `FAILED`: unavailable because no unequivocal explicit terminal timestamp exists.

Nonterminal/ambiguous states also have no E2E business duration. Missing authoritative timestamps produce `duration_available=false`, a NULL duration, and an explicit unavailable reason. No timestamp is invented and no migration is required solely to make all historical terminal states measurable.

Human-wait duration is reported separately from interaction `waiting_since` to `resolved_at` (or current time for an open diagnostic view). It remains included in business E2E; the business E2E value is never reduced by human wait.

Active/processing subdurations are P1 and may be reported only when exact existing durable boundaries support their definition; they are not required for G9-X03.

Final-notification duration is separate and exact when available: `FINAL_NOTIFICATION_ACKNOWLEDGED` or `FINAL_NOTIFICATION_OUTCOME_UNKNOWN` Execution `completed_at` minus the matching `FINAL_NOTIFICATION_DISPATCHED` Execution `completed_at`. A reservation without dispatch or a dispatch without terminal finalization has no completed final-notification duration. It never changes business E2E duration.

All persisted timestamps are PostgreSQL timezone-aware values normalized/reportable in UTC. Durations are calculated at query time; no duration column migration is required.

## 7. Error and correlation contract (G9-T05, G9-X04)

Existing bounded vocabularies remain separate:

- Event routing/outbound: e.g. `BOT_ROUTING_FAILED`, `WUZAPI_SEND_FAILED`;
- ProcessingItem/queue/extraction/persistence: e.g. `QUEUE_CAPACITY_EXCEEDED`, Transcription errors, Writer response errors, `MAX_PERSISTENCE_ATTEMPTS_EXCEEDED`;
- Execution recovery/interaction/outbound: e.g. `USER_EVENT_PAYLOAD_MUTATED`, `NO_WAITING_ITEM`, `INTERACTION_ALREADY_CLOSED`, `CONTRADICTORY_LEDGER`, `LATER_EFFECT_EXISTS`, `OUTBOUND_OUTCOME_UNKNOWN`;
- Transcription validation/provider/persistence: bounded validation errors, `PROVIDER_TIMEOUT`, `PROVIDER_RATE_LIMITED`, `PROVIDER_TEMPORARY_ERROR`, `PROVIDER_AUTH_ERROR`, `SYSTEM_PROMPT_INVALID`, `PERSISTENCE_ERROR`, `INTERNAL_ERROR`;
- Writer: `INVALID_BUSINESS_PAYLOAD`, `INVALID_ENTERPRISE_ID`, `ENTERPRISE_NOT_FOUND`, supplier/schema/constraint errors, transient destination errors, and `AMBIGUOUS_COMMIT`;
- answer ledgers: parsing, late, closed-session, and invalid-enterprise-choice codes.

The operational lookup accepts only a validated correlation ID and returns sanitized identifiers and state in this order:

`Event -> ProcessingItem -> Executions -> UserInteraction/UserAnswer -> Transcription Request/UsageLog by ProcessingItem ID -> Writer write_ledger by ProcessingItem ID -> final-notification Executions`.

It must not return raw payloads, document contents, prompts/responses, SQL, stack traces, credentials, database URLs, tokens, phone numbers, CPF/CNPJ, or raw provider bodies.

## 8. Operational interface (G9-T06)

P0 interface: a versioned, read-only CLI plus documented SQL semantics. No web dashboard or new application endpoint is required.

Required reports:

- recent ingress/terminal volume and success/failure counts;
- current blocked statuses, including `WAITING_USER_INPUT`, `PERSIST_RETRYABLE`, and `PERSIST_OUTCOME_UNKNOWN`;
- final-notification UNKNOWN count;
- average and PostgreSQL `percentile_cont(0.50/0.95)` E2E duration where terminal time is available;
- tokens by document and organization with unknown counts;
- failures grouped by bounded error code;
- correlation-ID drilldown.

Every reporting database connection must open an explicit PostgreSQL transaction, establish `READ ONLY` before any reporting statement, and set `statement_timeout = '30s'`, `lock_timeout = '5s'`, and `idle_in_transaction_session_timeout = '30s'`. Metadata lookup and reporting SQL must execute inside that boundary. The command explicitly commits or rolls back; no SQL may run before, after, or outside the boundary except connection establishment and transaction/read-only/timeout setup. Failure to establish any guard fails closed. Application-level intent alone is not a read-only guarantee; `INSERT`, `UPDATE`, `DELETE`, DDL, migrations, repair SQL, and mutation-requiring locks are prohibited.

Time-bounded commands default to the previous 24 UTC hours and accept at most 31 days. They reject missing/unbounded, inverted, malformed, or over-maximum intervals when an explicit interval is supplied; they never clamp. Detailed results default to 100 rows and accept 1 through 1,000 only. Single-document and correlation reports apply that maximum independently to every attempt, execution, interaction/answer, and ledger collection. Aggregate SQL may cover the accepted interval, but every detailed collection returned or inspected is bounded.

Detailed collections use deterministic `(authoritative_timestamp DESC, stable_id ASC)` ordering and keyset cursors containing only those sanitized values. Each query fetches at most `limit + 1`, emits at most `limit`, sets `truncated=true` when another row exists, and provides a next cursor; no automatic pagination or unbounded export occurs.

## 9. Platform backup and restore (G9-T07/T08, G9-X05/X06)

Scope is Platform PostgreSQL only. Transcription, Writer/client DF, Supabase, and production databases are excluded.

Dedicated configuration names:

- reporting: `G9_PLATFORM_DATABASE_URL`, `G9_TRANSCRIPTION_DATABASE_URL`, and command-optional `G9_WRITER_DATABASE_URL`;
- backup: `G9_BACKUP_SOURCE_DATABASE_URL`, `G9_BACKUP_EXPECTED_DATABASE_NAME`, `G9_BACKUP_DISPOSABLE_CONFIRMATION`, `G9_BACKUP_OUTPUT_ROOT`, `G9_BACKUP_OUTPUT_DIRECTORY`, and optional `G9_BACKUP_RETENTION_COUNT`;
- restore: `G9_RESTORE_ADMIN_DATABASE_URL`, `G9_RESTORE_ADMIN_DATABASE_NAME`, `G9_RESTORE_TARGET_OWNER`, `G9_RESTORE_DISPOSABLE_CONFIRMATION`, `G9_RESTORE_OWNERSHIP_ROOT`, and `G9_RESTORE_OWNERSHIP_DIRECTORY`.

Every required value is explicit. Scripts do not discover/load `.env`, read generic `DATABASE_URL`, use application configuration, borrow another service's DSN, or infer host, port, database, user, directory, owner, or authorization. Credentials are accepted only inside the dedicated DSN environment variables, never as command-line arguments. Missing or malformed configuration fails closed. Complete DSNs, passwords, environment contents, and connection command lines never appear in stdout, stderr, manifests, checksum files, ownership markers, sidecars, exceptions, or tracebacks.

### Connection and PostgreSQL 15 preflight

Backup and restore parse the dedicated DSN without logging it and require exactly one explicit host, port, database, and user. The system resolver must return at least one address and every resolved IPv4/IPv6 address must be loopback. Unix sockets, unspecified/wildcard addresses, multi-host/alternate-host DSNs, redirects, and libpq connection-service indirection are rejected. After connecting, `inet_server_addr()` and `inet_server_port()` must identify a loopback address and the exact requested port; ambiguity or mismatch fails closed.

Before any dump, target database, ownership sidecar, or other material artifact exists, perform the following exact client-version preflight. Backup checks both `pg_dump` and `pg_restore` because catalog validation is mandatory; restore checks `pg_restore`.

1. Invoke each required executable with exactly `--version` and require subprocess exit code 0.
2. Decode stdout deterministically as text. Accept exactly one non-empty output line, allowing only its terminal LF or CRLF.
3. Reject decoding failure, missing/empty output, leading whitespace, additional lines, NUL/control characters, or an executable-name mismatch.
4. Parse `pg_dump` with `^pg_dump \(PostgreSQL\) (?P<version>[0-9]+(?:\.[0-9]+)*)(?: \([^\r\n]*\))?$`.
5. Parse `pg_restore` with `^pg_restore \(PostgreSQL\) (?P<version>[0-9]+(?:\.[0-9]+)*)(?: \([^\r\n]*\))?$`.
6. Interpret the first numeric component of the captured version as the client major and require it to equal 15.
7. Do not infer compatibility from executable availability or successful startup alone.
8. Missing executables, nonzero version-command exits, malformed output, executable-name mismatch, parsing failure, and any major other than 15 map to exit code 3.
9. Diagnostics are sanitized and never reproduce raw subprocess output when it could contain unexpected data.

After the loopback connected-server identity check, execute exactly `SELECT current_setting('server_version_num')`. Require exactly one non-NULL scalar string matching `^[0-9]{6}$`, parse it as a base-10 integer, and require `150000 <= server_version_num < 160000`. Do not parse the human-readable `server_version` string. Missing, malformed, multirow, nonnumeric, NULL, or non-15 results map to exit code 3. Complete the client and server checks before dump creation, restore-target creation, ownership-sidecar creation, or any other material artifact.

### Backup source, artifact, and retention

Before artifact creation, `G9_BACKUP_EXPECTED_DATABASE_NAME` must equal `G9_BACKUP_DISPOSABLE_CONFIRMATION`, equal `current_database()`, and match `^gate9_platform_[a-f0-9]{12,32}$`. `postgres`, `template0`, and `template1` are forbidden. The strict loopback and PostgreSQL 15 preflight must also pass.

Backup format and artifact:

- PostgreSQL 15 `pg_dump --format=custom --no-owner --no-privileges`;
- UTC/group filename `platform-YYYYMMDDTHHMMSSZ-<32 lowercase hex>.dump`;
- adjacent same-group SHA-256 and sanitized JSON manifest containing schema version, tool-directory identifier, artifact-group identifier, tool major version, UTC timestamp, database name (not host/user), artifact size, and hash;
- validate with `pg_restore --list` plus non-empty catalog entries;
- output root and exact directory must be explicit and outside the repository; tests use an owned temporary root and clean only verified owned files;
- credentials are passed privately to child processes without printing and never appear in logged arguments;
- all source identity, authorization, loopback, and version checks occur before creating the dump or a sidecar.

The backup script may create the exact configured output directory if absent and then atomically create a regular `.gate9-platform-backup-owner.json` marker containing schema version 1 and a cryptographically generated tool-directory identifier. A pre-existing directory is usable only when that marker already exists, is regular, and parses/validates. The resolved output path must exactly equal the validated configured target beneath the configured root. The root, every relevant ancestor below it, the directory, marker, and artifacts must be regular/non-link objects as applicable; symlinks, junctions, mount substitutions, and Windows reparse points are rejected.

Only a complete group of three regular direct children—dump, checksum, and manifest—sharing the generated group identifier and validated tool-directory identifier is retention eligible. Cleanup is nonrecursive, never follows manifest paths, and deletes only those three individually known files after verifying their resolved parent equals the owned directory. It never deletes directories, unknown files, partial groups, links/reparse points, or unowned files. Retention defaults to 5 groups, accepts only 1 through 5, and also removes validated groups older than 7 days; invalid values are rejected rather than clamped. Any ownership or containment ambiguity fails closed without deletion.

Local artifacts are test evidence, not production retention. Encryption, off-host storage, production permissions, and scheduled production retention are Gate 10 decisions.

### Restore creation, ownership, and cleanup

- `G9_RESTORE_ADMIN_DATABASE_URL` is mandatory, uses the strict loopback checks, and must connect to the explicit `G9_RESTORE_ADMIN_DATABASE_NAME`; it is used only for target creation, catalog identity verification, and owned-target cleanup;
- `G9_RESTORE_DISPOSABLE_CONFIRMATION` must equal the exact literal `GATE9_DISPOSABLE_RESTORE`; no default or alternative value is accepted;
- generate a cryptographically random invocation identifier and target name `gate9_restore_<32 lowercase hex>`; an arbitrary/operator-supplied target name is never accepted;
- validate the ownership root/directory with the same no-link/reparse/resolved-containment rules and atomically create a regular invocation sidecar before database creation;
- create a new database only; any pre-existing target is rejected. Set owner to the explicit `G9_RESTORE_TARGET_OWNER`, store the invocation identifier in a database comment, and verify exact name, owner, and marker through PostgreSQL catalogs;
- update the invocation sidecar atomically with the verified server address/port, database name, owner, and marker, without credentials, only after ownership is established;
- use `pg_restore --exit-on-error --no-owner --no-privileges` only after all ownership checks pass; preserve Platform schema/data and `alembic_version`;
- validate representative Gate 4–8 rows, constraints, indexes, operation identities, and counts;
- immediately before cleanup, revalidate loopback server identity plus the exact generated name, owner, database comment/invocation marker, and local sidecar against the current invocation;
- drop only that exact invocation-owned target. If any revalidation is missing, mismatched, or ambiguous, drop nothing and emit sanitized manual-cleanup evidence; never use partial-name patterns or clean a pre-existing/operator-supplied database.

## 10. Logging and retention (G9-T09)

Current state: applications emit stdout/stderr; FastAPI services generally use the shared JSON formatter and correlation context; the standalone FIFO worker uses basic logging; Docker Compose uses the default logging driver with no explicit rotation. No file logger or centralized collector is configured.

Closed local Gate 9 policy:

- stdout/stderr only; no application-managed log files;
- local Docker `json-file` rotation capped at `max-size=10m`, `max-file=5` for each service;
- INFO default, bounded sanitized ERROR messages, no raw exception/provider payload for operator reports;
- local rotation is size/count bounded, not a production time-retention promise;
- production centralized retention, alerting, storage, access control, and exact days remain Gate 10.

Gate 9 records this policy in documentation only. `docker-compose.yml` and all runtime/deployment configuration remain unchanged; actual local/VPS enforcement requires a later explicit HOLD or Gate 10 authorization.

## 11. Incident runbook scope (G9-T10)

Each incident entry must contain signal, bounded query/log evidence, first diagnostic action, safe recovery, prohibited actions, and escalation condition.

| Incident | First evidence / safe action | Prohibited action / escalation |
|---|---|---|
| Transcription/Gemini unavailable | request/usage error and extraction backlog; verify config/liveness, allow bounded existing retry | no synthetic success or manual token zero; escalate sustained terminal/provider-auth errors |
| WUZAPI unavailable | DISPATCHED/UNKNOWN checkpoints; verify configuration/liveness | no blind resend after DISPATCHED; escalate growing UNKNOWN |
| Platform DB unavailable | connection failure and worker/API health; stop new mutation paths safely, restore connectivity | no schema repair/migration; escalate data-integrity symptoms |
| Writer DB unavailable | persistence retry/unknown ledger; verify Writer readiness and reconcile only through frozen path | no blind write replay for unknown; escalate unknown backlog |
| PERSIST_RETRYABLE backlog | age/count/attempt query; restore dependency and allow bounded retry | no attempt reset or direct status edit |
| PERSIST_OUTCOME_UNKNOWN backlog | correlation and Writer status lookup; run frozen reconciliation | no blind POST resend |
| final notification UNKNOWN | final execution chain; leave outcome unknown | no blind WhatsApp resend |
| FIFO stuck | oldest blocking item, lease, interaction/command barrier; use existing recovery/sweeper | no sequence/status mutation by SQL |
| enterprise command stuck | open session TTL/answer evidence; use existing expiry/cancel flow | no binding/session manual rewrite |
| token/cost spike | provider attempt aggregation by org/model/pricing version | no deletion/repricing; escalate unexplained retries/model change |
| backup failure | tool exit, manifest/hash/list validation; retain prior valid artifact | no overwrite of last good backup |
| restore failure | target ownership and `pg_restore` error; remove only owned disposable target | no cleanup of source/preserved DB; escalate compatibility/integrity failures |

The backup/restore runbook begins with a prominent warning: Gate 9 procedures are disposable PostgreSQL 15 validation only; they are not production restore or VPS deployment authorization. Gate 10 discussion does not authorize production use. Gate 9 documentation contains no production restore command example, and any future production procedure requires separate approval plus environment-specific safeguards.

## 12. Health/readiness and external stack

Existing `/health` endpoints prove process responsiveness only. They do not prove database reachability or dependency configuration. This suffices for current liveness but not full readiness.

Database-aware `/ready` is P1 RECOMMENDED; environment-specific container health wiring is Gate 10. It is not required to satisfy G9-X01–X06 and must not expand Gate 9 P0 without approval.

No Prometheus, Grafana, Sentry, OpenTelemetry, Loki, ELK, or other external monitoring dependency is required. PostgreSQL durable telemetry plus structured logs and bounded CLI reports satisfy P0 without new runtime dependencies.

## 13. Privacy and sanitization

- correlate by UUIDs, stable operation identities, and correlation ID;
- phone numbers, CPF/CNPJ, and other PII are omitted; if cross-log correlation is later required, use existing HMAC-SHA256 `hash_pii` with `LOG_PII_HASH_KEY`;
- never print registration secrets, API/service tokens, database URLs, raw SQL, document payloads, prompts/responses, or provider bodies;
- error output uses bounded stored codes and sanitized messages only;
- stack traces remain local diagnostic data and are not included in operator report output;
- backup manifests contain no host, username, or credential.

## 14. Exact acceptance contracts

### G9-X01 — Tokens per document

Setup disposable PostgreSQL 15 Platform and Transcription databases, one ProcessingItem/request, and three authoritative Transcription attempt rows: known success, known failed retry, and unknown usage. Run the actual read-only `tokens-document` reporting boundary. Assert exact input/output/provider-total and known-cost sums, attempt count 3, known/unknown counts, partial status, correct durable mapping/IDs, and no zero substitution. Evidence: integration/disposable PostgreSQL; no external service.

### G9-X02 — Tokens per organization

Setup disposable Platform and Transcription databases with two organizations and multiple ProcessingItems/requests/attempts inside and outside the requested UTC interval. Run the actual read-only `tokens-organization` boundary. Assert only the selected organization/window is aggregated, document/attempt/known/unknown counts and token/cost sums are exact, partial status is correct, and mapping occurs through Platform ProcessingItem ID. Evidence: integration/disposable PostgreSQL; no external service.

### G9-X03 — E2E duration

Use disposable PostgreSQL 15 and representative durable Gate 4–8 rows produced through real/local lifecycle paths where feasible. Assert Event receipt, authoritative business terminal timestamp, business E2E duration, closed human-wait duration, and dispatch-to-ACK/UNKNOWN final-notification duration remain semantically distinct. Also create a terminal `EXTRACTION_FAILED` or `FAILED` case without an authoritative terminal timestamp, deliberately assign a tempting later `updated_at`, and assert the CLI ignores it: `duration_available=false`, NULL duration, and the explicit unavailable reason. Expected values are independently fixed, not calculated by the reporting function. Evidence: integration/disposable PostgreSQL; no external service.

### G9-X04 — Failure by correlation ID

Drive a deterministic local Writer rejection to terminal `PERSISTENCE_FAILED` with a bounded code. Query by correlation ID and assert Event, ProcessingItem, persistence Execution attempts/effects, Writer ledger identity/status/error, and final-notification checkpoint are reconstructed without PII/payload/secret fields. Evidence: local integration with disposable Platform and Writer PostgreSQL; no external service.

### G9-X05 — Valid backup artifact

Migrate and seed a disposable PostgreSQL 15 Platform source with representative Gate 4–8 rows. Run the actual backup script boundary. Assert custom-format artifact exists, is nonempty, manifest/hash match, `pg_restore --list` succeeds, and Platform tables plus `alembic_version` are present. Assert credentials and complete DSNs are absent from stdout, stderr, manifest, checksum, ownership marker, and every sidecar. Version cases cover valid `pg_dump` 15, valid `pg_restore` 15, valid PostgreSQL 15 output with a parenthesized distribution suffix, executable-name mismatch, leading whitespace, an extra output line, empty output, decoding failure, nonzero version-command exit, malformed version, and client majors below and above 15. Server cases cover one valid six-digit value in `[150000, 160000)` plus malformed, NULL, multirow, below-15, and above-15 results. Every rejection maps to exit code 3 and occurs before material artifact creation. Evidence: subprocess plus disposable PostgreSQL 15; no external service.

### G9-X06 — Restore in clean environment

Use the real X05 custom-format artifact and invoke the actual restore script against a physical disposable PostgreSQL 15 server. Assert valid `pg_restore` 15 parsing and a valid `[150000, 160000)` `server_version_num` before generated target identity/invocation ownership, then verify representative Gate 4–8 rows, constraints, unique operation identities, indexes, migration version, and expected counts/data identities. Directly test that malformed/NULL/multirow/below-15/above-15 server results and every invalid client-version class exit 3 before target or ownership-sidecar creation. Also test missing disposable authorization, missing administrative DSN, pre-existing target rejection, ownership-marker mismatch, owner mismatch, cleanup refusal after any revalidation failure, and successful cleanup only for the exact invocation-owned target. Same-source, nonempty, non-disposable, remote, and user-supplied targets fail closed. Evidence: subprocess plus disposable PostgreSQL 15; no external service.

## 15. Proposed implementation inventory for HOLD review

NEW:

- `scripts/operations/gate9_report.py` — bounded read-only operational CLI;
- `scripts/operations/platform_backup.py` — guarded custom-format backup and manifest;
- `scripts/operations/platform_restore.py` — guarded clean disposable restore;
- `.agents/GATE_9_INCIDENT_RUNBOOK.md`;
- `.agents/GATE_9_PLATFORM_BACKUP_RESTORE_RUNBOOK.md`;
- `tests/test_platform_gate9_usage_reporting_disposable_postgres.py`;
- `tests/test_platform_gate9_operational_queries_disposable_postgres.py`;
- `tests/test_platform_gate9_backup_restore_disposable_postgres.py`;
- `tests/test_platform_gate9_operations_unit.py`.

No shared helper is proposed or authorized. If implementation later demonstrates that one is genuinely necessary, stop and request exact scope authorization before creating it.

Acceptance-to-file map:

- G9-X01 and G9-X02: `tests/test_platform_gate9_usage_reporting_disposable_postgres.py`;
- G9-X03 and G9-X04: `tests/test_platform_gate9_operational_queries_disposable_postgres.py`;
- G9-X05 and G9-X06: `tests/test_platform_gate9_backup_restore_disposable_postgres.py`;
- argument validation, numeric bounds, read-only/timeout setup failure, deterministic ordering/truncation, no-fallback environment handling, sanitization, exact client/server version parsing, loopback resolution, link/reparse rejection, ownership mismatch, target rejection, cleanup refusal, and exit-code mapping: `tests/test_platform_gate9_operations_unit.py`.

The unit version matrix covers the two exact executable regexes, plain and parenthesized-suffix PostgreSQL 15 output, executable-name mismatch, leading whitespace, additional lines, empty output, decoding failure, nonzero exit, malformed version, below/above-15 client majors, exact six-digit server scalar validation, malformed/NULL/multirow server results, and below/above-15 server ranges. It also proves sanitized code-3 rejection before the material-artifact/target-creation boundary. The integration file proves the valid real `pg_dump --version`, `pg_restore --version`, `server_version_num`, dump, and restore path and repeats the pre-creation ordering assertion at the actual script/CLI boundary.

The disposable backup/restore integration file also proves physical connected-server identity, tool-owned artifact grouping/retention containment, real subprocess boundaries, generated restore identity/catalog marker verification, and exact-target cleanup. Production exposes no target-name or invocation-ID override; the pre-existing-name collision case may drive the same CLI `main()` boundary with a test-controlled cryptographic generator, while the physical happy path remains a subprocess invocation.

MODIFY:

- `.agents/CURRENT_STATE.md`, `.agents/TASKS_TESTS_GATES.md`, and this plan for governance closure.

UNCHANGED / FROZEN:

- all Gate 4–8 business services, including ingestion, extraction dispatch, FIFO/business rules, interaction, persistence, final notification, WUZAPI, and Writer behavior;
- all Gate 4–8 tests and migrations;
- Transcription request/provider/retry/usage producers and schemas;
- `packages/db/src/db/models.py`, all models, and all migrations;
- dependencies and lockfile;
- `docker-compose.yml` and runtime/deployment configuration;
- production/VPS configuration and production Phase B adapter.

## 16. Script interfaces and failure behavior

Repository convention: `scripts/operations/` because existing operator utilities live under `scripts/`, while the new namespace separates non-migration operations.

Scripts accept only the explicit flags and dedicated environment variables defined above. They never load `.env`, use generic `DATABASE_URL`, fall back to application/cross-service configuration, or infer connection/directory/authorization values. Reporting enforces PostgreSQL read-only transactions and numeric query bounds. Backup and restore require their exact disposable confirmations.

Deterministic exit codes:

- 0: success;
- 2: missing/malformed configuration, missing authorization, unsafe source/target, invalid reporting bounds, or other precondition/safety rejection;
- 3: missing, unparsable, or non-15 PostgreSQL client/server version;
- 4: database or subprocess operational failure after validated preconditions;
- 5: artifact, checksum, manifest, ownership, containment, or post-operation validation failure.

Unexpected Python exceptions map to 5 unless a more specific class above applies. Every failure emits sanitized stderr only: no raw subprocess stderr that may contain connection data, raw exception representation, traceback, DSN/password, environment contents, or connection command line.

## 17. Gate 10 / VPS boundary

Gate 9 validation is local and disposable only. It requires no VPS, domain, HTTPS, firewall, production Docker host, real WUZAPI/phone/Gemini traffic, client DB, or production backup destination. Gate 9 produces scripts, reports, and runbooks that Gate 10 may later execute after separate environment authorization.

## 18. Final contract closure

**G9-D01 CLOSED:** Transcription `usage_logs` is the authoritative per-provider-attempt token/cost source; retries and failures count; unknown usage stays NULL and makes totals partial.

**G9-D02 CLOSED:** Gate 9 reports from component-owned durable sources and does not populate or project into Platform `service_usage`; NO GATE 9 MIGRATION REQUIRED.

**G9-D03 CLOSED:** business E2E ends only at an unequivocal durable terminal timestamp; `updated_at` is prohibited as fallback; missing authoritative terminal time yields `duration_available=false`.

The safety correction pass is incorporated without reopening G9-D01–D03 or expanding source/test/documentation scope. Gate 9 contract remains CLOSED. The final-review correction code and host, disposable-reporting, and physical backup/restore verification are complete. The repeated final application review passed, and the user explicitly approved Gate 9 on 2026-08-15. `G9-APPROVED = true`.

## 19. Implementation and verification closure

Implemented inventory is exactly the three standalone operational scripts, two Gate 9 runbooks, and four planned test files. No shared helper, application/business-service change, migration, model/index change, dependency/lockfile change, Docker/runtime configuration change, Gate 10 work, or Production Phase B implementation was added.

Evidence completed on 2026-08-15:

- Gate 9 focused suite: **62 passed, 0 skipped, 0 failed, 0 errors**. The host run covered 60 reporting/unit/integration tests and the isolated client-equipped disposable runner covered the two physical backup/restore tests.
- G9-X01/X02: actual reporting CLI plus disposable Platform and Transcription PostgreSQL 15 databases; exact per-document and per-organization attempt/token/cost/PARTIAL results; Platform `service_usage` remained unchanged.
- G9-X03: exact business E2E, closed human-wait, and final-notification durations; a tempting later `updated_at` was ignored when no authoritative terminal timestamp existed.
- G9-X04: actual local Writer rejection produced durable Platform/Writer failure evidence and a sanitized correlation reconstruction including final-notification reservation.
- G9-X05/X06: actual PostgreSQL 15 `pg_dump` custom-format artifact, SHA-256/manifest/catalog verification, clean generated restore target, schema/data/count/index/constraint/revision validation, and exact invocation-owned cleanup.
- Exact client/server detection matrix, pre-material rejection ordering, loopback/containment/ownership/retention/cleanup refusal, reporting guards/bounds/cursors, and sanitized deterministic exit behavior passed in the unit suite.
- Frozen regressions: Gate 4 **210 passed**; Gate 5 **63 passed**; Gate 6 **64 passed**; Gate 7 **126 passed**; Gate 8 **46 passed**; every suite had 0 skipped, 0 failed, and 0 errors.
- Complete safe host suite excluding the two separately executed physical backup/restore tests: **671 passed, 0 skipped, 0 failed, 0 errors**. Combined complete evidence: **673 passed, 0 skipped, 0 failed, 0 errors**.
- Physical database evidence used only uniquely named tmpfs-backed PostgreSQL 15 containers and invocation-owned temporary databases/artifacts. No persistent, staging, production, client, Supabase, remote, or VPS database was touched; no external WUZAPI, cellphone, Gemini, or provider traffic occurred; database, container, image, backup/restore artifact, and temporary Dockerfile cleanup completed.
- Static evidence: compileall, Ruff, Gate 9-targeted mypy, `git diff --check`, credential/DSN leakage inspection, repository-scope inspection, and separate authorized-untracked-file trailing-whitespace inspection passed. The final host pytest rerun initially left `gate9-pytest-tmp/`; a true elevated Windows Administrator session validated the exact target, transferred ownership to the Administrators group, granted removal access, removed the pytest-created symlinks and exact temporary tree, and independent post-cleanup verification confirmed `Test-Path=false` and no remaining Git-status entry.

Gate 9 status is **APPROVED / COMPLETE**. Repository cleanup, scope/whitespace closure, correction verification, final application review, and explicit user approval are complete. Gate 10 remains **NOT STARTED**. Production Phase B remains **NOT IMPLEMENTED**. Persistent/staging/production/remote migration execution remains **NOT AUTHORIZED**.

## 20. Final-review correction pass

The first final application review found four P1 blockers. The bounded correction pass changed only the existing `gate9_report.py`, `platform_restore.py`, three existing Gate 9 test files, and governance:

- document token totals now use one database aggregate instead of materializing every attempt; organization totals use a constant-memory incremental accumulator while detailed rows remain `limit + 1` bounded;
- correlation drilldown now returns a next cursor per detailed collection and accepts a cursor only with an explicit collection selector;
- restore target creation records whether `CREATE DATABASE` succeeded before the ownership comment, preserving the sidecar and emitting sanitized manual-cleanup evidence when marker establishment fails;
- restore artifact validation now requires marker and manifest schema version 1 plus the same valid 32-lowercase-hex tool-directory identity.

Fresh correction evidence:

- Gate 9 unit correction suite: **64 passed, 0 skipped, 0 failed, 0 errors**;
- affected disposable PostgreSQL 15 reporting integrations: **4 passed, 0 skipped, 0 failed, 0 errors**, including exact token totals with bounded detail and two-page correlation continuation;
- compileall PASS with bytecode redirected outside the repository; Ruff PASS; targeted mypy PASS for five touched source/test files;
- PostgreSQL containers/databases used by the reporting integrations were invocation-named, tmpfs-backed, and removed afterward;
- fresh physical G9-X05/X06 re-verification: **2 passed, 0 skipped, 0 failed, 0 errors** in a client-equipped PostgreSQL 15 runner sharing the disposable server's network namespace, so both resolver and connected-server identity were true loopback; the run exercised the corrected restore code with a real custom-format backup, catalog/hash validation, clean generated restore, row-identity comparison, exact target cleanup, and missing-authorization rejection;
- the physical database was uniquely named and tmpfs-backed; the runner used a read-only repository mount and an internal tmpfs pytest directory; the database container, runner container/image, backup artifacts, restore target, and temporary runner definition were removed after verification.

The repeated final review also checked two residual edge cases within the original blockers. Organization token reporting now scans organization-owned ProcessingItem IDs in fixed-size batches, applies per-batch SQL summaries, and keeps only `limit + 1` globally ordered detail candidates; it no longer retains an unbounded document-ID set. Correlation child collections query through durable correlation joins directly, while Writer rows scan ProcessingItem identities in fixed-size batches and retain only `limit + 1` candidates, so continuation is not limited to the first ProcessingItem page. Unit and disposable PostgreSQL tests cover both invariants. Final review result: **PASSED / APPROVED**.

No migration, dependency, model/index, Docker/runtime configuration, Gate 4–8 source/test, Gate 10, VPS, external-service, or Production Phase B change was made. `G9-APPROVED = true`.

## 21. Stop conditions

Stop and return for review if implementation would reinterpret a Gate 4–8 business state, mutate frozen tests/migrations, require a runtime service to hold another service's DB credential, coerce unknown tokens to zero, issue unbounded queries, expose PII/secrets/raw payloads, restore to a nonempty or non-disposable database, include Writer/client DB in Platform backup, add an external monitoring vendor, require VPS assumptions, or start Gate 10/production Phase B.
