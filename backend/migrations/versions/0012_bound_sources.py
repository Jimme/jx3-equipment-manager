"""Store equipment grade and origin once, on the source."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql('SELECT 1 FROM equipment_sources LIMIT 1').first() or connection.exec_driver_sql('SELECT 1 FROM equipment_templates LIMIT 1').first():
        raise RuntimeError('旧数据库含来源或装备。0.1.14 请使用独立空白数据目录初始化。')
    op.drop_index('ix_equipment_templates_cycle_origin_id', table_name='equipment_templates')
    op.drop_table('equipment_templates')
    op.drop_table('equipment_sources')
    op.create_table(
        'equipment_sources',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('cycle_id', sa.String(), sa.ForeignKey('equipment_cycles.id'), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('short_label', sa.String(), nullable=False),
        sa.Column('actual_grade', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint('actual_grade BETWEEN 1 AND 2147483647'),
    )
    op.create_index('ix_equipment_sources_cycle_id', 'equipment_sources', ['cycle_id'])
    op.create_table(
        'equipment_templates',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('source_id', sa.String(), sa.ForeignKey('equipment_sources.id'), nullable=False),
        sa.Column('equipment_type', sa.String(), nullable=False),
        sa.Column('has_constitution_socket', sa.Boolean(), nullable=False),
        sa.Column('green_stats_ok', sa.Boolean(), nullable=False),
        sa.Column('base_constitution_override', sa.Integer()),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.CheckConstraint("equipment_type IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring')"),
        sa.CheckConstraint('has_constitution_socket IN (0,1) AND green_stats_ok IN (0,1)'),
        sa.CheckConstraint('base_constitution_override IS NULL OR base_constitution_override >= 0'),
    )
    op.create_index('ix_equipment_templates_source_id', 'equipment_templates', ['source_id'])
    op.execute("""
        CREATE TRIGGER bound_source_grade
        BEFORE UPDATE OF actual_grade, cycle_id ON equipment_sources
        WHEN (NEW.actual_grade != OLD.actual_grade OR NEW.cycle_id != OLD.cycle_id)
             AND EXISTS (SELECT 1 FROM equipment_templates WHERE source_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'source already referenced');
        END
    """)


def downgrade():
    raise RuntimeError('请使用 0.1.13 独立数据库回退')
