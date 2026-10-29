from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta, datetime, timezone
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4
import json

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, OperationFailure
from pymongo.synchronous.collection import Collection

from app.main import COOKIE, create_app
from app.db import initialize, VALIDATORS
from tests.conftest import ORIGIN, HEADERS
from tests.test_p2 import register,login,question
from tests.test_w007 import configure, only_question
from tests.fixtures_f01 import *


def create_open(context,questions=None,repeat=True,anonymous=True):
    app,client,db=context
    register(client,'owner');login(client,'owner')
    survey,body=configure(client,questions or [question()],repeat)
    body['revision']=survey['revision'];body['allow_anonymous']=anonymous
    survey=client.put('/api/v1/surveys/'+survey['id']+'/draft',json=body).json()
    response=client.post('/api/v1/surveys/'+survey['id']+'/publish',json={'revision':survey['revision']})
    assert response.status_code==200,response.text
    return response.json()


def payload(survey,answers=None,key=None,anonymous=False):
    if answers is None:
        q=survey['questions'][0];answers={q['id']:q['options'][0]['id']}
    return {'content_version':1,'submission_key':key or str(uuid4()),'anonymous':anonymous,'answers':answers}


def post(client,survey,body):
    return client.post('/api/v1/fill/'+survey['id']+'/responses',json=body)


def summary(client,survey):
    response=client.get('/api/v1/surveys/'+survey['id']+'/statistics')
    assert response.status_code==200,response.text
    return response.json()


def client_for(app,token):
    client=TestClient(app,base_url=ORIGIN,headers=HEADERS)
    client.cookies.set(COOKIE,token)
    return client


def test_T06_F01_full_statistics_contents_and_r5(context):
    app,client,db=context
    survey=create_open(context,f01_questions())
    empty=summary(client,survey)
    assert (empty['submission_count'],empty['respondent_count'])==(0,0)
    assert [o['respondent_count'] for o in empty['questions'][0]['options']]==[0,0,0]
    assert [o['selection_count'] for o in empty['questions'][1]['options']]==[0,0,0]
    assert empty['questions'][3]['average'] is None and empty['questions'][3]['average_display']=='暂无数据'
    t0=app.state.clock()
    for name in ['f01_u1','f01_u2','f01_u3']:register(client,name)
    cases=[('f01_u1',False,{Q1:A,Q2:[APPLE,BANANA],Q3:'甲',Q4:'0'}),
           ('f01_u1',True,{Q1:B,Q2:[BANANA],Q3:'乙',Q4:'10'}),
           ('f01_u2',True,{Q1:A,Q2:[APPLE],Q3:'丙',Q4:'20'}),
           ('f01_u3',False,{Q1:None,Q2:None,Q3:None,Q4:None})]
    for index,(name,anonymous,answers) in enumerate(cases,1):
        app.state.clock=lambda index=index:t0+timedelta(seconds=index)
        login(client,name)
        response=post(client,survey,payload(survey,answers,anonymous=anonymous));assert response.status_code==201,response.text
    login(client,'owner')
    data=summary(client,survey)
    assert (data['submission_count'],data['respondent_count'])==(4,3)
    single,multiple,text,number=data['questions']
    assert (single['submission_count'],single['respondent_count'])==(3,2)
    assert [(o['submission_count'],o['respondent_count']) for o in single['options']]==[(2,2),(1,1),(0,0)]
    assert multiple['answered_submission_count']==3
    assert [o['selection_count'] for o in multiple['options']]==[2,2,0]
    assert text['answered_submission_count']==number['answered_submission_count']==3
    assert number['average']=='10' and number['average_display']=='10.00'
    for q in data['questions']:
        assert client.get('/api/v1/surveys/'+survey['id']+'/questions/'+q['id']+'/statistics').json()==q
    for qid,expected in [(Q3,['丙','乙','甲']),(Q4,['20','10','0'])]:
        url='/api/v1/surveys/'+survey['id']+'/questions/'+qid+'/contents'
        first=client.get(url+'?limit=2').json();second=client.get(url+'?limit=2&offset=2').json()
        assert first['total']==second['total']==3
        assert [row['value'] for row in first['items']+second['items']]==expected
        assert all(set(row)=={'value','created_at','anonymous'} and row['anonymous'] is True for row in first['items'])
        assert set(second['items'][0])=={'value','created_at','anonymous','username'}
        assert second['items'][0]['username']=='f01_u1'
        assert client.get(url+'?offset=100').json()['items']==[]
    encoded=json.dumps(data)
    for name in ['respondent_id','submission_key','request_digest','f01_u1','f01_u2']:assert name not in encoded
    app.state.clock=lambda:t0+timedelta(seconds=5)
    login(client,'f01_u2');assert post(client,survey,payload(survey,{Q1:C,Q2:None,Q3:None,Q4:None})).status_code==201
    login(client,'owner');after=summary(client,survey)
    assert (after['submission_count'],after['respondent_count'])==(5,3)
    assert after['questions'][0]['submission_count']==4 and after['questions'][0]['respondent_count']==2
    assert [(o['submission_count'],o['respondent_count']) for o in after['questions'][0]['options']]==[(2,2),(1,1),(1,1)]
    assert after['questions'][1:]==data['questions'][1:]
    assert db.survey_responses.count_documents({'answers':[]})==1
    assert db.survey_responses.find_one({'answers.value':Decimal128('0')}) is not None


@pytest.mark.parametrize('values,expected,display',[
    (['1.005'],'1.005','1.01'),(['-1.005'],'-1.005','-1.01'),
    (['1234567890123456789012345678901234'],'1234567890123456789012345678901234','1234567890123456789012345678901234.00'),
    (['1E-6176'],'1E-6176','0.00'),
    (['9.999999999999999999999999999999999E6144']*2,'9.999999999999999999999999999999999E6144','9'*34+'0'*6111+'.00'),
    (['1E6144','-1E6144'],'0','0.00'),
    (['9.999999999999999999999999999999999E6144']*2+['-9.999999999999999999999999999999999E6144']*2,'0','0.00')])
def test_T06_decimal_mean_precision_rounding_and_overflow(context,values,expected,display):
    _,client,_=context
    survey=create_open(context,[only_question('number')])
    qid=survey['questions'][0]['id']
    for value in values:assert post(client,survey,payload(survey,{qid:value})).status_code==201
    number=summary(client,survey)['questions'][0]
    assert Decimal(number['average'])==Decimal(expected)
    assert number['average_display']==display and number['answered_submission_count']==len(values)


def test_T06_contents_equal_timestamps_use_id_tiebreaker(context):
    app,client,db=context
    survey=create_open(context,[only_question('text')]);qid=survey['questions'][0]['id']
    fixed=app.state.clock();app.state.clock=lambda:fixed
    receipts=[]
    for value in ['第一','第二','第三']:
        receipts.append(post(client,survey,payload(survey,{qid:value},anonymous=True)).json()['response_id'])
    docs=list(db.survey_responses.find().sort('_id',-1))
    assert len({doc['created_at'] for doc in docs})==1
    expected=[doc['answers'][0]['value'] for doc in docs]
    url='/api/v1/surveys/'+survey['id']+'/questions/'+qid+'/contents'
    pages=[client.get(url+'?limit=2&offset='+str(offset)).json() for offset in [0,2]]
    assert [item['value'] for page in pages for item in page['items']]==expected==['第三','第二','第一']


@pytest.mark.parametrize('offset_ms,expected',[(-1,201),(0,409),(1,409)])
def test_T08_exact_deadline_submission_boundary(context,offset_ms,expected):
    app,client,db=context
    survey=create_open(context)
    deadline=app.state.clock()+timedelta(hours=1)
    response=client.patch('/api/v1/surveys/'+survey['id']+'/deadline',json={'revision':survey['revision'],'deadline_at':deadline.isoformat()})
    assert response.status_code==200,response.text
    app.state.clock=lambda:deadline+timedelta(milliseconds=offset_ms)
    result=post(client,survey,payload(survey))
    assert result.status_code==expected,result.text
    assert db.survey_responses.count_documents({})==(1 if expected==201 else 0)


def test_T08_deadline_management_utc_milliseconds_frozen_and_recovery(context):
    app,client,db=context
    survey=create_open(context)
    fixed=datetime(2026,10,15,12,0,0,tzinfo=timezone.utc);app.state.clock=lambda:fixed
    url='/api/v1/surveys/'+survey['id']+'/deadline'
    before=db.survey_surveys.find_one({'_id':ObjectId(survey['id'])})
    for value in [fixed.isoformat(),(fixed+timedelta(microseconds=500)).isoformat(),(fixed-timedelta(milliseconds=1)).isoformat(),'2026-10-15T20:00:00',True,'0001-01-01T00:00:00+14:00','9999-12-31T23:59:59-14:00']:
        assert client.patch(url,json={'revision':survey['revision'],'deadline_at':value}).status_code==422
    for value in ['0001-01-01T00:00:00+14:00','9999-12-31T23:59:59-14:00']:
        assert client.post('/api/v1/surveys',json={'title':'边界','deadline_at':value}).status_code==422
    response=client.patch(url,json={'revision':survey['revision'],'deadline_at':'2026-10-15T21:00:00.123987+08:00'})
    assert response.status_code==200,response.text
    updated=response.json();end=datetime(2026,10,15,13,0,0,123000,tzinfo=timezone.utc)
    after=db.survey_surveys.find_one({'_id':ObjectId(survey['id'])})
    assert after['deadline_at']==end and after['revision']==before['revision']+1
    for key in set(before)-{'revision','updated_at','deadline_at'}:assert after[key]==before[key]
    assert client.patch(url,json={'revision':survey['revision'],'deadline_at':None}).status_code==409
    for body in [{'revision':updated['revision']},{'revision':updated['revision'],'deadline_at':None,'title':'改内容'}]:assert client.patch(url,json=body).status_code==422
    app.state.clock=lambda:end
    assert client.get('/api/v1/fill/'+survey['id']).json()['accepting'] is False
    assert post(client,survey,payload(survey)).status_code==409
    extended=client.patch(url,json={'revision':updated['revision'],'deadline_at':(end+timedelta(hours=1)).isoformat()})
    assert extended.status_code==200 and client.get('/api/v1/fill/'+survey['id']).json()['accepting'] is True
    cleared=client.patch(url,json={'revision':extended.json()['revision'],'deadline_at':None})
    assert cleared.status_code==200 and cleared.json()['deadline_at'] is None
    closed=client.post('/api/v1/surveys/'+survey['id']+'/close',json={'revision':cleared.json()['revision']})
    assert closed.status_code==200
    assert client.patch(url,json={'revision':closed.json()['revision'],'deadline_at':(end+timedelta(hours=1)).isoformat()}).status_code==409
    draft=client.post('/api/v1/surveys',json={'title':'草稿'}).json()
    assert client.get('/api/v1/fill/'+draft['id']).status_code==404
    assert client.patch('/api/v1/surveys/'+draft['id']+'/deadline',json={'revision':draft['revision'],'deadline_at':None}).status_code==409


def test_T09_ten_accounts_concurrent_complete_documents(context,monkeypatch):
    app,client,db=context
    survey=create_open(context,repeat=False)
    q=survey['questions'][0];sid=ObjectId(survey['id']);tokens=[];users=[]
    for index in range(10):
        other=TestClient(app,base_url=ORIGIN,headers=HEADERS)
        try:
            users.append(register(other,'concurrent_'+str(index))['id']);login(other,'concurrent_'+str(index));tokens.append(other.cookies.get(COOKIE))
        finally:other.close()
    gate=Barrier(10);original=Collection.insert_one
    def overlapping(self,document,*args,**kwargs):
        if self.name=='survey_responses' and document['survey_id']==sid:gate.wait(timeout=10)
        return original(self,document,*args,**kwargs)
    def send(index):
        other=client_for(app,tokens[index])
        try:
            assert other.get('/api/v1/auth/me').json()['id']==users[index]
            return post(other,survey,payload(survey,{q['id']:q['options'][index%2]['id']}))
        finally:other.close()
    monkeypatch.setattr(Collection,'insert_one',overlapping)
    with ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(send,range(10)))
    assert all(result.status_code==201 for result in results)
    docs=list(db.survey_responses.find({'survey_id':sid}));assert len(docs)==10
    for index,user in enumerate(users):
        doc=next(doc for doc in docs if str(doc['respondent_id'])==user)
        assert doc['path']==[q['id']] and doc['answers']==[{'question_id':q['id'],'value':q['options'][index%2]['id']}]
    login(client,'owner');data=summary(client,survey)
    assert (data['submission_count'],data['respondent_count'])==(10,10)


@pytest.mark.parametrize('repeat,mode',[(False,'different'),(False,'same'),(True,'different'),(True,'same'),(False,'changed'),(True,'changed')])
def test_T09_overlapping_keys_unique_indexes_and_conflicts(context,monkeypatch,repeat,mode):
    app,client,db=context
    survey=create_open(context,repeat=repeat);q=survey['questions'][0];sid=ObjectId(survey['id'])
    token=client.cookies.get(COOKIE);first=payload(survey);second=deepcopy(first)
    if mode=='different':second['submission_key']=str(uuid4())
    if mode=='changed':second['answers'][q['id']]=q['options'][1]['id']
    gate=Barrier(2);original=Collection.insert_one
    def overlap(self,document,*args,**kwargs):
        if self.name=='survey_responses' and document['survey_id']==sid:gate.wait(timeout=10)
        return original(self,document,*args,**kwargs)
    monkeypatch.setattr(Collection,'insert_one',overlap)
    def send(body):
        other=client_for(app,token)
        try:return post(other,survey,body)
        finally:other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,[first,second]))
    statuses=sorted(result.status_code for result in results)
    expected=[201,201] if repeat and mode=='different' else [200,201] if mode=='same' else [201,409]
    assert statuses==expected,[result.text for result in results]
    docs=list(db.survey_responses.find({'survey_id':sid}));assert len(docs)==(2 if expected==[201,201] else 1)
    assert all(doc['path']==[q['id']] and len(doc['answers'])==1 for doc in docs)
    if mode=='same':assert results[0].json()==results[1].json()
    if mode=='changed':assert next(r for r in results if r.status_code==409).json()['error']['code']=='KEY_REUSED_WITH_DIFFERENT_CONTENT'


def test_T09_write_succeeded_but_ack_lost_recovers_after_close(context,monkeypatch):
    _,client,db=context
    survey=create_open(context);body=payload(survey);sid=ObjectId(survey['id']);original=Collection.insert_one
    def uncertain(self,document,*args,**kwargs):
        result=original(self,document,*args,**kwargs)
        if self.name=='survey_responses' and document['survey_id']==sid:raise AutoReconnect('private test failure')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(Collection,'insert_one',uncertain)
        result=post(client,survey,body)
        assert result.status_code==503 and 'private test failure' not in result.text
    saved=db.survey_responses.find_one({'survey_id':sid});assert db.survey_responses.count_documents({'survey_id':sid})==1
    client.post('/api/v1/surveys/'+survey['id']+'/close',json={'revision':survey['revision']})
    retry=post(client,survey,body)
    assert retry.status_code==200 and retry.json()['response_id']==str(saved['_id'])
    assert db.survey_responses.count_documents({'survey_id':sid})==1


@pytest.mark.parametrize('action',['close','deadline'])
def test_T10_accepted_inflight_then_closed_or_expired(context,monkeypatch,action):
    app,client,db=context
    survey=create_open(context);sid=ObjectId(survey['id']);body=payload(survey);token=client.cookies.get(COOKIE)
    boundary=app.state.clock()+timedelta(hours=1)
    if action=='deadline':
        survey=client.patch('/api/v1/surveys/'+survey['id']+'/deadline',json={'revision':survey['revision'],'deadline_at':boundary.isoformat()}).json()
        app.state.clock=lambda:boundary-timedelta(milliseconds=1)
    accepted,release=Event(),Event();observed={};original=Collection.insert_one
    def pause(self,document,*args,**kwargs):
        if self.name=='survey_responses' and document['survey_id']==sid:
            observed['accepted_at']=document['accepted_at'];accepted.set()
            assert release.wait(10),'release not signaled'
        return original(self,document,*args,**kwargs)
    monkeypatch.setattr(Collection,'insert_one',pause)
    def request():
        other=client_for(app,token)
        try:return post(other,survey,body)
        finally:other.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(request)
        try:
            assert accepted.wait(10),'submission did not reach accepted insert'
            if action=='close':assert client.post('/api/v1/surveys/'+survey['id']+'/close',json={'revision':survey['revision']}).status_code==200
            else:app.state.clock=lambda:boundary
            assert post(client,survey,payload(survey)).status_code==409
        finally:release.set()
        result=future.result(timeout=10)
    assert result.status_code==201,result.text
    assert db.survey_responses.count_documents({'survey_id':sid})==1
    saved=db.survey_responses.find_one({'survey_id':sid});assert saved['accepted_at']==observed['accepted_at']
    assert post(client,survey,body).status_code==200
    changed=deepcopy(body);q=survey['questions'][0];changed['answers'][q['id']]=q['options'][1]['id']
    assert post(client,survey,changed).json()['error']['code']=='KEY_REUSED_WITH_DIFFERENT_CONTENT'


def test_T11_content_deadline_permissions_pagination_and_whitelists(context):
    _,client,db=context
    survey=create_open(context,f01_questions());sid=survey['id'];base='/api/v1/surveys/'+sid
    for qid in [Q3,Q4]:
        for suffix in ['?offset=-1','?limit=0','?limit=101']:assert client.get(base+'/questions/'+qid+'/contents'+suffix).status_code==422
    assert client.get(base+'/questions/'+Q1+'/contents').status_code==422
    assert client.get(base+'/questions/'+str(uuid4())+'/contents').status_code==404
    assert client.get(base+'/questions/'+Q3+'/contents').json()['items']==[]
    register(client,'other');login(client,'other')
    for suffix in ['/statistics','/questions/'+Q3+'/contents','/questions/'+Q4+'/contents','/questions/'+Q1+'/statistics']:
        assert client.get(base+suffix).status_code==404
    assert client.patch(base+'/deadline',json={'revision':survey['revision'],'deadline_at':None}).status_code==404
    client.post('/api/v1/auth/logout',json={})
    assert client.get(base+'/questions/'+Q3+'/contents').status_code==401
    assert client.patch(base+'/deadline',json={'revision':survey['revision'],'deadline_at':None}).status_code==401


def test_T12_request_source_and_html_preserved_as_data(context):
    _,client,db=context
    q=only_question('text');q['title']='<script>window.bad=1</script>'
    survey=create_open(context,[q]);base='/api/v1/surveys/'+survey['id'];html='<img src=x onerror="window.bad=1">'
    assert post(client,survey,payload(survey,{q['id']:html},anonymous=True)).status_code==201
    result=client.get(base+'/questions/'+q['id']+'/contents').json()
    assert result['items'][0]['value']==html and set(result['items'][0])=={'value','created_at','anonymous'}
    for headers in [{},{'Origin':'https://outside.example','X-Survey-Request':'1'},{'Origin':ORIGIN}]:
        other=TestClient(client.app,base_url=ORIGIN)
        try:assert other.patch(base+'/deadline',json={'revision':survey['revision'],'deadline_at':None},headers=headers).status_code==403
        finally:other.close()


def test_T13_resources_and_startup_contract_failures(context,settings):
    app,client,db=context
    survey=create_open(context,[only_question('number')]);qid=survey['questions'][0]['id']
    for value in ['NaN','Infinity','1E6145','1E-6177','12345678901234567890123456789012345']:
        assert post(client,survey,payload(survey,{qid:value})).status_code==422
    assert db.survey_responses.count_documents({})==0
    assert client.post('/api/v1/surveys',content='x'*(1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413
    draft=client.post('/api/v1/surveys',json={'title':'资源'}).json()
    base={'revision':draft['revision'],'title':'资源','description':'','deadline_at':None,'allow_anonymous':False,'allow_repeat':False}
    for questions in [[question() for _ in range(101)], [{**question(),'title':'字'*1001}]]:
        assert client.put('/api/v1/surveys/'+draft['id']+'/draft',json={**base,'questions':questions}).status_code==422
    db.survey_sessions.drop_index('session_expiry');db.survey_sessions.create_index('expires_at',expireAfterSeconds=60,name='session_expiry')
    try:
        assert client.get('/health').status_code==503
        with pytest.raises(RuntimeError, match='database index options do not match contract'):
            with TestClient(create_app(settings)):pass
    finally:
        db.survey_sessions.drop_index('session_expiry')
        db.survey_sessions.create_index('expires_at',expireAfterSeconds=0,name='session_expiry')
        initialize(db)
    db.command('collMod','survey_responses',validator={})
    try:
        assert client.get('/health').status_code==503
        with pytest.raises(RuntimeError):
            with TestClient(create_app(settings)):pass
    finally:db.command('collMod','survey_responses',validator=VALIDATORS['responses'])
    invalid={'schema_version':2,'username':'unknown_version','password_hash':'unused','created_at':app.state.clock()}
    db.survey_users.insert_one(invalid,bypass_document_validation=True)
    try:
        with pytest.raises(RuntimeError):
            with TestClient(create_app(settings)):pass
    finally:db.survey_users.delete_many({'schema_version':2})
