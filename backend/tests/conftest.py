import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from backend.models import Source
from backend.services import new_id

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
        'source_type': '测试', 'name': '测试来源', 'short_label': 't', 'actual_grade': 1110,
    }).json()
    return {'cycle': cycle, 'account': account, 'character': character, 'source': source, 'client': client}


def equipment_payload(setup, **overrides):
    source = setup['source']
    grade = overrides.pop('actual_grade', None)
    if grade is not None and type(grade) is int and 0 < grade <= 2147483647 and grade != source['actual_grade']:
        cache = setup.setdefault('fixture_sources', {})
        if grade not in cache:
            with Session(setup['client'].app.state.engine) as session:
                alternate = Source(id=new_id(), cycle_id=source['cycle_id'], source_type=source['source_type'],
                                   name=source['name'], short_label=source['short_label'], actual_grade=grade)
                session.add(alternate)
                session.commit()
                cache[grade] = alternate.id
        source_id = cache[grade]
    else:
        source_id = source['id']
        if grade is not None and (type(grade) is not int or not 0 < grade <= 2147483647):
            overrides['actual_grade'] = grade
    return {'source_id': source_id, 'equipment_type': 'hat', 'has_constitution_socket': True,
            'green_stats_ok': True, **overrides}


def equipment_url(setup, slot='hat'):
    return f"/api/accounts/{setup['account']['id']}/characters/{setup['character']['id']}/equipment/{slot}"
