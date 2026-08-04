from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from transcription.database.migrations.schema_verifier import (
    USAGE_ATTEMPT_UQ,
    SchemaCheckResult,
    verify_gate3,
    verify_profile_b,
)


def reconcile_profile_b(bind: Engine | Connection) -> SchemaCheckResult:
    """Explicit Profile B reconciliation.

    This never runs on import/startup/Alembic initialization. Callers must provide
    a reviewed disposable or authorized adoption connection. The operation aborts
    before DDL unless the read-only Profile B preflight is exact.
    """
    conn = bind.connect() if isinstance(bind, Engine) else bind
    should_close = isinstance(bind, Engine)
    try:
        preflight = verify_profile_b(conn)
        if not preflight.ok:
            return preflight
        conn.rollback()
        with conn.begin():
            op_sql = [
                "ALTER TABLE requests ALTER COLUMN application_id DROP NOT NULL",
                "ALTER TABLE extractions ALTER COLUMN prompt DROP NOT NULL",
                "ALTER TABLE usage_logs ALTER COLUMN estimated_cost DROP DEFAULT",
                "ALTER TABLE usage_logs ALTER COLUMN estimated_cost TYPE NUMERIC(18,8) USING estimated_cost::numeric(18,8)",
                f"ALTER TABLE usage_logs ADD CONSTRAINT {USAGE_ATTEMPT_UQ} UNIQUE (request_id, attempt_number)",
                "ALTER TABLE usage_logs DROP CONSTRAINT usage_logs_request_id_key",
            ]
            for statement in op_sql:
                conn.execute(sa.text(statement))
        return verify_gate3(conn)
    finally:
        if should_close:
            conn.close()
