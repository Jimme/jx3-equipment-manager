import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        yield client


@pytest.fixture
def setup(client):
    cycle = client.post('/api/cycles', json={'name': '测试周期（非游戏数据）'}).json()
    account = client.post('/api/accounts', json={'name': '测试账号'}).json()
    character = client.post(f"/api/accounts/{account['id']}/characters", json={'name': '测试角色'}).json()
    source = client.post(f"/api/cycles/{cycle['id']}/sources", json={
        'source_type': '测试', 'name': '测试来源', 'short_label': 't', 'default_grade': 1110,
    }).json()
    return {'cycle': cycle, 'account': account, 'character': character, 'source': source}


def equipment_payload(setup, **overrides):
    return {
        'cycle_origin_id': setup['cycle']['id'], 'source_id': setup['source']['id'],
        'equipment_type': 'hat', 'actual_grade': 1110,
        'has_constitution_socket': True, 'green_stats_ok': True,
        **overrides,
    }


def equipment_url(setup, slot='hat'):
    return f"/api/accounts/{setup['account']['id']}/characters/{setup['character']['id']}/equipment/{slot}"


def legacy_document(document, revision):
    """Construct an actual pre-0010 format, not a new format with an old label."""
    import copy
    result = copy.deepcopy(document)
    result['schema_revision'] = revision
    for name in ('creation_receipts', 'raid_cd_settings'):
        result['tables'].pop(name, None)
    for row in result['tables']['game_accounts']:
        row.pop('version', None)
    for row in result['tables']['characters']:
        for key in ('version', 'raid_version', 'history_complete'):
            row.pop(key, None)
    return result


def legacy_database_columns(connection):
    for table in ('creation_receipts', 'raid_cd_settings'):
        connection.execute('DROP TABLE ' + table)
    connection.execute('ALTER TABLE game_accounts DROP COLUMN version')
    for name in ('version', 'raid_version', 'history_complete'):
        connection.execute('ALTER TABLE characters DROP COLUMN ' + name)
