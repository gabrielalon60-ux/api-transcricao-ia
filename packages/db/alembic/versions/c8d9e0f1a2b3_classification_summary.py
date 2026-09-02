"""add validated classification outcome and enterprise display snapshot

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATUSES_WITHOUT_VALIDATED = (
    "'RECEIVED', 'EXTRACTING', 'EXTRACTED', 'READY', 'ACTIVE', 'VALIDATING', "
    "'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', "
    "'PERSIST_OUTCOME_UNKNOWN', 'COMPLETED', 'EXTRACTION_FAILED', "
    "'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED', 'IGNORED'"
)


def upgrade() -> None:
    op.add_column(
        "processing_items",
        sa.Column("enterprise_display_name", sa.String(length=255), nullable=True),
    )
    op.drop_constraint(
        "ck_processing_items_status_valid", "processing_items", type_="check"
    )
    op.create_check_constraint(
        "ck_processing_items_status_valid",
        "processing_items",
        f"status IN ({_STATUSES_WITHOUT_VALIDATED}, 'VALIDATED')",
    )
    op.drop_index("ix_processing_items_capacity_check", table_name="processing_items")
    op.create_index(
        "ix_processing_items_capacity_check",
        "processing_items",
        ["organization_id", "instance_id", "user_id"],
        postgresql_where=sa.text(
            "status NOT IN ('VALIDATED', 'COMPLETED', 'EXTRACTION_FAILED', "
            "'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED', 'IGNORED')"
        ),
    )


def downgrade() -> None:
    op.execute("UPDATE processing_items SET status = 'READY' WHERE status = 'VALIDATED'")
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
    op.drop_constraint(
        "ck_processing_items_status_valid", "processing_items", type_="check"
    )
    op.create_check_constraint(
        "ck_processing_items_status_valid",
        "processing_items",
        f"status IN ({_STATUSES_WITHOUT_VALIDATED})",
    )
    op.drop_column("processing_items", "enterprise_display_name")
