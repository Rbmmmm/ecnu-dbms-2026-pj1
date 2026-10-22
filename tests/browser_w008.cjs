const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const report={checks:{},console:[],screenshots:[],flow:'F-01全类型 → r5刷新 → 内容分页与匿名 → 北京时间截止/清除/关闭 → HTML与登录回跳'};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.SURVEY_CHROMIUM?{executablePath:process.env.SURVEY_CHROMIUM}:{})});
 try{
  const contexts=await Promise.all(Array.from({length:4},()=>browser.newContext({viewport:{width:1280,height:900},timezoneId:'America/Los_Angeles'})));
  const pages=await Promise.all(contexts.map(c=>c.newPage())),[a,b,c,d]=pages,[owner,u1,u2,u3]=contexts,errors=[];
  function observe(p){p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text(),url:m.location().url});});}
  pages.forEach(observe);
  const headers={'Origin':base,'X-Survey-Request':'1'},password='browser-test-only-pass',suffix=process.env.SURVEY_TEST_RUN_ID.slice(0,10);
  const names=['statsowner_','statsu1_','statsu2_','statsu3_'].map(n=>n+suffix);
  async function api(ctx,url,method='get',data){const response=await ctx.request[method](base+'/api/v1'+url,{headers,...(data===undefined?{}:{data})});assert.ok(response.ok(),await response.text());return response.json();}
  for(let i=0;i<contexts.length;i++){await api(contexts[i],'/auth/register','post',{username:names[i],password});await api(contexts[i],'/auth/login','post',{username:names[i],password});}
  const qs=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures-f01.json'))),[q1,q2,q3,q4]=qs,[A,B,C]=q1.options.map(o=>o.id),[APPLE,BANANA]=q2.options.map(o=>o.id);
  const draft=await api(owner,'/surveys','post',{title:'P3 F-01 全类型统计'});
  const configured=await api(owner,'/surveys/'+draft.id+'/draft','put',{revision:draft.revision,title:draft.title,description:'固定样本与手算结果核对',deadline_at:null,allow_anonymous:true,allow_repeat:true,questions:qs});
  const survey=await api(owner,'/surveys/'+draft.id+'/publish','post',{revision:configured.revision}),sid=survey.id;
  const body=(answers,anonymous=false)=>({content_version:1,submission_key:crypto.randomUUID(),anonymous,answers});
  const empty=()=>Object.fromEntries(qs.map(q=>[q.id,null]));
  const answers=(single,multiple,text,number)=>({[q1.id]:single,[q2.id]:multiple,[q3.id]:text,[q4.id]:number});
  await api(u1,'/fill/'+sid+'/responses','post',body(answers(A,[APPLE,BANANA],'甲','0')));
  await api(u1,'/fill/'+sid+'/responses','post',body(answers(B,[BANANA],'乙','10'),true));
  // r3 traverses all four question types through the actual page and explicit confirmation.
  await c.goto(base+'/survey/'+sid);
  async function next(){await c.getByRole('button',{name:'下一步',exact:true}).click();}
  await c.getByLabel('A',{exact:true}).check();await next();await c.getByLabel('苹果',{exact:true}).check();await next();await c.getByLabel('回答内容',{exact:true}).fill('丙');await next();await c.getByLabel('数字答案',{exact:true}).fill('20');await next();await c.getByRole('heading',{name:'确认答案',exact:true}).waitFor();
  await c.getByLabel('匿名提交（创建者看不到账号身份）',{exact:true}).check();await c.getByRole('button',{name:'确认提交',exact:true}).click();await c.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
  await api(u3,'/fill/'+sid+'/responses','post',body(empty()));
  await a.goto(base+'/surveys/'+sid+'/statistics');await a.getByText('有效答卷 4 份 · 去重填写 3 人',{exact:true}).waitFor();await a.getByText('平均值：10.00',{exact:true}).waitFor();
  const card=q=>a.locator('[data-stat-question-id="'+q.id+'"]');
  const rows=async q=>card(q).locator('tr').evaluateAll(rs=>rs.slice(1).map(r=>Array.from(r.querySelectorAll('td'),td=>td.textContent)));
  assert.deepEqual(await rows(q1),[['A','2','2'],['B','1','1'],['C','0','0']]);assert.deepEqual(await rows(q2),[['苹果','2'],['香蕉','2'],['葡萄','0']]);
  await card(q3).getByLabel('每页条数',{exact:true}).selectOption('2');await card(q3).getByText('全部内容 3 条；当前 1—2 条',{exact:true}).waitFor();
  assert.deepEqual((await rows(q3)).map(r=>r.slice(0,2)),[['丙','匿名'],['乙','匿名']]);
  await card(q3).getByRole('button',{name:'下一页',exact:true}).click();await card(q3).getByText('全部内容 3 条；当前 3—3 条',{exact:true}).waitFor();assert.deepEqual((await rows(q3)).map(r=>r.slice(0,2)),[['甲',names[1]]]);
  await card(q3).getByRole('button',{name:'上一页',exact:true}).click();await card(q3).getByText('全部内容 3 条；当前 1—2 条',{exact:true}).waitFor();
  await card(q4).getByLabel('每页条数',{exact:true}).selectOption('2');await card(q4).getByText('全部内容 3 条；当前 1—2 条',{exact:true}).waitFor();assert.deepEqual((await rows(q4)).map(r=>r[0]),['20','10']);
  await card(q4).getByRole('button',{name:'下一页',exact:true}).click();await card(q4).getByText('全部内容 3 条；当前 3—3 条',{exact:true}).waitFor();assert.deepEqual((await rows(q4)).map(r=>r[0]),['0']);
  const contents=await api(owner,'/surveys/'+sid+'/questions/'+q3.id+'/contents?limit=2');for(const item of contents.items)assert.deepEqual(Object.keys(item).sort(),['anonymous','created_at','value']);
  const four=await api(owner,'/surveys/'+sid+'/statistics');assert.equal(four.questions[0].respondent_count,2);assert.equal(four.questions[0].submission_count,3);assert.equal(four.questions[1].answered_submission_count,3);
  assert.ok(!/respondent_id|submission_key|request_digest|statsu[123]_/.test(JSON.stringify(four)+JSON.stringify(contents)));
  report.checks.f01_all_types_counts_and_paginated_anonymous_contents='passed';
  await a.screenshot({path:path.join(output,'p3-anonymous-pagination.png'),fullPage:true});report.screenshots.push('p3-anonymous-pagination.png');
  const r5=body({...empty(),[q1.id]:C}),receipt=await api(u2,'/fill/'+sid+'/responses','post',r5);
  await a.getByRole('button',{name:'刷新统计',exact:true}).click();await a.getByText('有效答卷 5 份 · 去重填写 3 人',{exact:true}).waitFor();await a.getByText('平均值：10.00',{exact:true}).waitFor();assert.deepEqual(await rows(q1),[['A','2','2'],['B','1','1'],['C','1','1']]);
  const five=await api(owner,'/surveys/'+sid+'/statistics');assert.deepEqual(five.questions.slice(1),four.questions.slice(1));assert.equal(five.questions[0].submission_count,4);assert.equal(five.questions[0].respondent_count,2);for(const q of qs)assert.deepEqual(await api(owner,'/surveys/'+sid+'/questions/'+q.id+'/statistics'),five.questions.find(item=>item.id===q.id));
  report.checks.r5_refresh_whole_and_single_statistics='passed';
  await a.screenshot({path:path.join(output,'p3-statistics-desktop.png'),fullPage:true});report.screenshots.push('p3-statistics-desktop.png');
  await a.setViewportSize({width:390,height:844});await a.reload();await a.getByText('平均值：10.00',{exact:true}).waitFor();assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await a.screenshot({path:path.join(output,'p3-statistics-mobile.png'),fullPage:true});report.screenshots.push('p3-statistics-mobile.png');await a.setViewportSize({width:1280,height:900});
  await a.goto(base+'/surveys/'+sid+'/manage');const future=new Date(Date.now()+3600000);future.setMilliseconds(123);const beijing=new Date(future.getTime()+8*3600000).toISOString().slice(0,23);
  await a.getByLabel('截止时间（北京时间，可留空）',{exact:true}).fill(beijing);await a.getByRole('button',{name:'保存截止时间',exact:true}).click();await a.locator('#current-deadline').filter({hasNotText:'未设置'}).waitFor();
  let state=await api(owner,'/surveys/'+sid);assert.equal(Date.parse(state.deadline_at),future.getTime());assert.deepEqual(state.questions,survey.questions);assert.equal(state.content_version,1);
  await a.screenshot({path:path.join(output,'p3-deadline-beijing.png'),fullPage:true});report.screenshots.push('p3-deadline-beijing.png');
  await a.getByRole('button',{name:'清除截止时间',exact:true}).click();await a.locator('#current-deadline').filter({hasText:'未设置'}).waitFor();assert.equal((await api(owner,'/surveys/'+sid)).deadline_at,null);
  await a.getByRole('button',{name:'关闭问卷',exact:true}).click();await a.getByText('状态：已关闭。发布后内容冻结。',{exact:true}).waitFor();assert.equal(await a.getByRole('button',{name:'保存截止时间',exact:true}).count(),0);
  await c.goto(base+'/survey/'+sid);await c.getByText('问卷已关闭或截止。',{exact:true}).waitFor();assert.deepEqual(await api(u2,'/fill/'+sid+'/responses','post',r5),receipt);
  for(const changed of[{...r5,submission_key:crypto.randomUUID()},{...r5,answers:empty()}])assert.equal((await u2.request.post(base+'/api/v1/fill/'+sid+'/responses',{headers,data:changed})).status(),409);
  assert.equal((await api(owner,'/surveys/'+sid+'/statistics')).submission_count,5);report.checks.beijing_deadline_clear_close_and_retry='passed';
  const html='<img src=x onerror="window.injected=true">',qid=crypto.randomUUID();
  const xss=await api(owner,'/surveys','post',{title:html}),saved=await api(owner,'/surveys/'+xss.id+'/draft','put',{revision:xss.revision,title:html,description:html,allow_anonymous:true,allow_repeat:false,deadline_at:null,questions:[{id:qid,title:html,kind:'text',required:false,options:[],rules:[],validation:{}}]});
  await api(owner,'/surveys/'+xss.id+'/publish','post',{revision:saved.revision});await api(u2,'/fill/'+xss.id+'/responses','post',body({[qid]:html},true));
  await a.goto(base+'/surveys/'+xss.id+'/statistics');await a.locator('.content-value').filter({hasText:html}).waitFor();assert.equal(await a.locator('#app img,#app script').count(),0);assert.equal(await a.evaluate(()=>window.injected),undefined);assert.equal(await a.getByRole('cell',{name:'匿名',exact:true}).count(),1);
  const xraw=await api(owner,'/surveys/'+xss.id+'/questions/'+qid+'/contents');assert.deepEqual(Object.keys(xraw.items[0]).sort(),['anonymous','created_at','value']);assert.equal(xraw.items[0].value,html);
  const safe=await browser.newContext(),loginPage=await safe.newPage();observe(loginPage);
  for(const next of['https://evil.example/path','//evil.example/path','/\\evil.example/path','/\u0009/evil.example']){
   await loginPage.goto(base+'/login?next='+encodeURIComponent(next));await loginPage.getByLabel('用户名',{exact:true}).fill(names[2]);await loginPage.getByLabel('密码',{exact:true}).fill(password);await loginPage.getByRole('button',{name:'登录',exact:true}).click();await loginPage.waitForURL(base+'/surveys');assert.equal(new URL(loginPage.url()).origin,base);
  }
  assert.deepEqual(errors,[]);assert.deepEqual(report.console,[]);report.checks.literal_html_anonymous_whitelist_safe_login_console_mobile='passed';
  report.summary={survey_id:sid,question_ids:qs.map(q=>q.id),submission_count:5,respondent_count:3,number_average:'10.00',anonymous_submissions:2,empty_submissions:1,xss_survey_id:xss.id};
  fs.writeFileSync(path.join(output,'browser-w008.json'),JSON.stringify(report,null,2));console.log('P3 browser F-01, r5, pagination, anonymity, Beijing deadline, close/retry and input safety passed');
 }finally{await browser.close();}
})().catch(e=>{report.failure=e.message;fs.writeFileSync(path.join(output,'browser-w008.json'),JSON.stringify(report,null,2));console.error(e);process.exitCode=1;});
