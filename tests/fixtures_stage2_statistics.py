"""F2-N/S/M/P inputs from design 04, with independent expected constants."""
from copy import deepcopy
from uuid import uuid4
from tests import fixtures_stage2 as f
from tests.fixtures_f02 import uid
from tests.test_stage2_catalog import account, call, setup, new_survey, imported, draft_body
from tests.test_stage2_management import share, grant, publish

OPTIONS = [uid(i) for i in range(21001, 21005)]
SINGLE = {'kind': 'single', 'title': 'F2-S单选', 'required': False, 'validation': {},
          'options': [{'id': oid, 'text': text} for oid, text in zip(OPTIONS, 'ABCD')]}
MULTIPLE = {'kind': 'multiple', 'title': 'F2-M多选', 'required': False,
            'validation': {'min_choices': 1, 'max_choices': 3},
            'options': [{'id': oid, 'text': text} for oid, text in zip(OPTIONS, 'abcd')]}
TEXT = {'kind': 'text', 'title': 'F2-P留言', 'required': False, 'validation': {}, 'options': []}
UNBOUNDED = {'kind': 'number', 'title': 'F2-E数值', 'required': False,
             'validation': {'min_value': None, 'max_value': None, 'integer_only': False}, 'options': []}
COUNTS = (2, 3, 7, 5, 2)
REVOKED_COUNTS = (1, 2, 5, 3, 2)
FREQUENCIES = [{'value': value, 'count': count} for value, count in [('0', 1), ('18', 3), ('30', 1), ('60', 1), ('120', 1)]]
EDGES = ['0', '18', '30', '60', '121']


def submit(c, s, answers, anonymous=False, status=201):
    return call(c, 'POST', '/fill/' + s['id'] + '/responses', {
        'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': anonymous, 'answers': answers}, status)


def dataset(context, kind='number', authorized=True):
    app, a, db, q, v = setup(context, {'number': f.AGE, 'single': SINGLE, 'multiple': MULTIPLE}[kind])
    b, c = account(app, '用户乙'), account(app, '用户丙')
    share(a, q, ['用户乙'])
    surveys = {}
    for key, actor, iids in [('a', a, [f.A1, f.A2]), ('b', b, [f.B1])]:
        s = new_survey(actor, 'F2-' + kind + '-' + key)
        for iid in iids:
            s = imported(actor, s, q, v, iid)
        questions = {x['id']: x for x in s['questions']}
        body = draft_body(s)
        body['questions'] = ([f.gate(f.G0A), questions[f.A1], f.gate(f.G1A), questions[f.A2]]
                            if key == 'a' else [f.gate(f.G0B), questions[f.B1]])
        s = call(actor, 'PUT', '/surveys/' + s['id'] + '/draft', body)
        if key == 'b' and authorized:
            s = grant(b, s, q, True)
        surveys[key] = publish(actor, s)
    fillers = {name: account(app, 'cross_' + name) for name in ['u1', 'u2', 'u3', 'u4', 'u5']}
    single = [[0, 1], [1, None], [0, 0], [None], [2], [1], [], [None]]
    multiple = [[[0, 1], [1]], [[2], None], [[0, 2], [0, 1, 2]], [None], [[1, 2]], [[2]], [], [None]]
    for index, (key, user, anonymous, initial) in enumerate(f.ROWS):
        answers = deepcopy(initial)
        if kind != 'number':
            values = (single if kind == 'single' else multiple)[index]
            for iid, value in zip([i for i in initial if i in {f.A1, f.A2, f.B1}], values):
                answers[iid] = None if value is None else OPTIONS[value] if kind == 'single' else [OPTIONS[x] for x in value]
        submit(fillers[user], surveys[key], answers, anonymous)
    return {'app': app, 'a': a, 'b': b, 'c': c, 'db': db, 'q': q, 'v': v, 'surveys': surveys, 'fillers': fillers}
