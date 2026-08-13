"""gate7 local DF MVP destination

Revision ID: b7c8d9e0f1a3
Revises: a1b2c3d4e5f6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7c8d9e0f1a3"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "enterprises",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enterprises_deterministic_list", "enterprises", ["name", "id"])

    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("cnpj", sa.String(14), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("contact", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cnpj", name="uq_suppliers_cnpj"),
        sa.CheckConstraint("cnpj ~ '^[0-9]{14}$'", name="ck_suppliers_cnpj_normalized"),
    )

    op.create_table(
        "financial_records",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expense_type_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("supplier_cnpj_snapshot", sa.String(14), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column(
            "is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("processing_item_id", sa.String(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["enterprise_id"], ["enterprises.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "processing_item_id", name="uq_financial_records_processing_item"
        ),
        sa.CheckConstraint("amount > 0", name="ck_financial_records_amount_positive"),
        sa.CheckConstraint(
            "origin IN ('WHATSAPP', 'SITE')", name="ck_financial_records_origin_valid"
        ),
        sa.CheckConstraint(
            "supplier_cnpj_snapshot IS NULL OR supplier_cnpj_snapshot ~ '^[0-9]{14}$'",
            name="ck_financial_records_supplier_snapshot_normalized",
        ),
        sa.CheckConstraint(
            "(is_deleted = false AND deleted_at IS NULL) OR (is_deleted = true AND deleted_at IS NOT NULL)",
            name="ck_financial_records_soft_delete_consistent",
        ),
    )


def downgrade() -> None:
    op.drop_table("financial_records")
    op.drop_table("suppliers")
    op.drop_index("ix_enterprises_deterministic_list", table_name="enterprises")
    op.drop_table("enterprises")
