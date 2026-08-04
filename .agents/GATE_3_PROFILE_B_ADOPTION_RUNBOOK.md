# Gate 3 — Preserved PostgreSQL Profile B Adoption Runbook

This runbook prepares the future adoption of the preserved Transcription PostgreSQL database from the approved Profile B physical schema to the approved Gate 3 schema.

This document is a plan. It does not authorize execution.

## 1. Scope and hard prohibitions

Approved source state:

- Dedicated Transcription Alembic environment exists under `apps/transcription/alembic/`.
- Dedicated version table is `alembic_version_transcription`.
- Baseline revision is `transcription_1_0_baseline`.
- Gate 3 revision/head is `gate3_schema`.
- Profile B verifier, external reconciliation source, post-Gate-3 verifier, and disposable PostgreSQL tests passed.

Do not:

- run `alembic upgrade` against the preserved database;
- run `alembic downgrade` against the preserved database;
- run `alembic stamp` before post-Gate-3 verification passes;
- execute Profile B reconciliation before all hold points are approved;
- create `alembic_version_transcription` before the stamp step;
- stop/restart/scale services without explicit operator approval;
- expose credentials, full URLs, business payloads, or secrets in logs;
- rely on implicit `DATABASE_URL` or implicit `TRANSCRIPTION_DATABASE_URL` fallbacks;
- pass passwords in command-line arguments;
- use `PGPASSWORD` in recorded commands or logs;
- start Gate 4 or activate Gate 3 application behavior as part of this adoption.

## 2. Worktree classification before execution

Before any adoption execution, run:

```powershell
git status --short
git diff --stat
git diff --name-only
git diff -- apps/transcription/src/transcription/database/models.py
git diff -- apps/transcription/src/transcription/api/extract.py
git diff -- apps/transcription/src/transcription/services/prompt_service.py
```

Classify current known changes:

| Path | Classification | Include in migration-source commit? |
|---|---|---:|
| `.agents/IMPLEMENTATION_PLAN_GATE_3.md` | tracker/documentation, approved planning source | Yes |
| `.agents/transcription_schema_mapping.md` | tracker/documentation, approved schema source | Yes |
| `.agents/CURRENT_STATE.md` | tracker/documentation | Yes |
| `.agents/TASKS_TESTS_GATES.md` | tracker/documentation | Yes |
| `.agents/IMPLEMENTATION_PLAN.md` | synchronized non-authoritative planning artifact | Yes, if repository workflow keeps it |
| `apps/transcription/alembic.ini` | approved migration-source scope | Yes |
| `apps/transcription/alembic/**` | approved migration-source scope | Yes |
| `apps/transcription/src/transcription/database/migrations/**` | approved verifier/reconciliation source | Yes |
| `tests/test_transcription_migration_sources.py` | approved migration-source tests | Yes |
| `tests/test_transcription_migration_postgres.py` | approved disposable PostgreSQL validation tests | Yes |
| `apps/transcription/src/transcription/database/models.py` | approved ORM scope | Yes, with Gate 3 schema/model change set |
| `apps/transcription/src/transcription/api/extract.py` | premature Gate 3 application code | No; exclude from migration-source commit unless separately approved |
| `apps/transcription/src/transcription/services/prompt_service.py` | premature/non-migration Gate 3 application code | No; exclude from migration-source commit unless separately approved |

Abort adoption execution if unclassified changes touch database, migration, verifier, runtime configuration, or service startup behavior.

## 3. Mandatory preserved-database identity check

Future execution must record a sanitized target identity and require explicit user/operator confirmation at HOLD 1.

Required fields:

- source environment variable name: `<explicit operator-provided variable, recommended GATE3_PRESERVED_DATABASE_URL>`;
- host: `<host>`;
- port: `<port>`;
- database name: `<database>`;
- current schema: `<schema>`;
- current user: `<database_user>`;
- PostgreSQL version: `<server_version>`;
- Docker container name, if applicable: `<container>`;
- Docker volume identity, if applicable: `<volume>`;
- sanitized URL: `postgresql://<user>@<host>:<port>/<database>`;

The execution wrapper must parse the non-logged connection string from the explicit operator-provided variable and assert that parsed host, port and database exactly match the HOLD 1 identity. It must abort if the variable is missing or if parsing fails. It must not silently read `DATABASE_URL`, `TRANSCRIPTION_DATABASE_URL`, `.env`, or Alembic defaults.

Suggested read-only identity SQL:

```sql
SELECT
  current_database() AS database_name,
  current_schema() AS current_schema,
  current_user AS current_user,
  inet_server_addr() AS server_addr,
  inet_server_port() AS server_port,
  version() AS postgresql_version;
```

Docker identity examples, if Docker is used:

```powershell
docker ps --format "{{.Names}} {{.Ports}}"
docker inspect <container> --format "{{.Name}} {{range .Mounts}}{{.Name}} {{.Destination}}{{end}}"
```

Abort if the identity is not exactly the preserved database intended for Profile B adoption, or if the operator does not explicitly confirm it.

All `psql`, `pg_dump`, `pg_restore`, verifier, reconciliation and stamp commands must use explicit host, port, database and user values or a non-logged connection value supplied only by the approved operator variable. Logs may show only sanitized values.

For manual SQL execution, use this command pattern:

```powershell
psql --host "<confirmed-host>" --port "<confirmed-port>" --username "<confirmed-user>" --dbname "<confirmed-database>" -v ON_ERROR_STOP=1 --file "<reviewed-sql-file>"
if ($LASTEXITCODE -ne 0) { throw "psql command failed" }
```

Authentication must come from a temporary `.pgpass` file, a `pg_service.conf` entry, or another approved non-logged secret mechanism. Do not put passwords in the command line. Do not record `PGPASSWORD` commands in evidence.

## 4. HOLD 1 — confirm database identity

Operator must answer:

```text
I confirm the target is the preserved Profile B Transcription database:
host=<host>, port=<port>, database=<database>, container=<container-or-n/a>, volume=<volume-or-n/a>.
I authorize read-only pre-adoption inventory only.
```

No write operation may occur before this hold is cleared.

## 5. Read-only pre-adoption inventory

Use aggregate-only output. Do not select or print business payloads such as `response_json`, prompt bodies, raw metadata, or file identifiers beyond safe aggregate hashes.

### 5.1 Profile B verifier

Run the read-only Profile B verifier using a small operator wrapper that imports:

```python
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine
from transcription.database.migrations.schema_verifier import verify_profile_b

url = os.environ["GATE3_PRESERVED_DATABASE_URL"]
parsed = urlparse(url)
assert parsed.hostname == "<confirmed-host>"
assert parsed.port == <confirmed-port>
assert parsed.path.lstrip("/") == "<confirmed-database>"
engine = create_engine(url)
with engine.connect() as conn:
    result = verify_profile_b(conn)
print(result)
raise SystemExit(0 if result.ok else 1)
```

Expected: `ok=True`, `mismatches=()`.

Abort if any mismatch is returned.

### 5.2 Aggregate inventory SQL

Run these as read-only statements only:

```sql
BEGIN READ ONLY;
SELECT 'applications' AS table_name, count(*) AS row_count FROM applications
UNION ALL SELECT 'requests', count(*) FROM requests
UNION ALL SELECT 'extractions', count(*) FROM extractions
UNION ALL SELECT 'usage_logs', count(*) FROM usage_logs;

SELECT status::text AS status, count(*) AS row_count
FROM requests
GROUP BY status::text
ORDER BY status::text;

SELECT count(*) AS extraction_count FROM extractions;
SELECT count(*) AS usage_log_count FROM usage_logs;

SELECT count(*) AS duplicate_request_attempt_pairs
FROM (
  SELECT request_id, attempt_number
  FROM usage_logs
  GROUP BY request_id, attempt_number
  HAVING count(*) > 1
) duplicates;

SELECT count(*) AS orphan_requests
FROM requests r
LEFT JOIN applications a ON a.id = r.application_id
WHERE r.application_id IS NOT NULL AND a.id IS NULL;

SELECT count(*) AS orphan_extractions
FROM extractions e
LEFT JOIN requests r ON r.id = e.request_id
WHERE r.id IS NULL;

SELECT count(*) AS orphan_usage_logs
FROM usage_logs u
LEFT JOIN requests r ON r.id = u.request_id
WHERE r.id IS NULL;

SELECT
  count(*) AS estimated_cost_count,
  min(estimated_cost) AS estimated_cost_min,
  max(estimated_cost) AS estimated_cost_max,
  sum(estimated_cost) AS estimated_cost_sum
FROM usage_logs;

SELECT count(*) AS prompt_non_null_count FROM extractions WHERE prompt IS NOT NULL;
SELECT count(*) AS application_id_non_null_count FROM requests WHERE application_id IS NOT NULL;

SELECT count(*) AS safe_request_identity_checksum
FROM requests
WHERE id IS NOT NULL;
ROLLBACK;
```

### 5.3 Schema inventory SQL

```sql
BEGIN READ ONLY;
SELECT e.enumlabel
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typname = 'requeststatus'
ORDER BY e.enumsortorder;

SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name IN ('applications', 'requests', 'extractions', 'usage_logs')
ORDER BY table_name, ordinal_position;

SELECT conrelid::regclass::text AS table_name, conname, contype, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid::regclass::text IN ('applications', 'requests', 'extractions', 'usage_logs')
ORDER BY table_name, conname;

SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname = current_schema()
  AND tablename IN ('applications', 'requests', 'extractions', 'usage_logs')
ORDER BY tablename, indexname;

SELECT to_regclass('alembic_version_transcription') AS transcription_version_table;
ROLLBACK;
```

Expected Profile B highlights:

- `requeststatus`: `PENDING`, `PROCESSING`, `COMPLETED`, `SUCCEEDED`, `FAILED`, `PERSISTENCE_FAILED`.
- `usage_logs.attempt_number`: `integer NOT NULL`, no server default.
- `usage_logs.estimated_cost`: Profile B floating type.
- `usage_logs_request_id_key`: present.
- `uq_usage_logs_request_attempt`: absent.
- `requests.application_id`: `NOT NULL`.
- `extractions.prompt`: `NOT NULL`.
- `alembic_version_transcription`: absent.

### 5.4 Active write transaction check

```sql
BEGIN READ ONLY;
SELECT pid, state, wait_event_type, wait_event, xact_start, query_start
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
  AND (
    query ILIKE '%applications%'
    OR query ILIKE '%requests%'
    OR query ILIKE '%extractions%'
    OR query ILIKE '%usage_logs%'
  );
ROLLBACK;
```

Expected: no active writer or long-running transaction touching Transcription tables.

## 6. HOLD 2 — confirm application quiescence

Before backup and DDL, the operator must:

- stop or scale to zero every Transcription writer;
- include the Transcription API process, legacy `/extract` route, internal `/internal/extract` route if present, Bot DF callers, scheduled/background workers, development processes, tests, startup hooks and manual scripts in the writer inventory;
- confirm no active extraction requests;
- confirm Bot DF cannot invoke Transcription;
- confirm no background worker writes `applications`, `requests`, `extractions`, or `usage_logs`;
- confirm application startup does not run Alembic, `Base.metadata.create_all()`, or reconciliation;
- confirm no process supervisor or compose/orchestration policy can automatically restart a writer during the maintenance window;
- keep writers stopped through post-state verification, Gate 3 stamp and post-stamp verification;
- record maintenance-window start time.

Operator confirmation:

```text
I confirm all Transcription writers are quiesced and no Bot DF/background process can write to the Transcription tables.
I authorize backup and restore rehearsal preparation only.
```

## 7. Backup procedure

Do not store backups inside the repository.

Recommended output directory:

```text
C:\tmp\gate3_transcription_backups
```

Timestamped filename:

```text
transcription_profile_b_pre_gate3_<YYYYMMDDTHHMMSSZ>.dump
```

Sanitized command template:

```powershell
$backupDir = "C:\tmp\gate3_transcription_backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$dump = Join-Path $backupDir "transcription_profile_b_pre_gate3_$stamp.dump"
if (Test-Path -LiteralPath $dump) { throw "Backup file already exists: $dump" }

# Authentication must use a temporary .pgpass/pg_service entry or inherited secure secret mechanism.
# Do not put passwords in the command line and do not log the full connection string.
pg_dump --host "<confirmed-host>" --port "<confirmed-port>" --username "<confirmed-user>" --dbname "<confirmed-database>" --format=custom --no-owner --no-acl --file "$dump"
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath "$dump"
$file = Get-Item -LiteralPath "$dump"
if ($file.Length -le 0) { throw "Backup file is empty" }
$hash
$file | Select-Object FullName, Length

pg_restore --list "$dump"
if ($LASTEXITCODE -ne 0) { throw "pg_restore --list failed" }
```

Abort if:

- `pg_dump` fails;
- file size is zero or unexpectedly small;
- SHA-256 checksum cannot be produced;
- `pg_restore --list` fails;
- archive contents do not include all Transcription tables.

## 8. HOLD 3 — approve backup and restore rehearsal

Operator confirmation:

```text
I confirm the backup path, checksum, file size, and archive listing are valid.
I authorize restore rehearsal into disposable PostgreSQL only.
```

## 9. Restore rehearsal procedure

Restore into a disposable PostgreSQL database only. Use a database identity that cannot be confused with the preserved database, such as:

- host port: not `5432`;
- database: `gate3_restore_rehearsal_<timestamp>`;
- container/volume: `gate3_restore_rehearsal_*`.

Sanitized example:

```powershell
docker run -d --name gate3_restore_rehearsal_<timestamp> `
  -e POSTGRES_USER=restore_user `
  -e POSTGRES_PASSWORD=<temporary-secret> `
  -e POSTGRES_DB=gate3_restore_rehearsal `
  -p <non-5432-port>:5432 `
  -v gate3_restore_rehearsal_<timestamp>_data:/var/lib/postgresql/data `
  postgres:15

pg_restore --host "<disposable-host>" --port "<non-5432-port>" --username "<restore-user>" --dbname "gate3_restore_rehearsal" --no-owner --no-acl "$dump"
if ($LASTEXITCODE -ne 0) { throw "pg_restore rehearsal failed" }
```

After restore:

1. run the Profile B verifier against the restored disposable database;
2. run aggregate inventory checks;
3. validate restored row counts, constraints, enum order and physical types against the preserved pre-adoption inventory;
4. confirm `alembic_version_transcription` is absent;
5. remove only the rehearsal container and volume after evidence is recorded. Cleanup commands must name only `gate3_restore_rehearsal_*` resources and must never use broad Docker prune commands.

Abort preserved adoption if restore rehearsal fails or the restored schema does not pass Profile B verification.

## 10. HOLD 4 — approve reconciliation SQL

The operator must review the exact SQL in section 11 and confirm:

```text
I approve the reviewed Profile B reconciliation SQL and lock/timeout policy.
I authorize final read-only Profile B preflight only.
```

## 11. Reconciliation SQL review

The external reconciliation source performs only these operations after Profile B verifier passes. It does not add enum labels, add `attempt_number`, recreate tables, delete rows, modify platform tables, run Alembic commands, or stamp automatically.

| Order | Table | Statement | Expected lock | Expected duration | Preconditions | Postconditions | Rollback behavior |
|---:|---|---|---|---|---|---|---|
| 1 | `requests` | `ALTER TABLE requests ALTER COLUMN application_id DROP NOT NULL` | `ACCESS EXCLUSIVE` on `requests` | short metadata change for current small dataset | Profile B verifier passed; writers quiesced | internal requests can use `NULL application_id` | transaction rollback restores NOT NULL |
| 2 | `extractions` | `ALTER TABLE extractions ALTER COLUMN prompt DROP NOT NULL` | `ACCESS EXCLUSIVE` on `extractions` | short metadata change | Profile B verifier passed | internal rows can store `NULL prompt` | transaction rollback restores NOT NULL |
| 3 | `usage_logs` | `ALTER TABLE usage_logs ALTER COLUMN estimated_cost DROP DEFAULT` | `ACCESS EXCLUSIVE` on `usage_logs` | short metadata change | default may exist or be absent per reviewed Profile B | no server default | transaction rollback restores previous state |
| 4 | `usage_logs` | `ALTER TABLE usage_logs ALTER COLUMN estimated_cost TYPE NUMERIC(18,8) USING estimated_cost::numeric(18,8)` | `ACCESS EXCLUSIVE` on `usage_logs`; table rewrite possible | depends on row count; expected short for current small dataset | Profile B floating cost type; backup verified | cost stored as `NUMERIC(18,8)`; values cast, not repriced | transaction rollback restores old type |
| 5 | `usage_logs` | `ALTER TABLE usage_logs ADD CONSTRAINT uq_usage_logs_request_attempt UNIQUE (request_id, attempt_number)` | lock on `usage_logs`; uniqueness validation scans table | depends on row count; expected short | duplicate `(request_id, attempt_number)` count is zero | composite uniqueness exists | transaction rollback drops new constraint |
| 6 | `usage_logs` | `ALTER TABLE usage_logs DROP CONSTRAINT usage_logs_request_id_key` | `ACCESS EXCLUSIVE` on `usage_logs` | short metadata change | composite uniqueness already exists in same transaction | request-only uniqueness removed | transaction rollback restores old constraint |

Safe constraint sequence:

1. Profile B verifier checks duplicate pairs before DDL.
2. Composite uniqueness is created.
3. Request-only uniqueness is removed.

## 12. Lock and timeout policy

Future execution should use explicit PostgreSQL safeguards before DDL:

```sql
SET lock_timeout = '5s';
SET statement_timeout = '60s';
```

Because `reconcile_profile_b()` owns the DDL transaction internally, the operator wrapper must set timeout safeguards at the session level before calling it, or use an equivalent non-secret `PGOPTIONS` value:

```powershell
$env:PGOPTIONS = "-c lock_timeout=5s -c statement_timeout=60s"
```

or:

```python
with engine.connect() as conn:
    conn.execute(text("SET lock_timeout = '5s'"))
    conn.execute(text("SET statement_timeout = '60s'"))
    conn.commit()
    result = reconcile_profile_b(conn)
```

Do not wrap `reconcile_profile_b()` in an external transaction. The reconciliation source is the single transactional owner for DDL: it runs the Profile B verifier, rolls back the verifier's read-only transaction, opens one DDL transaction with `conn.begin()`, executes the DDL list, and then runs the post-Gate-3 verifier.

Recommended initial values for the current small dataset:

- `lock_timeout`: `5s`;
- `statement_timeout`: `60s`;
- backup/restore timeout: operator-configurable, typically `10m`;
- verifier timeout: operator-configurable, typically `60s`.

Operators may tune these values after reviewing row counts. Do not use infinite waits.

DDL transaction policy:

- Profile B reconciliation should run as one transaction where PostgreSQL permits it.
- If any statement fails, the transaction must roll back.
- Do not stamp unless post-Gate-3 verification passes.

Operations with significant locks:

- all `ALTER TABLE` statements can acquire `ACCESS EXCLUSIVE`;
- cost type conversion may rewrite `usage_logs`;
- adding the unique constraint scans `usage_logs`.

## 13. Final adoption sequence

Freeze this exact sequence:

1. Operator confirms preserved-database identity.
2. Application writers are quiesced.
3. Read-only inventory is captured.
4. Profile B verifier passes.
5. Backup is created.
6. Checksum and archive validation pass.
7. Restore rehearsal passes in disposable PostgreSQL.
8. Profile B verifier is rerun immediately before DDL.
9. HOLD 4 reconciliation SQL and timeout policy are accepted.
10. HOLD 5 reconciliation execution is authorized.
11. External Profile B reconciliation is executed exactly once.
12. Transaction result is checked.
13. Post-Gate-3 verifier passes.
14. Aggregate data-integrity checks pass.
15. HOLD 6 Gate 3 stamp is authorized.
16. Run `alembic -c apps/transcription/alembic.ini stamp gate3_schema`.
17. Confirm `alembic_version_transcription = gate3_schema`.
18. Rerun post-state verifier.
19. HOLD 7 service resume is authorized.
20. Resume service only through a separate explicit operator action.

Do not substitute `alembic upgrade` for Profile B reconciliation.

## 14. HOLD 5 — authorize reconciliation execution

Immediately before DDL:

```text
I confirm Profile B verifier passed immediately before DDL, backup and restore rehearsal passed, writers remain quiesced, and I authorize executing Profile B reconciliation exactly once.
```

## 15. Post-reconciliation verification

Run the post-Gate-3 verifier:

```python
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine
from transcription.database.migrations.schema_verifier import verify_gate3

url = os.environ["GATE3_PRESERVED_DATABASE_URL"]
parsed = urlparse(url)
assert parsed.hostname == "<confirmed-host>"
assert parsed.port == <confirmed-port>
assert parsed.path.lstrip("/") == "<confirmed-database>"
engine = create_engine(url)
with engine.connect() as conn:
    result = verify_gate3(conn, require_version_table=False)
print(result)
raise SystemExit(0 if result.ok else 1)
```

Expected: `ok=True`, `mismatches=()`.

Run aggregate comparisons before stamp:

- row counts for all four tables unchanged;
- request-status distribution unchanged;
- PK counts unchanged;
- orphan FK counts remain zero;
- extraction/request and usage/request relationships unchanged;
- estimated-cost count/min/max/sum match within expected cast precision. Exact binary floating-point equality is not required after `FLOAT` to `NUMERIC(18,8)` conversion; compare rounded decimal values at scale 8 and record any representational difference;
- prompt non-null count unchanged;
- application_id non-null count unchanged;
- duplicate `(request_id, attempt_number)` pairs remain zero;
- `usage_logs.request_id` request-only uniqueness absent;
- `uq_usage_logs_request_attempt` present.

## 16. HOLD 6 — authorize Gate 3 stamp

Only after post-state verification passes:

```text
I confirm schema is physically Gate 3 equivalent and authorize Alembic bookkeeping stamp to gate3_schema only.
```

Stamp command template:

```powershell
$env:TRANSCRIPTION_DATABASE_URL = $env:GATE3_PRESERVED_DATABASE_URL
# Do not use DATABASE_URL fallback. Confirm parsed TRANSCRIPTION_DATABASE_URL still matches HOLD 1 identity before running.
alembic -c apps/transcription/alembic.ini stamp gate3_schema
if ($LASTEXITCODE -ne 0) { throw "Alembic stamp failed" }
```

Expected:

```sql
SELECT version_num FROM alembic_version_transcription;
-- gate3_schema
```

Rerun `verify_gate3(..., require_version_table=True)` after stamp.

## 17. HOLD 7 — authorize application resume

Application remains stopped after schema/stamp success until the operator explicitly confirms:

```text
I confirm post-stamp verification passed and authorize resuming application services through the approved operational procedure.
```

Service resume is a separate operator action, not an automatic runbook step.

## 18. Failure and recovery paths

### Preflight failure

- No DDL.
- Do not stamp.
- Do not restart automatically.
- Record verifier mismatches and inventory output.

### Backup or restore-rehearsal failure

- No DDL.
- Do not stamp.
- Keep services quiesced or resume only by explicit operator decision.
- Fix backup/rehearsal issue and restart from HOLD 3.

### Reconciliation transaction failure

- Verify rollback.
- Run Profile B verifier again.
- Do not stamp.
- Keep service quiescence until exact state is understood.
- Decide whether to retry only after diagnosing lock timeout, duplicate data, or schema mismatch.

### Reconciliation succeeds but post-state verification fails

- Do not stamp.
- Do not activate application.
- Inspect exact mismatch.
- Decide between reviewed corrective forward operation or restore.

### Stamp failure after verified Gate 3 equivalence

- Schema remains Gate 3.
- Do not rerun reconciliation blindly.
- Diagnose version-table/bookkeeping issue.
- Retry only the stamp/bookkeeping operation after explicit approval.

### Emergency restore

- Restore into a new database/container first.
- Validate restored state.
- Switch traffic only through an explicit recovery decision.
- Never overwrite the original database without separate approval.

## 19. Evidence checklist

Use `.agents/GATE_3_PROFILE_B_ADOPTION_EVIDENCE_TEMPLATE.md`.

Required evidence:

- Git status and commit/revision;
- target identity confirmation;
- quiescence confirmation;
- pre-adoption inventory;
- Profile B verifier output;
- backup path, size, SHA-256 checksum;
- `pg_restore --list` output;
- restore rehearsal result;
- final pre-DDL Profile B verifier output;
- reconciliation result;
- post-Gate-3 verifier output;
- before/after aggregate data comparisons;
- stamp command result;
- version-table value;
- post-stamp verifier output;
- service resume decision/result;
- deviations and sign-off.
