"""Add combined comparison without changing existing account preferences."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    table = sa.Table('game_accounts', sa.MetaData(), autoload_with=op.get_bind())
    for constraint in table.constraints:
        if isinstance(constraint, sa.CheckConstraint):
            if 'comparison_mode' in str(constraint.sqltext):
                constraint.name = 'ck_game_accounts_comparison_mode'
            elif 'target_grade' in str(constraint.sqltext):
                constraint.name = 'ck_game_accounts_target_grade'
    with op.batch_alter_table('game_accounts', copy_from=table, recreate='always') as batch:
        batch.drop_constraint('ck_game_accounts_comparison_mode', type_='check')
        batch.create_check_constraint('ck_game_accounts_comparison_mode', "comparison_mode IN ('strict', 'grade', 'combined')")


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动回退数据库')
