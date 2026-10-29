"""Immutable reusable payloads; questionnaire rules always belong to an instance."""
from copy import deepcopy
import hashlib
import json
from bson import ObjectId, BSON
from bson.decimal128 import Decimal128
from pymongo.errors import DuplicateKeyError
from app.config import LIMITS
from app.decimal_values import decimal_string, questions_to_bson
from app.domain import Problem, publish_issues

PAYLOAD_FIELDS = ('kind', 'title', 'required', 'validation', 'options')
PUBLIC_QUESTION_FIELDS = ('id', *PAYLOAD_FIELDS, 'rules')


def wire(value):
    if isinstance(value, Decimal128):
        return decimal_string(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [wire(v) for v in value]
    return value


def payload(question):
    return {k: deepcopy(question[k]) for k in PAYLOAD_FIELDS}


def digest(value):
    return hashlib.sha256(json.dumps(wire(value), sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def payload_bson(value):
    q = questions_to_bson([{'id': '00000000-0000-4000-8000-000000000001',
                            **value, 'rules': []}])[0]
    return payload(q)


def issues(value):
    return publish_issues({'title': '题目定稿', 'questions': [
        {'id': '00000000-0000-4000-8000-000000000001', **wire(value), 'rules': []}]})


def size_check(document):
    if len(BSON.encode(document)) > LIMITS['document_bytes']:
        raise Problem(413, 'DOCUMENT_TOO_LARGE', '文档不能超过8MiB')


def family(db, qid, uid, use=False):
    query = {'_id': ObjectId(qid)}
    query.update({'$or': [{'owner_id': uid}, {'shared_with': uid}]} if use else {'owner_id': uid})
    doc = db.survey_questions.find_one(query)
    if not doc:
        raise Problem(404, 'NOT_FOUND', '题目不存在')
    return doc


def version(db, qid, vid):
    doc = db.survey_question_versions.find_one({'_id': ObjectId(vid), 'question_id': ObjectId(qid)})
    if not doc:
        raise Problem(404, 'NOT_FOUND', '版本不存在')
    return doc


def same_creation(existing, creation_digest):
    if existing['creation_digest'] != creation_digest:
        raise Problem(409, 'KEY_REUSED_WITH_DIFFERENT_CONTENT', '创建键已用于不同内容')
    return existing, False


def create_family(db, uid, body, time):
    data = body.model_dump()
    lookup = {'owner_id': uid, 'creation_key': data['creation_key']}
    hashed = digest(data)
    existing = db.survey_questions.find_one(lookup)
    if existing:
        return same_creation(existing, hashed)
    doc = {**lookup, 'label': data['label'], 'creation_digest': hashed,
        'schema_version': 1, 'revision': 1, 'shared_with': [], 'created_at': time, 'updated_at': time}
    try:
        db.survey_questions.insert_one(doc)
    except DuplicateKeyError:
        return same_creation(db.survey_questions.find_one(lookup), hashed)
    return doc, True


def create_version(db, qid, uid, body, time):
    family(db, qid, uid)
    data = body.model_dump()
    lookup = {'question_id': ObjectId(qid), 'creation_key': body.creation_key}
    hashed = digest(data)
    existing = db.survey_question_versions.find_one(lookup)
    if existing:
        return same_creation(existing, hashed)
    parent = None
    if body.operation in {'derive', 'restore'}:
        parent = version(db, qid, body.parent_version_id)
        if parent['state'] != 'sealed':
            raise Problem(409, 'VERSION_NOT_SEALED', '只能从定稿版本派生')
    initial = deepcopy(parent['payload']) if parent else payload_bson(body.payload.model_dump())
    for _ in range(5):
        latest = db.survey_question_versions.find_one({'question_id': ObjectId(qid)}, sort=[('version_no', -1)])
        number = latest['version_no'] + 1 if latest else 1
        if number > LIMITS['revision']:
            raise Problem(409, 'VERSION_LIMIT', '版本序号已达上限')
        doc = {**lookup, 'schema_version': 1, 'version_no': number, 'revision': 1,
            'state': 'draft', 'operation': body.operation,
            'parent_version_id': parent['_id'] if parent else None,
            'restored_from_version_id': parent['_id'] if body.operation == 'restore' else None,
            'creation_digest': hashed, 'payload': initial, 'payload_hash': None,
            'created_at': time, 'updated_at': time, 'sealed_at': None}
        size_check(doc)
        try:
            db.survey_question_versions.insert_one(doc)
            return doc, True
        except DuplicateKeyError:
            existing = db.survey_question_versions.find_one(lookup)
            if existing:
                return same_creation(existing, hashed)
    raise Problem(409, 'VERSION_CONFLICT', '版本创建冲突，请使用原创建键重试')


def save_version(db, qid, vid, uid, revision, value, time, seal=False):
    family(db, qid, uid)
    doc = version(db, qid, vid)
    if seal and doc['state'] == 'sealed':
        return doc
    value = deepcopy(doc['payload']) if seal else payload_bson(value)
    if (not seal and doc['state'] == 'draft' and doc['revision'] == revision + 1
            and wire(doc['payload']) == wire(value)):
        return doc
    if doc['revision'] != revision:
        raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变，请刷新后重试')
    if revision == LIMITS['revision']:
        raise Problem(409, 'REVISION_LIMIT', '版本已达上限')
    changes = {'payload': value, 'updated_at': time}
    if seal:
        problems = issues(value)
        if problems:
            raise Problem(422, 'INVALID_CONFIGURATION', '请修复题目后定稿', problems)
        changes.update(state='sealed', payload_hash=digest(value), sealed_at=time)
    size_check({**doc, **changes})
    updated = db.survey_question_versions.find_one_and_update(
        {'_id': doc['_id'], 'revision': revision, 'state': 'draft'},
        {'$set': changes, '$inc': {'revision': 1}}, return_document=True)
    if updated is None:
        raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变，请刷新后重试')
    return updated


def validate_source(db, question):
    source = question['source']
    doc = db.survey_question_versions.find_one({'_id': source['question_version_id'],
        'question_id': source['question_id'], 'state': 'sealed'})
    if (not doc or not db.survey_questions.find_one({'_id': source['question_id']}, {'_id': 1})
            or doc['payload_hash'] != source['payload_hash'] or digest(doc['payload']) != source['payload_hash']
            or wire(payload(question)) != wire(doc['payload'])):
        raise Problem(422, 'INVALID_SOURCE', '来源版本与题目内容不一致')


def protect_draft(db, survey, questions):
    previous = {q['id']: q for q in survey['questions']}
    for q in questions:
        old = previous.get(q['id'], {})
        if 'source' in q and ('source' not in old or wire(q['source']) != wire(old['source'])):
            raise Problem(422, 'INVALID_SOURCE', '请通过选入接口设置来源')
        if 'source' in old:
            q['source'] = deepcopy(old['source'])
            validate_source(db, q)
        elif q.get('source') is None:
            q.pop('source', None)
    return questions


def remaining_grants(survey, questions):
    families = {q['source']['question_id'] for q in questions if 'source' in q}
    grants = {g['question_id']: deepcopy(g) for g in survey.get('question_result_grants', [])}
    return [grants.get(qid, {'question_id': qid, 'enabled': False, 'allowed_at_publish': False})
            for qid in sorted(families)]


def cas_document(collection, doc, revision, changes, time):
    # PUT/PATCH retries may recover exactly the immediately following revision.
    if doc['revision'] != revision:
        if doc['revision'] == revision + 1 and all(doc.get(k) == v for k, v in changes.items()):
            return doc
        raise Problem(409, 'REVISION_CONFLICT', '版本已改变，请刷新后重试')
    if revision == LIMITS['revision']:
        raise Problem(409, 'REVISION_LIMIT', '管理版本已达上限')
    changes = {**changes, 'updated_at': time}
    size_check({**doc, **changes})
    updated = collection.find_one_and_update({'_id': doc['_id'], 'revision': revision},
        {'$set': changes, '$inc': {'revision': 1}}, return_document=True)
    if updated is None:
        raise Problem(409, 'REVISION_CONFLICT', '版本已改变，请刷新后重试')
    return updated


def create_bank(db, uid, body, time):
    data = body.model_dump()
    lookup = {'owner_id': uid, 'creation_key': body.creation_key}
    hashed = digest(data)
    existing = db.survey_question_banks.find_one(lookup)
    if existing:
        return same_creation(existing, hashed)
    doc = {**lookup, 'schema_version': 1, 'title': body.title, 'entries': [], 'revision': 1,
           'creation_digest': hashed, 'created_at': time, 'updated_at': time}
    try:
        db.survey_question_banks.insert_one(doc)
    except DuplicateKeyError:
        return same_creation(db.survey_question_banks.find_one(lookup), hashed)
    return doc, True


def bank(db, bid, uid):
    doc = db.survey_question_banks.find_one({'_id': ObjectId(bid), 'owner_id': uid})
    if not doc:
        raise Problem(404, 'NOT_FOUND', '题库不存在')
    return doc


def save_bank(db, doc, uid, body, time):
    entries = [{'question_id': ObjectId(x.question_id), 'default_version_id': ObjectId(x.default_version_id)}
               for x in body.entries]
    changes = {'title': body.title, 'entries': entries}
    if doc['revision'] != body.revision:
        return cas_document(db.survey_question_banks, doc, body.revision, changes, time)
    previous = {x['question_id']: x['default_version_id'] for x in doc['entries']}
    changed = [x for x in entries if previous.get(x['question_id']) != x['default_version_id']]
    for x in changed:
        family(db, x['question_id'], uid, use=True)
        if version(db, x['question_id'], x['default_version_id'])['state'] != 'sealed':
            raise Problem(409, 'VERSION_NOT_SEALED', '题库默认版本必须已定稿')
    # Last permission reads admit the changed entries; later revocation does not undo this save.
    for x in changed:
        family(db, x['question_id'], uid, use=True)
    return cas_document(db.survey_question_banks, doc, body.revision, changes, time)
