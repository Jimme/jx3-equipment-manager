from sqlalchemy.exc import IntegrityError
import pytest

from .conftest import equipment_payload, equipment_url
from .test_inventory import add, daily, path


def test_old_source_remains_available_and_equal_grade_sources_stay_distinct(client, setup):
    first = setup['source']
    other = client.post('/api/cycles', json={'name': '新周期'}).json()
    new_source = client.post(f"/api/cycles/{other['id']}/sources", json={
        'source_type': '测试', 'name': first['name'], 'short_label': first['short_label'], 'actual_grade': 1110,
    }).json()
    assert {row['id'] for row in client.get('/api/sources').json()} >= {first['id'], new_source['id']}
    old_stock, _ = add(client, setup)
    new_stock, _ = add(client, setup, source_id=new_source['id'])
    assert old_stock['id'] != new_stock['id']
    assert old_stock['equipment']['cycle_origin_id'] == first['cycle_id']
    assert new_stock['equipment']['cycle_origin_id'] == other['id']
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)}).status_code == 200
    url = f"/api/cycles/{first['cycle_id']}/sources/{first['id']}"
    body = {key: first[key] for key in ('name', 'short_label', 'source_type', 'actual_grade')}
    assert client.put(url, json={**body, 'active': False}).status_code == 200
    assert client.post(path(setup) + '/warehouse', json={
        'request_id': 'inactive-add', 'quantity': 1, 'equipment': equipment_payload(setup)}).status_code == 409
    assert client.put(equipment_url(setup), json={'expected_version': 1, 'equipment': equipment_payload(setup, note='更正备注')}).status_code == 200
    assert len(daily(client, setup)['warehouse']) == 2
    with pytest.raises(IntegrityError), client.app.state.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE equipment_sources SET actual_grade=1200 WHERE id=?", (first['id'],))


def test_stale_grade_rejected_before_first_use_and_backup_roundtrip(client, setup):
    first = setup['source']
    url = f"/api/cycles/{first['cycle_id']}/sources/{first['id']}"
    body = {key: first[key] for key in ('name', 'short_label', 'source_type')}
    assert client.put(url, json={**body, 'actual_grade': 1200}).status_code == 200
    draft = equipment_payload(setup, expected_source_grade=1110)
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': draft}).status_code == 409
    assert client.get('/api/templates').json() == []
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': {**draft, 'expected_source_grade': 1200}}).status_code == 200
    exported = client.get('/api/data/export').json()
    table = exported['tables']['equipment_templates']
    assert len(table) == 1 and set(table[0]).isdisjoint({'actual_grade', 'cycle_origin_id', 'source_name'})
    name = client.post('/api/data/backups').json()['name']
    response = client.post(f'/api/data/backups/{name}/restore-preview')
    assert response.status_code == 200 and response.json()['unchanged']
    exported['schema_revision'] = '0011'
    assert client.post('/api/data/import-preview', json=exported).status_code == 422
