"""add destination management

Revision ID: b4d8474a9f44
Revises: 408c4ba0d475
Create Date: 2026-11-05 11:21:49.962710

Hand-edited after autogenerate:
- enum types are created explicitly (op.add_column does not create them),
- CHECK constraints added (autogenerate does not detect them),
- existing destination rows are backfilled as 'primary' before the check is added,
- the self-referencing FK got a name so downgrade can drop it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4d8474a9f44'
down_revision: Union[str, Sequence[str], None] = '408c4ba0d475'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

trip_location_status = postgresql.ENUM('confirmed', 'replaced', 'removed', name='trip_location_status', create_type=False)
destination_type = postgresql.ENUM('primary', 'optional', name='destination_type', create_type=False)
pending_destination_status = postgresql.ENUM('pending', 'confirmed', 'cancelled', name='pending_destination_status', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for enum_type in (trip_location_status, destination_type, pending_destination_status):
        enum_type.create(bind, checkfirst=True)

    # --- trip_locations: new columns (all nullable or with server defaults, so existing rows stay valid)
    op.add_column('trip_locations', sa.Column('status', trip_location_status, server_default='confirmed', nullable=False))
    op.add_column('trip_locations', sa.Column('destination_type', destination_type, nullable=True))
    op.add_column('trip_locations', sa.Column('planned_duration_days', sa.Integer(), nullable=True))
    op.add_column('trip_locations', sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('trip_locations', sa.Column('replaced_by_id', sa.UUID(), nullable=True))
    op.add_column('trip_locations', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))

    # Existing destination rows (if any) become primary destinations.
    op.execute("UPDATE trip_locations SET destination_type = 'primary' WHERE role = 'destination'")

    op.create_check_constraint('ck_trip_locations_duration_positive', 'trip_locations', 'planned_duration_days IS NULL OR planned_duration_days > 0')
    op.create_check_constraint('ck_trip_locations_destination_type_matches_role', 'trip_locations', "(role = 'destination') = (destination_type IS NOT NULL)")

    # Full unique (trip, role, sequence) -> partial unique over active rows only,
    # so replaced/removed rows can be kept as history.
    op.drop_constraint('uq_trip_locations_trip_role_sequence', 'trip_locations', type_='unique')
    op.create_index(op.f('ix_trip_locations_trip_id'), 'trip_locations', ['trip_id'], unique=False)
    op.create_index('uq_trip_locations_active_destination_location', 'trip_locations', ['trip_id', 'location_id'], unique=True, postgresql_where=sa.text("role = 'destination' AND status = 'confirmed'"))
    op.create_index('uq_trip_locations_active_sequence', 'trip_locations', ['trip_id', 'role', 'sequence'], unique=True, postgresql_where=sa.text("status = 'confirmed'"))
    op.create_foreign_key('fk_trip_locations_replaced_by_id', 'trip_locations', 'trip_locations', ['replaced_by_id'], ['id'], ondelete='SET NULL')

    # --- pending_destinations: unconfirmed geocoding results, kept apart from trip_locations
    op.create_table('pending_destinations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('trip_id', sa.UUID(), nullable=False),
    sa.Column('query', sa.String(length=200), nullable=False),
    sa.Column('candidates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', pending_destination_status, server_default='pending', nullable=False),
    sa.Column('destination_type', destination_type, nullable=False),
    sa.Column('planned_duration_days', sa.Integer(), nullable=True),
    sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('replaces_trip_location_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('planned_duration_days IS NULL OR planned_duration_days > 0', name='ck_pending_destinations_duration_positive'),
    sa.ForeignKeyConstraint(['replaces_trip_location_id'], ['trip_locations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pending_destinations_trip_id'), 'pending_destinations', ['trip_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    WARNING: the old schema can't hold history, so replaced/removed trip_locations
    and all pending destinations are deleted.
    """
    op.drop_index(op.f('ix_pending_destinations_trip_id'), table_name='pending_destinations')
    op.drop_table('pending_destinations')

    op.execute("DELETE FROM trip_locations WHERE status <> 'confirmed'")
    op.drop_constraint('fk_trip_locations_replaced_by_id', 'trip_locations', type_='foreignkey')
    op.drop_index('uq_trip_locations_active_sequence', table_name='trip_locations')
    op.drop_index('uq_trip_locations_active_destination_location', table_name='trip_locations')
    op.drop_index(op.f('ix_trip_locations_trip_id'), table_name='trip_locations')
    op.create_unique_constraint('uq_trip_locations_trip_role_sequence', 'trip_locations', ['trip_id', 'role', 'sequence'])
    op.drop_constraint('ck_trip_locations_destination_type_matches_role', 'trip_locations', type_='check')
    op.drop_constraint('ck_trip_locations_duration_positive', 'trip_locations', type_='check')
    op.drop_column('trip_locations', 'updated_at')
    op.drop_column('trip_locations', 'replaced_by_id')
    op.drop_column('trip_locations', 'preferences')
    op.drop_column('trip_locations', 'planned_duration_days')
    op.drop_column('trip_locations', 'destination_type')
    op.drop_column('trip_locations', 'status')

    bind = op.get_bind()
    for enum_type in (pending_destination_status, destination_type, trip_location_status):
        enum_type.drop(bind, checkfirst=True)
