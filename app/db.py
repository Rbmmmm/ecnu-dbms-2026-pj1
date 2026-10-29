from pymongo import MongoClient, IndexModel
from app.config import guard_test_database, LIMITS


def field(kind, **kwargs):
    return {'bsonType': kind, **kwargs}


def contract(properties):
    return {'$jsonSchema': {'bsonType': 'object', 'required': list(properties),
                           'properties': {'_id': field('objectId'), **properties}}}


BASE = {'schema_version': field('int', enum=[1])}
DATE = field('date')
OID = field('objectId')
STR = field('string')
NULL_DATE = field(['date', 'null'])
VALIDATORS = {
    'users': contract({**BASE, 'username': STR, 'password_hash': STR, 'created_at': DATE}),
    'sessions': contract({**BASE, 'user_id': OID, 'token_hash': STR, 'created_at': DATE, 'expires_at': DATE}),
    'surveys': contract({**BASE, 'owner_id': OID, 'title': STR, 'description': STR,
        'allow_anonymous': field('bool'), 'allow_repeat': field('bool'),
        'state': field('string', enum=['draft', 'published', 'closed']),
        'deadline_at': NULL_DATE, 'revision': field('int', minimum=1),
        'content_version': field(['int', 'null'], enum=[None, 1]),
        'created_at': DATE, 'updated_at': DATE, 'published_at': NULL_DATE, 'closed_at': NULL_DATE,
        'questions': field('array', maxItems=LIMITS['questions'], items={'bsonType': 'object',
            'required': ['id', 'kind', 'title', 'required', 'validation', 'options', 'rules'],
            'properties': {'id': STR, 'kind': field('string', enum=['single', 'multiple', 'text', 'number']),
                'title': STR, 'required': field('bool'), 'validation': field('object'),
                'options': field('array'), 'rules': field('array')}})}),
    'responses': contract({**BASE, 'survey_id': OID, 'respondent_id': OID,
        'content_version': field('int', enum=[1]), 'anonymous': field('bool'), 'repeat_allowed': field('bool'),
        'submission_key': STR, 'request_digest': STR, 'accepted_at': DATE, 'created_at': DATE,
        'path': field('array', items=STR), 'answers': field('array', items={'bsonType': 'object',
            'required': ['question_id', 'value'], 'properties': {'question_id': STR,
                'value': field(['string', 'array', 'decimal'])}})})
}
INDEXES = {
    'users': [IndexModel([('username', 1)], unique=True, name='username_unique')],
    'surveys': [IndexModel([('owner_id', 1), ('created_at', -1), ('_id', -1)], name='owner_created')],
    'sessions': [IndexModel([('token_hash', 1)], unique=True, name='token_unique'),
                 IndexModel([('expires_at', 1)], expireAfterSeconds=0, name='session_expiry')],
    'responses': [IndexModel([('survey_id', 1), ('respondent_id', 1), ('submission_key', 1)],
                            unique=True, name='submission_unique'),
                  IndexModel([('survey_id', 1), ('respondent_id', 1)], unique=True,
                             partialFilterExpression={'repeat_allowed': False}, name='once_per_account'),
                  IndexModel([('survey_id', 1), ('created_at', -1), ('_id', -1)], name='survey_created')]
}


# Frozen v1 definitions are used only by the explicit upgrade preflight.
from copy import deepcopy
V1_VALIDATORS = deepcopy(VALIDATORS)
V1_INDEXES = deepcopy(INDEXES)
SOURCE = field('object', required=['question_id', 'question_version_id', 'payload_hash'],
    additionalProperties=False, properties={'question_id': OID, 'question_version_id': OID,
                                           'payload_hash': field('string', pattern='^[a-f0-9]{64}$')})
v2_survey = deepcopy(V1_VALIDATORS['surveys'])
v2_props = v2_survey['$jsonSchema']['properties']
v2_props['schema_version'] = field('int', enum=[2])
v2_props['questions']['items']['properties']['source'] = SOURCE
v2_props['question_result_grants'] = field('array', maxItems=LIMITS['questions'], items=field('object',
    required=['question_id', 'enabled', 'allowed_at_publish'], additionalProperties=False,
    properties={'question_id': OID, 'enabled': field('bool'), 'allowed_at_publish': field('bool')}))
v2_survey['$jsonSchema']['required'].append('question_result_grants')
VALIDATORS['surveys'] = {'$or': [
    {'$and': [deepcopy(V1_VALIDATORS['surveys']), {'question_result_grants': {'$exists': False}},
              {'questions': {'$not': {'$elemMatch': {'source': {'$exists': True}}}}}]}, v2_survey]}
REV = field('int', minimum=1, maximum=LIMITS['revision'])
CREATE = {'creation_key': STR, 'creation_digest': STR}
VALIDATORS.update({
    'questions': contract({**BASE, 'owner_id': OID, 'label': field('string', minLength=1, maxLength=200),
        'revision': REV, 'shared_with': field('array', maxItems=100, uniqueItems=True, items=OID),
        'created_at': DATE, 'updated_at': DATE, **CREATE}),
    'question_versions': contract({**BASE, 'question_id': OID, 'version_no': REV,
        'state': field('string', enum=['draft', 'sealed']), 'revision': REV,
        'parent_version_id': field(['objectId', 'null']),
        'operation': field('string', enum=['create', 'derive', 'restore']),
        'restored_from_version_id': field(['objectId', 'null']), **CREATE,
        'payload': field('object', required=['kind', 'title', 'required', 'validation', 'options'],
            additionalProperties=False, properties={key: deepcopy(value) for key, value in
                v2_props['questions']['items']['properties'].items() if key in
                ['kind', 'title', 'required', 'validation', 'options']}),
        'payload_hash': field(['string', 'null']), 'created_at': DATE, 'updated_at': DATE,
        'sealed_at': NULL_DATE}),
    'question_banks': contract({**BASE, 'owner_id': OID, 'title': field('string', minLength=1, maxLength=200),
        'revision': REV, 'entries': field('array', maxItems=1000, items=field('object',
            required=['question_id', 'default_version_id'], additionalProperties=False,
            properties={'question_id': OID, 'default_version_id': OID})),
        'created_at': DATE, 'updated_at': DATE, **CREATE})
})
INDEXES['surveys'] += [
    IndexModel([('questions.source.question_id', 1)], name='source_question'),
    IndexModel([('questions.source.question_version_id', 1)], name='source_version')]
INDEXES.update({
    'questions': [IndexModel([('owner_id', 1), ('created_at', -1), ('_id', -1)], name='owner_created'),
        IndexModel([('shared_with', 1), ('created_at', -1), ('_id', -1)], name='shared_created'),
        IndexModel([('owner_id', 1), ('creation_key', 1)], unique=True, name='owner_creation_unique')],
    'question_versions': [IndexModel([('question_id', 1), ('version_no', 1)], unique=True, name='family_version_unique'),
        IndexModel([('question_id', 1), ('creation_key', 1)], unique=True, name='family_creation_unique')],
    'question_banks': [IndexModel([('owner_id', 1), ('created_at', -1), ('_id', -1)], name='owner_created'),
        IndexModel([('owner_id', 1), ('creation_key', 1)], unique=True, name='owner_creation_unique')]
})


def connect(settings):
    if settings.testing:
        guard_test_database(settings.mongo_uri, settings.database, settings.run_id)
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10000, tz_aware=True)
    return client, client[settings.database]


def check_documents(db):
    for name in VALIDATORS:
        allowed = [1, 2] if name == 'surveys' else [1]
        if db['survey_' + name].find_one({'schema_version': {'$nin': allowed}}, {'_id': 1}):
            raise RuntimeError('unsupported schema_version')
    if db.survey_surveys.find_one({'schema_version': 1, '$or': [
            {'question_result_grants': {'$exists': True}}, {'questions.source': {'$exists': True}}]}, {'_id': 1}):
        raise RuntimeError('schema1 contains reserved source/grant')
    for name, validator in VALIDATORS.items():
        if db['survey_' + name].find_one({'$nor': [validator]}, {'_id': 1}):
            raise RuntimeError('stored document does not match contract')


def check_indexes(actual, specs, partial=False):
    expected = {'_id_'} | {i.document['name'] for i in specs}
    if set(actual) - expected or (not partial and set(actual) != expected):
        raise RuntimeError('database index set does not match contract')
    if actual.get('_id_', {}).get('key') != [('_id', 1)] or set(actual['_id_']) - {'v', 'key'}:
        raise RuntimeError('database primary index does not match contract')
    for index in specs:
        spec = index.document
        if partial and spec['name'] not in actual:
            continue
        found = actual.get(spec['name'], {})
        if list(found.get('key', [])) != list(spec['key'].items()):
            raise RuntimeError('database index does not match contract')
        if ({k: v for k, v in found.items() if k not in {'v', 'key'}} !=
                {k: v for k, v in spec.items() if k not in {'name', 'key'}}):
            raise RuntimeError('database index options do not match contract')


def inspect_contract(db):
    result = {}
    expected = {'survey_' + n for n in VALIDATORS}
    actual = {n for n in db.list_collection_names() if n.startswith('survey_')}
    if actual != expected:
        raise RuntimeError('database collections do not match contract; explicit v2 upgrade required')
    for name, validator in VALIDATORS.items():
        collection = 'survey_' + name
        options = db[collection].options()
        if (options.get('validator') != validator or options.get('validationLevel', 'strict') != 'strict'
                or options.get('validationAction', 'error') != 'error' or
                set(options) - {'validator', 'validationLevel', 'validationAction'}):
            raise RuntimeError('database validator does not match contract; explicit v2 upgrade required')
        actual_indexes = db[collection].index_information()
        check_indexes(actual_indexes, INDEXES[name])
        result[collection] = {'validator': True, 'indexes': list(actual_indexes)}
    check_documents(db)
    return result


def initialize(db):
    db.command('ping')
    # An existing database is inspected before any mutation. Startup never migrates it.
    if any(n.startswith('survey_') for n in db.list_collection_names()):
        inspect_contract(db)
        return
    for name, validator in VALIDATORS.items():
        db.create_collection('survey_' + name, validator=validator,
                             validationLevel='strict', validationAction='error')
        db['survey_' + name].create_indexes(INDEXES[name])
    inspect_contract(db)
