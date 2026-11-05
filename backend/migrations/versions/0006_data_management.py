"""Short-lived, optimistic workspace import and restore previews."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('workspace_settings', sa.Column('version_seed', sa.Integer(),
        sa.CheckConstraint('version_seed >= 0', name='ck_workspace_version_seed'),
        nullable=False, server_default='0'))
    op.create_table('data_change_previews',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('expected_fingerprint', sa.String(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动回退数据库')
