"""gate4_media_ref_and_claim_token

Revision ID: 9c0a1b2c3d4e
Revises: 8b9a0c1d2e3f
Create Date: 2026-08-04 23:59:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c0a1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "8b9a0c1d2e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "processing_items",
        sa.Column("media_ref", sa.JSON(), nullable=True),
    )
    op.add_column(
        "processing_items",
        sa.Column("extraction_claim_token", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("processing_items", "extraction_claim_token")
    op.drop_column("processing_items", "media_ref")
