"""Managed database accounts use the existing browser and desktop handoff contract."""
import re
import threading
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from test_enterprise_setup import server, create_admin
from test_enterprise_client import TestProtector
from core.enterprise_client import EnterpriseClient, EnterpriseStore
from web.platform.server.database import AuthSource, DatabaseAccount, LoginTransaction, Member
from web.platform.server.security import challenge, PlatformError

PASSWORD='employee password 123'


@pytest.fixture
def local_auth(server):
    client,app,settings=server
    assert create_admin(client,settings).status_code==200
    headers={'Authorization':'Bearer '+client.cookies['cowork_session']}
    source={'id':'staff','name':'员工账号','mode':'database','config':{},'secrets':{},'enabled':False}
    assert client.put('/api/v1/admin/auth-sources/staff',headers=headers,json=source).status_code==200
    response=client.post('/api/v1/admin/auth-sources/staff/accounts',headers=headers,json={
        'username':'employee','name':'测试员工','password':PASSWORD})
    assert response.status_code==200,response.text
    account=response.json()
    return client,app,settings,headers,source,account


def transaction(client, *, source='staff', test=False, headers=None, desktop=False):
    verifier='a'*48
    response=client.post('/api/v1/auth/transactions',headers=headers or {},json={
        'provider_id':source,'mode':'desktop' if desktop else 'web','test':test,
        'code_challenge':challenge(verifier) if desktop else ''})
    assert response.status_code==200,response.text
    tx=response.json()
    response=client.get(tx['login_url'],follow_redirects=False)
    assert response.status_code==302,response.text
    tx['form_url']=response.headers['location']
    form=client.get(tx['form_url'])
    assert form.status_code==200,form.text
    tx['state']=re.search("name='state' value='([^']+)'",form.text).group(1)
    tx['verifier']=verifier
    return tx


def submit(client,tx,password=PASSWORD,username='employee'):
    return client.post(tx['form_url'],data={'state':tx['state'],'username':username,'password':password},follow_redirects=False)


def enable(local_auth):
    client,app,settings,headers,source,account=local_auth
    response=client.put('/api/v1/admin/auth-sources/staff',headers=headers,json={**source,'revision':1,'enabled':True})
    assert response.status_code==200,response.text


def save_account(local_auth,account,**changes):
    client,app,settings,headers,source,_=local_auth
    return client.put('/api/v1/admin/auth-sources/staff/accounts/'+account['id'],headers=headers,json={**account,**changes})


def exchange(client,tx):
    prefix='/api/v1/auth/transactions/'+tx['id']
    status=client.post(prefix+'/status',json={'verifier':tx['verifier']}).json()
    return client.post(prefix+'/exchange',json={'verifier':tx['verifier'],'handoff_code':status.get('handoff_code','')})


def test_admin_create_enable_employee_web_login(local_auth):
    client,app,settings,headers,source,account=local_auth
    assert client.post('/api/v1/auth/transactions',json={'provider_id':'staff','mode':'web'}).status_code==404
    enable(local_auth)
    tx=transaction(client)
    bad=submit(client,tx,password='wrong')
    assert bad.status_code==401 and "value='employee'" in bad.text
    assert submit(client,tx).status_code==303
    me=client.get('/api/v1/me').json()
    assert me['member']['id']==account['id'] and me['member']['role']=='member'
    assert me['models']==[] and not me['can_assign_admin']
    assert client.get('/api/v1/admin/settings').status_code==403
    listing=client.get('/api/v1/admin/auth-sources/staff/accounts',headers=headers).json()
    assert PASSWORD not in str(listing) and 'password_hash' not in str(listing) and 'salt' not in str(listing)
    with app.state.database.transaction() as db:
        credential=db.get(DatabaseAccount,account['id'])
        assert len(credential.password_hash)==128 and credential.password_hash!=PASSWORD


def test_enable_empty_source_then_create_account(server):
    client,app,settings=server
    assert create_admin(client,settings).status_code==200
    headers={'Authorization':'Bearer '+client.cookies['cowork_session']}
    source={'id':'staff','name':'员工账号','mode':'database','config':{},'secrets':{},'enabled':True}
    assert client.put('/api/v1/admin/auth-sources/staff',headers=headers,json=source).status_code==200
    assert client.put('/api/v1/admin/login-entry',headers=headers,json={'provider_id':'staff','revision':0}).status_code==200
    assert client.post('/api/v1/admin/auth-sources/staff/accounts',headers=headers,json={
        'username':'employee','name':'测试员工','password':PASSWORD}).status_code==200
    tx=transaction(client)
    assert submit(client,tx).status_code==303


def test_native_form_origin_policy_and_csrf(local_auth):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    tx=transaction(client)
    assert client.get(tx['form_url']).headers['referrer-policy']=='strict-origin'
    for origin in ('null','https://other.example'):
        response=client.post(tx['form_url'],headers={'Origin':origin},data={
            'state':tx['state'],'username':'employee','password':PASSWORD})
        assert response.status_code==403 and response.json()['error']['code']=='origin_rejected'
    response=client.post(tx['form_url'],headers={'Origin':settings.origin},data={
        'state':tx['state'],'username':'employee','password':'wrong'})
    assert response.status_code==401 and response.headers['referrer-policy']=='strict-origin'
    assert client.post(tx['form_url'],headers={'Origin':settings.origin},data={
        'state':tx['state'],'username':'employee','password':PASSWORD},follow_redirects=False).status_code==303
    assert client.get('/app').headers['referrer-policy']=='no-referrer'


def test_duplicate_username_and_source_isolation(local_auth):
    client,app,settings,headers,source,account=local_auth
    payload={'username':'employee','name':'其他员工','password':PASSWORD,'role':'admin'}
    assert client.post('/api/v1/admin/auth-sources/staff/accounts',headers=headers,json=payload).status_code==409
    assert client.put('/api/v1/admin/auth-sources/other',headers=headers,json={**source,'id':'other'}).status_code==200
    second=client.post('/api/v1/admin/auth-sources/other/accounts',headers=headers,json=payload).json()
    assert second['id']!=account['id'] and second['role']=='member'
    assert client.put('/api/v1/admin/auth-sources/other/accounts/'+account['id'],headers=headers,json=account).status_code==404


def test_reset_revokes_sessions_and_pending_handoff(local_auth):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    first=transaction(client,desktop=True)
    assert submit(client,first).status_code==200
    response=exchange(client,first)
    assert response.status_code==200,response.text
    employee_headers={'Authorization':'Bearer '+response.json()['access_token']}
    pending=transaction(client,desktop=True)
    assert submit(client,pending).status_code==200
    changed=save_account(local_auth,account,password='replacement password 456')
    assert changed.status_code==200,changed.text
    assert client.get('/api/v1/me',headers=employee_headers).status_code==401
    assert exchange(client,pending).status_code==409
    tx=transaction(client)
    assert submit(client,tx).status_code==401
    assert submit(client,tx,password='replacement password 456').status_code==303
    assert save_account(local_auth,account,password='another replacement').status_code==409


def test_reset_between_password_check_and_session_is_rejected(local_auth):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    identity=app.state.auth.accounts.authenticate('staff','employee',PASSWORD)
    assert save_account(local_auth,account,password='replacement password 456').status_code==200
    with pytest.raises(PlatformError,match='变更'):
        app.state.auth.session(account['id'],'desktop',expected_revision=identity['_member_revision'])
    with pytest.raises(PlatformError,match='变更'):
        app.state.auth.register({'id':'staff','mode':'database','revision':1,'verified_revision':1},identity)


def test_disable_and_persistent_failed_attempt_limit(local_auth):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    for _ in range(5):
        tx=transaction(client)
        assert submit(client,tx,password='wrong').status_code==401
    tx=transaction(client)
    assert submit(client,tx).status_code==429
    updated=save_account(local_auth,account,password=PASSWORD).json()
    assert save_account(local_auth,updated,enabled=False).status_code==200
    tx=transaction(client)
    assert submit(client,tx).status_code==401


def test_browser_binding_cancel_and_one_use_desktop_handoff(local_auth):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    tx=transaction(client,desktop=True)
    assert client.post(tx['form_url'],data={'state':'wrong','username':'employee','password':PASSWORD}).status_code==403
    client.post('/api/v1/auth/transactions/'+tx['id']+'/cancel',json={'verifier':tx['verifier']})
    assert submit(client,tx).status_code==409
    tx=transaction(client,desktop=True)
    assert submit(client,tx).status_code==200
    assert exchange(client,tx).status_code==200
    assert exchange(client,tx).status_code==409


def test_admin_account_password_requires_founder(local_auth):
    client,app,settings,headers,source,account=local_auth
    assert client.put('/api/v1/admin/members/'+account['id'],headers=headers,json={'revision':1,'role':'admin'}).status_code==200
    account=client.get('/api/v1/admin/auth-sources/staff/accounts',headers=headers).json()['items'][0]
    token=app.state.auth.session(account['id'],'web')['access_token']
    assert client.put('/api/v1/admin/auth-sources/staff/accounts/'+account['id'],headers={'Authorization':'Bearer '+token},json={**account,'password':'replacement password 456'}).status_code==403
    assert save_account(local_auth,account,password='replacement password 456').status_code==200


def test_login_entry_selection_and_existing_desktop_client(local_auth,tmp_path):
    client,app,settings,headers,source,account=local_auth
    enable(local_auth)
    assert client.put('/api/v1/admin/login-entry',headers=headers,json={'provider_id':'staff','revision':0}).status_code==200
    assert client.put('/api/v1/admin/login-entry',headers=headers,json={'provider_id':'staff','revision':0}).status_code==409
    from web.platform.server.database import Model,ModelGrant
    with app.state.database.transaction() as db:
        model=Model(id='test-model',name='测试模型',provider='openai',protocol='chat_completions',model_name='test',base_url='https://models.example/v1',secret=app.state.vault.seal({'api_key':'test'}),enabled=True,tested_revision=1)
        db.add(model);db.add(ModelGrant(member_id=account['id'],model_id='test-model'))
    connection={'schema_version':1,'config_version':1,'platform_id':settings.platform_id,'platform_name':settings.platform_name,'platform_url':'https://localhost','distribution_channel':'enterprise'}
    def forward(request):
        response=client.request(request.method,request.url.path,content=request.content,headers=dict(request.headers),follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    store=EnterpriseStore(tmp_path/'desktop',protector=TestProtector())
    enterprise=EnterpriseClient(store,transport=httpx.MockTransport(forward))
    enterprise.bind(connection)
    def open_browser(url):
        response=client.get(url,follow_redirects=False)
        form_url=response.headers['location']
        form=client.get(form_url)
        state=re.search("name='state' value='([^']+)'",form.text).group(1)
        assert client.post(form_url,data={'state':state,'username':'employee','password':PASSWORD},follow_redirects=False).status_code==200
    state=enterprise.login(None,threading.Event(),open_browser)
    assert state['member']['id']==account['id']
    assert state['models'][0]['id']=='test-model'
    assert store.profiles()[0]['enterprise']['member_id']==account['id']


def test_delete_unused_source_and_dependency_guards(local_auth):
    client,app,settings,headers,source,account=local_auth
    url='/api/v1/admin/auth-sources/empty'
    payload={**source,'id':'empty'}
    assert client.put(url,headers=headers,json=payload).status_code==200
    assert client.request('DELETE',url,json={'revision':1}).status_code==403
    assert client.request('DELETE',url,headers=headers,json={'revision':99}).status_code==409
    tx=transaction(client,source='empty',test=True,headers=headers)
    response=client.request('DELETE',url,headers=headers,json={'revision':1})
    assert response.json()['error']['code']=='source_login_pending'
    from web.platform.server.database import LoginTransaction
    with app.state.database.transaction() as db:
        db.get(LoginTransaction,tx['id']).expires_at=0
    assert client.request('DELETE',url,headers=headers,json={'revision':1}).status_code==200
    assert client.get(tx['form_url']).status_code==409
    assert client.request('DELETE',url,headers=headers,json={'revision':1}).status_code==404
    assert all(s['id']!='empty' for s in client.get('/api/v1/admin/settings',headers=headers).json()['sources'])
    response=client.request('DELETE','/api/v1/admin/auth-sources/staff',headers=headers,json={'revision':1})
    assert response.json()['error']['code']=='source_has_members'
    assert client.put(url,headers=headers,json={**payload,'enabled':True}).status_code==200
    response=client.request('DELETE',url,headers=headers,json={'revision':1})
    assert response.json()['error']['code']=='source_enabled'
    assert client.put('/api/v1/admin/login-entry',headers=headers,json={'provider_id':'empty','revision':0}).status_code==200
    assert client.put(url,headers=headers,json={**payload,'revision':1}).status_code==200
    response=client.request('DELETE',url,headers=headers,json={'revision':1})
    assert response.json()['error']['code']=='source_in_use'
