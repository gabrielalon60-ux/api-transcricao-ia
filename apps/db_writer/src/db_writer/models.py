from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


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
    schema_version: Mapped[str] = mapped_column(sa.String(32), default="1.0", nullable=False)
    status: Mapped[str] = mapped_column(sa.String, nullable=False)  # RECEIVED, COMMITTED, REJECTED, RETRYABLE_FAILURE
    committed_record_id: Mapped[Optional[str]] = mapped_column(sa.String, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(sa.String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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
