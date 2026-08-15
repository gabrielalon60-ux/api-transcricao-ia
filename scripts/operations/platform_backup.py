from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

EXIT_SUCCESS = 0
EXIT_PRECONDITION = 2
EXIT_VERSION = 3
EXIT_OPERATIONAL = 4
EXIT_VALIDATION = 5

SOURCE_DSN_ENV = "G9_BACKUP_SOURCE_DATABASE_URL"
EXPECTED_DATABASE_ENV = "G9_BACKUP_EXPECTED_DATABASE_NAME"
CONFIRMATION_ENV = "G9_BACKUP_DISPOSABLE_CONFIRMATION"
OUTPUT_ROOT_ENV = "G9_BACKUP_OUTPUT_ROOT"
OUTPUT_DIRECTORY_ENV = "G9_BACKUP_OUTPUT_DIRECTORY"
RETENTION_COUNT_ENV = "G9_BACKUP_RETENTION_COUNT"

DATABASE_PATTERN = re.compile(r"^gate9_platform_[a-f0-9]{12,32}$")
PG_DUMP_PATTERN = re.compile(
    r"^pg_dump \(PostgreSQL\) (?P<version>[0-9]+(?:\.[0-9]+)*)(?: \([^\r\n]*\))?$"
)
PG_RESTORE_PATTERN = re.compile(
    r"^pg_restore \(PostgreSQL\) (?P<version>[0-9]+(?:\.[0-9]+)*)(?: \([^\r\n]*\))?$"
)
SERVER_VERSION_PATTERN = re.compile(r"^[0-9]{6}$")
GROUP_PATTERN = re.compile(
    r"^platform-(?P<timestamp>[0-9]{8}T[0-9]{6}Z)-(?P<group>[a-f0-9]{32})$"
)

MARKER_NAME = ".gate9-platform-backup-owner.json"
MARKER_SCHEMA_VERSION = 1
RETENTION_DEFAULT = 5
RETENTION_MAX = 5
RETENTION_AGE = timedelta(days=7)
EXPECTED_TABLES = ("events", "processing_items", "executions", "alembic_version")
TOOL_MAJOR_VERSION = 1


class Gate9BackupError(Exception):
    exit_code = EXIT_VALIDATION


class PreconditionError(Gate9BackupError):
    exit_code = EXIT_PRECONDITION


class VersionError(Gate9BackupError):
    exit_code = EXIT_VERSION


class BackupOperationalError(Gate9BackupError):
    exit_code = EXIT_OPERATIONAL


class ArtifactValidationError(Gate9BackupError):
    exit_code = EXIT_VALIDATION


@dataclass(frozen=True)
class ConnectionSpec:
    url: URL
    host: str
    port: int
    database: str
    user: str
    resolved_addresses: frozenset[str]


@dataclass(frozen=True)
class ServerIdentity:
    address: str
    port: int
    database: str
    version_num: int


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise PreconditionError(f"Missing required configuration: {name}")
    return value


def _retention_count() -> int:
    raw = os.environ.get(RETENTION_COUNT_ENV)
    if raw is None:
        return RETENTION_DEFAULT
    try:
        value = int(raw)
    except ValueError as exc:
        raise PreconditionError("Invalid backup retention count") from exc
    if value < 1 or value > RETENTION_MAX:
        raise PreconditionError("Backup retention count must be between 1 and 5")
    return value


def _is_control(character: str) -> bool:
    code = ord(character)
    return code < 32 or code == 127


def parse_client_version(
    executable: str,
    stdout: bytes,
    return_code: int,
) -> int:
    if return_code != 0:
        raise VersionError("PostgreSQL version command failed")
    try:
        decoded = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise VersionError("PostgreSQL version output decoding failed") from exc
    if decoded.endswith("\r\n"):
        line = decoded[:-2]
    elif decoded.endswith("\n"):
        line = decoded[:-1]
    else:
        line = decoded
    if not line or line[0].isspace():
        raise VersionError("PostgreSQL version output is malformed")
    if "\r" in line or "\n" in line or any(_is_control(char) for char in line):
        raise VersionError("PostgreSQL version output is malformed")
    pattern = {
        "pg_dump": PG_DUMP_PATTERN,
        "pg_restore": PG_RESTORE_PATTERN,
    }.get(executable)
    if pattern is None:
        raise VersionError("Unsupported PostgreSQL executable")
    match = pattern.fullmatch(line)
    if match is None:
        raise VersionError("PostgreSQL version output is malformed")
    major = int(match.group("version").split(".", 1)[0])
    if major != 15:
        raise VersionError("PostgreSQL client major version must be 15")
    return major


def check_client_version(executable: str) -> int:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=False,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise VersionError("Required PostgreSQL executable is unavailable") from exc
    return parse_client_version(executable, result.stdout, result.returncode)


def parse_server_version(value: Any) -> int:
    if not isinstance(value, str) or SERVER_VERSION_PATTERN.fullmatch(value) is None:
        raise VersionError("PostgreSQL server version result is malformed")
    parsed = int(value, 10)
    if not 150000 <= parsed < 160000:
        raise VersionError("PostgreSQL server major version must be 15")
    return parsed


def _resolved_loopback(host: str, port: int) -> frozenset[str]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PreconditionError("Database host could not be resolved") from exc
    addresses: set[str] = set()
    for result in results:
        address = result[4][0]
        try:
            parsed = ipaddress.ip_address(str(address).split("%", 1)[0])
        except ValueError as exc:
            raise PreconditionError("Database host resolution was ambiguous") from exc
        if not parsed.is_loopback:
            raise PreconditionError("Database host must resolve only to loopback")
        addresses.add(parsed.compressed)
    if not addresses:
        raise PreconditionError("Database host resolved to no addresses")
    return frozenset(addresses)


def parse_connection(value: str) -> ConnectionSpec:
    try:
        url = make_url(value)
    except Exception as exc:
        raise PreconditionError("Database configuration is malformed") from exc
    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        raise PreconditionError("Only explicit PostgreSQL DSNs are accepted")
    if url.query:
        raise PreconditionError("Database DSN indirection/options are prohibited")
    if not url.host or url.port is None or not url.database or not url.username:
        raise PreconditionError("Database host, port, name, and user are required")
    if any(separator in url.host for separator in (",", " ")):
        raise PreconditionError("Multi-host database DSNs are prohibited")
    addresses = _resolved_loopback(url.host, url.port)
    return ConnectionSpec(
        url=url,
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        resolved_addresses=addresses,
    )


def inspect_server(spec: ConnectionSpec) -> ServerIdentity:
    engine = create_engine(spec.url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    text(
                        """
                        SELECT inet_server_addr()::text AS address,
                               inet_server_port() AS port,
                               current_database() AS database
                        """
                    )
                ).all()
                if len(rows) != 1:
                    raise VersionError("PostgreSQL server version result is ambiguous")
                row = rows[0]
                if row.address is None or row.port is None:
                    raise PreconditionError("Unix-socket database connections are prohibited")
                address_text = str(row.address).split("%", 1)[0].split("/", 1)[0]
                parsed_address = ipaddress.ip_address(address_text)
                if not parsed_address.is_loopback:
                    raise PreconditionError("Connected PostgreSQL address is not loopback")
                if parsed_address.compressed not in spec.resolved_addresses:
                    raise PreconditionError("Connected PostgreSQL address was not resolved")
                if int(row.port) != spec.port:
                    raise PreconditionError("Connected PostgreSQL port does not match")
                version_rows = connection.execute(
                    text("SELECT current_setting('server_version_num')")
                ).all()
                if len(version_rows) != 1 or version_rows[0][0] is None:
                    raise VersionError("PostgreSQL server version result is ambiguous")
                version_num = parse_server_version(version_rows[0][0])
                identity = ServerIdentity(
                    address=parsed_address.compressed,
                    port=int(row.port),
                    database=str(row.database),
                    version_num=version_num,
                )
                transaction.commit()
                return identity
            except Exception:
                transaction.rollback()
                raise
    except Gate9BackupError:
        raise
    except Exception as exc:
        raise BackupOperationalError("PostgreSQL source inspection failed") from exc
    finally:
        engine.dispose()


def _path_is_special(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PreconditionError("Configured path could not be inspected") from exc
    if stat.S_ISLNK(details.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if reparse_flag and getattr(details, "st_file_attributes", 0) & reparse_flag:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _validate_existing_path(path: Path, *, directory: bool) -> None:
    if _path_is_special(path):
        raise PreconditionError("Linked or reparse-point paths are prohibited")
    if directory and not path.is_dir():
        raise PreconditionError("Configured directory is not a directory")
    if not directory and not path.is_file():
        raise ArtifactValidationError("Expected regular file is missing")


def _configured_paths() -> tuple[Path, Path]:
    root = Path(_required_env(OUTPUT_ROOT_ENV))
    target = Path(_required_env(OUTPUT_DIRECTORY_ENV))
    if not root.is_absolute() or not target.is_absolute():
        raise PreconditionError("Backup paths must be absolute")
    _validate_existing_path(root, directory=True)
    resolved_root = root.resolve(strict=True)
    if os.path.ismount(resolved_root):
        raise PreconditionError("Backup root mount substitutions are prohibited")
    if target.parent.resolve(strict=True) != resolved_root:
        raise PreconditionError("Backup directory must be a direct child of its root")
    if target.exists():
        _validate_existing_path(target, directory=True)
        resolved_target = target.resolve(strict=True)
    else:
        resolved_target = target.resolve(strict=False)
    if resolved_target != resolved_root / target.name:
        raise PreconditionError("Backup directory containment is ambiguous")
    return resolved_root, resolved_target


def _atomic_json(path: Path, payload: Mapping[str, Any], *, exclusive: bool) -> None:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    _validate_existing_path(path, directory=False)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError("Ownership metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ArtifactValidationError("Ownership metadata is invalid")
    return payload


def ensure_owned_directory(target: Path) -> tuple[str, Path]:
    created = False
    if not target.exists():
        try:
            target.mkdir()
            created = True
        except OSError as exc:
            raise BackupOperationalError("Backup directory could not be created") from exc
    _validate_existing_path(target, directory=True)
    marker = target / MARKER_NAME
    if created:
        directory_id = secrets.token_hex(16)
        try:
            _atomic_json(
                marker,
                {
                    "schema_version": MARKER_SCHEMA_VERSION,
                    "tool_directory_id": directory_id,
                },
                exclusive=True,
            )
        except Exception as exc:
            raise ArtifactValidationError("Backup ownership marker creation failed") from exc
    elif not marker.exists():
        raise PreconditionError("Existing backup directory is not tool-owned")
    payload = _read_json(marker)
    stored_directory_id = payload.get("tool_directory_id")
    if (
        payload.get("schema_version") != MARKER_SCHEMA_VERSION
        or not isinstance(stored_directory_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", stored_directory_id) is None
    ):
        raise ArtifactValidationError("Backup ownership marker is invalid")
    return stored_directory_id, marker


def _child_environment(spec: ConnectionSpec, database: str | None = None) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PGHOST": spec.host,
        "PGPORT": str(spec.port),
        "PGDATABASE": database or spec.database,
        "PGUSER": spec.user,
        "PGCONNECT_TIMEOUT": "5",
        "PGSSLMODE": "disable",
        "PGPASSFILE": os.devnull,
    }
    if spec.url.password is not None:
        environment["PGPASSWORD"] = spec.url.password
    # These process-launch variables are not credentials and are required for
    # deterministic executable resolution on Windows (including .cmd test
    # shims used by the disposable PostgreSQL acceptance environment).
    for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_evidence(
    spec: ConnectionSpec,
) -> tuple[dict[str, int], dict[str, dict[str, str | None]], str | None]:
    counts: dict[str, int] = {}
    identity_bounds: dict[str, dict[str, str | None]] = {}
    alembic_revision: str | None = None
    engine = create_engine(spec.url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                for table in EXPECTED_TABLES:
                    exists = connection.execute(
                        text("SELECT to_regclass(:table_name) IS NOT NULL"),
                        {"table_name": f"public.{table}"},
                    ).scalar_one()
                    if not exists:
                        raise ArtifactValidationError("Expected Platform table is missing")
                    if table != "alembic_version":
                        count = connection.exec_driver_sql(
                            f'SELECT count(*)::bigint FROM "{table}"'
                        ).scalar_one()
                        counts[table] = int(count)
                        bounds = connection.exec_driver_sql(
                            f'SELECT min(id)::text, max(id)::text FROM "{table}"'
                        ).one()
                        identity_bounds[table] = {
                            "minimum_id": bounds[0],
                            "maximum_id": bounds[1],
                        }
                alembic_revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one_or_none()
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    except Gate9BackupError:
        raise
    except Exception as exc:
        raise BackupOperationalError("Platform evidence query failed") from exc
    finally:
        engine.dispose()
    return counts, identity_bounds, alembic_revision


def _validate_catalog(path: Path) -> None:
    try:
        result = subprocess.run(
            ["pg_restore", "--list", str(path)],
            check=False,
            capture_output=True,
            text=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupOperationalError("Backup catalog validation could not run") from exc
    if result.returncode != 0:
        raise ArtifactValidationError("Backup catalog validation failed")
    try:
        catalog = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ArtifactValidationError("Backup catalog output is invalid") from exc
    if not catalog.strip():
        raise ArtifactValidationError("Backup catalog is empty")
    for table in EXPECTED_TABLES:
        if re.search(rf"\bTABLE\b.*\b{re.escape(table)}\b", catalog) is None:
            raise ArtifactValidationError("Backup catalog lacks expected Platform tables")


def _validate_direct_child(directory: Path, path: Path) -> None:
    _validate_existing_path(path, directory=False)
    if path.resolve(strict=True).parent != directory.resolve(strict=True):
        raise ArtifactValidationError("Artifact containment validation failed")


def apply_retention(directory: Path, directory_id: str, keep: int) -> int:
    now = datetime.now(UTC)
    groups: list[tuple[datetime, tuple[Path, Path, Path]]] = []
    for manifest_path in directory.glob("platform-*.manifest.json"):
        _validate_direct_child(directory, manifest_path)
        manifest = _read_json(manifest_path)
        if manifest.get("tool_directory_id") != directory_id:
            continue
        group_id = manifest.get("artifact_group_id")
        if not isinstance(group_id, str):
            continue
        base = manifest_path.name.removesuffix(".manifest.json")
        match = GROUP_PATTERN.fullmatch(base)
        if match is None or match.group("group") != group_id:
            continue
        dump_path = directory / f"{base}.dump"
        checksum_path = directory / f"{base}.sha256"
        if not dump_path.exists() or not checksum_path.exists():
            continue
        _validate_direct_child(directory, dump_path)
        _validate_direct_child(directory, checksum_path)
        digest = manifest.get("sha256")
        if (
            manifest.get("artifact_filename") != dump_path.name
            or manifest.get("checksum_filename") != checksum_path.name
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or manifest.get("artifact_size") != dump_path.stat().st_size
        ):
            continue
        try:
            checksum_text = checksum_path.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            continue
        if checksum_text != f"{digest}  {dump_path.name}\n" or _sha256(dump_path) != digest:
            continue
        try:
            created_at = datetime.strptime(
                match.group("timestamp"), "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=UTC)
        except ValueError:
            continue
        groups.append((created_at, (dump_path, checksum_path, manifest_path)))
    groups.sort(key=lambda item: (item[0], item[1][0].name), reverse=True)
    deleted = 0
    for index, (created_at, files) in enumerate(groups):
        if index < keep and now - created_at <= RETENTION_AGE:
            continue
        for path in files:
            _validate_direct_child(directory, path)
        for path in files:
            path.unlink()
        deleted += 1
    return deleted


def create_backup() -> dict[str, Any]:
    source_value = _required_env(SOURCE_DSN_ENV)
    expected_database = _required_env(EXPECTED_DATABASE_ENV)
    confirmation = _required_env(CONFIRMATION_ENV)
    retention_count = _retention_count()
    _, output_directory = _configured_paths()

    if expected_database != confirmation:
        raise PreconditionError("Disposable backup confirmation does not match")
    if expected_database in {"postgres", "template0", "template1"}:
        raise PreconditionError("System databases cannot be backed up by Gate 9")
    if DATABASE_PATTERN.fullmatch(expected_database) is None:
        raise PreconditionError("Backup source name is not Gate 9 disposable")

    spec = parse_connection(source_value)
    if spec.database != expected_database:
        raise PreconditionError("Configured backup database identity does not match")

    dump_major = check_client_version("pg_dump")
    restore_major = check_client_version("pg_restore")
    identity = inspect_server(spec)
    if identity.database != expected_database:
        raise PreconditionError("Connected backup database identity does not match")
    counts, identity_bounds, alembic_revision = _source_evidence(spec)

    directory_id, _ = ensure_owned_directory(output_directory)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    group_id = secrets.token_hex(16)
    base = f"platform-{timestamp}-{group_id}"
    dump_path = output_directory / f"{base}.dump"
    checksum_path = output_directory / f"{base}.sha256"
    manifest_path = output_directory / f"{base}.manifest.json"
    created_paths: list[Path] = []
    try:
        result = subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(dump_path),
            ],
            check=False,
            capture_output=True,
            text=False,
            timeout=300,
            env=_child_environment(spec),
        )
        created_paths.append(dump_path)
        if result.returncode != 0:
            raise BackupOperationalError("PostgreSQL backup subprocess failed")
        _validate_direct_child(output_directory, dump_path)
        if dump_path.stat().st_size <= 0:
            raise ArtifactValidationError("Backup artifact is empty")
        _validate_catalog(dump_path)
        digest = _sha256(dump_path)
        checksum_path.write_text(f"{digest}  {dump_path.name}\n", encoding="ascii")
        created_paths.append(checksum_path)
        manifest = {
            "schema_version": 1,
            "tool_major_version": TOOL_MAJOR_VERSION,
            "tool_directory_id": directory_id,
            "artifact_group_id": group_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "database_name": expected_database,
            "artifact_filename": dump_path.name,
            "checksum_filename": checksum_path.name,
            "artifact_size": dump_path.stat().st_size,
            "sha256": digest,
            "pg_dump_major": dump_major,
            "pg_restore_major": restore_major,
            "server_version_num": identity.version_num,
            "alembic_version": alembic_revision,
            "table_counts": counts,
            "table_identity_bounds": identity_bounds,
        }
        _atomic_json(manifest_path, manifest, exclusive=True)
        created_paths.append(manifest_path)
        _validate_direct_child(output_directory, checksum_path)
        _validate_direct_child(output_directory, manifest_path)
        deleted_groups = apply_retention(
            output_directory, directory_id, retention_count
        )
        return {
            "status": "SUCCESS",
            "artifact": dump_path.name,
            "checksum": checksum_path.name,
            "manifest": manifest_path.name,
            "sha256": digest,
            "artifact_size": dump_path.stat().st_size,
            "retention_deleted_groups": deleted_groups,
        }
    except Exception:
        for path in reversed(created_paths):
            try:
                if path.exists() and path.parent.resolve() == output_directory.resolve():
                    path.unlink()
            except OSError:
                pass
        raise


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Gate 9 disposable Platform PostgreSQL backup"
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        build_parser().parse_args(argv)
        print(json.dumps(create_backup(), sort_keys=True))
        return EXIT_SUCCESS
    except Gate9BackupError as exc:
        print(
            json.dumps({"error": exc.__class__.__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return exc.exit_code
    except Exception:
        print(
            json.dumps(
                {
                    "error": "ArtifactValidationError",
                    "message": "Gate 9 backup failed safely",
                }
            ),
            file=sys.stderr,
        )
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
