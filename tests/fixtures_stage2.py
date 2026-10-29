"""F2-N/V inputs and independent expected values from design 04 (no tested helpers)."""
from copy import deepcopy
from tests.fixtures_f02 import uid

A1, A2, B1, G0A, G1A, G0B = [uid(i) for i in range(20001, 20007)]
CONTINUE, SKIP = uid(20101), uid(20102)
AGE = {'kind': 'number', 'title': '你的年龄', 'required': False,
       'validation': {'min_value': '0', 'max_value': '120', 'integer_only': True}, 'options': []}
AGE_V2 = {**deepcopy(AGE), 'title': '年龄（扩展范围）',
          'validation': {'min_value': '0', 'max_value': '200', 'integer_only': True}}
ROWS = [
    ('a', 'u1', False, {G0A: CONTINUE, A1: '0', G1A: CONTINUE, A2: '18'}),
    ('a', 'u1', True, {G0A: CONTINUE, A1: '18', G1A: CONTINUE, A2: None}),
    ('a', 'u2', True, {G0A: CONTINUE, A1: '30', G1A: CONTINUE, A2: '60'}),
    ('a', 'u3', False, {G0A: CONTINUE, A1: None, G1A: SKIP}),
    ('b', 'u1', True, {G0B: CONTINUE, B1: '120'}),
    ('b', 'u2', False, {G0B: CONTINUE, B1: '18'}),
    ('b', 'u4', False, {G0B: SKIP}),
    ('b', 'u5', False, {G0B: CONTINUE, B1: None})]
EXPECTED = {'a': {'submissions': 4, 'people': 3, 'ages': [(A1, 3, '16.00'), (A2, 2, '39.00')]},
            'b': {'submissions': 4, 'people': 4, 'ages': [(B1, 2, '69.00')]}}


def gate(iid):
    return {'id': iid, 'kind': 'single', 'title': '继续或跳过', 'required': True,
        'validation': {}, 'options': [{'id': CONTINUE, 'text': '继续'}, {'id': SKIP, 'text': '跳过'}],
        'rules': [{'op': 'eq', 'operand': SKIP, 'target': 'END'}]}
