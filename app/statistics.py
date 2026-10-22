from decimal import Decimal, localcontext
from bson.decimal128 import Decimal128, create_decimal128_context
from app.decimal_values import decimal_string
from app.domain import Problem


def average_display(total, count):
    if not count:
        return '暂无数据'
    # Round the exact fraction once; rounding to Decimal128 first can cross a half-cent.
    numerator, denominator = total.as_integer_ratio()
    denominator *= count
    cents, remainder = divmod(abs(numerator)*100, denominator)
    if remainder*2 >= denominator:
        cents += 1
    whole, fraction = divmod(cents,100)
    return ('-' if numerator < 0 and cents else '') + format(Decimal(whole),'f') + f'.{fraction:02d}'


def finite_average(db, survey_id, question_id):
    # Sum exactly: Decimal128 $avg can lose small terms even when its result is finite.
    rows = db.survey_responses.aggregate([
        {'$match': {'survey_id': survey_id}}, {'$unwind': '$answers'},
        {'$match': {'answers.question_id': question_id, 'answers.value': {'$type': 'decimal'}}},
        {'$project': {'_id': 0, 'value': '$answers.value'}}])
    total, count = Decimal(0), 0
    with localcontext() as context:
        for row in rows:
            value = row['value'].to_decimal()
            if not value.is_finite():
                raise Problem(503, 'INVALID_STORED_NUMBER', '已保存数值无效')
            count += 1
            # Decimal128 spans 1E-6176 through adjusted exponent 6144.
            context.prec = 12322 + len(str(count))
            total += value
    if not count:
        return None, 0, '暂无数据'
    with localcontext(create_decimal128_context()):
        return Decimal128(total / Decimal(count)), count, average_display(total,count)


def statistics(db, survey):
    ids = {kind: [q['id'] for q in survey['questions'] if q['kind'] == kind]
           for kind in ['single', 'multiple', 'text', 'number']}
    answers = [{'$unwind': '$answers'}]
    single = answers + [{'$match': {'answers.question_id': {'$in': ids['single']}}}]
    data = list(db.survey_responses.aggregate([
        {'$match': {'survey_id': survey['_id']}},
        {'$facet': {
            'total': [{'$group': {'_id': '$respondent_id', 'n': {'$sum': 1}}},
                      {'$group': {'_id': None, 'submission_count': {'$sum': '$n'}, 'respondent_count': {'$sum': 1}}}],
            'single_options': single + [
                {'$group': {'_id': {'q': '$answers.question_id', 'o': '$answers.value', 'u': '$respondent_id'}, 'n': {'$sum': 1}}},
                {'$group': {'_id': {'q': '$_id.q', 'o': '$_id.o'}, 'respondent_count': {'$sum': 1}, 'submission_count': {'$sum': '$n'}}}],
            'answered': answers + [
                {'$group': {'_id': {'q': '$answers.question_id', 'u': '$respondent_id'}, 'n': {'$sum': 1}}},
                {'$group': {'_id': '$_id.q', 'respondent_count': {'$sum': 1}, 'submission_count': {'$sum': '$n'}}}],
            'multiple_options': answers + [
                {'$match': {'answers.question_id': {'$in': ids['multiple']}}}, {'$unwind': '$answers.value'},
                {'$group': {'_id': {'q': '$answers.question_id', 'o': '$answers.value'}, 'selection_count': {'$sum': 1}}}],
        }}]))[0]
    total = data['total'][0] if data['total'] else {'submission_count': 0, 'respondent_count': 0}
    answered = {item['_id']: item for item in data['answered']}
    questions = []
    for q in survey['questions']:
        count = answered.get(q['id'], {})
        item = {'id': q['id'], 'title': q['title'], 'kind': q['kind']}
        if q['kind'] == 'single':
            item.update(respondent_count=count.get('respondent_count', 0), submission_count=count.get('submission_count', 0), options=[])
            for option in q['options']:
                c = next((v for v in data['single_options'] if v['_id'] == {'q': q['id'], 'o': option['id']}), {})
                item['options'].append({'id': option['id'], 'text': option['text'],
                    'respondent_count': c.get('respondent_count', 0), 'submission_count': c.get('submission_count', 0)})
        else:
            item['answered_submission_count'] = count.get('submission_count', 0)
            if q['kind'] == 'multiple':
                item['options'] = []
                for option in q['options']:
                    c = next((v for v in data['multiple_options'] if v['_id'] == {'q': q['id'], 'o': option['id']}), {})
                    item['options'].append({'id': option['id'], 'text': option['text'], 'selection_count': c.get('selection_count', 0)})
            elif q['kind'] == 'number':
                average, count, display = finite_average(db, survey['_id'], q['id'])
                item.update(average=decimal_string(average) if average is not None else None,
                            average_display=display, answered_submission_count=count)
        questions.append(item)
    return {'survey_id': str(survey['_id']), 'title': survey['title'],
            'submission_count': total['submission_count'], 'respondent_count': total['respondent_count'],
            'questions': questions}


def contents(db, survey, question, offset, limit):
    query = {'survey_id': survey['_id'], 'answers': {'$elemMatch': {'question_id': question['id']}}}
    rows = list(db.survey_responses.find(query, {'created_at': 1, 'anonymous': 1, 'respondent_id': 1,
                'answers': {'$elemMatch': {'question_id': question['id']}}})
                .sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit))
    real_ids = {row['respondent_id'] for row in rows if not row['anonymous']}
    names = {user['_id']: user['username'] for user in db.survey_users.find({'_id': {'$in': list(real_ids)}}, {'username': 1})}
    items = []
    for row in rows:
        value = row['answers'][0]['value']
        item = {'value': decimal_string(value) if question['kind'] == 'number' else value,
                'created_at': row['created_at'].isoformat(), 'anonymous': row['anonymous']}
        if not row['anonymous']:
            item['username'] = names.get(row['respondent_id'], '已注册用户')
        items.append(item)
    return {'question_id': question['id'], 'kind': question['kind'], 'items': items,
            'total': db.survey_responses.count_documents(query), 'offset': offset, 'limit': limit}
