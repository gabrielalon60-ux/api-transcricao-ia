from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC = ROOT / "apps" / "transcription" / "alembic"
VERSIONS = ALEMBIC / "versions"
PLATFORM_ENV = ROOT / "packages" / "db" / "alembic" / "env.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dedicated_alembic_env_configures_transcription_version_table() -> None:
    source = read(ALEMBIC / "env.py")
    assert 'VERSION_TABLE = "alembic_version_transcription"' in source
    assert source.count("version_table=VERSION_TABLE") == 2
    assert "OWNED_TABLES" in source
    assert "applications" in source
    assert "usage_logs" in source


def test_platform_alembic_env_still_uses_platform_version_table() -> None:
    source = read(PLATFORM_ENV)
    assert "alembic_version_transcription" not in source


def test_revision_chain_is_canonical_and_independent_from_platform() -> None:
    gate3 = ast.parse(read(VERSIONS / "gate3_schema.py"))
    assigns = {}
    for node in gate3.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns[node.target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = ast.literal_eval(node.value)
    assert assigns["revision"] == "gate3_schema"
    assert assigns["down_revision"] == "transcription_1_0_baseline"
    assert "31b9b65431a4" not in read(VERSIONS / "gate3_schema.py")
    assert "ca05fc68c4bf" not in read(VERSIONS / "gate3_schema.py")


def test_baseline_reconstructs_historical_v1_without_gate3_columns() -> None:
    source = read(VERSIONS / "transcription_1_0_baseline.py")
    assert "5fb2e485351dbd14962a44f9a4bbfd4da7ba6787" in source
    assert '"PENDING"' in source and '"PROCESSING"' in source
    assert '"COMPLETED"' in source and '"FAILED"' in source
    assert "SUCCEEDED" not in source
    assert "PERSISTENCE_FAILED" not in source
    assert '"api_key"' in source
    assert "attempt_number" not in source
    assert 'sa.UniqueConstraint("request_id")' in source
    assert "sa.Float()" in source


def test_gate3_migration_contains_required_operations_and_omissions() -> None:
    source = read(VERSIONS / "gate3_schema.py")
    assert 'ALTER TYPE requeststatus ADD VALUE \'SUCCEEDED\' BEFORE \'FAILED\'' in source
    assert 'ALTER TYPE requeststatus ADD VALUE \'PERSISTENCE_FAILED\'' in source
    assert '"application_id"' in source and "nullable=True" in source
    assert '"request_id"' not in source.split('op.add_column("requests"')[1]
    assert "correlation_id" in source and "event_id" in source
    assert "processing_started_at" in source and "last_persistence_error_at" in source
    assert "file_sha256" in source
    assert "raw" not in source.lower()
    assert "attempt_number = 1" in source
    assert "postgresql_using=\"estimated_cost::numeric(18,8)\"" in source
    assert "sa.Numeric(18, 8)" in source
    assert "uq_usage_logs_request_attempt" in source
    assert "op.create_unique_constraint" in source
    assert "op.drop_constraint" in source
    assert "server_default=None" in source
    assert "NotImplementedError" in source


def test_verifiers_are_read_only_and_reconciliation_is_explicit() -> None:
    verifier = read(ROOT / "apps" / "transcription" / "src" / "transcription" / "database" / "migrations" / "schema_verifier.py")
    reconciliation = read(ROOT / "apps" / "transcription" / "src" / "transcription" / "database" / "migrations" / "profile_b_reconciliation.py")
    assert "SET TRANSACTION READ ONLY" in verifier
    assert "op." not in verifier
    assert "from alembic" not in verifier
    assert "command." not in verifier
    assert "verify_profile_b(conn)" in reconciliation
    assert "ALTER TABLE" in reconciliation
    assert "DROP CONSTRAINT usage_logs_request_id_key" in reconciliation
    assert "ADD CONSTRAINT {USAGE_ATTEMPT_UQ}" in reconciliation


def test_post_gate3_verifier_checks_negative_requirements() -> None:
    source = read(ROOT / "apps" / "transcription" / "src" / "transcription" / "database" / "migrations" / "schema_verifier.py")
    assert "requests.request_id must be absent" in source
    assert "request-only usage uniqueness must be absent" in source
    assert "composite usage uniqueness missing" in source
    assert "NUMERIC(18,8)" in source
    assert "server default must be absent" in source
