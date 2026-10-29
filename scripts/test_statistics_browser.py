"""II-W005 fixed F2 data, real browser, seven-collection inspection and restart."""
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
import httpx
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient
from app.config import Settings, guard_test_database
from app.db import inspect_contract
from app.main import create_app
from scripts.test_browser import start, stop
from scripts.test_stage2_browser import all_fingerprints
from tests.conftest import ORIGIN, HEADERS
from tests import fixtures_stage2 as f
from tests.fixtures_stage2_statistics import dataset, submit
from tests.test_stage2_catalog import new_survey, imported
from tests.test_stage2_management import new_version, publish


def main():
    run = uuid4().hex;uri = 'mongodb://127.0.0.1:27017/';name = 'survey_test_' + run
    guard_test_database(uri, name, run)
    output = Path(os.getenv('SURVEY_STATISTICS_OUTPUT', str(ROOT / 'artifacts' / ('statistics-browser-' + run)))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'date': datetime.now(timezone.utc).isoformat(), 'run_id': run, 'result': 'failed', 'checks': {},
              'browser_plugin': 'not available; existing Playwright and installed Chrome'}
    process = None
    with MongoClient(uri, tz_aware=True) as mongo:
        assert name not in mongo.list_database_names()
        try:
            app = create_app(Settings(uri, name, ORIGIN, testing=True, run_id=run))
            with TestClient(app, base_url=ORIGIN, headers=HEADERS) as seed:
                d = dataset((app, seed, app.state.db))
                v2 = new_version(d['a'], d['q'], d['v'], value=f.AGE_V2)
                v3 = new_version(d['a'], d['q'], d['v'], operation='restore')
                for v, number in [(v2, '40'), (v3, '60')]:
                    s = publish(d['a'], imported(d['a'], new_survey(d['a']), d['q'], v, f.A1))
                    submit(d['c'], s, {f.A1: number})
                fixture = {'question_id': d['q']['id'], 'versions': [d['v']['id'], v2['id'], v3['id']],
                           'surveys': {k: s['id'] for k, s in d['surveys'].items()}}
                (output / 'fixture.json').write_text(json.dumps(fixture, indent=2))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
            origin = 'http://127.0.0.1:' + str(port);report['origin'] = origin
            env = {**os.environ, 'SURVEY_TEST_URI': uri, 'SURVEY_TEST_DB': name, 'SURVEY_TEST_RUN_ID': run,
                   'SURVEY_TEST_ORIGIN': origin, 'SURVEY_EVIDENCE_DIR': str(output)}
            with (output / 'server.log').open('w') as log:
                process = start(env, port, log)
                with (output / 'browser.log').open('w') as browser_log:
                    result = subprocess.run([env['SURVEY_NODE'], str(ROOT / 'tests/browser_statistics.cjs')],
                        cwd=ROOT, env=env, stdout=browser_log, stderr=subprocess.STDOUT, timeout=300)
                assert result.returncode == 0, 'browser failed; see browser.log'
                browser = json.loads((output / 'browser-statistics.json').read_text())
                assert browser['checks'] and all(x == 'passed' for x in browser['checks'].values())
                db = mongo[name];inspect_contract(db)
                s = db.survey_surveys.find_one({'_id': ObjectId(fixture['surveys']['b'])})
                assert s['question_result_grants'][0]['enabled'] is False
                assert s['question_result_grants'][0]['allowed_at_publish'] is True
                rows = list(db.survey_responses.find({'survey_id': {'$in': [ObjectId(x) for x in fixture['surveys'].values()]}}))
                numbers = [(r['_id'], r['respondent_id'], a['value'].to_decimal()) for r in rows
                           for a in r['answers'] if a['question_id'] in {f.A1, f.A2, f.B1}]
                assert len(rows) == 8 and len(numbers) == 7 and sum(x[2] for x in numbers) == 264
                assert len({x[0] for x in numbers}) == 5 and len({x[1] for x in numbers}) == 2
                report['checks']['fixed_bson_answers_grants_and_seven_collection_contract'] = 'passed'
                before = all_fingerprints(db)
                stop(process);process = start(env, port, log)
                assert all_fingerprints(db) == before
                with httpx.Client(base_url=origin, trust_env=False, headers={'Origin': origin, 'X-Survey-Request': '1'}) as c:
                    assert c.post('/api/v1/auth/login', json={'username': 's2_owner', 'password': 'stage2-test-pass'}).status_code == 200
                    groups = c.get('/api/v1/questions/' + fixture['question_id'] + '/statistics').json()['groups']
                    assert [x['average_display'] for x in groups] == ['25.20', '40.00', '60.00']
                report['checks']['seven_collections_restart_and_current_scope_http'] = 'passed'
                report.update(browser=browser, collections=before,
                              indexes={n: db[n].index_information() for n in db.list_collection_names()}, result='passed')
        except BaseException as exc:
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            stop(process);guard_test_database(uri, name, run);mongo.drop_database(name)
            report['checks']['cleanup'] = 'passed' if name not in mongo.list_database_names() else 'failed'
            if report['checks']['cleanup'] != 'passed': report['result'] = 'failed'
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({'result': report['result'], 'evidence': str(output), 'failure': report.get('failure')}, ensure_ascii=False))
    return report['result'] != 'passed'


if __name__ == '__main__':
    sys.exit(main())
