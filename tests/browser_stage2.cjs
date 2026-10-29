const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const report={checks:{},console:[],screenshots:[],viewport:{desktop:[1280,900],mobile:[390,844]},survey_ids:[]};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SURVEY_CHROMIUM});
 const errors=[];
 try{
  const owner=await browser.newContext({viewport:{width:1280,height:900}}),filler=await browser.newContext({viewport:{width:390,height:844}});
  const a=await owner.newPage(),b=await filler.newPage();
  for(const p of[a,b]){p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text()});});}
  const suffix=process.env.SURVEY_TEST_RUN_ID.slice(0,8),headers={'Origin':base,'X-Survey-Request':'1'};
  async function account(p,name){await p.goto(base+'/register');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('stage2-browser-pass');await p.getByRole('button',{name:'注册',exact:true}).click();await p.waitForURL('**/login*');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('stage2-browser-pass');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');}
  async function api(url){const r=await owner.request.get(base+'/api/v1'+url);assert.equal(r.status(),200,await r.text());return r.json();}
  async function shot(p,name){await p.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name);}
  await account(a,'s2web_owner_'+suffix);await account(b,'s2web_filler_'+suffix);
  await a.getByRole('link',{name:'我的题目',exact:true}).click();await a.getByRole('heading',{name:'我的题目',exact:true}).waitFor();
  assert.equal(await a.title(),'MongoDB 问卷系统');assert.equal(new URL(a.url()).pathname,'/questions');
  // A choice payload must keep its original option UUIDs after a lost creation response.
  await a.getByRole('button',{name:'创建常用题',exact:true}).click();await a.getByRole('alert').filter({hasText:'输入格式不正确'}).waitFor();
  assert.equal(await a.getByLabel('题目标签',{exact:true}).isDisabled(),false);
  report.checks.invalid_family_input_can_be_corrected='passed';
  await a.getByLabel('题目标签',{exact:true}).fill('创建重试单选');await a.getByLabel('新题类型',{exact:true}).selectOption('single');
  let createLost=false,createdInput,createRetry;
  await a.route('**/versions',async route=>{
   if(route.request().method()!=='POST'){await route.continue();return;}
   const body=route.request().postDataJSON();
   if(!createLost){createLost=true;createdInput=body;const r=await route.fetch();assert.equal(r.status(),201);await route.fulfill({response:r,body:'{'});}
   else{createRetry=body;await route.continue();}
  });
  await a.getByRole('button',{name:'创建常用题',exact:true}).click();await a.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();
  await a.getByRole('button',{name:'创建常用题',exact:true}).click();await a.waitForURL('**/questions?question=*&version=*');
  assert.deepEqual(createRetry,createdInput);await a.unroute('**/versions');
  report.checks.choice_creation_same_payload_retry='passed';
  await a.getByLabel('题目标签',{exact:true}).fill('常用年龄');await a.getByLabel('新题类型',{exact:true}).selectOption('number');
  await a.getByRole('button',{name:'创建常用题',exact:true}).click();await a.waitForURL(url=>url.searchParams.get('question')!==new URL(base+'/').searchParams.get('question')&&url.searchParams.has('version'));
  await a.getByRole('heading',{name:'常用年龄 · v1 · 草稿',exact:true}).waitFor();
  const qid=new URL(a.url()).searchParams.get('question'),vid=new URL(a.url()).searchParams.get('version');
  report.question_id=qid;report.v1_id=vid;
  await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('alert').filter({hasText:'题干不能为空'}).waitFor();
  assert.equal(await a.locator('#version-editor').getByLabel('题干',{exact:true}).isDisabled(),false);
  report.checks.invalid_seal_can_be_edited_and_retried='passed';
  const editor=a.locator('#version-editor');
  await editor.getByLabel('题干',{exact:true}).fill('你的年龄');await editor.getByLabel('最小值',{exact:true}).fill('0');await editor.getByLabel('最大值',{exact:true}).fill('120');await editor.getByLabel('仅允许整数',{exact:true}).check();
  await a.getByRole('button',{name:'保存题目草稿',exact:true}).click();await a.getByRole('status').filter({hasText:'题目草稿已保存'}).waitFor();
  let sealLost=false;
  await a.route('**/seal',async route=>{if(!sealLost){sealLost=true;const r=await route.fetch();assert.equal(r.status(),200);await route.fulfill({response:r,body:'{'});}else await route.continue();});
  await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();
  assert.equal(await a.locator('#version-editor').getByLabel('题干',{exact:true}).isDisabled(),true);assert.equal(await a.getByRole('button',{name:'保存题目草稿',exact:true}).isDisabled(),true);
  await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('heading',{name:'常用年龄 · v1 · 已定稿',exact:true}).waitFor();await a.unroute('**/seal');
  assert.equal(await a.locator('#version-editor').getByLabel('题干',{exact:true}).isDisabled(),true);
  report.checks.version_edit_seal_lost_response_retry='passed';await shot(a,'stage2-v1-sealed.png');
  let importLost=false,importBody,retryBody;
  for(let i=0;i<2;i++){
   await a.goto(base+'/surveys');await a.getByRole('button',{name:'创建问卷',exact:true}).click();await a.waitForURL('**/edit');
   const sid=new URL(a.url()).pathname.split('/')[2];report.survey_ids.push(sid);
   await a.getByLabel('问卷标题',{exact:true}).fill('复用年龄 '+(i+1));await a.getByLabel('允许匿名填写',{exact:true}).check();
   await a.getByLabel('已有题目版本',{exact:true}).selectOption({label:'常用年龄 · v1 · 你的年龄'});
   if(i===0)await a.route('**/question-imports',async route=>{if(!importLost){importLost=true;importBody=route.request().postDataJSON();const r=await route.fetch();assert.equal(r.status(),200);await route.fulfill({response:r,body:'{'});}else{retryBody=route.request().postDataJSON();await route.continue();}});
   await a.getByRole('button',{name:'选入题目',exact:true}).click();
   if(i===0){await a.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();assert.equal(await a.getByLabel('问卷标题',{exact:true}).isDisabled(),true);await a.getByRole('button',{name:'选入题目',exact:true}).click();await a.unroute('**/question-imports');assert.deepEqual(importBody,retryBody);}
   await a.locator('fieldset[data-kind="number"]').waitFor();
   assert.equal(await a.locator('fieldset[data-kind="number"]').getByLabel('题干',{exact:true}).isDisabled(),true);
   await a.getByRole('button',{name:'添加单选题',exact:true}).click();
   let gate=a.locator('fieldset[data-kind="single"]');await gate.getByLabel('题干',{exact:true}).fill('继续或跳过');await gate.getByLabel('必须回答',{exact:true}).check();await gate.getByLabel('选项 1',{exact:true}).fill('继续');await gate.getByLabel('选项 2',{exact:true}).fill('跳过');
   await gate.getByRole('button',{name:'上移题目',exact:true}).click();gate=a.locator('fieldset[data-kind="single"]');await gate.getByRole('button',{name:'添加规则',exact:true}).click();await gate.getByLabel('匹配选项',{exact:true}).selectOption({label:'跳过'});
   await a.getByRole('button',{name:'保存草稿',exact:true}).click();await a.getByRole('status').filter({hasText:'草稿已保存'}).waitFor();
   await a.getByRole('button',{name:'发布问卷',exact:true}).click();await a.waitForURL('**/manage');
   await b.goto(base+'/survey/'+sid);await b.getByLabel('跳过',{exact:true}).check();await b.getByRole('button',{name:'下一步',exact:true}).click();await b.getByRole('heading',{name:'确认答案',exact:true}).waitFor();
   await b.getByRole('button',{name:'返回修改',exact:true}).click();await b.getByLabel('继续',{exact:true}).check();await b.getByRole('button',{name:'下一步',exact:true}).click();await b.getByRole('group',{name:'你的年龄（可跳过）',exact:true}).waitFor();
   await b.getByLabel('数字答案',{exact:true}).fill('121');await b.getByRole('button',{name:'下一步',exact:true}).click();await b.getByRole('alert').filter({hasText:'数字大于最大值'}).waitFor();
   await b.getByLabel('数字答案',{exact:true}).fill(i===0?'0':'18');await b.getByRole('button',{name:'下一步',exact:true}).click();await b.getByRole('heading',{name:'确认答案',exact:true}).waitFor();
   if(i===0)await b.getByLabel('匿名提交（创建者看不到账号身份）',{exact:true}).check();
   await shot(b,'stage2-fill-'+(i+1)+'-mobile.png');
   await b.getByRole('button',{name:'确认提交',exact:true}).click();await b.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
   await a.goto(base+'/surveys/'+sid+'/statistics');await a.getByText('有效答卷 1 份 · 去重填写 1 人',{exact:true}).waitFor();
   await a.getByText('平均值：'+(i===0?'0.00':'18.00'),{exact:false}).waitFor();await shot(a,'stage2-statistics-'+(i+1)+'.png');
  }
  report.checks.two_survey_ui_import_publish_branch_validation_submit_statistics='passed';report.checks.import_lost_response_same_instance_retry='passed';
  const old=[];for(const sid of report.survey_ids)old.push({management:await api('/surveys/'+sid),statistics:await api('/surveys/'+sid+'/statistics')});
  await a.goto(base+'/questions?question='+qid+'&version='+vid);await a.getByRole('button',{name:'派生新版',exact:true}).click();await a.getByRole('heading',{name:'常用年龄 · v2 · 草稿',exact:true}).waitFor();
  await a.locator('#version-editor').getByLabel('题干',{exact:true}).fill('年龄（扩展范围）');await a.locator('#version-editor').getByLabel('最大值',{exact:true}).fill('200');await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('heading',{name:'常用年龄 · v2 · 已定稿',exact:true}).waitFor();report.v2_id=new URL(a.url()).searchParams.get('version');
  for(let i=0;i<2;i++){assert.deepEqual(await api('/surveys/'+report.survey_ids[i]),old[i].management);assert.deepEqual(await api('/surveys/'+report.survey_ids[i]+'/statistics'),old[i].statistics);}
  report.checks.derive_v2_old_surveys_and_statistics_unchanged='passed';
  await a.setViewportSize({width:390,height:844});await a.locator('#version-editor').scrollIntoViewIfNeeded();await a.locator('#version-editor').screenshot({path:path.join(output,'stage2-v2-mobile.png')});report.screenshots.push('stage2-v2-mobile.png');
  for(const p of[a,b])assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);assert.ok(report.console.every(x=>/Failed to load resource: the server responded with a status of 422/.test(x.text)),JSON.stringify(report.console));
  report.checks.page_identity_nonblank_console_and_responsive='passed';
 }catch(e){report.failure=e.stack;throw e;}
 finally{await browser.close();fs.writeFileSync(path.join(output,'browser-stage2.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
