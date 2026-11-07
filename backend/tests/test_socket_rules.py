import copy
import json
import sqlite3

from .conftest import equipment_payload, equipment_url


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
        'name': setup['cycle']['name'], 'expected_version': version, 'has_constitution_sockets': False})
    assert rule.status_code == 200
    assert client.put(f'/api/cycles/{cycle_id}', json={
        'name': setup['cycle']['name'], 'expected_version': version, 'has_constitution_sockets': True}).status_code == 409
    cells = client.get(account + '/matrix').json()['cells'][setup['character']['id']]
    assert cells['hat']['status'] == 'met' and cells['hat']['label'] == 't+'
    assert client.get(growth_path).json()['slots'][0]['components']['stone']['status'] == 'not_applicable'
    newer = client.post('/api/cycles', json={'name': '有孔新周期'}).json()
    assert newer['has_constitution_sockets'] is True
    assert client.get(account + '/matrix').json()['cells'][setup['character']['id']]['hat']['status'] == 'met'
    assert client.get(growth_path).json()['slots'][0]['settings'] == before['slots'][0]['settings']

    template = client.post('/api/templates', json=equipment_payload(setup, equipment_type='ring')).json()
    assert template['has_constitution_socket'] is False
    stock = client.post(account + '/warehouse', json={
        'request_id': 'socket-rule-stock', 'quantity': 1, 'equipment': equipment_payload(setup)}).json()
    assert stock['equipment']['socket_applicable'] is False
    assert stock['label'] == 't+'
    assert not any(gap['slot'] == 'hat' for gap in client.get(account + '/daily').json()['gaps'])
    cycle = client.get(f'/api/cycles/{cycle_id}/data').json()['cycle']
    preview = client.post(f'/api/cycles/{cycle_id}/copy-preview', json={
        'name': '复制无孔周期', 'expected_version': cycle['version']}).json()
    result = client.post(f"/api/cycles/{cycle_id}/changes/{preview['preview_id']}/apply").json()
    assert client.get(f"/api/cycles/{result['cycle_id']}/data").json()['cycle']['has_constitution_sockets'] is False


def test_previous_cycle_settings_import_and_sqlite_restore_default_to_socket_support(client, setup, tmp_path):
    current = client.get('/api/data/export').json()
    legacy = copy.deepcopy(current)
    legacy['schema_revision'] = '0007'
    for cycle in legacy['tables']['equipment_cycles']:
        cycle.pop('has_constitution_sockets')
    response = client.post('/api/data/import-preview', content=json.dumps(legacy).encode())
    assert response.status_code == 200, response.text
    assert response.json()['unchanged']
    name = client.post('/api/data/backups').json()['name']
    path = tmp_path / 'previous.sqlite3'
    path.write_bytes(client.get('/api/data/backups/' + name).content)
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE equipment_cycles DROP COLUMN has_constitution_sockets')
        connection.execute("UPDATE alembic_version SET version_num='0007'")
    response = client.post('/api/data/restore-preview', content=path.read_bytes())
    assert response.status_code == 200, response.text
    assert response.json()['unchanged']
