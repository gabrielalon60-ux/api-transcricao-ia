"""Deterministic Gate 3 Transcription schema migration.

Limited downgrade before Gate 3 data exists; operationally forward-only after
Gate 3 activation. PostgreSQL enum values are not casually removed on downgrade.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "gate3_schema"
down_revision: Union[str, Sequence[str], None] = "transcription_1_0_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE requeststatus ADD VALUE 'SUCCEEDED' BEFORE 'FAILED'")
    op.execute("ALTER TYPE requeststatus ADD VALUE 'PERSISTENCE_FAILED'")

    op.alter_column("requests", "application_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("requests", sa.Column("correlation_id", sa.String(length=128), nullable=True))
    op.add_column("requests", sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("requests", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("requests", sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("requests", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("requests", sa.Column("received_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("requests", sa.Column("source", sa.String(length=64), nullable=True))
    op.add_column("requests", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("requests", sa.Column("last_persistence_error_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("requests", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("requests", sa.Column("detected_mime", sa.String(length=64), nullable=True))
    op.add_column("requests", sa.Column("declared_mime", sa.String(length=64), nullable=True))
    op.add_column("requests", sa.Column("file_size_bytes", sa.Integer(), nullable=True))
    op.add_column("requests", sa.Column("file_sha256", sa.String(length=64), nullable=True))

    op.alter_column("extractions", "prompt", existing_type=sa.Text(), nullable=True)

    op.add_column("usage_logs", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.add_column("usage_logs", sa.Column("provider", sa.String(length=100), nullable=True))
    op.add_column("usage_logs", sa.Column("status", sa.String(length=64), nullable=True))
    op.add_column("usage_logs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("usage_logs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("usage_logs", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column("usage_logs", sa.Column("cached_tokens", sa.Integer(), nullable=True))
    op.add_column("usage_logs", sa.Column("usage_status", sa.String(length=32), nullable=True))
    op.add_column("usage_logs", sa.Column("currency", sa.String(length=8), nullable=True))
    op.add_column("usage_logs", sa.Column("pricing_version", sa.String(length=32), nullable=True))
    op.add_column("usage_logs", sa.Column("sanitized_error_code", sa.String(length=64), nullable=True))
    op.alter_column("usage_logs", "input_tokens", existing_type=sa.Integer(), nullable=True, server_default=None)
    op.alter_column("usage_logs", "output_tokens", existing_type=sa.Integer(), nullable=True, server_default=None)
    op.alter_column(
        "usage_logs",
        "estimated_cost",
        existing_type=sa.Float(),
        type_=sa.Numeric(18, 8),
        nullable=True,
        server_default=None,
        postgresql_using="estimated_cost::numeric(18,8)",
    )
    op.execute("UPDATE usage_logs SET attempt_number = 1 WHERE attempt_number IS NULL")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM usage_logs WHERE attempt_number IS NULL) "
        "THEN RAISE EXCEPTION 'usage_logs.attempt_number backfill failed'; END IF; END $$;"
    )
    op.alter_column("usage_logs", "attempt_number", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_usage_logs_request_attempt", "usage_logs", ["request_id", "attempt_number"])
    op.drop_constraint("usage_logs_request_id_key", "usage_logs", type_="unique")


def downgrade() -> None:
    raise NotImplementedError(
        "Gate 3 downgrade is limited before Gate 3 data exists and operationally "
        "forward-only after activation; PostgreSQL enum value removal is not safe here."
    )
