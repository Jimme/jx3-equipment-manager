from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.database import backup_database
from backend.main import create_app
from backend.models import EquipmentConfig, MutationReceipt, WornEquipment
from .conftest import equipment_payload, equipment_url


def request_id():
    return str(uuid4())


def path(setup):
    return f"/api/accounts/{setup['account']['id']}"


def daily(client, setup):
    return client.get(path(setup) + '/daily').json()


def add(client, setup, target='warehouse', quantity=1, choices=None, **equipment):
    payload = {'request_id': request_id(), 'equipment': equipment_payload(setup, **equipment), 'quantity': quantity}
    if choices is not None:
        payload['choices'] = choices
    response = client.post(path(setup) + '/' + target, json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def equip_payload(setup, item, **changes):
    return {'request_id': request_id(), 'character_id': setup['character']['id'], 'slot': 'hat', 'expected_version': item['version'], 'expected_equipment_version': 0, **changes}


def test_add_merges_and_duplicate_request_does_not_add_twice(client, setup):
    item, body = add(client, setup, quantity=2)
    response = client.post(path(setup) + '/warehouse', json=body)
    assert response.json() == item
    assert daily(client, setup)['warehouse'][0]['quantity'] == 2
    body['quantity'] = 3
    assert client.post(path(setup) + '/warehouse', json=body).status_code == 409
    clone, _ = add(client, setup, source_id=None, source_type='测试', source_name='测试来源', short_label='t')
    assert clone['id'] == item['id']
    assert clone['quantity'] == 3
    assert daily(client, setup)['drops'] == []


def test_different_attachment_configs_do_not_merge(client, setup):
    first, _ = add(client, setup)
    second, _ = add(client, setup, choices={'enchant': {'state': 'none'}})
    third, _ = add(client, setup, choices={'enchant': {'state': 'configured', 'name': '测试附魔'}})
    assert len({first['id'], second['id'], third['id']}) == 3
    assert third['choices']['enchant']['constitution'] is None
    assert first['equipment']['base_constitution_override'] is None


def test_equip_one_unit_receipt_and_old_equipment_not_returned(client, setup):
    with client.app.state.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE character_slot_settings SET refine_level=6,stone_state='set',stone_level=8 WHERE character_id=? AND slot='hat'", (setup['character']['id'],))
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, actual_grade=965)})
    item, _ = add(client, setup, quantity=2)
    body = equip_payload(setup, item, expected_equipment_version=1)
    url = path(setup) + f"/warehouse/{item['id']}/equip"
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).json()['quantity'] == 1
    warehouse = daily(client, setup)['warehouse']
    assert len(warehouse) == 1
    assert warehouse[0]['quantity'] == 1
    assert warehouse[0]['equipment']['actual_grade'] == 1110
    history = client.get(path(setup) + '/history').json()
    assert len([row for row in history if row['title'] == '从仓库穿戴']) == 1
    assert '旧装备不回库' in history[0]['summary']
    with client.app.state.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT refine_level,stone_level FROM character_slot_settings WHERE character_id=? AND slot='hat'", (setup['character']['id'],)).one() == (6, 8)


def test_last_unit_competition_only_one_character_gets_it(client, setup):
    other = client.post(path(setup) + '/characters', json={'name': '另一个角色'}).json()
    item, _ = add(client, setup)
    bodies = [equip_payload(setup, item), equip_payload(setup, item, character_id=other['id'])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda body: client.post(path(setup) + f"/warehouse/{item['id']}/equip", json=body).status_code, bodies))
    assert sorted(codes) == [200, 409]
    assert daily(client, setup)['warehouse'] == []
    matrix = client.get(path(setup) + '/matrix').json()
    assert sum(matrix['cells'][c]['hat']['equipment'] is not None for c in matrix['cells']) == 1


def test_parallel_identical_add_replays_without_double_count(client, setup):
    payload = {'request_id': request_id(), 'equipment': equipment_payload(setup), 'quantity': 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path(setup) + '/warehouse', json=payload), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert daily(client, setup)['warehouse'][0]['quantity'] == 1


def test_stale_worn_or_wrong_slot_rolls_back_all_changes(client, setup):
    item, _ = add(client, setup, quantity=2)
    with client.app.state.engine.connect() as connection:
        configs_before = len(connection.execute(select(EquipmentConfig.id)).all())
    body = equip_payload(setup, item, expected_equipment_version=99, choices={'enchant': {'state': 'none'}})
    url = path(setup) + f"/warehouse/{item['id']}/equip"
    assert client.post(url, json=body).status_code == 409
    body = equip_payload(setup, item, slot='weapon')
    assert client.post(url, json=body).status_code == 422
    current = daily(client, setup)['warehouse'][0]
    assert (current['quantity'], current['version']) == (2, item['version'])
    with client.app.state.engine.connect() as connection:
        assert len(connection.execute(select(EquipmentConfig.id)).all()) == configs_before
        assert connection.execute(select(MutationReceipt).where(MutationReceipt.request_id == body['request_id'])).first() is None


def test_overridden_enchant_only_changes_taken_item_and_replacement_resets(client, setup):
    item, _ = add(client, setup, quantity=2)
    body = equip_payload(setup, item, choices={'enchant': {'state': 'configured', 'name': '测试附魔', 'constitution': 0}})
    assert client.post(path(setup) + f"/warehouse/{item['id']}/equip", json=body).status_code == 200
    assert daily(client, setup)['warehouse'][0]['choices']['enchant']['state'] == 'unknown'
    matrix = client.get(path(setup) + '/matrix').json()
    assert matrix['cells'][setup['character']['id']]['hat']['choices']['enchant']['constitution'] == 0
    assert client.put(equipment_url(setup), json={'expected_version': 1, 'equipment': equipment_payload(setup, actual_grade=1200)}).status_code == 200
    matrix = client.get(path(setup) + '/matrix').json()
    assert matrix['cells'][setup['character']['id']]['hat']['choices']['enchant']['state'] == 'unknown'
    assert matrix['constitution']['stacks'] is None


def test_weapon_rainbow_and_non_weapon_rejection(client, setup):
    weapon, _ = add(client, setup, equipment_type='weapon', choices={'enchant': {'state': 'none'}, 'rainbow': {'state': 'configured', 'name': '测试五彩石'}})
    assert weapon['choices']['rainbow']['constitution'] is None
    assert client.post(path(setup) + f"/warehouse/{weapon['id']}/equip", json=equip_payload(setup, weapon, slot='weapon')).status_code == 200
    matrix = client.get(path(setup) + '/matrix').json()
    assert matrix['cells'][setup['character']['id']]['weapon']['choices']['rainbow']['name'] == '测试五彩石'
    response = client.post(path(setup) + '/warehouse', json={'request_id': request_id(), 'quantity': 1, 'equipment': equipment_payload(setup), 'choices': {'rainbow': {'state': 'none'}}})
    assert response.status_code == 422


@pytest.mark.parametrize('choice', [
    {'state': 'configured'}, {'state': 'unknown', 'constitution': 0}, {'state': 'none', 'name': '冲突'},
    {'state': 'configured', 'name': '测试', 'constitution': -1},
])
def test_invalid_attachment_choices_are_rejected(client, setup, choice):
    response = client.post(path(setup) + '/warehouse', json={'request_id': request_id(), 'quantity': 1, 'equipment': equipment_payload(setup), 'choices': {'enchant': choice}})
    assert response.status_code == 422
    assert daily(client, setup)['warehouse'] == []


def test_cross_account_all_inventory_paths_rejected(client, setup):
    item, _ = add(client, setup)
    note, _ = add(client, setup, target='drops')
    other = client.post('/api/accounts', json={'name': '其他账号'}).json()
    other_character = client.post(f"/api/accounts/{other['id']}/characters", json={'name': '其他角色'}).json()
    other_path = f"/api/accounts/{other['id']}"
    correction = {'request_id': request_id(), 'quantity': 0, 'expected_version': item['version']}
    assert client.post(other_path + f"/warehouse/{item['id']}/quantity", json=correction).status_code == 404
    assert client.post(other_path + f"/warehouse/{item['id']}/equip", json=equip_payload(setup, item, character_id=other_character['id'])).status_code == 404
    assert client.post(path(setup) + f"/warehouse/{item['id']}/equip", json=equip_payload(setup, item, character_id=other_character['id'])).status_code == 404
    assert client.post(other_path + f"/drops/{note['id']}/transfer", json={'request_id': request_id(), 'quantity': 1, 'expected_version': note['version']}).status_code == 404
    assert client.post(other_path + f"/drops/{note['id']}/quantity", json={'request_id': request_id(), 'quantity': 0, 'expected_version': note['version']}).status_code == 404
    assert client.get(other_path + '/daily').json()['warehouse'] == []
    assert client.get(other_path + '/history').json() == []


def test_drop_transfer_partial_full_and_replay(client, setup):
    stock, _ = add(client, setup, quantity=1)
    note, _ = add(client, setup, target='drops', quantity=3)
    assert daily(client, setup)['warehouse'][0]['quantity'] == 1
    body = {'request_id': request_id(), 'quantity': 2, 'expected_version': note['version']}
    url = path(setup) + f"/drops/{note['id']}/transfer"
    response = client.post(url, json=body)
    assert response.status_code == 200
    assert response.json()['warehouse_id'] == stock['id']
    assert client.post(url, json=body).json() == response.json()
    state = daily(client, setup)
    assert state['warehouse'][0]['quantity'] == 3
    assert state['drops'][0]['quantity'] == 1
    body = {'request_id': request_id(), 'quantity': 1, 'expected_version': state['drops'][0]['version']}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 200
    assert daily(client, setup)['drops'] == []
    assert daily(client, setup)['warehouse'][0]['quantity'] == 4


def test_drop_oversized_transfer_does_not_create_stock(client, setup):
    note, _ = add(client, setup, target='drops')
    response = client.post(path(setup) + f"/drops/{note['id']}/transfer", json={'request_id': request_id(), 'quantity': 2, 'expected_version': note['version']})
    assert response.status_code == 409
    assert daily(client, setup)['warehouse'] == []
    assert daily(client, setup)['drops'][0]['quantity'] == 1


def test_clear_stale_memo_is_rejected_and_valid_clear_keeps_stock(client, setup):
    add(client, setup, quantity=2)
    add(client, setup, target='drops')
    old_version = daily(client, setup)['drop_version']
    add(client, setup, target='drops')
    url = path(setup) + '/drops/clear'
    assert client.post(url, json={'request_id': request_id(), 'expected_version': old_version}).status_code == 409
    body = {'request_id': request_id(), 'expected_version': daily(client, setup)['drop_version']}
    assert client.post(url, json=body).json()['cleared'] == 2
    assert client.post(url, json=body).json()['cleared'] == 2
    assert daily(client, setup)['warehouse'][0]['quantity'] == 2
    assert daily(client, setup)['drops'] == []


def test_concurrent_drop_transfer_only_once(client, setup):
    note, _ = add(client, setup, target='drops')
    def transfer(_):
        return client.post(path(setup) + f"/drops/{note['id']}/transfer", json={'request_id': request_id(), 'expected_version': note['version'], 'quantity': 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(transfer, range(2)))
    assert codes == [200, 404]
    assert daily(client, setup)['warehouse'][0]['quantity'] == 1
    assert daily(client, setup)['drops'] == []


def test_clearing_one_account_never_clears_another(client, setup):
    note, _ = add(client, setup, target='drops')
    other = client.post('/api/accounts', json={'name': '另一个团本账号'}).json()
    other_path = f"/api/accounts/{other['id']}"
    body = {'request_id': request_id(), 'quantity': 1, 'equipment': equipment_payload(setup)}
    assert client.post(other_path + '/drops', json=body).status_code == 200
    assert client.post(other_path + '/drops/clear', json={'request_id': request_id(), 'expected_version': 1}).status_code == 200
    assert daily(client, setup)['drops'][0]['id'] == note['id']


def test_quantity_corrections_are_versioned_and_audited(client, setup):
    item, _ = add(client, setup)
    url = path(setup) + f"/warehouse/{item['id']}/quantity"
    body = {'request_id': request_id(), 'expected_version': item['version'], 'quantity': 2}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json={**body, 'request_id': request_id(), 'quantity': 0}).status_code == 409
    current = daily(client, setup)['warehouse'][0]
    assert client.post(url, json={'request_id': request_id(), 'expected_version': current['version'], 'quantity': 0}).status_code == 200
    assert daily(client, setup)['warehouse'] == []
    assert client.get(path(setup) + '/history').json()[0]['title'] == '修正仓库数量'


def test_gaps_use_mode_and_show_shared_candidate_without_allocating(client, setup):
    account_path = path(setup)
    client.put(account_path + '/preferences', json={'target_grade': 1110, 'comparison_mode': 'combined'})
    client.put(equipment_url(setup), json={'expected_version': 0, 'equipment': equipment_payload(setup, has_constitution_socket=False)})
    stock, _ = add(client, setup)
    second = client.post(account_path + '/characters', json={'name': '第二角色'}).json()
    state = daily(client, setup)
    hats = [g for g in state['gaps'] if g['slot'] == 'hat']
    assert len(hats) == 2
    assert all(g['candidate_ids'] == [stock['id']] for g in hats)
    assert state['warehouse'][0]['quantity'] == 1
    client.put(account_path + '/preferences', json={'target_grade': 1110, 'comparison_mode': 'grade'})
    assert all(g['character_id'] == second['id'] for g in daily(client, setup)['gaps'] if g['slot'] == 'hat')


def test_inventory_backup_restore_preserves_receipts_and_unknowns(client, setup, tmp_path):
    stock, body = add(client, setup, choices={'enchant': {'state': 'configured', 'name': '仅记录名称'}})
    add(client, setup, target='drops', quantity=2)
    backup = backup_database(Path(client.app.state.engine.url.database))
    restored = tmp_path / 'restored'
    restored.mkdir()
    (restored / 'app.sqlite3').write_bytes(backup.read_bytes())
    with TestClient(create_app(restored)) as restored_client:
        state = daily(restored_client, setup)
        assert state['warehouse'][0]['choices']['enchant']['constitution'] is None
        assert state['drops'][0]['quantity'] == 2
        assert restored_client.post(path(setup) + '/warehouse', json=body).json() == stock
        assert daily(restored_client, setup)['warehouse'][0]['quantity'] == 1
