"""Configure constitution socket availability by equipment cycle."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('equipment_cycles', sa.Column('has_constitution_sockets', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动回退数据库')
