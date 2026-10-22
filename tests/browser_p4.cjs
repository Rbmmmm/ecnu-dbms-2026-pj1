const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const report={checks:{},observations:{},console:[],screenshots:[],flow:'独立复查：Unicode密码注册登录 → 本人问卷分页 → 匿名选择返回修改再提交'};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.SURVEY_CHROMIUM?{executablePath:process.env.SURVEY_CHROMIUM}:{})});
 try{
  const owner=await browser.newContext({viewport:{width:1280,height:900}}), visitor=await browser.newContext({viewport:{width:390,height:844}});
  const a=await owner.newPage(),b=await visitor.newPage(),errors=[];
  for(const p of[a,b]){p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text(),url:m.location().url});});}
  const headers={'Origin':base,'X-Survey-Request':'1'},suffix=process.env.SURVEY_TEST_RUN_ID.slice(0,8);
  async function api(ctx,url,method='get',data){const r=await ctx.request[method](base+'/api/v1'+url,{headers,...(data===undefined?{}:{data})});assert.ok(r.ok(),await r.text());return r.status()===204?null:r.json();}
  async function check(name,fn){try{await fn();report.checks[name]='passed';}catch(e){report.checks[name]='failed';report.observations[name]={failure:e.message};await a.screenshot({path:path.join(output,'p4-failure-'+name+'.png'),fullPage:true});}}
  await check('unicode_password_register_login',async()=>{
   const password='😀'.repeat(70),username='p4unicode_'+suffix;
   await a.goto(base+'/register');await a.getByLabel('用户名',{exact:true}).fill(username);await a.getByLabel('密码',{exact:true}).fill(password);
   assert.equal(await a.getByLabel('密码',{exact:true}).inputValue(),password,'70码点合法密码不得被UTF16上限截断');
   await a.getByRole('button',{name:'注册',exact:true}).click();await a.waitForURL(base+'/login?next=%2Fsurveys');
   await a.getByLabel('用户名',{exact:true}).fill(username);await a.getByLabel('密码',{exact:true}).fill(password);await a.getByRole('button',{name:'登录',exact:true}).click();await a.waitForURL(base+'/surveys');
   assert.equal((await api(owner,'/auth/me')).username,username);
   report.observations.unicode_password_register_login={password_codepoints:70,registered_and_logged_in:true};
  });
  const password='p4-browser-test-pass',ownerName='p4owner_'+suffix,visitorName='p4visitor_'+suffix;
  for(const [ctx,username] of [[owner,ownerName],[visitor,visitorName]]){await api(ctx,'/auth/register','post',{username,password});await api(ctx,'/auth/login','post',{username,password});}
  await check('own_list_pagination_desktop_mobile',async()=>{
   const ids=[];for(let i=0;i<22;i++)ids.push((await api(owner,'/surveys','post',{title:'P4列表 '+String(i).padStart(2,'0')})).id);
   await api(visitor,'/surveys','post',{title:'他人问卷不可混入'});
   await a.goto(base+'/surveys');await a.getByRole('heading',{name:'我的问卷',exact:true}).waitFor();
   assert.equal(new URL(a.url()).pathname,'/surveys');assert.equal(await a.title(),'MongoDB 问卷系统');
   assert.equal(await a.locator('section.card').count(),20);assert.equal(await a.getByText('他人问卷不可混入',{exact:true}).count(),0);
   assert.deepEqual(await a.locator('section.card h2').allTextContents(),Array.from({length:20},(_,i)=>'P4列表 '+String(21-i).padStart(2,'0')));
   await a.getByRole('button',{name:'下一页',exact:true}).click();await a.getByText('P4列表 00',{exact:true}).waitFor();assert.equal(await a.locator('section.card').count(),2);
   await a.setViewportSize({width:390,height:844});assert.ok(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await a.screenshot({path:path.join(output,'p4-list-mobile.png'),fullPage:true});report.screenshots.push('p4-list-mobile.png');
   await a.getByRole('button',{name:'上一页',exact:true}).click();await a.getByText('P4列表 21',{exact:true}).waitFor();assert.equal(await a.locator('section.card').count(),20);
   await a.setViewportSize({width:1280,height:900});report.observations.own_list_pagination_desktop_mobile={own:22,pages:[20,2],other_excluded:true};
  });
  await check('anonymous_choice_survives_backtracking',async()=>{
   const draft=await api(owner,'/surveys','post',{title:'P4匿名回退'}),qid=crypto.randomUUID();
   const saved=await api(owner,'/surveys/'+draft.id+'/draft','put',{revision:draft.revision,title:draft.title,description:'同一份填写的选择应保留',allow_anonymous:true,allow_repeat:true,deadline_at:null,questions:[{id:qid,kind:'text',title:'回退修改内容',required:true,validation:{},options:[],rules:[]}]});
   const survey=await api(owner,'/surveys/'+draft.id+'/publish','post',{revision:saved.revision});
   await b.goto(base+'/survey/'+survey.id);await b.getByLabel('回答内容',{exact:true}).fill('修改前');await b.getByRole('button',{name:'下一步',exact:true}).click();
   const anonymous=b.getByLabel('匿名提交（创建者看不到账号身份）',{exact:true});await anonymous.waitFor();assert.equal(await anonymous.isChecked(),false);await anonymous.check();
   await b.getByRole('button',{name:'返回修改',exact:true}).click();await b.getByLabel('回答内容',{exact:true}).fill('修改后');await b.getByRole('button',{name:'下一步',exact:true}).click();await anonymous.waitFor();
   assert.equal(await anonymous.isChecked(),true,'返回修改必须保留已显式选择的匿名状态');
   assert.equal((await api(owner,'/surveys/'+survey.id+'/statistics')).submission_count,0);
   await b.screenshot({path:path.join(output,'p4-anonymous-backtrack.png'),fullPage:true});report.screenshots.push('p4-anonymous-backtrack.png');
   await b.getByRole('button',{name:'确认提交',exact:true}).click();await b.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
   const contents=await api(owner,'/surveys/'+survey.id+'/questions/'+qid+'/contents');assert.equal(contents.total,1);assert.equal(contents.items[0].value,'修改后');assert.equal(contents.items[0].anonymous,true);assert.deepEqual(Object.keys(contents.items[0]).sort(),['anonymous','created_at','value']);
   await b.getByRole('button',{name:'再填一份',exact:true}).click();await b.getByLabel('回答内容',{exact:true}).fill('新一份');await b.getByRole('button',{name:'下一步',exact:true}).click();await anonymous.waitFor();assert.equal(await anonymous.isChecked(),false);
   assert.equal((await api(owner,'/surveys/'+survey.id+'/statistics')).submission_count,1);
   report.observations.anonymous_choice_survives_backtracking={survey_id:survey.id,question_id:qid,anonymous:true,value:'修改后',new_fill_default_anonymous:false};
  });
  assert.deepEqual(errors,[]);assert.deepEqual(report.console,[]);report.checks.page_console_health='passed';
  report.environment={base,viewports:[[1280,900],[390,844]],browser:'Playwright; Browser plugin not available'};
  fs.writeFileSync(path.join(output,'browser-p4.json'),JSON.stringify(report,null,2));
  assert.ok(Object.values(report.checks).every(value=>value==='passed'),JSON.stringify(report.checks));
 }finally{await browser.close();}
})().catch(e=>{report.failure=e.message;fs.writeFileSync(path.join(output,'browser-p4.json'),JSON.stringify(report,null,2));console.error(e);process.exitCode=1;});
