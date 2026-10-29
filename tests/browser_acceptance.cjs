const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const f=JSON.parse(fs.readFileSync(path.join(output,'fixture.json'),'utf8'));
const report={checks:{},console:[],screenshots:[],surveys:[],viewports:{desktop:[1280,900],mobile:[390,844]}},errors=[];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SURVEY_CHROMIUM});
 const p=await browser.newPage({viewport:{width:1280,height:900}});
 p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text()});});
 async function api(url){const r=await p.request.get(base+'/api/v1'+url);assert.equal(r.status(),200,await r.text());return r.json();}
 async function shot(name){await p.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name);}
 try{
  await p.goto(base+'/login');await p.getByLabel('用户名',{exact:true}).fill('s2_owner');await p.getByLabel('密码',{exact:true}).fill('stage2-test-pass');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');
  for(const action of ['选入题目','从题库选入']){
   await p.goto(base+'/surveys');await p.getByRole('button',{name:'创建问卷',exact:true}).click();await p.waitForURL('**/edit');
   await p.getByRole('button',{name:action,exact:true}).waitFor();const sid=new URL(p.url()).pathname.split('/')[2];report.surveys.push(sid);
   await p.getByLabel('问卷标题',{exact:true}).fill('独立验收 '+action);
   let writes=0;const watch=r=>{if(r.method()==='PUT'&&r.url().endsWith('/draft')||r.method()==='POST'&&r.url().endsWith('/question-imports'))writes++;};p.on('request',watch);
   await p.getByRole('button',{name:action,exact:true}).click();await p.waitForLoadState('networkidle');
   assert.equal(writes,0,'unsaved import must not implicitly save or import');p.off('request',watch);
   await p.getByRole('alert').filter({hasText:'请先保存草稿'}).waitFor();
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).inputValue(),'独立验收 '+action);
   await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('status').filter({hasText:'草稿已保存'}).waitFor();
   await p.route('**/question-imports',async route=>{const body=route.request().postDataJSON();const response=await route.fetch({postData:JSON.stringify({...body,instance_id:'invalid-uuid'})});assert.equal(response.status(),422);await route.fulfill({response});});
   await p.getByRole('button',{name:action,exact:true}).click();await p.getByRole('alert').filter({hasText:'输入格式不正确'}).waitFor();await p.unroute('**/question-imports');
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).isDisabled(),false);assert.equal((await api('/surveys/'+sid)).questions.length,0);
   report.checks[action+'_definite_rejection_unlocks_without_writing']='passed';
   let received,release,done,first=true,originalBody,retryBody;
   const seen=new Promise(r=>received=r),resume=new Promise(r=>release=r),completed=new Promise(r=>done=r);
   await p.route('**/question-imports',async route=>{if(first){first=false;originalBody=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);received();await resume;await route.fulfill({response,body:'{'});done();}else{retryBody=route.request().postDataJSON();await route.continue();}});
   await p.getByRole('button',{name:action,exact:true}).click();await seen;
   assert.equal(await p.getByRole('button',{name:action,exact:true}).isDisabled(),true,'in-flight import action must also be locked');
   for(const name of ['保存草稿','发布问卷',action==='选入题目'?'从题库选入':'选入题目'])assert.equal(await p.getByRole('button',{name,exact:true}).isDisabled(),true);
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).isDisabled(),true);
   release();await completed;await p.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();
   assert.equal(await p.getByRole('button',{name:action,exact:true}).isDisabled(),false);
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).isDisabled(),true);
   await p.getByRole('button',{name:action,exact:true}).click();await p.locator('fieldset[data-kind=single]').waitFor();await p.unroute('**/question-imports');
   assert.deepEqual(retryBody,originalBody);assert.equal((await api('/surveys/'+sid)).questions.length,1);
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).isDisabled(),false);
   report.checks[action+'_explicit_save_inflight_unknown_lock_and_original_instance_retry']='passed';
  }
  await p.goto(base+'/questions?question='+f.question.id+'&version='+f.version.id);
  await p.getByRole('button',{name:'派生新版',exact:true}).click();await p.getByRole('heading',{name:'常用年龄 · v2 · 草稿',exact:true}).waitFor();
  const area=()=>p.locator('#version-editor');
  await area().getByRole('button',{name:'删除选项',exact:true}).nth(1).click();
  await area().getByRole('button',{name:'上移选项',exact:true}).nth(1).click();
  await area().getByRole('button',{name:'删除选项',exact:true}).nth(2).click();
  await area().getByRole('button',{name:'添加选项',exact:true}).click();await area().getByLabel('选项 3',{exact:true}).fill('E');
  await p.getByRole('button',{name:'保存共享名单',exact:true}).click();await p.getByRole('alert').filter({hasText:'请先保存题目草稿'}).waitFor();
  await p.getByRole('button',{name:'保存题目草稿',exact:true}).click();await p.getByRole('status').filter({hasText:'题目草稿已保存'}).waitFor();
  report.derived_version=new URL(p.url()).searchParams.get('version');const saved=await api('/questions/'+f.question.id+'/versions/'+report.derived_version);
  assert.deepEqual(saved.payload.options.map(x=>x.text),['C','A','E']);assert.equal(saved.payload.options[0].id,f.version.payload.options[2].id);assert.equal(saved.payload.options[1].id,f.version.payload.options[0].id);
  assert.deepEqual((await api('/questions/'+f.question.id+'/versions/'+f.version.id)).payload,f.version.payload);
  await p.getByRole('button',{name:'定稿版本',exact:true}).click();await p.getByRole('heading',{name:'常用年龄 · v2 · 已定稿',exact:true}).waitFor();
  assert.equal(await area().getByLabel('选项 1',{exact:true}).isDisabled(),true);
  await shot('independent-choice-desktop.png');await p.setViewportSize({width:390,height:844});await shot('independent-choice-mobile.png');
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await p.title(),'MongoDB 问卷系统');assert.ok((await p.locator('main').innerText()).length>20);assert.equal(await p.locator('vite-error-overlay,nextjs-portal').count(),0);
  assert.deepEqual(errors,[]);assert.ok(report.console.every(x=>/Failed to load resource: the server responded with a status of 422/.test(x.text)),JSON.stringify(report.console));
  report.checks.choice_delete_reorder_add_stable_ids_and_frozen_parent='passed';report.checks.identity_nonblank_console_responsive_interaction='passed';
 }catch(e){report.failure=e.stack;await shot('failure.png');throw e;}
 finally{await browser.close();fs.writeFileSync(path.join(output,'browser-acceptance.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
