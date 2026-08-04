# Transcription Schema Mapping – Gate 3 (Partial)

Official source of truth:
.agents/transcription_schema_mapping.md

The Antigravity transcription_schema_mapping.md artifact is a synchronized,
non-authoritative planning copy.

**Status:** `TBD-TRANSCRIPTION-SCHEMA-MAPPING — PARTIALLY RESOLVED`

## 1. Current Physical Schema (Version 1.0)

| Table | Columns (type) |
|-------|----------------|
| **applications** | `id UUID PK`, `name String(255) NOT NULL`, `api_key_hash String(255) UNIQUE NOT NULL`, `active Boolean DEFAULT TRUE NOT NULL`, `created_at DateTime(tz) DEFAULT utcnow NOT NULL` |
| **requests** | `id UUID PK`, `application_id UUID FK NOT NULL`, `created_at DateTime(tz) DEFAULT utcnow NOT NULL`, `completed_at DateTime(tz) NULL`, `status Enum('PENDING','PROCESSING','COMPLETED','FAILED') NOT NULL`, `processing_time_ms Integer NULL` |
| **extractions** | `id UUID PK`, `request_id UUID FK UNIQUE NOT NULL`, `prompt Text NULL`, `response_json JSONB NOT NULL`, `image_reference String(512) NULL`, `created_at DateTime(tz) DEFAULT utcnow NOT NULL` |
| **usage_logs** | `id UUID PK`, `request_id UUID FK NOT NULL`, `model_name String(100) NOT NULL`, `input_tokens Integer DEFAULT 0 NOT NULL`, `output_tokens Integer DEFAULT 0 NOT NULL`, `estimated_cost Float DEFAULT 0.0 NOT NULL`, `created_at DateTime(tz) DEFAULT utcnow NOT NULL` |

---

## 2. Request Identity Decision

- `/internal/extract` supplies `metadata.request_id` as `requests.id`.
- `/extract` omits `request_id` and retains automatic UUID generation.
- No schema migration is required solely for explicit PK assignment.
- Evidence: `apps/transcription/src/transcription/database/models.py` defines `Request.id` as primary key `UUID` default `uuid.uuid4()` and `Extraction.request_id` as foreign key to it.

---

## 3. Transitional Request‑Status Enum Compatibility

Physical PostgreSQL enum (pre‑migration) will contain:
```
PENDING
PROCESSING
COMPLETED
SUCCEEDED
FAILED
PERSISTENCE_FAILED
```
- Legacy `/extract` continues using `PENDING` → `COMPLETED`.
- Internal `/internal/extract` uses only `PROCESSING`, `SUCCEEDED`, `FAILED`, `PERSISTENCE_FAILED`.
- New internal requests write `SUCCEEDED` directly; no mapping through `COMPLETED`.
- Migration approach (conceptual): `ALTER TYPE request_status ADD VALUE 'SUCCEEDED'; ALTER TYPE request_status ADD VALUE 'PERSISTENCE_FAILED';` – both operations are non‑blocking on PostgreSQL 13+ but require coordination.
- Python layer will define matching `Enum` with all six members to stay compatible.

---

## 4. `usage_logs.request_id` Uniqueness Evidence

Current ORM definition (models.py line 106‑108) sets `unique=True` on `request_id`, generating a PostgreSQL unique constraint (auto‑named, e.g. `uq_usage_logs_request_id`).
- Constraint name (auto‑generated) can be inspected via `psql \d usage_logs`.
- Migration plan: keep this constraint for legacy rows, then add `attempt_number` column (nullable), backfill `1`, drop the old unique constraint, and create `UNIQUE(request_id, attempt_number)`.

---

## 5. `extractions.prompt` Nullability

Legacy rows keep existing prompt text. For internal requests we will store `NULL`.
- Change: alter column to `NULLABLE` (no data loss, existing NOT NULL rows remain valid).
- Optional audit columns (`prompt_version` or `prompt_hash`) are **not** added at this stage.

---

## 6. Success‑Response Persistence Design

All successful extractions are persisted in `extractions.response_json` using a versioned JSON structure:
```json
{
  "schema_version": "gate3-v1",
  "document_type": "PIX",
  "extraction": {},
  "normalization": {},
  "confidence": null,
  "quality_flags": []
}
```
- No additional relational columns are added.
- Legacy rows (pre‑Gate 3) lack `schema_version`; they remain readable.

### Document-type label contract

Explicit product decision: Gate 3 supports six business document categories represented by four technical runtime labels. This is an approved contract decision, not an inferred implementation shortcut.

| Business category | Runtime `document_type` label |
| --- | --- |
| Nota fiscal | `invoice` |
| Cupom fiscal | `invoice` |
| Comprovante PIX | `pix_receipt` |
| Boleto | `bank_receipt` |
| Pedido | `commercial_document` |
| Orçamento | `commercial_document` |

`unknown` is fallback-only and is not a supported business document category. The database stores the runtime label in the JSON response body; there is no enum or database constraint requiring six type labels.

---

## 6.1 System Prompt Runtime Contract

Explicit product and architecture decision:

- `MAX_SYSTEM_PROMPT_SIZE_BYTES = 262144` bytes (256 KiB).
- The limit applies to raw file bytes before UTF-8 decoding.
- Boundary is inclusive: exactly `262144` bytes is accepted; `262145` bytes is rejected.
- Strict UTF-8 decoding is required.
- Empty or whitespace-only prompts are invalid.
- Prompt validation occurs during application startup and uses the same shared runtime loading implementation.
- The validated prompt is cached once per application process; prompt file changes, `SYSTEM_PROMPT_PATH` changes, or size-setting changes require process restart.
- Runtime defensive failure maps to `SYSTEM_PROMPT_INVALID`, HTTP 503, `retryable=false`.
- For `/internal/extract`, Transaction A remains committed before runtime prompt loading/provider execution. If a defensive runtime prompt failure occurs after Transaction A, the terminal persisted status is `FAILED` with `error_code=SYSTEM_PROMPT_INVALID`.
- Prompt validation does not create or modify database schema and does not require a database connection.

---

## 7. Nullable‑First Migration Phases for `usage_logs`

**Phase A – Compatible Expansion** (all new columns nullable):
- `attempt_number Integer NULLABLE`
- `provider String NULLABLE`
- `status String NULLABLE`
- `started_at DateTime(tz) NULLABLE`
- `completed_at DateTime(tz) NULLABLE`
- `total_tokens Integer NULLABLE`
- `cached_tokens Integer NULLABLE`
- `usage_status String NULLABLE`
- `currency String NULLABLE`
- `pricing_version String NULLABLE`
- `sanitized_error_code String NULLABLE`
- Change `estimated_cost Float NOT NULL DEFAULT 0.0` → `Numeric(18,8) NULLABLE` (cast existing values).
- Token fields become nullable (zero is no longer a sentinel).

**Phase B – Legacy Backfill**
- `attempt_number = 1` for existing rows.
- `started_at = created_at` only if safe; otherwise leave NULL.
- `usage_status` left NULL unless trustworthy.
- Preserve existing `estimated_cost` via explicit cast.

**Phase C – Final Constraints**
- `attempt_number NOT NULL`
- `UNIQUE(request_id, attempt_number)`
- Other columns may stay nullable for historical compatibility.

---

## 8. Per‑Attempt `usage_logs` Target Model

```
id UUID PK
request_id UUID FK NOT NULL
attempt_number Integer NOT NULL
provider String NULLABLE (required for new rows)
model_name String NOT NULL
status String NULLABLE (required for new rows)
started_at DateTime(tz) NULLABLE (required for new rows)
completed_at DateTime(tz) NULLABLE
input_tokens Integer NULLABLE
output_tokens Integer NULLABLE
total_tokens Integer NULLABLE
cached_tokens Integer NULLABLE
usage_status Enum('AVAILABLE','PARTIAL','UNAVAILABLE') NULLABLE
estimated_cost Numeric(18,8) NULLABLE
currency String NULLABLE
pricing_version String NULLABLE
sanitized_error_code String NULLABLE
created_at DateTime(tz) NOT NULL

UNIQUE(request_id, attempt_number)
```
- One row per provider attempt.
- No raw error text, only sanitized code.

---

## 9. `application_id` Compatibility Decision

- `requests.application_id` becomes **nullable** for internal requests.
- Legacy `/extract` still supplies a non‑NULL value.
- **Removed constraint:** `CHECK (application_id IS NOT NULL OR status = 'PROCESSING')` (it conflicted with `SUCCEEDED`/`FAILED` states).
- Service‑layer invariants remain:
  - Legacy request: `application_id NOT NULL`.
  - Internal WhatsApp request: `application_id NULL`; `source='WHATSAPP'`,
    `correlation_id`, `received_at`, and `bot_instance_id` required by the
    current approved contract. `bot_instance_id` is persisted as
    `requests.instance_id`.

---

## 10. Full Internal Request Metadata Mapping (Nullable Physical Columns)

Approved current internal metadata contract clarification:

- `/internal/extract` currently requires `request_id`, `bot_instance_id`,
  `correlation_id`, `received_at`, and `source = WHATSAPP`.
- `bot_instance_id` is persisted in the nullable physical `requests.instance_id`
  column.
- Older references to required `event_id`, `organization_id`, `instance_id`, and
  `user_id` are obsolete for the current application contract. Those columns
  remain nullable compatibility columns for legacy/migration safety and are not
  required by the Gate 3 internal route.

| Column | Type | Nullable (legacy) | Required for internal |
|--------|------|-------------------|-----------------------|
| correlation_id | String(128) | NULL | ✅ |
| event_id | UUID | NULL | ✅ |
| organization_id | UUID | NULL | ✅ |
| instance_id | UUID | NULL | ✅ |
| user_id | UUID | NULL | ✅ |
| received_at | DateTime(tz) | NULL | ✅ |
| source | String (or Enum) | NULL | ✅ |
| processing_started_at | DateTime(tz) | NULL | ✅ (set when status → PROCESSING) |
| last_persistence_error_at | DateTime(tz) | NULL | ✅ (set on PERSISTENCE_FAILED) |
| error_code | String | NULL | ✅ (business/provider identifier) |
| detected_mime | String | NULL | ✅ (magic‑byte inspection) |
| declared_mime | String | NULL | ✅ (client header) |
| file_size_bytes | BigInteger | NULL | ✅ |
| file_sha256 | String(64) | NULL | ✅ (audit‑only) |

All columns are nullable physically; the API layer validates presence for internal requests.

---

## 11. Timestamp Semantics

- `created_at`: row creation.
- `processing_started_at`: Transaction A acquisition time (durable).
- `completed_at`: Terminal `SUCCEEDED`/`FAILED` persistence time.
- `last_persistence_error_at`: Time of compensation for `PERSISTENCE_FAILED`.
- `usage_logs.started_at` / `completed_at` follow provider‑attempt lifecycle.

---

## 12. Migration‑Risk Corrections

| Risk | Impact | Mitigation |
|------|--------|------------|
| PostgreSQL enum alteration (add `SUCCEEDED`, `PERSISTENCE_FAILED`) | May acquire a lock; requires coordination. | Perform on PG 13+ where `ADD VALUE` is non‑blocking; schedule during low traffic; ensure both old and new app versions understand the extended enum. |
| Changing `usage_logs.request_id` uniqueness | Dropping a constraint could fail if duplicate rows exist. | Backfill `attempt_number` first, verify uniqueness, then drop old constraint. |
| Float → Numeric for `estimated_cost` | Precision loss if not cast correctly. | Use `ALTER TABLE … ALTER COLUMN estimated_cost TYPE Numeric(18,8) USING estimated_cost::numeric(18,8)`. Verify sample rows. |
| Prompt nullability | Legacy code may assume prompt always present. | Add regression tests for `/extract` path; internal path already tolerant of NULL. |
| Application ID nullability | Queries may assume non‑null FK. | Add index/partial index where needed; test legacy route extensively. |
| Concurrent deployment | Old service must tolerate new enum values; new service must handle historic rows. | Deploy schema first (nullable), then roll new service version; monitor for unexpected enum values. |

---

## 13. Tracker & Status Updates

- **Physical schema inspection completed.**
- **Corrected schema mapping documented** (this file).
- **Awaiting user approval** before any model changes or migrations.
- Model changes and migration tasks remain unchecked.

---

*No code, model, or migration generation has been performed.*
