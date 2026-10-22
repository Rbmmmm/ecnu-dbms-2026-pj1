"""Create or restore synthetic v1 fixtures in a new, strictly guarded local database."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from bson import json_util, ObjectId, Decimal128
from pymongo import MongoClient
from app.config import guard_test_database
from app.db import VALIDATORS, INDEXES, initialize
from tests import fixtures_f01 as f1, fixtures_f02 as f2

COLLECTIONS = ['survey_users', 'survey_surveys', 'survey_responses']


def digest(value):
    return hashlib.sha256(json_util.dumps(value, sort_keys=True, json_options=json_util.CANONICAL_JSON_OPTIONS).encode()).hexdigest()


def dump(db, sessions=False):
    names = COLLECTIONS + (['survey_sessions'] if sessions else [])
    return {name: list(db[name].find().sort('_id', 1)) for name in names}


def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def start(root, port, log):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SURVEY_', 'PYTHON'))}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
                                '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
                               cwd=root, env=env, stdout=log, stderr=log)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('clean application failed to start')
        try:
            if httpx.get(f'http://127.0.0.1:{port}/health', trust_env=False, timeout=.5).status_code == 200:
                return process
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError('clean application readiness timeout')


def call(client, method, path, expected=200, **kwargs):
    response = client.request(method, '/api/v1' + path, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f'{method} {path}: expected {expected}, got {response.status_code}: {response.text}')
    return response.json() if response.content else None


def seed(origin, db):
    clients = []
    for username in ['v1_owner', 'v1_u1', 'v1_u2', 'v1_u3']:
        client = httpx.Client(base_url=origin, headers={'Origin': origin, 'X-Survey-Request': '1'}, trust_env=False)
        password = secrets.token_urlsafe(24)
        call(client, 'POST', '/auth/register', 201, json={'username': username, 'password': password})
        call(client, 'POST', '/auth/login', json={'username': username, 'password': password})
        clients.append(client)
    owner, u1, u2, u3 = clients

    def publish(title, questions, repeat=True):
        survey = call(owner, 'POST', '/surveys', 201, json={'title': title, 'allow_anonymous': True, 'allow_repeat': repeat})
        sid = survey['id']
        survey = call(owner, 'PUT', f'/surveys/{sid}/draft', json={
            'revision': survey['revision'], 'title': title, 'description': '合成验收样本',
            'allow_anonymous': True, 'allow_repeat': repeat, 'deadline_at': None, 'questions': questions})
        call(owner, 'POST', f'/surveys/{sid}/publish', json={'revision': survey['revision']})
        return sid

    sid = publish('v1 F01 四题型统计', f1.f01_questions())
    rows = [(u1, False, [f1.A, [f1.APPLE, f1.BANANA], '甲', '0']),
            (u1, True, [f1.B, [f1.BANANA], '乙', '10']),
            (u2, True, [f1.A, [f1.APPLE], '丙', '20']),
            (u3, False, [None] * 4), (u2, False, [f1.C, None, None, None])]
    receipts = []
    for client, anonymous, values in rows:
        payload = {'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': anonymous,
                   'answers': dict(zip([f1.Q1, f1.Q2, f1.Q3, f1.Q4], values))}
        receipt = call(client, 'POST', f'/fill/{sid}/responses', 201, json=payload)
        assert call(client, 'POST', f'/fill/{sid}/responses', 200, json=payload) == receipt
        receipts.append(receipt)
        time.sleep(.003)
    jid = publish('v1 F02 分支', f2.f02_questions())
    branches = [{f2.J1: f2.A, f2.J3: '18', f2.J5: None},
                {f2.J1: f2.B, f2.J2: [f2.X, f2.Y], f2.J4: '完成', f2.J5: None}]
    for answers in branches:
        call(u1, 'POST', f'/fill/{jid}/responses', 201, json={
            'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': False, 'answers': answers})
    once = publish('v1 每账号一次', f1.f01_questions(), False)
    payload = {'content_version': 1, 'submission_key': str(uuid4()), 'anonymous': True,
               'answers': {q['id']: None for q in f1.f01_questions()}}
    call(u1, 'POST', f'/fill/{once}/responses', 201, json=payload)
    payload['submission_key'] = str(uuid4())
    rejected = call(u1, 'POST', f'/fill/{once}/responses', 409, json=payload)
    assert rejected['error']['code'] == 'ALREADY_SUBMITTED'
    return owner, clients, {'f01': sid, 'f02': jid, 'once': once}


def observe(owner, db, ids):
    views = {}
    for label, sid in ids.items():
        management = call(owner, 'GET', f'/surveys/{sid}')
        assert management['questions'] == (f2.f02_questions() if label == 'f02' else f1.f01_questions())
        assert management['content_version'] == 1 and management['state'] == 'published'
        assert management['allow_anonymous'] is True
        assert management['allow_repeat'] is (label != 'once')
        stats = call(owner, 'GET', f'/surveys/{sid}/statistics')
        contents = {}
        for q in management['questions']:
            single = call(owner, 'GET', f'/surveys/{sid}/questions/{q["id"]}/statistics')
            assert single == next(item for item in stats['questions'] if item['id'] == q['id'])
            if q['kind'] in {'text', 'number'}:
                first = call(owner, 'GET', f'/surveys/{sid}/questions/{q["id"]}/contents?limit=2')
                items = list(first['items'])
                for offset in range(2, first['total'], 2):
                    items.extend(call(owner, 'GET', f'/surveys/{sid}/questions/{q["id"]}/contents?limit=2&offset={offset}')['items'])
                assert len(items) == first['total']
                for item in items:
                    assert set(item) == ({'value', 'created_at', 'anonymous'} if item['anonymous'] else {'value', 'created_at', 'anonymous', 'username'})
                contents[q['id']] = items
        views[label] = {'management': management, 'statistics': stats, 'contents': contents}
    stats = views['f01']['statistics']
    assert (stats['submission_count'], stats['respondent_count']) == (5, 3)
    q1, q2, q3, q4 = stats['questions']
    assert (q1['submission_count'], q1['respondent_count']) == (4, 2)
    assert [(o['submission_count'], o['respondent_count']) for o in q1['options']] == [(2, 2), (1, 1), (1, 1)]
    assert [o['selection_count'] for o in q2['options']] == [2, 2, 0]
    assert q3['answered_submission_count'] == q4['answered_submission_count'] == 3
    assert q4['average'] == '10' and q4['average_display'] == '10.00'
    assert [i['value'] for i in views['f01']['contents'][f1.Q3]] == ['丙', '乙', '甲']
    assert [i['value'] for i in views['f01']['contents'][f1.Q4]] == ['20', '10', '0']
    docs = list(db.survey_responses.find({'survey_id': ObjectId(ids['f01'])}).sort([('created_at', 1), ('_id', 1)]))
    expected = [{f1.Q1:f1.A, f1.Q2:[f1.APPLE,f1.BANANA], f1.Q3:'甲', f1.Q4:Decimal128('0')},
                {f1.Q1:f1.B, f1.Q2:[f1.BANANA], f1.Q3:'乙', f1.Q4:Decimal128('10')},
                {f1.Q1:f1.A, f1.Q2:[f1.APPLE], f1.Q3:'丙', f1.Q4:Decimal128('20')}, {}, {f1.Q1:f1.C}]
    assert [{a['question_id']:a['value'] for a in d['answers']} for d in docs] == expected
    assert all(d['path'] == [f1.Q1,f1.Q2,f1.Q3,f1.Q4] for d in docs)
    assert [d['anonymous'] for d in docs] == [False, True, True, False, False]
    branches = list(db.survey_responses.find({'survey_id':ObjectId(ids['f02'])}).sort([('created_at',1),('_id',1)]))
    assert [d['path'] for d in branches] == [[f2.J1,f2.J3,f2.J5],[f2.J1,f2.J2,f2.J4,f2.J5]]
    assert [len(d['answers']) for d in branches] == [2,3]
    assert [{a['question_id']:a['value'] for a in d['answers']} for d in branches] == [
        {f2.J1:f2.A,f2.J3:Decimal128('18')}, {f2.J1:f2.B,f2.J2:[f2.X,f2.Y],f2.J4:'完成'}]
    once=list(db.survey_responses.find({'survey_id':ObjectId(ids['once'])}))
    assert len(once)==1 and once[0]['answers']==[]
    assert once[0]['path']==[f1.Q1,f1.Q2,f1.Q3,f1.Q4]
    assert once[0]['anonymous'] is True and once[0]['repeat_allowed'] is False
    assert (views['once']['statistics']['submission_count'],views['once']['statistics']['respondent_count'])==(1,1)
    users={d['_id'] for d in db.survey_users.find()}
    surveys={d['_id']:d for d in db.survey_surveys.find()}
    assert all(s['owner_id'] in users for s in surveys.values())
    assert db.survey_responses.count_documents({}) == 8
    for d in db.survey_responses.find():
        assert d['schema_version'] == d['content_version'] == 1
        assert d['respondent_id'] in users and d['survey_id'] in surveys
        assert d['repeat_allowed'] == surveys[d['survey_id']]['allow_repeat']
    return views


def run(output, restore=None):
    if not __debug__:
        raise ValueError('assertions must be enabled')
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid4().hex
    uri, database = 'mongodb://127.0.0.1:27017/', 'survey_test_' + run_id
    guard_test_database(uri, database, run_id)
    result = {'run_id': run_id, 'date': datetime.now(timezone.utc).isoformat(), 'result': 'failed',
              'mode': 'restore' if restore else 'create', 'synthetic_data_only': True}
    process = None
    clients = []
    with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000) as mongo, tempfile.TemporaryDirectory(prefix='survey-v1-') as folder:
        db = mongo[database]
        if database in mongo.list_database_names():
            raise ValueError('refuse to overwrite an existing database')
        clean = Path(folder)
        shutil.copytree(ROOT/'app', clean/'app', ignore=shutil.ignore_patterns('__pycache__','.DS_Store'))
        (clean/'scripts').mkdir()
        shutil.copy2(ROOT/'scripts/init_db.py', clean/'scripts/init_db.py')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        (clean/'.env').write_text(f'SURVEY_MONGO_URI={uri}\nSURVEY_DB={database}\nSURVEY_ORIGIN={origin}\nSURVEY_SECURE_COOKIE=false\n')
        env = {k:v for k,v in os.environ.items() if not k.startswith(('SURVEY_','PYTHON'))}
        with (output/'application.log').open('w') as log:
            try:
                subprocess.run([sys.executable,'scripts/init_db.py'], cwd=clean, env=env, stdout=log, stderr=log, check=True)
                if restore:
                    archive = json_util.loads(restore.read_text())
                    assert archive['format_version'] == 1
                    assert set(archive['collections']) == set(COLLECTIONS)
                    assert digest(archive['collections']) == archive['data_sha256']
                    for name, docs in archive['collections'].items():
                        db[name].insert_many(docs)
                    ids = archive['survey_ids']
                    account = db.survey_users.find_one({'username':'v1_owner'})
                    token = secrets.token_urlsafe(32)
                    now = datetime.now(timezone.utc)
                    db.survey_sessions.insert_one({'schema_version':1, 'user_id':account['_id'],
                        'token_hash':hashlib.sha256(token.encode()).hexdigest(), 'created_at':now, 'expires_at':now+timedelta(hours=1)})
                    owner = httpx.Client(base_url=origin, trust_env=False)
                    owner.cookies.set('survey_session', token)
                    clients = [owner]
                process = start(clean, port, log)
                result['health'] = httpx.get(origin+'/health',trust_env=False).json()
                if not restore:
                    owner, clients, ids = seed(origin, db)
                before = observe(owner, db, ids)
                all_before = dump(db, sessions=True)
                exported = dump(db)
                process.terminate(); process.wait(timeout=10); process = None
                process = start(clean, port, log)
                after = observe(owner, db, ids)
                assert before == after
                assert digest(all_before) == digest(dump(db, sessions=True))
                if restore:
                    assert before == archive['expected_http']
                    assert digest(exported) == archive['data_sha256']
                openapi = httpx.get(origin+'/openapi.json',trust_env=False).json()
                schema = {'validators':VALIDATORS, 'indexes':{name:[dict(i.document) for i in specs] for name,specs in INDEXES.items()}}
                if restore:
                    assert digest(schema) == archive['schema_sha256']
                    assert digest(openapi) == archive['api_sha256']
                baseline = {'format_version':1, 'synthetic_only':True, 'survey_ids':ids,
                            'collections':exported, 'data_sha256':digest(exported), 'expected_http':before,
                            'schema_sha256':digest(schema), 'api_sha256':digest(openapi)}
                (output/'v1-baseline.json').write_text(json_util.dumps(baseline,ensure_ascii=False,indent=2,json_options=json_util.CANONICAL_JSON_OPTIONS)+'\n')
                json_write(output/'schema-v1.json',schema)
                json_write(output/'openapi-v1.json',openapi)
                json_write(output/'http-observations.json',before)
                # Simulate old/unsupported stored data only in this guarded, disposable database.
                owner_id = db.survey_users.find_one({'username':'v1_owner'})['_id']
                db.survey_users.update_one({'_id':owner_id},{'$set':{'schema_version':2}},bypass_document_validation=True)
                try:
                    try:
                        initialize(db)
                    except RuntimeError as exc:
                        assert str(exc) == 'unsupported schema_version'
                    else:
                        raise AssertionError('unsupported schema was accepted')
                    process.terminate(); process.wait(timeout=10); process = None
                    failed = subprocess.run([sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
                                             '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
                                            cwd=clean, env=env, capture_output=True, text=True, timeout=15)
                    (output/'unknown-schema.log').write_text(failed.stdout + failed.stderr)
                    assert failed.returncode != 0 and 'unsupported schema_version' in failed.stderr
                finally:
                    db.survey_users.update_one({'_id':owner_id},{'$set':{'schema_version':1}},bypass_document_validation=True)
                assert digest(all_before) == digest(dump(db,sessions=True))
                process = start(clean, port, log)
                assert observe(owner, db, ids) == before
                result.update(result='passed', process_restart='passed', restore_comparison='passed' if restore else 'not_applicable',
                    counts={name:len(docs) for name,docs in exported.items()}, data_sha256=digest(exported),
                    schema_sha256=digest(schema), api_sha256=digest(openapi), unknown_schema='initialize and actual application startup rejected',
                    python=sys.version, mongodb=mongo.server_info()['version'], origin=origin,
                    independent_expected={'f01':[5,3],'single_options':[[2,2],[1,1],[1,1]],'multiple':[2,2,0],
                                          'average':'10.00','all_responses':8,'branches':[2,3]})
            except Exception as exc:
                result['failure'] = f'{type(exc).__name__}: {exc}'
                (output/'failure.log').write_text(traceback.format_exc())
            finally:
                if process is not None:
                    process.terminate(); process.wait(timeout=10)
                for client in clients: client.close()
                guard_test_database(uri,database,run_id)
                mongo.drop_database(database)
                result['test_database_cleaned'] = database not in mongo.list_database_names()
                if not result['test_database_cleaned']: result['result']='failed'
                json_write(output/'result.json',result)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--restore',type=Path)
    args=parser.parse_args()
    result=run(args.output.resolve(),args.restore.resolve() if args.restore else None)
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(0 if result['result']=='passed' else 1)
