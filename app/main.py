from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import re
import secrets
from typing import Literal
from copy import deepcopy

from bson import ObjectId, BSON
from bson.decimal128 import Decimal128
from app.decimal_values import decimal_string, questions_to_bson, answer_to_bson
from fastapi import FastAPI, Request, Depends, Query
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pwdlib import PasswordHash
from pymongo.errors import PyMongoError, DuplicateKeyError

from app.config import Settings, LIMITS
from app.time_values import utc_millisecond
from app.db import connect, initialize, inspect_contract
from app.domain import Problem, publish_issues, replay
from app.schemas import Credentials, SurveyCreate, Draft, Revision, Evaluate, Submission, LoginCredentials, DeadlineChange
from app.statistics import statistics, contents
from app import question_catalog as catalog
from app.schemas import QuestionCreate, VersionCreate, VersionDraft, QuestionImport
from app.schemas import QuestionLabel, QuestionSharing, BankCreate, BankUpdate, SourceChange, ResultGrant

COOKIE = 'survey_session'
PASSWORD = PasswordHash.recommended()
DUMMY_HASH = PASSWORD.hash(secrets.token_urlsafe(32))
ROOT = Path(__file__).resolve().parent


def now():
    value = datetime.now(timezone.utc)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def server_time(request):
    return utc_millisecond(request.app.state.clock())

def error(status, code, message, fields=None):
    return JSONResponse({'error': {'code': code, 'message': message, 'fields': fields or []}}, status_code=status)


def public(value):
    if isinstance(value, Decimal128):
        return decimal_string(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {('id' if k == '_id' else k): public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def oid(value):
    if not re.fullmatch(r'[a-f0-9]{24}', value):
        raise Problem(404, 'NOT_FOUND', '问卷不存在')
    return ObjectId(value)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9_\u4e00-\u9fff]{3,32}', value):
        raise Problem(422, 'INVALID_USERNAME', '用户名需3—32个中文、英文字母、数字或下划线')
    return value


class WriteGuard:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            headers = dict(scope['headers'])
            if (headers.get(b'origin', b'') != self.settings.origin.encode('ascii') or
                    headers.get(b'x-survey-request') != b'1'):
                return await error(403, 'BAD_ORIGIN', '请求来源校验失败')(scope, receive, send)
            if headers.get(b'content-type', b'').split(b';')[0].strip() != b'application/json':
                return await error(400, 'JSON_REQUIRED', '请使用JSON请求')(scope, receive, send)
            messages, size = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                size += len(message.get('body', b''))
                if size > self.settings.max_body:
                    return await error(413, 'BODY_TOO_LARGE', '请求不能超过1MiB')(scope, receive, send)
                messages.append(message)
                if not message.get('more_body', False):
                    break
            async def buffered():
                return messages.pop(0) if messages else await receive()
            return await self.app(scope, buffered, send)
        return await self.app(scope, receive, send)


def create_app(settings=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        client, db = connect(settings)
        try:
            initialize(db)
            app.state.db, app.state.client = db, client
            app.state.clock = now
            yield
        finally:
            client.close()

    app = FastAPI(title='MongoDB 问卷系统', lifespan=lifespan)
    app.add_middleware(WriteGuard, settings=settings)
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    templates = Jinja2Templates(directory=ROOT / 'templates')

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return error(exc.status, exc.code, exc.message, exc.fields)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Do not return input values (especially credentials) in validation errors.
        fields = [{'question_id': None, 'field': '.'.join(map(str, e['loc'])), 'message': e['msg']}
                  for e in exc.errors()]
        status = 400 if any(e['type'] == 'json_invalid' for e in exc.errors()) else 422
        return error(status, 'INVALID_INPUT', '输入格式不正确', fields)

    @app.exception_handler(HTTPException)
    async def http_handler(request, exc):
        return error(exc.status_code, 'HTTP_ERROR', '请求资源或方法无效')

    @app.exception_handler(PyMongoError)
    async def database_handler(request, exc):
        return error(503, 'DATABASE_UNAVAILABLE', '数据库暂不可用，请保留原提交重试')

    def db(request: Request):
        return request.app.state.db

    def user(request: Request):
        database = request.app.state.db
        token = request.cookies.get(COOKIE)
        session = database.survey_sessions.find_one({'token_hash': hash_token(token)}) if token else None
        if not session or session['expires_at'] <= server_time(request):
            raise Problem(401, 'AUTH_REQUIRED', '请先登录')
        account = database.survey_users.find_one({'_id': session['user_id']})
        if not account:
            raise Problem(401, 'AUTH_REQUIRED', '请先登录')
        return account

    def owned(database, sid, account):
        survey = database.survey_surveys.find_one({'_id': oid(sid), 'owner_id': account['_id']})
        if not survey:
            raise Problem(404, 'NOT_FOUND', '问卷不存在')
        return survey

    def fill_survey(database, sid):
        survey = database.survey_surveys.find_one({'_id': oid(sid), 'state': {'$ne': 'draft'}})
        if not survey:
            raise Problem(404, 'NOT_FOUND', '问卷不存在或尚未发布')
        return survey

    def deadline(value, time):
        if value is not None and value <= time:
            raise Problem(422, 'INVALID_DEADLINE', '截止时间必须在未来')

    def view(survey):
        value = public(survey)
        value.pop('owner_id', None)
        if survey['schema_version'] == 2:
            value['result_grant_options'] = [{'question_id': str(g['question_id']),
                'external': app.state.db.survey_questions.find_one(
                    {'_id': g['question_id'], 'owner_id': survey['owner_id']}, {'_id': 1}) is None}
                for g in survey['question_result_grants']]
        value['share_url'] = '/survey/' + str(survey['_id'])
        value['publish_issues'] = publish_issues(public(survey)) if survey['state'] == 'draft' else []
        return value

    def update(database, survey, revision, changes, required_state):
        if revision == LIMITS['revision']:
            raise Problem(409, 'REVISION_LIMIT', '问卷管理版本已达上限')
        updated = database.survey_surveys.find_one_and_update(
            {'_id': survey['_id'], 'owner_id': survey['owner_id'], 'revision': revision, 'state': required_state},
            {'$set': changes, '$inc': {'revision': 1}}, return_document=True)
        if updated is None:
            raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变，请刷新后重试')
        return view(updated)

    @app.get('/health')
    def health(database=Depends(db)):
        database.command('ping')
        try:
            inspect_contract(database)
        except RuntimeError:
            raise Problem(503, 'SCHEMA_NOT_READY', '数据库契约检查失败')
        return {'status': 'ready', 'stage': 'II-P3', 'work_package': 'II-W004', 'schema_version': 2}

    @app.post('/api/v1/auth/register', status_code=201)
    def register(body: Credentials, request: Request, database=Depends(db)):
        account = {'schema_version': 1, 'username': username(body.username),
                   'password_hash': PASSWORD.hash(body.password), 'created_at': server_time(request)}
        try:
            database.survey_users.insert_one(account)
        except DuplicateKeyError:
            raise Problem(409, 'USERNAME_EXISTS', '用户名已存在')
        return {'id': str(account['_id']), 'username': account['username']}

    @app.post('/api/v1/auth/login')
    def login(body: LoginCredentials, request: Request, response: Response, database=Depends(db)):
        try:
            name = username(body.username)
        except Problem:
            name = ''
        account = database.survey_users.find_one({'username': name})
        valid = PASSWORD.verify(body.password, account['password_hash'] if account else DUMMY_HASH)
        if not account or not valid:
            raise Problem(401, 'INVALID_CREDENTIALS', '账号或密码错误')
        previous = request.cookies.get(COOKIE)
        if previous:
            database.survey_sessions.delete_one({'token_hash': hash_token(previous)})
        token, time = secrets.token_urlsafe(32), server_time(request)
        database.survey_sessions.insert_one({'schema_version': 1, 'user_id': account['_id'],
            'token_hash': hash_token(token), 'created_at': time, 'expires_at': time + timedelta(hours=24)})
        response.set_cookie(COOKIE, token, httponly=True, secure=settings.secure_cookie,
                            samesite='lax', path='/', max_age=86400)
        return {'id': str(account['_id']), 'username': account['username']}

    @app.get('/api/v1/auth/me')
    def me(account=Depends(user)):
        return {'id': str(account['_id']), 'username': account['username']}

    @app.post('/api/v1/auth/logout', status_code=204)
    def logout(request: Request, database=Depends(db)):
        token = request.cookies.get(COOKIE)
        if token:
            database.survey_sessions.delete_one({'token_hash': hash_token(token)})
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.post('/api/v1/surveys', status_code=201)
    def create(body: SurveyCreate, request: Request, account=Depends(user), database=Depends(db)):
        time = server_time(request)
        deadline(body.deadline_at, time)
        survey = {**body.model_dump(), 'schema_version': 1, 'owner_id': account['_id'],
                  'state': 'draft', 'revision': 1, 'content_version': None, 'questions': [],
                  'created_at': time, 'updated_at': time, 'published_at': None, 'closed_at': None}
        database.survey_surveys.insert_one(survey)
        return view(survey)

    @app.get('/api/v1/surveys')
    def listing(offset: int = Query(0, ge=0, le=LIMITS['offset']), limit: int = Query(20, ge=1, le=100), account=Depends(user), database=Depends(db)):
        query = {'owner_id': account['_id']}
        rows = database.survey_surveys.find(query, {'questions': 0, 'owner_id': 0}).sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit)
        return {'items': public(list(rows)), 'total': database.survey_surveys.count_documents(query)}

    @app.get('/api/v1/surveys/{sid}')
    def management(sid: str, account=Depends(user), database=Depends(db)):
        return view(owned(database, sid, account))

    @app.put('/api/v1/surveys/{sid}/draft')
    def save(sid: str, body: Draft, request: Request, account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        deadline(body.deadline_at, server_time(request))
        changes = body.model_dump(exclude={'revision'})
        for question in changes['questions']:
            if question.get('source') is None:
                question.pop('source', None)
        changes['questions'] = catalog.protect_draft(database, survey, questions_to_bson(changes['questions']))
        if survey['schema_version'] == 2:
            changes['question_result_grants'] = catalog.remaining_grants(survey, changes['questions'])
        changes['updated_at'] = server_time(request)
        if len(BSON.encode({**survey, **changes})) > LIMITS['document_bytes']:
            raise Problem(413, 'DOCUMENT_TOO_LARGE', '文档不能超过8MiB')
        return update(database, survey, body.revision, changes, 'draft')

    @app.post('/api/v1/surveys/{sid}/publish')
    def publish(sid: str, body: Revision, request: Request, account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        if survey['state'] == 'published':
            return view(survey)
        if survey['state'] != 'draft' or body.revision != survey['revision']:
            raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变')
        issues = publish_issues(public(survey))
        if issues:
            raise Problem(422, 'INVALID_CONFIGURATION', '请修复问卷后发布', issues)
        for question in survey['questions']:
            if 'source' in question:
                catalog.validate_source(database, question)
        time = server_time(request)
        deadline(survey['deadline_at'], time)
        changes = {'state': 'published', 'content_version': 1, 'published_at': time, 'updated_at': time}
        if survey['schema_version'] == 2:
            changes['question_result_grants'] = [{**g, 'allowed_at_publish': g['enabled']}
                for g in catalog.remaining_grants(survey, survey['questions'])]
        return update(database, survey, body.revision, changes, 'draft')

    @app.patch('/api/v1/surveys/{sid}/deadline')
    def change_deadline(sid: str, body: DeadlineChange, request: Request, account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        time = server_time(request)
        deadline(body.deadline_at, time)
        return update(database, survey, body.revision,
                      {'deadline_at': body.deadline_at, 'updated_at': time}, 'published')

    @app.post('/api/v1/surveys/{sid}/close')
    def close(sid: str, body: Revision, request: Request, account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        if survey['state'] == 'closed':
            return view(survey)
        time = server_time(request)
        return update(database, survey, body.revision, {'state': 'closed', 'closed_at': time, 'updated_at': time}, 'published')

    @app.get('/api/v1/fill/{sid}')
    def filling(sid: str, request: Request, account=Depends(user), database=Depends(db)):
        survey = fill_survey(database, sid)
        time = server_time(request)
        accepting = survey['state'] == 'published' and (survey['deadline_at'] is None or time < survey['deadline_at'])
        result = {'id': sid, 'title': survey['title'], 'description': survey['description'],
                'questions': public([{k: q[k] for k in catalog.PUBLIC_QUESTION_FIELDS} for q in survey['questions']]), 'content_version': survey['content_version'],
                'allow_anonymous': survey['allow_anonymous'], 'allow_repeat': survey['allow_repeat'],
                'accepting': accepting, 'deadline_at': public(survey['deadline_at']),
                'already_submitted': database.survey_responses.find_one({'survey_id': survey['_id'], 'respondent_id': account['_id']}, {'_id': 1}) is not None}
        if survey['schema_version'] == 2:
            enabled = {g['question_id'] for g in survey['question_result_grants']
                       if g['enabled'] and g['allowed_at_publish']}
            result['result_sharing_notice'] = {'enabled': bool(enabled), 'aggregate_only': True,
                'question_instance_ids': [q['id'] for q in survey['questions']
                    if q.get('source', {}).get('question_id') in enabled]}
        return result

    @app.post('/api/v1/fill/{sid}/evaluate')
    def evaluate(sid: str, body: Evaluate, account=Depends(user), database=Depends(db)):
        survey = fill_survey(database, sid)
        return replay(public(survey['questions']), body.answers)

    @app.post('/api/v1/fill/{sid}/responses')
    def submit(sid: str, body: Submission, request: Request, response: Response, account=Depends(user), database=Depends(db)):
        survey = fill_survey(database, sid)
        result = replay(public(survey['questions']), body.answers)
        if not result['ready_to_submit'] or result['errors']:
            raise Problem(422, 'INVALID_ANSWERS', '请完成当前路径的题目', result['errors'])
        if set(body.answers) != set(result['path']):
            raise Problem(422, 'STALE_BRANCH_ANSWERS', '请重新确认有效答案')
        digest = hashlib.sha256(json.dumps({'content_version': body.content_version,
            'anonymous': body.anonymous, 'answers': result['active_answers']}, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        lookup = {'survey_id': survey['_id'], 'respondent_id': account['_id'], 'submission_key': body.submission_key}
        def receipt(doc):
            if doc['request_digest'] != digest:
                raise Problem(409, 'KEY_REUSED_WITH_DIFFERENT_CONTENT', '提交键已用于不同内容')
            return {'response_id': str(doc['_id']), 'survey_id': sid, 'accepted_at': public(doc['accepted_at'])}
        existing = database.survey_responses.find_one(lookup)
        if existing:
            return receipt(existing)
        survey = fill_survey(database, sid)
        accepted = server_time(request)
        if survey['state'] != 'published' or (survey['deadline_at'] is not None and accepted >= survey['deadline_at']):
            raise Problem(409, 'NOT_ACCEPTING', '问卷已关闭或截止')
        if body.anonymous and not survey['allow_anonymous']:
            raise Problem(422, 'ANONYMOUS_FORBIDDEN', '此问卷不允许匿名')
        question_map = {q['id']: q for q in survey['questions']}
        document = {**lookup, 'schema_version': 1, 'content_version': 1, 'anonymous': body.anonymous,
            'repeat_allowed': survey['allow_repeat'], 'request_digest': digest,
            'accepted_at': accepted, 'created_at': server_time(request), 'path': result['path'],
            'answers': [{'question_id': qid, 'value': answer_to_bson(question_map[qid], value)} for qid, value in result['active_answers'].items() if value is not None]}
        if len(BSON.encode(document)) > LIMITS['document_bytes']:
            raise Problem(413, 'DOCUMENT_TOO_LARGE', '文档不能超过8MiB')
        try:
            database.survey_responses.insert_one(document)
        except DuplicateKeyError:
            existing = database.survey_responses.find_one(lookup)
            if existing:
                return receipt(existing)
            if not survey['allow_repeat'] and database.survey_responses.find_one({'survey_id': survey['_id'], 'respondent_id': account['_id'], 'repeat_allowed': False}):
                raise Problem(409, 'ALREADY_SUBMITTED', '此问卷每个账号只能填写一次')
            raise
        response.status_code = 201
        return receipt(document)

    @app.get('/api/v1/surveys/{sid}/statistics')
    def stats(sid: str, account=Depends(user), database=Depends(db)):
        return statistics(database, owned(database, sid, account))

    @app.get('/api/v1/surveys/{sid}/questions/{qid}/statistics')
    def question_stats(sid: str, qid: str, account=Depends(user), database=Depends(db)):
        result = statistics(database, owned(database, sid, account))
        for q in result['questions']:
            if q['id'] == qid:
                return q
        raise Problem(404, 'NOT_FOUND', '题目不存在')

    @app.get('/api/v1/surveys/{sid}/questions/{qid}/contents')
    def question_contents(sid: str, qid: str, offset: int = Query(0, ge=0, le=LIMITS['offset']), limit: int = Query(20, ge=1, le=100), account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        question = next((q for q in survey['questions'] if q['id'] == qid), None)
        if question is None:
            raise Problem(404, 'NOT_FOUND', '题目不存在')
        if question['kind'] not in {'text', 'number'}:
            raise Problem(422, 'CONTENTS_NOT_SUPPORTED', '选择题请查看选项统计')
        return contents(database, survey, question, offset, limit)

    def family_view(doc, uid=None, database=None):
        is_owner = uid is None or doc['owner_id'] == uid
        result = public({k: doc[k] for k in ['_id', 'label', 'revision', 'created_at', 'updated_at']})
        result['role'] = 'owner' if is_owner else 'shared'
        if is_owner and database is not None:
            result['shared_with'] = sorted(x['username'] for x in database.survey_users.find(
                {'_id': {'$in': doc['shared_with']}}, {'username': 1}))
        return result

    def version_view(doc):
        result = public({k: doc[k] for k in ['_id', 'question_id', 'version_no', 'revision', 'state',
            'parent_version_id', 'operation', 'restored_from_version_id', 'payload', 'payload_hash',
            'created_at', 'updated_at', 'sealed_at']})
        result['seal_issues'] = catalog.issues(doc['payload']) if doc['state'] == 'draft' else []
        return result

    @app.post('/api/v1/questions')
    def create_question(body: QuestionCreate, request: Request, response: Response, account=Depends(user), database=Depends(db)):
        doc, created = catalog.create_family(database, account['_id'], body, server_time(request))
        response.status_code = 201 if created else 200
        return family_view(doc)

    @app.get('/api/v1/questions')
    def questions(scope: Literal['owned', 'shared'] = 'owned', offset: int = Query(0, ge=0, le=LIMITS['offset']),
                  limit: int = Query(20, ge=1, le=100), account=Depends(user), database=Depends(db)):
        query = {'owner_id': account['_id']} if scope == 'owned' else {'shared_with': account['_id'], 'owner_id': {'$ne': account['_id']}}
        rows = database.survey_questions.find(query).sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit)
        return {'items': [family_view(row, account['_id']) for row in rows], 'total': database.survey_questions.count_documents(query)}

    @app.get('/api/v1/questions/{qid}')
    def get_question(qid: str, account=Depends(user), database=Depends(db)):
        return family_view(catalog.family(database, oid(qid), account['_id'], use=True), account['_id'], database)

    @app.patch('/api/v1/questions/{qid}')
    def rename_question(qid: str, body: QuestionLabel, request: Request, account=Depends(user), database=Depends(db)):
        doc = catalog.family(database, oid(qid), account['_id'])
        return family_view(catalog.cas_document(database.survey_questions, doc, body.revision,
            {'label': body.label}, server_time(request)), account['_id'], database)

    @app.put('/api/v1/questions/{qid}/sharing')
    def share_question(qid: str, body: QuestionSharing, request: Request, account=Depends(user), database=Depends(db)):
        doc = catalog.family(database, oid(qid), account['_id'])
        names = [username(x) for x in body.usernames]
        accounts = list(database.survey_users.find({'username': {'$in': names}}, {'username': 1}))
        if len(set(names)) != len(names) or len(accounts) != len(names) or account['username'] in names:
            raise Problem(422, 'INVALID_SHARING', '共享名单需为不重复、已存在的其他用户')
        changes = {'shared_with': sorted(x['_id'] for x in accounts)}
        return family_view(catalog.cas_document(database.survey_questions, doc, body.revision,
            changes, server_time(request)), account['_id'], database)

    @app.post('/api/v1/questions/{qid}/versions')
    def create_question_version(qid: str, body: VersionCreate, request: Request, response: Response,
                                account=Depends(user), database=Depends(db)):
        doc, created = catalog.create_version(database, oid(qid), account['_id'], body, server_time(request))
        response.status_code = 201 if created else 200
        return version_view(doc)

    @app.get('/api/v1/questions/{qid}/versions')
    def question_versions(qid: str, offset: int = Query(0, ge=0, le=LIMITS['offset']),
                          limit: int = Query(20, ge=1, le=100), account=Depends(user), database=Depends(db)):
        doc = catalog.family(database, oid(qid), account['_id'], use=True)
        query = {'question_id': oid(qid)}
        if doc['owner_id'] != account['_id']:
            query['state'] = 'sealed'
        rows = database.survey_question_versions.find(query).sort('version_no', -1).skip(offset).limit(limit)
        return {'items': [version_view(row) for row in rows], 'total': database.survey_question_versions.count_documents(query)}

    @app.get('/api/v1/questions/{qid}/versions/{vid}')
    def get_question_version(qid: str, vid: str, account=Depends(user), database=Depends(db)):
        doc = catalog.family(database, oid(qid), account['_id'], use=True)
        selected = catalog.version(database, oid(qid), oid(vid))
        if doc['owner_id'] != account['_id'] and selected['state'] != 'sealed':
            raise Problem(404, 'NOT_FOUND', '版本不存在')
        return version_view(selected)

    @app.get('/api/v1/questions/{qid}/usages')
    def question_usages(qid: str, request: Request, version_id: str | None = None,
                        offset: int = Query(0, ge=0, le=LIMITS['offset']), limit: int = Query(20, ge=1, le=100),
                        account=Depends(user), database=Depends(db)):
        qid = oid(qid)
        family = database.survey_questions.find_one({'_id': qid})
        own = family is not None and family['owner_id'] == account['_id']
        query = {'questions.source.question_id': qid}
        if not own:
            query['owner_id'] = account['_id']
        referenced = database.survey_surveys.find_one(query, {'_id': 1})
        if not family or (not own and account['_id'] not in family['shared_with'] and not referenced):
            raise Problem(404, 'NOT_FOUND', '题目不存在')
        vid = oid(version_id) if version_id is not None else None
        if vid is not None:
            selected = catalog.version(database, qid, vid)
            if not own and (selected['state'] != 'sealed' or
                    (account['_id'] not in family['shared_with'] and not database.survey_surveys.find_one(
                        {**query, 'questions': {'$elemMatch': {'source.question_id': qid, 'source.question_version_id': vid}}}))):
                raise Problem(404, 'NOT_FOUND', '版本不存在')
            query['questions'] = {'$elemMatch': {'source.question_id': qid, 'source.question_version_id': vid}}
        total = database.survey_surveys.count_documents(query)
        rows = database.survey_surveys.find(query).sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit)
        result, time = [], server_time(request)
        for survey in rows:
            counts = {}
            for question in survey['questions']:
                source = question.get('source', {})
                if source.get('question_id') == qid and (vid is None or source['question_version_id'] == vid):
                    key = source['question_version_id']
                    counts[key] = counts.get(key, 0) + 1
            result.append({'survey_id': str(survey['_id']), 'title': survey['title'], 'state': survey['state'],
                'expired': survey['deadline_at'] is not None and survey['deadline_at'] <= time,
                'versions': [{'version_id': str(key), 'instance_count': value} for key, value in sorted(counts.items())]})
        return {'items': result, 'total': total}

    def bank_view(doc, uid, database):
        result = public({k: doc[k] for k in ['_id', 'title', 'revision', 'created_at', 'updated_at']})
        entries = []
        for x in doc['entries']:
            family = database.survey_questions.find_one({'_id': x['question_id'], '$or': [{'owner_id': uid}, {'shared_with': uid}]})
            selected = database.survey_question_versions.find_one({'_id': x['default_version_id'],
                'question_id': x['question_id'], 'state': 'sealed'}) if family else None
            item = public(x) | {'available': bool(selected)}
            if selected:
                item.update(label=family['label'], version_no=selected['version_no'])
            entries.append(item)
        return {**result, 'entries': entries}

    @app.post('/api/v1/question-banks')
    def create_bank(body: BankCreate, request: Request, response: Response, account=Depends(user), database=Depends(db)):
        doc, created = catalog.create_bank(database, account['_id'], body, server_time(request))
        response.status_code = 201 if created else 200
        return bank_view(doc, account['_id'], database)

    @app.get('/api/v1/question-banks')
    def banks(offset: int = Query(0, ge=0, le=LIMITS['offset']), limit: int = Query(20, ge=1, le=100),
              account=Depends(user), database=Depends(db)):
        query = {'owner_id': account['_id']}
        rows = database.survey_question_banks.find(query).sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit)
        return {'items': [bank_view(x, account['_id'], database) for x in rows], 'total': database.survey_question_banks.count_documents(query)}

    @app.get('/api/v1/question-banks/{bid}')
    def get_bank(bid: str, account=Depends(user), database=Depends(db)):
        return bank_view(catalog.bank(database, oid(bid), account['_id']), account['_id'], database)

    @app.put('/api/v1/question-banks/{bid}')
    def save_bank(bid: str, body: BankUpdate, request: Request, account=Depends(user), database=Depends(db)):
        doc = catalog.bank(database, oid(bid), account['_id'])
        return bank_view(catalog.save_bank(database, doc, account['_id'], body, server_time(request)), account['_id'], database)

    @app.put('/api/v1/questions/{qid}/versions/{vid}/draft')
    def save_question_version(qid: str, vid: str, body: VersionDraft, request: Request,
                              account=Depends(user), database=Depends(db)):
        return version_view(catalog.save_version(database, oid(qid), oid(vid), account['_id'], body.revision,
                                                  body.payload.model_dump(), server_time(request)))

    @app.post('/api/v1/questions/{qid}/versions/{vid}/seal')
    def seal_question_version(qid: str, vid: str, body: Revision, request: Request,
                              account=Depends(user), database=Depends(db)):
        return version_view(catalog.save_version(database, oid(qid), oid(vid), account['_id'], body.revision,
                                                  None, server_time(request), seal=True))

    @app.post('/api/v1/surveys/{sid}/question-imports')
    def import_question(sid: str, body: QuestionImport, request: Request,
                        account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        existing = next((q for q in survey['questions'] if q['id'] == body.instance_id), None)
        if existing is not None:
            source = existing.get('source', {})
            if (source.get('question_id') == oid(body.question_id) and
                    source.get('question_version_id') == oid(body.question_version_id)):
                return view(survey)
            raise Problem(409, 'INSTANCE_CONFLICT', '实例ID已用于其他题目或版本')
        if survey['state'] != 'draft' or body.revision != survey['revision']:
            raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变')
        if len(survey['questions']) >= LIMITS['questions']:
            raise Problem(422, 'QUESTION_LIMIT', '问卷题目数量已达上限')
        catalog.family(database, body.question_id, account['_id'], use=True)
        source_version = catalog.version(database, body.question_id, body.question_version_id)
        if source_version['state'] != 'sealed':
            raise Problem(409, 'VERSION_NOT_SEALED', '请先定稿题目版本')
        # The last permission read is the admission point for this import.
        catalog.family(database, body.question_id, account['_id'], use=True)
        question = {'id': body.instance_id, **deepcopy(source_version['payload']), 'rules': [],
            'source': {'question_id': oid(body.question_id), 'question_version_id': oid(body.question_version_id),
                       'payload_hash': source_version['payload_hash']}}
        catalog.validate_source(database, question)
        questions = survey['questions'] + [question]
        changes = {'questions': questions, 'schema_version': 2,
                   'question_result_grants': catalog.remaining_grants(survey, questions),
                   'updated_at': server_time(request)}
        catalog.size_check({**survey, **changes})
        return update(database, survey, body.revision, changes, 'draft')

    @app.get('/api/v1/surveys/{sid}/questions/{iid}/source')
    def instance_source(sid: str, iid: str, account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        question = next((q for q in survey['questions'] if q['id'] == iid and 'source' in q), None)
        if question is None:
            raise Problem(404, 'NOT_FOUND', '来源不存在')
        catalog.validate_source(database, question)
        source = question['source']
        return version_view(catalog.version(database, source['question_id'], source['question_version_id']))

    @app.put('/api/v1/surveys/{sid}/questions/{iid}/source')
    def change_source(sid: str, iid: str, body: SourceChange, request: Request,
                      account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        existing = next((q for q in survey['questions'] if q['id'] == iid and 'source' in q), None)
        if existing is None:
            raise Problem(404, 'NOT_FOUND', '来源不存在')
        if existing['source']['question_id'] != oid(body.question_id):
            raise Problem(422, 'DIFFERENT_QUESTION_FAMILY', '只能换用同一家族的版本')
        if (survey['revision'] == body.revision + 1 and
                existing['source']['question_version_id'] == oid(body.question_version_id) and not existing['rules']):
            return view(survey)
        if survey['state'] != 'draft' or survey['revision'] != body.revision:
            raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变')
        catalog.family(database, body.question_id, account['_id'], use=True)
        selected = catalog.version(database, body.question_id, body.question_version_id)
        if selected['state'] != 'sealed':
            raise Problem(409, 'VERSION_NOT_SEALED', '请先定稿题目版本')
        replacement = {'id': iid, **deepcopy(selected['payload']), 'rules': [],
            'source': {'question_id': oid(body.question_id), 'question_version_id': selected['_id'],
                       'payload_hash': selected['payload_hash']}}
        catalog.validate_source(database, replacement)
        catalog.family(database, body.question_id, account['_id'], use=True)
        questions = [replacement if q['id'] == iid else q for q in survey['questions']]
        changes = {'questions': questions, 'question_result_grants': catalog.remaining_grants(survey, questions),
                   'updated_at': server_time(request)}
        catalog.size_check({**survey, **changes})
        return update(database, survey, body.revision, changes, 'draft')

    @app.put('/api/v1/surveys/{sid}/question-result-grants/{qid}')
    def change_result_grant(sid: str, qid: str, body: ResultGrant, request: Request,
                            account=Depends(user), database=Depends(db)):
        survey = owned(database, sid, account)
        qid = oid(qid)
        if not any(q.get('source', {}).get('question_id') == qid for q in survey['questions']):
            raise Problem(404, 'NOT_FOUND', '本卷没有该来源题目')
        family = database.survey_questions.find_one({'_id': qid})
        if not family:
            raise Problem(422, 'INVALID_SOURCE', '来源题目不存在')
        if body.enabled and family['owner_id'] == account['_id']:
            raise Problem(422, 'OWN_QUESTION_GRANT', '本人题目不需要对外授权')
        grants = catalog.remaining_grants(survey, survey['questions'])
        grant = next(g for g in grants if g['question_id'] == qid)
        if survey['revision'] == body.revision + 1 and grant['enabled'] == body.enabled:
            return view(survey)
        if survey['revision'] != body.revision:
            raise Problem(409, 'REVISION_OR_STATE_CONFLICT', '状态或版本已改变')
        if survey['state'] != 'draft' and body.enabled:
            raise Problem(409, 'GRANT_FROZEN', '发布后仅可撤回，不能开启或重新开启授权')
        grant['enabled'] = body.enabled
        return update(database, survey, body.revision,
            {'question_result_grants': grants, 'updated_at': server_time(request)}, survey['state'])

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get('/')
    def home():
        return RedirectResponse('/surveys', status_code=303)

    def page(request, name, sid=''):
        return templates.TemplateResponse(request=request, name='page.html', context={'page': name, 'sid': sid, 'limits': LIMITS})

    @app.get('/register')
    @app.get('/login')
    def auth_page(request: Request):
        return page(request, request.url.path[1:])

    @app.get('/questions')
    def catalog_page(request: Request):
        return page(request, 'catalog')

    @app.get('/question-banks')
    def banks_page(request: Request):
        return page(request, 'banks')

    @app.get('/surveys')
    def survey_page(request: Request):
        return page(request, 'list')

    @app.get('/surveys/{sid}/edit')
    def edit_page(sid: str, request: Request):
        return page(request, 'edit', sid)

    @app.get('/surveys/{sid}/manage')
    def manage_page(sid: str, request: Request):
        return page(request, 'manage', sid)

    @app.get('/surveys/{sid}/statistics')
    def stats_page(sid: str, request: Request):
        return page(request, 'statistics', sid)

    @app.get('/survey/{sid}')
    def fill_page(sid: str, request: Request):
        return page(request, 'fill', sid)

    return app
