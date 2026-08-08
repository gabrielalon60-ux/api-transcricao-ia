# Gate 4 — Queue & Processing State Schema Mapping

## Overview

This document specifies the database schema mapping, state machine transitions, indexing strategy, physical database constraints, and transactional pseudocode for the Gate 4 persistent FIFO processing queue in Platform PostgreSQL (`packages/db`), as well as the Database Writer idempotency schema in the DF Holding database.

---

## 1. Schema Definitions & Table Ownership

Platform PostgreSQL (`packages/db`) is the **sole source of truth** for platform queue state, sequence counters, execution effect ledgers, and platform usage aggregation.

### 1.1 Platform PostgreSQL Table: `conversation_queue_counters`

Tracks monotonic sequence counter per conversation (`organization_id`, `instance_id`, `user_id`).

```sql
CREATE TABLE conversation_queue_counters (
    organization_id VARCHAR NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    instance_id VARCHAR NOT NULL REFERENCES instances(id) ON DELETE RESTRICT,
    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    last_sequence BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    PRIMARY KEY (organization_id, instance_id, user_id)
);
```

### 1.2 Platform PostgreSQL Table: `processing_items`

Tracks document processing lifecycle, extraction payloads, business fields, and leases.

```sql
CREATE TABLE processing_items (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id VARCHAR NOT NULL UNIQUE REFERENCES events(id) ON DELETE RESTRICT,
    correlation_id VARCHAR NOT NULL,
    organization_id VARCHAR NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    instance_id VARCHAR NOT NULL REFERENCES instances(id) ON DELETE RESTRICT,
    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sequence BIGINT NULL, -- NULL for QUEUE_CAPACITY_EXCEEDED items
    status VARCHAR NOT NULL DEFAULT 'RECEIVED',

    -- Lease & Worker Execution Metadata
    claimed_by VARCHAR NULL,
    lease_expires_at TIMESTAMP WITH TIME ZONE NULL,
    heartbeat_at TIMESTAMP WITH TIME ZONE NULL,
    attempt_count INT NOT NULL DEFAULT 0,

    -- Ingestion Metadata
    message_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    file_mime_type VARCHAR NOT NULL,
    file_size BIGINT NOT NULL,
    file_sha256 VARCHAR(64) NOT NULL,
    original_filename VARCHAR NULL,

    -- Normalized Extraction Payload
    document_type VARCHAR NULL,
    raw_extraction JSONB NULL,
    normalized_data JSONB NULL,
    quality_flags JSONB NULL,
    confidence_data JSONB NULL,

    -- Business Processing Fields
    amount NUMERIC(18, 2) NULL,
    document_date VARCHAR NULL,
    transaction_date TIMESTAMP WITH TIME ZONE NULL,
    date_source VARCHAR NULL, -- 'DOCUMENT' | 'MESSAGE_TIMESTAMP'
    direction VARCHAR NULL,   -- 'expense' | 'income'

    -- Interactive State Fields
    question_type VARCHAR NULL, -- 'DIRECTION' | 'AMOUNT'
    waiting_since TIMESTAMP WITH TIME ZONE NULL,
    expires_at TIMESTAMP WITH TIME ZONE NULL,

    -- External Writer Integration
    writer_idempotency_key VARCHAR NULL UNIQUE,
    external_operation_status VARCHAR NULL,

    -- Error & Audit Fields
    error_code VARCHAR NULL,
    error_message_sanitized VARCHAR NULL,

    -- Lifecycle Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    extracted_at TIMESTAMP WITH TIME ZONE NULL,
    activated_at TIMESTAMP WITH TIME ZONE NULL,
    completed_at TIMESTAMP WITH TIME ZONE NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_processing_items_conversation_sequence
        UNIQUE (organization_id, instance_id, user_id, sequence)
);
```

### 1.3 Platform PostgreSQL Table: `executions` (Durable Idempotent Effect Ledger)

Durable effect and checkpoint ledger for recovery and auditing.

```sql
CREATE TABLE executions (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    event_id VARCHAR NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    processing_item_id VARCHAR NULL REFERENCES processing_items(id) ON DELETE RESTRICT,
    correlation_id VARCHAR NOT NULL,
    component VARCHAR NOT NULL, -- 'ORCHESTRATOR' | 'BOT_DF' | 'TRANSCRIPTION' | 'DB_WRITER'
    operation VARCHAR NOT NULL, -- 'INGEST' | 'EXTRACTION_DISPATCH' | 'USER_PROMPT' | 'DB_WRITE'
    outbound_message_id VARCHAR NULL UNIQUE, -- Deterministic ID for user message deduplication
    status VARCHAR NOT NULL,    -- 'PENDING' | 'RUNNING' | 'SUCCESS' | 'RETRYING' | 'FAILED'
    effect_status VARCHAR NULL, -- 'DISPATCHED' | 'ACKNOWLEDGED' | 'OUTBOUND_OUTCOME_UNKNOWN' | 'FAILED'
    external_reference VARCHAR NULL, -- External ID (e.g. WUZAPI msg ID, Writer record ID)
    attempt INT NOT NULL DEFAULT 1,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE NULL,
    duration_ms INT NULL,
    error_code VARCHAR NULL,
    error_message_sanitized VARCHAR NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_executions_attempt_positive CHECK (attempt >= 1),
    CONSTRAINT ck_executions_status_valid CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'RETRYING', 'FAILED')),
    CONSTRAINT ck_executions_component_valid CHECK (component IN ('ORCHESTRATOR', 'BOT_DF', 'TRANSCRIPTION', 'DB_WRITER')),
    CONSTRAINT ck_executions_effect_status_valid CHECK (effect_status IS NULL OR effect_status IN ('DISPATCHED', 'ACKNOWLEDGED', 'OUTBOUND_OUTCOME_UNKNOWN', 'FAILED'))
);
```

### 1.4 Platform PostgreSQL Table: `service_usage` (Token & AI Cost Tracking)

Tracks token usage and estimated AI provider cost per request attempt.

```sql
CREATE TABLE service_usage (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    event_id VARCHAR NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    processing_item_id VARCHAR NOT NULL REFERENCES processing_items(id) ON DELETE RESTRICT,
    execution_id VARCHAR NULL REFERENCES executions(id) ON DELETE RESTRICT,
    source_service VARCHAR NOT NULL DEFAULT 'TRANSCRIPTION',
    source_request_id VARCHAR NOT NULL,
    source_attempt_number INT NOT NULL DEFAULT 1,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    estimated_cost NUMERIC(18, 8) NULL,
    duration_ms INT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_service_usage_source_attempt UNIQUE (source_service, source_request_id, source_attempt_number),
    CONSTRAINT ck_service_usage_tokens_non_negative CHECK (input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0),
    CONSTRAINT ck_service_usage_source_attempt_positive CHECK (source_attempt_number >= 1)
);
```

### 1.4 DF Holding Database Table: `db_writer_idempotency` (Database Writer Storage)

Physically resides in the **DF Holding database** alongside business DML tables (`despesas`, `entradas`). Managed and bootstrapped solely by Database Writer service.

```sql
CREATE TABLE db_writer_idempotency (
    idempotency_key VARCHAR PRIMARY KEY, -- processing_item.id
    payload_hash VARCHAR(64) NOT NULL,
    status VARCHAR NOT NULL,             -- 'CONFIRMED_SUCCESS' | 'PERMANENT_ERROR'
    df_record_id VARCHAR NULL,
    error_code VARCHAR NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
```

---

## 2. Physical Database Constraints & Indexes

### 2.1 Physical One-Active Partial Unique Index (Platform DB)

```sql
CREATE UNIQUE INDEX uq_processing_items_one_active_per_conversation
ON processing_items (organization_id, instance_id, user_id)
WHERE status IN ('ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN');
```

---

## 3. Database Writer Single Local Transaction & Advisory Lock Pseudocode

Database Writer serializes concurrent requests for absent keys using a transaction-scoped advisory lock (`pg_advisory_xact_lock`):

```sql
BEGIN;

-- 1. Acquire transaction-scoped advisory lock derived from idempotency key
--    (hashtextextended produces a 64-bit bigint lock key; harmless hash collisions serialize without error)
SELECT pg_advisory_xact_lock(hashtextextended(:idempotency_key, 0));

-- 2. Query Idempotency Table
SELECT idempotency_key, payload_hash, status, df_record_id, error_code
FROM db_writer_idempotency
WHERE idempotency_key = :idempotency_key;

-- 3. Case A: Key Exists
--    a. Validate Payload Hash:
--       IF existing.payload_hash != :current_payload_hash THEN
--          ROLLBACK;
--          RETURN HTTP 409 Conflict ("IDEMPOTENCY_KEY_PAYLOAD_MISMATCH");
--       END IF;
--    b. Duplicate Success Check:
--       IF existing.status = 'CONFIRMED_SUCCESS' THEN
--          COMMIT;
--          RETURN HTTP 200 OK { status: "CONFIRMED_SUCCESS", df_record_id: existing.df_record_id };
--       END IF;
--    c. Duplicate Permanent Error Check:
--       IF existing.status = 'PERMANENT_ERROR' THEN
--          COMMIT;
--          RETURN HTTP 400 Bad Request { status: "PERMANENT_ERROR", error_code: existing.error_code };
--       END IF;

-- 4. Case B: Key Does NOT Exist
--    a. Perform deterministic business contract validation in Python BEFORE any DML.
--    b. IF business contract validation fails permanently:
--          INSERT INTO db_writer_idempotency (idempotency_key, payload_hash, status, error_code)
--          VALUES (:idempotency_key, :current_payload_hash, 'PERMANENT_ERROR', :validation_error_code);
--          COMMIT;
--          RETURN HTTP 400 Bad Request { status: "PERMANENT_ERROR", error_code: :validation_error_code };
--    c. ELSE (validation succeeds):
--          INSERT INTO despesas (amount, date, direction, ...)
--          VALUES (:amount, :date, :direction, ...)
--          RETURNING id INTO :df_record_id;

--          INSERT INTO db_writer_idempotency (idempotency_key, payload_hash, status, df_record_id)
--          VALUES (:idempotency_key, :current_payload_hash, 'CONFIRMED_SUCCESS', :df_record_id);

--          COMMIT;
--          RETURN HTTP 200 OK { status: "CONFIRMED_SUCCESS", df_record_id: :df_record_id };
```

---

## 4. SQL Exception Handling & Advisory Lock Properties

1. **Missing-Row Serialization:** `SELECT pg_advisory_xact_lock(...)` serializes concurrent first requests before querying `db_writer_idempotency`, guaranteeing that Caller 2 waits for Caller 1 to commit before attempting DML.
2. **Aborted Transaction Rule:** Unexpected database infrastructure errors (e.g. connection drop, DB timeout) abort the PostgreSQL transaction. Aborted transactions immediately execute `ROLLBACK` and return retryable failures without writing `PERMANENT_ERROR`. Permanent contract errors are checked in code **prior to** DML.
3. **Collision Safety:** Advisory key derivation uses `hashtextextended(key, 0)`. Hash collisions cause rare, temporary serialization between different keys but never cause correctness failures.
