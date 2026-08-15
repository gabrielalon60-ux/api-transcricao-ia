from __future__ import annotations

import json
import socket
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / "operations" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate9_report = _load_script("gate9_report")
platform_backup = _load_script("platform_backup")
platform_restore = _load_script("platform_restore")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (b"pg_dump (PostgreSQL) 15.8\n", 15),
        (b"pg_dump (PostgreSQL) 15\r\n", 15),
        (b"pg_dump (PostgreSQL) 15.8 (Debian 15.8-1)\n", 15),
        (b"pg_dump (PostgreSQL) 15.8", 15),
    ],
)
def test_pg_dump_exact_valid_versions(output: bytes, expected: int) -> None:
    assert platform_backup.parse_client_version("pg_dump", output, 0) == expected


@pytest.mark.parametrize(
    "output",
    [
        b"pg_restore (PostgreSQL) 15.8\n",
        b"pg_restore (PostgreSQL) 15.8 (Ubuntu 15.8-1)\r\n",
    ],
)
def test_pg_restore_exact_valid_versions(output: bytes) -> None:
    assert platform_backup.parse_client_version("pg_restore", output, 0) == 15
    assert platform_restore.parse_client_version(output, 0) == 15


@pytest.mark.parametrize(
    ("executable", "output", "return_code"),
    [
        ("pg_dump", b"pg_restore (PostgreSQL) 15.1\n", 0),
        ("pg_restore", b"pg_dump (PostgreSQL) 15.1\n", 0),
        ("pg_dump", b" pg_dump (PostgreSQL) 15.1\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 15.1\nextra\n", 0),
        ("pg_dump", b"", 0),
        ("pg_dump", b"\xff", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 15.1\n", 1),
        ("pg_dump", b"pg_dump 15.1\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) x15\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 14.9\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 16.0\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 15.1\x00\n", 0),
        ("pg_dump", b"pg_dump (PostgreSQL) 15.1\r", 0),
    ],
)
def test_client_version_rejects_every_invalid_class(
    executable: str, output: bytes, return_code: int
) -> None:
    with pytest.raises(platform_backup.VersionError) as exc:
        platform_backup.parse_client_version(executable, output, return_code)
    assert exc.value.exit_code == 3


@pytest.mark.parametrize(
    "output",
    [
        b"pg_dump (PostgreSQL) 15.1\n",
        b" pg_restore (PostgreSQL) 15.1\n",
        b"pg_restore (PostgreSQL) 15.1\nextra\n",
        b"",
        b"\xff",
        b"pg_restore (PostgreSQL) 14.9\n",
        b"pg_restore (PostgreSQL) 16.0\n",
    ],
)
def test_restore_parser_rejects_invalid_output(output: bytes) -> None:
    with pytest.raises(platform_restore.VersionError):
        platform_restore.parse_client_version(output, 0)


@pytest.mark.parametrize("value", ["150000", "150001", "159999"])
def test_exact_server_version_accepts_postgresql_15(value: str) -> None:
    assert platform_backup.parse_server_version(value) == int(value)
    assert platform_restore.parse_server_version(value) == int(value)


@pytest.mark.parametrize(
    "value", [None, "", "15000", "1500000", "15.1", "abcdef", 150000, "149999", "160000"]
)
def test_exact_server_version_rejects_invalid_or_non_15(value: object) -> None:
    with pytest.raises(platform_backup.VersionError) as exc:
        platform_backup.parse_server_version(value)
    assert exc.value.exit_code == 3
    with pytest.raises(platform_restore.VersionError):
        platform_restore.parse_server_version(value)


def test_reporting_window_and_limit_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    monkeypatch.setattr(gate9_report, "_utc_now", lambda: now)
    default = gate9_report.resolve_window(None, None)
    assert default.until - default.since == timedelta(hours=24)
    assert gate9_report.validate_limit(100) == 100
    assert gate9_report.validate_limit(1_000) == 1_000
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.validate_limit(0)
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.validate_limit(1_001)
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.resolve_window(now.isoformat(), None)
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.resolve_window(
            (now - timedelta(days=31, seconds=1)).isoformat(), now.isoformat()
        )
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.resolve_window(now.isoformat(), now.isoformat())


def test_cursor_round_trip_and_tamper_rejection() -> None:
    timestamp = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
    identity = "f19a16c7-f0a6-45bc-99d1-b8af2a686db4"
    encoded = gate9_report.encode_cursor(timestamp, identity)
    assert gate9_report.decode_cursor(encoded) == (timestamp, identity)
    with pytest.raises(gate9_report.ConfigurationError):
        gate9_report.decode_cursor(encoded + "!")


def test_attempt_aggregation_uses_incremental_state() -> None:
    accumulator = gate9_report._AttemptAccumulator()
    for index in range(2_500):
        accumulator.add(
            {
                "input_tokens": index,
                "output_tokens": 1,
                "total_tokens": index + 1,
                "estimated_cost": None,
            }
        )
    result = accumulator.result()
    assert result["attempt_count"] == 2_500
    assert result["known_usage_attempt_count"] == 2_500
    assert result["provider_total_tokens_known_sum"] == sum(range(1, 2_501))
    assert result["known_cost_sum"] is None
    assert not hasattr(accumulator, "rows")


def test_summary_aggregation_and_cross_batch_detail_stay_bounded() -> None:
    accumulator = gate9_report._AttemptAccumulator()
    for _ in range(2_500):
        accumulator.add_summary(
            {
                "attempt_count": 1,
                "known_usage_attempt_count": 1,
                "input_tokens_known_sum": 2,
                "output_tokens_known_sum": 3,
                "provider_total_tokens_known_sum": 5,
                "known_cost_count": 1,
                "known_cost_sum": "0.01",
            }
        )
    candidates: list[dict[str, Any]] = []
    for batch in range(2_500):
        candidates = gate9_report._merge_bounded_rows(
            candidates,
            [
                {
                    "id": f"{batch:08d}",
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC)
                    + timedelta(seconds=batch),
                }
            ],
            limit=100,
            timestamp_key="created_at",
        )
        assert len(candidates) <= 101
    result = accumulator.result()
    assert result["attempt_count"] == 2_500
    assert result["known_cost_sum"] == "25.00"
    assert candidates[0]["id"] == "00002499"


class _FakeResult:
    def scalar_one(self) -> str:
        return "on"


class _FakeTransaction:
    is_active = True

    def __init__(self, calls: list[str]):
        self.calls = calls

    def commit(self) -> None:
        self.calls.append("COMMIT")
        self.is_active = False

    def rollback(self) -> None:
        self.calls.append("ROLLBACK")
        self.is_active = False


class _FakeConnection:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def begin(self) -> _FakeTransaction:
        self.calls.append("BEGIN")
        return _FakeTransaction(self.calls)

    def exec_driver_sql(self, sql: str) -> None:
        self.calls.append(sql)

    def execute(self, _statement: object) -> _FakeResult:
        self.calls.append("VERIFY_READ_ONLY")
        return _FakeResult()

    def close(self) -> None:
        self.calls.append("CLOSE")


class _FakeEngine:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def connect(self) -> _FakeConnection:
        self.calls.append("CONNECT")
        return _FakeConnection(self.calls)

    def dispose(self) -> None:
        self.calls.append("DISPOSE")


def test_reporting_transaction_establishes_all_guards_before_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate9_report, "create_engine", lambda *_a, **_k: _FakeEngine(calls))
    with gate9_report.ReadOnlyDatabase("postgresql://explicit"):
        calls.append("REPORT_QUERY")
    assert calls == [
        "CONNECT",
        "BEGIN",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL idle_in_transaction_session_timeout = '30s'",
        "VERIFY_READ_ONLY",
        "REPORT_QUERY",
        "COMMIT",
        "CLOSE",
        "DISPOSE",
    ]


def test_reporting_guard_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FailingConnection(_FakeConnection):
        def exec_driver_sql(self, sql: str) -> None:
            super().exec_driver_sql(sql)
            if sql == "SET TRANSACTION READ ONLY":
                raise RuntimeError("unsafe connection details")

    class FailingEngine(_FakeEngine):
        def connect(self) -> FailingConnection:
            self.calls.append("CONNECT")
            return FailingConnection(self.calls)

    monkeypatch.setattr(gate9_report, "create_engine", lambda *_a, **_k: FailingEngine(calls))
    with pytest.raises(gate9_report.OperationalError):
        with gate9_report.ReadOnlyDatabase("postgresql://secret"):
            pass
    assert "ROLLBACK" in calls
    assert "secret" not in " ".join(calls)


def test_reporting_never_loads_generic_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(gate9_report.PLATFORM_DSN_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://forbidden:secret@remote/prod")
    with pytest.raises(gate9_report.ConfigurationError) as exc:
        gate9_report._dsn(gate9_report.PLATFORM_DSN_ENV)
    assert gate9_report.PLATFORM_DSN_ENV in str(exc.value)
    assert "forbidden" not in str(exc.value)
    assert "secret" not in str(exc.value)


def test_loopback_resolver_accepts_all_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5432)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 5432, 0, 0)),
        ],
    )
    spec = platform_backup.parse_connection(
        "postgresql://operator:password@localhost:5432/gate9_platform_aaaaaaaaaaaa"
    )
    assert spec.resolved_addresses == frozenset({"127.0.0.1", "::1"})


def test_loopback_resolver_rejects_any_nonloopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5432)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.4", 5432)),
        ],
    )
    with pytest.raises(platform_backup.PreconditionError):
        platform_backup.parse_connection(
            "postgresql://operator:password@localhost:5432/gate9_platform_aaaaaaaaaaaa"
        )


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql:///database",
        "postgresql://user@localhost/database",
        "postgresql://user@localhost:5432/database?service=x",
        "postgresql://user@host1,host2:5432/database",
        "sqlite:///local.db",
    ],
)
def test_connection_configuration_fails_closed(dsn: str) -> None:
    with pytest.raises(platform_backup.PreconditionError):
        platform_backup.parse_connection(dsn)


def _write_owned_group(
    directory: Path,
    directory_id: str,
    timestamp: str,
    group_id: str,
) -> tuple[Path, Path, Path]:
    base = f"platform-{timestamp}-{group_id}"
    dump = directory / f"{base}.dump"
    checksum = directory / f"{base}.sha256"
    manifest = directory / f"{base}.manifest.json"
    dump.write_bytes(b"owned-artifact")
    digest = platform_backup._sha256(dump)
    checksum.write_text(f"{digest}  {dump.name}\n", encoding="ascii")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool_directory_id": directory_id,
                "artifact_group_id": group_id,
                "artifact_filename": dump.name,
                "checksum_filename": checksum.name,
                "artifact_size": dump.stat().st_size,
                "sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return dump, checksum, manifest


def test_retention_deletes_only_complete_owned_direct_children(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    output = root / "owned"
    output.mkdir()
    directory_id = "a" * 32
    (output / platform_backup.MARKER_NAME).write_text(
        json.dumps({"schema_version": 1, "tool_directory_id": directory_id}),
        encoding="utf-8",
    )
    old = _write_owned_group(output, directory_id, "20000101T000000Z", "b" * 32)
    unknown = output / "operator-file.log"
    unknown.write_text("preserve", encoding="utf-8")
    partial = output / f"platform-20000101T000001Z-{'c' * 32}.dump"
    partial.write_bytes(b"partial")
    deleted = platform_backup.apply_retention(output, directory_id, keep=5)
    assert deleted == 1
    assert all(not path.exists() for path in old)
    assert unknown.read_text(encoding="utf-8") == "preserve"
    assert partial.read_bytes() == b"partial"


def test_retention_refuses_linked_candidate(tmp_path: Path) -> None:
    if not hasattr(os := __import__("os"), "symlink"):
        pytest.skip("symlink unsupported")
    directory = tmp_path / "owned"
    directory.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = directory / f"platform-20000101T000000Z-{'d' * 32}.dump"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation unavailable")
    checksum = link.with_suffix(".sha256")
    checksum.write_text("x", encoding="ascii")
    manifest = directory / f"platform-20000101T000000Z-{'d' * 32}.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tool_directory_id": "a" * 32,
                "artifact_group_id": "d" * 32,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(platform_backup.PreconditionError):
        platform_backup.apply_retention(directory, "a" * 32, keep=1)


def test_backup_version_rejection_precedes_material_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    output = root / "output"
    monkeypatch.setenv(platform_backup.SOURCE_DSN_ENV, "postgresql://u:p@localhost:5432/gate9_platform_aaaaaaaaaaaa")
    monkeypatch.setenv(platform_backup.EXPECTED_DATABASE_ENV, "gate9_platform_aaaaaaaaaaaa")
    monkeypatch.setenv(platform_backup.CONFIRMATION_ENV, "gate9_platform_aaaaaaaaaaaa")
    monkeypatch.setenv(platform_backup.OUTPUT_ROOT_ENV, str(root))
    monkeypatch.setenv(platform_backup.OUTPUT_DIRECTORY_ENV, str(output))
    monkeypatch.setattr(
        platform_backup,
        "parse_connection",
        lambda _value: SimpleNamespace(database="gate9_platform_aaaaaaaaaaaa"),
    )
    monkeypatch.setattr(
        platform_backup,
        "check_client_version",
        lambda _name: (_ for _ in ()).throw(platform_backup.VersionError("invalid")),
    )
    with pytest.raises(platform_backup.VersionError):
        platform_backup.create_backup()
    assert not output.exists()


def test_restore_version_rejection_precedes_sidecar_or_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ownership = root / "ownership"
    artifact = tmp_path / "artifact.dump"
    artifact.write_bytes(b"x")
    monkeypatch.setenv(platform_restore.ADMIN_DSN_ENV, "postgresql://u:p@localhost:5432/postgres")
    monkeypatch.setenv(platform_restore.ADMIN_DATABASE_ENV, "postgres")
    monkeypatch.setenv(platform_restore.TARGET_OWNER_ENV, "postgres")
    monkeypatch.setenv(platform_restore.CONFIRMATION_ENV, platform_restore.CONFIRMATION_VALUE)
    monkeypatch.setenv(platform_restore.OWNERSHIP_ROOT_ENV, str(root))
    monkeypatch.setenv(platform_restore.OWNERSHIP_DIRECTORY_ENV, str(ownership))
    monkeypatch.setattr(platform_restore, "validate_artifact", lambda _path: {})
    monkeypatch.setattr(
        platform_restore,
        "parse_connection",
        lambda _value: SimpleNamespace(database="postgres"),
    )
    monkeypatch.setattr(
        platform_restore,
        "check_client_version",
        lambda: (_ for _ in ()).throw(platform_restore.VersionError("invalid")),
    )
    with pytest.raises(platform_restore.VersionError):
        platform_restore.restore_and_verify(artifact)
    assert not ownership.exists()


def _write_restore_artifact_group(
    directory: Path,
    *,
    marker_directory_id: object,
    manifest_directory_id: object,
    manifest_schema_version: object = 1,
) -> Path:
    group_id = "e" * 32
    base = f"platform-20260815T120000Z-{group_id}"
    artifact = directory / f"{base}.dump"
    artifact.write_bytes(b"restore-artifact")
    digest = platform_restore._sha256(artifact)
    (directory / f"{base}.sha256").write_text(
        f"{digest}  {artifact.name}\n", encoding="ascii"
    )
    (directory / platform_restore.BACKUP_MARKER_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool_directory_id": marker_directory_id,
            }
        ),
        encoding="utf-8",
    )
    (directory / f"{base}.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": manifest_schema_version,
                "tool_major_version": 1,
                "tool_directory_id": manifest_directory_id,
                "artifact_group_id": group_id,
                "artifact_filename": artifact.name,
                "checksum_filename": f"{base}.sha256",
                "artifact_size": artifact.stat().st_size,
                "sha256": digest,
                "database_name": "gate9_platform_aaaaaaaaaaaa",
            }
        ),
        encoding="utf-8",
    )
    return artifact


@pytest.mark.parametrize(
    ("marker_id", "manifest_id", "manifest_schema"),
    [
        (None, None, 1),
        ("not-hex", "not-hex", 1),
        ("a" * 32, "a" * 32, None),
        ("a" * 32, "a" * 32, 2),
    ],
)
def test_restore_rejects_incomplete_artifact_ownership_metadata(
    tmp_path: Path,
    marker_id: object,
    manifest_id: object,
    manifest_schema: object,
) -> None:
    artifact = _write_restore_artifact_group(
        tmp_path,
        marker_directory_id=marker_id,
        manifest_directory_id=manifest_id,
        manifest_schema_version=manifest_schema,
    )
    with pytest.raises(platform_restore.RestoreValidationError):
        platform_restore.validate_artifact(artifact)


def test_restore_accepts_complete_artifact_ownership_metadata(tmp_path: Path) -> None:
    directory_id = "a" * 32
    artifact = _write_restore_artifact_group(
        tmp_path,
        marker_directory_id=directory_id,
        manifest_directory_id=directory_id,
    )
    manifest = platform_restore.validate_artifact(artifact)
    assert manifest["schema_version"] == 1
    assert manifest["tool_directory_id"] == directory_id


def test_restore_preserves_sidecar_when_comment_fails_after_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ownership = root / "ownership"
    ownership.mkdir()
    artifact = tmp_path / "artifact.dump"
    artifact.write_bytes(b"x")
    monkeypatch.setenv(platform_restore.ADMIN_DSN_ENV, "postgresql://u:p@localhost:5432/postgres")
    monkeypatch.setenv(platform_restore.ADMIN_DATABASE_ENV, "postgres")
    monkeypatch.setenv(platform_restore.TARGET_OWNER_ENV, "postgres")
    monkeypatch.setenv(platform_restore.CONFIRMATION_ENV, platform_restore.CONFIRMATION_VALUE)
    monkeypatch.setenv(platform_restore.OWNERSHIP_ROOT_ENV, str(root))
    monkeypatch.setenv(platform_restore.OWNERSHIP_DIRECTORY_ENV, str(ownership))
    identity = platform_restore.ServerIdentity("127.0.0.1", 5432, "postgres", 150000)
    spec = SimpleNamespace(database="postgres")
    monkeypatch.setattr(platform_restore, "validate_artifact", lambda _path: {})
    monkeypatch.setattr(platform_restore, "parse_connection", lambda _value: spec)
    monkeypatch.setattr(platform_restore, "check_client_version", lambda: 15)
    monkeypatch.setattr(platform_restore, "inspect_server", lambda *_args: identity)
    monkeypatch.setattr(platform_restore, "validate_catalog", lambda _path: None)
    monkeypatch.setattr(platform_restore, "ensure_ownership_directory", lambda _path: "a" * 32)

    def fail_after_create(*_args: object) -> None:
        raise platform_restore.TargetCreationError(
            "comment failed", database_created=True
        )

    cleanup_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(platform_restore, "create_target", fail_after_create)

    def refuse_cleanup(*args: object) -> bool:
        cleanup_calls.append(args)
        return False

    monkeypatch.setattr(platform_restore, "cleanup_target", refuse_cleanup)

    with pytest.raises(platform_restore.TargetCreationError):
        platform_restore.restore_and_verify(artifact)

    sidecars = list(ownership.glob("restore-*.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text(encoding="utf-8"))[
        "ownership_established"
    ] is False
    assert len(cleanup_calls) == 1
    warning = capsys.readouterr().err
    assert "SANITIZED_MANUAL_CLEANUP_REQUIRED" in warning
    assert "postgresql://" not in warning


def test_cleanup_refuses_sidecar_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(
        json.dumps(
            {
                "invocation_id": "wrong",
                "target_database": "gate9_restore_" + "a" * 32,
                "target_owner": "postgres",
                "server_address": "127.0.0.1",
                "server_port": 5432,
                "ownership_established": True,
            }
        ),
        encoding="utf-8",
    )
    identity = platform_restore.ServerIdentity("127.0.0.1", 5432, "postgres", 150000)
    spec = SimpleNamespace(database="postgres")
    monkeypatch.setattr(platform_restore, "inspect_server", lambda *_a: identity)
    assert not platform_restore.cleanup_target(
        spec,
        identity,
        "gate9_restore_" + "a" * 32,
        "postgres",
        "expected",
        sidecar,
    )


def test_sanitized_main_error_does_not_emit_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(platform_backup.SOURCE_DSN_ENV, "postgresql://user:TOPSECRET@remote/prod")
    result = platform_backup.main([])
    captured = capsys.readouterr()
    assert result == 2
    assert "TOPSECRET" not in captured.out + captured.err
    assert "postgresql://" not in captured.out + captured.err
