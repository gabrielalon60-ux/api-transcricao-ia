"""gate7 income outcome and enterprise interaction platform schema

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_STATUSES = (
    "'RECEIVED', 'EXTRACTING', 'EXTRACTED', 'READY', 'ACTIVE', 'VALIDATING', "
    "'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', "
    "'PERSIST_OUTCOME_UNKNOWN', 'COMPLETED', 'EXTRACTION_FAILED', "
    "'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED'"
)


def upgrade() -> None:
    op.add_column(
        "processing_items", sa.Column("enterprise_id", sa.String(), nullable=True)
    )
    op.add_column(
        "processing_items", sa.Column("outcome_reason", sa.String(), nullable=True)
    )
    op.drop_constraint(
        "ck_processing_items_status_valid", "processing_items", type_="check"
    )
    op.create_check_constraint(
        "ck_processing_items_status_valid",
        "processing_items",
        f"status IN ({_OLD_STATUSES}, 'IGNORED')",
    )
    op.create_check_constraint(
        "ck_processing_items_ignored_reason_valid",
        "processing_items",
        "(status = 'IGNORED' AND outcome_reason IS NOT DISTINCT FROM 'INCOME_OUT_OF_SCOPE') OR "
        "(status <> 'IGNORED' AND outcome_reason IS NULL)",
    )
    op.drop_index("ix_processing_items_capacity_check", table_name="processing_items")
    op.create_index(
        "ix_processing_items_capacity_check",
        "processing_items",
        ["organization_id", "instance_id", "user_id"],
        postgresql_where=sa.text(
            "status NOT IN ('COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', "
            "'FAILED', 'EXPIRED', 'CANCELLED', 'IGNORED')"
        ),
    )

    op.add_column(
        "user_interactions",
        sa.Column(
            "option_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.drop_constraint(
        "ck_user_interactions_question_type_valid", "user_interactions", type_="check"
    )
    op.create_check_constraint(
        "ck_user_interactions_question_type_valid",
        "user_interactions",
        "question_type IN ('transaction_direction', 'transaction_amount', "
        "'document_classification', 'enterprise_selection')",
    )
    op.create_check_constraint(
        "ck_user_interactions_enterprise_options_required",
        "user_interactions",
        "question_type <> 'enterprise_selection' OR option_mapping IS NOT NULL",
    )

    op.create_table(
        "whatsapp_chat_enterprise_bindings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "instance_id",
            sa.String(),
            sa.ForeignKey("instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enterprise_id", sa.String(), nullable=False),
        sa.Column("source_command_session_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "instance_id",
            "user_id",
            name="uq_whatsapp_chat_enterprise_binding_conversation",
        ),
    )

    op.create_table(
        "enterprise_command_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "instance_id",
            sa.String(),
            sa.ForeignKey("instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "option_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("clear_option_position", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.String(), nullable=False),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_event_id",
            sa.String(),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "instance_id",
            "user_id",
            "generation",
            name="uq_enterprise_command_session_generation",
        ),
        sa.UniqueConstraint(
            "outbound_message_id", name="uq_enterprise_command_outbound_message"
        ),
        sa.CheckConstraint(
            "generation > 0", name="ck_enterprise_command_generation_positive"
        ),
        sa.CheckConstraint(
            "clear_option_position > 0",
            name="ck_enterprise_command_clear_position_positive",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN', "
            "'ANSWERED', 'EXPIRED', 'CANCELLED')",
            name="ck_enterprise_command_status_valid",
        ),
        sa.CheckConstraint(
            "(status IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN') AND waiting_since IS NOT NULL "
            "AND expires_at IS NOT NULL) OR status NOT IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')",
            name="ck_enterprise_command_waiting_timestamps",
        ),
        sa.CheckConstraint(
            "(status IN ('ANSWERED', 'EXPIRED', 'CANCELLED') AND resolved_at IS NOT NULL) "
            "OR status NOT IN ('ANSWERED', 'EXPIRED', 'CANCELLED')",
            name="ck_enterprise_command_resolved_timestamp",
        ),
    )
    op.create_index(
        "uq_enterprise_command_one_open_per_conversation",
        "enterprise_command_sessions",
        ["organization_id", "instance_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')"
        ),
    )

    op.create_table(
        "enterprise_command_answers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("enterprise_command_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "inbound_event_id",
            sa.String(),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sanitized_answer", sa.Text(), nullable=True),
        sa.Column(
            "parsing_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "inbound_event_id", name="uq_enterprise_command_answers_inbound_event"
        ),
        sa.CheckConstraint(
            "status IN ('APPLIED', 'REJECTED', 'LATE')",
            name="ck_enterprise_command_answers_status_valid",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    incompatible_data_exists = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM processing_items
                    WHERE status = 'IGNORED'
                       OR outcome_reason IS NOT NULL
                       OR enterprise_id IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1 FROM user_interactions
                    WHERE question_type = 'enterprise_selection'
                       OR option_mapping IS NOT NULL
                )
                OR EXISTS (SELECT 1 FROM whatsapp_chat_enterprise_bindings)
                OR EXISTS (SELECT 1 FROM enterprise_command_sessions)
                OR EXISTS (SELECT 1 FROM enterprise_command_answers)
            """
        )
    ).scalar_one()
    if incompatible_data_exists:
        raise RuntimeError(
            "Gate 7 downgrade refused because data-bearing Gate 7 state exists"
        )

    op.drop_table("enterprise_command_answers")
    op.drop_index(
        "uq_enterprise_command_one_open_per_conversation",
        table_name="enterprise_command_sessions",
    )
    op.drop_table("enterprise_command_sessions")
    op.drop_table("whatsapp_chat_enterprise_bindings")
    op.drop_constraint(
        "ck_user_interactions_enterprise_options_required",
        "user_interactions",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_interactions_question_type_valid", "user_interactions", type_="check"
    )
    op.create_check_constraint(
        "ck_user_interactions_question_type_valid",
        "user_interactions",
        "question_type IN ('transaction_direction', 'transaction_amount', 'document_classification')",
    )
    op.drop_column("user_interactions", "option_mapping")
    op.drop_index("ix_processing_items_capacity_check", table_name="processing_items")
    op.create_index(
        "ix_processing_items_capacity_check",
        "processing_items",
        ["organization_id", "instance_id", "user_id"],
        postgresql_where=sa.text(
            "status NOT IN ('COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', "
            "'FAILED', 'EXPIRED', 'CANCELLED')"
        ),
    )
    op.drop_constraint(
        "ck_processing_items_ignored_reason_valid", "processing_items", type_="check"
    )
    op.drop_constraint(
        "ck_processing_items_status_valid", "processing_items", type_="check"
    )
    op.create_check_constraint(
        "ck_processing_items_status_valid",
        "processing_items",
        f"status IN ({_OLD_STATUSES})",
    )
    op.drop_column("processing_items", "outcome_reason")
    op.drop_column("processing_items", "enterprise_id")
