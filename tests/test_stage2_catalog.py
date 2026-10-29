from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4
import os
import json
from pathlib import Path
import hashlib
from bson import ObjectId, BSON, Decimal128
from fastapi.testclient import TestClient
import pytest
from pymongo.errors import AutoReconnect, OperationFailure
from app.config import LIMITS
from app import question_catalog as catalog
from tests.conftest import HEADERS, ORIGIN
from tests import fixtures_stage2 as f


def call(c, method, path, body=None, status=200):
    r = c.request(method, '/api/v1' + path, **({'json': body} if body is not None else {}))
    assert r.status_code == status, r.text
    return r.json()


def account(app, name):
    c = TestClient(app, base_url=ORIGIN, headers=HEADERS)
    call(c, 'POST', '/auth/register', {'username': name, 'password': 'stage2-test-pass'}, 201)
    call(c, 'POST', '/auth/login', {'username': name, 'password': 'stage2-test-pass'})
    return c


def setup(context, value=None):
    app, c, db = context
    owner = account(app, 's2_owner')
    q = call(owner, 'POST', '/questions', {'label': '常用年龄', 'creation_key': str(uuid4())}, 201)
    v = call(owner, 'POST', '/questions/' + q['id'] + '/versions',
             {'creation_key': str(uuid4()), 'payload': value or f.AGE}, 201)
    v = call(owner, 'POST', f"/questions/{q['id']}/versions/{v['id']}/seal", {'revision': v['revision']})
    return app, owner, db, q, v


def new_survey(c, title='复用卷'):
    return call(c, 'POST', '/surveys', {'title': title, 'allow_anonymous': True, 'allow_repeat': True}, 201)


def imported(c, s, q, v, iid):
    body = {'revision': s['revision'], 'instance_id': iid,
            'question_id': q['id'], 'question_version_id': v['id']}
    return call(c, 'POST', '/surveys/' + s['id'] + '/question-imports', body)


def draft_body(s):
    return {k: deepcopy(s[k]) for k in ['revision', 'title', 'description', 'allow_anonymous',
                                       'allow_repeat', 'deadline_at', 'questions']}


def test_T2_01_lifecycle_and_idempotency(context):
    app, c, db = context
    owner = account(app, 's2_owner')
    body = {'label': '年龄', 'creation_key': str(uuid4())}
    q = call(owner, 'POST', '/questions', body, 201)
    assert call(owner, 'POST', '/questions', body) == q
    call(owner, 'POST', '/questions', {**body, 'label': '其他'}, 409)
    prefix = '/questions/' + q['id'] + '/versions'
    create = {'creation_key': str(uuid4()), 'payload': {**f.AGE, 'title': ''}}
    v = call(owner, 'POST', prefix, create, 201)
    call(owner, 'POST', prefix + '/' + v['id'] + '/seal', {'revision': 1}, 422)
    call(owner, 'POST', '/surveys/' + new_survey(owner)['id'] + '/question-imports',
         {'revision': 1, 'instance_id': f.A1, 'question_id': q['id'], 'question_version_id': v['id']}, 409)
    saved = call(owner, 'PUT', prefix + '/' + v['id'] + '/draft', {'revision': 1, 'payload': f.AGE})
    assert call(owner, 'POST', prefix, create)['payload'] == saved['payload']
    call(owner, 'POST', prefix, {**create, 'payload': f.AGE}, 409)
    v = call(owner, 'POST', prefix + '/' + v['id'] + '/seal', {'revision': 2})
    frozen = BSON.encode(db.survey_question_versions.find_one({'_id': ObjectId(v['id'])}))
    assert call(owner, 'POST', prefix + '/' + v['id'] + '/seal', {'revision': 1}) == v
    call(owner, 'PUT', prefix + '/' + v['id'] + '/draft', {'revision': v['revision'], 'payload': f.AGE_V2}, 409)
    assert frozen == BSON.encode(db.survey_question_versions.find_one({'_id': ObjectId(v['id'])}))
    assert ObjectId(q['id']) != ObjectId(v['id'])
    assert v['payload_hash'] == catalog.digest(f.AGE)


def test_T2_01_03_two_surveys_fixed_answers_old_snapshots(context):
    app, owner, db, q, v = setup(context)
    surveys = {}
    for key, iids in [('a', [f.A1, f.A2]), ('b', [f.B1])]:
        s = new_survey(owner, 'F2-N ' + key)
        assert s['schema_version'] == 1 and 'question_result_grants' not in s
        for iid in iids:
            s = imported(owner, s, q, v, iid)
        assert s['schema_version'] == 2 and s['content_version'] is None
        assert s['question_result_grants'] == [{'question_id': q['id'], 'enabled': False, 'allowed_at_publish': False}]
        questions = {x['id']: x for x in s['questions']}
        body = draft_body(s)
        body['questions'] = ([f.gate(f.G0A), questions[f.A1], f.gate(f.G1A), questions[f.A2]]
                             if key == 'a' else [f.gate(f.G0B), questions[f.B1]])
        s = call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body)
        surveys[key] = call(owner, 'POST', '/surveys/' + s['id'] + '/publish', {'revision': s['revision']})
    fillers = {name: account(app, 's2_' + name) for name in ['u1', 'u2', 'u3', 'u4', 'u5']}
    for key, user, anonymous, answers in f.ROWS:
        c, s = fillers[user], surveys[key]
        submission = {'content_version': 1, 'anonymous': anonymous, 'submission_key': str(uuid4()), 'answers': answers}
        receipt = call(c, 'POST', '/fill/' + s['id'] + '/responses', submission, 201)
        assert call(c, 'POST', '/fill/' + s['id'] + '/responses', submission) == receipt
        doc = db.survey_responses.find_one({'_id': ObjectId(receipt['response_id'])})
        assert doc['schema_version'] == doc['content_version'] == 1
        assert doc['path'] == list(answers) and doc['anonymous'] == anonymous
        assert {a['question_id']: a['value'] for a in doc['answers']} == {
            iid: Decimal128(value) if iid in [f.A1, f.A2, f.B1] else value
            for iid, value in answers.items() if value is not None}
    expected_stats = {}
    for key, s in surveys.items():
        expected = f.EXPECTED[key]
        stats = call(owner, 'GET', '/surveys/' + s['id'] + '/statistics')
        expected_stats[key] = stats
        assert (stats['submission_count'], stats['respondent_count']) == (expected['submissions'], expected['people'])
        for iid, count, average in expected['ages']:
            item = next(x for x in stats['questions'] if x['id'] == iid)
            assert (item['answered_submission_count'], item['average_display']) == (count, average)
        bad = {'content_version': 1, 'submission_key': str(uuid4()),
               'answers': {f.G0A: f.CONTINUE, f.A1: '121', f.G1A: f.SKIP}} if key == 'a' else {
               'content_version': 1, 'submission_key': str(uuid4()), 'answers': {f.G0B: f.CONTINUE, f.B1: '121'}}
        call(fillers['u1'], 'POST', '/fill/' + s['id'] + '/responses', bad, 422)
        fill = call(fillers['u1'], 'GET', '/fill/' + s['id'])
        assert all(set(x) == set(catalog.PUBLIC_QUESTION_FIELDS) for x in fill['questions'])
        assert fill['result_sharing_notice'] == {'enabled': False, 'aggregate_only': True, 'question_instance_ids': []}
    old = {name: list(db[name].find().sort('_id', 1)) for name in ['survey_surveys', 'survey_responses']}
    old_v1 = db.survey_question_versions.find_one({'_id': ObjectId(v['id'])})
    v2 = call(owner, 'POST', '/questions/' + q['id'] + '/versions',
              {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    assert v2['payload'] == f.AGE and v2['parent_version_id'] == v['id']
    v2 = call(owner, 'PUT', f"/questions/{q['id']}/versions/{v2['id']}/draft", {'revision': 1, 'payload': f.AGE_V2})
    v2 = call(owner, 'POST', f"/questions/{q['id']}/versions/{v2['id']}/seal", {'revision': v2['revision']})
    assert v2['payload'] == f.AGE_V2 and v2['id'] != v['id']
    assert db.survey_question_versions.find_one({'_id': ObjectId(v['id'])}) == old_v1
    for name, rows in old.items():
        assert [BSON.encode(x) for x in db[name].find().sort('_id', 1)] == [BSON.encode(x) for x in rows]
    for key, s in surveys.items():
        assert call(owner, 'GET', '/surveys/' + s['id'] + '/statistics') == expected_stats[key]
        call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', draft_body(s), 409)
    assert db.survey_responses.count_documents({}) == 8
    number_answers = [(doc['_id'], doc['respondent_id'], a['value'].to_decimal())
        for doc in db.survey_responses.find() for a in doc['answers']
        if a['question_id'] in [f.A1, f.A2, f.B1]]
    assert len(number_answers) == 7 and sum(row[2] for row in number_answers) == 264
    assert len({row[0] for row in number_answers}) == 5 and len({row[1] for row in number_answers}) == 2
    new = imported(owner, new_survey(owner, 'F2-V v2'), q, v2, str(uuid4()))
    new = call(owner, 'POST', '/surveys/' + new['id'] + '/publish', {'revision': new['revision']})
    iid = new['questions'][0]['id']
    call(fillers['u1'], 'POST', '/fill/' + new['id'] + '/responses', {
        'content_version': 1, 'submission_key': str(uuid4()), 'answers': {iid: '40'}}, 201)
    assert call(owner, 'GET', '/surveys/' + new['id'] + '/statistics')['questions'][0]['average_display'] == '40.00'
    for key, survey in surveys.items():
        assert call(owner, 'GET', '/surveys/' + survey['id'] + '/statistics') == expected_stats[key]
    evidence = os.getenv('SURVEY_STAGE2_EVIDENCE')
    if evidence:
        from app.main import public
        directory = Path(evidence)
        directory.mkdir(parents=True, exist_ok=True)
        record = {'sample': 'F2-N/V', 'scope': 'two owned surveys; shared/granted aggregation deferred',
            'question_id': q['id'], 'v1': v, 'v2': v2, 'surveys': surveys,
            'independent_expected_per_survey': f.EXPECTED, 'actual_statistics': expected_stats,
            'actual_number_answer_count': len(number_answers), 'actual_number_sum': str(sum(x[2] for x in number_answers)),
            'old_rows_sha256': {name: hashlib.sha256(b''.join(BSON.encode(x) for x in rows)).hexdigest() for name, rows in old.items()},
            'old_documents': old, 'new_v2_statistics': call(owner, 'GET', '/surveys/' + new['id'] + '/statistics')}
        (directory / 'fixed-sample.json').write_text(json.dumps(public(record), ensure_ascii=False, indent=2))


@pytest.mark.parametrize('mutation', ['title', 'required', 'kind', 'validation', 'options', 'source', 'new-source', 'null-source'])
def test_T2_01_source_tampering_and_legacy_put(context, mutation):
    app, owner, db, q, v = setup(context)
    s = imported(owner, new_survey(owner), q, v, f.A1)
    body = draft_body(s)
    body['questions'][0].pop('source')
    s = call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body)
    assert s['questions'][0]['source']['question_version_id'] == v['id']
    before = db.survey_surveys.find_one({'_id': ObjectId(s['id'])})
    body = draft_body(s); question = body['questions'][0]
    if mutation == 'title': question['title'] = '篡改'
    if mutation == 'required': question['required'] = True
    if mutation == 'kind': question.update(kind='text', validation={'min_length': None, 'max_length': None})
    if mutation == 'validation': question['validation']['max_value'] = '200'
    if mutation == 'options': question['options'] = [{'id': str(uuid4()), 'text': '伪造'}]
    if mutation == 'source': question['source']['payload_hash'] = '0' * 64
    if mutation == 'new-source': question['id'] = f.A2
    if mutation == 'null-source': question['source'] = None
    call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body, 422)
    assert db.survey_surveys.find_one({'_id': before['_id']}) == before
    body = draft_body(s); body['question_result_grants'] = []
    call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body, 422)
    body = draft_body(s); body['questions'][0]['rules'] = [{'op': 'gte', 'operand': '18', 'target': 'END'}]
    s = call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body)
    assert s['questions'][0]['rules'][0]['target'] == 'END'
    body = draft_body(s); body['questions'] = []
    s = call(owner, 'PUT', '/surveys/' + s['id'] + '/draft', body)
    assert s['schema_version'] == 2 and s['question_result_grants'] == []


def test_T2_11_import_retry_after_write_failure_and_revocation(context, monkeypatch):
    app, owner, db, q, v = setup(context)
    s = new_survey(owner)
    body = {'revision': 1, 'instance_id': f.A1, 'question_id': q['id'], 'question_version_id': v['id']}
    collection_class = type(db.survey_surveys)
    original = collection_class.find_one_and_update
    triggered = False
    def dropped(self, query, change, *args, **kwargs):
        nonlocal triggered
        result = original(self, query, change, *args, **kwargs)
        if self.name == 'survey_surveys' and not triggered:
            triggered = True
            raise AutoReconnect('injected response lost after real CAS')
        return result
    monkeypatch.setattr(collection_class, 'find_one_and_update', dropped)
    call(owner, 'POST', '/surveys/' + s['id'] + '/question-imports', body, 503)
    assert db.survey_surveys.find_one({'_id': ObjectId(s['id'])})['schema_version'] == 2
    monkeypatch.setattr(collection_class, 'find_one_and_update', original)
    current = call(owner, 'POST', '/surveys/' + s['id'] + '/question-imports', body)
    current = call(owner, 'POST', '/surveys/' + s['id'] + '/publish', {'revision': current['revision']})
    current = call(owner, 'POST', '/surveys/' + s['id'] + '/close', {'revision': current['revision']})
    before = db.survey_surveys.find_one({'_id': ObjectId(s['id'])})
    assert call(owner, 'POST', '/surveys/' + s['id'] + '/question-imports', body) == current
    assert db.survey_surveys.find_one({'_id': before['_id']}) == before
    call(owner, 'POST', '/surveys/' + s['id'] + '/question-imports', {**body, 'question_version_id': str(ObjectId())}, 409)
    other = account(app, 's2_other')
    call(other, 'POST', '/surveys/' + s['id'] + '/question-imports', body, 404)


def test_T2_11_concurrent_import_and_versions(context):
    app, owner, db, q, v = setup(context)
    s = new_survey(owner)
    barrier = Barrier(2)
    def importing(iid):
        barrier.wait()
        return owner.post('/api/v1/surveys/' + s['id'] + '/question-imports', json={
            'revision': 1, 'instance_id': iid, 'question_id': q['id'], 'question_version_id': v['id']})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(importing, [f.A1, f.A2]))
    assert sorted(r.status_code for r in results) == [200, 409]
    doc = db.survey_surveys.find_one({'_id': ObjectId(s['id'])})
    assert doc['schema_version'] == 2 and len(doc['questions']) == len(doc['question_result_grants']) == 1
    key = str(uuid4())
    def derive(k):
        return owner.post('/api/v1/questions/' + q['id'] + '/versions', json={
            'creation_key': k, 'operation': 'derive', 'parent_version_id': v['id']})
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(derive, [key, key, str(uuid4()), str(uuid4()), str(uuid4())]))
    assert sorted(r.status_code for r in results) == [200, 201, 201, 201, 201]
    rows = list(db.survey_question_versions.find({'question_id': ObjectId(q['id'])}))
    assert len(rows) == 5 and len({x['version_no'] for x in rows}) == 5
    assert all(x['parent_version_id'] == ObjectId(v['id']) for x in rows if x['state'] == 'draft')


def test_T2_11_draft_seal_race_and_publish_source_validation(context):
    app, owner, db, q, v = setup(context)
    prefix = '/questions/' + q['id'] + '/versions'
    v2 = call(owner, 'POST', prefix, {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    barrier = Barrier(2)
    def competing(seal):
        barrier.wait()
        return owner.request('POST' if seal else 'PUT', '/api/v1' + prefix + '/' + v2['id'] + ('/seal' if seal else '/draft'),
            json={'revision': 1} if seal else {'revision': 1, 'payload': f.AGE_V2})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(competing, [False, True]))
    assert sorted(r.status_code for r in results) == [200, 409]
    current = db.survey_question_versions.find_one({'_id': ObjectId(v2['id'])})
    assert (current['state'], current['payload']['title']) in [('draft', f.AGE_V2['title']), ('sealed', f.AGE['title'])]
    s = imported(owner, new_survey(owner), q, v, f.A1)
    db.survey_surveys.update_one({'_id': ObjectId(s['id'])}, {'$set': {'questions.0.title': 'tampered stored payload'}})
    call(owner, 'POST', '/surveys/' + s['id'] + '/publish', {'revision': s['revision']}, 422)


def test_T2_01_permissions_whitelist_and_unexposed_routes(context):
    app, owner, db, q, v = setup(context)
    stranger = account(app, 's2_stranger')
    s = imported(owner, new_survey(owner), q, v, f.A1)
    for path in ['/questions/' + q['id'], f"/questions/{q['id']}/versions/{v['id']}",
                 '/surveys/' + s['id'], f"/surveys/{s['id']}/questions/{f.A1}/source",
                 '/surveys/' + s['id'] + '/statistics', f"/surveys/{s['id']}/questions/{f.A1}/contents"]:
        call(stranger, 'GET', path, status=404)
    call(stranger, 'POST', '/surveys/' + new_survey(stranger)['id'] + '/question-imports',
         {'revision': 1, 'instance_id': f.B1, 'question_id': q['id'], 'question_version_id': v['id']}, 404)
    paths = app.openapi()['paths']
    assert {'/api/v1/questions/{qid}/sharing', '/api/v1/question-banks',
            '/api/v1/questions/{qid}/usages', '/api/v1/surveys/{sid}/question-result-grants/{qid}'} <= set(paths)
    assert {p for p in paths if 'distribution' in p} == {'/api/v1/questions/{qid}/versions/{vid}/distribution'}
    assert '/api/v1/questions/{qid}/statistics' in paths
    for path in [f"/questions/{q['id']}/statistics", f"/questions/{q['id']}/versions/{v['id']}/distribution"]:
        call(stranger, 'GET', path, status=404)
    assert 'creation_key' not in q and 'creation_digest' not in q and 'owner_id' not in q
    assert 'creation_key' not in v and 'creation_digest' not in v


def test_T2_11_import_permission_admission_and_revoked_retry(context, monkeypatch):
    app, owner, db, q, v = setup(context)
    receiver = account(app, 's2_receiver')
    uid = db.survey_users.find_one({'username': 's2_receiver'})['_id']
    db.survey_questions.update_one({'_id': ObjectId(q['id'])}, {'$set': {'shared_with': [uid]}})
    s = new_survey(receiver)
    original = catalog.family
    reads = 0
    def revoked_after_read(database, qid, account_id, use=False):
        nonlocal reads
        result = original(database, qid, account_id, use)
        if use and account_id == uid:
            reads += 1
            if reads == 2:
                db.survey_questions.update_one({'_id': ObjectId(q['id'])}, {'$set': {'shared_with': []}})
        return result
    monkeypatch.setattr(catalog, 'family', revoked_after_read)
    saved = imported(receiver, s, q, v, f.B1)
    assert reads == 2 and len(saved['questions']) == 1
    before = db.survey_surveys.find_one({'_id': ObjectId(s['id'])})
    assert imported(receiver, s, q, v, f.B1) == saved
    assert db.survey_surveys.find_one({'_id': before['_id']}) == before
    call(receiver, 'POST', '/surveys/' + s['id'] + '/question-imports', {
        'revision': saved['revision'], 'instance_id': f.A2, 'question_id': q['id'], 'question_version_id': v['id']}, 404)
    # Revocation does not make the saved snapshot unpublishable or reveal family history.
    call(receiver, 'POST', '/surveys/' + s['id'] + '/publish', {'revision': saved['revision']})
    assert call(receiver, 'GET', f"/surveys/{s['id']}/questions/{f.B1}/source")['id'] == v['id']
    call(receiver, 'GET', '/questions/' + q['id'] + '/versions', status=404)
    call(owner, 'GET', '/surveys/' + s['id'] + '/statistics', status=404)


def test_T2_11_create_write_lost_response_recovers_original_key(context, monkeypatch):
    app, owner, db, q, v = setup(context)
    cls = type(db.survey_question_versions)
    original = cls.insert_one
    triggered = False
    def dropped(self, *args, **kwargs):
        nonlocal triggered
        result = original(self, *args, **kwargs)
        if self.name == 'survey_question_versions' and not triggered:
            triggered = True
            raise AutoReconnect('injected response lost after real insert')
        return result
    monkeypatch.setattr(cls, 'insert_one', dropped)
    body = {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}
    prefix = '/questions/' + q['id'] + '/versions'
    call(owner, 'POST', prefix, body, 503)
    recovered = call(owner, 'POST', prefix, body)
    assert recovered['parent_version_id'] == v['id']
    assert db.survey_question_versions.count_documents({'question_id': ObjectId(q['id'])}) == 2
    assert call(owner, 'POST', prefix, body) == recovered


def test_T2_01_unauthorized_draft_is_not_a_state_oracle(context):
    app, owner, db, q, v = setup(context)
    draft = call(owner, 'POST', '/questions/' + q['id'] + '/versions',
                 {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    other = account(app, 's2_other')
    s = new_survey(other)
    call(other, 'POST', '/surveys/' + s['id'] + '/question-imports', {
        'revision': 1, 'instance_id': f.A1, 'question_id': q['id'], 'question_version_id': draft['id']}, 404)


def test_T2_11_version_limits_and_strict_inputs(context):
    app, owner, db, q, v = setup(context)
    prefix = '/questions/' + q['id'] + '/versions'
    for extra in [{'owner_id': str(ObjectId())}, {'payload': {**f.AGE, 'rules': []}},
                  {'operation': 'restore', 'parent_version_id': v['id']}, {'payload': {**f.AGE, 'source': {}}}]:
        call(owner, 'POST', prefix, {'creation_key': str(uuid4()), 'payload': f.AGE, **extra}, 422)
    draft = call(owner, 'POST', prefix, {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    db.survey_question_versions.update_one({'_id': ObjectId(draft['id'])}, {'$set': {'revision': LIMITS['revision']}})
    call(owner, 'PUT', prefix + '/' + draft['id'] + '/draft', {'revision': LIMITS['revision'], 'payload': f.AGE}, 409)
    call(owner, 'POST', prefix + '/' + draft['id'] + '/seal', {'revision': LIMITS['revision']}, 409)
    db.survey_question_versions.update_one({'_id': ObjectId(draft['id'])}, {'$set': {'version_no': LIMITS['revision']}})
    call(owner, 'POST', prefix, {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 409)
