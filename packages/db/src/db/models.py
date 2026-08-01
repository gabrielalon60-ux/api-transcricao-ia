from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, func, ForeignKey, Boolean
from typing import Optional
from datetime import datetime
import uuid
import sqlalchemy as sa


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    registration_secret_hash: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    service_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Instance(Base):
    __tablename__ = "instances"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"))
    bot_id: Mapped[str] = mapped_column(String, ForeignKey("bots.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String, default="WUZAPI")
    external_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"))
    phone_number: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )  # Unique phone number constraint
    name: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    correlation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Identidade de origem externa (para idempotência antes de resolver a instância interna)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    external_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    external_message_id: Mapped[str] = mapped_column(String, nullable=False)

    # Roteamento interno (nullable para eventos não associados a instâncias/organizações conhecidas)
    organization_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=True
    )
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("instances.id"), nullable=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )

    message_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="RECEIVED", index=True)

    # Controle atômico de duplicados
    duplicate_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    last_duplicate_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    routed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message_sanitized: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "external_instance_id",
            "external_message_id",
            name="uq_events_source_idempotency",
        ),
        sa.Index("ix_events_org_received", "organization_id", "received_at"),
    )


class RegistrationAttempt(Base):
    __tablename__ = "registration_attempts"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False
    )
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("instances.id"), nullable=True
    )
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        sa.Index(
            "ix_reg_attempts_lookup", "organization_id", "phone_number", "attempted_at"
        ),
    )


class RegistrationRateLimit(Base):
    __tablename__ = "registration_rate_limits"
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), primary_key=True
    )
    phone_number: Mapped[str] = mapped_column(String, primary_key=True)
    failure_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
