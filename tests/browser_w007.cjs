const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const report={checks:{},console:[],screenshots:[],flow:'编辑全部题型和规则 → F-02回退/pending/END/无复活 → 首条命中 → 显式提交'};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.SURVEY_CHROMIUM?{executablePath:process.env.SURVEY_CHROMIUM}:{})});
 try{
  const owner=await browser.newContext({viewport:{width:1280,height:900}}),filler=await browser.newContext({viewport:{width:1280,height:900}});
  const a=await owner.newPage(),b=await filler.newPage(),errors=[];
  for(const p of[a,b]){p.on('pageerror',e=>errors.push(e.message));p.on('console',msg=>{if(['error','warning'].includes(msg.type()))report.console.push({type:msg.type(),text:msg.text(),url:msg.location().url});});}
  async function account(p,name){
   await p.goto(base+'/register');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');await p.getByRole('button',{name:'注册',exact:true}).click();await p.waitForURL('**/login*');
   await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');
  }
  const suffix=process.env.SURVEY_TEST_RUN_ID.slice(0,10);
  await account(a,'ruleowner_'+suffix);await a.getByRole('button',{name:'创建问卷',exact:true}).click();await a.waitForURL('**/edit');
  const sid=new URL(a.url()).pathname.split('/')[2];
  const wideTitle='😀'.repeat(150);await a.getByLabel('问卷标题',{exact:true}).fill(wideTitle);assert.equal(await a.getByLabel('问卷标题',{exact:true}).inputValue(),wideTitle);
  await a.getByLabel('问卷标题',{exact:true}).fill('W-007 F-02 回退验收');await a.getByLabel('允许每个账号多次填写',{exact:true}).check();
  const card=i=>a.locator('fieldset[data-question-id]').nth(i);
  for(const [i,kind,title] of[[0,'单选','J1 分支选择'],[1,'多选','J2 多选条件'],[2,'数字','J3 年龄'],[3,'文本','J4 完成说明'],[4,'文本','J5 补充']]){
   await a.getByRole('button',{name:'添加'+kind+'题',exact:true}).click();await card(i).getByLabel('题干',{exact:true}).fill(title);
   if([0,1,3].includes(i))await card(i).getByLabel('必须回答',{exact:true}).check();
   if(i<2){
    const values=i===0?['A','B','结束']:['x','y','z'];
    await card(i).getByLabel('选项 1',{exact:true}).fill(values[0]);await card(i).getByLabel('选项 2',{exact:true}).fill(values[1]);
    await card(i).getByRole('button',{name:'添加选项',exact:true}).click();await card(i).getByLabel('选项 3',{exact:true}).fill(values[2]);
   }else if(i===2){await card(i).getByLabel('最小值',{exact:true}).fill('0');await card(i).getByLabel('最大值',{exact:true}).fill('120');await card(i).getByLabel('仅允许整数',{exact:true}).check();}
   else{await card(i).getByLabel('最少字数',{exact:true}).fill('1');await card(i).getByLabel('最多字数',{exact:true}).fill('10');}
  }
  const wideQuestion='😀'.repeat(600),wideOption='😀'.repeat(150);await card(0).getByLabel('题干',{exact:true}).fill(wideQuestion);assert.equal(await card(0).getByLabel('题干',{exact:true}).inputValue(),wideQuestion);await card(0).getByLabel('题干',{exact:true}).fill('J1 分支选择');await card(0).getByLabel('选项 1',{exact:true}).fill(wideOption);assert.equal(await card(0).getByLabel('选项 1',{exact:true}).inputValue(),wideOption);await card(0).getByLabel('选项 1',{exact:true}).fill('A');
  const rule=(i,r)=>card(i).locator('[data-rule-index]').nth(r);
  for(const [r,option,target] of[[0,'A','第 3 题：J3 年龄'],[1,'B','第 2 题：J2 多选条件'],[2,'结束','结束（进入确认页）']]){
   await card(0).getByRole('button',{name:'添加规则',exact:true}).click();await rule(0,r).getByLabel('匹配选项',{exact:true}).selectOption({label:option});await rule(0,r).getByLabel('跳转目标',{exact:true}).selectOption({label:target});
  }
  await card(1).getByRole('button',{name:'添加规则',exact:true}).click();await rule(1,0).getByLabel('跳转目标',{exact:true}).selectOption({label:'第 4 题：J4 完成说明'});
  await card(1).getByRole('button',{name:'添加规则',exact:true}).click();await rule(1,1).getByLabel('匹配方式',{exact:true}).selectOption('all');await rule(1,1).getByLabel('条件选项：y',{exact:true}).check();await rule(1,1).getByLabel('跳转目标',{exact:true}).selectOption({label:'第 5 题：J5 补充'});
  await card(2).getByRole('button',{name:'添加规则',exact:true}).click();await rule(2,0).getByLabel('条件数值',{exact:true}).fill('18');await rule(2,0).getByLabel('跳转目标',{exact:true}).selectOption({label:'第 5 题：J5 补充'});
  await a.getByRole('button',{name:'保存草稿',exact:true}).click();await a.getByRole('status').filter({hasText:'草稿已保存；待修复项：'}).waitFor();
  const before=await(await owner.request.get(base+'/api/v1/surveys/'+sid)).json();
  assert.deepEqual(before.questions.map(q=>q.kind),['single','multiple','number','text','text']);
  assert.deepEqual(before.questions.map(q=>q.required),[true,true,false,true,false]);
  assert.deepEqual(before.questions[2].validation,{min_value:'0',max_value:'120',integer_only:true});
  for(const index of [3,4])assert.deepEqual(before.questions[index].validation,{min_length:1,max_length:10});
  const [q1,q2,q3,q4,q5]=before.questions;
  assert.deepEqual(q1.rules,[{op:'eq',operand:q1.options[0].id,target:q3.id},{op:'eq',operand:q1.options[1].id,target:q2.id},{op:'eq',operand:q1.options[2].id,target:'END'}]);
  assert.deepEqual(q2.rules,[{op:'any',operand:[q2.options[0].id],target:q4.id},{op:'all',operand:[q2.options[0].id,q2.options[1].id],target:q5.id}]);
  assert.deepEqual(q3.rules,[{op:'gte',operand:'18',target:q5.id}]);
  // Exercise editing operations, then restore the same final configuration and verify stable IDs.
  await card(0).getByRole('button',{name:'下移选项',exact:true}).nth(0).click();
  await card(0).getByRole('button',{name:'上移选项',exact:true}).nth(1).click();
  await rule(1,0).getByRole('button',{name:'下移规则',exact:true}).click();
  await rule(1,1).getByRole('button',{name:'上移规则',exact:true}).click();
  await card(2).getByRole('button',{name:'添加规则',exact:true}).click();
  await rule(2,1).getByRole('button',{name:'删除规则',exact:true}).click();
  await a.getByRole('button',{name:'添加单选题',exact:true}).click();
  const temporaryID=await card(5).getAttribute('data-question-id');
  await card(5).getByRole('button',{name:'添加规则',exact:true}).click();
  await card(5).getByLabel('题型',{exact:true}).selectOption('number');
  assert.equal(await card(5).getAttribute('data-question-id'),temporaryID);
  assert.equal(await card(5).locator('[data-rule-index]').count(),0);
  assert.equal(await card(5).getByLabel('选项 1',{exact:true}).count(),0);
  await card(5).getByLabel('最小值',{exact:true}).fill('0');
  await card(5).getByLabel('题型',{exact:true}).selectOption('text');
  assert.equal(await card(5).getAttribute('data-question-id'),temporaryID);
  assert.equal(await card(5).getByLabel('最小值',{exact:true}).count(),0);
  await card(5).getByRole('button',{name:'删除题目',exact:true}).click();
  await a.getByRole('button',{name:'保存草稿',exact:true}).click();
  await a.getByRole('status').filter({hasText:'草稿已保存；待修复项：'}).waitFor();
  const edited=await(await owner.request.get(base+'/api/v1/surveys/'+sid)).json();
  assert.deepEqual(edited.questions,before.questions);
  await a.reload();await card(2).getByLabel('最小值',{exact:true}).waitFor();
  const reread=await(await owner.request.get(base+'/api/v1/surveys/'+sid)).json();assert.deepEqual(reread.questions,before.questions);
  await a.screenshot({path:path.join(output,'w007-editor.png'),fullPage:true});report.screenshots.push('w007-editor.png');
  await a.getByRole('button',{name:'发布问卷',exact:true}).click();await a.waitForURL('**/manage');report.checks.editor_all_types_constraints_rules='passed';
  const qs=reread.questions,[j1,j2,j3,j4,j5]=qs.map(q=>q.id),[A,B,END]=qs[0].options.map(o=>o.id),[X,Y]=qs[1].options.map(o=>o.id);
  await account(b,'rulefiller_'+suffix);await b.goto(base+'/survey/'+sid);
  const statistics=async()=>await(await owner.request.get(base+'/api/v1/surveys/'+sid+'/statistics')).json();
  async function next(){const waiting=b.waitForResponse(r=>r.url()===base+'/api/v1/fill/'+sid+'/evaluate');await b.getByRole('button',{name:'下一步',exact:true}).click();return(await waiting).json();}
  async function confirm(){await b.getByRole('heading',{name:'确认答案',exact:true}).waitFor();}
  async function modify(title){await b.getByRole('button',{name:'修改：'+title,exact:true}).click();}
  await b.getByLabel('B',{exact:true}).check();await next();await b.getByLabel('y',{exact:true}).check();await next();await b.getByLabel('数字答案',{exact:true}).fill('18');await next();await b.getByLabel('回答内容',{exact:true}).fill('保留');await next();await confirm();
  await modify('J1 分支选择');await b.getByLabel('A',{exact:true}).check();let result=await next();await confirm();
  assert.deepEqual(result.removed_question_ids,[j2]);assert.deepEqual(result.path,[j1,j3,j5]);assert.equal(await b.locator('#app li').filter({hasText:'J2'}).count(),0);
  await modify('J3 年龄');await b.getByLabel('数字答案',{exact:true}).fill('17');result=await next();
  assert.equal(result.current_question_id,j4);assert.deepEqual(result.pending_answers,{[j5]:'保留'});assert.equal(await b.getByRole('heading',{name:'确认答案',exact:true}).count(),0);
  const invalid='超过十码点的回答应保留用于修改';await b.getByLabel('回答内容',{exact:true}).fill(invalid);await next();await b.getByRole('alert').filter({hasText:'回答超过上限'}).waitFor();assert.equal(await b.getByLabel('回答内容',{exact:true}).inputValue(),invalid);
  await b.getByLabel('回答内容',{exact:true}).fill('完成');result=await next();await confirm();assert.deepEqual(result.path,[j1,j3,j4,j5]);assert.equal(result.active_answers[j5],'保留');
  await b.screenshot({path:path.join(output,'w007-pending-reused.png'),fullPage:true});report.screenshots.push('w007-pending-reused.png');report.checks.backtrack_pending_reuse_and_error_preservation='passed';
  // Delay an old evaluation, navigate back and finish a new branch, then deliver the old response.
  await modify('J3 年龄');let release,seenResolve,delayed=false;const seen=new Promise(resolve=>seenResolve=resolve),gate=new Promise(resolve=>release=resolve);
  await b.route('**/evaluate',async route=>{if(!delayed){delayed=true;seenResolve();await gate;}await route.continue();});
  await b.getByRole('button',{name:'下一步',exact:true}).click();await seen;
  assert.equal(await b.getByLabel('数字答案',{exact:true}).isDisabled(),true);
  await b.getByRole('button',{name:'返回上一题',exact:true}).click();await b.getByLabel('结束',{exact:true}).check();result=await next();await confirm();assert.deepEqual(result.path,[j1]);
  const old=b.waitForResponse(r=>r.url().endsWith('/evaluate')&&r.request().postDataJSON().answers[j1]===A);release();await old;await b.evaluate(()=>new Promise(requestAnimationFrame));await b.unroute('**/evaluate');
  assert.equal(await b.locator('#app li').count(),1);assert.equal(await b.locator('#app li').first().innerText(),'J1 分支选择：结束');assert.equal((await statistics()).submission_count,0);
  await b.screenshot({path:path.join(output,'w007-end-confirmation.png'),fullPage:true});report.screenshots.push('w007-end-confirmation.png');report.checks.old_evaluation_discarded_and_end_not_submitted='passed';
  // A deliberately delayed HTTP validation failure must also lose to the new branch.
  await modify('J1 分支选择');await b.getByLabel('A',{exact:true}).check();await next();
  let releaseError,seenErrorResolve;const errorGate=new Promise(r=>releaseError=r),errorSeen=new Promise(r=>seenErrorResolve=r);let heldError=false;
  await b.route('**/evaluate',async route=>{if(!heldError){heldError=true;seenErrorResolve();await errorGate;await route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:{code:'TEST_DELAYED',message:'过期错误不应显示',fields:[]}})});}else await route.continue();});
  await b.getByRole('button',{name:'下一步',exact:true}).click();await errorSeen;await b.getByRole('button',{name:'返回上一题',exact:true}).click();await b.getByLabel('结束',{exact:true}).check();await next();await confirm();
  const lateError=b.waitForResponse(r=>r.url().endsWith('/evaluate')&&r.status()===422);releaseError();await lateError;await b.evaluate(()=>new Promise(requestAnimationFrame));await b.unroute('**/evaluate');
  assert.equal(await b.locator('#error').isVisible(),false);assert.equal(await b.locator('#app li').count(),1);
  const expectedConsole=report.console.filter(m=>m.type==='error'&&m.url===base+'/api/v1/fill/'+sid+'/evaluate'&&m.text==='Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)');assert.equal(expectedConsole.length,1);report.expected_console=expectedConsole;report.console=report.console.filter(m=>!expectedConsole.includes(m));
  await modify('J1 分支选择');await b.getByLabel('A',{exact:true}).check();result=await next();assert.equal(result.current_question_id,j3);assert.equal(await b.getByLabel('数字答案',{exact:true}).inputValue(),'');
  await b.getByLabel('数字答案',{exact:true}).fill('18');await next();assert.equal(await b.getByLabel('回答内容',{exact:true}).inputValue(),'');await next();await confirm();assert.equal(await b.locator('#app li').count(),3);report.checks.end_clear_and_no_resurrection='passed';
  await b.getByRole('button',{name:'确认提交',exact:true}).click();await b.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
  await b.getByRole('button',{name:'再填一份',exact:true}).click();await b.getByLabel('B',{exact:true}).check();await next();await b.getByLabel('x',{exact:true}).check();await b.getByLabel('y',{exact:true}).check();result=await next();assert.equal(result.current_question_id,j4);
  await b.getByLabel('回答内容',{exact:true}).fill('完成');await next();await next();await confirm();await b.getByRole('button',{name:'确认提交',exact:true}).click();await b.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
  report.checks.first_matching_rule_and_explicit_submission='passed';
  const summary=await statistics();assert.equal(summary.submission_count,2);assert.equal(summary.respondent_count,1);
  // Regression for Unicode codepoints: native maxlength must not truncate supplementary characters.
  const headers={'Origin':base,'X-Survey-Request':'1'};
  const auxiliary=await(await owner.request.post(base+'/api/v1/surveys',{headers,data:{title:'码点输入回归'}})).json();
  const textID=crypto.randomUUID();const draft={revision:auxiliary.revision,title:auxiliary.title,description:'',allow_anonymous:false,allow_repeat:false,deadline_at:null,questions:[{id:textID,kind:'text',title:'码点内容',required:false,validation:{max_length:10000},options:[],rules:[]}]};
  const saved=await(await owner.request.put(base+'/api/v1/surveys/'+auxiliary.id+'/draft',{headers,data:draft})).json();
  assert.equal((await owner.request.post(base+'/api/v1/surveys/'+auxiliary.id+'/publish',{headers,data:{revision:saved.revision}})).status(),200);
  await b.goto(base+'/survey/'+auxiliary.id);const long='😀'.repeat(6000);await b.getByLabel('回答内容',{exact:true}).fill(long);assert.equal(await b.getByLabel('回答内容',{exact:true}).inputValue(),long);await b.getByRole('button',{name:'下一步',exact:true}).click();await confirm();assert.equal(Array.from((await b.locator('#app li').innerText()).split('：')[1]).length,6000);report.checks.unicode_input_not_utf16_truncated='passed';
  await a.goto(base+'/surveys/'+sid+'/statistics');await a.getByText('有效答卷 2 份 · 去重填写 1 人',{exact:true}).waitFor();await a.getByText('平均值：18.00',{exact:true}).waitFor();assert.equal(await a.getByText('本题答案已保存；完整统计与内容分页将在W-008完成。',{exact:true}).count(),0);
  await a.setViewportSize({width:390,height:844});await a.reload();await a.getByText('有效答卷 2 份 · 去重填写 1 人',{exact:true}).waitFor();assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await a.screenshot({path:path.join(output,'w007-mobile.png'),fullPage:true});report.screenshots.push('w007-mobile.png');
  assert.deepEqual(errors,[]);assert.deepEqual(report.console,[]);report.checks.page_identity_console_and_mobile='passed';
  report.summary={survey_id:sid,question_ids:[j1,j2,j3,j4,j5],option_ids:{A,B,END,X,Y},submission_count:2,respondent_count:1,expected_paths:[[j1,j3,j5],[j1,j2,j4,j5]]};
  fs.writeFileSync(path.join(output,'browser-w007.json'),JSON.stringify(report,null,2));console.log('W-007 browser: all types/rules, F-02 backtracking/pending/END, stale responses and Unicode passed');
 }finally{await browser.close();}
})().catch(e=>{report.failure=e.message;fs.writeFileSync(path.join(output,'browser-w007.json'),JSON.stringify(report,null,2));console.error(e);process.exitCode=1;});
