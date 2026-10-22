"""Stage acceptance: never treat preflight, collection or a skipped test as a pass."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import guard_test_database
from app.db import connect, inspect_contract
from app.config import Settings
from scripts import p4_audit

W007_ADDED_REQUIREMENTS = {'R-%03d' % n for n in [12,13,14,15]}
P2_REQUIREMENTS = {'R-%03d' % n for n in [1,2,3,5,6,7,8,9,10,11,16,17,18,19,22,23,24,27]}


def code_fingerprint():
    files = [p for directory in ['app','scripts','tests'] for p in (ROOT/directory).rglob('*')
             if p.is_file() and '__pycache__' not in str(p) and p.name not in {'manual-review.json','.DS_Store'}]
    files += [ROOT/p for p in ['requirements.lock.txt','README.md','.env.example','大作业一第一阶段.docx','大作业一第一阶段.md']]
    files += [p for p in (ROOT/'开发文档').glob('*.md') if p.name.startswith(('11-','12-','13-','14-'))]
    files += [ROOT/'开发文档/W-009-独立检查.md'] if (ROOT/'开发文档/W-009-独立检查.md').exists() else []
    files += [ROOT/'开发文档/04-AI记录与提交文档.md']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def version():
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
           for directory in ['app','scripts','tests'] for p in sorted((ROOT/directory).rglob('*'))
           if p.is_file() and '__pycache__' not in str(p) and p.name != '.DS_Store'}
    for file in ['requirements.lock.txt','README.md']:
        p=ROOT/file
        if p.exists():files[file]=hashlib.sha256(p.read_bytes()).hexdigest()
    return {'head':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),'dirty':bool(git('status','--porcelain')),
            'status':git('status','--porcelain'), 'file_sha256':files}


def preflight(mapping_path, uri, database, run_id, stage="P2"):
    guard_test_database(uri,database,run_id)
    expected=json.loads((ROOT/'tests/original-materials.json').read_text())
    for path,digest in expected.items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:
            raise ValueError('original material fingerprint changed: '+path)
    for p in [ROOT/'AGENTS.md',ROOT/'README.md',*(ROOT/'开发文档').rglob('*.md')]:
        for destination in re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text()):
            if destination.startswith(('https:','http:','#','mailto:')):continue
            target=destination.split('#')[0]
            if target and not (p.parent/target).exists():raise ValueError('broken document link: '+str(p))
    scan_files = [p for folder in ['app','scripts','tests','开发文档'] for p in (ROOT/folder).rglob('*')]
    scan_files += [ROOT/'.env.example', ROOT/'README.md', ROOT/'requirements.lock.txt']
    for p in scan_files:
        if not p.is_file() or (p.suffix not in {'.py','.js','.cjs','.json','.md','.txt','.example'}):continue
        text=p.read_text()
        if re.search(r'mongodb(?:\+srv)?://[^\s\"\']+:[^\s\"\']+@',text) or ('-----BEGIN ' + 'PRIVATE KEY-----') in text:
            raise ValueError('credential-like content in '+str(p.relative_to(ROOT)))
    mapping=json.loads(mapping_path.read_text())
    required = P2_REQUIREMENTS | (W007_ADDED_REQUIREMENTS if stage in {'W007','P3','P4'} else set()) | ({'R-020','R-021'} if stage in {'P3','P4'} else set())
    if set(mapping.get('requirements',{})) != required:
        raise ValueError(stage + ' required requirement mapping missing or unexpected')
    if mapping.get('stage')!=stage or not mapping.get('scope'):
        raise ValueError('stage scope missing')
    tests=set()
    for req,item in mapping['requirements'].items():
        if not item.get('scope') or not (item.get('tests') or (stage=='P4' and item.get('manual_checks'))) or not item.get('sources'):
            raise ValueError('missing test/source/scope for '+req)
        for source in item['sources']:
            if not (ROOT/source).is_file():raise ValueError('missing source for '+req)
        tests.update(item['tests'])
    if not tests:raise ValueError('no acceptance tests')
    manual=json.loads((ROOT/'tests/manual-review.json').read_text())
    if manual.get('code_sha256')!=code_fingerprint():raise ValueError('manual review is stale for current code')
    for key in ['date','steps','conclusion','reviewer','object']:
        if not manual.get(key):raise ValueError('manual review lacks '+key)
    if manual['conclusion']!='passed':raise ValueError('manual review not passed')
    datetime.fromisoformat(manual['date'])
    if stage == 'P4':
        p4_audit.validate_mapping(mapping, manual)
    return mapping,tests


def run_command(command,env,log):
    with log.open('w') as output:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
    return result.returncode


def start_server(env,output,port):
    log=output.open('a')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','scripts.test_server:factory','--factory',
                              '--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdout=log,stderr=log)
    import httpx
    for _ in range(100):
        if process.poll() is not None:log.close();raise RuntimeError('test server failed to start')
        try:
            response=httpx.get(env['SURVEY_TEST_ORIGIN']+'/health',timeout=0.4,trust_env=False)
            if response.status_code==200:return process,log
        except httpx.HTTPError:pass
        time.sleep(0.1)
    process.terminate();process.wait(timeout=10);log.close();raise RuntimeError('test server readiness timed out')


def stop_server(process,log):
    process.terminate()
    process.wait(timeout=10)
    log.close()


def self_test(args):
    with tempfile.TemporaryDirectory() as directory:
        directory=Path(directory)
        bad_map=json.loads(args.mapping.read_text());bad_map['requirements'].pop('R-001')
        (directory/'missing.json').write_text(json.dumps(bad_map))
        (directory/'test_deliberate_failure.py').write_text('def test_deliberate_failure():\n    assert False, "verifier must fail"\n')
        cases=[('missing_mapping',['--mapping',str(directory/'missing.json')]),
               ('failing_test',['--pytest-file',str(directory/'test_deliberate_failure.py')]),
               ('invalid_database',['--database','ecnu00000000000'])]
        if args.stage == 'P4':
            weakened=json.loads(args.mapping.read_text())
            for item in weakened['requirements'].values():item['tests']=['tests/test_p2.py::test_T01_ownership_and_app_restart']
            (directory/'weakened.json').write_text(json.dumps(weakened))
            missing=json.loads(args.mapping.read_text());missing['independent_checks'].remove('raw_database_oracle')
            (directory/'no-independent.json').write_text(json.dumps(missing))
            cases += [('weakened_mapping',['--mapping',str(directory/'weakened.json')]),
                      ('missing_independent_check',['--mapping',str(directory/'no-independent.json')])]
        results=[]
        for name, extra in cases:
            command=[sys.executable,str(ROOT/'scripts/verify.py'),'--stage',args.stage,*extra]
            completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            results.append({'case':name,'returncode':completed.returncode,'output':completed.stdout.strip()})
            if completed.returncode==0:raise RuntimeError('verifier incorrectly accepted '+name)
        if not any('API tests failed' in item['output'] for item in results if item['case']=='failing_test'):
            raise RuntimeError('failing test was not actually executed')
        return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',required=True,choices=['P2','W007','P3','P4','P6'])
    parser.add_argument('--changed',action='store_true')
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--mapping',type=Path)
    parser.add_argument('--pytest-file',type=Path,nargs='+')
    parser.add_argument('--database')
    parser.add_argument('--node',default=os.getenv('SURVEY_NODE') or shutil.which('node'))
    parser.add_argument('--playwright-module',default=os.getenv('SURVEY_PLAYWRIGHT_MODULE','playwright'))
    parser.add_argument('--chromium',default=os.getenv('SURVEY_CHROMIUM'))
    args=parser.parse_args()
    if args.stage == 'P6':
        from scripts.p6_acceptance import main as delivery_main
        return delivery_main(args)
    args.mapping=args.mapping or ROOT/({'W007':'tests/requirements-map-w007.json','P3':'tests/requirements-map-p3.json','P4':'tests/requirements-map-p4.json'}.get(args.stage,'tests/requirements-map.json'))
    if args.stage == 'P4':
        args.pytest_file=list(dict.fromkeys([*(ROOT/p for p in p4_audit.TEST_FILES), *(p.resolve() for p in (args.pytest_file or []))]))
    else:
        args.pytest_file=args.pytest_file or [ROOT/'tests/test_p2.py', *([ROOT/'tests/test_w007.py'] if args.stage in {'W007','P3'} else []), *([ROOT/'tests/test_w008.py'] if args.stage=='P3' else [])]
    run_id=uuid4().hex
    output=ROOT/'artifacts'/run_id;output.mkdir(parents=True)
    report={'date':datetime.now(timezone.utc).isoformat(),'command':sys.argv,'stage':args.stage,
            'result':'failed','checks':{},'run_id':run_id, 'version':version()}
    uri=os.getenv('SURVEY_TEST_URI','mongodb://127.0.0.1:27017/')
    database=args.database or 'survey_test_'+run_id
    safe=False
    try:
        if args.stage not in {'P2','W007','P3','P4'}:raise ValueError('this stage has not been implemented; acceptance not executed')
        if not __debug__:raise ValueError('acceptance requires enabled assertions; do not use Python -O')
        if args.changed:raise ValueError('--changed is not implemented; run a full implemented stage')
        if args.self_test:
            report['counterexamples']=self_test(args);report['result']='passed_counterexamples_only'
        else:
            guard_test_database(uri,database,run_id);safe=True
            mapping,expected_tests=preflight(args.mapping,uri,database,run_id,args.stage)
            reviewed_fingerprints=code_fingerprint()
            report['checks']['preflight']='passed'
            env={**os.environ,'SURVEY_TEST_URI':uri,'SURVEY_TEST_DB':database,'SURVEY_TEST_RUN_ID':run_id}
            env.pop('PYTEST_ADDOPTS',None)
            pytest_command=[sys.executable,'-m','pytest',*[str(p) for p in args.pytest_file],'-o','addopts=']
            collected=subprocess.run([*pytest_command,'--collect-only','-q'],cwd=ROOT,env=env,capture_output=True,text=True)
            (output/'collection.log').write_text(collected.stdout+collected.stderr)
            if collected.returncode:raise RuntimeError('test collection failed')
            collected_names=Counter(line.strip() for line in collected.stdout.splitlines()
                                    if re.match(r'.+\.py::test_',line))
            if not collected_names:raise RuntimeError('no cases collected')
            command=[*pytest_command,'-q','--tb=short','--junitxml='+str(output/'pytest.xml')]
            report['api_command']=command
            code=run_command(command,env,output/'pytest.log')
            if code:raise RuntimeError('API tests failed (exit '+str(code)+')')
            xml=ET.parse(output/'pytest.xml')
            cases=xml.findall('.//testcase')
            executed_nodes=[case.attrib['classname'].replace('.','/')+'.py::'+case.attrib['name'] for case in cases]
            executed={case.attrib['name'] for case in cases}
            executed_functions={name.split('[',1)[0] for name in (executed_nodes if args.stage=='P4' else executed)}
            if Counter(executed_nodes)!=collected_names:
                raise RuntimeError('collected parameter cases were not all executed')
            if expected_tests-executed_functions:raise RuntimeError('required tests were not executed')
            if any(case.find(tag) is not None for case in cases for tag in ['skipped','failure','error']):
                raise RuntimeError('required tests failed or were skipped')
            report['checks']['api_database']={'result':'passed','count':len(cases),'tests':sorted(executed)}
            report['checks']['api_database']['nodeids']=sorted(executed_nodes)
            if not args.node:raise RuntimeError('Node required for mandatory browser acceptance')
            import socket
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env.update({'SURVEY_TEST_ORIGIN':'http://127.0.0.1:'+str(port), 'SURVEY_EVIDENCE_DIR':str(output),
                        'SURVEY_PLAYWRIGHT_MODULE':args.playwright_module})
            if args.chromium:env['SURVEY_CHROMIUM']=args.chromium
            process,log=start_server(env,output/'server.log',port)
            try:
                command=[args.node,str(ROOT/'tests/browser.cjs')]
                report['browser_command']=command
                code=run_command(command,env,output/'browser.log')
                if code:raise RuntimeError('browser flow failed (exit '+str(code)+')')
                browser=json.loads((output/'browser.json').read_text())
                required={'creator_draft_publish_share','confirmation_does_not_save','truncated_response_same_content_recovery',
                    'statistics_zero_options_and_privacy','page_identity','not_blank','no_framework_overlay','mobile_no_horizontal_overflow','console_health'}
                if set(browser['checks'])!=required or set(browser['checks'].values())!={'passed'}:raise RuntimeError('browser checks missing or failed')
                client,db=connect(Settings(uri,database,testing=True,run_id=run_id))
                try:
                    report['database_contract']=inspect_contract(db)
                    assert db.survey_users.count_documents({})==2
                    assert db.survey_responses.count_documents({})==1
                    saved=db.survey_responses.find_one({})
                    assert saved['anonymous'] is True and saved['repeat_allowed'] is False
                    assert len(saved['path'])==2 and len(saved['answers'])==1
                    before=hashlib.sha256(repr(sorted(saved.items())).encode()).hexdigest()
                    report['stored_document_check']={'accounts':2,'surveys':db.survey_surveys.count_documents({}),
                        'responses':1,'path_length':2,'nonempty_answers':1,'anonymous':True}
                finally:client.close()
                if args.stage in {'W007','P3','P4'}:
                    command=[args.node,str(ROOT/'tests/browser_w007.cjs')]
                    report['w007_browser_command']=command
                    code=run_command(command,env,output/'browser-w007.log')
                    if code:raise RuntimeError('W007 browser flow failed (exit '+str(code)+')')
                    rules_browser=json.loads((output/'browser-w007.json').read_text())
                    required_rules={'editor_all_types_constraints_rules','backtrack_pending_reuse_and_error_preservation',
                        'old_evaluation_discarded_and_end_not_submitted','end_clear_and_no_resurrection',
                        'first_matching_rule_and_explicit_submission','unicode_input_not_utf16_truncated','page_identity_console_and_mobile'}
                    if set(rules_browser['checks'])!=required_rules or set(rules_browser['checks'].values())!={'passed'}:
                        raise RuntimeError('W007 browser checks missing or failed')
                    client,db=connect(Settings(uri,database,testing=True,run_id=run_id))
                    try:
                        from bson import ObjectId
                        from bson.decimal128 import Decimal128
                        summary=rules_browser['summary']
                        documents=list(db.survey_responses.find({'survey_id':ObjectId(summary['survey_id'])}).sort([('created_at',1),('_id',1)]))
                        assert [doc['path'] for doc in documents]==summary['expected_paths']
                        assert [len(doc['answers']) for doc in documents]==[2,3]
                        assert isinstance(documents[0]['answers'][1]['value'],Decimal128)
                        assert documents[0]['answers'][1]['value'].to_decimal()==18
                        assert documents[1]['answers'][1]['value']==[summary['option_ids']['X'],summary['option_ids']['Y']]
                        all_documents=list(db.survey_responses.find().sort('_id',1))
                        before=hashlib.sha256(repr(all_documents).encode()).hexdigest()
                        report['w007_stored_documents']={'paths':[doc['path'] for doc in documents],
                            'nonempty_answers':[2,3],'number_bson':'Decimal128(18)','all_responses':len(all_documents)}
                    finally:client.close()
                    report['checks']['w007_browser']=rules_browser
                if args.stage in {'P3','P4'}:
                    command=[args.node,str(ROOT/'tests/browser_w008.cjs')]
                    report['w008_browser_command']=command
                    code=run_command(command,env,output/'browser-w008.log')
                    if code:raise RuntimeError('P3 browser flow failed (exit '+str(code)+')')
                    full_browser=json.loads((output/'browser-w008.json').read_text())
                    required_full={'f01_all_types_counts_and_paginated_anonymous_contents','r5_refresh_whole_and_single_statistics',
                        'beijing_deadline_clear_close_and_retry','literal_html_anonymous_whitelist_safe_login_console_mobile'}
                    if set(full_browser['checks'])!=required_full or set(full_browser['checks'].values())!={'passed'}:
                        raise RuntimeError('P3 browser checks missing or failed')
                    client,db=connect(Settings(uri,database,testing=True,run_id=run_id))
                    try:
                        from app.statistics import statistics
                        summary=full_browser['summary'];survey=db.survey_surveys.find_one({'_id':ObjectId(summary['survey_id'])})
                        docs=list(db.survey_responses.find({'survey_id':survey['_id']}))
                        assert len(docs)==5 and sum(d['anonymous'] for d in docs)==2
                        assert sum(not d['answers'] for d in docs)==1 and all(d['path']==summary['question_ids'] for d in docs)
                        numbers=[answer['value'] for doc in docs for answer in doc['answers'] if answer['question_id']==summary['question_ids'][3]]
                        assert all(isinstance(value,Decimal128) for value in numbers)
                        assert sorted(value.to_decimal() for value in numbers)==[0,10,20]
                        stats=statistics(db,survey)
                        assert (stats['submission_count'],stats['respondent_count'])==(5,3)
                        assert stats['questions'][3]['average_display']=='10.00'
                        all_documents=list(db.survey_responses.find().sort('_id',1))
                        before=hashlib.sha256(repr(all_documents).encode()).hexdigest()
                        report['p3_stored_documents']={'f01_responses':5,'f01_respondents':3,'anonymous':2,'all_empty':1,
                            'numbers_bson':'Decimal128(0), Decimal128(10), Decimal128(20)','average_display':'10.00','all_responses':len(all_documents)}
                    finally:client.close()
                    report['checks']['w008_browser']=full_browser
                if args.stage == 'P4':
                    command=[args.node,str(ROOT/'tests/browser_p4.cjs')]
                    report['p4_browser_command']=command
                    code=run_command(command,env,output/'browser-p4.log')
                    if code:raise RuntimeError('P4 independent browser flow failed (exit '+str(code)+')')
                    extra=json.loads((output/'browser-p4.json').read_text())
                    if set(extra['checks'])!=p4_audit.BROWSER_CHECKS or set(extra['checks'].values())!={'passed'}:
                        raise RuntimeError('P4 independent browser checks missing or failed')
                    report['checks']['p4_browser']=extra
                    client,db=connect(Settings(uri,database,testing=True,run_id=run_id))
                    try:
                        report['checks']['raw_database_oracle']=p4_audit.raw_database_oracle(db,output)
                        before=hashlib.sha256(repr(list(db.survey_responses.find().sort('_id',1))).encode()).hexdigest()
                        collection_before=p4_audit.collection_fingerprints(db)
                    finally:client.close()
            finally:stop_server(process,log)
            process,log=start_server(env,output/'server.log',port)
            try:
                client,db=connect(Settings(uri,database,testing=True,run_id=run_id))
                try:
                    if args.stage in {'W007','P3','P4'}:
                        saved=list(db.survey_responses.find().sort('_id',1))
                        assert hashlib.sha256(repr(saved).encode()).hexdigest()==before
                    else:
                        saved=db.survey_responses.find_one({})
                        assert hashlib.sha256(repr(sorted(saved.items())).encode()).hexdigest()==before
                    from app.statistics import statistics
                    from bson import ObjectId
                    survey=db.survey_surveys.find_one({'_id':ObjectId(browser['summary']['survey_id'])})
                    assert statistics(db,survey)['submission_count']==1
                    if args.stage=='P4':
                        assert collection_before==p4_audit.collection_fingerprints(db)
                        report['checks']['all_collections_restart_persistence']=collection_before
                finally:client.close()
                report['checks']['process_restart_persistence']='passed'
            finally:stop_server(process,log)
            report['checks']['browser']=browser
            report['scope']=mapping['scope']
            report['not_executed']=mapping['deferred']
            if args.stage=='P4':
                report['checks']['verifier_counterexamples']=self_test(args)
                manual=json.loads((ROOT/'tests/manual-review.json').read_text())
                report['checks']['independent_review']=manual['p4']
                report['requirements']=p4_audit.evidence_links(mapping,executed_nodes,manual,code_fingerprint())
                report['groups']={group:{'result':'passed','review':record,
                    'executed_cases':sorted(n for n in executed_nodes if '::test_'+group.replace('-','')+'_' in n)}
                    for group,record in manual['p4']['groups'].items()}
                if any(not v['executed_cases'] for v in report['groups'].values()):raise RuntimeError('P4 T group not executed')
            if version()!=report['version'] or code_fingerprint()!=reviewed_fingerprints:
                raise RuntimeError('acceptance objects changed during execution')
            report['result']='passed'
    except Exception as exc:
        report['failure']=str(exc)
    finally:
        if safe:
            guard_test_database(uri,database,run_id)
            from pymongo import MongoClient
            try:
                with MongoClient(uri,serverSelectionTimeoutMS=10000) as client:client.drop_database(database)
                report['test_database_cleaned']=True
            except Exception as exc:
                report['test_database_cleaned']=False
                report['cleanup_failure']=type(exc).__name__
                report['result']='failed'
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'result':report['result'],'evidence':str(output/'report.json'),'failure':report.get('failure')},ensure_ascii=False))
    return 0 if report['result'].startswith('passed') else 1


if __name__=='__main__':sys.exit(main())
