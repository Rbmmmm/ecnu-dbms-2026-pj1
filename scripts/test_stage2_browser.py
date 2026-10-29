"""II-W003 browser entry; preserves the original four-suite runner unchanged."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bson import BSON, ObjectId
from pymongo import MongoClient
from app.config import guard_test_database
from scripts.test_browser import start, stop


def all_fingerprints(db):
    return {n: {'count': db[n].count_documents({}), 'sha256': hashlib.sha256(
        b''.join(BSON.encode(d) for d in db[n].find().sort('_id', 1))).hexdigest()}
        for n in sorted(db.list_collection_names())}


def main():
    run = uuid4().hex
    uri, name = 'mongodb://127.0.0.1:27017/', 'survey_test_' + run
    guard_test_database(uri, name, run)
    output = ROOT / 'artifacts' / ('stage2-browser-' + run)
    output.mkdir()
    report = {'date': datetime.now(timezone.utc).isoformat(), 'run_id': run, 'result': 'failed',
              'browser_plugin': 'not available; existing Playwright workflow', 'checks': {}}
    process = None
    with MongoClient(uri, tz_aware=True) as mongo:
        assert name not in mongo.list_database_names()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port)
        env = {**os.environ, 'SURVEY_TEST_URI': uri, 'SURVEY_TEST_DB': name, 'SURVEY_TEST_RUN_ID': run,
               'SURVEY_TEST_ORIGIN': origin, 'SURVEY_EVIDENCE_DIR': str(output)}
        report['origin'] = origin
        try:
            with (output / 'server.log').open('w') as log:
                process = start(env, port, log)
                with (output / 'browser.log').open('w') as browser_log:
                    result = subprocess.run([env['SURVEY_NODE'], str(ROOT / 'tests/browser_stage2.cjs')],
                        cwd=ROOT, env=env, stdout=browser_log, stderr=subprocess.STDOUT, timeout=240)
                assert result.returncode == 0, 'browser failed; see browser.log'
                browser = json.loads((output / 'browser-stage2.json').read_text())
                assert browser['checks'] and all(v == 'passed' for v in browser['checks'].values())
                db = mongo[name]
                surveys = [db.survey_surveys.find_one({'_id': ObjectId(sid)}) for sid in browser['survey_ids']]
                assert all(s['schema_version'] == 2 and s['state'] == 'published' for s in surveys)
                assert surveys[0]['questions'][1]['source'] == surveys[1]['questions'][1]['source']
                assert surveys[0]['questions'][1]['id'] != surveys[1]['questions'][1]['id']
                assert all(s['questions'][1]['validation']['max_value'].to_decimal() == 120 for s in surveys)
                for s, age in zip(surveys, ['0', '18']):
                    rows = list(db.survey_responses.find({'survey_id': s['_id']}))
                    assert len(rows) == 1 and rows[0]['answers'][1]['value'].to_decimal() == int(age)
                    assert rows[0]['path'] == [q['id'] for q in s['questions']]
                report['checks']['real_mongodb_snapshots_and_answers'] = 'passed'
                before = all_fingerprints(db)
                stop(process); process = start(env, port, log)
                assert all_fingerprints(db) == before
                report['checks']['seven_collection_restart'] = 'passed'
                report['collections'] = before
                report['browser'] = browser
                report['indexes'] = {n: db[n].index_information() for n in db.list_collection_names()}
                report['result'] = 'passed'
        except BaseException as exc:
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            stop(process)
            guard_test_database(uri, name, run)
            mongo.drop_database(name)
            report['checks']['cleanup'] = 'passed' if name not in mongo.list_database_names() else 'failed'
            if report['checks']['cleanup'] != 'passed': report['result'] = 'failed'
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({'result': report['result'], 'evidence': str(output), 'failure': report.get('failure')}, ensure_ascii=False))
    return report['result'] != 'passed'


if __name__ == '__main__':
    sys.exit(main())
