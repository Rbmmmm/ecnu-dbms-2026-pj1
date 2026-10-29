"""II-W006 independent adversarial checks derived from revision and grant contracts."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import AutoReconnect
from app import question_catalog as catalog
from app import question_statistics as cross
from app.db import inspect_contract
from scripts.upgrade_schema_v2 import upgrade, snapshot, unchanged, structure
from scripts.v1_baseline import observe
from app.main import create_app, COOKIE
from fastapi.testclient import TestClient
from tests.conftest import ORIGIN, HEADERS
from tests.test_stage2_upgrade import old_copy
from tests.test_stage2_catalog import setup, call, account, new_survey, imported, draft_body
from tests.test_stage2_management import share, grant, publish
from tests import fixtures_stage2 as f
from tests.fixtures_stage2_statistics import submit


def evidence(name, result):
    if os.getenv('SURVEY_ACCEPTANCE_EVIDENCE'):
        target = Path(os.environ['SURVEY_ACCEPTANCE_EVIDENCE'])
        target.mkdir(parents=True, exist_ok=True)
        (target / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def test_T2_11_future_revision_cannot_seal_a_stale_payload(context, monkeypatch):
    app, a, db, q, v = setup(context)
    draft = call(a, 'POST', '/questions/' + q['id'] + '/versions',
                 {'creation_key': str(uuid4()), 'operation': 'derive', 'parent_version_id': v['id']}, 201)
    original = catalog.version
    fired = False
    def race(*args, **kwargs):
        nonlocal fired
        seen = original(*args, **kwargs)
        if str(seen['_id']) == draft['id'] and not fired:
            fired = True
            result = db.survey_question_versions.update_one({'_id': seen['_id'], 'revision': 1},
                {'$set': {'payload': catalog.payload_bson(f.AGE_V2)}, '$inc': {'revision': 1}})
            assert result.modified_count == 1
        return seen
    with monkeypatch.context() as m:
        m.setattr(catalog, 'version', race)
        result = a.post(f"/api/v1/questions/{q['id']}/versions/{draft['id']}/seal", json={'revision': 2})
    evidence('future-revision-seal', {'status': result.status_code, 'body': result.json()})
    assert result.status_code == 409
    saved = db.survey_question_versions.find_one({'_id': ObjectId(draft['id'])})
    assert saved['state'] == 'draft' and saved['revision'] == 2
    assert catalog.wire(saved['payload']) == f.AGE_V2


def test_T2_11_future_survey_revision_cannot_erase_concurrent_import(context, monkeypatch):
    app, a, db, q, v = setup(context)
    survey = new_survey(a)
    body = draft_body(survey); body['revision'] += 1
    original = catalog.protect_draft
    saved_import = []
    def race(database, seen, questions):
        if not saved_import:
            saved_import.append(imported(a, survey, q, v, f.A1))
        return original(database, seen, questions)
    with monkeypatch.context() as m:
        m.setattr(catalog, 'protect_draft', race)
        result = a.put('/api/v1/surveys/' + survey['id'] + '/draft', json=body)
    evidence('future-revision-survey', {'status': result.status_code, 'body': result.json()})
    assert result.status_code == 409
    current = call(a, 'GET', '/surveys/' + survey['id'])
    assert current == saved_import[0]
    assert current['schema_version'] == 2 and len(current['questions']) == 1


@pytest.mark.parametrize('action', ['validator', 'index', 'collection'])
@pytest.mark.parametrize('moment', ['before', 'after'])
def test_T2_10_11_failed_structural_operation_resumes_exact_journal(old_copy, tmp_path, monkeypatch, action, moment):
    db, settings, archive, token = old_copy
    before = snapshot(db); journal_path = tmp_path / 'failure-upgrade.json'
    original = {'validator': Database.command, 'index': Collection.create_indexes,
                'collection': Database.create_collection}[action]
    fired = False
    def fault(obj, *args, **kwargs):
        nonlocal fired
        target_db = obj.database.name if isinstance(obj, Collection) else obj.name
        applicable = target_db == db.name and (action != 'validator' or (args and args[0] == 'collMod'))
        if applicable and not fired:
            fired = True
            if moment == 'after': original(obj, *args, **kwargs)
            raise AutoReconnect('II-W006 injected ' + moment + ' structural operation')
        return original(obj, *args, **kwargs)
    cls, method = (Database, 'command') if action == 'validator' else (Collection, 'create_indexes') if action == 'index' else (Database, 'create_collection')
    with monkeypatch.context() as m:
        m.setattr(cls, method, fault)
        with pytest.raises(AutoReconnect): upgrade(db, journal_path)
    failed = json.loads(journal_path.read_text())
    assert failed['result'] == 'failed' and fired
    last = failed['steps'][-1]
    assert last['result'] == 'failed'
    assert structure(db) == last['structure_before' if moment == 'before' else 'expected_structure']
    assert snapshot(db) == before
    resumed = upgrade(db, journal_path)
    assert resumed['result'] == 'passed' and len(resumed['attempts']) == 2
    assert inspect_contract(db) and unchanged(before, snapshot(db), datetime.now(timezone.utc))['session_ttl_removed'] == []
    count = len(resumed['steps'])
    assert len(upgrade(db, journal_path)['steps']) == count
    with TestClient(create_app(settings), base_url=ORIGIN, headers=HEADERS) as owner:
        owner.cookies.set(COOKIE, token)
        assert observe(owner, db, archive['survey_ids']) == archive['expected_http']
    assert snapshot(db) == before
    evidence('upgrade-' + action + '-' + moment, {'failed': failed, 'recovered': resumed, 'legacy_http_equal': True})


@pytest.mark.parametrize('endpoint', ['statistics', 'distribution'])
def test_T2_11_all_grant_batches_rechecked_before_return(context, monkeypatch, endpoint):
    app, a, db, q, v = setup(context)
    b = account(app, 'many_grants')
    share(a, q, ['many_grants'])
    surveys = []
    for _ in range(101):
        survey = publish(b, grant(b, imported(b, new_survey(b), q, v, f.B1), q, True))
        submit(b, survey, {f.B1: '18'})
        surveys.append(survey)
    url = '/questions/' + q['id'] + ('/statistics' if endpoint == 'statistics' else '/versions/' + v['id'] + '/distribution')
    first = call(a, 'GET', url)
    g = first['groups'][0] if endpoint == 'statistics' else first
    assert [g[k] for k in ['included_survey_count', 'included_instance_count', 'answer_count', 'answered_submission_count', 'respondent_count']] == [101, 101, 101, 101, 1]
    original = cross.verify_grants
    def race(database, revisions):
        assert len(revisions) == 101
        final_id = list(revisions)[100]
        survey = next(s for s in surveys if s['id'] == str(final_id))
        grant(b, survey, q, False)
        return original(database, revisions)
    with monkeypatch.context() as m:
        m.setattr(cross, 'verify_grants', race)
        rejected = a.get('/api/v1' + url)
    assert rejected.status_code == 409
    assert rejected.json()['error']['code'] == 'STATISTICS_SCOPE_CHANGED'
    after = call(a, 'GET', url)
    g = after['groups'][0] if endpoint == 'statistics' else after
    assert [g[k] for k in ['included_survey_count', 'included_instance_count', 'answer_count', 'answered_submission_count', 'respondent_count']] == [100, 100, 100, 100, 1]
    assert g['average_display'] == '18.00'
    assert call(a, 'GET', '/questions/' + q['id'] + '/usages')['total'] == 101
    evidence('grant-second-batch-' + endpoint, {'before': first, 'rejection': rejected.json(), 'after': after})


@pytest.mark.parametrize('state', ['interrupted', 'completed'])
def test_T2_10_missing_backup_never_rebases_an_existing_journal(old_copy, tmp_path, state):
    from app.db import VALIDATORS, INDEXES
    db, settings, archive, token = old_copy
    journal_path = tmp_path / 'original-journal.json'
    original = snapshot(db)
    if state == 'interrupted':
        with pytest.raises(RuntimeError, match='injected interruption'):
            upgrade(db, journal_path, fail_after=1)
    else:
        upgrade(db, journal_path)
    backup = journal_path.with_suffix('.backup.json')
    backup.unlink()
    # Complete the known v2 structures outside this journal; knowing v2 is not permission to rebase.
    for key, validator in VALIDATORS.items():
        name = 'survey_' + key
        if name not in db.list_collection_names():
            db.create_collection(name, validator=validator, validationLevel='strict', validationAction='error')
        else:
            db.command('collMod', name, validator=validator, validationLevel='strict', validationAction='error')
        db[name].create_indexes(INDEXES[key])
    before_structure = structure(db)
    with pytest.raises(RuntimeError, match='backup'):
        upgrade(db, journal_path)
    assert structure(db) == before_structure and snapshot(db) == original
    assert not backup.exists()
    evidence('missing-backup-' + state, json.loads(journal_path.read_text()))


def test_T2_10_preflight_failure_without_snapshot_can_be_corrected(old_copy, tmp_path):
    db, settings, archive, token = old_copy
    journal_path = tmp_path / 'preflight.json'
    before = snapshot(db)
    db.survey_surveys.create_index('title', name='unexpected')
    with pytest.raises(RuntimeError): upgrade(db, journal_path)
    assert not journal_path.with_suffix('.backup.json').exists()
    db.survey_surveys.drop_index('unexpected')
    assert upgrade(db, journal_path)['result'] == 'passed'
    assert snapshot(db) == before
