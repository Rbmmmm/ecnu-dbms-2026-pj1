"""II-W006 real import recovery and choice-version editing checks in an isolated database."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
from app.config import Settings, guard_test_database
from app.db import inspect_contract
from app.main import create_app
from scripts.test_browser import start, stop
from scripts.test_stage2_browser import all_fingerprints
from tests.conftest import ORIGIN, HEADERS
from tests.test_stage2_catalog import setup
from tests.test_stage2_management import bank, put_bank, entry
from tests.fixtures_stage2_statistics import SINGLE


def main():
    run = uuid4().hex; uri = 'mongodb://127.0.0.1:27017/'; name = 'survey_test_' + run
    guard_test_database(uri, name, run)
    output = Path(os.getenv('SURVEY_ACCEPTANCE_OUTPUT', str(ROOT / 'artifacts' / ('acceptance-browser-' + run)))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'date': datetime.now(timezone.utc).isoformat(), 'run_id': run, 'result': 'failed', 'checks': {}}
    process = None
    with MongoClient(uri, tz_aware=True) as mongo:
        assert name not in mongo.list_database_names()
        try:
            app = create_app(Settings(uri, name, ORIGIN, testing=True, run_id=run))
            with TestClient(app, base_url=ORIGIN, headers=HEADERS) as seed:
                _, owner, db, q, v = setup((app, seed, app.state.db), SINGLE)
                lib = put_bank(owner, bank(owner), [entry(q, v)])
                (output / 'fixture.json').write_text(json.dumps({'question': q, 'version': v, 'bank': lib}))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            origin = 'http://127.0.0.1:' + str(port); report['origin'] = origin
            env = {**os.environ, 'SURVEY_TEST_URI': uri, 'SURVEY_TEST_DB': name, 'SURVEY_TEST_RUN_ID': run,
                   'SURVEY_TEST_ORIGIN': origin, 'SURVEY_EVIDENCE_DIR': str(output)}
            with (output / 'server.log').open('w') as log:
                process = start(env, port, log)
                with (output / 'editor-save.log').open('w') as browser_log:
                    done = subprocess.run([env['SURVEY_NODE'], str(ROOT / 'tests/browser_editor_save.cjs')],
                        cwd=ROOT, env=env, stdout=browser_log, stderr=subprocess.STDOUT, timeout=180)
                assert done.returncode == 0, 'editor save/publish browser failed; see editor-save.log'
                editor = json.loads((output / 'browser-editor-save.json').read_text()); report['editor'] = editor
                assert editor['checks'] and all(x == 'passed' for x in editor['checks'].values())
                for expected in editor['surveys']:
                    survey = mongo[name].survey_surveys.find_one({'_id': ObjectId(expected['id'])})
                    assert survey['title'] == expected['title'] and survey['state'] == expected['state']
                    assert survey['questions'][0]['title'] == expected['question_title']
                    assert survey['schema_version'] == expected['schema_version']
                report['checks']['editor_save_publish_real_bson'] = 'passed'
                with (output / 'browser.log').open('w') as browser_log:
                    done = subprocess.run([env['SURVEY_NODE'], str(ROOT / 'tests/browser_acceptance.cjs')],
                        cwd=ROOT, env=env, stdout=browser_log, stderr=subprocess.STDOUT, timeout=300)
                assert done.returncode == 0, 'browser failed; see browser.log'
                browser = json.loads((output / 'browser-acceptance.json').read_text()); report['browser'] = browser
                assert browser['checks'] and all(x == 'passed' for x in browser['checks'].values())
                db = mongo[name]; inspect_contract(db)
                for sid in browser['surveys']:
                    survey = db.survey_surveys.find_one({'_id': ObjectId(sid)})
                    assert len(survey['questions']) == 1 and survey['schema_version'] == 2
                    assert survey['questions'][0]['source']['question_version_id'] == ObjectId(v['id'])
                original = db.survey_question_versions.find_one({'_id': ObjectId(v['id'])})
                assert [x['text'] for x in original['payload']['options']] == ['A', 'B', 'C', 'D']
                final = db.survey_question_versions.find_one({'_id': ObjectId(browser['derived_version'])})
                assert [x['text'] for x in final['payload']['options']] == ['C', 'A', 'E']
                assert final['state'] == 'sealed' and final['parent_version_id'] == original['_id']
                report['checks']['real_bson_imports_and_independent_choice_versions'] = 'passed'
                before = all_fingerprints(db); stop(process); process = start(env, port, log)
                assert all_fingerprints(db) == before
                report['checks']['seven_collections_process_restart'] = 'passed'
                report['result'] = 'passed'
        except BaseException as exc:
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            stop(process); guard_test_database(uri, name, run); mongo.drop_database(name)
            report['checks']['cleanup'] = 'passed' if name not in mongo.list_database_names() else 'failed'
            if report['checks']['cleanup'] != 'passed': report['result'] = 'failed'
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({'result': report['result'], 'evidence': str(output), 'failure': report.get('failure')}, ensure_ascii=False))
    return report['result'] != 'passed'


if __name__ == '__main__':
    sys.exit(main())
