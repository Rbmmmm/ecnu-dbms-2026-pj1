const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const fixture=JSON.parse(fs.readFileSync(path.join(output,'fixture.json'),'utf8'));
const report={checks:{},failures:{},console:[],screenshots:[],surveys:[],viewports:{desktop:[1280,900],mobile:[390,844]}};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SURVEY_CHROMIUM});
 async function run(name,test,importSource=true){
  const p=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],gates=[];
  p.setDefaultTimeout(7000);
  p.on('pageerror',e=>errors.push(e.message));
  p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text()});});
  async function api(method,url,body,status=200){
   const r=await p.request.fetch(base+'/api/v1'+url,{method,headers:{Origin:base,'X-Survey-Request':'1'},...(body?{data:body}:{})});
   assert.equal(r.status(),status,await r.text());return r.json();
  }
  async function shot(label){const file=name+'-'+label+'.png';await p.screenshot({path:path.join(output,file),fullPage:true});report.screenshots.push(file);}
  async function gate(url){
   let seen,failed,release,finished,timer;
   const received=new Promise((resolve,reject)=>{seen=resolve;failed=reject;timer=setTimeout(()=>reject(new Error('request gate timed out: '+url)),15000);});
   received.catch(()=>{});
   const resume=new Promise(r=>release=r),done=new Promise(r=>finished=r);
   const g={received,release:()=>{clearTimeout(timer);release();},done};gates.push(g);
   await p.route(base+'/api/v1'+url,async route=>{
    try{const response=await route.fetch();const body=await response.json();clearTimeout(timer);seen({body:route.request().postDataJSON(),response:body,status:response.status()});await resume;await route.fulfill({response});}
    catch(e){failed(e);await route.abort().catch(()=>{});}
    finally{finished();}
   });
   return g;
  }
  async function locked(){
   const enabled=await p.locator('#app input,#app textarea,#app select,#app button').evaluateAll(nodes=>nodes.filter(n=>!n.disabled).map(n=>n.id||n.textContent));
   assert.deepEqual(enabled,[],'all editor controls must remain locked until the action finishes');
  }
  async function editable(){
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).isDisabled(),false);
   assert.equal(await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).isDisabled(),false);
   assert.equal(await p.locator('fieldset[data-kind=single]').getByLabel('题干',{exact:true}).isDisabled(),true,'source payload must stay read-only');
   assert.equal(await p.getByRole('button',{name:'上移题目',exact:true}).first().isDisabled(),true,'original disabled controls must stay disabled');
  }
  async function save(){
   const control=await p.getByRole('button',{name:'保存草稿',exact:true}).elementHandle();await control.click();
   await p.waitForFunction(n=>!n.isConnected,control);await p.getByRole('status').filter({hasText:'草稿已保存'}).waitFor();
  }
  let sid;
  try{
   await p.goto(base+'/login');await p.getByLabel('用户名',{exact:true}).fill('s2_owner');await p.getByLabel('密码',{exact:true}).fill('stage2-test-pass');
   await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');
   let s=await api('POST','/surveys',{title:name},201);sid=s.id;
   const q={id:randomUUID(),kind:'number',title:'原题干',required:false,options:[],rules:[],validation:{min_value:null,max_value:null,integer_only:false}};
   s=await api('PUT','/surveys/'+sid+'/draft',{revision:s.revision,title:name,description:'',allow_anonymous:false,allow_repeat:false,deadline_at:null,questions:[q]});
   if(importSource)await api('POST','/surveys/'+sid+'/question-imports',{revision:s.revision,instance_id:randomUUID(),question_id:fixture.question.id,question_version_id:fixture.version.id});
   await p.goto(base+'/surveys/'+sid+'/edit');await p.getByRole('button',{name:'从题库选入',exact:true}).waitFor();
   assert.equal(await p.title(),'MongoDB 问卷系统');assert.ok((await p.locator('main').innerText()).length>20);
   assert.equal(await p.locator('vite-error-overlay,nextjs-portal').count(),0);
   await test({p,sid,api,gate,locked,editable,save,shot});
   assert.deepEqual(errors,[]);
   const final=await api('GET','/surveys/'+sid);
   assert.equal(final.schema_version,importSource?2:1);
   report.surveys.push({id:sid,title:final.title,state:final.state,question_title:final.questions[0].title,schema_version:final.schema_version});
   report.checks[name]='passed';
  }catch(e){report.checks[name]='failed';report.failures[name]=e.stack;await shot('failure');}
  finally{for(const g of gates)g.release();await p.unrouteAll({behavior:'wait'});await p.close();}
 }
 try{
  await run('save_inflight_and_next_edit',async({p,sid,api,gate,locked,editable,save,shot})=>{
   await p.getByLabel('问卷标题',{exact:true}).fill('本次保存标题');
   await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).fill('本次保存题干');
   const g=await gate('/surveys/'+sid+'/draft');
   const old=await p.getByRole('button',{name:'保存草稿',exact:true}).elementHandle();await old.click();
   const request=await g.received;assert.equal(request.status,200);await locked();await shot('locked-desktop');
   await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await shot('locked-mobile');
   g.release();await g.done;await p.waitForFunction(n=>!n.isConnected,old);await p.unroute(base+'/api/v1/surveys/'+sid+'/draft');
   await editable();assert.equal(await p.getByLabel('问卷标题',{exact:true}).inputValue(),'本次保存标题');
   assert.equal((await api('GET','/surveys/'+sid)).questions[0].title,'本次保存题干');
   await p.getByLabel('问卷标题',{exact:true}).fill('保存后的再次编辑');
   await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).fill('再次编辑题干');await save();await editable();
   const saved=await api('GET','/surveys/'+sid);assert.equal(saved.title,'保存后的再次编辑');assert.equal(saved.questions[0].title,'再次编辑题干');await shot('saved-mobile');
  });
  await run('publish_both_requests_locked',async({p,sid,api,gate,locked,shot})=>{
   await p.getByLabel('问卷标题',{exact:true}).fill('发布时的标题');
   const draft=await gate('/surveys/'+sid+'/draft'),publish=await gate('/surveys/'+sid+'/publish');
   let puts=0,posts=0;p.on('request',r=>{if(r.url().endsWith('/draft')&&r.method()==='PUT')puts++;if(r.url().endsWith('/publish')&&r.method()==='POST')posts++;});
   await p.getByRole('button',{name:'发布问卷',exact:true}).click();const saved=await draft.received;await locked();assert.equal(posts,0);
   draft.release();const published=await publish.received;await locked();assert.equal(published.body.revision,saved.response.revision);assert.equal(published.status,200);
   await shot('publish-locked');publish.release();await p.waitForURL('**/manage');
   assert.equal(puts,1);assert.equal(posts,1);const result=await api('GET','/surveys/'+sid);assert.equal(result.title,'发布时的标题');assert.equal(result.state,'published');await shot('published');
  },false);
  await run('save_422_preserves_input',async({p,sid,api,editable,save})=>{
   const original=await api('GET','/surveys/'+sid),title='长'.repeat(201);
   await p.getByLabel('问卷标题',{exact:true}).fill(title);await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).fill('错误后保留题干');
   await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('alert').filter({hasText:'输入格式不正确'}).waitFor();await editable();
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).inputValue(),title);assert.equal((await api('GET','/surveys/'+sid)).revision,original.revision);
   await p.getByLabel('问卷标题',{exact:true}).fill('纠错后保存');await save();assert.equal((await api('GET','/surveys/'+sid)).questions[0].title,'错误后保留题干');
  });
  await run('publish_422_rebinds_inputs',async({p,sid,api,editable})=>{
   await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).fill('');
   await p.getByRole('button',{name:'发布问卷',exact:true}).click();await p.getByRole('alert').filter({hasText:'请修复问卷后发布'}).waitFor();await editable();
   assert.equal((await api('GET','/surveys/'+sid)).state,'draft');
   await p.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).fill('发布失败后修改题干');
   await p.getByRole('button',{name:'发布问卷',exact:true}).click();await p.waitForURL('**/manage');
   const final=await api('GET','/surveys/'+sid);assert.equal(final.state,'published');assert.equal(final.questions[0].title,'发布失败后修改题干');
  });
  await run('save_409_keeps_local_and_remote',async({p,sid,api,editable})=>{
   await p.getByLabel('问卷标题',{exact:true}).fill('本地待保存标题');
   let count=0;
   await p.route(base+'/api/v1/surveys/'+sid+'/draft',async route=>{
    count++;const body=route.request().postDataJSON();await api('PUT','/surveys/'+sid+'/draft',{...body,title:'另一请求已保存标题'});
    const response=await route.fetch();assert.equal(response.status(),409);await route.fulfill({response});
   });
   await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('alert').filter({hasText:'状态或版本已改变'}).waitFor();await editable();
   assert.equal(count,1);assert.equal(await p.getByLabel('问卷标题',{exact:true}).inputValue(),'本地待保存标题');
   assert.equal((await api('GET','/surveys/'+sid)).title,'另一请求已保存标题');
  });
  await run('save_unknown_no_auto_replay',async({p,sid,api,editable})=>{
   await p.getByLabel('问卷标题',{exact:true}).fill('响应未知时保留标题');let requests=0;const bodies=[];
   p.on('request',r=>{if(r.method()==='PUT'&&r.url().endsWith('/draft'))bodies.push(r.postDataJSON());});
   await p.route(base+'/api/v1/surveys/'+sid+'/draft',async route=>{requests++;const response=await route.fetch();assert.equal(response.status(),200);await route.fulfill({response,body:'{'});});
   await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();await editable();
   assert.equal(await p.getByLabel('问卷标题',{exact:true}).inputValue(),'响应未知时保留标题');assert.equal(requests,1);
   const savedRevision=(await api('GET','/surveys/'+sid)).revision;
   await p.unroute(base+'/api/v1/surveys/'+sid+'/draft');
   await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('alert').filter({hasText:'状态或版本已改变'}).waitFor();
   assert.equal(bodies.length,2);assert.deepEqual(bodies[1],bodies[0]);
   const final=await api('GET','/surveys/'+sid);assert.equal(final.title,'响应未知时保留标题');assert.equal(final.revision,savedRevision);
  });
  assert.ok(report.console.every(x=>/Failed to load resource: the server responded with a status of (409|422)/.test(x.text)),JSON.stringify(report.console));
  assert.ok(Object.values(report.checks).every(x=>x==='passed'),JSON.stringify(report.failures));
 }finally{await browser.close();fs.writeFileSync(path.join(output,'browser-editor-save.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
