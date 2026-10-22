"""Delivery verification. Technical evidence never substitutes for user review."""
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
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DOCUMENTS=['项目完成报告.md','系统与数据库设计.md','运行与API说明.md','关键逻辑说明.md',
           '测试用例与结果.md','AI使用日志.md','提交清单.md']
PDF='2026_ECNU_PJ1_1_任北鸣.pdf'
TESTS=['tests/test_p2.py','tests/test_w007.py','tests/test_w008.py','tests/test_p4.py']


def expected_mapping():
    old=json.loads((ROOT/'tests/requirements-map-p4.json').read_text())
    requirements={req:{'sources':item['sources'],'evidence':['full_p4']} for req,item in old['requirements'].items()}
    additions={4:'系统与数据库设计.md',25:'运行与API说明.md',26:'项目完成报告.md',
               28:'AI使用日志.md',29:'提交清单.md',30:'提交清单.md'}
    for number,filename in additions.items():
        requirements[f'R-{number:03}']={'sources':['提交文档/'+filename],
            'evidence':['documents','user_review']}
    requirements['R-024']['evidence']+=['clean_install','v1_restore','github_delivery']
    requirements['R-023']['sources']+=['提交文档/测试用例与结果.md']
    requirements['R-023']['evidence']+=['documents']
    requirements['R-025']['sources']+=['提交文档/系统与数据库设计.md','提交文档/关键逻辑说明.md']
    requirements['R-027']['sources']+=['提交文档/AI使用日志.md']
    requirements['R-027']['evidence']+=['documents','user_review']
    requirements['R-029']['sources']+=['提交文档/附件/v1-baseline.json','scripts/v1_baseline.py']
    requirements['R-029']['evidence']+=['v1_restore']
    requirements['R-030']['evidence']+=['github_delivery']
    return {'stage':'P6','gates':['full_p4','clean_install','v1_restore','documents','user_review','github_delivery'],
            'requirements':dict(sorted(requirements.items()))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_for_package():
    paths=[p for p in (ROOT/'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store']
    paths += [ROOT/p for p in ['README.md','.env.example','requirements.lock.txt','大作业一第一阶段.md',
        '大作业一第一阶段.docx','scripts/run.py','scripts/init_db.py','scripts/test_server.py','scripts/v1_baseline.py',
        'tests/__init__.py','tests/conftest.py','tests/fixtures_f01.py','tests/fixtures_f02.py','tests/fixtures-f01.json',
        'tests/browser.cjs','tests/browser_w007.cjs','tests/browser_w008.cjs','tests/browser_p4.cjs',*TESTS]]
    paths += [ROOT/'提交文档'/p for p in [*DOCUMENTS,PDF]]
    paths += [p for p in (ROOT/'提交文档/附件').rglob('*') if p.is_file() and p.name!='.DS_Store']
    if any(not p.is_file() for p in paths):
        raise ValueError('required submission file missing')
    return sorted(set(paths))


def submission_fingerprints():
    return {str(p.relative_to(ROOT)):sha(p) for p in files_for_package()}


def review_gate(fingerprints, review):
    reasons=[]
    if review.get('status')!='approved' or review.get('reviewer')!='user':
        reasons.append('user wording and contribution review not recorded')
    if review.get('files_sha256')!=fingerprints:
        reasons.append('user review does not bind the current submission files')
    if not review.get('statement') or not review.get('reviewed_at'):
        reasons.append('explicit user review evidence missing')
    elif review.get('reviewed_at'):
        try: datetime.fromisoformat(review['reviewed_at'])
        except (ValueError,TypeError): reasons.append('invalid user review date')
    url=review.get('repository_url') or ''
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',url):
        reasons.append('confirmed GitHub repository URL missing')
    return reasons


def pdf_check():
    path=ROOT/'提交文档'/PDF
    if not path.read_bytes().startswith(b'%PDF-'):raise ValueError('submission PDF is invalid')
    info=subprocess.check_output(['pdfinfo',str(path)],text=True)
    pages=int(re.search(r'Pages:\s+(\d+)',info)[1])
    first=subprocess.check_output(['pdftotext','-f','1','-l','1',str(path),'-'],text=True)
    full=subprocess.check_output(['pdftotext',str(path),'-'],text=True)
    if pages<1 or len(full.strip())<1000:raise ValueError('submission PDF is empty or incomplete')
    for word in ['MongoDB','API','AI','测试','跳转','校验']:
        if word not in full:raise ValueError('PDF required content missing: '+word)
    review=json.loads((ROOT/'tests/delivery-qa.json').read_text())
    if review.get('pdf_sha256')!=sha(path) or review.get('source_sha256')!=sha(ROOT/'提交文档/项目完成报告.md'):
        raise ValueError('PDF visual review is stale')
    if review.get('reviewed_pages')!=list(range(1,pages+1)) or review.get('result')!='reviewed' or not review.get('observations'):
        raise ValueError('every PDF page requires recorded visual review')
    docs={str((ROOT/'提交文档'/p).relative_to(ROOT)):sha(ROOT/'提交文档'/p) for p in DOCUMENTS}
    if review.get('documents_sha256')!=docs or not review.get('semantic_steps'):
        raise ValueError('submission semantic review is stale')
    return {'result':'passed','pages':pages,'sha256':sha(path),'visual_review':review,'first_page_text':first}


def github_check(review, fingerprints, head, dirty):
    reasons=[]
    url=review.get('repository_url') or ''
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',url):
        return {'result':'pending','reasons':['confirmed GitHub URL is missing']}
    for path,digest in fingerprints.items():
        stored=subprocess.run(['git','show','HEAD:'+path],cwd=ROOT,capture_output=True)
        if stored.returncode or hashlib.sha256(stored.stdout).hexdigest()!=digest:
            reasons.append('file missing or different in Git HEAD: '+path)
    remote=subprocess.run(['git','ls-remote',url,'refs/heads/main'],cwd=ROOT,capture_output=True,text=True,timeout=30)
    if remote.returncode or not remote.stdout.strip():reasons.append('GitHub main publication not verified')
    elif remote.stdout.split()[0]!=head:reasons.append('GitHub main does not match current local commit')
    if dirty:reasons.append('final Git working tree is not clean')
    return {'result':'pending' if reasons else 'passed','reasons':reasons,'repository_url':url}


def check_package(package, expected):
    with zipfile.ZipFile(package) as archive:
        names=set(archive.namelist())
        if names != set(expected)|{'release-manifest.json'}:
            raise ValueError('package file list differs from fixed manifest')
        for name,digest in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:
                raise ValueError('package content hash mismatch: '+name)
        embedded=json.loads(archive.read('release-manifest.json'))
        if embedded['files_sha256']!=expected:
            raise ValueError('embedded manifest mismatch')


def counterexamples(package, fingerprints, output):
    results={}
    with tempfile.TemporaryDirectory() as folder:
        for label,mode in [('missing_file','missing'),('tampered_file','tamper'),('tampered_baseline','baseline')]:
            candidate=Path(folder)/f'{label}.zip'
            with zipfile.ZipFile(package) as src, zipfile.ZipFile(candidate,'w') as dst:
                target='提交文档/附件/v1-baseline.json' if mode=='baseline' else 'requirements.lock.txt'
                for name in src.namelist():
                    if name==target and mode=='missing': continue
                    dst.writestr(name,src.read(name)+(b'\n# modified' if name==target and mode in {'tamper','baseline'} else b''))
            try: check_package(candidate,fingerprints)
            except ValueError as exc: results[label]={'rejected':True,'reason':str(exc)}
            else: raise AssertionError('corrupt package accepted')
    for label,record in [('missing_review',{}),('ai_review',{'status':'approved','reviewer':'AI'}),
                         ('stale_review',{'status':'approved','reviewer':'user','files_sha256':{},
                            'statement':'counterexample only','reviewed_at':'2026-10-16','repository_url':'https://github.com/example/example'})]:
        reasons=review_gate(fingerprints,record)
        if not reasons: raise AssertionError('incomplete review accepted')
        results[label]={'rejected':True,'reason':reasons}
    (output/'delivery-counterexamples.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    return results


def run_command(command,cwd,env,log):
    with log.open('w') as handle:
        result=subprocess.run(command,cwd=cwd,env=env,stdout=handle,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}); see {log.name}')


def main(args):
    if not __debug__: raise ValueError('P6 requires enabled assertions')
    if args.changed or args.self_test or args.mapping or args.pytest_file or args.database:
        raise ValueError('P6 fixed full pipeline does not accept stage-reduction overrides')
    from scripts import verify
    run_id=uuid4().hex
    output=ROOT/'artifacts'/('p6-'+run_id);output.mkdir(parents=True)
    report={'stage':'P6','run_id':run_id,'date':datetime.now(timezone.utc).isoformat(),
            'result':'failed','technical_result':'failed','version':verify.version(),'checks':{},'command':sys.argv}
    env={k:v for k,v in os.environ.items() if not k.startswith(('SURVEY_','PYTHON')) and k!='PYTEST_ADDOPTS'}
    try:
        mapping=json.loads((ROOT/'tests/requirements-map-p6.json').read_text())
        if mapping!=expected_mapping():raise ValueError('P6 mapping differs from fixed full requirements')
        if set(mapping['requirements']) != {f'R-{i:03}' for i in range(1,31)}:
            raise ValueError('P6 requires all 30 requirements')
        if set(mapping['gates'])!={'full_p4','clean_install','v1_restore','documents','user_review','github_delivery'}:
            raise ValueError('P6 fixed gate list changed')
        for req,record in mapping['requirements'].items():
            if not record.get('evidence') or any(not (ROOT/p).is_file() for p in record.get('sources',[])) or not record.get('sources'):
                raise ValueError('P6 missing evidence/source for '+req)
        fingerprints=submission_fingerprints()
        original=json.loads((ROOT/'tests/original-materials.json').read_text())
        assert all(sha(ROOT/p)==v for p,v in original.items())
        for p in [ROOT/'提交文档'/name for name in DOCUMENTS]:
            text=p.read_text()
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
                if target.startswith(('https:','http:','#')):continue
                if not (p.parent/target.split('#')[0]).exists():raise ValueError('broken submission document link: '+p.name)
        for path in files_for_package():
            if path.suffix in {'.py','.cjs','.js','.json','.md','.txt','.example'}:
                text=path.read_text()
                if re.search(r'mongodb(?:\+srv)?://[^\s\"\']+:[^\s\"\']+@',text):
                    raise ValueError('credential-like content in delivery')
        report['checks']['documents']=pdf_check()
        command=[sys.executable,'scripts/verify.py','--stage','P4','--node',args.node or '',
                 '--playwright-module',args.playwright_module]
        if args.chromium:command+=['--chromium',args.chromium]
        run_command(command,ROOT,os.environ.copy(),output/'p4.log')
        summary=json.loads((output/'p4.log').read_text().strip().splitlines()[-1])
        p4=json.loads(Path(summary['evidence']).read_text())
        assert p4['result']=='passed' and p4['checks']['api_database']['count']>=101
        shutil.copytree(Path(summary['evidence']).parent,output/'full-p4')
        report['checks']['full_p4']={'result':'passed','run_id':p4['run_id'],'cases':p4['checks']['api_database']['count']}
        manifest={'format_version':1,'status':'review_candidate','source_git':report['version'],
                  'schema_version':1,'content_version':1,'files_sha256':fingerprints}
        package=output/'PJ1-stage1-runtime-reproduction.zip'
        with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in fingerprints:archive.write(ROOT/name,name)
            archive.writestr('release-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        check_package(package,fingerprints)
        report['checks']['counterexamples']=counterexamples(package,fingerprints,output)
        extracted=output/'clean-source';extracted.mkdir()
        with zipfile.ZipFile(package) as archive:archive.extractall(extracted)
        for p in [extracted/'README.md',*(extracted/'提交文档').glob('*.md')]:
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text()):
                if target.startswith(('https:','http:','#')):continue
                if not (p.parent/target.split('#')[0]).exists():raise ValueError('reproduction package has broken link: '+p.name)
        assert not (extracted/'.env').exists()
        clean=output/'clean-venv'
        run_command([sys.executable,'-m','venv',str(clean)],ROOT,env,output/'venv.log')
        python=clean/'bin/python'
        assert 'include-system-site-packages = false' in (clean/'pyvenv.cfg').read_text()
        run_command([str(python),'-m','pip','install','-r','requirements.lock.txt'],extracted,env,output/'install.log')
        run_command([str(python),'-m','pip','check'],extracted,env,output/'pip-check.log')
        run_command([str(python),'-m','pip','freeze','--all'],extracted,env,output/'installed.txt')
        lock={line.lower() for line in (ROOT/'requirements.lock.txt').read_text().splitlines() if line and not line.startswith('#')}
        installed={line.lower() for line in (output/'installed.txt').read_text().splitlines()}
        assert lock <= installed
        assert all(line.split('==')[0] in {'pip','setuptools','wheel'} for line in installed-lock)
        report['checks']['clean_install']={'result':'passed','locked_packages':len(lock),'system_site_packages':False}
        test_command=[str(python),'-m','pytest',*TESTS,'-o','addopts=','-q','--tb=short','--junitxml='+str(output/'clean-pytest.xml')]
        run_command(test_command,extracted,env,output/'clean-pytest.log')
        cases=ET.parse(output/'clean-pytest.xml').findall('.//testcase')
        nodes=[c.attrib['classname'].replace('.','/')+'.py::'+c.attrib['name'] for c in cases]
        assert Counter(nodes)==Counter(p4['checks']['api_database']['nodeids'])
        assert not any(c.find(tag) is not None for c in cases for tag in ['failure','error','skipped'])
        report['checks']['clean_api_regression']={'result':'passed','cases':len(cases)}
        for label,restore in [('fresh',False),('restored',True)]:
            result_dir=output/('v1-'+label)
            cmd=[str(python),'scripts/v1_baseline.py','--output',str(result_dir)]
            if restore:cmd+=['--restore','提交文档/附件/v1-baseline.json']
            run_command(cmd,extracted,env,output/('v1-'+label+'.log'))
            result=json.loads((result_dir/'result.json').read_text())
            assert result['result']=='passed' and result['test_database_cleaned']
            report['checks']['v1_'+label]=result
        current_api=json.loads((output/'v1-fresh/openapi-v1.json').read_text())
        frozen_api=json.loads((ROOT/'提交文档/附件/openapi-v1.json').read_text())
        assert current_api==frozen_api
        assert json.loads((output/'v1-fresh/schema-v1.json').read_text())==json.loads((ROOT/'提交文档/附件/schema-v1.json').read_text())
        if fingerprints!=submission_fingerprints() or report['version']!=verify.version():
            raise ValueError('delivery objects changed during execution')
        report['manifest']=manifest
        report['package']={'path':str(package),'sha256':sha(package)}
        review_path=ROOT/'tests/delivery-review.json'
        review=json.loads(review_path.read_text()) if review_path.exists() else {}
        pending=review_gate(fingerprints,review)
        if review.get('repository_url'):
            url=review['repository_url']
            source=(ROOT/'提交文档/项目完成报告.md').read_text().splitlines()[0]
            if url not in source: pending.append('report first line does not contain confirmed repository URL')
            if url not in report['checks']['documents']['first_page_text']:pending.append('PDF first page does not contain confirmed repository URL')
        report['checks']['user_review']={'result':'pending' if pending else 'passed','reasons':list(pending)}
        report['checks']['github_delivery']=github_check(review,fingerprints,report['version']['head'],report['version']['dirty'])
        pending+=report['checks']['github_delivery']['reasons']
        report['checks']['v1_restore']={'result':'passed','create':'v1-fresh/result.json','restore':'v1-restored/result.json'}
        report['technical_result']='passed'
        report['pending']=pending
        report['requirements']={req:{**record,'result':'passed' if all(report['checks'].get(g,{}).get('result')=='passed'
            for g in record['evidence']) else 'pending'}
            for req,record in mapping['requirements'].items()}
        report['result']='pending_user_review_or_publication' if pending else 'passed'
    except Exception as exc:
        import traceback
        report['failure']=f'{type(exc).__name__}: {exc}'
        (output/'failure.log').write_text(traceback.format_exc())
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'result':report['result'],'technical_result':report['technical_result'],
                      'evidence':str(output/'report.json'),'failure':report.get('failure'),'pending':report.get('pending')},ensure_ascii=False))
    return 0 if report['result']=='passed' else 1
