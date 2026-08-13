from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID


class DBWriterBase(DeclarativeBase):
    pass


class WriteLedger(DBWriterBase):
    __tablename__ = "write_ledger"

    id: Mapped[str] = mapped_column(
        sa.String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    idempotency_key: Mapped[str] = mapped_column(
        sa.String(512),
        unique=True,
        nullable=False,
    )
    canonical_payload_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )
    processing_item_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    organization_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    instance_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        sa.String(32), default="1.0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.String, nullable=False
    )  # RECEIVED, COMMITTED, REJECTED, RETRYABLE_FAILURE
    committed_record_id: Mapped[Optional[str]] = mapped_column(sa.String, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(sa.String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    committed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('COMMITTED', 'REJECTED')",
            name="ck_write_ledger_status_valid",
        ),
    )


class BusinessRecord(DBWriterBase):
    """Mock DF Holding target business transaction table for test evidence."""

    __tablename__ = "df_business_records"

    id: Mapped[str] = mapped_column(
        sa.String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    organization_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    instance_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    processing_item_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    document_type: Mapped[str] = mapped_column(sa.String, nullable=False)
    direction: Mapped[str] = mapped_column(sa.String, nullable=False)
    amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(18, 2), nullable=False)
    document_date: Mapped[Optional[str]] = mapped_column(sa.String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Enterprise(DBWriterBase):
    __tablename__ = "enterprises"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Supplier(DBWriterBase):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cnpj: Mapped[str] = mapped_column(sa.String(14), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(sa.Text)
    contact: Mapped[Optional[str]] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FinancialRecord(DBWriterBase):
    __tablename__ = "financial_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    transaction_date: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    expense_type_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    enterprise_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        sa.ForeignKey("enterprises.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[sa.Numeric] = mapped_column(sa.Numeric(18, 2), nullable=False)
    supplier_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), sa.ForeignKey("suppliers.id", ondelete="RESTRICT")
    )
    supplier_cnpj_snapshot: Mapped[Optional[str]] = mapped_column(sa.String(14))
    comments: Mapped[Optional[str]] = mapped_column(sa.Text)
    is_deleted: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    processing_item_id: Mapped[str] = mapped_column(
        sa.String, nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.CheckConstraint("amount > 0", name="ck_financial_records_amount_positive"),
        sa.CheckConstraint(
            "origin IN ('WHATSAPP', 'SITE')", name="ck_financial_records_origin_valid"
        ),
    )
