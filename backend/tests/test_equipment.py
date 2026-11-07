from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from backend.database import backup_database, make_engine, migrate
from backend.domain import SLOT_KEYS, compare, label
from backend.main import create_app
from backend.models import AuditEvent, SlotSetting
from .conftest import equipment_payload, equipment_url


@pytest.mark.parametrize('grade,socket,green,status,reasons', [
    (965, True, True, 'gap', ['品级不足']),
    (1110, False, True, 'gap', ['同品级缺体质孔']),
    (1110, True, False, 'gap', ['同品级绿字未达标']),
    (1110, False, False, 'gap', ['同品级缺体质孔', '同品级绿字未达标']),
    (1110, True, True, 'met', []),
    (1200, False, False, 'met', []),
    (None, False, False, 'missing', ['未装备']),
])
def test_strict_boundaries(grade, socket, green, status, reasons):
    assert compare(grade, socket, green, 1110, 'strict') == {'status': status, 'reasons': reasons}


@pytest.mark.parametrize('socket,green', [(False, False), (False, True), (True, False), (True, True)])
def test_grade_mode(socket, green):
    assert compare(1110, socket, green, 1110, 'grade')['status'] == 'met'


def test_unknown_target_and_labels():
    assert compare(1110, True, True, None, 'strict')['status'] == 'unset'
    assert [label('pt', s, g) for s, g in [(False, False), (False, True), (True, False), (True, True)]] == ['pt-', 'pt-+', 'pt', 'pt+']


@pytest.mark.parametrize('grade,socket,green,status', [
    (965, True, True, 'gap'),
    (1110, False, True, 'grade_met'),
    (1110, True, False, 'grade_met'),
    (1110, False, False, 'grade_met'),
    (1110, True, True, 'met'),
    (1200, False, False, 'met'),
    (None, False, False, 'missing'),
])
def test_combined_boundaries(grade, socket, green, status):
    result = compare(grade, socket, green, 1110, 'combined')
    assert result['status'] == status
    assert result['reasons'] == compare(grade, socket, green, 1110, 'strict')['reasons']
    assert compare(grade, socket, green, None, 'combined')['status'] == 'unset'


def test_combined_preference_and_matrix(client, setup):
    account = setup['account']['id']
    assert client.put(f'/api/accounts/{account}/preferences', json={'target_grade': 1110, 'comparison_mode': 'combined'}).status_code == 200
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, has_constitution_socket=False)}).status_code == 200
    matrix = client.get(f'/api/accounts/{account}/matrix').json()
    assert matrix['account']['comparison_mode'] == 'combined'
    assert matrix['cells'][setup['character']['id']]['hat']['status'] == 'grade_met'
    assert matrix['constitution']['stacks'] is None


def test_clean_workspace_no_preset_game_data(client):
    workspace = client.get('/api/workspace').json()
    assert workspace['accounts'] == workspace['cycles'] == []
    assert workspace['active_cycle_id'] is None
    assert [s['key'] for s in workspace['slots']] == SLOT_KEYS
    assert client.get('/api/templates').json() == []


def test_no_constitution_required(client, setup):
    result = client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    assert result.status_code == 200
    matrix = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    cell = matrix['cells'][setup['character']['id']]['hat']
    assert cell['equipment']['base_constitution_override'] is None
    assert matrix['constitution']['total'] is None
    assert matrix['constitution']['stacks'] is None
    assert cell['status'] == 'unset'
    assert len(matrix['cells'][setup['character']['id']]) == 12


def test_source_defaults_and_snapshot_do_not_retroactively_change(client, setup):
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, actual_grade=None)}).status_code == 200
    source = setup['source']
    assert client.put(f"/api/cycles/{setup['cycle']['id']}/sources/{source['id']}", json={
        'source_type': '特殊', 'name': '新来源名', 'short_label': 'new', 'default_grade': 1200,
    }).status_code == 200
    template = client.get('/api/templates').json()[0]
    assert (template['actual_grade'], template['source_name'], template['short_label']) == (1110, '测试来源', 't')
    assert client.put(equipment_url(setup, 'chest'), json={'expected_version': 0, 'equipment': equipment_payload(setup, equipment_type='chest', actual_grade=None)}).status_code == 200
    matrix = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    assert matrix['cells'][setup['character']['id']]['chest']['equipment']['actual_grade'] == 1200


def test_numeric_grade_overrides_source(client, setup):
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, actual_grade=942)}).status_code == 200
    template = client.get('/api/templates').json()[0]
    assert template['actual_grade'] == 942


@pytest.mark.parametrize('field,value', [('actual_grade', 0), ('actual_grade', -1), ('actual_grade', 1.5), ('actual_grade', '1110'), ('actual_grade', True), ('base_constitution_override', -1), ('equipment_type', 'invalid')])
def test_invalid_equipment_is_not_saved(client, setup, field, value):
    result = client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, **{field: value})})
    assert result.status_code == 422
    assert client.get('/api/templates').json() == []


def test_explicit_zero_is_distinct_from_missing(client, setup):
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, base_constitution_override=0)})
    assert client.get('/api/templates').json()[0]['base_constitution_override'] == 0
    assert client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()['constitution']['stacks'] is None


def test_cross_account_edit_rejected(client, setup):
    other = client.post('/api/accounts', json={'name': '其他账号'}).json()
    path = equipment_url(setup).replace(setup['account']['id'], other['id'])
    assert client.put(path, json={'expected_version': 0, 'equipment': equipment_payload(setup)}).status_code == 404
    assert client.put(f"/api/accounts/{other['id']}/characters/{setup['character']['id']}", json={'name': '篡改'}).status_code == 404
    assert client.get(f"/api/accounts/{other['id']}/matrix").json()['characters'] == []
    assert client.get('/api/templates').json() == []


def test_rings_are_independent_and_templates_reusable(client, setup):
    template = client.post('/api/templates', json=equipment_payload(setup, equipment_type='ring')).json()
    for slot in ('ring1', 'ring2'):
        assert client.put(equipment_url(setup, slot), json={'expected_version': 0, 'template_id': template['id']}).status_code == 200
    matrix = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    assert matrix['cells'][setup['character']['id']]['ring1']['version'] == 1
    assert matrix['cells'][setup['character']['id']]['ring2']['version'] == 1
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'template_id': template['id']}).status_code == 422
    assert client.delete(equipment_url(setup, 'ring1') + '?expected_version=1').status_code == 200
    matrix = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    assert matrix['cells'][setup['character']['id']]['ring2']['equipment']['id'] == template['id']


def test_stale_write_rejected_and_audited(client, setup):
    body = {'expected_version': 0, 'equipment': equipment_payload(setup)}
    assert client.put(equipment_url(setup), json=body).status_code == 200
    assert client.put(equipment_url(setup), json=body).status_code == 409
    assert client.delete(equipment_url(setup) + '?expected_version=0').status_code == 409
    with client.app.state.engine.connect() as conn:
        events = conn.execute(select(AuditEvent.snapshots).where(AuditEvent.operation == 'replace_equipment')).scalars().all()
        assert len(events) == 1
        assert json.loads(events[0])['after']['version'] == 1


def test_concurrent_same_slot_only_one_success(client, setup):
    body = {'expected_version': 0, 'equipment': equipment_payload(setup)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.put(equipment_url(setup), json=body).status_code, range(2)))
    assert sorted(results) == [200, 409]


def test_slot_settings_survive_replacement(client, setup):
    engine = client.app.state.engine
    with engine.begin() as conn:
        conn.execute(text("UPDATE character_slot_settings SET refine_level=6,stone_state='set',stone_level=8 WHERE character_id=:id AND slot='hat'"), {'id': setup['character']['id']})
    for version, grade in [(0, 965), (1, 1110)]:
        assert client.put(equipment_url(setup), json={'expected_version': version, 'equipment': equipment_payload(setup, actual_grade=grade)}).status_code == 200
    with engine.connect() as conn:
        setting = conn.execute(select(SlotSetting).where(SlotSetting.character_id == setup['character']['id'], SlotSetting.slot == 'hat')).mappings().one()
        assert (setting['refine_level'], setting['stone_state'], setting['stone_level']) == (6, 'set', 8)


def test_cycle_switch_keeps_old_equipment(client, setup):
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    new = client.post('/api/cycles', json={'name': '新测试周期'}).json()
    assert client.get('/api/workspace').json()['active_cycle_id'] == new['id']
    template = client.get('/api/templates').json()[0]
    assert template['cycle_origin_id'] == setup['cycle']['id']
    assert template['actual_grade'] == 1110
    payload = equipment_payload(setup, cycle_origin_id=new['id'])
    assert client.post('/api/templates', json=payload).status_code == 422


def test_archive_restore_and_order(client, setup):
    account_id = setup['account']['id']
    character_id = setup['character']['id']
    path = f'/api/accounts/{account_id}/characters/{character_id}'
    assert client.put(path, json={'name': '改名', 'sort_order': 2, 'archived': True}).status_code == 200
    assert client.get(f'/api/accounts/{account_id}/matrix').json()['characters'] == []
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)}).status_code == 409
    client.put(path, json={'name': '改名', 'sort_order': 2, 'archived': False})
    client.post(f'/api/accounts/{account_id}/characters', json={'name': '排在前面', 'sort_order': 1})
    assert [c['name'] for c in client.get(f'/api/accounts/{account_id}/matrix').json()['characters']] == ['排在前面', '改名']


def test_persistence_backup_and_migrations(tmp_path):
    directory = tmp_path / 'live'
    with TestClient(create_app(directory)) as client:
        account = client.post('/api/accounts', json={'name': '持久化测试'}).json()
        engine = client.app.state.engine
        with engine.connect() as connection:
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
            assert connection.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
        backup = backup_database(directory / 'app.sqlite3')
    with TestClient(create_app(directory)) as client:
        assert client.get('/api/workspace').json()['accounts'][0]['id'] == account['id']
    restore = tmp_path / 'restored'
    restore.mkdir()
    (restore / 'app.sqlite3').write_bytes(backup.read_bytes())
    with TestClient(create_app(restore)) as client:
        assert client.get('/api/workspace').json()['accounts'][0]['id'] == account['id']
    engine = make_engine(directory)
    migrate(engine)
    migrate(engine)
    assert 'alembic_version' in inspect(engine).get_table_names()
    with sqlite3.connect(directory / 'app.sqlite3') as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    engine.dispose()


def test_api_route_missing_returns_json(client):
    response = client.get('/api/unknown')
    assert response.status_code == 404
    assert response.headers['content-type'] == 'application/json'


def test_migrate_legacy_database_preserves_references_and_constraints(tmp_path):
    from alembic import command
    from alembic.config import Config
    from backend.database import ROOT
    from backend.models import Account, Cycle, EquipmentConfig, Template, Workspace, WornEquipment
    from sqlalchemy.exc import IntegrityError

    engine = make_engine(tmp_path)
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend' / 'migrations'))
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, '0001')
        conn.exec_driver_sql("INSERT INTO equipment_cycles (id,name,archived) VALUES ('cycle','旧周期',0)")
        conn.exec_driver_sql("INSERT INTO workspace_settings (id,active_cycle_id) VALUES (1,'cycle')")
        conn.execute(Account.__table__.insert().values(id='account', name='已有账号', target_grade=1110))
        conn.exec_driver_sql(
            'INSERT INTO characters (id, account_id, name, sort_order, archived) VALUES (?, ?, ?, 0, 0)',
            ('character', 'account', '已有角色'),
        )
        conn.execute(Template.__table__.insert().values(id='template', cycle_origin_id='cycle', source_type='测试', source_name='旧来源', short_label='旧', equipment_type='hat', actual_grade=1110, has_constitution_socket=False, green_stats_ok=True))
        conn.exec_driver_sql("INSERT INTO equipment_configs (id,template_id,enchant_state,rainbow_state) VALUES ('config','template','unknown','not_applicable')")
        conn.execute(WornEquipment.__table__.insert().values(character_id='character', slot='hat', config_id='config', version=3))
        conn.execute(AuditEvent.__table__.insert().values(id='audit', created_at='2026-11-04', account_id='account', operation='test', entity_id='character', snapshots='{}'))
    migrate(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall() == []
        assert conn.exec_driver_sql('SELECT comparison_mode FROM game_accounts').scalar() == 'strict'
        assert conn.exec_driver_sql('SELECT version FROM character_equipment').scalar() == 3
        assert conn.exec_driver_sql('SELECT account_id FROM audit_events').scalar() == 'account'
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == '0009'
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE game_accounts SET comparison_mode='combined'")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql('UPDATE game_accounts SET target_grade=-1')
    backup = list((tmp_path / 'backups').glob('before-migration-*.sqlite3'))
    assert len(backup) == 1
    with sqlite3.connect(backup[0]) as conn:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0001'
        assert conn.execute('SELECT actual_grade FROM equipment_templates').fetchone()[0] == 1110
    engine.dispose()
    with TestClient(create_app(tmp_path)) as client:
        matrix = client.get('/api/accounts/account/matrix').json()
        assert matrix['account']['comparison_mode'] == 'combined'
        assert matrix['cells']['character']['hat']['status'] == 'grade_met'
