"""II-W005: independent F2 oracles against real MongoDB and HTTP boundaries."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from itertools import permutations
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
import json
import os

import pytest
from bson import BSON, ObjectId, Decimal128
from pymongo.collection import Collection
from pymongo.errors import AutoReconnect
from app import question_statistics as cross
from tests import fixtures_stage2 as f
from tests import fixtures_stage2_statistics as sample
from tests.test_stage2_catalog import account, call, setup, imported, new_survey
from tests.test_stage2_management import share, grant, publish, new_version, snapshot, bank, entry, put_bank

GROUP_KEYS = {'version_id', 'version_no', 'kind', 'title', 'included_survey_count',
              'included_instance_count', 'answer_count', 'answered_submission_count', 'respondent_count'}


def path(d, endpoint='statistics'):
    return '/questions/' + d['q']['id'] + ('/statistics' if endpoint == 'statistics' else
        '/versions/' + d['v']['id'] + '/distribution')


def counts(g):
    return tuple(g[k] for k in ['included_survey_count', 'included_instance_count',
                               'answer_count', 'answered_submission_count', 'respondent_count'])


def stats(d, actor='a', suffix=''):
    return call(d[actor], 'GET', path(d) + suffix)


def dist(d, actor='a', **params):
    return call(d[actor], 'GET', path(d, 'distribution') + '?' + urlencode(params))


def evidence(name, value):
    if os.getenv('SURVEY_CROSS_EVIDENCE'):
        target = Path(os.environ['SURVEY_CROSS_EVIDENCE']);target.mkdir(parents=True, exist_ok=True)
        (target / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@pytest.mark.parametrize('kind', ['number', 'single', 'multiple'])
def test_T2_08_fixed_f2n_s_m_authorized_and_revoked(context, kind):
    d = sample.dataset(context, kind)
    before = snapshot(d['db'], ['survey_responses', 'survey_question_versions'])
    legacy = {key: call(d[key], 'GET', '/surveys/' + s['id'] + '/statistics') for key, s in d['surveys'].items()}
    result = stats(d)
    assert set(result) == {'question_id', 'scope', 'groups'}
    assert result['scope'] == 'owned_and_granted' and len(result['groups']) == 1
    g = result['groups'][0]
    assert counts(g) == sample.COUNTS
    assert set(g) == GROUP_KEYS | ({'average', 'average_display'} if kind == 'number' else {'options'})
    if kind == 'number':
        assert g['average_display'] == '37.71'
        values = [a['value'].to_decimal() for r in d['db'].survey_responses.find() for a in r['answers']
                  if isinstance(a['value'], Decimal128)]
        assert len(values) == 7 and sum(values) == 264
        assert dist(d)['items'] == sample.FREQUENCIES
        buckets = dist(d, mode='buckets', boundaries=json.dumps(sample.EDGES))
        assert [x['count'] for x in buckets['buckets']] == [1, 3, 1, 2]
        assert (buckets['underflow'], buckets['overflow']) == (0, 0)
    else:
        assert [x['selection_count'] for x in g['options']] == ([3, 3, 1, 0] if kind == 'single' else [3, 4, 5, 0])
        for o in g['options']:
            assert set(o) == {'option_id', 'text', 'selection_count'} | ({'respondent_count'} if kind == 'single' else set())
        if kind == 'single':
            assert [x['respondent_count'] for x in g['options']] == [2, 2, 1, 0]
        else:
            for bad in [[sample.OPTIONS[0]] * 2, [str(uuid4())]]:
                sample.submit(d['fillers']['u1'], d['surveys']['b'], {f.G0B: f.CONTINUE, f.B1: bad}, status=422)
    assert stats(d, 'b')['scope'] == 'owned_only'
    assert counts(stats(d, 'b')['groups'][0]) == (1, 1, 2, 2, 2)
    for endpoint in ['statistics', 'distribution']:
        call(d['c'], 'GET', path(d, endpoint), status=404)
    d['surveys']['b'] = grant(d['b'], d['surveys']['b'], d['q'], False)
    reduced = stats(d)['groups'][0]
    assert counts(reduced) == sample.REVOKED_COUNTS
    if kind == 'number':
        assert reduced['average_display'] == '25.20'
        assert [x['count'] for x in dist(d, mode='buckets', boundaries=json.dumps(sample.EDGES))['buckets']] == [1, 2, 1, 1]
    else:
        assert [x['selection_count'] for x in reduced['options']] == ([3, 2, 0, 0] if kind == 'single' else [3, 3, 3, 0])
        if kind == 'single':
            assert [x['respondent_count'] for x in reduced['options']] == [2, 1, 0, 0]
    assert call(d['a'], 'GET', '/questions/' + d['q']['id'] + '/usages')['total'] == 2
    assert snapshot(d['db'], before) == before
    for key, s in d['surveys'].items():
        assert call(d[key], 'GET', '/surveys/' + s['id'] + '/statistics') == legacy[key]
    evidence('fixed-f2-' + kind, {'expected_counts': sample.COUNTS, 'actual': result, 'after_revoke': reduced,
                                 'surveys': {k: s['id'] for k, s in d['surveys'].items()}})


def test_T2_09_f2v_exact_version_not_title_hash_or_instance_uuid(context):
    d = sample.dataset(context)
    a, c, db, q, v = [d[k] for k in ['a', 'c', 'db', 'q', 'v']]
    frozen = snapshot(db, ['survey_surveys', 'survey_responses'])
    old = deepcopy(stats(d))
    v2 = new_version(a, q, v, value=f.AGE_V2)
    v3 = new_version(a, q, v, operation='restore')
    assert v3['payload_hash'] == v['payload_hash'] and v3['id'] != v['id']
    for ver, value in [(v2, '40'), (v3, '60')]:
        s = publish(a, imported(a, new_survey(a), q, ver, f.A1))
        sample.submit(c, s, {f.A1: value})
    other = call(a, 'POST', '/questions', {'label': q['label'], 'creation_key': str(uuid4())}, 201)
    ov = call(a, 'POST', '/questions/' + other['id'] + '/versions', {'creation_key': str(uuid4()), 'payload': f.AGE}, 201)
    ov = call(a, 'POST', f"/questions/{other['id']}/versions/{ov['id']}/seal", {'revision': 1})
    same_name = publish(a, imported(a, new_survey(a), other, ov, f.A1))
    sample.submit(c, same_name, {f.A1: '99'})
    zero = new_version(a, q, v)
    draft = call(a, 'POST', '/questions/' + q['id'] + '/versions', {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    groups = stats(d, suffix='?versions=all')['groups']
    assert [x['version_id'] for x in groups] == [v['id'], v2['id'], v3['id'], zero['id']]
    assert [x['average_display'] for x in groups] == ['37.71', '40.00', '60.00', '暂无数据']
    assert counts(groups[-1]) == (0, 0, 0, 0, 0) and groups[-1]['average'] is None
    assert stats(d, suffix='?version_id=' + v['id']) == old
    for name, rows in frozen.items():
        for encoded in rows:
            doc = BSON(encoded).decode()
            assert BSON.encode(db[name].find_one({'_id': doc['_id']})) == encoded
    call(a, 'GET', path(d) + '?version_id=' + ov['id'], status=404)
    call(a, 'GET', path(d) + '?version_id=' + draft['id'], status=404)
    evidence('fixed-f2-versions', {'groups': groups, 'other_family_id': other['id'], 'other_version_id': ov['id']})


def test_T2_08_f2p_text_counts_and_old_owner_only(context):
    app, a, db, q, v = setup(context, sample.TEXT)
    b, c = account(app, '用户乙'), account(app, '用户丙')
    share(a, q, ['用户乙'])
    s = publish(b, grant(b, imported(b, new_survey(b), q, v, f.B1), q, True))
    for text in ['甲', '乙']:
        sample.submit(c, s, {f.B1: text}, anonymous=True)
    d = {'a': a, 'q': q, 'v': v}
    result = stats(d);g = result['groups'][0]
    assert counts(g) == (1, 1, 2, 2, 1) and set(g) == GROUP_KEYS
    assert set(result) == {'question_id', 'scope', 'groups'}
    assert {x['value'] for x in call(b, 'GET', f"/surveys/{s['id']}/questions/{f.B1}/contents")['items']} == {'甲', '乙'}
    for suffix in ['', '/statistics', '/questions/' + f.B1 + '/statistics', '/questions/' + f.B1 + '/contents']:
        call(a, 'GET', '/surveys/' + s['id'] + suffix, status=404)
    call(a, 'GET', path(d, 'distribution'), status=422)
    assert a.get('/api/v1' + path(d)).headers['cache-control'] == 'no-store'
    evidence('fixed-f2-text', result)


def test_T2_09_usage_revoked_access_only_saved_versions_and_grant_independence(context):
    d = sample.dataset(context)
    a, b, q, v = [d[k] for k in ['a', 'b', 'q', 'v']]
    hidden = new_version(a, q, v)
    draft_only = new_version(a, q, v)
    imported(b, new_survey(b), q, draft_only, str(uuid4()))
    lib = put_bank(b, bank(b), [entry(q, v)])
    share(a, q, [])
    assert counts(stats(d)['groups'][0]) == sample.COUNTS
    assert [g['version_id'] for g in stats(d, 'b')['groups']] == [v['id'], draft_only['id']]
    assert counts(stats(d, 'b')['groups'][1]) == (0, 0, 0, 0, 0)
    call(b, 'GET', path(d) + '?version_id=' + hidden['id'], status=404)
    call(b, 'GET', f"/questions/{q['id']}/versions/{hidden['id']}/distribution", status=404)
    call(b, 'GET', '/questions/' + q['id'] + '/versions', status=404)
    assert dist(d, 'b')['average_display'] == '69.00'
    put_bank(b, lib, [])
    assert counts(stats(d)['groups'][0]) == sample.COUNTS
    grant(b, d['surveys']['b'], q, False)
    assert counts(stats(d)['groups'][0]) == sample.REVOKED_COUNTS
    assert dist(d, 'b')['average_display'] == '69.00'


def test_T2_08_scope_before_responses_draft_closed_expired_default_off(context, monkeypatch):
    d = sample.dataset(context, authorized=False)
    a, b, db, q, v = [d[k] for k in ['a', 'b', 'db', 'q', 'v']]
    draft = imported(b, new_survey(b), q, v, str(uuid4()))
    grant(b, draft, q, True)
    own = d['surveys']['a'];db.survey_surveys.update_one({'_id': ObjectId(own['id'])}, {'$set': {'deadline_at': d['app'].state.clock() - timedelta(days=1)}})
    original = Collection.find;read_ids = []
    def watch(collection, query=None, *args, **kwargs):
        if collection.name == 'survey_responses':
            read_ids.extend(query['survey_id']['$in'])
        return original(collection, query, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Collection, 'find', watch)
        assert counts(stats(d)['groups'][0]) == sample.REVOKED_COUNTS
    assert set(read_ids) == {ObjectId(own['id'])}
    closed = call(a, 'POST', '/surveys/' + own['id'] + '/close', {'revision': own['revision']})
    assert closed['state'] == 'closed' and counts(stats(d)['groups'][0]) == sample.REVOKED_COUNTS
    assert call(a, 'GET', '/questions/' + q['id'] + '/usages')['total'] == 3


def test_T2_09_f2e_empty_zero_explicit_edges_and_paging(context):
    app, a, db, q, v = setup(context, sample.UNBOUNDED)
    s = publish(a, imported(a, new_survey(a), q, v, f.A1))
    d = {'a': a, 'q': q, 'v': v}
    empty = stats(d)['groups'][0]
    assert counts(empty) == (1, 1, 0, 0, 0) and empty['average'] is None
    assert dist(d)['items'] == []
    sample.submit(a, s, {f.A1: '0'})
    assert counts(stats(d)['groups'][0]) == (1, 1, 1, 1, 1)
    assert stats(d)['groups'][0]['average_display'] == '0.00'
    for value in ['-1', '18', '30', '60', '121']:
        sample.submit(a, s, {f.A1: value})
    buckets = dist(d, mode='buckets', boundaries=json.dumps(sample.EDGES))
    assert [x['count'] for x in buckets['buckets']] == [1, 1, 1, 1]
    assert (buckets['underflow'], buckets['overflow']) == (1, 1)
    sample.submit(a, s, {f.A1: '18.0'})
    all_values = dist(d, limit=100)
    pages = [dist(d, limit=2, offset=n) for n in [0, 2, 4, 6, 100]]
    assert [x for p in pages for x in p['items']] == all_values['items']
    stable = GROUP_KEYS | {'average', 'average_display', 'question_id', 'scope', 'mode', 'total'}
    assert all({k: p[k] for k in stable} == {k: all_values[k] for k in stable} for p in pages)
    assert all_values['items'][2] == {'value': '18', 'count': 2}
    assert set(all_values) == stable | {'items', 'offset', 'limit'}
    assert all(set(x) == {'value', 'count'} for x in all_values['items'])
    assert all(set(x) == {'lower', 'upper', 'count'} for x in buckets['buckets'])
    evidence('fixed-f2-edges', {'empty': empty, 'buckets': buckets, 'frequencies': all_values})


@pytest.mark.parametrize('raw', ['null', '{}', '"0,18"', '[0,18]', '[]', '["0"]', '["0","0"]',
    '["1","0"]', '["NaN","1"]', '["0","Infinity"]', '["0","1E6145"]',
    '["0","0.12345678901234567890123456789012345"]', '["0","0.0"]',
    json.dumps([str(i) for i in range(102)])])
def test_T2_09_invalid_boundaries(context, raw):
    app, a, db, q, v = setup(context)
    d = {'a': a, 'q': q, 'v': v}
    call(a, 'GET', path(d, 'distribution') + '?' + urlencode({'mode': 'buckets', 'boundaries': raw}), status=422)


@pytest.mark.parametrize('endpoint,params', [
    ('statistics', 'versions=all&version_id=000000000000000000000001'),
    ('statistics', 'versions=any'), ('statistics', 'survey_ids=000000000000000000000001'),
    ('statistics', 'versions=all&versions=all'), ('distribution', 'mode=unknown'),
    ('distribution', 'offset=-1'), ('distribution', 'limit=101'),
    ('distribution', 'survey_id=000000000000000000000001'), ('distribution', 'mode=buckets'),
    ('distribution', 'mode=exact&boundaries=%5B%220%22,%221%22%5D'),
    ('distribution', 'mode=buckets&offset=0&boundaries=%5B%220%22,%221%22%5D')])
def test_T2_09_strict_query_no_client_scope_expansion(context, endpoint, params):
    app, a, db, q, v = setup(context)
    call(a, 'GET', path({'q': q, 'v': v}, endpoint) + '?' + params, status=422)


@pytest.mark.parametrize('values,expected', [(list(p), '0.33') for p in permutations(['1E34', '1', '-1E34'])] + [
    (['1.005', '1.004999999999999999999999999999999'], '1.00'),
    (['1.004999999999999999999999999999999', '1.005'], '1.00'),
    (['-1.005', '-1.004999999999999999999999999999999'], '-1.00'),
    (['-1.004999999999999999999999999999999', '-1.005'], '-1.00')])
def test_T2_09_original_cancellation_and_double_rounding_across_surveys(context, values, expected):
    app, a, db, q, v = setup(context, sample.UNBOUNDED)
    d = {'a': a, 'q': q, 'v': v}
    for value in values:
        s = publish(a, imported(a, new_survey(a), q, v, f.A1))
        sample.submit(a, s, {f.A1: value})
    g = stats(d)['groups'][0]
    assert g['average_display'] == expected and dist(d)['average_display'] == expected
    assert counts(g) == (len(values), len(values), len(values), len(values), 1)
    with localcontext() as ctx:
        ctx.prec = 100
        exact = sum(Decimal(x) for x in values) / len(values)
        assert abs(Decimal(g['average']) - exact) < Decimal('1E-32')


@pytest.mark.parametrize('endpoint', ['statistics', 'distribution'])
@pytest.mark.parametrize('moment', ['before_scope', 'after_scope', 'after_read', 'after_check', 'other_revision'])
def test_T2_11_grant_revocation_admission_real_reads(context, monkeypatch, endpoint, moment):
    d = sample.dataset(context)
    original = getattr(cross, 'scope' if moment in {'before_scope', 'after_scope'} else
                       'verify_grants' if moment == 'after_check' else 'collect')
    def intercepted(*args, **kwargs):
        def change():
            if moment == 'other_revision':
                call(d['b'], 'PATCH', '/surveys/' + d['surveys']['b']['id'] + '/deadline',
                     {'revision': d['surveys']['b']['revision'], 'deadline_at': None})
            else:
                grant(d['b'], d['surveys']['b'], d['q'], False)
        if moment == 'before_scope': change()
        value = original(*args, **kwargs)
        if moment != 'before_scope': change()
        return value
    function = 'scope' if moment in {'before_scope', 'after_scope'} else 'verify_grants' if moment == 'after_check' else 'collect'
    with monkeypatch.context() as m:
        m.setattr(cross, function, intercepted)
        result = call(d['a'], 'GET', path(d, endpoint), status=409 if moment in {'after_scope', 'after_read', 'other_revision'} else 200)
    if moment in {'after_scope', 'after_read', 'other_revision'}:
        assert result['error']['code'] == 'STATISTICS_SCOPE_CHANGED' and set(result) == {'error'}
    else:
        g = result['groups'][0] if endpoint == 'statistics' else result
        assert counts(g) == (sample.REVOKED_COUNTS if moment == 'before_scope' else sample.COUNTS)
    assert counts(stats(d)['groups'][0]) == (sample.COUNTS if moment == 'other_revision' else sample.REVOKED_COUNTS)
    evidence('admission-' + endpoint + '-' + moment, result)


@pytest.mark.parametrize('endpoint', ['statistics', 'distribution'])
def test_T2_11_real_database_read_failure_no_success(context, monkeypatch, endpoint):
    d = sample.dataset(context);original = Collection.find
    def fail(collection, *args, **kwargs):
        cursor = original(collection, *args, **kwargs)
        if collection.name == 'survey_responses':
            assert next(cursor)['survey_id'] in {ObjectId(s['id']) for s in d['surveys'].values()}
            raise AutoReconnect('II-W005 deliberate failure after a real read')
        return cursor
    with monkeypatch.context() as m:
        m.setattr(Collection, 'find', fail)
        result = call(d['a'], 'GET', path(d, endpoint), status=503)
    assert set(result) == {'error'} and result['error']['code'] == 'DATABASE_UNAVAILABLE'
    assert counts(stats(d)['groups'][0]) == sample.COUNTS
    evidence('injected-read-failure-' + endpoint, result)


def test_T2_08_all_surveys_beyond_one_hundred_and_instance_mapping(context):
    app, a, db, q, v = setup(context)
    for index in range(105):
        s = publish(a, imported(a, new_survey(a), q, v, f.A1))
        sample.submit(a, s, {f.A1: '1'})
    d = {'a': a, 'q': q, 'v': v}
    g = stats(d)['groups'][0]
    assert counts(g) == (105, 105, 105, 105, 1) and g['average_display'] == '1.00'
    assert dist(d, limit=1)['items'] == [{'value': '1', 'count': 105}]


def test_T2_09_unvisited_and_empty_values_excluded_but_zero_kept(context):
    d = sample.dataset(context)
    # Storage fault probe: a skipped instance is not admitted by path membership.
    row = d['db'].survey_responses.find_one({'survey_id': ObjectId(d['surveys']['b']['id']), 'path': [f.G0B]})
    d['db'].survey_responses.update_one({'_id': row['_id']}, {'$push': {'answers': {'question_id': f.B1, 'value': Decimal128('99')}}})
    assert counts(stats(d)['groups'][0]) == sample.COUNTS
    assert dist(d)['items'] == sample.FREQUENCIES


def test_T2_08_grant_same_element_not_cross_family(context):
    d = sample.dataset(context, authorized=False)
    s = d['surveys']['b'];db = d['db']
    # Deliberately valid-shaped foreign grant must not authorize this family's answers.
    db.survey_surveys.update_one({'_id': ObjectId(s['id'])}, {'$push': {'question_result_grants': {
        'question_id': ObjectId(), 'enabled': True, 'allowed_at_publish': True}}})
    assert counts(stats(d)['groups'][0]) == sample.REVOKED_COUNTS


@pytest.mark.parametrize('payload', [sample.SINGLE, sample.MULTIPLE])
def test_T2_09_choice_distribution_not_a_number(context, payload):
    app, a, db, q, v = setup(context, payload)
    call(a, 'GET', path({'q': q, 'v': v}, 'distribution'), status=422)


@pytest.mark.parametrize('endpoint', ['statistics', 'distribution'])
def test_T2_11_final_grant_check_database_failure(context, monkeypatch, endpoint):
    d = sample.dataset(context);original = Collection.find
    def fail(collection, *args, **kwargs):
        cursor = original(collection, *args, **kwargs)
        if collection.name == 'survey_surveys' and len(args) > 1 and args[1] == {'revision': 1}:
            assert next(cursor)['revision'] == d['surveys']['b']['revision']
            raise AutoReconnect('II-W005 failure after real final authorization read')
        return cursor
    with monkeypatch.context() as m:
        m.setattr(Collection, 'find', fail)
        result = call(d['a'], 'GET', path(d, endpoint), status=503)
    assert set(result) == {'error'} and result['error']['code'] == 'DATABASE_UNAVAILABLE'
    assert counts(stats(d)['groups'][0]) == sample.COUNTS
    evidence('injected-final-check-failure-' + endpoint, result)
