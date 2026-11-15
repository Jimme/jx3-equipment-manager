from .conftest import legacy_document, legacy_database_columns
import copy
import json
import sqlite3
from shutil import copyfile

from fastapi.testclient import TestClient

from .conftest import equipment_payload, equipment_url
from backend.domain import EQUIPMENT_TYPES
from backend.main import create_app


def test_cycle_and_ring_socket_rules_keep_original_cycle_and_stone_levels(client, setup):
    account = f"/api/accounts/{setup['account']['id']}"
    cycle_id = setup['cycle']['id']
    client.patch(account + '/preferences', json={'target_grade': 1110, 'comparison_mode': 'combined'})
    for slot, kind in [('hat', 'hat'), ('ring1', 'ring')]:
        response = client.put(equipment_url(setup, slot), json={
            'expected_version': 0, 'equipment': equipment_payload(setup, equipment_type=kind, has_constitution_socket=False)})
        assert response.status_code == 200
    cells = client.get(account + '/matrix').json()['cells'][setup['character']['id']]
    assert cells['hat']['status'] == 'grade_met'
    assert cells['ring1']['status'] == 'met'
    assert cells['ring1']['label'] == 't+'
    growth_path = account + f"/characters/{setup['character']['id']}/growth"
    before = client.get(growth_path).json()
    assert before['slots'][10]['socket_applicable'] is False

    version = client.get(f'/api/cycles/{cycle_id}/data').json()['cycle']['version']
    rule = client.put(f'/api/cycles/{cycle_id}', json={
        'name': setup['cycle']['name'], 'expected_version': version, 'socket_exempt_types': 'hat'})
    assert rule.status_code == 200
    assert client.put(f'/api/cycles/{cycle_id}', json={
        'name': setup['cycle']['name'], 'expected_version': version, 'socket_exempt_types': ''}).status_code == 409
    cells = client.get(account + '/matrix').json()['cells'][setup['character']['id']]
    assert cells['hat']['status'] == 'met' and cells['hat']['label'] == 't+'
    assert client.get(growth_path).json()['slots'][0]['components']['stone']['status'] == 'not_applicable'
    newer = client.post('/api/cycles', json={'name': '有孔新周期'}).json()
    assert newer['socket_exempt_types'] == ''
    assert client.get(account + '/matrix').json()['cells'][setup['character']['id']]['hat']['status'] == 'met'
    assert client.get(growth_path).json()['slots'][0]['settings'] == before['slots'][0]['settings']

    template = client.post('/api/templates', json=equipment_payload(setup, equipment_type='ring')).json()
    assert template['has_constitution_socket'] is False
    stock = client.post(account + '/warehouse', json={
        'request_id': 'socket-rule-stock', 'quantity': 1, 'equipment': equipment_payload(setup)}).json()
    assert stock['equipment']['socket_applicable'] is False
    assert stock['equipment']['has_constitution_socket'] is True
    assert stock['label'] == 't+'
    assert not any(gap['slot'] == 'hat' for gap in client.get(account + '/daily').json()['gaps'])
    cycle = client.get(f'/api/cycles/{cycle_id}/data').json()['cycle']
    preview = client.post(f'/api/cycles/{cycle_id}/copy-preview', json={
        'name': '复制无孔周期', 'expected_version': cycle['version']}).json()
    result = client.post(f"/api/cycles/{cycle_id}/changes/{preview['preview_id']}/apply").json()
    assert client.get(f"/api/cycles/{result['cycle_id']}/data").json()['cycle']['socket_exempt_types'] == 'hat'


def test_previous_cycle_settings_import_and_sqlite_restore_default_to_socket_support(client, setup, tmp_path):
    current = client.get('/api/data/export').json()
    legacy = copy.deepcopy(current)
    legacy = legacy_document(legacy, '0007')
    for cycle in legacy['tables']['equipment_cycles']:
        cycle.pop('has_constitution_sockets')
    response = client.post('/api/data/import-preview', content=json.dumps(legacy).encode())
    assert response.status_code == 200, response.text
    assert not response.json()['unchanged']  # Legacy roles gain conservative history metadata.

    name = client.post('/api/data/backups').json()['name']
    path = tmp_path / 'previous.sqlite3'
    path.write_bytes(client.get('/api/data/backups/' + name).content)
    with sqlite3.connect(path) as connection:
        legacy_database_columns(connection)
        connection.execute('ALTER TABLE equipment_cycles DROP COLUMN has_constitution_sockets')
        connection.execute("UPDATE alembic_version SET version_num='0007'")
    connection.close()
    response = client.post('/api/data/restore-preview', content=path.read_bytes())
    assert response.status_code == 200, response.text
    assert not response.json()['unchanged']  # Legacy roles gain conservative history metadata.


def test_exemption_by_slot_uses_actual_socket_for_growth(client, setup):
    account = f"/api/accounts/{setup['account']['id']}"
    cycle_id = setup['cycle']['id']
    cycle_url = f'/api/cycles/{cycle_id}'
    current = client.get(cycle_url + '/data').json()['cycle']
    patch = {'name': current['name'], 'expected_version': current['version']}
    assert client.put(cycle_url, json={**patch, 'socket_exempt_types': 'ring'}).status_code == 422
    assert client.put(cycle_url, json={**patch, 'socket_exempt_types': 'necklace,legs'}).json()['socket_exempt_types'] == 'legs,necklace'
    client.patch(account + '/preferences', json={'target_grade': 1110, 'comparison_mode': 'combined'})
    for slot in ('legs', 'hat'):
        assert client.put(equipment_url(setup, slot), json={'expected_version': 0, 'equipment': equipment_payload(
            setup, equipment_type=slot, has_constitution_socket=False)}).status_code == 200
    char = setup['character']['id']
    cells = client.get(account + '/matrix').json()['cells'][char]
    assert [cells[slot]['status'] for slot in ('legs', 'hat')] == ['met', 'grade_met']
    assert client.put(equipment_url(setup, 'legs'), json={'expected_version': 1, 'equipment': equipment_payload(
        setup, equipment_type='legs', actual_grade=965, has_constitution_socket=True)}).status_code == 200
    with client.app.state.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE character_slot_settings SET stone_state='set',stone_level=8 WHERE character_id=? AND slot='legs'", (char,))
        conn.exec_driver_sql('INSERT INTO cycle_stone_stats (cycle_id, stone_level, constitution) VALUES (?,8,46)', (cycle_id,))
    report = client.get(account + f'/characters/{char}/growth').json()
    legs = next(row for row in report['slots'] if row['slot'] == 'legs')
    assert legs['socket_applicable'] and legs['components']['stone']['value'] == 46
    assert client.get(account + '/matrix').json()['cells'][char]['legs']['equipment']['has_constitution_socket']


def test_0010_import_migrates_cycle_exemption_and_preserves_template_flags(client, setup, tmp_path):
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)}).status_code == 200
    current = client.get('/api/data/export').json()
    legacy = legacy_document(current, '0010')
    legacy['tables']['equipment_cycles'][0]['has_constitution_sockets'] = False
    from backend.data_management import sqlite_document, validate_document
    expected = ','.join(EQUIPMENT_TYPES[:-1])
    assert validate_document(legacy, tmp_path)['equipment_cycles'][0]['socket_exempt_types'] == expected
    backup = client.post('/api/data/backups').json()['name']
    path = tmp_path / 'old-0010.sqlite3'
    path.write_bytes(client.get('/api/data/backups/' + backup).content)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE equipment_cycles DROP COLUMN socket_exempt_types')
        db.execute('UPDATE equipment_cycles SET has_constitution_sockets=0')
        db.execute("UPDATE alembic_version SET version_num='0010'")
    db.close()
    restored = sqlite_document(path.read_bytes(), tmp_path)
    assert restored['equipment_cycles'][0]['socket_exempt_types'] == expected
    assert restored['equipment_templates'][0]['has_constitution_socket'] is True
    live = tmp_path / 'migrated'
    live.mkdir()
    copyfile(path, live / 'app.sqlite3')
    from scripts.verify_update import verify_tables
    with TestClient(create_app(live)) as migrated:
        assert verify_tables(legacy, migrated.get('/api/data/export').json()) == 0
        before = migrated.get('/api/data/backups').json()
        assert any(item['name'].startswith('before-migration-') for item in before)
