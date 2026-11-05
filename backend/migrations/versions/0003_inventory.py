"""Shared stock, raid memo and per-equipment attachments."""
import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('equipment_configs', sa.Column('config_key', sa.String(), nullable=True))
    for prefix in ('enchant', 'rainbow'):
        op.add_column('equipment_configs', sa.Column(f'{prefix}_name', sa.String(), nullable=False, server_default=''))
        op.add_column('equipment_configs', sa.Column(f'{prefix}_constitution', sa.Integer(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT c.*, t.equipment_type FROM equipment_configs c JOIN equipment_templates t ON t.id=c.template_id')).mappings().all()
    for row in rows:
        values = {'template_id': row['template_id'], 'enchant_state': row['enchant_state'], 'enchant_name': '', 'enchant_constitution': None,
                  'rainbow_state': 'unknown' if row['equipment_type'] == 'weapon' else 'not_applicable', 'rainbow_name': '', 'rainbow_constitution': None}
        key = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        connection.execute(sa.text('UPDATE equipment_configs SET config_key=:key,rainbow_state=:state WHERE id=:id'), {'key': key, 'state': values['rainbow_state'], 'id': row['id']})
    table = sa.Table('equipment_configs', sa.MetaData(), autoload_with=connection)
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint) and [c.name for c in constraint.columns] == ['template_id']:
            constraint.name = 'uq_config_template_legacy'
        elif isinstance(constraint, sa.CheckConstraint):
            constraint.name = 'ck_legacy_enchant' if 'enchant_state' in str(constraint.sqltext) else 'ck_legacy_rainbow'
    with op.batch_alter_table('equipment_configs', copy_from=table, recreate='always') as batch:
        batch.drop_constraint('uq_config_template_legacy', type_='unique')
        batch.drop_constraint('ck_legacy_enchant', type_='check')
        batch.drop_constraint('ck_legacy_rainbow', type_='check')
        batch.alter_column('config_key', nullable=False)
        batch.create_unique_constraint('uq_equipment_configs_config_key', ['config_key'])
        batch.create_check_constraint('ck_config_enchant', "(enchant_state IN ('unknown','none') AND enchant_name='' AND enchant_constitution IS NULL) OR (enchant_state='configured' AND (enchant_name<>'' OR enchant_constitution IS NOT NULL))")
        batch.create_check_constraint('ck_config_rainbow', "(rainbow_state IN ('unknown','none','not_applicable') AND rainbow_name='' AND rainbow_constitution IS NULL) OR (rainbow_state='configured' AND (rainbow_name<>'' OR rainbow_constitution IS NOT NULL))")
        batch.create_check_constraint('ck_config_enchant_value', 'enchant_constitution IS NULL OR enchant_constitution >= 0')
        batch.create_check_constraint('ck_config_rainbow_value', 'rainbow_constitution IS NULL OR rainbow_constitution >= 0')
        batch.create_index('ix_equipment_configs_template_id', ['template_id'])
    op.create_table('warehouse_stacks',
        sa.Column('id', sa.String(), primary_key=True), sa.Column('account_id', sa.String(), sa.ForeignKey('game_accounts.id'), nullable=False),
        sa.Column('config_id', sa.String(), sa.ForeignKey('equipment_configs.id'), nullable=False), sa.Column('quantity', sa.Integer(), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
        sa.UniqueConstraint('account_id', 'config_id', name='uq_warehouse_account_config'),
        sa.CheckConstraint('quantity BETWEEN 0 AND 2147483647', name='ck_warehouse_quantity'), sa.CheckConstraint('version >= 0', name='ck_warehouse_version'))
    op.create_index('ix_warehouse_stacks_account_id', 'warehouse_stacks', ['account_id'])
    op.create_table('raid_drop_notes',
        sa.Column('id', sa.String(), primary_key=True), sa.Column('account_id', sa.String(), sa.ForeignKey('game_accounts.id'), nullable=False),
        sa.Column('config_id', sa.String(), sa.ForeignKey('equipment_configs.id'), nullable=False), sa.Column('quantity', sa.Integer(), nullable=False), sa.Column('version', sa.Integer(), nullable=False), sa.Column('created_at', sa.String(), nullable=False),
        sa.CheckConstraint('quantity BETWEEN 1 AND 2147483647', name='ck_drop_quantity'), sa.CheckConstraint('version >= 0', name='ck_drop_version'))
    op.create_index('ix_raid_drop_notes_account_id', 'raid_drop_notes', ['account_id'])
    op.create_table('raid_memo_states', sa.Column('account_id', sa.String(), sa.ForeignKey('game_accounts.id'), primary_key=True), sa.Column('version', sa.Integer(), nullable=False), sa.CheckConstraint('version >= 0'))
    op.create_table('mutation_receipts',
        sa.Column('account_id', sa.String(), sa.ForeignKey('game_accounts.id'), primary_key=True), sa.Column('request_id', sa.String(), primary_key=True),
        sa.Column('fingerprint', sa.String(), nullable=False), sa.Column('result', sa.Text(), nullable=False))


def downgrade():
    raise RuntimeError('请使用升级前备份及匹配程序恢复，不自动删除库存数据')
