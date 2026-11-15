"""Convert the old cycle-wide switch to comparison exemptions by equipment type."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('equipment_cycles', sa.Column('socket_exempt_types', sa.String(), nullable=False, server_default=''))
    op.execute("UPDATE equipment_cycles SET socket_exempt_types = 'hat,chest,belt,weapon,ranged,wrist,legs,feet,necklace,pendant' "
               "WHERE has_constitution_sockets = 0")


def downgrade():
    op.drop_column('equipment_cycles', 'socket_exempt_types')
