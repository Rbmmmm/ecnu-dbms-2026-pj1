"""Permission-scoped, exact-version aggregates; never expose individual answers."""
from collections import Counter
from decimal import Decimal, localcontext
from bisect import bisect_right
import json

from bson import Decimal128
from bson.decimal128 import create_decimal128_context
from app.decimal_values import decimal_value, decimal_string
from app.domain import Problem
from app.statistics import average_display


def scope(db, qid, uid, vid=None):
    family = db.survey_questions.find_one({'_id': qid})
    if family is None:
        raise Problem(404, 'NOT_FOUND', '题目不存在')
    owner = family['owner_id'] == uid
    visible = owner or uid in family['shared_with']
    source = {'source.question_id': qid}
    if vid is not None:
        source['source.question_version_id'] = vid
    reference_query = {'questions': {'$elemMatch': source}}
    own_refs = list(db.survey_surveys.find({**reference_query, 'owner_id': uid}, {'questions.source': 1}))
    referenced_versions = {q['source']['question_version_id'] for s in own_refs for q in s['questions']
                           if q.get('source', {}).get('question_id') == qid}
    if not visible and not referenced_versions:
        raise Problem(404, 'NOT_FOUND', '题目不存在')
    versions_query = {'question_id': qid, 'state': 'sealed'}
    if vid is not None:
        versions_query['_id'] = vid
    elif not visible:
        versions_query['_id'] = {'$in': list(referenced_versions)}
    if vid is not None and not visible and vid not in referenced_versions:
        raise Problem(404, 'NOT_FOUND', '版本不存在')
    versions = list(db.survey_question_versions.find(versions_query).sort('version_no', 1))
    if vid is not None and not versions:
        raise Problem(404, 'NOT_FOUND', '已定稿版本不存在')
    allowed = [{'owner_id': uid}]
    if owner:
        allowed.append({'question_result_grants': {'$elemMatch': {
            'question_id': qid, 'enabled': True, 'allowed_at_publish': True}}})
    surveys = list(db.survey_surveys.find({**reference_query,
        'state': {'$in': ['published', 'closed']}, '$or': allowed},
        {'questions.source': 1, 'questions.id': 1, 'owner_id': 1, 'revision': 1}))
    selected = {v['_id'] for v in versions}
    instances, grants = {}, {}
    for survey in surveys:
        mapping = {q['id']: q['source']['question_version_id'] for q in survey['questions']
                   if q.get('source', {}).get('question_id') == qid
                   and q['source']['question_version_id'] in selected}
        if mapping:
            instances[survey['_id']] = mapping
            if survey['owner_id'] != uid:
                grants[survey['_id']] = survey['revision']
    return versions, instances, grants, 'owned_and_granted' if owner else 'owned_only'


def verify_grants(db, revisions):
    # Last revision check admits this response. Later revocation affects subsequent requests.
    # This is deliberately not a transaction snapshot across surveys and responses.
    ids = list(revisions)
    for start in range(0, len(ids), 100):
        batch = ids[start:start + 100]
        current = {s['_id']: s['revision'] for s in db.survey_surveys.find(
            {'_id': {'$in': batch}}, {'revision': 1})}
        if current != {sid: revisions[sid] for sid in batch}:
            raise Problem(409, 'STATISTICS_SCOPE_CHANGED', '统计授权或问卷状态已改变，请刷新统计')


def collect(db, versions, instances):
    data = {v['_id']: {'surveys': set(), 'instances': 0, 'answers': 0, 'submissions': set(),
                      'people': set(), 'options': Counter(), 'option_people': {},
                      'frequencies': Counter(), 'sum': Decimal(0)} for v in versions}
    payloads = {v['_id']: v['payload'] for v in versions}
    for sid, mapping in instances.items():
        for version_id in mapping.values():
            data[version_id]['surveys'].add(sid)
            data[version_id]['instances'] += 1
    ids = list(instances)
    for start in range(0, len(ids), 100):
        rows = db.survey_responses.find({'survey_id': {'$in': ids[start:start + 100]}},
            {'survey_id': 1, 'path': 1, 'answers': 1, 'respondent_id': 1})
        for row in rows:
            mapping, visited, seen = instances[row['survey_id']], set(row['path']), set()
            for answer in row['answers']:
                iid, value = answer['question_id'], answer['value']
                if iid not in mapping or iid not in visited or iid in seen:
                    continue
                seen.add(iid)
                if value is None or value == '' or value == []:
                    continue
                vid = mapping[iid]
                item, payload = data[vid], payloads[vid]
                kind = payload['kind']
                if kind == 'text' and not value.strip():
                    continue
                item['answers'] += 1
                item['submissions'].add(row['_id'])
                item['people'].add(row['respondent_id'])
                if kind in {'single', 'multiple'}:
                    options = {o['id'] for o in payload['options']}
                    choices = [value] if kind == 'single' else set(value)
                    if not set(choices) <= options:
                        raise Problem(503, 'INVALID_STORED_ANSWER', '已保存选项无效')
                    for choice in choices:
                        item['options'][choice] += 1
                        if kind == 'single':
                            item['option_people'].setdefault(choice, set()).add(row['respondent_id'])
                elif kind == 'number':
                    if not isinstance(value, Decimal128) or not value.to_decimal().is_finite():
                        raise Problem(503, 'INVALID_STORED_NUMBER', '已保存数值无效')
                    number = value.to_decimal()
                    with localcontext() as context:
                        context.prec = 12322 + len(str(item['answers']))
                        item['sum'] += number
                    item['frequencies'][number] += 1
    return data


def group(version, item):
    payload = version['payload']
    result = {'version_id': str(version['_id']), 'version_no': version['version_no'],
              'kind': payload['kind'], 'title': payload['title'],
              'included_survey_count': len(item['surveys']), 'included_instance_count': item['instances'],
              'answer_count': item['answers'], 'answered_submission_count': len(item['submissions']),
              'respondent_count': len(item['people'])}
    if payload['kind'] in {'single', 'multiple'}:
        result['options'] = []
        for option in payload['options']:
            count = {'option_id': option['id'], 'text': option['text'],
                     'selection_count': item['options'][option['id']]}
            if payload['kind'] == 'single':
                count['respondent_count'] = len(item['option_people'].get(option['id'], set()))
            result['options'].append(count)
    elif payload['kind'] == 'number':
        with localcontext(create_decimal128_context()):
            average = Decimal128(item['sum'] / Decimal(item['answers'])) if item['answers'] else None
        result.update(average=decimal_string(average) if average is not None else None,
                      average_display=average_display(item['sum'], item['answers']))
    return result


def statistics(db, qid, uid, vid=None):
    versions, instances, revisions, name = scope(db, qid, uid, vid)
    data = collect(db, versions, instances)
    result = {'question_id': str(qid), 'scope': name,
              'groups': [group(v, data[v['_id']]) for v in versions]}
    verify_grants(db, revisions)
    return result


def boundaries(raw):
    try:
        values = json.loads(raw) if raw is not None else None
        if not isinstance(values, list) or not 2 <= len(values) <= 101:
            raise ValueError()
        numbers = [decimal_value(v) for v in values]
        if any(a >= b for a, b in zip(numbers, numbers[1:])):
            raise ValueError()
        return numbers
    except (ValueError, TypeError):
        raise Problem(422, 'INVALID_BOUNDARIES', '区间需为2—101个严格递增的Decimal字符串边界')


def distribution(db, qid, vid, uid, mode, raw, offset, limit):
    versions, instances, revisions, name = scope(db, qid, uid, vid)
    if versions[0]['payload']['kind'] != 'number':
        raise Problem(422, 'NUMBER_REQUIRED', '仅数字题提供数值分布')
    edges = boundaries(raw) if mode == 'buckets' else None
    if mode == 'exact' and raw is not None:
        raise Problem(422, 'INVALID_INPUT', '精确频数不接受区间边界')
    item = collect(db, versions, instances)[vid]
    result = {'question_id': str(qid), 'scope': name, 'mode': mode, **group(versions[0], item)}
    if mode == 'exact':
        values = sorted(item['frequencies'])
        result.update(items=[{'value': decimal_string(v), 'count': item['frequencies'][v]}
                             for v in values[offset:offset + limit]],
                      total=len(values), offset=offset, limit=limit)
    else:
        counts = [0] * (len(edges) + 1)
        for value, count in item['frequencies'].items():
            counts[bisect_right(edges, value)] += count
        result.update(buckets=[{'lower': decimal_string(lo), 'upper': decimal_string(hi), 'count': counts[i + 1]}
                               for i, (lo, hi) in enumerate(zip(edges, edges[1:]))],
                      underflow=counts[0], overflow=counts[-1])
    verify_grants(db, revisions)
    return result
