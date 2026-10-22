"""Independent acceptance checks; no imports of application rules or statistics."""
import ast
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from bson import ObjectId
from bson.decimal128 import Decimal128

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = ['tests/test_p2.py','tests/test_w007.py','tests/test_w008.py','tests/test_p4.py']
MANUAL_CHECKS = {'original_materials','requirements_and_oracles','implementation_and_dependencies','test_integrity','ai_log'}
BROWSER_CHECKS = {'unicode_password_register_login','own_list_pagination_desktop_mobile',
                  'anonymous_choice_survives_backtracking','page_console_health'}
GROUPS = {'T-%02d' % n for n in range(1,14)}
INDEPENDENT_CHECKS = MANUAL_CHECKS | {'raw_database_oracle','p4_browser','verifier_counterexamples'}


def test_functions():
    return {file+'::'+node.name for file in TEST_FILES
            for node in ast.parse((ROOT/file).read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}


def validate_mapping(mapping, manual):
    canonical = json.loads((ROOT/'tests/requirements-map-p4.json').read_text())
    if mapping != canonical:
        raise ValueError('P4 mapping must match the fixed canonical acceptance set')
    if set(mapping.get('independent_checks',[])) != INDEPENDENT_CHECKS:
        raise ValueError('P4 independent checks missing')
    mapped = {test for item in mapping['requirements'].values() for test in item.get('tests',[])}
    if mapped != test_functions():
        raise ValueError('P4 every canonical test must be mapped by file and function')
    review = manual.get('p4',{})
    if set(review.get('checks',{})) != MANUAL_CHECKS or set(review.get('groups',{})) != GROUPS:
        raise ValueError('P4 independent review evidence missing')
    for record in [*review['checks'].values(), *review['groups'].values()]:
        for key in ['steps','inputs','expected','observed','sources']:
            if not record.get(key): raise ValueError('P4 review missing '+key)
        if record.get('result') != 'reviewed': raise ValueError('P4 semantic review not complete')
        for source in record['sources']:
            if not (ROOT/source).is_file(): raise ValueError('P4 review source missing: '+source)
    if 'W-009' not in (ROOT/'开发文档/04-AI记录与提交文档.md').read_text():
        raise ValueError('P4 work package log missing')


def collection_fingerprints(db):
    return {name:{'count':db[name].count_documents({}),
                  'sha256':hashlib.sha256(repr(list(db[name].find().sort('_id',1))).encode()).hexdigest()}
            for name in ['survey_users','survey_surveys','survey_responses','survey_sessions']}


def raw_database_oracle(db, output):
    f01 = json.loads((output/'browser-w008.json').read_text())['summary']
    f02 = json.loads((output/'browser-w007.json').read_text())['summary']
    p4 = json.loads((output/'browser-p4.json').read_text())['observations']['anonymous_choice_survives_backtracking']
    users = {d['_id'] for d in db.survey_users.find()}
    surveys = {d['_id']:d for d in db.survey_surveys.find()}
    all_docs = list(db.survey_responses.find().sort('_id',1))
    assert len(all_docs) == 10
    for survey in surveys.values():
        assert survey['owner_id'] in users and survey['schema_version'] == 1
    for doc in all_docs:
        survey = surveys[doc['survey_id']]
        assert doc['respondent_id'] in users
        assert doc['schema_version'] == doc['content_version'] == survey['content_version'] == 1
        assert doc['repeat_allowed'] == survey['allow_repeat']
        qids = {q['id'] for q in survey['questions']}
        assert set(doc['path']) <= qids and len(doc['path']) == len(set(doc['path']))
        assert {a['question_id'] for a in doc['answers']} <= set(doc['path'])
        assert len(doc['answers']) == len({a['question_id'] for a in doc['answers']})
        assert all(a['value'] is not None for a in doc['answers'])
    docs = sorted([d for d in all_docs if str(d['survey_id']) == f01['survey_id']],key=lambda d:(d['created_at'],d['_id']))
    questions = json.loads((ROOT/'tests/fixtures-f01.json').read_text())
    q1,q2,q3,q4 = [q['id'] for q in questions]
    assert f01['question_ids'] == [q1,q2,q3,q4]
    a,b,c = [o['id'] for o in questions[0]['options']]
    apple,banana,grape = [o['id'] for o in questions[1]['options']]
    expected = [{q1:a,q2:[apple,banana],q3:'甲',q4:Decimal128('0')},
                {q1:b,q2:[banana],q3:'乙',q4:Decimal128('10')},
                {q1:a,q2:[apple],q3:'丙',q4:Decimal128('20')},{},{q1:c}]
    actual = [{v['question_id']:v['value'] for v in d['answers']} for d in docs]
    assert actual == expected
    assert [d['path'] for d in docs] == [[q1,q2,q3,q4]]*5
    assert [d['anonymous'] for d in docs] == [False,True,True,False,False]
    respondents = [d['respondent_id'] for d in docs]
    assert len(set(respondents)) == 3 and respondents[0] == respondents[1] and respondents[2] == respondents[4]
    counts = []
    for option in [a,b,c]:
        matches = [d for d,answers in zip(docs,actual) if answers.get(q1) == option]
        counts.append([len(matches),len({d['respondent_id'] for d in matches})])
    multiple = Counter(o for answers in actual for o in answers.get(q2,[]))
    numbers = [answers[q4].to_decimal() for answers in actual if q4 in answers]
    assert counts == [[2,2],[1,1],[1,1]]
    assert [multiple[o] for o in [apple,banana,grape]] == [2,2,0]
    assert sum(numbers)/len(numbers) == Decimal(10)
    assert [v[q3] for v in reversed(actual) if q3 in v] == ['丙','乙','甲']
    j1,j2,j3,j4,j5 = f02['question_ids']; options = f02['option_ids']
    branches = sorted([d for d in all_docs if str(d['survey_id']) == f02['survey_id']],key=lambda d:(d['created_at'],d['_id']))
    assert [d['path'] for d in branches] == [[j1,j3,j5],[j1,j2,j4,j5]]
    assert [{a['question_id']:a['value'] for a in d['answers']} for d in branches] == [
        {j1:options['A'],j3:Decimal128('18')},
        {j1:options['B'],j2:[options['X'],options['Y']],j4:'完成'}]
    anonymous = [d for d in all_docs if str(d['survey_id']) == p4['survey_id']]
    assert len(anonymous) == 1 and anonymous[0]['anonymous'] is True
    assert anonymous[0]['answers'] == [{'question_id':p4['question_id'],'value':'修改后'}]
    return {'result':'passed','all_responses':len(all_docs),'all_relationships_and_versions':'passed',
            'F01':{'responses':5,'respondents':3,'single_option_times_people':counts,
                   'multiple_counts':[multiple[o] for o in [apple,banana,grape]],'text_latest_first':['丙','乙','甲'],
                   'numbers':[str(n) for n in numbers],'average':'10.00','anonymous':2,'empty':1,
                   'all_five_paths_and_answers_exact':'passed'},
            'F02':{'paths':[[j1,j3,j5],[j1,j2,j4,j5]],'nonempty_answers':[2,3],'exact_answers':'passed'},
            'P4':{'anonymous_backtrack':True,'stored_text':'修改后'},
            'collections':collection_fingerprints(db)}


def evidence_links(mapping, executed, manual, fingerprints):
    result = {}
    for req,item in mapping['requirements'].items():
        cases = sorted(n for n in executed if n.split('[',1)[0] in item.get('tests',[]))
        result[req] = {'result':'passed','scope':item['scope'],
                       'sources':{p:fingerprints.get(p) or hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in item['sources']},
                       'executed_cases':cases,'browser_checks':item.get('browser_checks',[]),
                       'manual_checks':{k:manual['p4']['checks'][k] for k in item.get('manual_checks',[])}}
    return result
