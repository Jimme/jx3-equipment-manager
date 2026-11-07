import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.data_management import fingerprint
from backend.main import create_app
from backend.models import DataChangePreview
from .conftest import equipment_payload, equipment_url


def export(client):
    response=client.get('/api/data/export')
    assert response.status_code==200
    return response.json()


def review(client, payload):
    response=client.post('/api/data/import-preview',content=json.dumps(payload,ensure_ascii=False).encode())
    assert response.status_code==200,response.text
    return response.json()


def apply(client, preview):
    return client.post('/api/data/changes/'+preview['preview_id']+'/apply',json={'confirm_replace':True})


def comparable(payload):
    tables=copy.deepcopy(payload['tables']);tables['audit_events']=[]
    for rows in tables.values():
        for row in rows:
            row.pop('version',None)
            row.pop('version_seed',None)
    return fingerprint(tables)


def seed_worn(client, setup):
    response=client.put(equipment_url(setup),json={'expected_version':0,'equipment':equipment_payload(setup,base_constitution_override=0)})
    assert response.status_code==200


def test_empty_and_seeded_export_round_trip_preserves_null_and_zero(client,setup,tmp_path):
    seed_worn(client,setup)
    payload=export(client)
    assert payload['schema_revision']=='0009'
    assert 'cycle_change_previews' not in payload['tables'] and 'mutation_receipts' in payload['tables']
    assert payload['tables']['equipment_templates'][0]['base_constitution_override']==0
    assert payload['tables']['character_slot_settings'][0]['refine_level'] is None
    with TestClient(create_app(tmp_path/'destination')) as destination:
        preview=review(destination,payload)
        assert preview['before']['角色']==0 and preview['after']['角色']==1
        assert destination.get('/api/workspace').json()['accounts']==[]
        result=apply(destination,preview)
        assert result.status_code==200,result.text
        assert result.json()['backup']
        assert comparable(export(destination))==comparable(payload)
        assert destination.get(f"/api/accounts/{setup['account']['id']}/matrix").status_code==200
        assert destination.get(f"/api/accounts/{setup['account']['id']}/history").status_code==200
        again=apply(destination,preview)
        assert again.json()==result.json()
        assert len(destination.get('/api/data/backups').json())==1


def test_noop_does_not_backup_or_write_audit(client,setup):
    before=export(client)
    preview=review(client,before)
    assert preview['unchanged']
    assert apply(client,preview).json()=={'changed':False,'backup':None}
    assert export(client)['tables']==before['tables']
    assert client.get('/api/data/backups').json()==[]


def test_raid_checks_are_independent_and_round_trip(client, setup, tmp_path):
    account_id = setup['account']['id']
    character_id = setup['character']['id']
    base = f'/api/accounts/{account_id}/characters/{character_id}/raid-checks'
    assert client.put(base + '/25pt', json={'cleared': True}).json() == {'cleared': True}
    assert client.put(base + '/10', json={'cleared': 'true'}).status_code == 422
    assert client.put(base + '/unknown', json={'cleared': True}).status_code == 422
    other = client.post('/api/accounts', json={'name': '另一个测试账号'}).json()['id']
    assert client.put(f'/api/accounts/{other}/characters/{character_id}/raid-checks/25yx',
                      json={'cleared': True}).status_code == 404
    payload = export(client)
    character = payload['tables']['characters'][0]
    assert (character['raid_10'], character['raid_25pt'], character['raid_25yx']) == (False, True, False)
    with TestClient(create_app(tmp_path / 'destination')) as destination:
        assert apply(destination, review(destination, payload)).status_code == 200
        assert destination.get(f'/api/accounts/{account_id}/characters').json() == [character]


def test_previous_backup_formats_default_raid_checks_to_unchecked(client, setup, tmp_path):
    payload = export(client)
    payload['schema_revision'] = '0006'
    for cycle in payload['tables']['equipment_cycles']:
        cycle.pop('has_constitution_sockets')
    raid_fields = ('raid_10', 'raid_25pt', 'raid_25yx')
    for character in payload['tables']['characters']:
        for field in raid_fields:
            character.pop(field)
    assert review(client, payload)['unchanged']
    backup = client.post('/api/data/backups').json()['name']
    path = tmp_path / 'old.sqlite3'
    path.write_bytes(client.get(f'/api/data/backups/{backup}').content)
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE equipment_cycles DROP COLUMN has_constitution_sockets')
        for field in raid_fields:
            connection.execute(f'ALTER TABLE characters DROP COLUMN {field}')
        connection.execute("UPDATE alembic_version SET version_num = '0006'")
    response = client.post('/api/data/restore-preview', content=path.read_bytes())
    assert response.status_code == 200, response.text
    assert response.json()['unchanged']


def test_stale_preview_and_expired_preview_do_not_overwrite(client,setup):
    payload=export(client);payload['tables']['game_accounts'][0]['name']='文件里的账号'
    preview=review(client,payload)
    client.put(f"/api/accounts/{setup['account']['id']}/preferences",json={'target_grade':1200,'comparison_mode':'strict'})
    before=comparable(export(client))
    assert apply(client,preview).status_code==409
    assert comparable(export(client))==before
    newer=review(client,payload)
    with Session(client.app.state.engine) as session:
        session.execute(update(DataChangePreview).where(DataChangePreview.id==newer['preview_id']).values(created_at=(datetime.now(timezone.utc)-timedelta(minutes=31)).isoformat()));session.commit()
    assert apply(client,newer).status_code==409


@pytest.mark.parametrize('mutation',[
    lambda p:p['tables'].pop('characters'),
    lambda p:p.update(schema_revision='0005'),
    lambda p:p['tables']['game_accounts'].append(p['tables']['game_accounts'][0]),
    lambda p:p['tables']['characters'][0].update(account_id='missing'),
    lambda p:p['tables']['character_slot_settings'].pop(),
    lambda p:p['tables']['character_slot_settings'][0].update(refine_level=5),
    lambda p:p['tables']['character_slot_settings'][0].update(stone_state='set',stone_level=9),
    lambda p:p['tables']['character_slot_settings'][0].update(refine_level=True),
    lambda p:p['tables']['equipment_cycles'][0].update(version=-1),
    lambda p:p['tables']['characters'][0].update(archived='false'),
    lambda p:p['tables']['character_calculation_settings'].clear(),
    lambda p:p['tables']['character_calculation_settings'][0].update(scope_confirmed=True),
    lambda p:p['tables']['equipment_cycles'][0].update(archived=True),
    lambda p:p['tables']['game_accounts'][0].update(extra='unknown'),
    lambda p:p['tables']['audit_events'][0].update(snapshots='[]'),
])
def test_invalid_json_rejected_without_partial_changes(client,setup,mutation):
    payload=export(client);before=comparable(payload)
    mutation(payload)
    response=client.post('/api/data/import-preview',content=json.dumps(payload).encode())
    assert response.status_code==422,response.text
    assert comparable(export(client))==before
    assert client.get('/api/data/backups').json()==[]


@pytest.mark.parametrize('raw',[b'',b'{}',b'{"format":1,"format":2}',b'{"x":NaN}',b'not json',b'\xff\xfe'])
def test_malformed_json_rejected(client,raw):
    assert client.post('/api/data/import-preview',content=raw).status_code==422


def test_backup_download_upload_restore_and_pre_restore_backup(client,setup):
    seed_worn(client,setup)
    original=export(client)
    backup=client.post('/api/data/backups').json()['name']
    raw=client.get('/api/data/backups/'+backup).content
    assert raw.startswith(b'SQLite format 3\x00')
    client.post('/api/accounts',json={'name':'之后新增'})
    preview=client.post('/api/data/restore-preview',content=raw)
    assert preview.status_code==200,preview.text
    assert preview.json()['before']['游戏账号']==2 and preview.json()['after']['游戏账号']==1
    result=apply(client,preview.json())
    assert result.status_code==200,result.text
    assert comparable(export(client))==comparable(original)
    prior=client.get('/api/data/backups/'+result.json()['backup']).content
    with open(client.app.state.engine.url.database+'.verify.sqlite3','wb') as file:file.write(prior)
    with sqlite3.connect(client.app.state.engine.url.database+'.verify.sqlite3') as connection:
        assert connection.execute('SELECT count(*) FROM game_accounts').fetchone()[0]==2
    assert client.post('/api/data/backups/'+backup+'/restore-preview').status_code==200


def test_delete_backup_only_removes_selected_snapshot(client, setup, tmp_path):
    names = [client.post('/api/data/backups').json()['name'] for _ in range(2)]
    workspace = client.get('/api/workspace').json()
    assert client.delete('/api/data/backups/%2e%2e%5capp.sqlite3').status_code == 404
    path = f'/api/data/backups/{names[0]}'
    assert client.delete(path).json() == {'name': names[0]}
    assert client.delete(path).status_code == 404
    assert client.get(path).status_code == 404
    assert client.post(path + '/restore-preview').status_code == 404
    assert [item['name'] for item in client.get('/api/data/backups').json()] == [names[1]]
    assert client.get('/api/workspace').json() == workspace
    assert (tmp_path / 'app.sqlite3').is_file()


def test_old_and_malformed_sqlite_backups_rejected(client,setup,tmp_path):
    for raw in [b'',b'not sqlite',b'SQLite format 3\x00' + b'bad data']:
        assert client.post('/api/data/restore-preview',content=raw).status_code==422
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('create table alembic_version (version_num text)');connection.execute("insert into alembic_version values ('0005')")
    response=client.post('/api/data/restore-preview',content=path.read_bytes())
    assert response.status_code==422 and '版本' in response.json()['detail']


def test_require_confirmation_and_sanitize_backup_paths(client,setup):
    preview=review(client,export(client))
    assert client.post('/api/data/changes/'+preview['preview_id']+'/apply',json={'confirm_replace':False}).status_code==422
    assert client.get('/api/data/backups/not_a_file.sqlite3').status_code==404
    assert client.post('/api/data/backups/not_a_file.sqlite3/restore-preview').status_code==404
    assert client.get('/api/data/backups/%2e%2e%5capp.sqlite3').status_code==404


def test_atomic_rollback_after_database_write_failure(client,setup,monkeypatch):
    payload=export(client);before=comparable(payload)
    payload['tables']['game_accounts'][0]['name']='替换后'
    preview=review(client,payload)
    from backend import data_management
    fill=data_management.fill_tables
    def fail(session,tables):
        fill(session,tables)
        raise RuntimeError('simulated failure')
    monkeypatch.setattr(data_management,'fill_tables',fail)
    with pytest.raises(RuntimeError,match='simulated'):
        apply(client,preview)
    assert comparable(export(client))==before
    assert len(client.get('/api/data/backups').json())==1


def test_backup_failure_prevents_any_workspace_change(client,setup,monkeypatch):
    payload=export(client);before=comparable(payload);payload['tables']['game_accounts'][0]['name']='替换后'
    preview=review(client,payload)
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('backend.data_management.backup_database',fail)
    result=apply(client,preview)
    assert result.status_code==503
    assert comparable(export(client))==before


def test_concurrent_imports_do_not_both_replace(client,setup):
    payload=export(client);payload['tables']['game_accounts'][0]['name']='替换后'
    previews=[review(client,payload),review(client,payload)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda p:apply(client,p).status_code,previews))
    assert 200 in statuses and any(s in (404,409) for s in statuses)


def test_copied_cycle_self_reference_order_restores(client,setup):
    state=client.get(f"/api/cycles/{setup['cycle']['id']}/data").json()
    p=client.post(f"/api/cycles/{setup['cycle']['id']}/copy-preview",json={'expected_version':state['cycle']['version'],'name':'复制周期'}).json()
    assert client.post(f"/api/cycles/{setup['cycle']['id']}/changes/{p['preview_id']}/apply").status_code==200
    payload=export(client)
    payload['tables']['equipment_cycles'].reverse()
    assert review(client,payload)['unchanged']


def test_restore_rebases_versions_to_reject_open_old_equipment_form(client,setup):
    seed_worn(client,setup)
    payload=export(client)
    payload['tables']['game_accounts'][0]['name']='文件里的账号'
    result=apply(client,review(client,payload))
    assert result.status_code==200,result.text
    stale=client.put(equipment_url(setup),json={'expected_version':1,'equipment':equipment_payload(setup,actual_grade=1200)})
    assert stale.status_code==409
    matrix=client.get(f"/api/accounts/{setup['account']['id']}/matrix").json()
    cell=matrix['cells'][setup['character']['id']]['hat']
    assert cell['version']>1 and cell['equipment']['actual_grade']==1110
    assert client.put(equipment_url(setup),json={'expected_version':cell['version'],'equipment':equipment_payload(setup,actual_grade=1200)}).status_code==200


def test_stream_upload_limit_stops_preview(client,monkeypatch):
    monkeypatch.setattr('backend.data_management.MAX_BYTES',8)
    assert client.post('/api/data/import-preview',content=b'123456789').status_code==413

def test_failed_backup_leaves_no_false_snapshot_and_success_closes_file_handles(tmp_path):
    from backend.database import backup_database
    bad=tmp_path/'bad.sqlite3';bad.write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):backup_database(bad)
    assert list((tmp_path/'backups').glob('*.sqlite3'))==[]
    good=tmp_path/'good.sqlite3'
    connection=sqlite3.connect(good);connection.execute('create table test (id integer)');connection.close()
    result=backup_database(good)
    renamed=result.with_name('renamed.sqlite3')
    result.rename(renamed)
    assert renamed.is_file() # Windows rejects this if the backup target remains open.

def test_backup_omits_pending_upload_payload_without_touching_live_preview(client,setup,tmp_path):
    payload=export(client);payload['tables']['game_accounts'][0]['name']='替换后'
    preview=review(client,payload)
    name=client.post('/api/data/backups').json()['name']
    path=tmp_path/'inspect.sqlite3';path.write_bytes(client.get('/api/data/backups/'+name).content)
    connection=sqlite3.connect(path)
    try:
        assert connection.execute('SELECT count(*) FROM data_change_previews').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM game_accounts').fetchone()[0]==1
        assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    finally:connection.close()
    assert apply(client,preview).status_code==200


def test_empty_restore_keeps_monotonic_version_generation(client,setup,tmp_path):
    seed_worn(client,setup)
    original=export(client)
    with TestClient(create_app(tmp_path/'empty')) as empty:
        empty_payload=export(empty)
    assert apply(client,review(client,empty_payload)).status_code==200
    generation=export(client)['tables']['workspace_settings'][0]['version_seed']
    assert generation>0
    assert apply(client,review(client,original)).status_code==200
    assert export(client)['tables']['workspace_settings'][0]['version_seed']>generation
    assert client.put(equipment_url(setup),json={'expected_version':1,'equipment':equipment_payload(setup,actual_grade=1200)}).status_code==409


def test_delayed_inventory_retry_after_restore_cannot_duplicate_rolled_back_add(client,setup):
    original=export(client)
    body={'request_id':'operation-after-snapshot','equipment':equipment_payload(setup),'quantity':1}
    url=f"/api/accounts/{setup['account']['id']}/warehouse"
    assert client.post(url,json=body).status_code==200
    assert apply(client,review(client,original)).status_code==200
    retry=client.post(url,json=body)
    assert retry.status_code==409 and '旧请求' in retry.json()['detail']
    assert client.get(f"/api/accounts/{setup['account']['id']}/daily").json()['warehouse']==[]
    body['request_id']='new-operation-after-reload'
    assert client.post(url,json=body).json()['quantity']==1
    # Tombstones are a valid part of a later JSON export/restore.
    assert review(client,export(client))['unchanged']


def test_json_round_trip_retains_receipts_for_already_included_inventory(client,setup,tmp_path):
    body={'request_id':'included-operation','equipment':equipment_payload(setup),'quantity':1}
    url=f"/api/accounts/{setup['account']['id']}/warehouse"
    original=client.post(url,json=body).json()
    payload=export(client)
    with TestClient(create_app(tmp_path/'destination')) as destination:
        assert apply(destination,review(destination,payload)).status_code==200
        replay=destination.post(url,json=body)
        assert replay.status_code==200 and replay.json()==original
        assert destination.get(f"/api/accounts/{setup['account']['id']}/daily").json()['warehouse'][0]['quantity']==1

def test_backup_listing_tolerates_a_file_being_moved(client,monkeypatch):
    from pathlib import Path
    name=client.post('/api/data/backups').json()['name']
    original=Path.stat
    def missing(path,*args,**kwargs):
        if path.name==name:raise FileNotFoundError('moved during scan')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',missing)
    response=client.get('/api/data/backups')
    assert response.status_code==200 and response.json()==[]
