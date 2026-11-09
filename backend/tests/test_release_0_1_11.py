import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.models import Character, RaidCDSetting
from backend.raid_cd import period_boundaries, sync_raid_cd
from .conftest import equipment_payload, equipment_url
from .test_inventory import add, daily, path


def identifier():
    return str(uuid4())


def role_path(setup):
    return path(setup) + '/characters/' + setup['character']['id']


@pytest.mark.parametrize('kind', ['account', 'character'])
def test_management_stale_rename_cannot_restore_archive(client, setup, kind):
    url = path(setup) if kind == 'account' else role_path(setup)
    response = client.patch(url, json={'expected_version': 0, 'archived': True})
    assert response.status_code == 200, response.text
    assert client.patch(url, json={'expected_version': 0, 'name': '旧页面改名'}).status_code == 409
    assert client.put(url, json={'name': '旧版本表单', 'archived': False}).status_code == 409
    assert client.patch(url, json={'expected_version': 1, 'name': '明确采用新版本'}).json()['archived']


@pytest.mark.parametrize('kind', ['account', 'character', 'cycle', 'source'])
def test_creation_replay_and_new_intents_allow_same_names(client, setup, kind):
    url = {'account': '/api/accounts', 'character': path(setup) + '/characters', 'cycle': '/api/cycles',
           'source': f"/api/cycles/{setup['cycle']['id']}/sources"}[kind]
    body = {'request_id': identifier(), 'name': '同名示意记录'}
    if kind == 'source':
        body.update(source_type='测试', short_label='示', default_grade=1110)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(url, json=body), range(2)))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    assert responses[0].json()['id'] == responses[1].json()['id']
    assert client.post(url, json={**body, 'name': '改输入但标识相同'}).status_code == 409
    other = client.post(url, json={**body, 'request_id': identifier()})
    assert other.status_code == 201 and other.json()['id'] != responses[0].json()['id']


def test_empty_deletion_keeps_history_and_invalidates_creation_replay(client):
    body = {'name': '误建空账号', 'request_id': identifier()}
    account = client.post('/api/accounts', json=body).json()
    base = '/api/accounts/' + account['id']
    role = client.post(base + '/characters', json={'name': '误建空角色'}).json()
    assert client.delete(base + '?expected_version=0').status_code == 409
    assert client.delete(base + '/characters/' + role['id'] + '?expected_version=0').status_code == 200
    assert client.delete(base + '?expected_version=0').status_code == 200
    assert client.post('/api/accounts', json=body).status_code == 409
    document = client.get('/api/data/export').json()
    assert len(document['tables']['audit_events']) >= 4
    preview = client.post('/api/data/import-preview', content=json.dumps(document).encode())
    assert preview.status_code == 200, preview.text
    assert document['tables']['game_accounts'] == []


def test_used_and_legacy_empty_roles_are_not_permanently_deleted(client, setup):
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    client.delete(equipment_url(setup) + '?expected_version=1')
    assert client.delete(role_path(setup) + '?expected_version=0').status_code == 409
    other = client.post(path(setup) + '/characters', json={'name': '旧版历史不完整'}).json()
    with Session(client.app.state.engine) as session:
        session.get(Character, other['id']).history_complete = False
        session.commit()
    assert client.delete(path(setup) + '/characters/' + other['id'] + '?expected_version=0').status_code == 409


def test_drop_batch_transfer_is_atomic_owned_and_replayable(client, setup):
    first, _ = add(client, setup, 'drops', quantity=3)
    second, _ = add(client, setup, 'drops', quantity=2, actual_grade=1200)
    add(client, setup, quantity=4)
    url = path(setup) + '/drops/batch-transfer'
    body = {'request_id': identifier(), 'rows': [{'id': item['id'], 'expected_version': item['version']} for item in (first, second)]}
    stale = copy.deepcopy(body); stale['rows'][1]['expected_version'] += 1
    assert client.post(url, json=stale).status_code == 409
    assert sum(row['quantity'] for row in daily(client, setup)['drops']) == 5
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['quantity'] == 5
    assert client.post(url, json=body).json() == response.json()
    assert daily(client, setup)['drops'] == []
    assert sum(row['quantity'] for row in daily(client, setup)['warehouse']) == 9
    other = client.post('/api/accounts', json={'name': '隔离账号'}).json()
    note, _ = add(client, setup, 'drops')
    assert client.post('/api/accounts/' + other['id'] + '/drops/batch-transfer', json={
        'request_id': identifier(), 'rows': [{'id': note['id'], 'expected_version': note['version']}]}).status_code == 404


def test_drop_batch_overflow_rolls_back_earlier_rows(client, setup):
    first, _ = add(client, setup, 'drops', quantity=3)
    second, _ = add(client, setup, 'drops', quantity=1, actual_grade=1200)
    add(client, setup, quantity=2147483647, actual_grade=1200)
    response = client.post(path(setup) + '/drops/batch-transfer', json={'request_id': identifier(),
        'rows': [{'id': item['id'], 'expected_version': item['version']} for item in (first, second)]})
    assert response.status_code == 422
    state = daily(client, setup)
    assert len(state['drops']) == 2 and len(state['warehouse']) == 1
    assert state['warehouse'][0]['quantity'] == 2147483647


def test_irrelevant_raid_and_target_changes_do_not_invalidate_batch(client, setup):
    url = role_path(setup) + '/equipment-batch'
    token = client.post(url + '/preview', json={'rows': [{'slot': 'hat', 'action': 'new', 'expected_version': 0,
                                                       'equipment': equipment_payload(setup)}]}).json()
    client.put(role_path(setup) + '/raid-checks/10', json={'cleared': True})
    client.patch(path(setup) + '/preferences', json={'target_grade': 1200})
    response = client.post(url + '/apply', json={'request_id': identifier(), 'preview_id': token['preview_id']})
    assert response.status_code == 200, response.text


def test_shanghai_boundaries_are_calendar_based():
    before = datetime.fromisoformat('2026-11-08T21:59:59+00:00')
    monday = datetime.fromisoformat('2026-11-08T22:00:00+00:00')
    friday = datetime.fromisoformat('2026-11-12T22:00:00+00:00')
    assert period_boundaries(before, True)[0] == '2026-11-05T22:00:00+00:00'
    assert period_boundaries(monday, True)[0] == monday.isoformat()
    assert period_boundaries(monday, False)[0] == monday.isoformat()
    assert period_boundaries(friday, True)[0] == friday.isoformat()
    assert period_boundaries(friday, False)[0] == monday.isoformat()
    assert period_boundaries(datetime.fromisoformat('2027-01-01T06:00:00+08:00'), True)[0] == '2026-12-31T22:00:00+00:00'


def test_raid_toggle_reset_offline_compensation_and_stale_page(client, setup, monkeypatch):
    import backend.raid_cd as cd
    now = datetime.fromisoformat('2026-11-09T05:59:59+08:00')
    monkeypatch.setattr(cd, 'utc_now', lambda: now)
    url = role_path(setup) + '/raid-checks/'
    for difficulty in ('10', '25pt', '25yx'):
        assert client.put(url + difficulty, json={'cleared': True}).status_code == 200
    assert client.get('/api/raid-cd').json()['enabled'] is False
    state = client.put('/api/raid-cd', json={'enabled': True, 'expected_version': 0}).json()
    assert client.get(path(setup) + '/matrix').json()['characters'][0]['raid_10'] is True
    now = datetime.fromisoformat('2026-11-09T06:00:00+08:00')
    sync_raid_cd(client.app.state.engine)
    role = client.get(path(setup) + '/matrix').json()['characters'][0]
    assert not any(role['raid_' + difficulty] for difficulty in ('10', '25pt', '25yx'))
    assert client.put(url + '10', json={'cleared': True, 'period': state['period_10'], 'expected_version': 3}).status_code == 409
    state = client.get('/api/raid-cd').json()
    for difficulty in ('10', '25pt', '25yx'):
        body = {'cleared': True, 'period': state['period_10' if difficulty == '10' else 'period_25'], 'expected_version': role['raid_version']}
        response = client.put(url + difficulty, json=body)
        assert response.status_code == 200, response.text
        role['raid_version'] = response.json()['raid_version']
    sync_raid_cd(client.app.state.engine)
    assert client.get(path(setup) + '/matrix').json()['characters'][0]['raid_10'] is True
    now = datetime.fromisoformat('2026-11-13T06:00:00+08:00')
    role = client.get(path(setup) + '/matrix').json()['characters'][0]
    assert not role['raid_10'] and role['raid_25pt'] and role['raid_25yx']
    now = datetime.fromisoformat('2026-12-01T12:00:00+08:00')
    role = client.get(path(setup) + '/matrix').json()['characters'][0]
    assert not any(role['raid_' + difficulty] for difficulty in ('10', '25pt', '25yx'))
    audits = client.get('/api/data/export').json()['tables']['audit_events']
    count = sum(row['operation'] == 'raid_cd_reset' for row in audits)
    for _ in range(3):
        sync_raid_cd(client.app.state.engine)
    assert sum(row['operation'] == 'raid_cd_reset' for row in client.get('/api/data/export').json()['tables']['audit_events']) == count
    now = datetime.fromisoformat('2026-11-30T01:00:00+08:00')
    assert client.get('/api/raid-cd').json()['clock_warning']
    assert client.put(url + '10', json={'cleared': True, 'period': 'old'}).status_code == 409


def test_creation_rolled_back_by_restore_cannot_run_again(client, setup):
    original = client.get('/api/data/export').content
    body = {'name': '恢复将回滚的账号', 'request_id': identifier()}
    created = client.post('/api/accounts', json=body)
    assert created.status_code == 201
    token = client.post('/api/data/import-preview', content=original).json()
    response = client.post('/api/data/changes/' + token['preview_id'] + '/apply', json={'confirm_replace': True})
    assert response.status_code == 200, response.text
    assert client.post('/api/accounts', json=body).status_code == 409


def test_backup_delete_cleans_only_its_own_sidecars(client, tmp_path):
    name = client.post('/api/data/backups').json()['name']
    file = tmp_path / 'backups' / name
    connection = sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    connection.close()
    response = client.delete('/api/data/backups/' + name)
    assert response.status_code == 200, response.text
    assert not file.exists() and not PathWithSuffix(file, '-wal').exists() and not PathWithSuffix(file, '-shm').exists()
    assert (tmp_path / 'app.sqlite3').exists()


def PathWithSuffix(path, suffix):
    return path.with_name(path.name + suffix)


def test_management_and_empty_delete_replay_with_original_intent(client):
    account = client.post('/api/accounts', json={'name': '管理重试示意'}).json()
    url = '/api/accounts/' + account['id']
    body = {'request_id': identifier(), 'expected_version': 0, 'name': '改名重试示意'}
    first = client.patch(url, json=body)
    assert first.status_code == 200
    assert client.patch(url, json=body).json() == first.json()
    operation = {'request_id': identifier()}
    assert client.request('DELETE', url + '?expected_version=1', json=operation).status_code == 200
    assert client.request('DELETE', url + '?expected_version=1', json=operation).json() == {'deleted': True}


def test_disabled_intervals_and_reenable_preserve_current_checks(client, setup, monkeypatch):
    import backend.raid_cd as cd
    now = datetime.fromisoformat('2026-11-09T12:00:00+08:00')
    monkeypatch.setattr(cd, 'utc_now', lambda: now)
    url = role_path(setup) + '/raid-checks/10'
    client.put(url, json={'cleared': True})
    client.put('/api/raid-cd', json={'enabled': True, 'expected_version': 0})
    client.put('/api/raid-cd', json={'enabled': False, 'expected_version': 1})
    now = datetime.fromisoformat('2026-12-01T12:00:00+08:00')
    assert client.get(path(setup) + '/matrix').json()['characters'][0]['raid_10']
    client.put('/api/raid-cd', json={'enabled': True, 'expected_version': 2})
    assert client.get(path(setup) + '/matrix').json()['characters'][0]['raid_10']
    now = datetime.fromisoformat('2026-12-04T06:00:00+08:00')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: sync_raid_cd(client.app.state.engine), range(2)))
    assert not client.get(path(setup) + '/matrix').json()['characters'][0]['raid_10']
    events = client.get('/api/data/export').json()['tables']['audit_events']
    assert sum(row['operation'] == 'raid_cd_reset' for row in events) == 1


def test_restore_enabled_snapshot_compensates_old_cd_before_display(client, setup, monkeypatch):
    import backend.raid_cd as cd
    now = datetime.fromisoformat('2026-11-09T12:00:00+08:00')
    monkeypatch.setattr(cd, 'utc_now', lambda: now)
    for difficulty in ('10', '25pt'):
        client.put(role_path(setup) + '/raid-checks/' + difficulty, json={'cleared': True})
    client.put('/api/raid-cd', json={'enabled': True, 'expected_version': 0})
    snapshot = client.get('/api/data/export').content
    now = datetime.fromisoformat('2026-11-13T06:00:00+08:00')
    client.get(path(setup) + '/matrix')
    token = client.post('/api/data/import-preview', content=snapshot).json()
    response = client.post('/api/data/changes/' + token['preview_id'] + '/apply', json={'confirm_replace': True})
    assert response.status_code == 200, response.text
    character = client.get(path(setup) + '/matrix').json()['characters'][0]
    assert not character['raid_10'] and character['raid_25pt']


def test_busy_snapshot_is_not_deleted(client, tmp_path):
    name = client.post('/api/data/backups').json()['name']
    path = tmp_path / 'backups' / name
    connection = sqlite3.connect(path)
    try:
        connection.execute('BEGIN')
        connection.execute('SELECT * FROM workspace_settings').fetchall()
        response = client.delete('/api/data/backups/' + name)
        assert response.status_code == 503
        assert path.exists()
    finally:
        connection.close()
    assert client.delete('/api/data/backups/' + name).status_code == 200


def test_single_equipment_save_and_clear_are_replayable(client, setup):
    body = {'request_id': identifier(), 'expected_version': 0, 'equipment': equipment_payload(setup)}
    first = client.put(equipment_url(setup), json=body)
    assert first.status_code == 200
    assert client.put(equipment_url(setup), json=body).json() == first.json()
    operation = {'request_id': identifier()}
    url = equipment_url(setup) + '?expected_version=1'
    cleared = client.request('DELETE', url, json=operation)
    assert cleared.status_code == 200
    assert client.request('DELETE', url, json=operation).json() == cleared.json()
    assert client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]['hat']['version'] == 2


def test_background_resets_without_open_page_or_progress_request(client, setup):
    import time
    from backend.raid_cd import utc_now
    engine = client.app.state.engine
    with Session(engine) as session:
        setting = session.get(RaidCDSetting, 1)
        setting.enabled = True
        setting.cursor_10 = '2000-01-01T00:00:00+00:00'
        setting.cursor_25 = period_boundaries(utc_now(), False)[0]
        setting.changed_at = utc_now().isoformat()
        session.get(Character, setup['character']['id']).raid_10 = True
        session.commit()
    deadline = time.monotonic() + 40
    # Only direct SQL reads; no HTTP reconciliation or explicit scheduler calls.
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            checked = connection.exec_driver_sql('SELECT raid_10 FROM characters WHERE id=?', (setup['character']['id'],)).scalar()
        if not checked:
            return
        time.sleep(0.2)
    pytest.fail('Background reconciliation did not reset the stale CD without a page request')
