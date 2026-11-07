import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.main import create_app
from backend.drop_merge import merge_drop_rows
from .conftest import equipment_payload, equipment_url
from .test_inventory import add, daily, path


def batch_url(setup):
    return path(setup) + f"/characters/{setup['character']['id']}/equipment-batch"


def manual(setup, slot='hat', **values):
    return {'slot': slot, 'action': 'new', 'expected_version': 0,
            'equipment': equipment_payload(setup, equipment_type='ring' if slot.startswith('ring') else slot, **values)}


def preview(client, setup, rows):
    response = client.post(batch_url(setup) + '/preview', json={'rows': rows})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, setup, token, body=None):
    return client.post(batch_url(setup) + '/apply', json=body or {'request_id': str(uuid4()), 'preview_id': token['preview_id']})


@pytest.mark.parametrize('target', ['warehouse', 'drops'])
def test_inventory_edit_split_merge_replay_and_shared_reference(client, setup, target):
    stock, original = add(client, setup, target, quantity=4)
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    other, _ = add(client, setup, target, quantity=2, actual_grade=1200)
    body = {**original, 'request_id': str(uuid4()), 'expected_version': stock['version'], 'quantity': 1,
            'equipment': equipment_payload(setup, actual_grade=1200)}
    url = path(setup) + f"/{target}/{stock['id']}/edit"
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['id'] == other['id'] and response.json()['quantity'] == 3
    assert client.post(url, json=body).json() == response.json()
    items = daily(client, setup)[target]
    assert sorted(row['quantity'] for row in items) == [3, 3]
    assert client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]['hat']['equipment']['actual_grade'] == 1110
    assert client.post(url, json={**body, 'request_id': str(uuid4())}).status_code == 409
    current = next(row for row in items if row['id'] == stock['id'])
    all_body = {**body, 'request_id': str(uuid4()), 'expected_version': current['version'], 'quantity': 3}
    assert client.post(url, json=all_body).json()['quantity'] == 6
    assert len(daily(client, setup)[target]) == 1
    assert any(row['title'].startswith('更正') for row in client.get(path(setup) + '/history').json())


def test_inventory_edit_noop_and_account_boundary(client, setup):
    stock, original = add(client, setup, quantity=2)
    url = path(setup) + f"/warehouse/{stock['id']}/edit"
    before = len(client.get('/api/data/export').json()['tables']['audit_events'])
    body = {**original, 'request_id': str(uuid4()), 'expected_version': stock['version']}
    assert client.post(url, json=body).json()['version'] == stock['version']
    assert len(client.get('/api/data/export').json()['tables']['audit_events']) == before
    other = client.post('/api/accounts', json={'name': '隔离账号'}).json()
    assert client.post(url.replace(setup['account']['id'], other['id']), json=body).status_code == 404


def test_batch_manual_preview_backup_atomic_apply_replay_and_settings(client, setup):
    rows = [manual(setup, slot) for slot in ['hat', 'weapon', 'ring1', 'ring2']]
    rows[1]['choices'] = {'enchant': {'state': 'configured', 'name': '测试附魔', 'constitution': None}, 'rainbow': {'state': 'none'}}
    token = preview(client, setup, rows)
    assert len(token['changes']) == 4 and token['changes'][1]['after_choices']['enchant']['constitution'] is None
    assert client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]['hat']['equipment'] is None
    body = {'request_id': str(uuid4()), 'preview_id': token['preview_id']}
    response = apply(client, setup, token, body)
    assert response.status_code == 200, response.text
    assert apply(client, setup, token, body).json() == response.json()
    backup = client.get('/api/data/backups/' + response.json()['backup'])
    assert backup.status_code == 200 and backup.content.startswith(b'SQLite format')
    cells = client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]
    assert cells['hat']['version'] == 1 and cells['ring1']['equipment']['has_constitution_socket'] is False
    assert cells['weapon']['choices']['enchant']['constitution'] is None
    rows = [{**row, 'action': 'edit', 'expected_version': 1} for row in rows]
    assert preview(client, setup, rows)['unchanged']
    clear = preview(client, setup, [{'slot': 'hat', 'action': 'clear', 'expected_version': 1}])
    assert apply(client, setup, clear).status_code == 200
    exported = client.get('/api/data/export').json()['tables']
    assert all(row['refine_level'] is None for row in exported['character_slot_settings'])


def test_batch_aggregates_two_rings_and_rejects_insufficient_stock(client, setup):
    stock, _ = add(client, setup, quantity=1, equipment_type='ring')
    rows = [{'slot': slot, 'action': 'warehouse', 'expected_version': 0, 'warehouse_id': stock['id'],
             'expected_stock_version': stock['version']} for slot in ['ring1', 'ring2']]
    assert client.post(batch_url(setup) + '/preview', json={'rows': rows}).status_code == 409
    stock, _ = add(client, setup, quantity=1, equipment_type='ring')
    for row in rows:
        row['expected_stock_version'] = stock['version']
    token = preview(client, setup, rows)
    assert token['deductions'] == {stock['id']: 2}
    assert apply(client, setup, token).status_code == 200
    assert daily(client, setup)['warehouse'] == []


@pytest.mark.parametrize('change', ['worn', 'cycle', 'stock'])
def test_batch_stale_preview_never_partially_applies(client, setup, change):
    stock, _ = add(client, setup, quantity=2, equipment_type='ring')
    rows = [manual(setup), {'slot': 'ring1', 'action': 'warehouse', 'expected_version': 0,
            'warehouse_id': stock['id'], 'expected_stock_version': stock['version']}]
    token = preview(client, setup, rows)
    if change == 'worn':
        client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, actual_grade=965)})
    elif change == 'cycle':
        cycle = client.get(f"/api/cycles/{setup['cycle']['id']}/data").json()['cycle']
        client.put(f"/api/cycles/{cycle['id']}", json={'name': cycle['name'], 'expected_version': cycle['version'], 'has_constitution_sockets': False})
    else:
        add(client, setup, quantity=1, equipment_type='ring')
    assert apply(client, setup, token).status_code == 409
    cells = client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]
    assert cells['ring1']['equipment'] is None
    assert cells['hat']['equipment'] is None if change != 'worn' else cells['hat']['equipment']['actual_grade'] == 965


def test_batch_invalid_later_row_and_backup_failure_roll_back(client, setup, monkeypatch):
    bad = manual(setup, 'weapon'); bad['equipment']['equipment_type'] = 'hat'
    assert client.post(batch_url(setup) + '/preview', json={'rows': [manual(setup), bad]}).status_code == 422
    token = preview(client, setup, [manual(setup), manual(setup, 'weapon')])
    def fail(*args):
        raise OSError('simulated disk full')
    monkeypatch.setattr('backend.equipment_batch.backup_database', fail)
    assert apply(client, setup, token).status_code == 503
    cells = client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]
    assert all(cell['equipment'] is None for cell in cells.values())
    assert client.get('/api/data/export').json()['tables']['equipment_templates'] == []


def test_batch_mid_apply_failure_and_concurrent_replay_are_atomic(client, setup, monkeypatch):
    import backend.equipment_batch as batch
    token = preview(client, setup, [manual(setup), manual(setup, 'weapon')])
    original = batch.replace_worn
    def fail_second(session, worn, config_id, expected_version):
        if worn.slot == 'weapon':
            raise RuntimeError('simulated failure')
        return original(session, worn, config_id, expected_version)
    monkeypatch.setattr(batch, 'replace_worn', fail_second)
    with pytest.raises(RuntimeError):
        apply(client, setup, token)
    assert client.get('/api/data/export').json()['tables']['equipment_templates'] == []
    monkeypatch.setattr(batch, 'replace_worn', original)
    body = {'request_id': str(uuid4()), 'preview_id': token['preview_id']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: apply(client, setup, token, body), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]['hat']['version'] == 1


def test_batch_scope_and_global_restore_endpoint_reject_batch_token(client, setup):
    token = preview(client, setup, [manual(setup)])
    other = client.post(path(setup) + '/characters', json={'name': '另一个'}).json()
    url = batch_url(setup).replace(setup['character']['id'], other['id'])
    assert client.post(url + '/apply', json={'request_id': str(uuid4()), 'preview_id': token['preview_id']}).status_code == 404
    assert client.post('/api/data/changes/' + token['preview_id'] + '/apply', json={'confirm_replace': True}).status_code == 404


def test_history_query_count_is_bounded_and_templates_have_held_filter(client, setup):
    for index in range(17):
        assert client.put(equipment_url(setup), json={'expected_version': index,
            'equipment': equipment_payload(setup, actual_grade=1000 + index)}).status_code == 200
    queries = []
    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(client.app.state.engine, 'before_cursor_execute', count)
    try:
        history = client.get(path(setup) + '/history').json()
    finally:
        event.remove(client.app.state.engine, 'before_cursor_execute', count)
    assert len(history) == 17 and len(queries) <= 5
    assert '1016品' in history[0]['summary']
    assert len(client.get('/api/templates?equipment_type=hat').json()) == 17
    assert len(client.get('/api/templates?equipment_type=hat&include_history=false').json()) == 1
    assert client.get(f"/api/cycles/{setup['cycle']['id']}/socket-impact").json()['worn'] == 1


def test_old_drop_migration_and_json_import_preserve_quantities_and_audit(client, setup, tmp_path):
    item, _ = add(client, setup, 'drops', quantity=2)
    document = client.get('/api/data/export').json()
    document['schema_revision'] = '0008'
    duplicate = {**document['tables']['raid_drop_notes'][0], 'id': str(uuid4()), 'quantity': 3}
    document['tables']['raid_drop_notes'].append(duplicate)
    response = client.post('/api/data/import-preview', content=json.dumps(document).encode())
    assert response.status_code == 200, response.text
    assert client.post('/api/data/changes/' + response.json()['preview_id'] + '/apply', json={'confirm_replace': True}).status_code == 200
    assert daily(client, setup)['drops'][0]['quantity'] == 5
    assert client.get(path(setup) + '/history').json()[0]['title'] == '归并相同掉落'
    source = tmp_path / 'migration'
    source.mkdir()
    backup = client.post('/api/data/backups').json()['name']
    database = source / 'app.sqlite3'
    database.write_bytes(client.get('/api/data/backups/' + backup).content)
    migration_before = client.get('/api/data/export').json()
    migration_before['schema_revision'] = '0008'
    with sqlite3.connect(database) as connection:
        connection.executescript('ALTER TABLE raid_drop_notes RENAME TO old_drops; CREATE TABLE raid_drop_notes AS SELECT * FROM old_drops; DROP TABLE old_drops;')
        current = connection.execute('SELECT * FROM raid_drop_notes').fetchone()
        columns = [row[1] for row in connection.execute('PRAGMA table_info(raid_drop_notes)')]
        another = dict(zip(columns, current)); another.update(id=str(uuid4()), quantity=7)
        migration_before['tables']['raid_drop_notes'].append(another)
        connection.execute('INSERT INTO raid_drop_notes VALUES (' + ','.join('?' for _ in columns) + ')', [another[key] for key in columns])
        connection.execute("UPDATE alembic_version SET version_num='0008'")
    with TestClient(create_app(source)) as migrated:
        rows = daily(migrated, setup)['drops']
        assert len(rows) == 1 and rows[0]['quantity'] == 12
        with sqlite3.connect(database) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert migrated.get('/api/data/export').json()['schema_revision'] == '0009'
        from scripts.verify_update import verify_tables
        assert verify_tables(migration_before, migrated.get('/api/data/export').json()) == 1


def test_deployment_verifier_checks_expected_defaults_and_detects_real_changes(client, setup):
    from scripts.verify_update import expected_tables, verify_tables
    before = client.get('/api/data/export').json()
    before['schema_revision'] = '0007'
    for cycle in before['tables']['equipment_cycles']:
        cycle.pop('has_constitution_sockets')
    expected, _ = expected_tables(before)
    after = {'schema_revision': '0009', 'tables': expected}
    assert verify_tables(before, after) == 0
    after['tables']['equipment_cycles'][0]['has_constitution_sockets'] = False
    with pytest.raises(AssertionError, match='Business data differs'):
        verify_tables(before, after)


def test_drop_merge_overflow_is_explicit():
    rows = [{'id': str(index), 'created_at': str(index), 'account_id': 'a', 'config_id': 'c', 'quantity': 2147483647, 'version': 0} for index in range(2)]
    with pytest.raises(ValueError, match='数量上限'):
        merge_drop_rows(rows)
