from decimal import ROUND_CEILING, ROUND_FLOOR
from app.config import LIMITS
from app.decimal_values import canonical_decimal, decimal_value


class Problem(Exception):
    def __init__(self, status, code, message, fields=None):
        self.status, self.code, self.message = status, code, message
        self.fields = fields or []


def publish_issues(survey):
    issues = []
    def add(message, qid=None, field='title'):
        issues.append({'question_id': qid, 'field': field, 'message': message})
    if not survey['title'].strip():
        add('标题不能为空')
    if not survey['questions']:
        add('至少添加一道题目', field='questions')
    positions = {q['id']: i for i, q in enumerate(survey['questions'])}
    for index, q in enumerate(survey['questions']):
        qid, kind, validation = q['id'], q['kind'], q['validation']
        if not q['title'].strip():
            add('题干不能为空', qid)
        if kind in {'single', 'multiple'}:
            texts = [o['text'].strip() for o in q['options']]
            if len(texts) < 2 or any(not t for t in texts):
                add('选择题至少需要两个非空选项', qid, 'options')
            if len(set(texts)) != len(texts):
                add('选项文字不能重复', qid, 'options')
        elif q['options']:
            add('填空题不能残留选项', qid, 'options')
        if kind in {'multiple', 'text'}:
            lower_key, upper_key = ('min_choices', 'max_choices') if kind == 'multiple' else ('min_length', 'max_length')
            lower, upper = validation.get(lower_key), validation.get(upper_key)
            available = len(q['options']) if kind == 'multiple' else LIMITS['text_answer']
            if lower is not None and upper is not None and lower > upper:
                add('下限不能超过上限', qid, 'validation')
            if (lower is not None and lower > available) or (upper is not None and upper > available):
                add('约束不能超过选项数量或工程上限', qid, 'validation')
            if upper == 0:
                add('题目须允许至少一种非空有效回答', qid, 'validation')
        if kind == 'number':
            lower = decimal_value(validation['min_value']) if validation.get('min_value') is not None else None
            upper = decimal_value(validation['max_value']) if validation.get('max_value') is not None else None
            if lower is not None and upper is not None:
                if lower > upper:
                    add('下限不能超过上限', qid, 'validation')
                elif validation.get('integer_only') and lower.to_integral_value(rounding=ROUND_CEILING) > upper.to_integral_value(rounding=ROUND_FLOOR):
                    add('此范围没有可填写的整数', qid, 'validation')
        options = {o['id'] for o in q['options']}
        operators = {'single': {'eq'}, 'multiple': {'any', 'all', 'exact'},
                     'number': {'eq', 'ne', 'gt', 'gte', 'lt', 'lte'}, 'text': set()}
        for rule in q['rules']:
            if rule['op'] not in operators[kind]:
                add('规则操作符不适用于本题型', qid, 'rules')
            if kind == 'single' and rule['operand'] not in options:
                add('规则选项不存在', qid, 'rules')
            if kind == 'multiple':
                operand = rule['operand']
                if not operand or len(set(operand)) != len(operand) or not set(operand) <= options:
                    add('多选规则至少指定一个选项，且选项不能重复或不存在', qid, 'rules')
            target = rule['target']
            if target != 'END' and (target not in positions or positions[target] <= index):
                add('跳转目标须为后续题或结束', qid, 'rules')
    return issues


def normalize_answer(question, value):
    kind = question['kind']
    def invalid(message):
        raise Problem(422, 'INVALID_ANSWER_STRUCTURE', message,
                      [{'question_id': question['id'], 'field': 'answer', 'message': message}])
    if value is None:
        return None
    if kind == 'multiple':
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            invalid('多选答案必须是选项ID数组')
        if len(value) != len(set(value)):
            invalid('多选选项不能重复')
        allowed = [o['id'] for o in question['options']]
        if not set(value) <= set(allowed):
            invalid('选项不存在')
        return [option for option in allowed if option in value] or None
    if not isinstance(value, str):
        invalid('本题答案必须为字符串或空值')
    if kind == 'text':
        value = value.strip()
        if len(value) > LIMITS['text_answer']:
            invalid('文本答案不能超过10000个字符')
        return value or None
    if not value.strip():
        return None
    if kind == 'single':
        if value not in {o['id'] for o in question['options']}:
            invalid('选项不存在')
        return value
    try:
        return canonical_decimal(value)
    except ValueError as exc:
        invalid(str(exc))


def validate_answer(question, value):
    if value is None:
        return ('请选择一个选项' if question['kind'] == 'single' else '本题必须回答') if question['required'] else None
    validation, kind = question['validation'], question['kind']
    if kind in {'multiple', 'text'}:
        lower, upper = ('min_choices', 'max_choices') if kind == 'multiple' else ('min_length', 'max_length')
        size = len(value)
        if validation.get(lower) is not None and size < validation[lower]:
            return '回答不足下限' if kind == 'text' else '选择数量不足下限'
        if validation.get(upper) is not None and size > validation[upper]:
            return '回答超过上限' if kind == 'text' else '选择数量超过上限'
    if kind == 'number':
        number = decimal_value(value)
        if validation.get('integer_only') and number != number.to_integral_value():
            return '请输入整数'
        if validation.get('min_value') is not None and number < decimal_value(validation['min_value']):
            return '数字小于最小值'
        if validation.get('max_value') is not None and number > decimal_value(validation['max_value']):
            return '数字大于最大值'
    return None


def matches_rule(kind, value, rule):
    if value is None:
        return False
    op, operand = rule['op'], rule['operand']
    if kind == 'single':
        return value == operand
    if kind == 'multiple':
        actual, expected = set(value), set(operand)
        if op == 'any':
            return bool(actual & expected)
        if op == 'all':
            return expected <= actual
        return actual == expected
    actual, expected = decimal_value(value), decimal_value(operand)
    if op == 'eq': return actual == expected
    if op == 'ne': return actual != expected
    if op == 'gt': return actual > expected
    if op == 'gte': return actual >= expected
    if op == 'lt': return actual < expected
    return actual <= expected


def next_target(questions, index, value):
    q = questions[index]
    if value is not None:
        for rule in q['rules']:
            if matches_rule(q['kind'], value, rule):
                return rule['target']
    return questions[index + 1]['id'] if index + 1 < len(questions) else 'END'


def replay(questions, answers):
    known = {q['id']: q for q in questions}
    if set(answers) - set(known):
        raise Problem(422, 'UNKNOWN_QUESTION', '答案含未知题目')
    normalized = {qid: normalize_answer(known[qid], value) for qid, value in answers.items()}
    positions = {q['id']: i for i, q in enumerate(questions)}
    active, path, errors, removed = {}, [], [], set()
    current, index = None, 0
    while index < len(questions):
        q = questions[index]
        qid = q['id']
        path.append(qid)
        current = qid
        if qid not in normalized:
            break
        value = normalized[qid]
        message = validate_answer(q, value)
        if message:
            errors.append({'question_id': qid, 'field': 'answer', 'message': message})
            break
        active[qid] = value
        target = next_target(questions, index, value)
        destination = len(questions) if target == 'END' else positions[target]
        if destination <= index:
            raise Problem(422, 'INVALID_CONFIGURATION', '问卷含向前跳转')
        removed.update(q['id'] for q in questions[index + 1:destination] if q['id'] in normalized)
        index = destination
        current = None
    pending = {qid: value for qid, value in normalized.items() if qid not in path and qid not in removed}
    return {'path': path, 'active_answers': active, 'pending_answers': pending,
            'removed_question_ids': [q['id'] for q in questions if q['id'] in removed],
            'current_question_id': current, 'ready_to_submit': current is None, 'errors': errors}
