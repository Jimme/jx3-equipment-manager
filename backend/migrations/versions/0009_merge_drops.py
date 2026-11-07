"""Merge equal drop configurations and enforce account-local uniqueness."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

from backend.drop_merge import merge_drop_rows

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    rows = [dict(row) for row in connection.execute(sa.text('SELECT * FROM raid_drop_notes')).mappings()]
    _, changes = merge_drop_rows(rows)
    for before, after in changes:
        for duplicate in before[1:]:
            connection.execute(sa.text('DELETE FROM raid_drop_notes WHERE id=:id'), {'id': duplicate['id']})
        connection.execute(sa.text('UPDATE raid_drop_notes SET quantity=:quantity, version=:version WHERE id=:id'), after)
        connection.execute(sa.text(
            'INSERT INTO raid_memo_states(account_id,version) VALUES(:account_id,1) '
            'ON CONFLICT(account_id) DO UPDATE SET version=version+1'), after)
        connection.execute(sa.text(
            'INSERT INTO audit_events(id,created_at,account_id,operation,entity_id,snapshots) '
            'VALUES(:id,:time,:account,:operation,:entity,:snapshots)'), {
                'id': str(uuid4()), 'time': datetime.now(timezone.utc).isoformat(),
                'account': after['account_id'], 'operation': 'drop_merge_migration', 'entity': after['id'],
                'snapshots': json.dumps({'before': before, 'after': after,
                                        'summary': f"合并 {len(before)} 条相同掉落，保留 {after['quantity']} 件"}, ensure_ascii=False),
            })
    with op.batch_alter_table('raid_drop_notes') as batch:
        batch.create_unique_constraint('uq_drop_account_config', ['account_id', 'config_id'])


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动拆分已归并掉落')
