from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import re
import secrets

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
        return {'status': 'ready', 'stage': 'P4', 'work_package': 'W-009', 'schema_version': 1}

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
        changes['questions'] = questions_to_bson(changes['questions'])
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
        time = server_time(request)
        deadline(survey['deadline_at'], time)
        return update(database, survey, body.revision, {'state': 'published', 'content_version': 1,
                      'published_at': time, 'updated_at': time}, 'draft')

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
        return {'id': sid, 'title': survey['title'], 'description': survey['description'],
                'questions': public(survey['questions']), 'content_version': survey['content_version'],
                'allow_anonymous': survey['allow_anonymous'], 'allow_repeat': survey['allow_repeat'],
                'accepting': accepting, 'deadline_at': public(survey['deadline_at']),
                'already_submitted': database.survey_responses.find_one({'survey_id': survey['_id'], 'respondent_id': account['_id']}, {'_id': 1}) is not None}

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
