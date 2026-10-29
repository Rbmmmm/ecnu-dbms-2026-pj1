from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from uuid import uuid4
from bson import BSON, ObjectId, json_util
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import OperationFailure
import pytest
from app.config import Settings, guard_test_database
from app.db import V1_VALIDATORS, V1_INDEXES, VALIDATORS, INDEXES, initialize, inspect_contract
from app.main import create_app, COOKIE
from scripts.upgrade_schema_v2 import upgrade, snapshot, unchanged, fingerprints
from scripts.v1_baseline import digest, observe
from tests.conftest import ORIGIN, HEADERS

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / '提交文档/第一阶段/附件/v1-baseline.json'


@pytest.fixture
def old_copy():
    run = uuid4().hex
    uri, name = 'mongodb://127.0.0.1:27017/', 'survey_test_' + run
    guard_test_database(uri, name, run)
    client = MongoClient(uri, tz_aware=True)
    assert name not in client.list_database_names()
    db = client[name]
    archive = json_util.loads(ARCHIVE.read_text())
    assert archive['synthetic_only'] and archive['format_version'] == 1
    assert digest(archive['collections']) == archive['data_sha256']
    for key, validator in V1_VALIDATORS.items():
        c = db.create_collection('survey_' + key, validator=validator, validationLevel='strict', validationAction='error')
        c.create_indexes(V1_INDEXES[key])
        if 'survey_' + key in archive['collections']:
            c.insert_many(archive['collections']['survey_' + key])
    import hashlib
    owner = db.survey_users.find_one({'username': 'v1_owner'})
    token = 'stage2-upgrade-synthetic-session'
    db.survey_sessions.insert_one({'schema_version': 1, 'user_id': owner['_id'],
        'token_hash': hashlib.sha256(token.encode()).hexdigest(),
        'created_at': datetime.now(timezone.utc), 'expires_at': datetime(2100, 1, 1, tzinfo=timezone.utc)})
    try:
        yield db, Settings(uri, name, ORIGIN, testing=True, run_id=run), archive, token
    finally:
        guard_test_database(uri, name, run)
        client.drop_database(name)
        client.close()


def structure(db):
    return {n: {'options': db[n].options(), 'indexes': db[n].index_information()}
            for n in db.list_collection_names()}


def test_T2_10_archived_copy_explicit_upgrade_and_legacy_http(old_copy, tmp_path):
    db, settings, archive, token = old_copy
    before = snapshot(db); old_structure = structure(db)
    with pytest.raises(RuntimeError, match='explicit v2 upgrade'):
        initialize(db)
    assert structure(db) == old_structure and snapshot(db) == before
    journal = upgrade(db, tmp_path / 'upgrade.json')
    assert journal['result'] == 'passed'
    assert snapshot(db) == before
    assert set(inspect_contract(db)) == {'survey_' + key for key in VALIDATORS}
    assert len(db.list_collection_names()) == 7
    again = upgrade(db, tmp_path / 'upgrade.json')
    assert len(again['steps']) == len(journal['steps']) and snapshot(db) == before
    with TestClient(create_app(settings), base_url=ORIGIN, headers=HEADERS) as owner:
        owner.cookies.set(COOKIE, token)
        assert observe(owner, db, archive['survey_ids']) == archive['expected_http']
        for sid in archive['survey_ids'].values():
            fill = owner.get('/api/v1/fill/' + sid).json()
            assert 'result_sharing_notice' not in fill
            assert all('source' not in q for q in fill['questions'])
        result = owner.post('/api/v1/surveys', json={'title': '原生仍为1'})
        assert result.status_code == 201 and result.json()['schema_version'] == 1
        assert 'question_result_grants' not in result.json()
        db.survey_surveys.delete_one({'_id': ObjectId(result.json()['id'])})
        # Same old endpoint fields and constraints; only Question.source is added.
        old_api = json.loads((ROOT / '提交文档/第一阶段/附件/openapi-v1.json').read_text())
        new_api = owner.get('/openapi.json').json()
        allowed = {'/api/v1/questions', '/api/v1/questions/{qid}', '/api/v1/questions/{qid}/versions',
            '/api/v1/questions/{qid}/versions/{vid}', '/api/v1/questions/{qid}/versions/{vid}/draft',
            '/api/v1/questions/{qid}/versions/{vid}/seal', '/api/v1/surveys/{sid}/question-imports',
            '/api/v1/surveys/{sid}/questions/{iid}/source', '/questions',
            '/api/v1/questions/{qid}/sharing', '/api/v1/questions/{qid}/usages',
            '/api/v1/question-banks', '/api/v1/question-banks/{bid}', '/question-banks',
            '/api/v1/surveys/{sid}/question-result-grants/{qid}',
            '/api/v1/questions/{qid}/statistics', '/api/v1/questions/{qid}/versions/{vid}/distribution',
            '/questions/{qid}/statistics'}
        assert set(new_api['paths']) - set(old_api['paths']) == allowed
        for path, spec in old_api['paths'].items():
            assert new_api['paths'][path] == spec
        for name, old in old_api['components']['schemas'].items():
            new = deepcopy(new_api['components']['schemas'][name])
            if name == 'Question':
                assert 'source' not in new.get('required', [])
                new['properties'].pop('source')
            assert new == old
    assert snapshot(db) == before
    with TestClient(create_app(settings), base_url=ORIGIN, headers=HEADERS) as owner:
        owner.cookies.set(COOKIE, token)
        assert observe(owner, db, archive['survey_ids']) == archive['expected_http']
    assert snapshot(db) == before


@pytest.mark.parametrize('fault', ['schema1-source', 'schema1-grant', 'unknown-schema', 'unknown-validator', 'index-conflict', 'extra-index'])
def test_T2_10_unknown_contract_zero_mutation(old_copy, tmp_path, fault):
    db, settings, archive, token = old_copy
    sid = ObjectId(archive['survey_ids']['f01'])
    if fault == 'schema1-source':
        db.survey_surveys.update_one({'_id': sid}, {'$set': {'questions.0.source': {}}})
    if fault == 'schema1-grant':
        db.survey_surveys.update_one({'_id': sid}, {'$set': {'question_result_grants': []}})
    if fault == 'unknown-schema':
        db.survey_surveys.update_one({'_id': sid}, {'$set': {'schema_version': 99}}, bypass_document_validation=True)
    if fault == 'unknown-validator': db.command('collMod', 'survey_surveys', validator={})
    if fault == 'index-conflict':
        db.survey_surveys.create_index('title', name='source_question')
    if fault == 'extra-index': db.survey_surveys.create_index('title', name='unrecognized_index')
    before, contract = snapshot(db), structure(db)
    with pytest.raises(RuntimeError): upgrade(db, tmp_path / 'upgrade.json')
    with pytest.raises(RuntimeError): initialize(db)
    assert snapshot(db) == before and structure(db) == contract
    journal = json.loads((tmp_path / 'upgrade.json').read_text())
    assert journal['result'] == 'failed' and journal['steps'] == []


def test_T2_10_11_upgrade_interruption_and_resume(old_copy, tmp_path):
    db, settings, archive, token = old_copy
    before = snapshot(db)
    with pytest.raises(RuntimeError, match='injected interruption'):
        upgrade(db, tmp_path / 'upgrade.json', fail_after=4)
    assert snapshot(db) == before
    with pytest.raises(RuntimeError): initialize(db)
    with pytest.raises(RuntimeError, match='intermediate'):
        upgrade(db, tmp_path / 'wrong-journal.json')
    report = upgrade(db, tmp_path / 'upgrade.json')
    assert report['result'] == 'passed' and len(report['attempts']) == 2
    assert snapshot(db) == before
    indexes = db.survey_question_versions.index_information()
    assert indexes['family_version_unique']['unique'] and indexes['family_creation_unique']['unique']
    assert db.survey_surveys.index_information()['source_question']['key'] == [('questions.source.question_id', 1)]
    doc = db.survey_surveys.find_one({})
    for mutate in ['source', 'grant']:
        invalid = deepcopy(doc); invalid['_id'] = ObjectId()
        if mutate == 'source': invalid['questions'][0]['source'] = {}
        else: invalid['question_result_grants'] = []
        with pytest.raises(OperationFailure) as error: db.survey_surveys.insert_one(invalid)
        assert error.value.code == 121


def test_T2_10_session_ttl_rule_is_narrow():
    time = datetime.now(timezone.utc)
    oid = ObjectId()
    before = {n: [] for n in ['survey_users', 'survey_surveys', 'survey_responses', 'survey_sessions']}
    before['survey_sessions'] = [{'_id': oid, 'expires_at': time - timedelta(seconds=1), 'token_hash': 'unchanged'}]
    after = deepcopy(before); after['survey_sessions'] = []
    assert unchanged(before, after, time)['session_ttl_removed'] == [str(oid)]
    before['survey_sessions'][0]['expires_at'] = time + timedelta(days=1)
    with pytest.raises(RuntimeError, match='unexpired'): unchanged(before, after, time)
    after = deepcopy(before); after['survey_sessions'][0]['token_hash'] = 'changed'
    with pytest.raises(RuntimeError, match='session fields'): unchanged(before, after, time)


def test_T2_10_journal_does_not_authorize_unrecorded_known_structures(old_copy, tmp_path):
    db, settings, archive, token = old_copy
    journal_path = tmp_path / 'upgrade.json'
    with pytest.raises(RuntimeError, match='injected interruption'):
        upgrade(db, journal_path, fail_after=1)
    # This is a known v2 index, but this interrupted run has not created it.
    db.survey_surveys.create_indexes([INDEXES['surveys'][1]])
    before, contract = snapshot(db), structure(db)
    with pytest.raises(RuntimeError, match='not a journaled intermediate'):
        upgrade(db, journal_path)
    assert snapshot(db) == before and structure(db) == contract
