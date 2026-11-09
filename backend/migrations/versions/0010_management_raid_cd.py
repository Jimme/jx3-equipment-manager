"""Versioned management, creation receipts and optional Shanghai raid resets."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('game_accounts', sa.Column('version', sa.Integer(), nullable=False, server_default='0'))
    for name in ('version', 'raid_version'):
        op.add_column('characters', sa.Column(name, sa.Integer(), nullable=False, server_default='0'))
    op.add_column('characters', sa.Column('history_complete', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('creation_receipts', sa.Column('request_id', sa.String(), primary_key=True),
                    sa.Column('fingerprint', sa.String(), nullable=False), sa.Column('result', sa.Text(), nullable=False))
    op.create_table('raid_cd_settings', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
                    sa.Column('cursor_10', sa.String()), sa.Column('cursor_25', sa.String()), sa.Column('changed_at', sa.String()),
                    sa.CheckConstraint('id = 1'), sa.CheckConstraint('version >= 0'))
    op.execute('INSERT INTO raid_cd_settings(id,enabled,version) VALUES(1,0,0)')


def downgrade():
    raise RuntimeError('请使用升级前快照及匹配程序恢复')
