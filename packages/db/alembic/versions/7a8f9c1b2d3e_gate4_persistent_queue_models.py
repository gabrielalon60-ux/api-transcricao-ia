"""gate4_persistent_queue_models

Revision ID: 7a8f9c1b2d3e
Revises: 31b9b65431a4
Create Date: 2026-08-04 23:25:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7a8f9c1b2d3e"
down_revision: Union[str, Sequence[str], None] = "31b9b65431a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create conversation_queue_counters table
    op.create_table(
        "conversation_queue_counters",
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("organization_id", "instance_id", "user_id"),
        sa.CheckConstraint(
            "last_sequence >= 0", name="ck_conv_queue_counters_sequence_non_negative"
        ),
    )

    # 2. Create processing_items table
    op.create_table(
        "processing_items",
        sa.Column(
            "id",
            sa.String(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="RECEIVED"),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_mime_type", sa.String(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("document_type", sa.String(), nullable=True),
        sa.Column("raw_extraction", sa.JSON(), nullable=True),
        sa.Column("normalized_data", sa.JSON(), nullable=True),
        sa.Column("quality_flags", sa.JSON(), nullable=True),
        sa.Column("confidence_data", sa.JSON(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("document_date", sa.String(), nullable=True),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_source", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("question_type", sa.String(), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("writer_idempotency_key", sa.String(), nullable=True),
        sa.Column("external_operation_status", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message_sanitized", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("writer_idempotency_key"),
        sa.UniqueConstraint(
            "organization_id",
            "instance_id",
            "user_id",
            "sequence",
            name="uq_processing_items_conversation_sequence",
        ),
        sa.CheckConstraint(
            "sequence IS NULL OR sequence > 0",
            name="ck_processing_items_sequence_positive",
        ),
        sa.CheckConstraint(
            "file_size >= 0", name="ck_processing_items_file_size_non_negative"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_processing_items_attempt_count_non_negative"
        ),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'EXTRACTING', 'EXTRACTED', 'READY', 'ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN', 'COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED')",
            name="ck_processing_items_status_valid",
        ),
    )

    # 3. Create partial unique index and secondary indexes for processing_items
    op.create_index(
        "uq_processing_items_one_active_per_conversation",
        "processing_items",
        ["organization_id", "instance_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN')"
        ),
    )
    op.create_index(
        "ix_processing_items_fifo_lookup",
        "processing_items",
        ["organization_id", "instance_id", "user_id", "status", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_processing_items_capacity_check",
        "processing_items",
        ["organization_id", "instance_id", "user_id"],
        unique=False,
        postgresql_where=sa.text(
            "status NOT IN ('COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED')"
        ),
    )
    op.create_index(
        "ix_processing_items_expiration",
        "processing_items",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'WAITING_USER_INPUT'"),
    )
    op.create_index(
        "ix_processing_items_lease_recovery",
        "processing_items",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('ACTIVE', 'VALIDATING', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN')"
        ),
    )

    # 4. Create executions table
    op.create_table(
        "executions",
        sa.Column(
            "id",
            sa.String(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("processing_item_id", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("outbound_message_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("effect_status", sa.String(), nullable=True),
        sa.Column("external_reference", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message_sanitized", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["processing_item_id"],
            ["processing_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("attempt >= 1", name="ck_executions_attempt_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'RETRYING', 'FAILED')",
            name="ck_executions_status_valid",
        ),
        sa.CheckConstraint(
            "component IN ('ORCHESTRATOR', 'BOT_DF', 'TRANSCRIPTION', 'DB_WRITER')",
            name="ck_executions_component_valid",
        ),
        sa.CheckConstraint(
            "effect_status IS NULL OR effect_status IN ('DISPATCHED', 'ACKNOWLEDGED', 'OUTBOUND_OUTCOME_UNKNOWN', 'FAILED')",
            name="ck_executions_effect_status_valid",
        ),
    )
    op.create_index(
        "uq_executions_outbound_msg",
        "executions",
        ["outbound_message_id"],
        unique=True,
        postgresql_where=sa.text("outbound_message_id IS NOT NULL"),
    )

    # 5. Create service_usage table
    op.create_table(
        "service_usage",
        sa.Column(
            "id",
            sa.String(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("processing_item_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=True),
        sa.Column(
            "source_service",
            sa.String(),
            nullable=False,
            server_default="TRANSCRIPTION",
        ),
        sa.Column("source_request_id", sa.String(), nullable=False),
        sa.Column(
            "source_attempt_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["processing_item_id"],
            ["processing_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_service",
            "source_request_id",
            "source_attempt_number",
            name="uq_service_usage_source_attempt",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_service_usage_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "source_attempt_number >= 1",
            name="ck_service_usage_source_attempt_positive",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("service_usage")
    op.drop_index("uq_executions_outbound_msg", table_name="executions")
    op.drop_table("executions")
    op.drop_index("ix_processing_items_lease_recovery", table_name="processing_items")
    op.drop_index("ix_processing_items_expiration", table_name="processing_items")
    op.drop_index("ix_processing_items_capacity_check", table_name="processing_items")
    op.drop_index("ix_processing_items_fifo_lookup", table_name="processing_items")
    op.drop_index("uq_processing_items_one_active_per_conversation", table_name="processing_items")
    op.drop_table("processing_items")
    op.drop_table("conversation_queue_counters")
