"""gate4_user_interactions_and_answers

Revision ID: 9e0a1b2c3d5e
Revises: 9c0a1b2c3d4e
Create Date: 2026-08-05 19:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9e0a1b2c3d5e"
down_revision: Union[str, Sequence[str], None] = "9c0a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_interactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "processing_item_id",
            sa.String(),
            sa.ForeignKey("processing_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(), nullable=False),
        sa.Column("outbound_message_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "processing_item_id", "generation", name="uq_user_interactions_item_generation"
        ),
        sa.UniqueConstraint("outbound_message_id", name="uq_user_interactions_outbound_msg"),
        sa.CheckConstraint("generation > 0", name="ck_user_interactions_generation_positive"),
        sa.CheckConstraint(
            "question_type IN ('transaction_direction', 'transaction_amount', 'document_classification')",
            name="ck_user_interactions_question_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'WAITING', 'ANSWERED', 'CANCELLED', 'EXPIRED', 'OUTBOUND_OUTCOME_UNKNOWN')",
            name="ck_user_interactions_status_valid",
        ),
        sa.CheckConstraint(
            "(status IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN') AND waiting_since IS NOT NULL AND expires_at IS NOT NULL) OR (status NOT IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN'))",
            name="ck_user_interactions_waiting_timestamps",
        ),
        sa.CheckConstraint(
            "(status IN ('ANSWERED', 'CANCELLED', 'EXPIRED') AND resolved_at IS NOT NULL) OR (status NOT IN ('ANSWERED', 'CANCELLED', 'EXPIRED'))",
            name="ck_user_interactions_resolved_timestamp",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR waiting_since IS NULL OR expires_at > waiting_since",
            name="ck_user_interactions_expires_after_waiting",
        ),
    )

    op.create_index(
        "uq_user_interactions_one_open_per_item",
        "user_interactions",
        ["processing_item_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')"
        ),
    )

    op.create_table(
        "user_answers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "interaction_id",
            sa.String(),
            sa.ForeignKey("user_interactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "processing_item_id",
            sa.String(),
            sa.ForeignKey("processing_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "inbound_event_id",
            sa.String(),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sanitized_answer", sa.Text(), nullable=True),
        sa.Column("parsing_result", sa.JSON(), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("inbound_event_id", name="uq_user_answers_inbound_event"),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'APPLIED', 'REJECTED', 'LATE')",
            name="ck_user_answers_status_valid",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("user_answers")
    op.drop_index(
        "uq_user_interactions_one_open_per_item",
        table_name="user_interactions",
    )
    op.drop_table("user_interactions")
