import pytest

from .conftest import equipment_payload, equipment_url
from .test_inventory import add, daily, path, request_id


def batch_row(setup, item, payload, grade, quantity):
    return {'item_id': item['id'], 'expected_version': item['version'], 'quantity': quantity,
            'equipment': {**payload['equipment'], 'source_id': equipment_payload(setup, actual_grade=grade)['source_id']}, 'choices': item['choices']}


def preview(client, setup, rows, all_stock=None):
    return client.post(path(setup) + '/warehouse-batch/preview', json={'rows': rows, 'all_stock': all_stock})


def apply(client, setup, token, request=None):
    return client.post(path(setup) + '/warehouse-batch/apply', json={'preview_id': token, 'request_id': request or request_id()})


def counts(client, setup):
    return {item['equipment']['actual_grade']: item['quantity'] for item in daily(client, setup)['warehouse']}


def test_chained_corrections_split_and_add_against_original_quantities(client, setup):
    first, a = add(client, setup, quantity=4, actual_grade=965)
    second, b = add(client, setup, quantity=2, actual_grade=1110)
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': a['equipment']})
    rows = [batch_row(setup, first, a, 1110, 3), batch_row(setup, second, b, 1200, 2),
            {'equipment': {**a['equipment'], 'source_id': equipment_payload(setup, actual_grade=1200)['source_id']}, 'quantity': 1}]
    before = client.get('/api/data/export').json()['tables']
    worn = client.get(path(setup) + '/matrix').json()['cells']
    response = preview(client, setup, rows, {first['id']: first['version'], second['id']: second['version']})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert [row['final_quantity'] for row in draft['changes']] == [3, 3, 3]
    assert client.get('/api/data/export').json()['tables'] == before
    request = request_id()
    result = apply(client, setup, draft['preview_id'], request)
    assert result.status_code == 200, result.text
    assert apply(client, setup, draft['preview_id'], request).json() == result.json()
    assert apply(client, setup, draft['preview_id']).json() == result.json()
    assert counts(client, setup) == {965: 1, 1110: 3, 1200: 3}
    assert client.get(path(setup) + '/matrix').json()['cells'] == worn
    assert len(client.get('/api/data/backups').json()) == 1


def test_new_stock_conflicts_with_whole_warehouse_preview(client, setup):
    first, a = add(client, setup, quantity=2, actual_grade=965)
    row = batch_row(setup, first, a, 1110, 2)
    draft = preview(client, setup, [row], {first['id']: first['version']}).json()
    add(client, setup, actual_grade=1300)
    assert apply(client, setup, draft['preview_id']).status_code == 409
    assert counts(client, setup) == {965: 2, 1300: 1}


def test_target_update_and_cross_account_reject_without_writes(client, setup):
    first, a = add(client, setup, quantity=3, actual_grade=965)
    add(client, setup, quantity=2, actual_grade=1110)
    row = batch_row(setup, first, a, 1110, 1)
    draft = preview(client, setup, [row]).json()
    add(client, setup, actual_grade=1110)
    assert apply(client, setup, draft['preview_id']).status_code == 409
    other = {'account': client.post('/api/accounts', json={'name': '另一个账号'}).json()}
    assert preview(client, other, [row]).status_code == 409
    assert apply(client, other, draft['preview_id']).status_code == 404
    assert counts(client, setup) == {965: 3, 1110: 3}
    assert daily(client, other)['warehouse'] == []


def test_preview_overflow_and_apply_backup_failure_roll_back(client, setup, monkeypatch):
    add(client, setup, quantity=2147483647, actual_grade=1110)
    invalid = {'equipment': equipment_payload(setup, actual_grade=1110), 'quantity': 1}
    assert preview(client, setup, [invalid]).status_code == 422
    valid = {'equipment': equipment_payload(setup, actual_grade=965), 'quantity': 1}
    draft = preview(client, setup, [valid]).json()
    before = client.get('/api/data/export').json()['tables']

    def fail_backup(*args):
        raise OSError('test backup failure')

    monkeypatch.setattr('backend.warehouse_batch.backup_database', fail_backup)
    assert apply(client, setup, draft['preview_id']).status_code == 503
    assert client.get('/api/data/export').json()['tables'] == before

    monkeypatch.undo()

    def fail_after_flush(session, *args):
        session.flush()
        raise RuntimeError('test failure after stock writes')

    with monkeypatch.context() as patch:
        patch.setattr('backend.warehouse_batch.finish_operation', fail_after_flush)
        with pytest.raises(RuntimeError, match='after stock writes'):
            apply(client, setup, draft['preview_id'])
    assert client.get('/api/data/export').json()['tables'] == before
    assert apply(client, setup, draft['preview_id']).status_code == 200
