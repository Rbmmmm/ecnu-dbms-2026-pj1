"""II-W004: independent F2-V/P assertions, real MongoDB, admission and recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4
import json
import os
from pathlib import Path
from bson import BSON, ObjectId
import pytest
from pymongo.collection import Collection
from pymongo.errors import AutoReconnect
from app import question_catalog as catalog
from app.config import LIMITS
from tests import fixtures_stage2 as f
from tests.test_stage2_catalog import call, account, setup, new_survey, imported, draft_body


def actors(context):
    app, a, db, q, v = setup(context)
    b, c = account(app, '用户乙'), account(app, '用户丙')
    return app, a, b, c, db, q, v


def share(a, q, names):
    current = call(a, 'GET', '/questions/' + q['id'])
    return call(a, 'PUT', '/questions/' + q['id'] + '/sharing', {'revision': current['revision'], 'usernames': names})


def new_version(a, q, v, operation='derive', value=None):
    body = {'creation_key': str(uuid4()), 'operation': operation, 'parent_version_id': v['id']}
    if operation == 'restore': body['restored_from_version_id'] = v['id']
    result = call(a, 'POST', '/questions/' + q['id'] + '/versions', body, 201)
    if value is not None:
        result = call(a, 'PUT', f"/questions/{q['id']}/versions/{result['id']}/draft", {'revision': result['revision'], 'payload': value})
    return call(a, 'POST', f"/questions/{q['id']}/versions/{result['id']}/seal", {'revision': result['revision']})


def grant(c, s, q, enabled, status=200, revision=None):
    return call(c, 'PUT', f"/surveys/{s['id']}/question-result-grants/{q['id']}", {'revision': s['revision'] if revision is None else revision, 'enabled': enabled}, status)


def publish(c, s):
    return call(c, 'POST', '/surveys/' + s['id'] + '/publish', {'revision': s['revision']})


def bank(c, title='个人题库'):
    return call(c, 'POST', '/question-banks', {'title': title, 'creation_key': str(uuid4())}, 201)


def entry(q, v):
    return {'question_id': q['id'], 'default_version_id': v['id']}


def put_bank(c, lib, entries, status=200, title=None):
    return call(c, 'PUT', '/question-banks/' + lib['id'], {'revision': lib['revision'], 'title': title or lib['title'], 'entries': entries}, status)


def snapshot(db, names):
    return {n: [BSON.encode(x) for x in db[n].find().sort('_id', 1)] for n in names}


def test_T2_02_three_actors_sharing_roles_strict_shapes(context):
    app, a, b, c, db, q, v = actors(context)
    shared = share(a, q, [' 用户乙 '])
    assert shared['shared_with'] == ['用户乙'] and shared['role'] == 'owner'
    received = call(b, 'GET', '/questions/' + q['id'])
    assert received['role'] == 'shared'
    assert set(received) == {'id', 'label', 'revision', 'created_at', 'updated_at', 'role'}
    rows = call(b, 'GET', '/questions?scope=shared&limit=1')
    assert rows['total'] == 1 and rows['items'][0]['id'] == q['id']
    assert call(c, 'GET', '/questions?scope=shared')['total'] == 0
    for names in [['用户乙', ' 用户乙 '], ['s2_owner'], ['unknown_user'], ['用户乙'] * 101]:
        call(a, 'PUT', '/questions/' + q['id'] + '/sharing', {'revision': shared['revision'], 'usernames': names}, 422)
    for actor in [b, c]:
        call(actor, 'PUT', '/questions/' + q['id'] + '/sharing', {'revision': shared['revision'], 'usernames': []}, 404)
        call(actor, 'PATCH', '/questions/' + q['id'], {'revision': shared['revision'], 'label': '篡改'}, 404)
        call(actor, 'POST', '/questions/' + q['id'] + '/versions', {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 404)
        call(actor, 'POST', f"/questions/{q['id']}/versions/{v['id']}/seal", {'revision': v['revision']}, 404)
        call(actor, 'PUT', f"/questions/{q['id']}/versions/{v['id']}/draft", {'revision': v['revision'], 'payload': f.AGE}, 404)
    call(c, 'GET', '/questions/' + q['id'], status=404)
    call(c, 'POST', '/surveys/' + new_survey(c)['id'] + '/question-imports', {'revision': 1, 'instance_id': f.B1, 'question_id': q['id'], 'question_version_id': v['id']}, 404)
    own = imported(a, new_survey(a), q, v, f.A1)
    receiver = imported(b, new_survey(b), q, v, f.B1)
    assert own['questions'][0]['source'] == receiver['questions'][0]['source']
    for actor, other in [(a, receiver), (b, own), (c, receiver)]:
        for suffix in ['', '/statistics', '/questions/' + other['questions'][0]['id'] + '/contents']:
            call(actor, 'GET', '/surveys/' + other['id'] + suffix, status=404)
    before = db.survey_questions.find_one({'_id': ObjectId(q['id'])})
    call(a, 'PUT', '/questions/' + q['id'] + '/sharing', {'revision': shared['revision'], 'usernames': [], 'owner_id': q['id']}, 422)
    assert db.survey_questions.find_one({'_id': before['_id']}) == before


def test_T2_04_history_restore_independent_f2v_and_restricted_sources(context):
    app, a, b, c, db, q, v1 = actors(context)
    share(a, q, ['用户乙'])
    old = imported(a, new_survey(a, 'F2-V旧卷'), q, v1, f.A1)
    old = imported(a, old, q, v1, f.A2)
    old = publish(a, old)
    call(c, 'POST', '/fill/' + old['id'] + '/responses', {'content_version': 1, 'submission_key': str(uuid4()), 'answers': {f.A1: '0', f.A2: '18'}}, 201)
    previous = snapshot(db, ['survey_surveys', 'survey_responses'])
    old_version = BSON.encode(db.survey_question_versions.find_one({'_id': ObjectId(v1['id'])}))
    old_stats = call(a, 'GET', '/surveys/' + old['id'] + '/statistics')
    v2 = new_version(a, q, v1, value=f.AGE_V2)
    body = {'creation_key': str(uuid4()), 'operation': 'restore', 'parent_version_id': v1['id'], 'restored_from_version_id': v1['id']}
    v3 = call(a, 'POST', '/questions/' + q['id'] + '/versions', body, 201)
    assert call(a, 'POST', '/questions/' + q['id'] + '/versions', body) == v3
    assert v3['payload'] == f.AGE and v3['id'] not in [v1['id'], v2['id']]
    assert v3['operation'] == 'restore' and v3['parent_version_id'] == v3['restored_from_version_id'] == v1['id']
    assert [x['id'] for x in call(b, 'GET', '/questions/' + q['id'] + '/versions')['items']] == [v2['id'], v1['id']]
    call(b, 'GET', f"/questions/{q['id']}/versions/{v3['id']}", status=404)
    v3 = call(a, 'POST', f"/questions/{q['id']}/versions/{v3['id']}/seal", {'revision': 1})
    assert v3['payload_hash'] == v1['payload_hash'] and v2['parent_version_id'] == v1['id']
    assert snapshot(db, previous) == previous
    assert BSON.encode(db.survey_question_versions.find_one({'_id': ObjectId(v1['id'])})) == old_version
    assert call(a, 'GET', '/surveys/' + old['id'] + '/statistics') == old_stats
    observed = []
    for version, value in [(v2, '40'), (v3, '60')]:
        s = publish(b, imported(b, new_survey(b), q, version, str(uuid4())))
        iid = s['questions'][0]['id']
        call(c, 'POST', '/fill/' + s['id'] + '/responses', {'content_version': 1, 'submission_key': str(uuid4()), 'answers': {iid: value}}, 201)
        stat = call(b, 'GET', '/surveys/' + s['id'] + '/statistics')['questions'][0]
        assert stat['average_display'] == value + '.00'
        observed.append({'version': version, 'survey_id': s['id'], 'average_display': stat['average_display']})
    share(a, q, [])
    call(b, 'GET', '/questions/' + q['id'], status=404)
    call(b, 'GET', f"/questions/{q['id']}/versions/{v1['id']}", status=404)
    source = call(b, 'GET', f"/surveys/{s['id']}/questions/{iid}/source")
    assert source['id'] == v3['id'] and source['parent_version_id'] == v1['id']
    call(c, 'GET', f"/surveys/{s['id']}/questions/{iid}/source", status=404)
    if os.getenv('SURVEY_MANAGEMENT_EVIDENCE'):
        Path(os.environ['SURVEY_MANAGEMENT_EVIDENCE']).mkdir(exist_ok=True, parents=True)
        Path(os.environ['SURVEY_MANAGEMENT_EVIDENCE'], 'fixed-f2v.json').write_text(json.dumps({'expected': {'v2': '40.00', 'v3': '60.00', 'restored_payload': f.AGE}, 'actual': observed, 'old_snapshot_unchanged': True}, ensure_ascii=False, indent=2))


@pytest.mark.parametrize('extra', [ {'payload': f.AGE}, {'restored_from_version_id': None}, {'parent_version_id': None}, {'operation': 'derive'}])
def test_T2_04_restore_rejects_spoofed_shape(context, extra):
    app, a, db, q, v = setup(context)
    body = {'creation_key': str(uuid4()), 'operation': 'restore', 'parent_version_id': v['id'], 'restored_from_version_id': v['id'], **extra}
    count = db.survey_question_versions.count_documents({})
    call(a, 'POST', '/questions/' + q['id'] + '/versions', body, 422)
    assert db.survey_question_versions.count_documents({}) == count


def test_T2_06_usage_minimum_fields_and_permission_first_paging(context):
    app, a, b, c, db, q, v = actors(context)
    share(a, q, ['用户乙'])
    mine = imported(a, new_survey(a, '甲的卷'), q, v, f.A1)
    mine = imported(a, mine, q, v, f.A2)
    theirs = publish(b, imported(b, new_survey(b, '乙的卷'), q, v, f.B1))
    closed = call(b, 'POST', '/surveys/' + theirs['id'] + '/close', {'revision': theirs['revision']})
    v2 = new_version(a, q, v, value=f.AGE_V2)
    extra = publish(b, imported(b, new_survey(b, '到期卷'), q, v2, f.A1))
    from datetime import timedelta
    db.survey_surveys.update_one({'_id': ObjectId(extra['id'])}, {'$set': {'deadline_at': app.state.clock() - timedelta(days=1)}})
    rows = call(a, 'GET', '/questions/' + q['id'] + '/usages')
    assert rows['total'] == 3
    for row in rows['items']:
        assert set(row) == {'survey_id', 'title', 'state', 'expired', 'versions'}
        assert all(set(x) == {'version_id', 'instance_count'} for x in row['versions'])
    assert next(x for x in rows['items'] if x['survey_id'] == mine['id'])['versions'] == [{'version_id': v['id'], 'instance_count': 2}]
    assert next(x for x in rows['items'] if x['survey_id'] == closed['id'])['state'] == 'closed'
    assert next(x for x in rows['items'] if x['survey_id'] == extra['id'])['expired'] is True
    assert call(b, 'GET', '/questions/' + q['id'] + '/usages?limit=1&offset=1')['total'] == 2
    assert call(a, 'GET', '/questions/' + q['id'] + '/usages?version_id=' + v2['id'])['total'] == 1
    call(c, 'GET', '/questions/' + q['id'] + '/usages', status=404)
    share(a, q, [])
    assert call(b, 'GET', '/questions/' + q['id'] + '/usages')['total'] == 2
    unused = new_version(a, q, v)
    call(b, 'GET', '/questions/' + q['id'] + '/usages?version_id=' + unused['id'], status=404)


def test_T2_05_same_family_swap_payload_edges_and_old_put(context):
    app, a, b, c, db, q, v = actors(context)
    share(a, q, ['用户乙'])
    v2 = new_version(a, q, v, value=f.AGE_V2)
    s = imported(b, new_survey(b), q, v, f.A1)
    s = imported(b, s, q, v, f.A2)
    body = draft_body(s)
    body['questions'].insert(0, f.gate(f.G0A))
    body['questions'][0]['rules'][0]['target'] = f.A1
    body['questions'][1]['rules'] = [{'op': 'gte', 'operand': '18', 'target': f.A2}]
    s = call(b, 'PUT', '/surveys/' + s['id'] + '/draft', body)
    source_path = f"/surveys/{s['id']}/questions/{f.A1}/source"
    request = {'revision': s['revision'], 'question_id': q['id'], 'question_version_id': v2['id']}
    updated = call(b, 'PUT', source_path, request)
    assert updated['questions'][1]['id'] == f.A1 and updated['questions'][1]['rules'] == []
    assert {k: updated['questions'][1][k] for k in catalog.PAYLOAD_FIELDS} == f.AGE_V2
    assert updated['questions'][0]['rules'] == s['questions'][0]['rules']
    assert updated['questions'][2] == s['questions'][2]
    assert call(b, 'PUT', source_path, request) == updated
    assert call(a, 'GET', '/questions/' + q['id'] + '/usages')['items'][0]['versions'] == sorted([
        {'version_id': v['id'], 'instance_count': 1}, {'version_id': v2['id'], 'instance_count': 1}], key=lambda x: x['version_id'])
    other = call(a, 'POST', '/questions', {'creation_key': str(uuid4()), 'label': '同名'}, 201)
    call(b, 'PUT', source_path, {**request, 'revision': updated['revision'], 'question_id': other['id']}, 422)
    assert call(b, 'GET', '/surveys/' + s['id']) == updated
    tampered = draft_body(updated);tampered['questions'][1]['required'] = True
    call(b, 'PUT', '/surveys/' + s['id'] + '/draft', tampered, 422)
    unchanged = draft_body(updated)
    for question in unchanged['questions']: question.pop('source', None)
    preserved = call(b, 'PUT', '/surveys/' + s['id'] + '/draft', unchanged)
    assert preserved['questions'] == updated['questions']
    share(a, q, [])
    call(b, 'PUT', source_path, {**request, 'revision': preserved['revision'], 'question_version_id': v['id']}, 404)
    assert publish(b, preserved)['state'] == 'published'


def test_T2_07_banks_explicit_default_no_propagation_and_revoked_entries(context):
    app, a, b, c, db, q, v = actors(context)
    share(a, q, ['用户乙'])
    v2 = new_version(a, q, v, value=f.AGE_V2)
    lib = bank(b)
    lib = put_bank(b, lib, [entry(q, v)])
    assert lib['entries'][0]['available'] is True
    s = imported(b, new_survey(b), q, v, f.B1)
    frozen = db.survey_surveys.find_one({'_id': ObjectId(s['id'])})
    lib = put_bank(b, lib, [entry(q, v2)])
    assert lib['entries'][0]['default_version_id'] == v2['id']
    assert db.survey_surveys.find_one({'_id': frozen['_id']}) == frozen
    next_s = imported(b, new_survey(b), q, v2, f.A1)
    assert next_s['questions'][0]['validation']['max_value'] == '200'
    assert len(call(b, 'GET', '/question-banks')['items']) == 1
    call(a, 'GET', '/question-banks/' + lib['id'], status=404)
    put_bank(c, lib, [], 404)
    put_bank(b, lib, [entry(q, v), entry(q, v2)], 422)
    share(a, q, [])
    unavailable = call(b, 'GET', '/question-banks/' + lib['id'])
    assert unavailable['entries'] == [{**entry(q, v2), 'available': False}]
    lib = put_bank(b, lib, [entry(q, v2)], title='失效条目仍可保留')
    put_bank(b, lib, [entry(q, v)], 404)
    put_bank(c, bank(c), [entry(q, v)], 404)
    call(b, 'POST', '/surveys/' + s['id'] + '/question-imports', {'revision': s['revision'], 'instance_id': f.A2, 'question_id': q['id'], 'question_version_id': v2['id']}, 404)
    lib = put_bank(b, lib, [])
    assert lib['entries'] == []
    assert db.survey_surveys.find_one({'_id': frozen['_id']}) == frozen
    assert db.survey_question_versions.find_one({'_id': ObjectId(v['id'])})['state'] == 'sealed'


def test_T2_02_result_grant_freeze_revoke_notice_and_original_owner_only(context):
    app, a, b, c, db, q, v = actors(context)
    share(a, q, ['用户乙'])
    own = imported(a, new_survey(a), q, v, f.A1)
    grant(a, own, q, True, 422)
    s = imported(b, new_survey(b), q, v, f.B1)
    s = imported(b, s, q, v, f.A2)
    assert s['question_result_grants'][0] == {'question_id': q['id'], 'enabled': False, 'allowed_at_publish': False}
    grant(a, s, q, True, 404)
    grant(c, s, q, True, 404)
    s = grant(b, s, q, True)
    assert s['question_result_grants'][0]['allowed_at_publish'] is False
    s = publish(b, s)
    assert s['question_result_grants'] == [{'question_id': q['id'], 'enabled': True, 'allowed_at_publish': True}]
    before = snapshot(db, ['survey_responses'])
    notice = call(c, 'GET', '/fill/' + s['id'])
    assert notice['result_sharing_notice'] == {'enabled': True, 'aggregate_only': True, 'question_instance_ids': [f.B1, f.A2]}
    assert all(set(x) == set(catalog.PUBLIC_QUESTION_FIELDS) for x in notice['questions'])
    share(a, q, [])
    assert call(c, 'GET', '/fill/' + s['id'])['result_sharing_notice']['enabled'] is True
    s = call(b, 'POST', '/surveys/' + s['id'] + '/close', {'revision': s['revision']})
    assert s['question_result_grants'][0]['enabled'] is True
    old_revision = s['revision'];s = grant(b, s, q, False)
    assert grant(b, s, q, False, revision=old_revision) == s
    assert s['question_result_grants'][0] == {'question_id': q['id'], 'enabled': False, 'allowed_at_publish': True}
    grant(b, s, q, True, 409)
    assert call(c, 'GET', '/fill/' + s['id'])['result_sharing_notice']['enabled'] is False
    assert snapshot(db, before) == before
    ungranted = publish(a, own)
    grant(a, ungranted, q, True, 422)
    share(a, q, ['用户乙'])
    off = publish(b, imported(b, new_survey(b), q, v, str(uuid4())))
    grant(b, off, q, True, 409)
    for path in [f"/questions/{q['id']}/statistics", f"/questions/{q['id']}/versions/{v['id']}/distribution"]:
        call(a, 'GET', path, status=404)


def test_T2_02_f2p_text_grant_does_not_expose_raw_answers(context):
    app, a, b, c, db, q, age = actors(context)
    qt = call(a, 'POST', '/questions', {'label': 'F2-P文本', 'creation_key': str(uuid4())}, 201)
    vt = call(a, 'POST', '/questions/' + qt['id'] + '/versions', {'creation_key': str(uuid4()), 'payload': {'kind': 'text', 'title': '留言', 'required': False, 'validation': {}, 'options': []}}, 201)
    vt = call(a, 'POST', f"/questions/{qt['id']}/versions/{vt['id']}/seal", {'revision': 1})
    share(a, qt, ['用户乙'])
    s = grant(b, imported(b, new_survey(b), qt, vt, f.B1), qt, True)
    s = publish(b, s)
    for value in ['甲', '乙']:
        call(c, 'POST', '/fill/' + s['id'] + '/responses', {'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': True, 'answers': {f.B1: value}}, 201)
    contents = call(b, 'GET', f"/surveys/{s['id']}/questions/{f.B1}/contents")
    assert sorted(x['value'] for x in contents['items']) == ['乙', '甲']
    assert all(x['anonymous'] and 'username' not in x and 'respondent_id' not in x for x in contents['items'])
    for suffix in ['/statistics', f'/questions/{f.B1}/contents']:
        call(a, 'GET', '/surveys/' + s['id'] + suffix, status=404)
    uses = call(a, 'GET', '/questions/' + qt['id'] + '/usages')
    assert uses['total'] == 1 and set(uses['items'][0]) == {'survey_id', 'title', 'state', 'expired', 'versions'}
    rows = list(db.survey_responses.find({'survey_id': ObjectId(s['id'])}))
    assert len(rows) == 2 and len({x['respondent_id'] for x in rows}) == 1


@pytest.mark.parametrize('operation', ['sharing', 'bank', 'swap', 'grant'])
def test_T2_11_write_committed_response_lost_exact_retry(context, monkeypatch, operation):
    app, a, b, c, db, q, v = actors(context)
    share(a, q, ['用户乙'])
    v2 = new_version(a, q, v, value=f.AGE_V2)
    s = imported(b, new_survey(b), q, v, f.A1)
    lib = bank(b)
    if operation == 'sharing':
        current = call(a, 'GET', '/questions/' + q['id']);actor=a;collection='survey_questions';resource=q['id']
        path='/questions/'+q['id']+'/sharing';body={'revision':current['revision'], 'usernames': []}
    elif operation == 'bank':
        actor=b;collection='survey_question_banks';resource=lib['id'];path='/question-banks/'+lib['id'];body={'revision':lib['revision'], 'title':lib['title'], 'entries':[entry(q,v)]}
    else:
        actor=b;collection='survey_surveys';resource=s['id']
        path=(f"/surveys/{s['id']}/questions/{f.A1}/source" if operation=='swap' else f"/surveys/{s['id']}/question-result-grants/{q['id']}")
        body=({'revision':s['revision'], 'question_id':q['id'], 'question_version_id':v2['id']} if operation=='swap' else {'revision':s['revision'], 'enabled':True})
    original=Collection.find_one_and_update;fired=False
    def lost(self,*args,**kwargs):
        nonlocal fired
        result=original(self,*args,**kwargs)
        if self.database.name==db.name and self.name==collection and not fired:
            fired=True;raise AutoReconnect('injected response loss after actual CAS')
        return result
    monkeypatch.setattr(Collection,'find_one_and_update',lost)
    call(actor,'PUT',path,body,503)
    saved=deepcopy(db[collection].find_one({'_id':ObjectId(resource)}))
    assert fired and saved['revision']==body['revision']+1
    call(actor,'PUT',path,body)
    assert db[collection].find_one({'_id':saved['_id']})==saved


@pytest.mark.parametrize('operation', ['bank-create', 'restore'])
def test_T2_11_creation_commit_lost_original_key(context, monkeypatch, operation):
    app,a,db,q,v=setup(context)
    path='/question-banks' if operation=='bank-create' else '/questions/'+q['id']+'/versions'
    body=({'title':'原键建库','creation_key':str(uuid4())} if operation=='bank-create' else {'creation_key':str(uuid4()),'operation':'restore','parent_version_id':v['id'],'restored_from_version_id':v['id']})
    name='survey_question_banks' if operation=='bank-create' else 'survey_question_versions'
    original=Collection.insert_one;fired=False
    def lost(self,*args,**kwargs):
        nonlocal fired
        result=original(self,*args,**kwargs)
        if self.database.name==db.name and self.name==name and not fired:
            fired=True;raise AutoReconnect('injected insert committed then disconnected')
        return result
    monkeypatch.setattr(Collection,'insert_one',lost)
    call(a,'POST',path,body,503)
    recovered=call(a,'POST',path,body)
    assert db[name].count_documents({'creation_key':body['creation_key']})==1
    assert call(a,'POST',path,body)['id']==recovered['id']
    call(a,'POST',path,{**body,**({'title':'different'} if operation=='bank-create' else {'operation':'derive','restored_from_version_id':None})},409)


@pytest.mark.parametrize('operation', ['sharing','bank','grant-delete','grant-publish','swap-save'])
def test_T2_11_same_revision_competing_mutations(context, monkeypatch, operation):
    app,a,b,c,db,q,v=actors(context);share(a,q,['用户乙'])
    v2=new_version(a,q,v,value=f.AGE_V2)
    s=imported(b,new_survey(b),q,v,f.A1);lib=bank(b)
    if operation=='sharing':
        current=call(a,'GET','/questions/'+q['id']);collection='survey_questions'
        tasks=[(a,'PUT','/questions/'+q['id']+'/sharing',{'revision':current['revision'],'usernames':x}) for x in [[],['用户丙']]]
    elif operation=='bank':
        collection='survey_question_banks';tasks=[(b,'PUT','/question-banks/'+lib['id'],{'revision':lib['revision'],'title':lib['title'],'entries':[entry(q,x)]}) for x in [v,v2]]
    else:
        collection='survey_surveys';g=(b,'PUT',f"/surveys/{s['id']}/question-result-grants/{q['id']}",{'revision':s['revision'],'enabled':True})
        if operation=='grant-delete':
            body=draft_body(s);body['questions']=[];tasks=[g,(b,'PUT','/surveys/'+s['id']+'/draft',body)]
        elif operation=='grant-publish':tasks=[g,(b,'POST','/surveys/'+s['id']+'/publish',{'revision':s['revision']})]
        else:tasks=[(b,'PUT',f"/surveys/{s['id']}/questions/{f.A1}/source",{'revision':s['revision'],'question_id':q['id'],'question_version_id':v2['id']}),(b,'PUT','/surveys/'+s['id']+'/draft',draft_body(s))]
    barrier=Barrier(2);original=Collection.find_one_and_update
    def competing(self,*args,**kwargs):
        if self.database.name==db.name and self.name==collection:barrier.wait(timeout=10)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Collection,'find_one_and_update',competing)
    def execute(task):
        client,method,path,body=task
        return client.request(method,'/api/v1'+path,json=body)
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(execute,tasks))
    assert sorted(x.status_code for x in result)==[200,409], [x.text for x in result]
    current=db.survey_surveys.find_one({'_id':ObjectId(s['id'])})
    assert {x['question_id'] for x in current['question_result_grants']} == {x['source']['question_id'] for x in current['questions'] if 'source'in x}
    if current['state']=='published':assert all(x['enabled']==x['allowed_at_publish'] for x in current['question_result_grants'])


@pytest.mark.parametrize('operation', ['bank','swap'])
@pytest.mark.parametrize('timing', ['before-last','after-last'])
def test_T2_11_permission_admission_revocation(context,monkeypatch,operation,timing):
    app,a,b,c,db,q,v=actors(context);share(a,q,['用户乙'])
    v2=new_version(a,q,v,value=f.AGE_V2)
    s=imported(b,new_survey(b),q,v,f.A1);lib=bank(b)
    uid=db.survey_users.find_one({'username':'用户乙'})['_id'];original=catalog.family;reads=0
    def revoke(database,qid,user,use=False):
        nonlocal reads
        if use and user==uid:
            reads+=1
            if reads==2 and timing=='before-last':share(a,q,[])
        result=original(database,qid,user,use)
        if use and user==uid and reads==2 and timing=='after-last':share(a,q,[])
        return result
    monkeypatch.setattr(catalog,'family',revoke)
    expected=200 if timing=='after-last' else 404
    if operation=='bank':put_bank(b,lib,[entry(q,v2)],expected)
    else:call(b,'PUT',f"/surveys/{s['id']}/questions/{f.A1}/source",{'revision':s['revision'],'question_id':q['id'],'question_version_id':v2['id']},expected)
    assert reads==2
    put_bank(b,bank(b),[entry(q,v)],404)
    call(b,'POST','/surveys/'+s['id']+'/question-imports',{'revision':s['revision']+1,'instance_id':f.A2,'question_id':q['id'],'question_version_id':v['id']},409 if operation=='bank' or timing=='before-last' else 404)


def test_T2_11_revoked_import_retry_after_publish_and_bank_limit(context):
    app,a,b,c,db,q,v=actors(context);share(a,q,['用户乙'])
    s=new_survey(b);saved=imported(b,s,q,v,f.A1)
    lib=bank(b);put_bank(b,lib,[entry(q,v)])
    share(a,q,[]);published=publish(b,saved)
    assert imported(b,s,q,v,f.A1)==published
    body={'title':'纠错','creation_key':str(uuid4())}
    call(b,'POST','/question-banks',{**body,'title':' '},422)
    created=call(b,'POST','/question-banks',body,201)
    put_bank(b,created,[entry(q,v)],404)
    db.survey_question_banks.update_one({'_id':ObjectId(created['id'])},{'$set':{'revision':LIMITS['revision']}})
    created=call(b,'GET','/question-banks/'+created['id'])
    put_bank(b,created,[],409,title='耗尽')


def test_T2_11_old_success_retry_never_reopens_later_revocation(context):
    app,a,b,c,db,q,v=actors(context)
    initial=call(a,'GET','/questions/'+q['id'])
    share_body={'revision':initial['revision'],'usernames':['用户乙']}
    shared=call(a,'PUT','/questions/'+q['id']+'/sharing',share_body)
    s=imported(b,new_survey(b),q,v,f.A1)
    enabled_body={'revision':s['revision'],'enabled':True}
    enabled=call(b,'PUT',f"/surveys/{s['id']}/question-result-grants/{q['id']}",enabled_body)
    revoked=grant(b,enabled,q,False)
    call(b,'PUT',f"/surveys/{s['id']}/question-result-grants/{q['id']}",enabled_body,409)
    assert call(b,'GET','/surveys/'+s['id'])==revoked
    revoked_share=share(a,q,[])
    call(a,'PUT','/questions/'+q['id']+'/sharing',share_body,409)
    assert call(a,'GET','/questions/'+q['id'])==revoked_share
    rename={'revision':revoked_share['revision'],'label':'新管理标签'}
    renamed=call(a,'PATCH','/questions/'+q['id'],rename)
    assert call(a,'PATCH','/questions/'+q['id'],rename)==renamed
    assert renamed['label']=='新管理标签'
    assert call(b,'GET',f"/surveys/{s['id']}/questions/{f.A1}/source")['payload']==f.AGE


def test_T2_04_parent_must_be_same_family_sealed(context):
    app,a,db,q,v=setup(context)
    another=call(a,'POST','/questions',{'creation_key':str(uuid4()),'label':'另一家族'},201)
    body={'creation_key':str(uuid4()),'operation':'restore','parent_version_id':v['id'],'restored_from_version_id':v['id']}
    call(a,'POST','/questions/'+another['id']+'/versions',body,404)
    draft=call(a,'POST','/questions/'+q['id']+'/versions',{'creation_key':str(uuid4()),'operation':'derive','parent_version_id':v['id']},201)
    call(a,'POST','/questions/'+q['id']+'/versions',{**body,'creation_key':str(uuid4()),'parent_version_id':draft['id'],'restored_from_version_id':draft['id']},409)


def test_T2_07_banks_strict_inputs_drafts_and_revisions(context):
    app,a,db,q,v=setup(context)
    lib=bank(a)
    draft=call(a,'POST','/questions/'+q['id']+'/versions',{'creation_key':str(uuid4()),'operation':'derive','parent_version_id':v['id']},201)
    put_bank(a,lib,[entry(q,draft)],409)
    put_bank(a,lib,[entry(q,v)]*1001,422)
    call(a,'PUT','/question-banks/'+lib['id'],{'revision':lib['revision'],'title':lib['title'],'entries':[entry(q,v)],'owner_id':q['id']},422)
    saved=put_bank(a,lib,[entry(q,v)])
    assert put_bank(a,lib,[entry(q,v)])==saved
    put_bank(a,lib,[],409)
    assert call(a,'GET','/question-banks/'+lib['id'])==saved


def test_T2_11_version_draft_commit_lost_then_seal_and_stale_guard(context,monkeypatch):
    app,a,db,q,v=setup(context)
    draft=call(a,'POST','/questions/'+q['id']+'/versions',{'creation_key':str(uuid4()),'operation':'derive','parent_version_id':v['id']},201)
    path=f"/questions/{q['id']}/versions/{draft['id']}/draft"
    body={'revision':draft['revision'],'payload':f.AGE_V2}
    original=Collection.find_one_and_update;fired=False
    def lost(self,*args,**kwargs):
        nonlocal fired
        result=original(self,*args,**kwargs)
        if self.database.name==db.name and self.name=='survey_question_versions' and not fired:
            fired=True;raise AutoReconnect('draft committed, reply lost before seal')
        return result
    monkeypatch.setattr(Collection,'find_one_and_update',lost)
    call(a,'PUT',path,body,503)
    saved=BSON.encode(db.survey_question_versions.find_one({'_id':ObjectId(draft['id'])}))
    restored=call(a,'PUT',path,body)
    assert restored['revision']==draft['revision']+1 and restored['payload']==f.AGE_V2
    assert BSON.encode(db.survey_question_versions.find_one({'_id':ObjectId(draft['id'])}))==saved
    sealed=call(a,'POST',f"/questions/{q['id']}/versions/{draft['id']}/seal",{'revision':restored['revision']})
    call(a,'PUT',path,body,409)
    assert call(a,'GET',f"/questions/{q['id']}/versions/{draft['id']}")==sealed
