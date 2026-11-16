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
        'name': '复制无孔周期', 'expected_version': cycle['version'], 'source_ids': [setup['source']['id']]}).json()
    result = client.post(f"/api/cycles/{cycle_id}/changes/{preview['preview_id']}/apply").json()
    assert client.get(f"/api/cycles/{result['cycle_id']}/data").json()['cycle']['socket_exempt_types'] == 'hat'


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
