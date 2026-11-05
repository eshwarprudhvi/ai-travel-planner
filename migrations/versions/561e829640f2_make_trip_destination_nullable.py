"""make_trip_destination_nullable

Revision ID: 561e829640f2
Revises: b4d8474a9f44
Create Date: 2026-11-05 13:12:16.787724

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '561e829640f2'
down_revision: Union[str, Sequence[str], None] = 'b4d8474a9f44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: make trips.destination nullable."""
    op.alter_column('trips', 'destination', existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    """Downgrade schema: restore trips.destination NOT NULL."""
    op.alter_column('trips', 'destination', existing_type=sa.String(length=100), nullable=False)
