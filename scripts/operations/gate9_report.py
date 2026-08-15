from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Literal, Mapping, Sequence

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.pool import NullPool

EXIT_SUCCESS = 0
EXIT_PRECONDITION = 2
EXIT_OPERATIONAL = 4
EXIT_VALIDATION = 5

DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(days=31)
DEFAULT_LIMIT = 100
MAX_LIMIT = 1_000
INTERNAL_BATCH_SIZE = 1_000

PLATFORM_DSN_ENV = "G9_PLATFORM_DATABASE_URL"
TRANSCRIPTION_DSN_ENV = "G9_TRANSCRIPTION_DATABASE_URL"
WRITER_DSN_ENV = "G9_WRITER_DATABASE_URL"
CORRELATION_COLLECTIONS = (
    "events",
    "processing_items",
    "executions",
    "interactions",
    "answers",
    "transcription_requests",
    "usage_attempts",
    "writer_ledger",
)


class Gate9ReportError(Exception):
    exit_code = EXIT_VALIDATION


class ConfigurationError(Gate9ReportError):
    exit_code = EXIT_PRECONDITION


class OperationalError(Gate9ReportError):
    exit_code = EXIT_OPERATIONAL


@dataclass(frozen=True)
class Window:
    since: datetime
    until: datetime


@dataclass
class _AttemptAccumulator:
    attempt_count: int = 0
    known_attempts: int = 0
    input_sum: int = 0
    output_sum: int = 0
    provider_total_sum: int = 0
    known_cost: Decimal = Decimal("0")
    known_cost_count: int = 0

    def add(self, row: Mapping[str, Any]) -> None:
        self.attempt_count += 1
        input_tokens = row.get("input_tokens")
        output_tokens = row.get("output_tokens")
        total_tokens = row.get("total_tokens")
        if input_tokens is not None:
            self.input_sum += int(input_tokens)
        if output_tokens is not None:
            self.output_sum += int(output_tokens)
        if total_tokens is not None:
            self.provider_total_sum += int(total_tokens)
        if all(value is not None for value in (input_tokens, output_tokens, total_tokens)):
            self.known_attempts += 1
        cost = row.get("estimated_cost")
        if cost is not None:
            self.known_cost += Decimal(str(cost))
            self.known_cost_count += 1

    def add_summary(self, row: Mapping[str, Any]) -> None:
        self.attempt_count += int(row["attempt_count"])
        self.known_attempts += int(row["known_usage_attempt_count"])
        self.input_sum += int(row["input_tokens_known_sum"])
        self.output_sum += int(row["output_tokens_known_sum"])
        self.provider_total_sum += int(row["provider_total_tokens_known_sum"])
        known_cost_count = int(row["known_cost_count"])
        known_cost = row["known_cost_sum"]
        if known_cost_count:
            self.known_cost += Decimal(str(known_cost))
            self.known_cost_count += known_cost_count

    def result(self) -> dict[str, Any]:
        unknown_attempts = self.attempt_count - self.known_attempts
        return {
            "attempt_count": self.attempt_count,
            "known_usage_attempt_count": self.known_attempts,
            "unknown_usage_attempt_count": unknown_attempts,
            "input_tokens_known_sum": self.input_sum,
            "output_tokens_known_sum": self.output_sum,
            "provider_total_tokens_known_sum": self.provider_total_sum,
            "known_cost_sum": str(self.known_cost) if self.known_cost_count else None,
            "partial_usage": unknown_attempts > 0,
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError("Invalid UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError("UTC timestamps must include an offset")
    return parsed.astimezone(UTC)


def resolve_window(since: str | None, until: str | None) -> Window:
    if (since is None) != (until is None):
        raise ConfigurationError("Both --since and --until are required together")
    if since is None:
        end = _utc_now()
        start = end - DEFAULT_WINDOW
    else:
        start = _parse_utc(since)
        end = _parse_utc(until or "")
    if start >= end:
        raise ConfigurationError("The UTC interval must be increasing")
    if end - start > MAX_WINDOW:
        raise ConfigurationError("The UTC interval exceeds 31 days")
    return Window(start, end)


def validate_limit(value: int) -> int:
    if value < 1 or value > MAX_LIMIT:
        raise ConfigurationError("Row limit must be between 1 and 1000")
    return value


def validate_uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ConfigurationError(f"Invalid {label}") from exc


def encode_cursor(timestamp: datetime, stable_id: str) -> str:
    payload = json.dumps(
        {"timestamp": timestamp.astimezone(UTC).isoformat(), "id": stable_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if set(payload) != {"timestamp", "id"}:
            raise ValueError
        timestamp = _parse_utc(payload["timestamp"])
        stable_id = validate_uuid(payload["id"], "cursor identity")
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Invalid cursor") from exc
    return timestamp, stable_id


def _dsn(name: str, *, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if required:
        raise ConfigurationError(f"Missing required configuration: {name}")
    return None


class ReadOnlyDatabase(AbstractContextManager[Connection]):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._engine: Engine | None = None
        self._connection: Connection | None = None
        self._transaction: Any = None

    def __enter__(self) -> Connection:
        try:
            self._engine = create_engine(self._dsn, poolclass=NullPool)
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
            self._connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            self._connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            self._connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            self._connection.exec_driver_sql(
                "SET LOCAL idle_in_transaction_session_timeout = '30s'"
            )
            read_only = self._connection.execute(
                text("SELECT current_setting('transaction_read_only')")
            ).scalar_one()
            if read_only != "on":
                raise ConfigurationError("Database read-only guard was not established")
            return self._connection
        except ConfigurationError:
            self._close(commit=False)
            raise
        except Exception as exc:
            self._close(commit=False)
            raise OperationalError("Unable to establish protected reporting transaction") from exc

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Literal[False]:
        self._close(commit=exc_type is None)
        return False

    def _close(self, *, commit: bool) -> None:
        try:
            if self._transaction is not None and self._transaction.is_active:
                if commit:
                    self._transaction.commit()
                else:
                    self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
            if self._engine is not None:
                self._engine.dispose()
            self._transaction = None
            self._connection = None
            self._engine = None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _mapping(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row._mapping.items()}


def _page(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int,
    timestamp_key: str,
    id_key: str,
) -> dict[str, Any]:
    truncated = len(rows) > limit
    emitted = list(rows[:limit])
    next_cursor = None
    if truncated and emitted:
        timestamp = emitted[-1][timestamp_key]
        if isinstance(timestamp, str):
            timestamp = _parse_utc(timestamp)
        next_cursor = encode_cursor(timestamp, str(emitted[-1][id_key]))
    return {
        "rows": emitted,
        "returned_count": len(emitted),
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def report_summary(window: Window) -> dict[str, Any]:
    query = text(
        """
        WITH item_terminals AS (
          SELECT p.id, p.event_id, p.status,
            CASE
              WHEN p.status IN ('COMPLETED','IGNORED') THEN p.completed_at
              WHEN p.status = 'PERSISTENCE_FAILED' THEN (
                SELECT max(x.completed_at) FROM executions x
                WHERE x.processing_item_id = p.id
                  AND x.operation IN ('PERSISTENCE_FAILED_FINAL',
                                      'PERSISTENCE_RECONCILED_REJECTED')
              )
              WHEN p.status = 'CANCELLED' THEN (
                SELECT max(x.completed_at) FROM executions x
                WHERE x.processing_item_id = p.id AND x.operation = 'USER_CANCELLED'
              )
              WHEN p.status = 'EXPIRED' THEN (
                SELECT max(x.completed_at) FROM executions x
                WHERE x.processing_item_id = p.id
                  AND x.operation = 'USER_INPUT_EXPIRED'
              )
              ELSE NULL
            END AS terminal_at
          FROM processing_items p
        ), duration_values AS (
          SELECT extract(epoch FROM (i.terminal_at - e.received_at)) AS seconds
          FROM item_terminals i
          JOIN events e ON e.id = i.event_id
          WHERE e.received_at >= :since AND e.received_at < :until
            AND i.terminal_at IS NOT NULL
        )
        SELECT
          count(*)::bigint AS event_count,
          count(*) FILTER (WHERE e.status = 'FAILED')::bigint AS failed_events,
          count(DISTINCT i.id)::bigint AS processing_item_count,
          count(*) FILTER (WHERE i.status = 'COMPLETED')::bigint AS completed_items,
          count(*) FILTER (WHERE i.status IN ('EXTRACTION_FAILED','PERSISTENCE_FAILED','FAILED'))::bigint AS failed_items,
          count(*) FILTER (WHERE i.status IN ('WAITING_USER_INPUT','PERSIST_RETRYABLE','PERSIST_OUTCOME_UNKNOWN'))::bigint AS blocked_items,
          (SELECT count(*)::bigint FROM executions x
           WHERE x.operation = 'FINAL_NOTIFICATION_OUTCOME_UNKNOWN'
             AND x.started_at >= :since AND x.started_at < :until)
            AS final_notification_unknown_count,
          (SELECT avg(seconds) FROM duration_values) AS average_business_e2e_seconds,
          (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY seconds)
           FROM duration_values) AS p50_business_e2e_seconds,
          (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds)
           FROM duration_values) AS p95_business_e2e_seconds
        FROM events e
        LEFT JOIN item_terminals i ON i.event_id = e.id
        WHERE e.received_at >= :since AND e.received_at < :until
        """
    )
    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as connection:
        result = _mapping(
            connection.execute(
                query, {"since": window.since, "until": window.until}
            ).one()
        )
    return {"report": "summary", "window": _json_value(window.__dict__), **result}


def _platform_page(
    *,
    query: str,
    window: Window,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "since": window.since,
        "until": window.until,
        "limit_plus_one": limit + 1,
        "cursor_ts": cursor[0] if cursor else None,
        "cursor_id": cursor[1] if cursor else None,
    }
    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as connection:
        return [_mapping(row) for row in connection.execute(text(query), params)]


def report_executions(
    window: Window, limit: int, cursor: tuple[datetime, str] | None
) -> dict[str, Any]:
    rows = _platform_page(
        query="""
          SELECT id, event_id, processing_item_id, correlation_id, component,
                 operation, status, effect_status, attempt, started_at,
                 completed_at, duration_ms, error_code
          FROM executions
          WHERE started_at >= :since AND started_at < :until
            AND (:cursor_ts IS NULL OR started_at < :cursor_ts
                 OR (started_at = :cursor_ts AND id > :cursor_id))
          ORDER BY started_at DESC, id ASC
          LIMIT :limit_plus_one
        """,
        window=window,
        limit=limit,
        cursor=cursor,
    )
    return {
        "report": "executions",
        "window": _json_value(window.__dict__),
        **_page(rows, limit=limit, timestamp_key="started_at", id_key="id"),
    }


def report_blocked(
    window: Window, limit: int, cursor: tuple[datetime, str] | None
) -> dict[str, Any]:
    rows = _platform_page(
        query="""
          SELECT id, event_id, correlation_id, organization_id, status,
                 attempt_count, persistence_attempt_count, created_at,
                 waiting_since, persistence_next_attempt_at, error_code
          FROM processing_items
          WHERE created_at >= :since AND created_at < :until
            AND status IN ('WAITING_USER_INPUT','PERSIST_RETRYABLE','PERSIST_OUTCOME_UNKNOWN')
            AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                 OR (created_at = :cursor_ts AND id > :cursor_id))
          ORDER BY created_at DESC, id ASC
          LIMIT :limit_plus_one
        """,
        window=window,
        limit=limit,
        cursor=cursor,
    )
    return {
        "report": "blocked",
        "window": _json_value(window.__dict__),
        **_page(rows, limit=limit, timestamp_key="created_at", id_key="id"),
    }


def report_failures(window: Window, limit: int) -> dict[str, Any]:
    query = text(
        """
        WITH failures AS (
          SELECT 'EVENT'::text AS component, 'ROUTING'::text AS operation,
                 status, error_code, received_at AS occurred_at
          FROM events
          WHERE received_at >= :since AND received_at < :until
            AND error_code IS NOT NULL
          UNION ALL
          SELECT 'PROCESSING_ITEM', status, status, error_code, created_at
          FROM processing_items
          WHERE created_at >= :since AND created_at < :until
            AND error_code IS NOT NULL
          UNION ALL
          SELECT component, operation, status, error_code, started_at
          FROM executions
          WHERE started_at >= :since AND started_at < :until
            AND error_code IS NOT NULL
        )
        SELECT component, operation, status, error_code, count(*)::bigint AS count
        FROM failures
        GROUP BY component, operation, status, error_code
        ORDER BY count DESC, component ASC, operation ASC, error_code ASC
        LIMIT :limit_plus_one
        """
    )
    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as connection:
        rows = [
            _mapping(row)
            for row in connection.execute(
                query,
                {
                    "since": window.since,
                    "until": window.until,
                    "limit_plus_one": limit + 1,
                },
            )
        ]
    return {
        "report": "failures",
        "window": _json_value(window.__dict__),
        "rows": rows[:limit],
        "returned_count": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "next_cursor": None,
    }


def _attempt_aggregate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    accumulator = _AttemptAccumulator()
    for row in rows:
        accumulator.add(row)
    return accumulator.result()


def _attempt_aggregate_from_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    attempt_count = int(row["attempt_count"])
    known_attempts = int(row["known_usage_attempt_count"])
    known_cost_count = int(row["known_cost_count"])
    known_cost = row["known_cost_sum"]
    return {
        "attempt_count": attempt_count,
        "known_usage_attempt_count": known_attempts,
        "unknown_usage_attempt_count": attempt_count - known_attempts,
        "input_tokens_known_sum": int(row["input_tokens_known_sum"]),
        "output_tokens_known_sum": int(row["output_tokens_known_sum"]),
        "provider_total_tokens_known_sum": int(
            row["provider_total_tokens_known_sum"]
        ),
        "known_cost_sum": str(known_cost) if known_cost_count else None,
        "partial_usage": attempt_count != known_attempts,
    }


def _merge_bounded_rows(
    current: Sequence[dict[str, Any]],
    incoming: Sequence[dict[str, Any]],
    *,
    limit: int,
    timestamp_key: str,
    id_key: str = "id",
) -> list[dict[str, Any]]:
    merged = [*current, *incoming]
    merged.sort(key=lambda row: str(row[id_key]))
    merged.sort(key=lambda row: str(row[timestamp_key]), reverse=True)
    return merged[: limit + 1]


def report_tokens_document(
    processing_item_id: str,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> dict[str, Any]:
    processing_item_id = validate_uuid(processing_item_id, "processing item ID")
    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as platform:
        identity_row = platform.execute(
            text(
                """
                SELECT p.id AS processing_item_id, p.correlation_id,
                       p.organization_id, p.event_id
                FROM processing_items p
                WHERE p.id = :item_id
                """
            ),
            {"item_id": processing_item_id},
        ).mappings().one_or_none()
    if identity_row is None:
        raise ConfigurationError("Processing item was not found")

    params = {
        "request_id": processing_item_id,
        "limit_plus_one": limit + 1,
        "cursor_ts": cursor[0] if cursor else None,
        "cursor_id": cursor[1] if cursor else None,
    }
    with ReadOnlyDatabase(_dsn(TRANSCRIPTION_DSN_ENV) or "") as transcription:
        aggregate_row = transcription.execute(
            text(
                """
                SELECT count(*)::bigint AS attempt_count,
                       count(*) FILTER (WHERE input_tokens IS NOT NULL
                          AND output_tokens IS NOT NULL
                          AND total_tokens IS NOT NULL)::bigint
                          AS known_usage_attempt_count,
                       coalesce(sum(input_tokens), 0)::bigint
                          AS input_tokens_known_sum,
                       coalesce(sum(output_tokens), 0)::bigint
                          AS output_tokens_known_sum,
                       coalesce(sum(total_tokens), 0)::bigint
                          AS provider_total_tokens_known_sum,
                       count(estimated_cost)::bigint AS known_cost_count,
                       sum(estimated_cost) AS known_cost_sum
                FROM usage_logs WHERE request_id = CAST(:request_id AS uuid)
                """
            ),
            {"request_id": processing_item_id},
        ).mappings().one()
        details = [
            _mapping(row)
            for row in transcription.execute(
                text(
                    """
                    SELECT id, request_id, attempt_number, provider, model_name,
                           status, input_tokens, output_tokens, total_tokens,
                           estimated_cost, currency, usage_status,
                           sanitized_error_code, created_at
                    FROM usage_logs
                    WHERE request_id = CAST(:request_id AS uuid)
                      AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                           OR (created_at = :cursor_ts AND id::text > :cursor_id))
                    ORDER BY created_at DESC, id ASC
                    LIMIT :limit_plus_one
                    """
                ),
                params,
            )
        ]
    identity = {key: _json_value(value) for key, value in identity_row.items()}
    return {
        "report": "tokens-document",
        "identity": {**identity, "transcription_request_id": processing_item_id},
        "totals": _attempt_aggregate_from_summary(dict(aggregate_row)),
        **_page(details, limit=limit, timestamp_key="created_at", id_key="id"),
    }


def report_tokens_organization(
    organization_id: str,
    window: Window,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> dict[str, Any]:
    organization_id = validate_uuid(organization_id, "organization ID")
    totals = _AttemptAccumulator()
    document_count = 0
    detail_rows: list[dict[str, Any]] = []
    item_cursor: str | None = None

    while True:
        with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as platform:
            request_ids = [
                str(row.id)
                for row in platform.execute(
                    text(
                        """
                        SELECT id FROM processing_items
                        WHERE organization_id = :organization_id
                          AND (:item_cursor IS NULL OR id > :item_cursor)
                        ORDER BY id ASC LIMIT :batch_size
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "item_cursor": item_cursor,
                        "batch_size": INTERNAL_BATCH_SIZE,
                    },
                )
            ]
        if not request_ids:
            break
        params = {
            "request_ids": request_ids,
            "since": window.since,
            "until": window.until,
            "limit_plus_one": limit + 1,
            "cursor_ts": cursor[0] if cursor else None,
            "cursor_id": cursor[1] if cursor else None,
        }
        with ReadOnlyDatabase(_dsn(TRANSCRIPTION_DSN_ENV) or "") as transcription:
            summary = transcription.execute(
                text(
                    """
                    SELECT count(*)::bigint AS attempt_count,
                           count(*) FILTER (WHERE input_tokens IS NOT NULL
                             AND output_tokens IS NOT NULL
                             AND total_tokens IS NOT NULL)::bigint
                             AS known_usage_attempt_count,
                           coalesce(sum(input_tokens), 0)::bigint
                             AS input_tokens_known_sum,
                           coalesce(sum(output_tokens), 0)::bigint
                             AS output_tokens_known_sum,
                           coalesce(sum(total_tokens), 0)::bigint
                             AS provider_total_tokens_known_sum,
                           count(estimated_cost)::bigint AS known_cost_count,
                           sum(estimated_cost) AS known_cost_sum,
                           count(DISTINCT request_id)::bigint AS document_count
                    FROM usage_logs
                    WHERE request_id::text = ANY(CAST(:request_ids AS text[]))
                      AND created_at >= :since AND created_at < :until
                    """
                ),
                params,
            ).mappings().one()
            batch_details = [
                _mapping(row)
                for row in transcription.execute(
                    text(
                        """
                        SELECT id, request_id, attempt_number, provider, model_name,
                               status, input_tokens, output_tokens, total_tokens,
                               estimated_cost, currency, usage_status,
                               sanitized_error_code, created_at
                        FROM usage_logs
                        WHERE request_id::text = ANY(CAST(:request_ids AS text[]))
                          AND created_at >= :since AND created_at < :until
                          AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                               OR (created_at = :cursor_ts AND id::text > :cursor_id))
                        ORDER BY created_at DESC, id ASC LIMIT :limit_plus_one
                        """
                    ),
                    params,
                )
            ]
        totals.add_summary(dict(summary))
        document_count += int(summary["document_count"])
        detail_rows = _merge_bounded_rows(
            detail_rows,
            batch_details,
            limit=limit,
            timestamp_key="created_at",
        )
        item_cursor = request_ids[-1]

    page = _page(
        detail_rows,
        limit=limit,
        timestamp_key="created_at",
        id_key="id",
    )
    return {
        "report": "tokens-organization",
        "organization_id": organization_id,
        "window": _json_value(window.__dict__),
        "document_count": document_count,
        "totals": totals.result(),
        **page,
    }


def report_service_usage(window: Window, limit: int) -> dict[str, Any]:
    with ReadOnlyDatabase(_dsn(TRANSCRIPTION_DSN_ENV) or "") as transcription:
        transcription_rows = [
            _mapping(row)
            for row in transcription.execute(
                text(
                    """
                    SELECT provider, model_name, status,
                           count(*)::bigint AS attempt_count,
                           count(*) FILTER (WHERE input_tokens IS NOT NULL
                              AND output_tokens IS NOT NULL
                              AND total_tokens IS NOT NULL)::bigint AS known_usage_attempt_count,
                           count(*) FILTER (WHERE input_tokens IS NULL
                              OR output_tokens IS NULL
                              OR total_tokens IS NULL)::bigint AS unknown_usage_attempt_count,
                           coalesce(sum(input_tokens),0)::bigint AS input_tokens_known_sum,
                           coalesce(sum(output_tokens),0)::bigint AS output_tokens_known_sum,
                           coalesce(sum(total_tokens),0)::bigint AS provider_total_tokens_known_sum,
                           sum(estimated_cost) AS known_cost_sum
                    FROM usage_logs
                    WHERE created_at >= :since AND created_at < :until
                    GROUP BY provider, model_name, status
                    ORDER BY attempt_count DESC, provider ASC, model_name ASC, status ASC
                    LIMIT :limit_plus_one
                    """
                ),
                {
                    "since": window.since,
                    "until": window.until,
                    "limit_plus_one": limit + 1,
                },
            )
        ]
    for row in transcription_rows:
        row["partial_usage"] = row["unknown_usage_attempt_count"] > 0

    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as platform:
        platform_rows = [
            _mapping(row)
            for row in platform.execute(
                text(
                    """
                    SELECT component, operation, status, effect_status,
                           count(*)::bigint AS checkpoint_count
                    FROM executions
                    WHERE started_at >= :since AND started_at < :until
                    GROUP BY component, operation, status, effect_status
                    ORDER BY checkpoint_count DESC, component ASC, operation ASC
                    LIMIT :limit_plus_one
                    """
                ),
                {
                    "since": window.since,
                    "until": window.until,
                    "limit_plus_one": limit + 1,
                },
            )
        ]

    writer_rows: list[dict[str, Any]] | None = None
    writer_dsn = _dsn(WRITER_DSN_ENV, required=False)
    if writer_dsn:
        with ReadOnlyDatabase(writer_dsn) as writer:
            writer_rows = [
                _mapping(row)
                for row in writer.execute(
                    text(
                        """
                        SELECT status, error_code, count(*)::bigint AS ledger_count
                        FROM write_ledger
                        WHERE created_at >= :since AND created_at < :until
                        GROUP BY status, error_code
                        ORDER BY ledger_count DESC, status ASC, error_code ASC
                        LIMIT :limit_plus_one
                        """
                    ),
                    {
                        "since": window.since,
                        "until": window.until,
                        "limit_plus_one": limit + 1,
                    },
                )
            ]
    return {
        "report": "service-usage",
        "window": _json_value(window.__dict__),
        "authoritative_sources": {
            "transcription_usage_logs": transcription_rows[:limit],
            "platform_executions": platform_rows[:limit],
            "writer_write_ledger": None if writer_rows is None else writer_rows[:limit],
        },
        "truncated": {
            "transcription_usage_logs": len(transcription_rows) > limit,
            "platform_executions": len(platform_rows) > limit,
            "writer_write_ledger": writer_rows is not None and len(writer_rows) > limit,
        },
    }


def report_durations(
    window: Window, limit: int, cursor: tuple[datetime, str] | None
) -> dict[str, Any]:
    query = """
      WITH duration_rows AS (
        SELECT
          p.id, p.event_id, p.correlation_id, p.organization_id, p.status,
          e.received_at,
          CASE
            WHEN p.status IN ('COMPLETED','IGNORED') THEN p.completed_at
            WHEN p.status = 'PERSISTENCE_FAILED' THEN (
              SELECT max(x.completed_at) FROM executions x
              WHERE x.processing_item_id = p.id
                AND x.operation IN ('PERSISTENCE_FAILED_FINAL','PERSISTENCE_RECONCILED_REJECTED')
            )
            WHEN p.status = 'CANCELLED' THEN (
              SELECT max(x.completed_at) FROM executions x
              WHERE x.processing_item_id = p.id AND x.operation = 'USER_CANCELLED'
            )
            WHEN p.status = 'EXPIRED' THEN (
              SELECT max(x.completed_at) FROM executions x
              WHERE x.processing_item_id = p.id AND x.operation = 'USER_INPUT_EXPIRED'
            )
            ELSE NULL
          END AS terminal_at,
          coalesce((
            SELECT sum(extract(epoch FROM (
              coalesce(ui.resolved_at, CURRENT_TIMESTAMP) - ui.waiting_since
            )))
            FROM user_interactions ui
            WHERE ui.processing_item_id = p.id
              AND ui.waiting_since IS NOT NULL
          ), 0) AS human_wait_seconds,
          (
            SELECT extract(epoch FROM (terminal_notice.completed_at - dispatched.completed_at))
            FROM executions dispatched
            JOIN executions terminal_notice
              ON terminal_notice.processing_item_id = dispatched.processing_item_id
             AND terminal_notice.operation IN ('FINAL_NOTIFICATION_ACKNOWLEDGED','FINAL_NOTIFICATION_OUTCOME_UNKNOWN')
             AND split_part(terminal_notice.operation_idempotency_key, ':', 3)
                 = split_part(dispatched.operation_idempotency_key, ':', 3)
            WHERE dispatched.processing_item_id = p.id
              AND dispatched.operation = 'FINAL_NOTIFICATION_DISPATCHED'
            ORDER BY terminal_notice.completed_at DESC
            LIMIT 1
          ) AS final_notification_seconds
        FROM processing_items p
        JOIN events e ON e.id = p.event_id
        WHERE e.received_at >= :since AND e.received_at < :until
      )
      SELECT id, event_id, correlation_id, organization_id, status, received_at,
             terminal_at,
             (terminal_at IS NOT NULL) AS duration_available,
             CASE WHEN terminal_at IS NOT NULL
                  THEN extract(epoch FROM (terminal_at - received_at)) END
                  AS business_e2e_seconds,
             human_wait_seconds, final_notification_seconds,
             CASE WHEN terminal_at IS NULL
                  THEN 'NO_AUTHORITATIVE_TERMINAL_TIMESTAMP' END AS unavailable_reason
      FROM duration_rows
      WHERE (:cursor_ts IS NULL OR received_at < :cursor_ts
             OR (received_at = :cursor_ts AND id > :cursor_id))
      ORDER BY received_at DESC, id ASC
      LIMIT :limit_plus_one
    """
    rows = _platform_page(
        query=query,
        window=window,
        limit=limit,
        cursor=cursor,
    )
    return {
        "report": "durations",
        "window": _json_value(window.__dict__),
        **_page(rows, limit=limit, timestamp_key="received_at", id_key="id"),
    }


def _bounded_query(
    connection: Connection,
    sql: str,
    params: Mapping[str, Any],
    limit: int,
    *,
    timestamp_key: str,
    id_key: str = "id",
    cursor: tuple[datetime, str] | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    rows = [
        _mapping(row)
        for row in connection.execute(
            text(sql),
            {
                **params,
                "limit_plus_one": limit + 1,
                "cursor_ts": cursor[0] if cursor else None,
                "cursor_id": cursor[1] if cursor else None,
            },
        )
    ]
    page = _page(
        rows,
        limit=limit,
        timestamp_key=timestamp_key,
        id_key=id_key,
    )
    return page["rows"], page["truncated"], page["next_cursor"]


def _writer_rows_for_correlation(
    correlation_id: str,
    writer_dsn: str,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    candidates: list[dict[str, Any]] = []
    item_cursor: str | None = None
    while True:
        with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as platform:
            item_ids = [
                str(row.id)
                for row in platform.execute(
                    text(
                        """
                        SELECT id FROM processing_items
                        WHERE correlation_id = :correlation_id
                          AND (:item_cursor IS NULL OR id > :item_cursor)
                        ORDER BY id ASC LIMIT :batch_size
                        """
                    ),
                    {
                        "correlation_id": correlation_id,
                        "item_cursor": item_cursor,
                        "batch_size": INTERNAL_BATCH_SIZE,
                    },
                )
            ]
        if not item_ids:
            break
        with ReadOnlyDatabase(writer_dsn) as writer:
            batch, _, _ = _bounded_query(
                writer,
                """
                  SELECT id, processing_item_id, organization_id, status,
                         committed_record_id, error_code, attempt_count,
                         created_at, updated_at, committed_at
                  FROM write_ledger
                  WHERE processing_item_id = ANY(CAST(:item_ids AS text[]))
                    AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                         OR (created_at = :cursor_ts AND id::text > :cursor_id))
                  ORDER BY created_at DESC, id ASC LIMIT :limit_plus_one
                """,
                {"item_ids": item_ids},
                limit,
                timestamp_key="created_at",
                cursor=cursor,
            )
        candidates = _merge_bounded_rows(
            candidates,
            batch,
            limit=limit,
            timestamp_key="created_at",
        )
        item_cursor = item_ids[-1]
    page = _page(
        candidates,
        limit=limit,
        timestamp_key="created_at",
        id_key="id",
    )
    return page["rows"], page["truncated"], page["next_cursor"]


def report_correlation(
    correlation_id: str,
    limit: int,
    *,
    collection: str | None = None,
    cursor: tuple[datetime, str] | None = None,
) -> dict[str, Any]:
    correlation_id = validate_uuid(correlation_id, "correlation ID")
    if collection is not None and collection not in CORRELATION_COLLECTIONS:
        raise ConfigurationError("Invalid correlation collection")

    def collection_cursor(name: str) -> tuple[datetime, str] | None:
        return cursor if collection == name else None

    with ReadOnlyDatabase(_dsn(PLATFORM_DSN_ENV) or "") as platform:
        events, events_truncated, events_next = _bounded_query(
            platform,
            """
              SELECT id, correlation_id, provider, organization_id, instance_id, user_id,
                     message_type, status, received_at, routed_at, failed_at, error_code
              FROM events WHERE correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR received_at < :cursor_ts
                     OR (received_at = :cursor_ts AND id > :cursor_id))
              ORDER BY received_at DESC, id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="received_at",
            cursor=collection_cursor("events"),
        )
        items, items_truncated, items_next = _bounded_query(
            platform,
            """
              SELECT id, event_id, correlation_id, organization_id, instance_id,
                     user_id, sequence, status, question_type, outcome_reason,
                     attempt_count, persistence_attempt_count, external_operation_status,
                     error_code, created_at, extracted_at, activated_at, completed_at
              FROM processing_items WHERE correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                     OR (created_at = :cursor_ts AND id > :cursor_id))
              ORDER BY created_at DESC, id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="created_at",
            cursor=collection_cursor("processing_items"),
        )
        executions, executions_truncated, executions_next = _bounded_query(
            platform,
            """
              SELECT id, event_id, processing_item_id, correlation_id, component,
                     operation, status, effect_status, external_reference, attempt,
                     started_at, completed_at, duration_ms, error_code
              FROM executions WHERE correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR started_at < :cursor_ts
                     OR (started_at = :cursor_ts AND id > :cursor_id))
              ORDER BY started_at DESC, id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="started_at",
            cursor=collection_cursor("executions"),
        )
        interactions, interactions_truncated, interactions_next = _bounded_query(
            platform,
            """
              SELECT ui.id, ui.processing_item_id, ui.generation,
                     ui.question_type, ui.status, ui.waiting_since,
                     ui.resolved_at, ui.created_at
              FROM user_interactions ui
              JOIN processing_items p ON p.id = ui.processing_item_id
              WHERE p.correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR ui.created_at < :cursor_ts
                     OR (ui.created_at = :cursor_ts AND ui.id > :cursor_id))
              ORDER BY ui.created_at DESC, ui.id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="created_at",
            cursor=collection_cursor("interactions"),
        )
        answers, answers_truncated, answers_next = _bounded_query(
            platform,
            """
              SELECT ua.id, ua.interaction_id, ua.processing_item_id,
                     ua.inbound_event_id, ua.status, ua.error_code,
                     ua.received_at, ua.applied_at
              FROM user_answers ua
              JOIN processing_items p ON p.id = ua.processing_item_id
              WHERE p.correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR ua.received_at < :cursor_ts
                     OR (ua.received_at = :cursor_ts AND ua.id > :cursor_id))
              ORDER BY ua.received_at DESC, ua.id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="received_at",
            cursor=collection_cursor("answers"),
        )

    transcription_requests: list[dict[str, Any]] = []
    usage_attempts: list[dict[str, Any]] = []
    transcription_truncated = usage_truncated = False
    transcription_next = usage_next = None
    with ReadOnlyDatabase(_dsn(TRANSCRIPTION_DSN_ENV) or "") as transcription:
        (
            transcription_requests,
            transcription_truncated,
            transcription_next,
        ) = _bounded_query(
            transcription,
            """
              SELECT id, correlation_id, status, received_at, completed_at,
                     processing_started_at, processing_time_ms, error_code, created_at
              FROM requests
              WHERE correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR created_at < :cursor_ts
                     OR (created_at = :cursor_ts AND id::text > :cursor_id))
              ORDER BY created_at DESC, id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="created_at",
            cursor=collection_cursor("transcription_requests"),
        )
        usage_attempts, usage_truncated, usage_next = _bounded_query(
            transcription,
            """
              SELECT u.id, u.request_id, u.attempt_number, u.provider,
                     u.model_name, u.status, u.input_tokens, u.output_tokens,
                     u.total_tokens, u.estimated_cost, u.currency,
                     u.usage_status, u.sanitized_error_code, u.created_at
              FROM usage_logs u JOIN requests r ON r.id = u.request_id
              WHERE r.correlation_id = :correlation_id
                AND (:cursor_ts IS NULL OR u.created_at < :cursor_ts
                     OR (u.created_at = :cursor_ts AND u.id::text > :cursor_id))
              ORDER BY u.created_at DESC, u.id ASC LIMIT :limit_plus_one
            """,
            {"correlation_id": correlation_id},
            limit,
            timestamp_key="created_at",
            cursor=collection_cursor("usage_attempts"),
        )

    writer_ledger: list[dict[str, Any]] | None = None
    writer_truncated = False
    writer_next = None
    writer_dsn = _dsn(WRITER_DSN_ENV, required=False)
    if writer_dsn:
        writer_ledger, writer_truncated, writer_next = _writer_rows_for_correlation(
            correlation_id,
            writer_dsn,
            limit,
            collection_cursor("writer_ledger"),
        )

    return {
        "report": "correlation",
        "correlation_id": correlation_id,
        "events": events,
        "processing_items": items,
        "executions": executions,
        "interactions": interactions,
        "answers": answers,
        "transcription_requests": transcription_requests,
        "usage_attempts": usage_attempts,
        "writer_ledger": writer_ledger,
        "truncated": {
            "events": events_truncated,
            "processing_items": items_truncated,
            "executions": executions_truncated,
            "interactions": interactions_truncated,
            "answers": answers_truncated,
            "transcription_requests": transcription_truncated,
            "usage_attempts": usage_truncated,
            "writer_ledger": writer_truncated,
        },
        "next_cursors": {
            "events": events_next,
            "processing_items": items_next,
            "executions": executions_next,
            "interactions": interactions_next,
            "answers": answers_next,
            "transcription_requests": transcription_next,
            "usage_attempts": usage_next,
            "writer_ledger": writer_next,
        },
    }


def _emit(payload: Mapping[str, Any], output_format: str) -> None:
    safe = _json_value(payload)
    if output_format == "json":
        print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
        return
    print(f"report: {safe.get('report', 'gate9')}")
    for key, value in safe.items():
        if key == "report":
            continue
        print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gate 9 bounded read-only reports")
    parser.add_argument("--format", choices=("json", "table"), default="json")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--cursor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "summary",
        "executions",
        "blocked",
        "failures",
        "service-usage",
        "durations",
    ):
        subparsers.add_parser(command)
    document = subparsers.add_parser("tokens-document")
    document.add_argument("--processing-item-id", required=True)
    organization = subparsers.add_parser("tokens-organization")
    organization.add_argument("--organization-id", required=True)
    correlation = subparsers.add_parser("correlation")
    correlation.add_argument("--correlation-id", required=True)
    correlation.add_argument("--collection", choices=CORRELATION_COLLECTIONS)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    limit = validate_limit(args.limit)
    cursor = decode_cursor(args.cursor)
    if args.command == "tokens-document":
        return report_tokens_document(args.processing_item_id, limit, cursor)
    if args.command == "correlation":
        if args.since is not None or args.until is not None:
            raise ConfigurationError("Correlation lookup does not accept time flags")
        if cursor is not None and args.collection is None:
            raise ConfigurationError("Correlation cursor requires --collection")
        return report_correlation(
            args.correlation_id,
            limit,
            collection=args.collection,
            cursor=cursor,
        )

    window = resolve_window(args.since, args.until)
    if args.command == "summary":
        if cursor is not None:
            raise ConfigurationError("Summary does not accept a cursor")
        return report_summary(window)
    if args.command == "executions":
        return report_executions(window, limit, cursor)
    if args.command == "blocked":
        return report_blocked(window, limit, cursor)
    if args.command == "failures":
        if cursor is not None:
            raise ConfigurationError("Failures does not accept a cursor")
        return report_failures(window, limit)
    if args.command == "service-usage":
        if cursor is not None:
            raise ConfigurationError("Service usage does not accept a cursor")
        return report_service_usage(window, limit)
    if args.command == "durations":
        return report_durations(window, limit, cursor)
    if args.command == "tokens-organization":
        return report_tokens_organization(args.organization_id, window, limit, cursor)
    raise ConfigurationError("Unsupported report command")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        payload = run(args)
        _emit(payload, args.format)
        return EXIT_SUCCESS
    except Gate9ReportError as exc:
        print(
            json.dumps({"error": exc.__class__.__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return exc.exit_code
    except Exception:
        print(
            json.dumps(
                {
                    "error": "InternalValidationError",
                    "message": "Gate 9 reporting failed safely",
                }
            ),
            file=sys.stderr,
        )
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
