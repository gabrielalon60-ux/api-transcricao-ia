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

ADMIN_DSN_ENV = "G9_RESTORE_ADMIN_DATABASE_URL"
ADMIN_DATABASE_ENV = "G9_RESTORE_ADMIN_DATABASE_NAME"
TARGET_OWNER_ENV = "G9_RESTORE_TARGET_OWNER"
CONFIRMATION_ENV = "G9_RESTORE_DISPOSABLE_CONFIRMATION"
OWNERSHIP_ROOT_ENV = "G9_RESTORE_OWNERSHIP_ROOT"
OWNERSHIP_DIRECTORY_ENV = "G9_RESTORE_OWNERSHIP_DIRECTORY"
CONFIRMATION_VALUE = "GATE9_DISPOSABLE_RESTORE"

PG_RESTORE_PATTERN = re.compile(
    r"^pg_restore \(PostgreSQL\) (?P<version>[0-9]+(?:\.[0-9]+)*)(?: \([^\r\n]*\))?$"
)
SERVER_VERSION_PATTERN = re.compile(r"^[0-9]{6}$")
SOURCE_DATABASE_PATTERN = re.compile(r"^gate9_platform_[a-f0-9]{12,32}$")
TARGET_DATABASE_PATTERN = re.compile(r"^gate9_restore_[a-f0-9]{32}$")
ROLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
GROUP_PATTERN = re.compile(
    r"^platform-(?P<timestamp>[0-9]{8}T[0-9]{6}Z)-(?P<group>[a-f0-9]{32})$"
)

BACKUP_MARKER_NAME = ".gate9-platform-backup-owner.json"
RESTORE_MARKER_NAME = ".gate9-platform-restore-owner.json"
MARKER_SCHEMA_VERSION = 1
EXPECTED_TABLES = ("events", "processing_items", "executions", "alembic_version")
TOOL_MAJOR_VERSION = 1


class Gate9RestoreError(Exception):
    exit_code = EXIT_VALIDATION


class PreconditionError(Gate9RestoreError):
    exit_code = EXIT_PRECONDITION


class VersionError(Gate9RestoreError):
    exit_code = EXIT_VERSION


class RestoreOperationalError(Gate9RestoreError):
    exit_code = EXIT_OPERATIONAL


class TargetCreationError(RestoreOperationalError):
    def __init__(self, message: str, *, database_created: bool):
        super().__init__(message)
        self.database_created = database_created


class RestoreValidationError(Gate9RestoreError):
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


def _is_control(character: str) -> bool:
    code = ord(character)
    return code < 32 or code == 127


def parse_client_version(stdout: bytes, return_code: int) -> int:
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
    match = PG_RESTORE_PATTERN.fullmatch(line)
    if match is None:
        raise VersionError("PostgreSQL version output is malformed")
    major = int(match.group("version").split(".", 1)[0])
    if major != 15:
        raise VersionError("PostgreSQL client major version must be 15")
    return major


def check_client_version() -> int:
    try:
        result = subprocess.run(
            ["pg_restore", "--version"],
            check=False,
            capture_output=True,
            text=False,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise VersionError("Required PostgreSQL executable is unavailable") from exc
    return parse_client_version(result.stdout, result.returncode)


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
    return ConnectionSpec(
        url=url,
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        resolved_addresses=_resolved_loopback(url.host, url.port),
    )


def inspect_server(spec: ConnectionSpec, expected_database: str) -> ServerIdentity:
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
                address = ipaddress.ip_address(address_text)
                if not address.is_loopback or address.compressed not in spec.resolved_addresses:
                    raise PreconditionError("Connected PostgreSQL address is unsafe")
                if int(row.port) != spec.port:
                    raise PreconditionError("Connected PostgreSQL port does not match")
                if str(row.database) != expected_database:
                    raise PreconditionError("Connected administrative database does not match")
                version_rows = connection.execute(
                    text("SELECT current_setting('server_version_num')")
                ).all()
                if len(version_rows) != 1 or version_rows[0][0] is None:
                    raise VersionError("PostgreSQL server version result is ambiguous")
                version_num = parse_server_version(version_rows[0][0])
                identity = ServerIdentity(
                    address=address.compressed,
                    port=int(row.port),
                    database=str(row.database),
                    version_num=version_num,
                )
                transaction.commit()
                return identity
            except Exception:
                transaction.rollback()
                raise
    except Gate9RestoreError:
        raise
    except Exception as exc:
        raise RestoreOperationalError("PostgreSQL administrative inspection failed") from exc
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
        raise RestoreValidationError("Expected regular file is missing")


def _configured_ownership_paths() -> tuple[Path, Path]:
    root = Path(_required_env(OWNERSHIP_ROOT_ENV))
    target = Path(_required_env(OWNERSHIP_DIRECTORY_ENV))
    if not root.is_absolute() or not target.is_absolute():
        raise PreconditionError("Restore ownership paths must be absolute")
    _validate_existing_path(root, directory=True)
    resolved_root = root.resolve(strict=True)
    if os.path.ismount(resolved_root):
        raise PreconditionError("Restore root mount substitutions are prohibited")
    if target.parent.resolve(strict=True) != resolved_root:
        raise PreconditionError("Restore ownership directory must be a direct child")
    if target.exists():
        _validate_existing_path(target, directory=True)
        resolved_target = target.resolve(strict=True)
    else:
        resolved_target = target.resolve(strict=False)
    if resolved_target != resolved_root / target.name:
        raise PreconditionError("Restore ownership containment is ambiguous")
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
        raise RestoreValidationError("Artifact metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise RestoreValidationError("Artifact metadata is invalid")
    return payload


def ensure_ownership_directory(target: Path) -> str:
    created = False
    if not target.exists():
        try:
            target.mkdir()
            created = True
        except OSError as exc:
            raise RestoreOperationalError("Restore ownership directory creation failed") from exc
    _validate_existing_path(target, directory=True)
    marker = target / RESTORE_MARKER_NAME
    if created:
        directory_id = secrets.token_hex(16)
        _atomic_json(
            marker,
            {"schema_version": MARKER_SCHEMA_VERSION, "tool_directory_id": directory_id},
            exclusive=True,
        )
    elif not marker.exists():
        raise PreconditionError("Existing restore directory is not tool-owned")
    payload = _read_json(marker)
    stored_directory_id = payload.get("tool_directory_id")
    if (
        payload.get("schema_version") != MARKER_SCHEMA_VERSION
        or not isinstance(stored_directory_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", stored_directory_id) is None
    ):
        raise RestoreValidationError("Restore ownership marker is invalid")
    return stored_directory_id


def _validate_direct_child(directory: Path, path: Path) -> None:
    _validate_existing_path(path, directory=False)
    if path.resolve(strict=True).parent != directory.resolve(strict=True):
        raise RestoreValidationError("Artifact containment validation failed")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact(artifact: Path) -> dict[str, Any]:
    if not artifact.is_absolute():
        raise PreconditionError("Restore artifact path must be absolute")
    _validate_existing_path(artifact, directory=False)
    directory = artifact.parent
    _validate_existing_path(directory, directory=True)
    _validate_direct_child(directory, artifact)
    base = artifact.name.removesuffix(".dump")
    match = GROUP_PATTERN.fullmatch(base)
    if match is None or artifact.name == base:
        raise RestoreValidationError("Restore artifact filename is invalid")
    checksum = directory / f"{base}.sha256"
    manifest_path = directory / f"{base}.manifest.json"
    backup_marker = directory / BACKUP_MARKER_NAME
    for path in (checksum, manifest_path, backup_marker):
        _validate_direct_child(directory, path)
    marker = _read_json(backup_marker)
    manifest = _read_json(manifest_path)
    directory_id = marker.get("tool_directory_id")
    if (
        marker.get("schema_version") != MARKER_SCHEMA_VERSION
        or manifest.get("schema_version") != MARKER_SCHEMA_VERSION
        or not isinstance(directory_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", directory_id) is None
        or manifest.get("tool_directory_id") != directory_id
        or manifest.get("tool_major_version") != TOOL_MAJOR_VERSION
        or manifest.get("artifact_group_id") != match.group("group")
        or manifest.get("artifact_filename") != artifact.name
        or manifest.get("checksum_filename") != checksum.name
    ):
        raise RestoreValidationError("Restore artifact ownership is invalid")
    database_name = manifest.get("database_name")
    if not isinstance(database_name, str) or SOURCE_DATABASE_PATTERN.fullmatch(database_name) is None:
        raise RestoreValidationError("Restore source identity is invalid")
    try:
        checksum_text = checksum.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise RestoreValidationError("Restore checksum is invalid") from exc
    expected_line = f"{manifest.get('sha256')}  {artifact.name}\n"
    if checksum_text != expected_line or _sha256(artifact) != manifest.get("sha256"):
        raise RestoreValidationError("Restore checksum does not match")
    if artifact.stat().st_size <= 0 or artifact.stat().st_size != manifest.get("artifact_size"):
        raise RestoreValidationError("Restore artifact size is invalid")
    return manifest


def validate_catalog(artifact: Path) -> None:
    try:
        result = subprocess.run(
            ["pg_restore", "--list", str(artifact)],
            check=False,
            capture_output=True,
            text=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestoreOperationalError("Restore catalog validation could not run") from exc
    if result.returncode != 0:
        raise RestoreValidationError("Restore catalog validation failed")
    try:
        catalog = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RestoreValidationError("Restore catalog output is invalid") from exc
    if not catalog.strip():
        raise RestoreValidationError("Restore catalog is empty")
    for table in EXPECTED_TABLES:
        if re.search(rf"\bTABLE\b.*\b{re.escape(table)}\b", catalog) is None:
            raise RestoreValidationError("Restore catalog lacks expected Platform tables")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _child_environment(spec: ConnectionSpec, database: str) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PGHOST": spec.host,
        "PGPORT": str(spec.port),
        "PGDATABASE": database,
        "PGUSER": spec.user,
        "PGCONNECT_TIMEOUT": "5",
        "PGSSLMODE": "disable",
        "PGPASSFILE": os.devnull,
    }
    if spec.url.password is not None:
        environment["PGPASSWORD"] = spec.url.password
    for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def _admin_engine(spec: ConnectionSpec):
    return create_engine(spec.url, poolclass=NullPool, isolation_level="AUTOCOMMIT")


def create_target(
    spec: ConnectionSpec,
    target_name: str,
    target_owner: str,
    invocation_id: str,
) -> None:
    if TARGET_DATABASE_PATTERN.fullmatch(target_name) is None:
        raise PreconditionError("Generated restore target identity is invalid")
    if ROLE_PATTERN.fullmatch(target_owner) is None:
        raise PreconditionError("Restore target owner is invalid")
    engine = _admin_engine(spec)
    quoted_target = _quote_identifier(target_name)
    quoted_owner = _quote_identifier(target_owner)
    database_created = False
    try:
        with engine.connect() as connection:
            if connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": target_name},
            ).scalar_one():
                raise PreconditionError("Generated restore target already exists")
            if not connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:owner)"),
                {"owner": target_owner},
            ).scalar_one():
                raise PreconditionError("Restore target owner does not exist")
            connection.exec_driver_sql(
                f"CREATE DATABASE {quoted_target} OWNER {quoted_owner}"
            )
            database_created = True
            connection.exec_driver_sql(
                f"COMMENT ON DATABASE {quoted_target} IS 'gate9:{invocation_id}'"
            )
    except Gate9RestoreError:
        raise
    except Exception as exc:
        raise TargetCreationError(
            "Disposable restore target creation failed",
            database_created=database_created,
        ) from exc
    finally:
        engine.dispose()


def verify_target_ownership(
    spec: ConnectionSpec,
    target_name: str,
    target_owner: str,
    invocation_id: str,
) -> bool:
    engine = _admin_engine(spec)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT d.datname, r.rolname AS owner,
                           shobj_description(d.oid, 'pg_database') AS marker
                    FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
                    WHERE d.datname = :name
                    """
                ),
                {"name": target_name},
            ).all()
        return (
            len(rows) == 1
            and rows[0].datname == target_name
            and rows[0].owner == target_owner
            and rows[0].marker == f"gate9:{invocation_id}"
        )
    except Exception as exc:
        raise RestoreOperationalError("Restore ownership verification failed") from exc
    finally:
        engine.dispose()


def _read_sidecar(path: Path) -> dict[str, Any]:
    return _read_json(path)


def cleanup_target(
    spec: ConnectionSpec,
    identity: ServerIdentity,
    target_name: str,
    target_owner: str,
    invocation_id: str,
    sidecar: Path,
) -> bool:
    try:
        current_identity = inspect_server(spec, spec.database)
        if current_identity != identity:
            return False
        sidecar_payload = _read_sidecar(sidecar)
        if (
            sidecar_payload.get("invocation_id") != invocation_id
            or sidecar_payload.get("target_database") != target_name
            or sidecar_payload.get("target_owner") != target_owner
            or sidecar_payload.get("server_address") != identity.address
            or sidecar_payload.get("server_port") != identity.port
            or sidecar_payload.get("ownership_established") is not True
        ):
            return False
        if not verify_target_ownership(spec, target_name, target_owner, invocation_id):
            return False
        engine = _admin_engine(spec)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    f"DROP DATABASE {_quote_identifier(target_name)}"
                )
        finally:
            engine.dispose()
        return True
    except Gate9RestoreError:
        return False
    except Exception:
        return False


def _validate_empty_target(spec: ConnectionSpec, target_name: str) -> None:
    target_url = spec.url.set(database=target_name)
    engine = create_engine(target_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                count = connection.execute(
                    text(
                        """
                        SELECT count(*) FROM pg_tables
                        WHERE schemaname NOT IN ('pg_catalog','information_schema')
                        """
                    )
                ).scalar_one()
                if int(count) != 0:
                    raise PreconditionError("Restore target is not empty")
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    except Gate9RestoreError:
        raise
    except Exception as exc:
        raise RestoreOperationalError("Restore target emptiness check failed") from exc
    finally:
        engine.dispose()


def _validate_restored_target(
    spec: ConnectionSpec,
    target_name: str,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    target_url = spec.url.set(database=target_name)
    engine = create_engine(target_url, poolclass=NullPool)
    restored_counts: dict[str, int] = {}
    restored_identity_bounds: dict[str, dict[str, str | None]] = {}
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
                        raise RestoreValidationError("Restored Platform table is missing")
                    if table != "alembic_version":
                        restored_counts[table] = int(
                            connection.exec_driver_sql(
                                f'SELECT count(*)::bigint FROM "{table}"'
                            ).scalar_one()
                        )
                        bounds = connection.exec_driver_sql(
                            f'SELECT min(id)::text, max(id)::text FROM "{table}"'
                        ).one()
                        restored_identity_bounds[table] = {
                            "minimum_id": bounds[0],
                            "maximum_id": bounds[1],
                        }
                revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one_or_none()
                constraint_count = int(
                    connection.execute(
                        text(
                            """
                            SELECT count(*) FROM pg_constraint c
                            JOIN pg_namespace n ON n.oid=c.connamespace
                            WHERE n.nspname='public'
                            """
                        )
                    ).scalar_one()
                )
                index_count = int(
                    connection.execute(
                        text("SELECT count(*) FROM pg_indexes WHERE schemaname='public'")
                    ).scalar_one()
                )
                operation_identity_indexes = int(
                    connection.execute(
                        text(
                            """
                            SELECT count(*) FROM pg_indexes
                            WHERE schemaname='public' AND tablename='executions'
                              AND indexname IN ('uq_executions_outbound_msg',
                                                'uq_executions_operation_idempotency_key')
                            """
                        )
                    ).scalar_one()
                )
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    except Gate9RestoreError:
        raise
    except Exception as exc:
        raise RestoreOperationalError("Restored Platform validation query failed") from exc
    finally:
        engine.dispose()
    expected_counts = manifest.get("table_counts")
    if not isinstance(expected_counts, dict) or restored_counts != expected_counts:
        raise RestoreValidationError("Restored Platform table counts do not match")
    if restored_identity_bounds != manifest.get("table_identity_bounds"):
        raise RestoreValidationError("Restored Platform data identities do not match")
    if revision != manifest.get("alembic_version"):
        raise RestoreValidationError("Restored Alembic revision does not match")
    if constraint_count <= 0 or index_count <= 0 or operation_identity_indexes != 2:
        raise RestoreValidationError("Restored constraints or indexes are incomplete")
    return {
        "table_counts": restored_counts,
        "table_identity_bounds": restored_identity_bounds,
        "alembic_version": revision,
        "constraint_count": constraint_count,
        "index_count": index_count,
        "operation_identity_indexes": operation_identity_indexes,
    }


def restore_and_verify(artifact: Path) -> dict[str, Any]:
    admin_value = _required_env(ADMIN_DSN_ENV)
    admin_database = _required_env(ADMIN_DATABASE_ENV)
    target_owner = _required_env(TARGET_OWNER_ENV)
    confirmation = _required_env(CONFIRMATION_ENV)
    if confirmation != CONFIRMATION_VALUE:
        raise PreconditionError("Disposable restore confirmation does not match")
    if ROLE_PATTERN.fullmatch(target_owner) is None:
        raise PreconditionError("Restore target owner is invalid")
    _, ownership_directory = _configured_ownership_paths()
    manifest = validate_artifact(artifact)
    spec = parse_connection(admin_value)
    if spec.database != admin_database:
        raise PreconditionError("Administrative database configuration does not match")

    restore_major = check_client_version()
    identity = inspect_server(spec, admin_database)
    validate_catalog(artifact)

    directory_id = ensure_ownership_directory(ownership_directory)
    invocation_id = secrets.token_hex(16)
    target_name = f"gate9_restore_{secrets.token_hex(16)}"
    sidecar = ownership_directory / f"restore-{invocation_id}.json"
    sidecar_payload = {
        "schema_version": 1,
        "tool_directory_id": directory_id,
        "invocation_id": invocation_id,
        "target_database": target_name,
        "target_owner": target_owner,
        "server_address": identity.address,
        "server_port": identity.port,
        "ownership_established": False,
    }
    _atomic_json(sidecar, sidecar_payload, exclusive=True)
    target_created = False
    cleaned = False
    try:
        try:
            create_target(spec, target_name, target_owner, invocation_id)
            target_created = True
        except TargetCreationError as exc:
            target_created = exc.database_created
            raise
        if not verify_target_ownership(spec, target_name, target_owner, invocation_id):
            raise RestoreValidationError("Restore invocation ownership was not established")
        sidecar_payload["ownership_established"] = True
        _atomic_json(sidecar, sidecar_payload, exclusive=False)
        _validate_empty_target(spec, target_name)
        result = subprocess.run(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                target_name,
                str(artifact),
            ],
            check=False,
            capture_output=True,
            text=False,
            timeout=300,
            env=_child_environment(spec, target_name),
        )
        if result.returncode != 0:
            raise RestoreOperationalError("PostgreSQL restore subprocess failed")
        restored = _validate_restored_target(spec, target_name, manifest)
        cleaned = cleanup_target(
            spec, identity, target_name, target_owner, invocation_id, sidecar
        )
        if not cleaned:
            raise RestoreValidationError(
                "Restore cleanup ownership revalidation failed; manual cleanup required"
            )
        sidecar.unlink()
        return {
            "status": "SUCCESS",
            "target_database": target_name,
            "invocation_id": invocation_id,
            "pg_restore_major": restore_major,
            "server_version_num": identity.version_num,
            "validation": restored,
            "cleanup_completed": True,
        }
    except Exception:
        if target_created and not cleaned:
            cleaned = cleanup_target(
                spec, identity, target_name, target_owner, invocation_id, sidecar
            )
        if cleaned:
            sidecar.unlink(missing_ok=True)
        elif target_created:
            print(
                json.dumps(
                    {
                        "warning": "SANITIZED_MANUAL_CLEANUP_REQUIRED",
                        "target_database": target_name,
                        "invocation_id": invocation_id,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            sidecar.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gate 9 disposable Platform PostgreSQL restore verification"
    )
    parser.add_argument("--artifact", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        artifact = Path(args.artifact)
        print(json.dumps(restore_and_verify(artifact), sort_keys=True))
        return EXIT_SUCCESS
    except Gate9RestoreError as exc:
        print(
            json.dumps({"error": exc.__class__.__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return exc.exit_code
    except Exception:
        print(
            json.dumps(
                {
                    "error": "RestoreValidationError",
                    "message": "Gate 9 restore failed safely",
                }
            ),
            file=sys.stderr,
        )
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
