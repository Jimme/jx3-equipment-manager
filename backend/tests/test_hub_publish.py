import json
from types import SimpleNamespace

import pytest

from scripts import publish_hub as hub


@pytest.mark.parametrize('info',[{'namespace':'jimmeyoumu','name':'jx3-equipment-manager','is_private':False},{'namespace':'someone-else','name':'jx3-equipment-manager','is_private':True},{'namespace':'jimmeyoumu','name':'jx3-equipment-manager','is_private':'true'},{}])
def test_private_repo_guard_rejects_public_wrong_owner_and_unknown(info):
    with pytest.raises(hub.PublishError):hub.require_private(info,'jimmeyoumu','jx3-equipment-manager')


def arrange(monkeypatch,check_only=False,private=True,version='0.1.1'):
    calls=[];http=[]
    def command(args,capture=True,secret=None):
        calls.append((args,secret))
        if args[1:3]==['image','inspect']:return json.dumps([{'Id':'sha256:test','Os':'linux','Architecture':'amd64','Size':171173772}])
        if args[1]=='inspect':return 'sha256:test'
        if args[1]=='run':return version
        return ''
    def request(path,payload=None,bearer=None,missing_ok=False):
        http.append(path)
        if path=='/v2/auth/token':return {'access_token':'test-jwt-not-a-credential'}
        if '/tags/' in path:return None
        return {'namespace':'jimmeyoumu','name':'jx3-equipment-manager','is_private':private}
    monkeypatch.setattr(hub,'command',command)
    monkeypatch.setattr(hub,'json_request',request)
    monkeypatch.setattr(hub.getpass,'getpass',lambda prompt:'fake-test-only')
    monkeypatch.setattr(hub.sys,'stdin',SimpleNamespace(isatty=lambda:True))
    monkeypatch.setattr(hub.sys,'argv',['publish_hub.py','--version','0.1.1']+(['--check-only'] if check_only else []))
    return calls,http


def test_check_only_never_authenticates_or_pushes(monkeypatch):
    calls,http=arrange(monkeypatch,check_only=True)
    hub.main()
    assert http==[]
    assert not any('push' in args or 'login' in args for args,_ in calls)


def test_public_repo_is_rejected_before_docker_login_and_upload(monkeypatch):
    calls,_=arrange(monkeypatch,private=False)
    with pytest.raises(hub.PublishError):hub.main()
    assert not any('push' in args or 'login' in args for args,_ in calls)


def test_wrong_image_version_is_rejected_before_network_or_upload(monkeypatch):
    calls,http=arrange(monkeypatch,version='0.0.0')
    with pytest.raises(hub.PublishError):hub.main()
    assert http==[]


def test_private_publish_uses_secret_stdin_and_fixed_version_before_stable(monkeypatch):
    calls,_=arrange(monkeypatch)
    hub.main()
    login=[(args,secret) for args,secret in calls if 'login' in args]
    assert len(login)==1 and '--password-stdin' in login[0][0]
    assert login[0][1]=='fake-test-only\n'
    assert all('fake-test-only' not in ' '.join(args) for args,_ in calls)
    pushed=[args[-1] for args,_ in calls if 'push' in args]
    assert pushed==['jimmeyoumu/jx3-equipment-manager:0.1.1','jimmeyoumu/jx3-equipment-manager:stable']


def test_create_private_explicitly_and_recheck_before_pushing(monkeypatch):
    calls,_=arrange(monkeypatch)
    original=hub.json_request
    created=[]
    def request(path,payload=None,bearer=None,missing_ok=False):
        if path=='/v2/namespaces/jimmeyoumu/repositories':
            created.append(payload)
            return {}
        if path=='/v2/namespaces/jimmeyoumu/repositories/jx3-equipment-manager' and not created:
            return None
        return original(path,payload,bearer,missing_ok)
    monkeypatch.setattr(hub,'json_request',request)
    monkeypatch.setattr(hub.sys,'argv',['publish_hub.py','--version','0.1.1','--create-private'])
    hub.main()
    assert len(created)==1 and created[0]['is_private'] is True
    assert len([args for args,_ in calls if 'push' in args])==2


def test_public_publish_requires_explicit_visibility(monkeypatch):
    calls,_=arrange(monkeypatch,private=False)
    monkeypatch.setattr(hub.sys,'argv',['publish_hub.py','--version','0.1.1','--visibility','public'])
    hub.main()
    assert len([args for args,_ in calls if 'push' in args])==2


def test_public_expectation_rejects_private_or_wrong_owner():
    for info in [dict(namespace='jimmeyoumu',name='jx3-equipment-manager',is_private=True), dict(namespace='other',name='jx3-equipment-manager',is_private=False)]:
        with pytest.raises(hub.PublishError):hub.require_visibility(info,'jimmeyoumu','jx3-equipment-manager','public')
