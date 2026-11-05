import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.container_start import prepare_data
from backend.database import ROOT


def test_container_start_drops_root_and_only_prepares_application_files(tmp_path,monkeypatch):
    directory=tmp_path/'data';directory.mkdir()
    database=directory/'app.sqlite3';database.write_bytes(b'existing-database')
    other=directory/'unrelated.txt';other.write_bytes(b'keep')
    calls=[];identity={'uid':0}
    monkeypatch.setattr(os,'geteuid',lambda:identity['uid'],raising=False)
    monkeypatch.setattr(os,'chown',lambda path,uid,gid,**kw:calls.append(('chown',Path(path),uid,gid)),raising=False)
    monkeypatch.setattr(os,'setgroups',lambda groups:calls.append(('groups',groups)),raising=False)
    monkeypatch.setattr(os,'setgid',lambda gid:calls.append(('gid',gid)),raising=False)
    def setuid(uid):identity['uid']=uid;calls.append(('uid',uid))
    monkeypatch.setattr(os,'setuid',setuid,raising=False)
    prepare_data(directory,10001,10001)
    assert identity['uid']==10001
    changed={call[1] for call in calls if call[0]=='chown'}
    assert changed=={directory,directory/'backups',database}
    assert other.read_bytes()==b'keep' and database.read_bytes()==b'existing-database'
    assert calls[-3:]==[('groups',[]),('gid',10001),('uid',10001)]


@pytest.mark.parametrize('uid,gid',[(0,10001),(10001,0),(-1,10001)])
def test_container_refuses_root_target_identity_without_creating_data(tmp_path,uid,gid):
    directory=tmp_path/'data'
    with pytest.raises(RuntimeError):prepare_data(directory,uid,gid)
    assert not directory.exists()


def test_deployment_package_preserves_existing_data_and_env_and_excludes_local_database(tmp_path):
    if os.name!='nt':pytest.skip('Windows packaging workflow')
    target=tmp_path/'package';target.mkdir();(target/'data').mkdir()
    marker=target/'data'/'app.sqlite3';marker.write_bytes(b'user-data-do-not-touch')
    env=target/'.env';env.write_text('JX3_PORT=12345',encoding='utf-8')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts'/'Prepare-Deployment.ps1'),'-Destination',str(target)],cwd=ROOT,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert marker.read_bytes()==b'user-data-do-not-touch' and env.read_text()=='JX3_PORT=12345'
    manifest=json.loads((target/'DEPLOYMENT-MANIFEST.json').read_text(encoding='utf-8-sig'))
    files={row['path'] for row in manifest['files']}
    assert {'compose.yaml','Dockerfile','backend/main.py','frontend/index.html'}<=files
    assert not any(name.startswith(('data/','.tools/','.venv/')) or 'node_modules' in name or '.sqlite3' in name for name in files)
    assert (target/'compose.yaml').read_bytes()==(ROOT/'compose.yaml').read_bytes()
    assert (ROOT/'compose.yaml').read_bytes()==(ROOT/'docker-compose.yml').read_bytes()
    # Run the delivered source against local temporary state, never the NAS data directory.
    smoke_env={**os.environ,'JX3_DATA_DIR':str(tmp_path/'smoke-data'),'PYTHONDONTWRITEBYTECODE':'1'}
    script='''
from pathlib import Path
import os
from fastapi.testclient import TestClient
from backend.main import create_app
path=Path(os.environ['JX3_DATA_DIR'])
with TestClient(create_app(path)) as c:
 assert c.get('/api/workspace').json()['accounts']==[]
 account=c.post('/api/accounts',json={'name':'package-smoke'}).json()
 assert c.post('/api/data/backups').status_code==201
with TestClient(create_app(path)) as c:
 assert c.get('/api/workspace').json()['accounts'][0]['id']==account['id']
 assert c.get('/api/health').json()['version']=='0.1.4'
print('Delivered-source startup and persistence passed.')
'''
    smoke=subprocess.run([sys.executable,'-c',script],cwd=target,env=smoke_env,capture_output=True)
    assert smoke.returncode==0,smoke.stdout+smoke.stderr
    assert marker.read_bytes()==b'user-data-do-not-touch'

def test_deployment_update_preserves_custom_compose_and_offers_reference(tmp_path):
    import hashlib
    if os.name!='nt':pytest.skip('Windows packaging workflow')
    target=tmp_path/'custom';target.mkdir()
    custom=b'services:\n  app:\n    image: keep-my-existing-config\n'
    (target/'compose.yaml').write_bytes(custom)
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts'/'Prepare-Deployment.ps1'),'-Destination',str(target)],cwd=ROOT,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert (target/'compose.yaml').read_bytes()==custom
    assert (target/'compose.reference.yaml').read_bytes()==(ROOT/'compose.yaml').read_bytes()
    manifest=json.loads((target/'DEPLOYMENT-MANIFEST.json').read_text(encoding='utf-8-sig'))
    entry=next(row for row in manifest['files'] if row['path']=='compose.yaml')
    assert entry['sha256'].lower()==hashlib.sha256(custom).hexdigest()
