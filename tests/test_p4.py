"""P4 probes derived afresh from the original requirements and contracts 11–13."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal, localcontext
from itertools import permutations
from threading import Barrier
from uuid import uuid4

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from pymongo.synchronous.collection import Collection

from app.main import COOKIE
from tests.conftest import HEADERS, ORIGIN
from tests.test_p2 import register, login
from tests.test_w007 import configure, only_question
from tests.test_w008 import create_open, payload, post, summary, client_for
from tests.fixtures_f02 import f02_questions


def test_T01_P4_list_pages_ownership_and_readonly(context):
    _, client, db = context
    register(client, 'page_owner'); login(client, 'page_owner')
    ids = []
    for i in range(23):
        result = client.post('/api/v1/surveys', json={'title': '分页'+str(i), 'description': '原文说明', 'allow_anonymous': True})
        assert result.status_code == 201
        ids.append(result.json()['id'])
    register(client, 'other_owner'); login(client, 'other_owner')
    assert client.post('/api/v1/surveys', json={'title': '不能混入'}).status_code == 201
    login(client, 'page_owner')
    before = list(db.survey_surveys.find().sort('_id', 1))
    pages = [client.get('/api/v1/surveys?offset='+str(n)).json() for n in [0, 20, 40]]
    assert [len(p['items']) for p in pages] == [20, 3, 0]
    assert all(p['total'] == 23 for p in pages)
    assert [r['id'] for p in pages for r in p['items']] == list(reversed(ids))
    assert all('questions' not in r and 'owner_id' not in r for p in pages for r in p['items'])
    assert before == list(db.survey_surveys.find().sort('_id', 1))


def test_T02_P4_concurrent_revision_keeps_whole_winner(context, monkeypatch):
    app, client, db = context
    register(client, 'owner'); login(client, 'owner')
    survey, body = configure(client, f02_questions())
    body['revision'] = survey['revision']
    bodies = [deepcopy(body), deepcopy(body)]
    bodies[0]['title'] = '标签页甲'; bodies[1]['title'] = '标签页乙'
    bodies[1]['questions'].reverse()
    bodies[1]['questions'][-1]['options'].reverse()
    bodies[1]['questions'][-1]['rules'].reverse()
    gate = Barrier(2)
    original = Collection.find_one_and_update
    def overlap(self, *args, **kwargs):
        if self.name == 'survey_surveys':
            gate.wait(timeout=10)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Collection, 'find_one_and_update', overlap)
    token = client.cookies.get(COOKIE)
    def save(body):
        other = client_for(app, token)
        try:
            return other.put('/api/v1/surveys/'+survey['id']+'/draft', json=body)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, bodies))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(i for i, r in enumerate(results) if r.status_code == 200)
    read = client.get('/api/v1/surveys/'+survey['id']).json()
    assert read == results[winner].json()
    assert read['questions'] == bodies[winner]['questions']
    assert read['title'] == bodies[winner]['title'] and read['revision'] == survey['revision']+1
    stored = db.survey_surveys.find_one({'_id': ObjectId(survey['id'])})
    assert stored['title'] == read['title'] and stored['revision'] == read['revision']
    assert [q['id'] for q in stored['questions']] == [q['id'] for q in read['questions']]


def test_T03_P4_renamed_F02_ids_and_titles_keep_paths(context):
    _, client, db = context
    questions = f02_questions()
    old_ids = [q['id'] for q in questions]
    rename = {value: str(uuid4()) for value in old_ids + [o['id'] for q in questions for o in q['options']]}
    for index, q in enumerate(questions):
        q['id'] = rename[q['id']]; q['title'] = '新题意标识 '+str(index)
        for option in q['options']:
            option['id'] = rename[option['id']]; option['text'] = '改名 '+option['text']
        for rule in q['rules']:
            rule['target'] = rename.get(rule['target'], rule['target'])
            if q['kind'] == 'single': rule['operand'] = rename[rule['operand']]
            if q['kind'] == 'multiple': rule['operand'] = [rename[v] for v in rule['operand']]
    survey = create_open(context, questions)
    j1,j2,j3,j4,j5 = [q['id'] for q in questions]
    a,b,_ = [o['id'] for o in questions[0]['options']]
    x,y,_ = [o['id'] for o in questions[1]['options']]
    for answers, path in [({j1:a,j3:'18',j5:None}, [j1,j3,j5]),
                          ({j1:b,j2:[y,x],j4:' 完成 ',j5:None}, [j1,j2,j4,j5])]:
        before = db.survey_responses.count_documents({})
        result = client.post('/api/v1/fill/'+survey['id']+'/evaluate', json={'content_version':1,'answers':answers})
        assert result.status_code == 200 and result.json()['path'] == path
        assert result.json()['ready_to_submit'] and db.survey_responses.count_documents({}) == before
        receipt = post(client, survey, payload(survey, answers))
        assert receipt.status_code == 201
        saved = db.survey_responses.find_one({'_id':ObjectId(receipt.json()['response_id'])})
        assert saved['path'] == path
        expected = ([{'question_id':j1,'value':a},{'question_id':j3,'value':Decimal128('18')}]
                    if path == [j1,j3,j5] else [{'question_id':j1,'value':b},{'question_id':j2,'value':[x,y]},{'question_id':j4,'value':'完成'}])
        assert saved['answers'] == expected


@pytest.mark.parametrize('values', list(permutations(['1E34','1','-1E34'])))
def test_T06_P4_finite_cancellation_independent_arithmetic(context, values):
    _, client, db = context
    survey = create_open(context, [only_question('number')]); qid = survey['questions'][0]['id']
    for value in values:
        assert post(client, survey, payload(survey, {qid:value})).status_code == 201
    stored = [d['answers'][0]['value'].to_decimal() for d in db.survey_responses.find()]
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        assert sum(stored) == Decimal(1)
        expected = sum(stored)/len(stored)
    actual = summary(client, survey)['questions'][0]
    assert actual['average_display'] == '0.33', actual
    assert abs(Decimal(actual['average'])-expected) < Decimal('1E-33')
    assert actual['answered_submission_count'] == 3


@pytest.mark.parametrize('values,expected', [
    (['1.005','1.004999999999999999999999999999999'],'1.00'),
    (['1.004999999999999999999999999999999','1.005'],'1.00'),
    (['-1.005','-1.004999999999999999999999999999999'],'-1.00'),
    (['-1.004999999999999999999999999999999','-1.005'],'-1.00')])
def test_T06_P4_display_rounds_original_values_once(context, values, expected):
    _, client, _ = context
    survey = create_open(context, [only_question('number')]); qid = survey['questions'][0]['id']
    for value in values:
        assert post(client, survey, payload(survey, {qid:value})).status_code == 201
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        from decimal import ROUND_HALF_UP
        exact = sum(Decimal(v) for v in values)/2
        assert format(exact.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'.2f') == expected
    assert summary(client, survey)['questions'][0]['average_display'] == expected


@pytest.mark.parametrize('operation', ['draft','publish','deadline','close','list','text_contents','number_contents'])
def test_T13_P4_oversized_database_integers_are_422(context, operation):
    app, client, db = context
    survey = create_open(context, [only_question('text')]); sid = survey['id']
    number = only_question('number'); number['id'] = str(uuid4())
    second, draft_body = configure(client, [number])
    before = list(db.survey_surveys.find().sort('_id',1))
    other = TestClient(app, base_url=ORIGIN, headers=HEADERS, raise_server_exceptions=False)
    other.cookies.set(COOKIE, client.cookies.get(COOKIE))
    huge = 2**100
    try:
        if operation == 'draft': result = other.put('/api/v1/surveys/'+second['id']+'/draft',json={**draft_body,'revision':huge})
        elif operation == 'publish': result = other.post('/api/v1/surveys/'+second['id']+'/publish',json={'revision':huge})
        elif operation == 'deadline': result = other.patch('/api/v1/surveys/'+sid+'/deadline',json={'revision':huge,'deadline_at':None})
        elif operation == 'close': result = other.post('/api/v1/surveys/'+sid+'/close',json={'revision':huge})
        elif operation == 'list': result = other.get('/api/v1/surveys?offset='+str(huge))
        else:
            s,q = (survey,survey['questions'][0]) if operation == 'text_contents' else (second,number)
            result = other.get('/api/v1/surveys/'+s['id']+'/questions/'+q['id']+'/contents?offset='+str(huge))
        assert result.status_code == 422, (operation,result.status_code,result.text)
        assert set(result.json()) == {'error'}
    finally:
        other.close()
    assert before == list(db.survey_surveys.find().sort('_id',1))
    assert db.survey_responses.count_documents({}) == 0
