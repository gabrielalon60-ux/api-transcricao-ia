from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_VERSIONS = ROOT / "packages" / "db" / "alembic" / "versions"
GATE4_MIGRATION = PLATFORM_VERSIONS / "7a8f9c1b2d3e_gate4_persistent_queue_models.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gate4_migration_source_chain_and_down_revision() -> None:
    source = read(GATE4_MIGRATION)
    tree = ast.parse(source)
    assigns = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns[node.target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = ast.literal_eval(node.value)

    assert assigns["revision"] == "7a8f9c1b2d3e"
    assert assigns["down_revision"] == "31b9b65431a4"


def test_gate4_migration_source_contains_all_approved_tables_and_indexes() -> None:
    source = read(GATE4_MIGRATION)

    # Table creations
    assert '"conversation_queue_counters"' in source
    assert '"processing_items"' in source
    assert '"executions"' in source
    assert '"service_usage"' in source

    # Physical partial unique 1-active index
    assert "uq_processing_items_one_active_per_conversation" in source
    assert "status IN ('ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN')" in source

    # Constraints
    assert "uq_processing_items_conversation_sequence" in source
    assert "ck_conv_queue_counters_sequence_non_negative" in source
    assert "ck_processing_items_sequence_positive" in source
    assert "ck_processing_items_file_size_non_negative" in source
    assert "ck_processing_items_attempt_count_non_negative" in source
    assert "ck_processing_items_status_valid" in source
    assert "ck_executions_status_valid" in source
    assert "ck_executions_component_valid" in source
    assert "ck_executions_effect_status_valid" in source
    assert "uq_executions_outbound_msg" in source
    assert "uq_service_usage_source_attempt" in source
    assert "ck_service_usage_source_attempt_positive" in source


def test_gate4_migration_does_not_contaminate_transcription_environment() -> None:
    source = read(GATE4_MIGRATION)
    assert "alembic_version_transcription" not in source
    assert '"applications"' not in source
    assert '"usage_logs"' not in source


def test_platform_models_contain_gate4_entities() -> None:
    from db.models import (
        ConversationQueueCounter,
        ProcessingItem,
        Execution,
        ServiceUsage,
    )

    assert ConversationQueueCounter.__tablename__ == "conversation_queue_counters"
    assert ProcessingItem.__tablename__ == "processing_items"
    assert Execution.__tablename__ == "executions"
    assert ServiceUsage.__tablename__ == "service_usage"
