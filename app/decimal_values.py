"""Exact decimal strings at the API boundary; Decimal128 only at persistence."""
from decimal import Decimal, DecimalException
import re
from copy import deepcopy
from bson.decimal128 import Decimal128


def decimal_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
        raise ValueError('数字需为有限十进制字符串')
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('数字必须有限')
        Decimal128(result)
    except DecimalException:
        raise ValueError('数字超出Decimal128范围或不能无损表示')
    return result


def decimal_string(value):
    if isinstance(value, Decimal128):
        value = value.to_decimal()
    if value == 0:
        return '0'
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    normalized = Decimal((sign, digits, exponent))
    return format(normalized, 'f') if -6 <= normalized.adjusted() <= 20 else str(normalized)


def canonical_decimal(value):
    return decimal_string(decimal_value(value))


def questions_to_bson(questions):
    result = deepcopy(questions)
    for q in result:
        if q['kind'] == 'number':
            for key in ['min_value', 'max_value']:
                if q['validation'][key] is not None:
                    q['validation'][key] = Decimal128(decimal_value(q['validation'][key]))
            for rule in q['rules']:
                rule['operand'] = Decimal128(decimal_value(rule['operand']))
    return result


def answer_to_bson(question, value):
    return Decimal128(decimal_value(value)) if question['kind'] == 'number' else value
