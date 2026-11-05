"""Character growth concurrency and explicit calculation scope; no game values."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN preserves the existing unnamed checks; recreating this table
    # would otherwise drop those legacy constraints during reflection.
    op.add_column('character_slot_settings', sa.Column('version', sa.Integer(),
        sa.CheckConstraint('version >= 0', name='ck_slot_setting_version'),
        nullable=False, server_default='0'))
    op.create_table('character_calculation_settings',
        sa.Column('character_id', sa.String(), sa.ForeignKey('characters.id'), primary_key=True),
        sa.Column('additional_constitution', sa.Integer(), nullable=True),
        sa.Column('scope_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('threshold_cycle_id', sa.String(), sa.ForeignKey('equipment_cycles.id'), nullable=True),
        sa.Column('threshold_signature', sa.String(), nullable=False, server_default=''),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('additional_constitution IS NULL OR additional_constitution >= 0'),
        sa.CheckConstraint('version >= 0'))
    op.execute('INSERT INTO character_calculation_settings (character_id) SELECT id FROM characters')
    for slot in ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring1','ring2'):
        op.execute(sa.text("INSERT INTO character_slot_settings (character_id,slot,refine_level,stone_state,stone_level,version) SELECT id,:slot,NULL,'unknown',NULL,0 FROM characters WHERE NOT EXISTS (SELECT 1 FROM character_slot_settings s WHERE s.character_id=characters.id AND s.slot=:slot)").bindparams(slot=slot))
        op.execute(sa.text("INSERT INTO character_equipment (character_id,slot,config_id,version) SELECT id,:slot,NULL,0 FROM characters WHERE NOT EXISTS (SELECT 1 FROM character_equipment w WHERE w.character_id=characters.id AND w.slot=:slot)").bindparams(slot=slot))


def downgrade():
    op.drop_table('character_calculation_settings')
    with op.batch_alter_table('character_slot_settings', table_args=(
        sa.CheckConstraint('refine_level IS NULL OR refine_level IN (0,4,6,8)'),
        sa.CheckConstraint("(stone_state IN ('unknown','none') AND stone_level IS NULL) OR (stone_state = 'set' AND stone_level BETWEEN 1 AND 8 AND stone_level IS NOT NULL)"),
    )) as batch:
        batch.drop_constraint('ck_slot_setting_version', type_='check')
        batch.drop_column('version')
