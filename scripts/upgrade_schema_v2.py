"""Explicit v1 -> v2 contract upgrade, currently restricted to isolated local copies.

Stop all writers to the target copy first. This command never changes business
records. Original-version reproduction remains scripts/v1_baseline.py at 3645b07.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bson import BSON, json_util
from pymongo import MongoClient
from app.config import guard_test_database
from app.db import (VALIDATORS, INDEXES, V1_VALIDATORS, V1_INDEXES,
                    check_indexes, check_documents, inspect_contract)

PERMANENT = ['survey_users', 'survey_surveys', 'survey_responses']
OLD = PERMANENT + ['survey_sessions']


def snapshot(db):
    return {name: list(db[name].find().sort('_id', 1)) for name in OLD}


def fingerprints(rows):
    return {name: {'count': len(docs), 'sha256': hashlib.sha256(
        b''.join(BSON.encode(doc) for doc in docs)).hexdigest()} for name, docs in rows.items()}


def unchanged(before, after, ended):
    for name in PERMANENT:
        if [BSON.encode(d) for d in before[name]] != [BSON.encode(d) for d in after[name]]:
            raise RuntimeError('permanent business records changed: ' + name)
    old = {d['_id']: d for d in before['survey_sessions']}
    new = {d['_id']: d for d in after['survey_sessions']}
    if set(new) - set(old):
        raise RuntimeError('unexpected session inserted during upgrade')
    for key, doc in old.items():
        if key in new:
            if BSON.encode(doc) != BSON.encode(new[key]):
                raise RuntimeError('session fields changed during upgrade')
        elif doc['expires_at'] > ended:
            raise RuntimeError('unexpired session removed during upgrade')
    return {'permanent_fields': 'unchanged', 'session_ttl_removed': [str(k) for k in set(old) - set(new)]}


def structure(db):
    value = {name: {'options': db[name].options(), 'indexes': db[name].index_information()}
             for name in db.list_collection_names() if name.startswith('survey_')}
    return json.loads(json.dumps(value))


def verify_journal_structure(db, journal):
    steps = journal.get('steps', [])
    if steps:
        last = steps[-1]
        allowed = [last['structure_after']] if last['result'] == 'passed' else [
            last['structure_before'], last['expected_structure']]
    else:
        allowed = [journal['initial_structure']]
    if structure(db) not in allowed:
        raise RuntimeError('database structure is not a journaled intermediate state')


def preflight(db, allow_intermediate=False):
    names = set(db.list_collection_names())
    expected = {'survey_' + n for n in VALIDATORS}
    existing = {n for n in names if n.startswith('survey_')}
    if existing - expected or not set(OLD) <= existing:
        raise RuntimeError('unknown or incomplete database collections')
    v1 = existing == set(OLD)
    v2 = existing == expected
    for name, validator in VALIDATORS.items():
        collection = 'survey_' + name
        if collection not in existing:
            v2 = False
            continue
        options = db[collection].options()
        if (options.get('validationLevel', 'strict') != 'strict' or options.get('validationAction', 'error') != 'error'
                or set(options) - {'validator', 'validationLevel', 'validationAction'}):
            raise RuntimeError('unknown database options')
        actual = options.get('validator')
        known = [validator] + ([V1_VALIDATORS[name]] if name in V1_VALIDATORS else [])
        if actual not in known:
            raise RuntimeError('unknown validator: ' + collection)
        indexes = db[collection].index_information()
        check_indexes(indexes, INDEXES[name], partial=True)
        if name in V1_INDEXES:
            for spec in V1_INDEXES[name]:
                check_indexes({'_id_': indexes['_id_'], spec.document['name']: indexes.get(spec.document['name'], {})}, [spec])
        if actual != validator or set(indexes) != {'_id_'} | {i.document['name'] for i in INDEXES[name]}:
            v2 = False
        if name in V1_VALIDATORS and (actual != V1_VALIDATORS[name] or set(indexes) != {'_id_'} | {i.document['name'] for i in V1_INDEXES[name]}):
            v1 = False
        if db[collection].find_one({'$nor': [actual]}, {'_id': 1}):
            raise RuntimeError('stored document violates current validator')
    check_documents(db)
    if v1:
        for name in OLD:
            if db[name].find_one({'schema_version': {'$ne': 1}}, {'_id': 1}):
                raise RuntimeError('unsupported schema_version in v1')
    if not v1 and not v2 and not allow_intermediate:
        raise RuntimeError('intermediate contract requires this tool journal')
    return 'v1' if v1 else 'v2' if v2 else 'intermediate'


def upgrade(db, journal_path, fail_after=None):
    journal_path = Path(journal_path)
    resumed = journal_path.exists()
    journal = json.loads(journal_path.read_text()) if resumed else {
        'tool': 'upgrade_schema_v2/1', 'database': db.name, 'steps': [], 'attempts': []}
    if journal.get('tool') != 'upgrade_schema_v2/1' or journal.get('database') != db.name:
        raise RuntimeError('journal belongs to another tool or database')
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    backup = journal_path.with_suffix('.backup.json')
    attempt = {'started_at': datetime.now(timezone.utc).isoformat(), 'result': 'failed'}
    journal['attempts'].append(attempt)

    def save():
        temporary = journal_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(journal_path)

    try:
        if resumed and backup.exists():
            verify_journal_structure(db, journal)
        attempt['contract_before'] = preflight(db, allow_intermediate=resumed and backup.exists())
        if 'initial_structure' not in journal:
            journal['initial_structure'] = structure(db)
        before = snapshot(db)
        if resumed and backup.exists() and journal.get('result') != 'passed':
            original = json_util.loads(backup.read_text())
            unchanged(original, before, datetime.now(timezone.utc))
        elif not backup.exists():
            backup.write_text(json_util.dumps(before, json_options=json_util.CANONICAL_JSON_OPTIONS))
        attempt['before'] = fingerprints(before)
        save()
        performed = 0
        def step(action, fn, collection, validator=None, index=None):
            nonlocal performed
            before_structure = structure(db)
            expected = deepcopy(before_structure)
            if collection not in expected:
                expected[collection] = {'options': {'validator': validator, 'validationLevel': 'strict',
                    'validationAction': 'error'}, 'indexes': {'_id_': {'v': 2, 'key': [['_id', 1]]}}}
            elif validator is not None:
                expected[collection]['options'].update(validator=validator, validationLevel='strict', validationAction='error')
            if index is not None:
                spec = dict(index.document)
                name = spec.pop('name')
                spec['key'] = list(spec['key'].items())
                expected[collection]['indexes'][name] = {'v': 2, **spec}
            expected = json.loads(json.dumps(expected))
            entry = {'action': action, 'result': 'started', 'structure_before': before_structure,
                     'expected_structure': expected}
            journal['steps'].append(entry)
            save()
            try:
                fn()
                after_structure = structure(db)
                if after_structure != expected:
                    raise RuntimeError('structural step produced an unexpected contract')
                entry.update(result='passed', structure_after=after_structure)
            except BaseException as exc:
                entry.update(result='failed', failure=type(exc).__name__ + ': ' + str(exc))
                raise
            finally:
                save()
            performed += 1
            if fail_after == performed:
                raise RuntimeError('injected interruption after structural step ' + str(performed))

        for name, validator in VALIDATORS.items():
            collection = 'survey_' + name
            if collection not in db.list_collection_names():
                step('create ' + collection, lambda c=collection, v=validator: db.create_collection(
                    c, validator=v, validationLevel='strict', validationAction='error'), collection, validator=validator)
            elif db[collection].options()['validator'] != validator:
                step('validator ' + collection, lambda c=collection, v=validator: db.command(
                    'collMod', c, validator=v, validationLevel='strict', validationAction='error'), collection, validator=validator)
            actual = db[collection].index_information()
            for index in INDEXES[name]:
                if index.document['name'] not in actual:
                    step('index ' + collection + '/' + index.document['name'],
                         lambda c=collection, i=index: db[c].create_indexes([i]), collection, index=index)
        inspect_contract(db)
        after = snapshot(db)
        attempt['unchanged'] = unchanged(before, after, datetime.now(timezone.utc))
        attempt.update(after=fingerprints(after), result='passed')
        journal['result'] = 'passed'
    except BaseException as exc:
        attempt['failure'] = type(exc).__name__ + ': ' + str(exc)
        journal['result'] = 'failed'
        if 'before' in locals():
            after = snapshot(db)
            attempt['after'] = fingerprints(after)
            attempt['unchanged'] = unchanged(before, after, datetime.now(timezone.utc))
        raise
    finally:
        attempt['ended_at'] = datetime.now(timezone.utc).isoformat()
        save()
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--journal', required=True, type=Path)
    args = parser.parse_args()
    uri = 'mongodb://127.0.0.1:27017/'
    guard_test_database(uri, args.database, args.run_id)
    with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000) as client:
        result = upgrade(client[args.database], args.journal)
    print(json.dumps({'result': result['result'], 'journal': str(args.journal)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
