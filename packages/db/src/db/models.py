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

    # Identidade imutável do payload (hash SHA-256 canônico)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

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


class ConversationQueueCounter(Base):
    __tablename__ = "conversation_queue_counters"
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), primary_key=True
    )
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("instances.id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), primary_key=True
    )
    last_sequence: Mapped[int] = mapped_column(sa.BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "last_sequence >= 0", name="ck_conv_queue_counters_sequence_non_negative"
        ),
    )


class ProcessingItem(Base):
    __tablename__ = "processing_items"
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey("events.id"), unique=True, nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False
    )
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("instances.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    sequence: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String, default="RECEIVED", nullable=False)

    # Lease & Worker Execution Metadata
    claimed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    # Ingestion Metadata
    message_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    file_mime_type: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    media_ref: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    extraction_claim_token: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )

    # Normalized Extraction Payload
    document_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_extraction: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    normalized_data: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    quality_flags: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    confidence_data: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)

    # Business Processing Fields
    amount: Mapped[Optional[sa.Numeric]] = mapped_column(
        sa.Numeric(18, 2), nullable=True
    )
    document_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    transaction_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    date_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enterprise_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enterprise_display_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # Interactive State Fields
    question_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    outcome_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    waiting_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # External Writer Integration
    writer_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String, unique=True, nullable=True
    )
    external_operation_status: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    persistence_generation: Mapped[int] = mapped_column(
        sa.Integer, default=0, nullable=False
    )
    persistence_claimed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    persistence_claim_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    persistence_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    persistence_attempt_count: Mapped[int] = mapped_column(
        sa.Integer, default=0, nullable=False
    )
    persistence_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Error & Audit Fields
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message_sanitized: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )

    # Lifecycle Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    extracted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
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
            "persistence_generation >= 0",
            name="ck_processing_items_persistence_generation_non_negative",
        ),
        sa.CheckConstraint(
            "persistence_claim_kind IS NULL OR persistence_claim_kind IN ('DISPATCH', 'RECONCILIATION')",
            name="ck_processing_items_persistence_claim_kind_valid",
        ),
        sa.CheckConstraint(
            "persistence_attempt_count >= 0",
            name="ck_processing_items_persistence_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'EXTRACTING', 'EXTRACTED', 'READY', 'ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'VALIDATED', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN', 'COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED', 'IGNORED')",
            name="ck_processing_items_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'IGNORED' AND outcome_reason IS NOT DISTINCT FROM 'INCOME_OUT_OF_SCOPE') OR "
            "(status <> 'IGNORED' AND outcome_reason IS NULL)",
            name="ck_processing_items_ignored_reason_valid",
        ),
        sa.Index(
            "uq_processing_items_one_active_per_conversation",
            "organization_id",
            "instance_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('ACTIVE', 'VALIDATING', 'WAITING_USER_INPUT', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN')"
            ),
        ),
        sa.Index(
            "ix_processing_items_fifo_lookup",
            "organization_id",
            "instance_id",
            "user_id",
            "status",
            "sequence",
        ),
        sa.Index(
            "ix_processing_items_capacity_check",
            "organization_id",
            "instance_id",
            "user_id",
            postgresql_where=sa.text(
                "status NOT IN ('VALIDATED', 'COMPLETED', 'EXTRACTION_FAILED', 'PERSISTENCE_FAILED', 'FAILED', 'EXPIRED', 'CANCELLED', 'IGNORED')"
            ),
        ),
        sa.Index(
            "ix_processing_items_expiration",
            "expires_at",
            postgresql_where=sa.text("status = 'WAITING_USER_INPUT'"),
        ),
        sa.Index(
            "ix_processing_items_lease_recovery",
            "lease_expires_at",
            postgresql_where=sa.text(
                "status IN ('ACTIVE', 'VALIDATING', 'PERSISTING', 'PERSIST_RETRYABLE', 'PERSIST_OUTCOME_UNKNOWN')"
            ),
        ),
    )


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey("events.id"), nullable=False
    )
    processing_item_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("processing_items.id"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    component: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    outbound_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    operation_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    effect_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attempt: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message_sanitized: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.Index(
            "uq_executions_outbound_msg",
            "outbound_message_id",
            unique=True,
            postgresql_where=sa.text("outbound_message_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_executions_operation_idempotency_key",
            "operation_idempotency_key",
            unique=True,
            postgresql_where=sa.text("operation_idempotency_key IS NOT NULL"),
        ),
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


class ServiceUsage(Base):
    __tablename__ = "service_usage"
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey("events.id"), nullable=False
    )
    processing_item_id: Mapped[str] = mapped_column(
        String, ForeignKey("processing_items.id"), nullable=False
    )
    execution_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("executions.id"), nullable=True
    )
    source_service: Mapped[str] = mapped_column(
        String, default="TRANSCRIPTION", nullable=False
    )
    source_request_id: Mapped[str] = mapped_column(String, nullable=False)
    source_attempt_number: Mapped[int] = mapped_column(
        sa.Integer, default=1, nullable=False
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    estimated_cost: Mapped[Optional[sa.Numeric]] = mapped_column(
        sa.Numeric(18, 8), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
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


class UserInteraction(Base):
    __tablename__ = "user_interactions"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    processing_item_id: Mapped[str] = mapped_column(
        String, ForeignKey("processing_items.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String, nullable=False)
    outbound_message_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    option_mapping: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    waiting_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "processing_item_id",
            "generation",
            name="uq_user_interactions_item_generation",
        ),
        sa.UniqueConstraint(
            "outbound_message_id", name="uq_user_interactions_outbound_msg"
        ),
        sa.CheckConstraint(
            "generation > 0", name="ck_user_interactions_generation_positive"
        ),
        sa.CheckConstraint(
            "question_type IN ('transaction_direction', 'transaction_amount', 'document_classification', 'enterprise_selection')",
            name="ck_user_interactions_question_type_valid",
        ),
        sa.CheckConstraint(
            "question_type <> 'enterprise_selection' OR option_mapping IS NOT NULL",
            name="ck_user_interactions_enterprise_options_required",
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
        sa.Index(
            "uq_user_interactions_one_open_per_item",
            "processing_item_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')"
            ),
        ),
    )


class UserAnswer(Base):
    __tablename__ = "user_answers"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    interaction_id: Mapped[str] = mapped_column(
        String, ForeignKey("user_interactions.id", ondelete="RESTRICT"), nullable=False
    )
    processing_item_id: Mapped[str] = mapped_column(
        String, ForeignKey("processing_items.id", ondelete="RESTRICT"), nullable=False
    )
    inbound_event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    sanitized_answer: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    parsing_result: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("inbound_event_id", name="uq_user_answers_inbound_event"),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'APPLIED', 'REJECTED', 'LATE')",
            name="ck_user_answers_status_valid",
        ),
    )


class WhatsappChatEnterpriseBinding(Base):
    __tablename__ = "whatsapp_chat_enterprise_bindings"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enterprise_id: Mapped[str] = mapped_column(String, nullable=False)
    source_command_session_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "instance_id",
            "user_id",
            name="uq_whatsapp_chat_enterprise_binding_conversation",
        ),
    )


class EnterpriseCommandSession(Base):
    __tablename__ = "enterprise_command_sessions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    option_mapping: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    clear_option_position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    outbound_message_id: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    waiting_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by_event_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("events.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "instance_id",
            "user_id",
            "generation",
            name="uq_enterprise_command_session_generation",
        ),
        sa.CheckConstraint(
            "generation > 0", name="ck_enterprise_command_generation_positive"
        ),
        sa.CheckConstraint(
            "clear_option_position > 0",
            name="ck_enterprise_command_clear_position_positive",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN', 'ANSWERED', 'EXPIRED', 'CANCELLED')",
            name="ck_enterprise_command_status_valid",
        ),
        sa.CheckConstraint(
            "(status IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN') AND waiting_since IS NOT NULL AND expires_at IS NOT NULL) "
            "OR status NOT IN ('WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')",
            name="ck_enterprise_command_waiting_timestamps",
        ),
        sa.CheckConstraint(
            "(status IN ('ANSWERED', 'EXPIRED', 'CANCELLED') AND resolved_at IS NOT NULL) "
            "OR status NOT IN ('ANSWERED', 'EXPIRED', 'CANCELLED')",
            name="ck_enterprise_command_resolved_timestamp",
        ),
        sa.Index(
            "uq_enterprise_command_one_open_per_conversation",
            "organization_id",
            "instance_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('RESERVED', 'WAITING', 'OUTBOUND_OUTCOME_UNKNOWN')"
            ),
        ),
    )


class EnterpriseCommandAnswer(Base):
    __tablename__ = "enterprise_command_answers"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("enterprise_command_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inbound_event_id: Mapped[str] = mapped_column(
        String, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    sanitized_answer: Mapped[Optional[str]] = mapped_column(sa.Text)
    parsing_result: Mapped[Optional[dict]] = mapped_column(sa.JSON)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "inbound_event_id", name="uq_enterprise_command_answers_inbound_event"
        ),
        sa.CheckConstraint(
            "status IN ('APPLIED', 'REJECTED', 'LATE')",
            name="ck_enterprise_command_answers_status_valid",
        ),
    )
