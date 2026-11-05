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
