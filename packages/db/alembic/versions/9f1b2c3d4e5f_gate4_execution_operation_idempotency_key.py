"""gate4_execution_operation_idempotency_key

Revision ID: 9f1b2c3d4e5f
Revises: 9e0a1b2c3d5e
Create Date: 2026-08-05 22:00:00.000000

Adds `operation_idempotency_key` column to the `executions` table with a
partial UNIQUE index to enforce exactly-once checkpoint creation per logical
operation per processing item.

Physical uniqueness contract:
  - Callers populate operation_idempotency_key as:
    '<processing_item_id>:<operation>:<qualifier>'
  - The qualifier is the outbound_message_id for prompt-lifecycle checkpoints,
    the inbound_event_id for answer checkpoints, or the worker_id for claim
    and heartbeat checkpoints.
  - The partial unique index only applies to rows where the key IS NOT NULL,
    so legacy rows and operations without a natural idempotency identity are
    unaffected.

Forward-only migration: this migration has no down_revision side-effect that
modifies existing data; downgrade drops the column safely.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "9e0a1b2c3d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add operation_idempotency_key column and partial unique index to executions."""
    op.add_column(
        "executions",
        sa.Column("operation_idempotency_key", sa.String(512), nullable=True),
    )
    op.create_index(
        "uq_executions_operation_idempotency_key",
        "executions",
        ["operation_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("operation_idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove operation_idempotency_key column and its index from executions."""
    op.drop_index(
        "uq_executions_operation_idempotency_key",
        table_name="executions",
    )
    op.drop_column("executions", "operation_idempotency_key")
