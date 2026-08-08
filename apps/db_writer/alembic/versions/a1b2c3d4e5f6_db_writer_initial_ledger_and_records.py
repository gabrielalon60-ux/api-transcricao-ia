"""db_writer_initial_ledger_and_records

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-08-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create write_ledger table
    op.create_table(
        "write_ledger",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("canonical_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("processing_item_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("committed_record_id", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
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
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('COMMITTED', 'REJECTED')",
            name="ck_write_ledger_status_valid",
        ),
        sa.CheckConstraint(
            "attempt_count >= 1",
            name="ck_write_ledger_attempt_count_positive",
        ),
    )
    op.create_index(
        "uq_write_ledger_idempotency_key",
        "write_ledger",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_write_ledger_tenant",
        "write_ledger",
        ["organization_id", "instance_id", "user_id"],
        unique=False,
    )

    # 2. Create df_business_records table (DF Holding destination business ledger adapter)
    op.create_table(
        "df_business_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("processing_item_id", sa.String(), nullable=False),
        sa.Column("document_type", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("document_date", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_business_records_amount_positive",
        ),
        sa.CheckConstraint(
            "direction IN ('income', 'expense')",
            name="ck_business_records_direction_valid",
        ),
    )
    op.create_index(
        "ix_df_business_records_processing_item_id",
        "df_business_records",
        ["processing_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_df_business_records_processing_item_id", table_name="df_business_records")
    op.drop_table("df_business_records")
    op.drop_index("ix_write_ledger_tenant", table_name="write_ledger")
    op.drop_index("uq_write_ledger_idempotency_key", table_name="write_ledger")
    op.drop_table("write_ledger")
