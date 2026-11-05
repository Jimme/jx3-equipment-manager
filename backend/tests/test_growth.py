import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import equipment_payload, equipment_url
from .test_cycle_data import apply, preview


def path(setup):
    return f"/api/accounts/{setup['account']['id']}/characters/{setup['character']['id']}/growth"


def report(client, setup):
    response = client.get(path(setup))
    assert response.status_code == 200, response.text
    return response.json()


def body_for(data):
    return {'slots': [{'slot': row['slot'], 'expected_version': row['settings']['version'],
                       'refine_level': row['settings']['refine_level'], 'stone_state': row['settings']['stone_state'],
                       'stone_level': row['settings']['stone_level']} for row in data['slots']],
            'expected_version': data['calculation']['version'], 'expected_cycle_id': data['cycle']['id'] if data['cycle'] else None,
            'expected_cycle_version': data['cycle']['version'] if data['cycle'] else None,
            'additional_constitution': data['calculation']['additional_constitution'],
            'scope_confirmed': data['calculation']['confirmation_valid']}


def save(client, setup, body):
    response = client.put(path(setup), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def seed_complete(client, setup):
    slots = report(client, setup)['slots']
    types = set()
    for row in slots:
        kind = 'ring' if row['slot'].startswith('ring') else row['slot']
        types.add(kind)
        response = client.put(equipment_url(setup, row['slot']), json={'expected_version': 0,
            'equipment': equipment_payload(setup, equipment_type=kind, base_constitution_override=100, has_constitution_socket=False),
            'choices': {'enchant': {'state': 'none'}, 'rainbow': {'state': 'none'} if kind == 'weapon' else {'state': 'unknown'}}})
        assert response.status_code == 200, response.text
    apply(client, setup, preview(client, setup, 'refine', [{'actual_grade': 1110, 'equipment_type': kind, 'refine_level': 0, 'constitution': 0} for kind in types]))
    body = body_for(report(client, setup))
    for row in body['slots']:
        row['refine_level'] = 0
    return save(client, setup, body)


def test_unknown_data_still_usable_and_no_fake_total(client, setup):
    data = report(client, setup)
    assert len(data['slots']) == 12 and len(data['missing']) == 12
    assert data['equipment_total'] is None and data['verified_total'] is None
    assert data['known_subtotal'] == 0 and data['buffs'] == []
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, base_constitution_override=0)})
    hat = report(client, setup)['slots'][0]
    assert hat['components']['base']['value'] == 0
    assert hat['components']['refine']['status'] == 'unknown'
    assert hat['components']['rainbow']['status'] == 'not_applicable'


def test_refine_stone_preserved_across_grade_socket_and_cycle_changes(client, setup):
    apply(client, setup, preview(client, setup, 'refine', [{'actual_grade': 1110, 'equipment_type': 'hat', 'refine_level': 6, 'constitution': 20}, {'actual_grade': 1200, 'equipment_type': 'hat', 'refine_level': 6, 'constitution': 30}]))
    apply(client, setup, preview(client, setup, 'stone', [{'stone_level': 8, 'constitution': 10}]))
    body = body_for(report(client, setup))
    body['slots'][0].update(refine_level=6, stone_state='set', stone_level=8)
    save(client, setup, body)
    for version, grade, socket, refine, stone in [(0,1110,True,20,10),(1,1200,False,30,None),(2,1200,True,30,10)]:
        response = client.put(equipment_url(setup), json={'expected_version': version, 'equipment': equipment_payload(setup, actual_grade=grade, has_constitution_socket=socket)})
        assert response.status_code == 200
        hat = report(client, setup)['slots'][0]
        assert hat['settings']['refine_level'] == 6 and hat['settings']['stone_level'] == 8
        assert hat['components']['refine']['value'] == refine
        assert hat['components']['stone']['value'] == stone
    client.post('/api/cycles', json={'name': '另一个周期'})
    hat = report(client, setup)['slots'][0]
    assert hat['components']['refine']['value'] == 30 and hat['components']['stone']['value'] == 10
    client.delete(equipment_url(setup)+'?expected_version=3')
    assert report(client, setup)['slots'][0]['settings']['stone_level'] == 8


def test_attachments_lookup_by_original_cycle_and_override_zero(client, setup):
    apply(client, setup, preview(client, setup, 'enchant', [{'equipment_type':'weapon','name':'模拟附魔','constitution':11}]))
    apply(client, setup, preview(client, setup, 'rainbow', [{'name':'模拟五彩石','constitution':12}]))
    choices = {'enchant':{'state':'configured','name':'模拟附魔'}, 'rainbow':{'state':'configured','name':'模拟五彩石'}}
    for version, override, expected in [(0,None,11),(1,0,0)]:
        if override is not None:
            choices['enchant']['constitution'] = override
        client.put(equipment_url(setup, 'weapon'), json={'expected_version':version, 'equipment':equipment_payload(setup,equipment_type='weapon'), 'choices':choices})
        weapon = report(client, setup)['slots'][3]
        assert weapon['components']['enchant']['value'] == expected
        assert weapon['components']['rainbow']['value'] == 12
    client.post('/api/cycles', json={'name':'新周期'})
    assert report(client, setup)['slots'][3]['components']['rainbow']['value'] == 12


def test_complete_equipment_is_not_automatically_verified_panel_total(client, setup):
    data = seed_complete(client, setup)
    assert data['equipment_total'] == data['known_subtotal'] == 1200
    assert data['missing'] == [] and data['verified_total'] is None
    body = body_for(data)
    body.update(additional_constitution=0, scope_confirmed=True)
    assert save(client, setup, body)['verified_total'] == 1200


@pytest.mark.parametrize('total,stacks,next_layer,difference', [(0,0,1,1200),(1199,0,1,1),(1200,1,2,100),(1300,2,None,None)])
def test_threshold_boundaries_only_after_explicit_confirmation(client, setup, total, stacks, next_layer, difference):
    seed_complete(client, setup)
    apply(client, setup, preview(client, setup, 'buff', [{'skill':'模拟技能','stacks':1,'threshold':1200},{'skill':'模拟技能','stacks':2,'threshold':1300}]))
    # Replace base values to obtain a controlled total without assuming game data.
    if total < 1200:
        for row in report(client, setup)['slots']:
            value = total if row['slot'] == 'hat' else 0
            client.put(equipment_url(setup,row['slot']), json={'expected_version':1,'equipment':equipment_payload(setup,equipment_type='ring' if row['slot'].startswith('ring') else row['slot'],base_constitution_override=value,has_constitution_socket=False),'choices':{'enchant':{'state':'none'},'rainbow':{'state':'none'} if row['slot']=='weapon' else {'state':'unknown'}}})
    data = report(client, setup)
    assert data['buffs'][0]['stacks'] is None
    body = body_for(data)
    body.update(additional_constitution=max(0,total-1200), scope_confirmed=True)
    data = save(client, setup, body)
    buff = data['buffs'][0]
    assert (buff['stacks'],buff['next_stacks'],buff['difference']) == (stacks,next_layer,difference)
    if next_layer is None:
        assert '不代表游戏' in buff['message']


@pytest.mark.parametrize('rules', [[(1,None)],[(2,100)],[(0,1),(1,100)],[(1,10),(3,20)]])
def test_incomplete_or_ambiguous_thresholds_never_produce_layers(client, setup, rules):
    seed_complete(client, setup)
    apply(client, setup, preview(client, setup, 'buff', [{'skill':'模拟技能','stacks':n,'threshold':v} for n,v in rules]))
    body=body_for(report(client,setup));body.update(additional_constitution=0,scope_confirmed=True)
    buff=save(client,setup,body)['buffs'][0]
    assert buff['stacks'] is None and buff['difference'] is None and buff['message']


def test_changed_thresholds_or_active_cycle_require_reconfirmation(client, setup):
    seed_complete(client, setup)
    apply(client,setup,preview(client,setup,'buff',[{'skill':'模拟技能','stacks':1,'threshold':100}]))
    body=body_for(report(client,setup));body.update(additional_constitution=0,scope_confirmed=True)
    assert save(client,setup,body)['buffs'][0]['stacks']==1
    apply(client,setup,preview(client,setup,'buff',[{'skill':'模拟技能','stacks':1,'threshold':200}]))
    data=report(client,setup)
    assert not data['calculation']['confirmation_valid'] and data['buffs'][0]['stacks'] is None
    body=body_for(data);body['scope_confirmed']=True
    assert save(client,setup,body)['buffs'][0]['stacks']==1
    client.post('/api/cycles',json={'name':'新周期'})
    assert not report(client,setup)['calculation']['confirmation_valid']


def test_missing_required_value_blocks_verified_total_and_layers(client, setup):
    seed_complete(client, setup)
    apply(client,setup,preview(client,setup,'buff',[{'skill':'模拟技能','stacks':1,'threshold':1}]))
    apply(client,setup,preview(client,setup,'refine',[]))
    body=body_for(report(client,setup));body.update(additional_constitution=0,scope_confirmed=True)
    data=save(client,setup,body)
    assert data['equipment_total'] is None and data['verified_total'] is None and data['buffs'][0]['stacks'] is None
    assert data['slots'][0]['components']['refine']['status']=='unknown' # Refine 0 still needs a verified value.


@pytest.mark.parametrize('patch', [{'refine_level':5},{'refine_level':True},{'stone_state':'set','stone_level':None},{'stone_state':'unknown','stone_level':1},{'stone_state':'set','stone_level':9}])
def test_invalid_slot_values_rejected(client, setup, patch):
    body=body_for(report(client,setup));body['slots'][0].update(patch)
    assert client.put(path(setup),json=body).status_code==422


def test_stale_write_is_atomic_and_noop_does_not_increment_versions(client, setup):
    original=body_for(report(client,setup));changed=copy.deepcopy(original)
    changed['slots'][0]['refine_level']=6
    save(client,setup,changed)
    original['slots'][1]['refine_level']=8
    assert client.put(path(setup),json=original).status_code==409
    data=report(client,setup)
    assert data['slots'][1]['settings']['refine_level'] is None
    versions=[r['settings']['version'] for r in data['slots']]
    assert [r['settings']['version'] for r in save(client,setup,body_for(data))['slots']]==versions


def test_account_isolation_archiving_and_stale_cycle(client, setup):
    other=client.post('/api/accounts',json={'name':'其他账号'}).json()
    wrong=path(setup).replace(setup['account']['id'],other['id'])
    assert client.get(wrong).status_code==404
    assert client.put(wrong,json=body_for(report(client,setup))).status_code==404
    body=body_for(report(client,setup))
    client.post('/api/cycles',json={'name':'切换周期'})
    assert client.put(path(setup),json=body).status_code==409
    client.put(f"/api/accounts/{setup['account']['id']}/characters/{setup['character']['id']}",json={'name':'归档角色','archived':True})
    assert client.get(path(setup)).status_code==200
    assert client.put(path(setup),json=body_for(report(client,setup))).status_code==409


def test_duplicate_slots_and_unverified_scope_rejected(client, setup):
    body=body_for(report(client,setup));body['slots'][1]=body['slots'][0]
    assert client.put(path(setup),json=body).status_code==422
    body=body_for(report(client,setup));body['scope_confirmed']=True
    assert client.put(path(setup),json=body).status_code==422


def test_migration_preserves_database_level_growth_checks(client, setup):
    for assignment in ["refine_level=5", "stone_state='set',stone_level=9", "version=-1"]:
        with pytest.raises(IntegrityError), client.app.state.engine.begin() as connection:
            connection.execute(text('UPDATE character_slot_settings SET '+assignment))


def test_concurrent_edit_allows_only_one_winner(client, setup):
    body=body_for(report(client,setup));body['slots'][0]['refine_level']=6
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:client.put(path(setup),json=body).status_code,range(2)))
    assert sorted(statuses)==[200,409]

def test_growth_change_is_auditable_without_affecting_inventory(client, setup):
    body=body_for(report(client,setup));body['slots'][0]['refine_level']=6
    save(client,setup,body)
    history=client.get(f"/api/accounts/{setup['account']['id']}/history").json()
    assert history[0]['title']=='修改角色养成'
    assert setup['character']['name'] in history[0]['summary']
    assert client.get(f"/api/accounts/{setup['account']['id']}/daily").json()['warehouse']==[]


def test_stage3_migration_preserves_growth_and_initializes_unknown_calculation(tmp_path):
    from alembic import command
    from alembic.config import Config
    from backend.database import ROOT, make_engine, migrate
    engine=make_engine(tmp_path)
    config=Config(str(ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'backend'/'migrations'))
    with engine.begin() as connection:
        config.attributes['connection']=connection
        command.upgrade(config,'0004')
        connection.exec_driver_sql("INSERT INTO game_accounts (id,name,sort_order,archived,comparison_mode) VALUES ('a','existing',0,0,'strict')")
        connection.exec_driver_sql("INSERT INTO characters (id,account_id,name,sort_order,archived) VALUES ('c','a','existing',0,0)")
        connection.exec_driver_sql("INSERT INTO character_slot_settings (character_id,slot,refine_level,stone_state,stone_level) VALUES ('c','hat',6,'set',8)")
    migrate(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT refine_level,stone_state,stone_level,version FROM character_slot_settings WHERE slot='hat'").one()==(6,'set',8,0)
        assert connection.exec_driver_sql('SELECT additional_constitution,scope_confirmed,threshold_cycle_id,threshold_signature,version FROM character_calculation_settings').one()==(None,0,None,'',0)
        assert connection.exec_driver_sql('SELECT count(*) FROM character_slot_settings').scalar()==12
        assert connection.exec_driver_sql('PRAGMA integrity_check').scalar()=='ok'
        assert not connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()
    engine.dispose()
