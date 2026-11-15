import pytest

from .conftest import equipment_payload, equipment_url
from .test_inventory import add, daily, path


def test_clear_checks_full_snapshot_and_preserves_other_records(client, setup):
    hat, _ = add(client, setup, quantity=3)
    other = {'account': client.post('/api/accounts', json={'name': '另一个账号'}).json(), 'cycle': setup['cycle'], 'source': setup['source']}
    add(client, other, quantity=5)
    add(client, setup, target='drops', quantity=4)
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    url = path(setup) + '/warehouse/clear'
    payload = {'request_id': 'clear-confirm', 'versions': {hat['id']: hat['version']}}
    ring, _ = add(client, setup, quantity=2, equipment_type='ring')
    assert client.post(url, json=payload).status_code == 409
    assert not client.get('/api/data/backups').json()
    payload['versions'][ring['id']] = ring['version']
    saved = client.post(url, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['configurations'] == 2 and saved.json()['quantity'] == 5
    assert daily(client, setup)['warehouse'] == []
    assert daily(client, setup)['drops'][0]['quantity'] == 4
    assert daily(client, other)['warehouse'][0]['quantity'] == 5
    assert client.get(path(setup) + '/matrix').json()['cells'][setup['character']['id']]['hat']['equipment']
    preview = client.post('/api/data/backups/' + saved.json()['backup'] + '/restore-preview')
    assert preview.status_code == 200, preview.text
    assert preview.json()['after']['共享仓库件数'] == 10
    assert client.post(url, json={'request_id': 'empty', 'versions': {}}).status_code == 422
    add(client, setup, quantity=1)
    assert client.post(url, json=payload).json() == saved.json()
    assert daily(client, setup)['warehouse'][0]['quantity'] == 1
    assert len(client.get('/api/data/backups').json()) == 1


def test_clear_fails_safely_if_backup_fails(client, setup, monkeypatch):
    item, _ = add(client, setup, quantity=2)

    def fail(*args):
        raise OSError('simulated disk failure')

    monkeypatch.setattr('backend.inventory.backup_database', fail)
    result = client.post(path(setup) + '/warehouse/clear', json={'request_id': 'clear-failed', 'versions': {item['id']: item['version']}})
    assert result.status_code == 503
    assert daily(client, setup)['warehouse'] == [item]


def test_clear_rolls_back_after_stock_write_if_audit_fails(client, setup, monkeypatch):
    item, _ = add(client, setup, quantity=2)
    url = path(setup) + '/warehouse/clear'
    body = {'request_id': 'interrupted-clear', 'versions': {item['id']: item['version']}}

    def fail(session, *args):
        session.flush()
        raise RuntimeError('simulated failure after write')

    with monkeypatch.context() as patch:
        patch.setattr('backend.inventory.finish_operation', fail)
        with pytest.raises(RuntimeError, match='after write'):
            client.post(url, json=body)
    assert daily(client, setup)['warehouse'] == [item]
    assert client.post(url, json=body).status_code == 200
