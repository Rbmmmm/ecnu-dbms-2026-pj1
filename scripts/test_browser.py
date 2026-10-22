"""Run the four browser suites against a new, disposable local database."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from bson import BSON
from pymongo import MongoClient
from app.config import guard_test_database

BROWSERS = [
    ('browser.cjs', 'browser.json'),
    ('browser_w007.cjs', 'browser-w007.json'),
    ('browser_w008.cjs', 'browser-w008.json'),
    ('browser_p4.cjs', 'browser-p4.json'),
]
COLLECTIONS = ['survey_users', 'survey_surveys', 'survey_responses', 'survey_sessions']


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def start(env, port, log):
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'scripts.test_server:factory', '--factory',
         '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        until = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=.5) as client:
            while time.monotonic() < until:
                if process.poll() is not None:
                    raise RuntimeError('test application stopped before it was ready')
                try:
                    if client.get(env['SURVEY_TEST_ORIGIN'] + '/health').status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
        raise RuntimeError('test application readiness timed out')
    except BaseException:
        stop(process)
        raise


def fingerprints(database):
    result = {}
    for name in COLLECTIONS:
        digest, count = hashlib.sha256(), 0
        for document in database[name].find().sort('_id', 1):
            digest.update(BSON.encode(document))
            count += 1
        result[name] = {'count': count, 'sha256': digest.hexdigest()}
    return result


def main():
    run_id = uuid4().hex
    uri, database = 'mongodb://127.0.0.1:27017/', 'survey_test_' + run_id
    guard_test_database(uri, database, run_id)
    output = ROOT / 'artifacts' / ('browser-' + run_id)
    output.mkdir(parents=True, exist_ok=False)
    report = {'run_id': run_id, 'date': datetime.now(timezone.utc).isoformat(),
              'result': 'failed', 'suites': {}, 'checks': {}, 'database': database}
    mongo, process, owns_database = None, None, False
    try:
        node = os.getenv('SURVEY_NODE') or shutil.which('node')
        if not node:
            raise RuntimeError('Node.js is required for browser tests')
        mongo = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000)
        if database in mongo.list_database_names():
            raise RuntimeError('refusing to overwrite an existing test database')
        owns_database = True
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port)
        env = {**os.environ, 'SURVEY_TEST_URI': uri, 'SURVEY_TEST_DB': database,
               'SURVEY_TEST_RUN_ID': run_id, 'SURVEY_TEST_ORIGIN': origin,
               'SURVEY_EVIDENCE_DIR': str(output)}
        report['origin'] = origin
        with (output / 'application.log').open('w') as log:
            try:
                process = start(env, port, log)
                for script, result_file in BROWSERS:
                    suite = {'result': 'failed', 'checks': {}}
                    report['suites'][script] = suite
                    with (output / (script + '.log')).open('w') as browser_log:
                        completed = subprocess.run(
                            [node, str(ROOT / 'tests' / script)], cwd=ROOT, env=env,
                            stdout=browser_log, stderr=subprocess.STDOUT, timeout=180)
                    suite['exit_code'] = completed.returncode
                    result_path = output / result_file
                    if result_path.is_file():
                        suite['checks'] = json.loads(result_path.read_text()).get('checks', {})
                    checks = suite['checks']
                    if (completed.returncode or not isinstance(checks, dict) or not checks
                            or any(value != 'passed' for value in checks.values())):
                        raise RuntimeError(script + ' failed; see its log and JSON result')
                    suite['result'] = 'passed'
                before = fingerprints(mongo[database])
                stop(process)
                process = None
                process = start(env, port, log)
                after = fingerprints(mongo[database])
                if before != after:
                    raise RuntimeError('stored BSON changed after application restart')
                report['checks']['application_restart'] = 'passed'
                report['collections'] = after
                report['result'] = 'passed'
            finally:
                stop(process)
                process = None
    except (Exception, KeyboardInterrupt) as exc:
        report['result'] = 'failed'
        report['failure'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        try:
            stop(process)
        except Exception as exc:
            report['result'] = 'failed'
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
        if mongo is not None:
            try:
                if owns_database:
                    guard_test_database(uri, database, run_id)
                    mongo.drop_database(database)
                    if database in mongo.list_database_names():
                        raise RuntimeError('test database cleanup failed')
                    report['checks']['database_cleanup'] = 'passed'
            except Exception as exc:
                report['result'] = 'failed'
                report['cleanup_failure'] = type(exc).__name__ + ': ' + str(exc)
            finally:
                mongo.close()
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'result': report['result'], 'evidence': str(output / 'report.json'),
                      'suites': report['suites'], 'checks': report['checks'],
                      'failure': report.get('failure'), 'cleanup_failure': report.get('cleanup_failure')},
                     ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
