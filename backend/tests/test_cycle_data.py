import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend.database import backup_database
from backend.main import create_app
from backend.models import CycleChangePreview
from .conftest import equipment_payload, equipment_url


def cycle_path(setup):
    return f"/api/cycles/{setup['cycle']['id']}"


def state(client, setup):
    return client.get(cycle_path(setup) + '/data').json()


def preview(client, setup, kind, rows):
    response = client.post(cycle_path(setup) + f'/data/{kind}/preview', json={'expected_version': state(client, setup)['cycle']['version'], 'rows': rows})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, setup, review):
    response = client.post(cycle_path(setup) + f"/changes/{review['preview_id']}/apply")
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_configuration_has_no_seed_game_values(client, setup):
    assert all(rows == [] for rows in state(client, setup)['tables'].values())
    result = client.get(cycle_path(setup) + '/base-stat?actual_grade=1110&equipment_type=hat').json()
    assert result == {'value': None, 'status': 'unknown'}


@pytest.mark.parametrize('kind,row', [
    ('base', {'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': 0}),
    ('refine', {'actual_grade': 1110, 'equipment_type': 'hat', 'refine_level': 6, 'constitution': None}),
    ('stone', {'stone_level': 8, 'constitution': None}),
    ('enchant', {'equipment_type': 'hat', 'name': '测试附魔', 'constitution': None}),
    ('rainbow', {'name': '测试五彩石', 'constitution': 0}),
    ('buff', {'skill': '测试技能', 'stacks': 1, 'threshold': None}),
])
def test_each_table_preview_apply_backup_and_replay(client, setup, kind, row):
    review = preview(client, setup, kind, [row])
    assert review['counts'] == {'added': 1, 'changed': 0, 'deleted': 0}
    assert state(client, setup)['tables'][kind] == []
    result = apply(client, setup, review)
    assert result['backup']
    assert (Path(client.app.state.engine.url.database).parent / 'backups' / result['backup']).is_file()
    assert state(client, setup)['tables'][kind] == [row]
    assert apply(client, setup, review) == result


def test_base_default_and_override_do_not_rewrite_equipment_or_calculate_stacks(client, setup):
    assert client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)}).status_code == 200
    review = preview(client, setup, 'base', [{'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': 149}])
    assert review['impact']['worn_count'] == 1
    assert review['impact']['worn'][0]['before'] is None
    apply(client, setup, review)
    cell = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    hat = cell['cells'][setup['character']['id']]['hat']
    assert hat['equipment']['base_stat']['value'] == 149
    assert hat['equipment']['base_constitution_override'] is None
    assert hat['equipment']['actual_grade'] == 1110
    assert hat['version'] == 1
    assert cell['constitution']['total'] is None and cell['constitution']['stacks'] is None
    client.put(equipment_url(setup), json={'expected_version': 1, 'equipment': equipment_payload(setup, base_constitution_override=0)})
    review = preview(client, setup, 'base', [{'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': None}])
    assert review['impact']['worn_count'] == 0
    apply(client, setup, review)
    cell = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()['cells'][setup['character']['id']]['hat']
    assert cell['equipment']['base_stat'] == {'value': 0, 'status': 'known', 'source': 'override'}


def test_csv_roundtrip_bom_chinese_headers_and_zero_versus_unknown(client, setup):
    rows = [{'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': 0}, {'actual_grade': 1110, 'equipment_type': 'weapon', 'constitution': None}]
    apply(client, setup, preview(client, setup, 'base', rows))
    exported = client.get(cycle_path(setup) + '/data/base/csv')
    assert exported.content.startswith(b'\xef\xbb\xbf')
    assert '真实品级,部位,体质' in exported.text
    assert '1110,帽子,0' in exported.text
    assert '1110,武器,' in exported.text
    review = client.post(cycle_path(setup) + '/data/base/csv-preview', json={'expected_version': state(client, setup)['cycle']['version'], 'csv_text': exported.text})
    assert review.status_code == 200
    assert review.json()['changes'] == []
    result = apply(client, setup, review.json())
    assert result['changed'] is False and result['backup'] is None


@pytest.mark.parametrize('text,needle', [
    ('错误,表头\n1,2\n', '表头'),
    ('真实品级,部位,体质\n1110,帽子,1.5\n', '第2行【体质】'),
    ('真实品级,部位,体质\n0,帽子,0\n', '第2行【真实品级】'),
    ('真实品级,部位,体质\n1110,未知部位,0\n', '第2行【部位】'),
    ('真实品级,部位,体质\n1110,帽子,-1\n', '第2行【体质】'),
    ('真实品级,部位,体质\n1110,帽子,0\n1110,帽子,1\n', '第3行'),
    ('真实品级,部位,体质\n1110,帽子,0,多余列\n', '第2行'),
    ('真实品级,部位,体质\n1110,帽子,' + '9' * 5000, '第2行'),
])
def test_bad_csv_has_line_column_and_no_partial_write(client, setup, text, needle):
    response = client.post(cycle_path(setup) + '/data/base/csv-preview', json={'expected_version': state(client, setup)['cycle']['version'], 'csv_text': text})
    assert response.status_code == 422
    assert needle in response.json()['detail']
    assert state(client, setup)['tables']['base'] == []


@pytest.mark.parametrize('kind,row', [
    ('refine', {'actual_grade': 1110, 'equipment_type': 'hat', 'refine_level': 5, 'constitution': 0}),
    ('refine', {'actual_grade': 1110, 'equipment_type': 'hat', 'refine_level': False, 'constitution': 0}),
    ('stone', {'stone_level': 9, 'constitution': None}),
    ('base', {'actual_grade': True, 'equipment_type': 'hat', 'constitution': None}),
    ('base', {'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': -1}),
    ('buff', {'skill': '测试', 'stacks': -1, 'threshold': 0}),
])
def test_invalid_dimensions_and_values_are_rejected(client, setup, kind, row):
    response = client.post(cycle_path(setup) + f'/data/{kind}/preview', json={'expected_version': state(client, setup)['cycle']['version'], 'rows': [row]})
    assert response.status_code == 422


def test_stale_preview_from_source_edit_does_not_apply(client, setup):
    review = preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}])
    source = setup['source']
    response = client.put(cycle_path(setup) + f"/sources/{source['id']}", json={'source_type': source['source_type'], 'name': source['name'], 'short_label': source['short_label'], 'default_grade': 1200})
    assert response.status_code == 200
    response = client.post(cycle_path(setup) + f"/changes/{review['preview_id']}/apply")
    assert response.status_code == 409
    assert state(client, setup)['tables']['stone'] == []


def test_preview_expiry_and_cross_cycle_rejected(client, setup):
    review = preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}])
    other = client.post('/api/cycles', json={'name': '另一周期'}).json()
    assert client.post(f"/api/cycles/{other['id']}/changes/{review['preview_id']}/apply").status_code == 404
    with client.app.state.engine.begin() as conn:
        conn.execute(update(CycleChangePreview).where(CycleChangePreview.id == review['preview_id']).values(created_at=(datetime.now(timezone.utc)-timedelta(minutes=31)).isoformat()))
    assert client.post(cycle_path(setup) + f"/changes/{review['preview_id']}/apply").status_code == 409


def test_copy_is_independent_and_old_equipment_keeps_origin(client, setup):
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup)})
    apply(client, setup, preview(client, setup, 'base', [{'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': 0}]))
    response = client.post(cycle_path(setup) + '/copy-preview', json={'expected_version': state(client, setup)['cycle']['version'], 'name': '复制的新周期'})
    assert response.status_code == 200
    review = response.json()
    assert review['sources'] == 1
    result = apply(client, setup, review)
    assert apply(client, setup, review) == result
    new_path = f"/api/cycles/{result['cycle_id']}"
    new = client.get(new_path + '/data').json()
    assert new['cycle']['copied_from_id'] == setup['cycle']['id']
    assert new['tables']['base'][0]['constitution'] == 0
    assert new['sources'][0]['id'] != setup['source']['id']
    assert new['sources'][0]['default_grade'] == 1110
    assert client.get('/api/workspace').json()['active_cycle_id'] == result['cycle_id']
    changed = client.post(new_path + '/data/base/preview', json={'expected_version': new['cycle']['version'], 'rows': [{'actual_grade': 1110, 'equipment_type': 'hat', 'constitution': 999}]}).json()
    assert client.post(new_path + f"/changes/{changed['preview_id']}/apply").status_code == 200
    assert state(client, setup)['tables']['base'][0]['constitution'] == 0
    hat = client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()['cells'][setup['character']['id']]['hat']
    assert hat['equipment']['cycle_origin_id'] == setup['cycle']['id']
    assert hat['equipment']['base_stat']['value'] == 0
    assert hat['equipment']['actual_grade'] == 1110


def test_archived_cycle_readonly_but_copyable_and_restoreable(client, setup):
    client.post('/api/cycles', json={'name': '当前新周期'})
    version = state(client, setup)['cycle']['version']
    response = client.put(cycle_path(setup) + '/archive', json={'expected_version': version, 'archived': True})
    assert response.status_code == 200
    assert client.get(cycle_path(setup) + '/data').json()['cycle']['archived']
    assert client.post(cycle_path(setup) + '/data/base/preview', json={'expected_version': version + 1, 'rows': []}).status_code == 409
    assert client.put('/api/workspace/active-cycle', json={'cycle_id': setup['cycle']['id']}).status_code == 409
    assert client.put(cycle_path(setup), json={'name': '不应改名'}).status_code == 409
    review = client.post(cycle_path(setup) + '/copy-preview', json={'expected_version': version + 1, 'name': '历史复制'}).json()
    assert apply(client, setup, review)['changed']
    assert client.put(cycle_path(setup) + '/archive', json={'expected_version': version + 1, 'archived': False}).status_code == 200


def test_header_only_csv_is_explicit_clear_with_backup(client, setup):
    apply(client, setup, preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}]))
    review = client.post(cycle_path(setup) + '/data/stone/csv-preview', json={'expected_version': state(client, setup)['cycle']['version'], 'csv_text': '五行石等级,体质\r\n'}).json()
    assert review['counts']['deleted'] == 1
    assert state(client, setup)['tables']['stone']
    result = apply(client, setup, review)
    assert result['backup'] and state(client, setup)['tables']['stone'] == []


def test_threshold_ordering_reports_line_and_accepts_missing_values(client, setup):
    text = '技能名称,层数,体质阈值\n测试技能,1,200\n测试技能,2,100\n'
    response = client.post(cycle_path(setup) + '/data/buff/csv-preview', json={'expected_version': state(client, setup)['cycle']['version'], 'csv_text': text})
    assert response.status_code == 422 and '第3行【体质阈值】' in response.json()['detail']
    review = preview(client, setup, 'buff', [{'skill':'测试技能','stacks':1,'threshold':None},{'skill':'测试技能','stacks':2,'threshold':100}])
    apply(client, setup, review)
    assert state(client, setup)['tables']['buff'][0]['threshold'] is None


def test_configuration_backup_restores_all_tables(client, setup, tmp_path):
    apply(client, setup, preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}]))
    backup = backup_database(Path(client.app.state.engine.url.database))
    restored = tmp_path / 'restored';restored.mkdir()
    (restored / 'app.sqlite3').write_bytes(backup.read_bytes())
    with TestClient(create_app(restored)) as restored_client:
        assert state(restored_client, setup)['tables']['stone'] == [{'stone_level':1,'constitution':0}]
    with sqlite3.connect(backup) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_failed_apply_rolls_back_table_and_version_then_can_retry(client, setup, monkeypatch):
    import backend.cycle_data as module
    apply(client, setup, preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}]))
    review = preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 100}])
    version = state(client, setup)['cycle']['version']
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('模拟审计提交前异常')
        patch.setattr(module, 'audit', fail)
        with pytest.raises(RuntimeError):
            client.post(cycle_path(setup) + f"/changes/{review['preview_id']}/apply")
    assert state(client, setup)['tables']['stone'] == [{'stone_level': 1, 'constitution': 0}]
    assert state(client, setup)['cycle']['version'] == version
    assert apply(client, setup, review)['changed']
    assert state(client, setup)['tables']['stone'][0]['constitution'] == 100


def test_stale_source_and_name_edits_do_not_overwrite_new_configuration(client, setup):
    version = state(client, setup)['cycle']['version']
    apply(client, setup, preview(client, setup, 'stone', [{'stone_level': 1, 'constitution': 0}]))
    response = client.put(cycle_path(setup) + f"/sources/{setup['source']['id']}", json={'source_type':'测试','name':'不应保存','short_label':'旧','default_grade':1200,'expected_version':version})
    assert response.status_code == 409
    response = client.put(cycle_path(setup), json={'name':'不应保存','expected_version':version})
    assert response.status_code == 409
    assert state(client, setup)['cycle']['name'] == setup['cycle']['name']
    assert state(client, setup)['sources'][0]['name'] == setup['source']['name']


def test_preview_reports_matching_worn_stock_and_memo_but_does_not_reserve_them(client, setup):
    from uuid import uuid4
    account_path = f"/api/accounts/{setup['account']['id']}"
    client.put(equipment_url(setup), json={'expected_version':0,'equipment':equipment_payload(setup)})
    for target in ('warehouse','drops'):
        assert client.post(account_path + '/' + target, json={'request_id':str(uuid4()),'equipment':equipment_payload(setup),'quantity':2}).status_code == 200
    review = preview(client, setup, 'base', [{'actual_grade':1110,'equipment_type':'hat','constitution':149}])
    assert (review['impact']['worn_count'],review['impact']['warehouse_count'],review['impact']['drop_count']) == (1,1,1)
    assert client.get(account_path+'/daily').json()['warehouse'][0]['quantity'] == 2
    apply(client, setup, review)
    data = client.get(account_path+'/daily').json()
    assert data['warehouse'][0]['quantity'] == data['drops'][0]['quantity'] == 2
    assert data['warehouse'][0]['equipment']['base_stat']['value'] == 149


def test_parallel_copy_confirmation_creates_only_one_cycle(client, setup):
    from concurrent.futures import ThreadPoolExecutor
    review = client.post(cycle_path(setup) + '/copy-preview', json={'expected_version':state(client, setup)['cycle']['version'],'name':'并发复制测试'}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _:client.post(cycle_path(setup)+f"/changes/{review['preview_id']}/apply"),range(2)))
    assert [r.status_code for r in responses] == [200,200]
    assert responses[0].json()['cycle_id'] == responses[1].json()['cycle_id']
    assert len(client.get('/api/workspace').json()['cycles']) == 2
