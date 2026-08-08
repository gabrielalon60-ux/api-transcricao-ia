"""gate4_persistence_dispatch_ownership

Revision ID: a1b2c3d4e5f6
Revises: 9f1b2c3d4e5f
Create Date: 2026-08-08 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9f1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add persistence dispatch ownership & scheduling columns to processing_items
    op.add_column(
        "processing_items",
        sa.Column("persistence_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "processing_items",
        sa.Column("persistence_claimed_by", sa.String(), nullable=True),
    )
    op.add_column(
        "processing_items",
        sa.Column("persistence_claim_kind", sa.String(), nullable=True),
    )
    op.add_column(
        "processing_items",
        sa.Column("persistence_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processing_items",
        sa.Column("persistence_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "processing_items",
        sa.Column("persistence_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_processing_items_persistence_claim_kind_valid",
        "processing_items",
        "persistence_claim_kind IS NULL OR persistence_claim_kind IN ('DISPATCH', 'RECONCILIATION')",
    )

    op.create_check_constraint(
        "ck_processing_items_persistence_generation_non_negative",
        "processing_items",
        "persistence_generation >= 0",
    )
    op.create_check_constraint(
        "ck_processing_items_persistence_attempt_count_non_negative",
        "processing_items",
        "persistence_attempt_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_processing_items_persistence_claim_kind_valid",
        "processing_items",
        type_="check",
    )
    op.drop_constraint(
        "ck_processing_items_persistence_attempt_count_non_negative",
        "processing_items",
        type_="check",
    )
    op.drop_constraint(
        "ck_processing_items_persistence_generation_non_negative",
        "processing_items",
        type_="check",
    )
    op.drop_column("processing_items", "persistence_next_attempt_at")
    op.drop_column("processing_items", "persistence_attempt_count")
    op.drop_column("processing_items", "persistence_lease_expires_at")
    op.drop_column("processing_items", "persistence_claim_kind")
    op.drop_column("processing_items", "persistence_claimed_by")
    op.drop_column("processing_items", "persistence_generation")
