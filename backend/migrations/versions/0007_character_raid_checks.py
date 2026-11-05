"""Add manual raid completion checks per character."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    for column in ('raid_10', 'raid_25pt', 'raid_25yx'):
        op.add_column('characters', sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动回退数据库')
