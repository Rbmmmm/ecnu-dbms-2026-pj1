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


def connect(settings):
    if settings.testing:
        guard_test_database(settings.mongo_uri, settings.database, settings.run_id)
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10000, tz_aware=True)
    return client, client[settings.database]


def initialize(db):
    db.command('ping')
    for name, validator in VALIDATORS.items():
        collection = 'survey_' + name
        if collection not in db.list_collection_names():
            db.create_collection(collection, validator=validator, validationLevel='strict', validationAction='error')
        if db[collection].find_one({'schema_version': {'$ne': 1}}, {'_id': 1}):
            raise RuntimeError('unsupported schema_version')
        db[collection].create_indexes(INDEXES[name])
    inspect_contract(db)


def inspect_contract(db):
    result = {}
    for name, validator in VALIDATORS.items():
        collection = 'survey_' + name
        options = db[collection].options()
        if options.get('validator') != validator or options.get('validationLevel', 'strict') != 'strict' or options.get('validationAction', 'error') != 'error':
            raise RuntimeError('database validator does not match contract')
        actual = db[collection].index_information()
        for index in INDEXES[name]:
            spec = index.document
            found = actual.get(spec['name'], {})
            if list(found.get('key', [])) != list(spec['key'].items()):
                raise RuntimeError('database index does not match contract')
            for key in ['unique', 'partialFilterExpression', 'expireAfterSeconds']:
                if found.get(key) != spec.get(key):
                    raise RuntimeError('database index options do not match contract')
        result[collection] = {'validator': True, 'indexes': list(actual)}
    return result
