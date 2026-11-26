"""Initial deployment must be configurable without an enterprise identity provider."""
from concurrent.futures import ThreadPoolExecutor
import time

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import AuthSource, Installation, LocalAdministrator, Member


@pytest.fixture
def server(tmp_path):
    settings = Settings(database_url='sqlite:///' + str(tmp_path/'db.sqlite'), data_dir=tmp_path,
                        platform_id='setup-test', platform_name='初始化测试', public_url='http://localhost',
                        encryption_key=Fernet.generate_key().decode(), development=True)
    app = create_app(settings)
    with TestClient(app, base_url='http://localhost') as client:
        yield client, app, settings


def create_admin(client, settings, **changes):
    payload = dict(username='admin', password='unique administrator password')
    payload.update(changes)
    return client.post('/api/v1/setup', json=payload)


def test_setup_login_and_configuration_without_enterprise_auth(server):
    client, app, settings = server
    assert client.get('/api/v1/setup').json() == {'required': True, 'administrator_login': False}
    assert client.get('/api/v1/admin/settings').status_code == 401
    assert create_admin(client, settings).status_code == 200
    me = client.get('/api/v1/me').json()['member']
    assert me['role'] == 'admin'
    assert me['provider_id'] == '__platform_admin__'
    csrf = {'X-CSRF-Token': client.cookies['cowork_csrf']}
    assert client.get('/api/v1/admin/settings').status_code == 200
    source = dict(id='directory', name='企业目录', mode='ldap', enabled=False,
                  config=dict(host='directory.example.com', base_dn='dc=example,dc=com',
                              user_filter='(uid={username})', identity_attribute='entryUUID', tls='ldaps'), secrets={})
    assert client.put('/api/v1/admin/auth-sources/directory', json=source).status_code == 403
    assert client.put('/api/v1/admin/auth-sources/directory', headers=csrf, json=source).status_code == 200
    assert client.post('/api/v1/auth/logout', headers=csrf).status_code == 200
    assert client.post('/api/v1/auth/administrator', json={'username':'admin','password':'wrong'}).status_code == 401
    assert client.post('/api/v1/auth/administrator', json={'username':'admin','password':'unique administrator password'}).status_code == 200
    with app.state.database.transaction() as db:
        credential = db.get(LocalAdministrator, 1)
        assert credential.password_hash != 'unique administrator password'
        assert len(credential.password_hash) == 128


def test_setup_validation_and_origin_protection(server):
    client, app, settings = server
    payload=dict(username='admin',password='long enough password')
    assert client.post('/api/v1/setup', json=payload, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
    assert create_admin(client, settings, password='short').status_code == 400
    assert client.get('/api/v1/setup').json()['required']
    with app.state.database.transaction() as db:
        assert db.scalar(select(Member)) is None


def test_intranet_http_initialization_keeps_origin_and_csrf_checks(tmp_path):
    url = 'http://192.168.1.50:8788'
    settings = Settings(database_url='sqlite:///' + str(tmp_path/'db.sqlite'), data_dir=tmp_path,
                        platform_id='intranet', platform_name='内网平台', public_url=url,
                        encryption_key=Fernet.generate_key().decode(), development=True, allow_platform_http=True)
    with TestClient(create_app(settings), base_url=url) as client:
        credentials = {'username': 'admin', 'password': 'unique administrator password'}
        assert client.post('/api/v1/setup', json=credentials, headers={'Origin': 'http://192.168.1.50:8789'}).status_code == 403
        created = client.post('/api/v1/setup', json=credentials, headers={'Origin': url})
        assert created.status_code == 200
        cookies = created.headers.get_list('set-cookie')
        assert cookies and all('; secure' not in value.lower() for value in cookies)
        assert client.get('/api/v1/admin/settings').status_code == 200
        assert client.post('/api/v1/auth/logout', headers={'Origin': url}).status_code == 403
        csrf = client.cookies['cowork_csrf']
        assert client.post('/api/v1/auth/logout', headers={'Origin': url, 'X-CSRF-Token': csrf}).status_code == 200


def test_consumed_setup_never_reopens(server):
    client, app, settings = server
    assert create_admin(client, settings).status_code == 200
    assert create_admin(client, settings).status_code == 409
    with app.state.database.transaction() as db:
        db.execute(delete(LocalAdministrator))
        db.execute(delete(Member))
    assert not client.get('/api/v1/setup').json()['required']
    assert create_admin(client, settings).status_code == 409


def test_existing_installation_cannot_be_claimed(server):
    client, app, settings = server
    with app.state.database.transaction() as db:
        db.add(Member(provider_id='sso', subject='existing', name='existing', role='admin'))
    assert not client.get('/api/v1/setup').json()['required']
    assert create_admin(client, settings).status_code == 409


def test_setup_rejects_concurrent_claim(server):
    client, app, settings = server
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create_admin(client, settings), range(2)))
    assert sorted(r.status_code for r in responses) == [200,409]


def test_throttling_persists_and_recovers(server):
    client, app, settings = server
    assert create_admin(client, settings).status_code == 200
    for _ in range(5):
        assert client.post('/api/v1/auth/administrator',json={'username':'admin','password':'wrong'}).status_code == 401
    assert client.post('/api/v1/auth/administrator',json={'username':'admin','password':'unique administrator password'}).status_code == 429


def test_disabled_admin_cannot_login(server):
    client, app, settings = server
    assert create_admin(client, settings).status_code == 200
    assert client.post('/api/v1/auth/administrator',json={'username':'错误账号','password':'wrong'}).status_code == 401
    with app.state.database.transaction() as db:
        db.scalar(select(Member)).enabled=False
    assert client.post('/api/v1/auth/administrator',json={'username':'admin','password':'unique administrator password'}).status_code == 401


def test_only_founder_can_authorize_another_administrator_and_demote_web_sessions(server):
    client, app, settings = server
    assert create_admin(client, settings).status_code == 200
    founder=client.get('/api/v1/me').json()['member']['id']
    with app.state.database.transaction() as db:
        member=Member(provider_id='sso',subject='employee',name='员工')
        another=Member(provider_id='sso',subject='another',name='另一个员工')
        db.add_all([member,another]);db.flush()
        member_id,another_id=member.id,another.id
    payload={'revision':1,'role':'admin'}
    desktop=app.state.auth.session(member_id,'desktop')
    headers={'Authorization':'Bearer '+desktop['access_token']}
    assert client.put('/api/v1/admin/members/'+member_id,json=payload,headers=headers).status_code==403
    csrf={'X-CSRF-Token':client.cookies['cowork_csrf']}
    assert client.put('/api/v1/admin/members/'+member_id,json=payload,headers=csrf).status_code==200
    web=app.state.auth.session(member_id,'web')
    web_headers={'Authorization':'Bearer '+web['access_token']}
    assert client.get('/api/v1/me',headers=web_headers).json()['member']['role']=='admin'
    assert client.put('/api/v1/admin/members/'+another_id,json=payload,headers=headers).status_code==403
    assert client.put('/api/v1/admin/members/'+founder,json={'revision':1,'enabled':False},headers=headers).status_code==409
    assert client.put('/api/v1/admin/members/'+member_id,json={'revision':2,'role':'member'},headers=csrf).status_code==200
    assert client.get('/api/v1/me',headers=web_headers).status_code==401
    assert client.get('/api/v1/me',headers=headers).json()['member']['role']=='member'
    assert client.put('/api/v1/admin/members/'+member_id,json={'revision':3,'role':'admin'},headers=csrf).status_code==200
    assert client.get('/api/v1/me',headers=web_headers).status_code==401
    assert create_admin(client,settings).status_code==409


def test_existing_employee_web_session_is_rejected_as_cookie_and_bearer(server):
    from web.platform.server.database import LoginSession
    from web.platform.server.security import digest
    client, app, _ = server
    with app.state.database.transaction() as db:
        member = Member(provider_id='legacy', subject='employee', name='员工')
        db.add(member)
        db.flush()
        db.add(LoginSession(token_hash=digest('previous-web-token'), member_id=member.id,
                            kind='web', expires_at=time.time() + 60))
    assert client.get('/api/v1/me', headers={'Cookie':'cowork_session=previous-web-token'}).status_code == 401
    assert client.get('/api/v1/me', headers={'Authorization':'Bearer previous-web-token'}).status_code == 401


@pytest.mark.parametrize("owner_state", ["unconfigured", "disabled", "active"])
def test_legacy_role_changes_require_an_explicit_enabled_super_admin(tmp_path, owner_state):
    from web.platform.server.database import DatabaseAccount
    settings = Settings(database_url='sqlite:///' + str(tmp_path / 'db.sqlite'), data_dir=tmp_path,
                        platform_id='legacy-roles', platform_name='旧版平台', public_url='http://localhost',
                        encryption_key=Fernet.generate_key().decode(), development=True,
                        bootstrap_provider='' if owner_state == 'unconfigured' else 'staff', bootstrap_subject='corporate-owner')
    app = create_app(settings)
    source = {'id': 'staff', 'name': '员工账号', 'mode': 'database', 'config': {}, 'secrets': {}, 'enabled': True}
    with TestClient(app, base_url='http://localhost') as client:
        auth = app.state.auth
        auth.save_source(source)
        with app.state.database.transaction() as db:
            delegate = Member(provider_id='staff', subject='delegate', name='平台管理员', role='admin')
            owner = Member(provider_id='staff', subject='corporate-owner', name='企业账号', role='admin',
                           enabled=owner_state != 'disabled')
            employee = Member(provider_id='staff', subject='employee', name='员工')
            db.add_all([delegate, owner, employee])
            db.flush()
            db.add(DatabaseAccount(member_id=owner.id, provider_id='staff', username='owner.account',
                                   password_hash='unused-test-hash', salt='unused-test-salt'))
        delegated = {'Authorization': 'Bearer ' + auth.session(delegate.id, 'web')['access_token']}
        assert client.get('/api/v1/me', headers=delegated).json()['can_assign_admin'] is False
        member_list = client.get('/api/v1/admin/members', headers=delegated).json()['items']
        assert [item['id'] for item in member_list if item['is_super_admin']] == ([owner.id] if owner_state == 'active' else [])
        member_api = '/api/v1/admin/members/'
        denied = client.put(member_api + employee.id, headers=delegated, json={'revision': 1, 'role': 'admin'})
        assert denied.status_code == 403
        assert denied.json()['error']['code'] == ('founder_required' if owner_state == 'active' else 'super_admin_unavailable')
        assert client.put(member_api + employee.id, headers=delegated, json={'revision': 1, 'enabled': True}).status_code == 200
        assert client.put('/api/v1/admin/auth-sources/other', headers=delegated,
                          json={**source, 'name': '另一认证来源'}).status_code == 200
        if owner_state != 'unconfigured':
            protected_source = client.put('/api/v1/admin/auth-sources/staff', headers=delegated,
                                          json={**source, 'revision': 1, 'enabled': False})
            assert protected_source.status_code == 403
            protected_password = client.put('/api/v1/admin/auth-sources/staff/accounts/' + owner.id, headers=delegated,
                json={'revision': 1, 'member_revision': 1, 'name': '企业账号', 'password': 'another password'})
            assert protected_password.status_code == 403
        if owner_state == 'active':
            super_header = {'Authorization': 'Bearer ' + auth.session(owner.id, 'web')['access_token']}
            me = client.get('/api/v1/me', headers=super_header).json()
            assert me['can_assign_admin'] is True and me['can_use_team_spaces'] is True
            assert client.put(member_api + owner.id, headers=delegated,
                              json={'revision': 1, 'enabled': False}).status_code == 409
            assert client.put(member_api + employee.id, headers=super_header,
                              json={'revision': 2, 'role': 'admin'}).status_code == 200
            assert client.put(member_api + employee.id, headers=super_header,
                              json={'revision': 3, 'role': 'member'}).status_code == 200


def test_legacy_bootstrap_cannot_grant_admin_after_setup(server):
    from dataclasses import replace
    client,app,settings=server
    assert create_admin(client,settings).status_code==200
    app.state.auth.settings=replace(settings,bootstrap_provider='legacy',bootstrap_subject='new-person')
    with app.state.database.transaction() as db:
        db.add(AuthSource(id='legacy',name='legacy',mode='oauth2',config={},secret='',enabled=True,revision=1,verified_revision=1))
    member_id=app.state.auth.register({'id':'legacy','revision':1,'verified_revision':1},{'subject':'new-person','name':'new-person','email':''})
    with app.state.database.transaction() as db:
        assert db.get(Member,member_id).role=='member'
