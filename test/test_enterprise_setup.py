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
    payload = dict(setup_code=(settings.data_dir/'setup-code').read_text().strip(),
                   username='admin', password='unique administrator password')
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


def test_setup_code_and_origin_protection(server):
    client, app, settings = server
    assert create_admin(client, settings, setup_code='wrong').status_code == 403
    assert create_admin(client, settings, setup_code='错误初始化码').status_code == 403
    payload=dict(setup_code=(settings.data_dir/'setup-code').read_text().strip(),username='admin',password='long enough password')
    assert client.post('/api/v1/setup', json=payload, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
    assert create_admin(client, settings, password='short').status_code == 400
    assert client.get('/api/v1/setup').json()['required']
    with app.state.database.transaction() as db:
        assert db.scalar(select(Member)) is None


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
    for _ in range(5):
        assert create_admin(client, settings, setup_code='wrong').status_code == 403
    assert create_admin(client, settings).status_code == 429
    with app.state.database.transaction() as db:
        db.get(Installation,1).locked_until=time.time()-1
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
