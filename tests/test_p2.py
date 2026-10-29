from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure, AutoReconnect
from app.config import guard_test_database
from app.db import inspect_contract
from app.main import COOKIE, hash_token
from tests.conftest import ORIGIN, HEADERS

PASSWORD = 'test-only-pass-2026'


def register(client, name):
    response = client.post('/api/v1/auth/register', json={'username': name, 'password': PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def login(client, name):
    response = client.post('/api/v1/auth/login', json={'username': name, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    return response


def question(required=False):
    return {'id': str(uuid4()), 'kind': 'single', 'title': '你选择什么？', 'required': required,
            'validation': {}, 'rules': [], 'options': [{'id': str(uuid4()), 'text': text} for text in ['A', 'B', 'C']]}


def create(client, required=False, repeat=False, anonymous=True):
    response = client.post('/api/v1/surveys', json={'title': '统计样本', 'allow_repeat': repeat, 'allow_anonymous': anonymous})
    assert response.status_code == 201, response.text
    survey = response.json()
    body = {key: survey[key] for key in ['revision', 'title', 'description', 'deadline_at', 'allow_repeat', 'allow_anonymous']}
    body['questions'] = [question(required)]
    response = client.put('/api/v1/surveys/' + survey['id'] + '/draft', json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def publish(client, survey):
    response = client.post('/api/v1/surveys/' + survey['id'] + '/publish', json={'revision': survey['revision']})
    assert response.status_code == 200, response.text
    return response.json()


def submission(survey, value, key=None, anonymous=False):
    return {'content_version': 1, 'submission_key': key or str(uuid4()), 'anonymous': anonymous,
            'answers': {survey['questions'][0]['id']: value}}


def setup_owner(context, **kwargs):
    app, client, db = context
    register(client, 'owner'); login(client, 'owner')
    survey, body = create(client, **kwargs)
    return app, client, db, publish(client, survey), body


def test_T01_ownership_and_app_restart(context, settings):
    app, client, db, survey, _ = setup_owner(context)
    owner = client.get('/api/v1/auth/me').json()['id']
    assert db.survey_surveys.find_one({'_id': ObjectId(survey['id'])})['owner_id'] == ObjectId(owner)
    register(client, 'visitor'); login(client, 'visitor')
    assert client.get('/api/v1/surveys').json() == {'items': [], 'total': 0}
    for suffix in ['', '/statistics', '/questions/' + survey['questions'][0]['id'] + '/statistics']:
        assert client.get('/api/v1/surveys/' + survey['id'] + suffix).status_code == 404
    assert client.get('/api/v1/fill/' + survey['id']).status_code == 200
    from app.main import create_app
    with TestClient(create_app(settings), base_url=ORIGIN, headers=HEADERS) as restarted:
        login(restarted, 'owner')
        assert restarted.get('/api/v1/surveys/' + survey['id']).json()['questions'] == survey['questions']
        assert restarted.get('/health').json()['status'] == 'ready'


def test_T02_draft_validation_revision_and_frozen_content(context):
    _, client, db = context
    register(client, 'owner'); login(client, 'owner')
    survey, body = create(client)
    sid = survey['id']
    assert client.put('/api/v1/surveys/' + sid + '/draft', json=body).status_code == 409
    body['revision'] = survey['revision']; body['questions'][0]['title'] = ''
    saved = client.put('/api/v1/surveys/' + sid + '/draft', json=body)
    assert saved.status_code == 200 and saved.json()['publish_issues']
    assert client.post('/api/v1/surveys/' + sid + '/publish', json={'revision': saved.json()['revision']}).status_code == 422
    body['revision'] = saved.json()['revision']; body['questions'][0]['title'] = '修改文字'
    body['questions'][0]['options'].reverse()
    changed = client.put('/api/v1/surveys/' + sid + '/draft', json=body).json()
    assert changed['questions'] == body['questions']
    assert {o['id'] for o in changed['questions'][0]['options']} == {o['id'] for o in survey['questions'][0]['options']}
    publish(client, changed)
    body['revision'] = changed['revision'] + 1
    assert client.put('/api/v1/surveys/' + sid + '/draft', json=body).status_code == 409
    body['questions'][0]['kind'] = 'unsupported'
    assert client.put('/api/v1/surveys/' + sid + '/draft', json=body).status_code == 422


def test_T05_explicit_confirmation_validation_and_atomic_document(context):
    _, client, db, survey, _ = setup_owner(context, required=True)
    q = survey['questions'][0]; endpoint = '/api/v1/fill/' + survey['id']
    for answers in [{}, {q['id']: None}, {q['id']: 'unknown-option'}]:
        response = client.post(endpoint + '/responses', json={'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': False, 'answers': answers})
        assert response.status_code == 422
    assert db.survey_responses.count_documents({}) == 0
    body = submission(survey, q['options'][0]['id'])
    for invalid_version in [True, 1.0, '1', 2]:
        assert client.post(endpoint + '/responses', json={**body, 'content_version': invalid_version}).status_code == 422
    evaluated = client.post(endpoint + '/evaluate', json={'content_version': 1, 'answers': body['answers']})
    assert evaluated.json()['ready_to_submit'] is True
    assert db.survey_responses.count_documents({}) == 0
    result = client.post(endpoint + '/responses', json=body)
    assert result.status_code == 201
    assert set(result.json()) == {'response_id', 'survey_id', 'accepted_at'}
    stored = db.survey_responses.find_one({})
    assert stored['path'] == [q['id']]
    assert stored['answers'] == [{'question_id': q['id'], 'value': q['options'][0]['id']}]
    assert stored['repeat_allowed'] is False and stored['content_version'] == 1
    assert client.post(endpoint + '/responses', json={**body, 'path': []}).status_code == 422
    assert client.post(endpoint + '/responses', json={**body, 'answers': {str(uuid4()): None}}).status_code == 422


def test_T05_database_failure_and_retry_receipt(context, monkeypatch):
    _, client, db, survey, _ = setup_owner(context)
    endpoint = '/api/v1/fill/' + survey['id'] + '/responses'
    body = submission(survey, survey['questions'][0]['options'][0]['id'])
    from pymongo.synchronous.collection import Collection
    original = Collection.insert_one
    def broken(self, document, *args, **kwargs):
        if self.name == 'survey_responses':
            raise AutoReconnect('test-only unavailable')
        return original(self, document, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Collection, 'insert_one', broken)
        response = client.post(endpoint, json=body)
        assert response.status_code == 503
        assert 'test-only unavailable' not in response.text
    assert db.survey_responses.count_documents({}) == 0
    first = client.post(endpoint, json=body)
    retry = client.post(endpoint, json=body)
    assert first.status_code == 201 and retry.status_code == 200 and first.json() == retry.json()
    assert db.survey_responses.count_documents({}) == 1
    different = deepcopy(body); different['answers'][survey['questions'][0]['id']] = survey['questions'][0]['options'][1]['id']
    assert client.post(endpoint, json=different).status_code == 409
    assert client.post(endpoint, json=submission(survey, None)).status_code == 409
    client.post('/api/v1/surveys/' + survey['id'] + '/close', json={'revision': survey['revision']})
    assert client.post(endpoint, json=body).json() == first.json()
    assert client.post(endpoint, json=submission(survey, None)).status_code == 409


def test_T06_F01_single_counts_zero_anonymous_refresh(context):
    _, client, db, survey, _ = setup_owner(context, repeat=True)
    q = survey['questions'][0]; sid = survey['id']; a,b,c = [o['id'] for o in q['options']]
    endpoint = '/api/v1/fill/' + sid + '/responses'
    empty = client.get('/api/v1/surveys/' + sid + '/statistics').json()
    assert (empty['submission_count'], empty['respondent_count']) == (0,0)
    assert [o['respondent_count'] for o in empty['questions'][0]['options']] == [0,0,0]
    for name in ['user1','user2','user3']:
        register(client, name)
    for name, value, anonymous in [('user1',a,False),('user1',b,True),('user2',a,True),('user3',None,False)]:
        login(client,name)
        assert client.post(endpoint,json=submission(survey,value,anonymous=anonymous)).status_code == 201
    login(client,'owner')
    summary = client.get('/api/v1/surveys/' + sid + '/statistics').json()
    assert (summary['submission_count'], summary['respondent_count']) == (4,3)
    question_stats = summary['questions'][0]
    assert (question_stats['submission_count'],question_stats['respondent_count']) == (3,2)
    assert [(o['submission_count'],o['respondent_count']) for o in question_stats['options']] == [(2,2),(1,1),(0,0)]
    assert client.get('/api/v1/surveys/' + sid + '/questions/' + q['id'] + '/statistics').json() == question_stats
    encoded=json.dumps(summary)
    for secret in ['respondent_id','submission_key','request_digest','user1','user2']:
        assert secret not in encoded
    login(client,'user2'); assert client.post(endpoint,json=submission(survey,c)).status_code == 201
    login(client,'owner'); after=client.get('/api/v1/surveys/' + sid + '/statistics').json()
    assert (after['submission_count'],after['respondent_count']) == (5,3)
    assert [(o['submission_count'],o['respondent_count']) for o in after['questions'][0]['options']] == [(2,2),(1,1),(1,1)]
    empty_doc=db.survey_responses.find_one({'answers': []})
    assert empty_doc['path'] == [q['id']]


def test_T07_account_hash_session_expiry_logout_rotation(context):
    app, client, db = context
    register(client,' Owner ')
    row=db.survey_users.find_one({})
    assert row['username']=='owner' and row['password_hash'].startswith('$argon2id$') and PASSWORD not in row['password_hash']
    unknown=client.post('/api/v1/auth/login',json={'username':'missing','password':PASSWORD})
    wrong=client.post('/api/v1/auth/login',json={'username':'owner','password':'incorrect-pass'})
    assert unknown.status_code==wrong.status_code==401 and unknown.json()==wrong.json()
    for password in ['', 'short']:
        short=client.post('/api/v1/auth/login',json={'username':'owner','password':password})
        assert short.status_code==401 and short.json()==wrong.json()
    response=login(client,'OWNER'); old=client.cookies.get(COOKIE)
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    login(client,'owner'); new=client.cookies.get(COOKIE)
    assert new!=old and db.survey_sessions.find_one({'token_hash':hash_token(old)}) is None
    assert db.survey_sessions.find_one({})['token_hash']==hashlib.sha256(new.encode()).hexdigest()
    app.state.clock=lambda: row['created_at']+timedelta(days=2)
    assert db.survey_sessions.count_documents({})==1
    assert client.get('/api/v1/auth/me').status_code==401
    assert client.post('/api/v1/auth/logout',json={}).status_code==204
    assert client.post('/api/v1/auth/logout',json={}).status_code==204
    assert db.survey_sessions.count_documents({})==0


def test_T07_same_username_concurrent_registration(context):
    app, client, db = context
    def attempt(_):
        other = TestClient(app, base_url=ORIGIN, headers=HEADERS)
        try:
            return other.post('/api/v1/auth/register',json={'username':'Same_Name','password':PASSWORD}).status_code
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==[201,409]
    assert db.survey_users.count_documents({'username':'same_name'})==1


def test_T11_anonymous_and_write_ownership(context):
    _, client, db, survey, body=setup_owner(context,anonymous=False)
    register(client,'other');login(client,'other')
    for suffix, method, payload in [('/draft','put',body),('/publish','post',{'revision':survey['revision']}),('/close','post',{'revision':survey['revision']})]:
        assert getattr(client,method)('/api/v1/surveys/'+survey['id']+suffix,json=payload).status_code==404
    response=client.post('/api/v1/fill/'+survey['id']+'/responses',json=submission(survey,None,anonymous=True))
    assert response.status_code==422 and db.survey_responses.count_documents({})==0
    filled=client.get('/api/v1/fill/'+survey['id']).json()
    assert not {'owner_id','respondent_id','responses','password_hash'}.intersection(filled)
    client.post('/api/v1/auth/logout',json={})
    assert client.get('/api/v1/fill/'+survey['id']).status_code==401


def test_T12_basic_origin_and_input_safety(context):
    _, client, db=context
    for headers in [{}, {'Origin':'https://outside.example','X-Survey-Request':'1'}, {'Origin':ORIGIN}]:
        other = TestClient(client.app,base_url=ORIGIN)
        try:
            assert other.post('/api/v1/auth/register',json={'username':'safe','password':PASSWORD},headers=headers).status_code==403
        finally:
            other.close()
    register(client,'owner');login(client,'owner')
    assert client.post('/api/v1/surveys',json={'title':'x','owner_id':str(ObjectId())}).status_code==422
    result=client.post('/api/v1/auth/login',json={'username':'owner','password':'secret','extra':'private'})
    assert result.status_code==422 and 'secret' not in result.text and 'private' not in result.text
    response=client.post('/api/v1/surveys',content='x'*(1024*1024+1),headers={'Content-Type':'application/json'})
    assert response.status_code==413
    assert client.post('/api/v1/surveys',content='{',headers={'Content-Type':'application/json'}).status_code==400
    assert 'error' in client.get('/api/v1/nonexistent').json()
    assert client.post('/api/v1/surveys',content='{}',headers={'Content-Type':'text/plain'}).status_code==400


def test_T13_real_validators_indexes_and_unknown_version(context):
    app, client, db, survey, _=setup_owner(context)
    contract=inspect_contract(db)
    assert set(contract)=={'survey_users','survey_sessions','survey_surveys','survey_responses',
                           'survey_questions','survey_question_versions','survey_question_banks'}
    body=submission(survey,None)
    assert client.post('/api/v1/fill/'+survey['id']+'/responses',json=body).status_code==201
    doc=db.survey_responses.find_one({}); doc.pop('_id')
    for key, value in [('repeat_allowed',None), ('repeat_allowed','false'), ('survey_id','bad'), ('schema_version',2)]:
        invalid=deepcopy(doc); invalid['submission_key']=str(uuid4())
        if value is None: invalid.pop(key)
        else: invalid[key]=value
        with pytest.raises(OperationFailure) as exc: db.survey_responses.insert_one(invalid)
        assert exc.value.code==121
    db.survey_sessions.drop_index('session_expiry')
    try:
        assert client.get('/health').status_code==503
    finally:
        db.survey_sessions.create_index('expires_at',expireAfterSeconds=0,name='session_expiry')


def test_T13_cleanup_guard_rejects_business_cloud_and_wrong_run():
    uri='mongodb://127.0.0.1:27017/'
    for endpoint, name, run in [(uri,'ecnu00000000000','abcdefgh'),('mongodb://remote.example:27017/','survey_test_abcdefgh','abcdefgh'),(uri,'survey_test_ijklmnop','abcdefgh'),('mongodb://localhost:27017/admin','survey_test_abcdefgh','abcdefgh')]:
        with pytest.raises(ValueError): guard_test_database(endpoint,name,run)
    guard_test_database(uri,'survey_test_abcdefgh','abcdefgh')


def test_T05_two_questions_explicit_optional_skip(context):
    _, client, db = context
    register(client, 'owner'); login(client, 'owner')
    survey, body = create(client, required=True)
    body['revision'] = survey['revision']
    second = question(required=False)
    body['questions'].append(second)
    saved = client.put('/api/v1/surveys/' + survey['id'] + '/draft', json=body)
    assert saved.status_code == 200
    survey = publish(client, saved.json())
    first = survey['questions'][0]
    payload = submission(survey, first['options'][0]['id'])
    endpoint = '/api/v1/fill/' + survey['id']
    evaluated = client.post(endpoint + '/evaluate', json={'content_version': 1, 'answers': payload['answers']}).json()
    assert evaluated['current_question_id'] == second['id'] and evaluated['ready_to_submit'] is False
    assert client.post(endpoint + '/responses', json=payload).status_code == 422
    assert db.survey_responses.count_documents({}) == 0
    payload['answers'][second['id']] = None
    assert client.post(endpoint + '/responses', json=payload).status_code == 201
    document = db.survey_responses.find_one({})
    assert document['path'] == [first['id'], second['id']]
    assert document['answers'] == [{'question_id': first['id'], 'value': first['options'][0]['id']}]
