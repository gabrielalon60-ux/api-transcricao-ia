from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

TRANSCRIPTION_TABLES = {"applications", "requests", "extractions", "usage_logs"}
VERSION_TABLE = "alembic_version_transcription"
REQUEST_ENUM = "requeststatus"
PROFILE_A_STATUS = ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
GATE3_STATUS = ["PENDING", "PROCESSING", "COMPLETED", "SUCCEEDED", "FAILED", "PERSISTENCE_FAILED"]
USAGE_ATTEMPT_UQ = "uq_usage_logs_request_attempt"


@dataclass(frozen=True)
class SchemaCheckResult:
    profile: str
    ok: bool
    mismatches: tuple[str, ...] = field(default_factory=tuple)


def _connect(bind: Engine | Connection) -> tuple[Connection, bool]:
    if isinstance(bind, Engine):
        return bind.connect(), True
    return bind, False


def _run_readonly(bind: Engine | Connection, profile: str, checks: Any) -> SchemaCheckResult:
    conn, should_close = _connect(bind)
    had_transaction = conn.in_transaction()
    try:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        mismatches = list(checks(conn))
        return SchemaCheckResult(profile=profile, ok=not mismatches, mismatches=tuple(mismatches))
    finally:
        if not had_transaction:
            conn.rollback()
        if should_close:
            conn.close()


def _columns(conn: Connection, table: str) -> dict[str, dict[str, Any]]:
    return {str(col["name"]): dict(col) for col in sa.inspect(conn).get_columns(table)}


def _unique_sets(conn: Connection, table: str) -> dict[str, tuple[str, ...]]:
    constraints: dict[str, tuple[str, ...]] = {}
    for item in sa.inspect(conn).get_unique_constraints(table):
        name = str(item["name"] or "<unnamed>")
        constraints[name] = tuple(str(column) for column in item["column_names"])
    return constraints


def _indexes(conn: Connection, table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    return {
        str(item["name"]): (
            tuple(str(column) for column in item["column_names"]),
            bool(item.get("unique")),
        )
        for item in sa.inspect(conn).get_indexes(table)
    }


def _enum_labels(conn: Connection, name: str) -> list[str]:
    return list(
        conn.execute(
            sa.text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = :name
                ORDER BY e.enumsortorder
                """
            ),
            {"name": name},
        ).scalars()
    )


def _has_duplicate_attempts(conn: Connection) -> bool:
    return bool(
        conn.execute(
            sa.text(
                """
                SELECT 1
                FROM usage_logs
                GROUP BY request_id, attempt_number
                HAVING count(*) > 1
                LIMIT 1
                """
            )
        ).first()
    )


def _expect_tables(conn: Connection, errors: list[str]) -> None:
    tables = set(sa.inspect(conn).get_table_names())
    missing = TRANSCRIPTION_TABLES - tables
    if missing:
        errors.append(f"missing transcription tables: {sorted(missing)}")


def _expect_exact_columns(
    conn: Connection,
    table: str,
    expected: Iterable[str],
    errors: list[str],
) -> None:
    actual = set(_columns(conn, table))
    wanted = set(expected)
    if actual != wanted:
        errors.append(f"{table} columns mismatch: missing={sorted(wanted - actual)} unexpected={sorted(actual - wanted)}")


def _common_profile_a_checks(conn: Connection) -> list[str]:
    errors: list[str] = []
    _expect_tables(conn, errors)
    if errors:
        return errors
    if _enum_labels(conn, REQUEST_ENUM) != PROFILE_A_STATUS:
        errors.append("requeststatus enum is not exact Version 1.0 order")
    _expect_exact_columns(conn, "applications", ["id", "name", "api_key", "active", "created_at"], errors)
    _expect_exact_columns(conn, "requests", ["id", "application_id", "created_at", "completed_at", "status", "processing_time_ms"], errors)
    _expect_exact_columns(conn, "extractions", ["id", "request_id", "prompt", "response_json", "image_reference", "created_at"], errors)
    _expect_exact_columns(conn, "usage_logs", ["id", "request_id", "model_name", "input_tokens", "output_tokens", "estimated_cost", "created_at"], errors)
    req = _columns(conn, "requests")
    ext = _columns(conn, "extractions")
    usage = _columns(conn, "usage_logs")
    if req["application_id"]["nullable"]:
        errors.append("Profile A requests.application_id must be NOT NULL")
    if ext["prompt"]["nullable"]:
        errors.append("Profile A extractions.prompt must be NOT NULL")
    if "attempt_number" in usage:
        errors.append("Profile A usage_logs.attempt_number must be absent")
    if tuple(["request_id"]) not in _unique_sets(conn, "usage_logs").values():
        errors.append("Profile A usage_logs.request_id unique constraint missing")
    return errors


def verify_profile_a(bind: Engine | Connection) -> SchemaCheckResult:
    """Verify exact canonical unmanaged Version 1.0 Transcription schema. No writes."""
    return _run_readonly(bind, "profile_a", _common_profile_a_checks)


def _profile_b_checks(conn: Connection) -> list[str]:
    errors = _common_gate3_shape_checks(conn, profile_b=True)
    if errors:
        return errors
    cols = _columns(conn, "usage_logs")
    req = _columns(conn, "requests")
    ext = _columns(conn, "extractions")
    uniques = _unique_sets(conn, "usage_logs")
    if req["application_id"]["nullable"]:
        errors.append("Profile B requests.application_id must still be NOT NULL")
    if ext["prompt"]["nullable"]:
        errors.append("Profile B extractions.prompt must still be NOT NULL")
    if cols["attempt_number"]["nullable"]:
        errors.append("Profile B usage_logs.attempt_number must be NOT NULL")
    if cols["attempt_number"].get("default") is not None:
        errors.append("Profile B usage_logs.attempt_number must have no server default")
    if tuple(["request_id"]) not in uniques.values():
        errors.append("Profile B request-only usage uniqueness missing")
    if tuple(["request_id", "attempt_number"]) in uniques.values():
        errors.append("Profile B composite usage uniqueness must not exist yet")
    if _has_duplicate_attempts(conn):
        errors.append("Profile B duplicate request/attempt pairs exist")
    return errors


def verify_profile_b(bind: Engine | Connection) -> SchemaCheckResult:
    """Verify exact approved partial-drift Profile B. No writes."""
    return _run_readonly(bind, "profile_b", _profile_b_checks)


def _common_gate3_shape_checks(conn: Connection, *, profile_b: bool = False) -> list[str]:
    errors: list[str] = []
    _expect_tables(conn, errors)
    if errors:
        return errors
    if _enum_labels(conn, REQUEST_ENUM) != GATE3_STATUS:
        errors.append("requeststatus enum is not exact Gate 3 order")
    expected_requests = [
        "id", "application_id", "created_at", "completed_at", "status", "processing_time_ms",
        "correlation_id", "event_id", "organization_id", "instance_id", "user_id", "received_at",
        "source", "processing_started_at", "last_persistence_error_at", "error_code",
        "detected_mime", "declared_mime", "file_size_bytes", "file_sha256",
    ]
    expected_usage = [
        "id", "request_id", "attempt_number", "provider", "model_name", "status", "started_at",
        "completed_at", "input_tokens", "output_tokens", "total_tokens", "cached_tokens",
        "usage_status", "estimated_cost", "currency", "pricing_version",
        "sanitized_error_code", "created_at",
    ]
    _expect_exact_columns(conn, "requests", expected_requests, errors)
    _expect_exact_columns(conn, "extractions", ["id", "request_id", "prompt", "response_json", "image_reference", "created_at"], errors)
    _expect_exact_columns(conn, "usage_logs", expected_usage, errors)
    request_cols = _columns(conn, "requests")
    cols = _columns(conn, "usage_logs")
    if "attempt_number" not in cols:
        errors.append("usage_logs.attempt_number missing")
    if "estimated_cost" not in cols:
        errors.append("usage_logs.estimated_cost missing")
    if "status" not in request_cols:
        errors.append("requests.status missing")
    if errors:
        return errors
    cost = str(cols["estimated_cost"]["type"]).upper()
    if profile_b:
        if "DOUBLE" not in cost and "FLOAT" not in cost:
            errors.append("Profile B estimated_cost must remain floating physical type")
    elif "NUMERIC(18, 8)" not in cost and "NUMERIC(18,8)" not in cost:
        errors.append(f"Gate 3 estimated_cost must be NUMERIC(18,8), got {cost}")
    if "request_id" in request_cols:
        errors.append("requests.request_id must be absent")
    if cols["attempt_number"].get("default") is not None:
        errors.append("usage_logs.attempt_number server default must be absent")
    if request_cols["status"].get("default") is not None:
        errors.append("requests.status server default must be absent")
    return errors


def verify_gate3(bind: Engine | Connection, *, require_version_table: bool = False) -> SchemaCheckResult:
    """Verify physical Transcription schema equals the approved Gate 3 target. No writes."""
    def checks(conn: Connection) -> list[str]:
        errors = _common_gate3_shape_checks(conn)
        if errors:
            return errors
        req = _columns(conn, "requests")
        ext = _columns(conn, "extractions")
        uniques = _unique_sets(conn, "usage_logs")
        if not req["application_id"]["nullable"]:
            errors.append("Gate 3 requests.application_id must be nullable")
        if not ext["prompt"]["nullable"]:
            errors.append("Gate 3 extractions.prompt must be nullable")
        if tuple(["request_id"]) in uniques.values():
            errors.append("Gate 3 request-only usage uniqueness must be absent")
        if tuple(["request_id", "attempt_number"]) not in uniques.values():
            errors.append("Gate 3 composite usage uniqueness missing")
        if require_version_table and VERSION_TABLE not in sa.inspect(conn).get_table_names():
            errors.append(f"{VERSION_TABLE} missing")
        return errors

    return _run_readonly(bind, "gate3", checks)
