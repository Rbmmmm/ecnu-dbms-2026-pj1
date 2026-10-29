"""Isolated II-W004 browser, BSON/index verification and process restart."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bson import ObjectId
from pymongo import MongoClient
from app.config import guard_test_database
from app.db import inspect_contract
from scripts.test_browser import start, stop
from scripts.test_stage2_browser import all_fingerprints


def main():
    run=uuid4().hex;uri='mongodb://127.0.0.1:27017/';name='survey_test_'+run
    guard_test_database(uri,name,run)
    output=Path(os.getenv('SURVEY_MANAGEMENT_OUTPUT',str(ROOT/'artifacts'/('management-browser-'+run)))).resolve()
    output.mkdir(parents=True,exist_ok=False)
    report={'date':datetime.now(timezone.utc).isoformat(),'run_id':run,'result':'failed','checks':{},'browser_plugin':'absent; existing Playwright and installed Chrome'}
    process=None
    with MongoClient(uri,tz_aware=True) as mongo:
        assert name not in mongo.list_database_names()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin='http://127.0.0.1:'+str(port);report['origin']=origin
        env={**os.environ,'SURVEY_TEST_URI':uri,'SURVEY_TEST_DB':name,'SURVEY_TEST_RUN_ID':run,'SURVEY_TEST_ORIGIN':origin,'SURVEY_EVIDENCE_DIR':str(output)}
        try:
            with (output/'server.log').open('w') as log:
                process=start(env,port,log)
                with (output/'browser.log').open('w') as browser_log:
                    done=subprocess.run([env['SURVEY_NODE'],str(ROOT/'tests/browser_management.cjs')],cwd=ROOT,env=env,stdout=browser_log,stderr=subprocess.STDOUT,timeout=300)
                assert done.returncode==0,'browser failed; see browser.log'
                browser=json.loads((output/'browser-management.json').read_text());report['browser']=browser
                assert browser['checks'] and all(x=='passed' for x in browser['checks'].values())
                db=mongo[name];inspect_contract(db)
                versions=[db.survey_question_versions.find_one({'_id':ObjectId(vid)}) for vid in browser['versions']]
                assert [x['version_no'] for x in versions]==[1,2,3]
                assert versions[2]['operation']=='restore' and versions[2]['parent_version_id']==versions[2]['restored_from_version_id']==versions[0]['_id']
                assert versions[0]['payload']==versions[2]['payload'] and versions[0]['payload_hash']==versions[2]['payload_hash']
                assert versions[1]['payload']['validation']['max_value'].to_decimal()==200
                surveys=[db.survey_surveys.find_one({'_id':ObjectId(sid)}) for sid in browser['surveys']]
                assert surveys[0]['state']=='published' and len(surveys[0]['questions'])==2
                assert surveys[0]['questions'][0]['source']==surveys[0]['questions'][1]['source']
                assert surveys[0]['questions'][0]['id']!=surveys[0]['questions'][1]['id']
                assert surveys[0]['question_result_grants'][0]=={'question_id':versions[0]['question_id'],'enabled':False,'allowed_at_publish':True}
                answers=list(db.survey_responses.find({'survey_id':surveys[0]['_id']}))
                assert len(answers)==1 and answers[0]['anonymous'] is True
                assert [a['value'].to_decimal() for a in answers[0]['answers']]==[18,30]
                assert db.survey_question_banks.find_one({'_id':ObjectId(browser['bank_id'])})['entries']==[]
                report['checks']['actual_bson_versions_grants_answers_banks_indexes']='passed'
                before=all_fingerprints(db);stop(process);process=start(env,port,log)
                assert all_fingerprints(db)==before
                report['checks']['seven_collections_restart']='passed';report['fingerprints']=before
                report['indexes']={n:db[n].index_information() for n in db.list_collection_names()}
                report['result']='passed'
        except BaseException as exc:report['failure']=type(exc).__name__+': '+str(exc)
        finally:
            stop(process);guard_test_database(uri,name,run);mongo.drop_database(name)
            report['checks']['cleanup']='passed' if name not in mongo.list_database_names() else 'failed'
            if report['checks']['cleanup']!='passed':report['result']='failed'
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({'result':report['result'],'evidence':str(output),'failure':report.get('failure')},ensure_ascii=False))
    return report['result']!='passed'

if __name__=='__main__':sys.exit(main())
