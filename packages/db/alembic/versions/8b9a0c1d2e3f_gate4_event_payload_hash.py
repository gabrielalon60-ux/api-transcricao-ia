"""gate4_event_payload_hash

Revision ID: 8b9a0c1d2e3f
Revises: 7a8f9c1b2d3e
Create Date: 2026-08-04 23:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8b9a0c1d2e3f"
down_revision: Union[str, Sequence[str], None] = "7a8f9c1b2d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "events",
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "payload_hash")
