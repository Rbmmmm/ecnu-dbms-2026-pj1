const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const report={checks:{},console:[],screenshots:[],versions:[],surveys:[],viewports:{desktop:[1280,900],mobile:[390,844]}},errors=[];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SURVEY_CHROMIUM});
 try{
  const contexts=await Promise.all([1280,390,390].map(width=>browser.newContext({viewport:{width,height:width===1280?900:844}})));
  const [a,b,c]=await Promise.all(contexts.map(x=>x.newPage()));
  for(const p of[a,b,c]){p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text()});});}
  const suffix=process.env.SURVEY_TEST_RUN_ID.slice(0,8),names=['甲用户_','乙用户_','丙用户_'].map(x=>x+suffix);
  async function register(p,name){await p.goto(base+'/register');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('management-browser-pass');await p.getByRole('button',{name:'注册',exact:true}).click();await p.waitForURL('**/login*');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('management-browser-pass');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');}
  async function api(p,url,method='GET',data,status=200){const r=await p.request.fetch(base+'/api/v1'+url,{method,data,headers:{'Origin':base,'X-Survey-Request':'1'}});assert.equal(r.status(),status,await r.text());return r.json();}
  async function shot(p,name){await p.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name);}
  async function loseOnce(p,pattern,method,action,retry,locked){
   let bodies=[],fired=false;await p.route(pattern,async route=>{if(route.request().method()!==method){await route.continue();return;}bodies.push(route.request().postDataJSON());if(!fired){fired=true;const r=await route.fetch();assert.ok([200,201].includes(r.status()),await r.text());await route.fulfill({response:r,body:'{'});}else await route.continue();});
   await action();await p.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();if(locked)await locked();await retry();await p.unroute(pattern);assert.equal(bodies.length,2);assert.deepEqual(bodies[0],bodies[1]);
  }
  for(let i=0;i<3;i++)await register([a,b,c][i],names[i]);
  await a.goto(base+'/questions');await a.getByLabel('题目标签',{exact:true}).fill('共享年龄');await a.getByRole('button',{name:'创建常用题',exact:true}).click();await a.getByRole('heading',{name:'共享年龄 · v1 · 草稿',exact:true}).waitFor();
  const qid=new URL(a.url()).searchParams.get('question');report.question_id=qid;report.versions.push(new URL(a.url()).searchParams.get('version'));
  const editor=a.locator('#version-editor');await editor.getByLabel('题干',{exact:true}).fill('你的年龄');await editor.getByLabel('最小值',{exact:true}).fill('0');await editor.getByLabel('最大值',{exact:true}).fill('120');await editor.getByLabel('仅允许整数',{exact:true}).check();await a.getByRole('button',{name:'保存标签',exact:true}).click();await a.getByRole('alert').filter({hasText:'请先保存题目草稿'}).waitFor();assert.equal(await editor.getByLabel('题干',{exact:true}).inputValue(),'你的年龄');
  let releaseDraft,seenDraft;const draftSeen=new Promise(resolve=>seenDraft=resolve),draftRelease=new Promise(resolve=>releaseDraft=resolve);
  await a.route('**/draft',async route=>{const response=await route.fetch();seenDraft();await draftRelease;await route.fulfill({response});});
  await a.getByRole('button',{name:'保存题目草稿',exact:true}).click();await draftSeen;assert.equal(await editor.getByLabel('题干',{exact:true}).isDisabled(),true);assert.equal(await a.getByRole('button',{name:'保存共享名单',exact:true}).isDisabled(),true);releaseDraft();await a.getByRole('status').filter({hasText:'题目草稿已保存'}).waitFor();await a.unroute('**/draft');report.checks.delayed_draft_save_locks_edits='passed';
  await loseOnce(a,'**/draft','PUT',()=>a.getByRole('button',{name:'定稿版本',exact:true}).click(),()=>a.getByRole('button',{name:'定稿版本',exact:true}).click(),async()=>{assert.equal(await a.getByRole('button',{name:'保存共享名单',exact:true}).isDisabled(),true);assert.equal(await a.getByRole('button',{name:'保存标签',exact:true}).isDisabled(),true);});await a.getByRole('heading',{name:'共享年龄 · v1 · 已定稿',exact:true}).waitFor();report.checks.unsaved_version_guard_and_preseal_save_unknown_retry='passed';
  const shareInput=()=>a.getByLabel('共享给指定用户名（逗号分隔，留空撤回全部）',{exact:true});
  await shareInput().fill('不存在的用户');await a.getByRole('button',{name:'保存共享名单',exact:true}).click();await a.getByRole('alert').filter({hasText:'已存在的其他用户'}).waitFor();assert.equal(await shareInput().isDisabled(),false);
  await shareInput().fill(names[1]);
  await loseOnce(a,'**/sharing','PUT',()=>a.getByRole('button',{name:'保存共享名单',exact:true}).click(),()=>a.getByRole('button',{name:'保存共享名单',exact:true}).click(),async()=>assert.equal(await shareInput().isDisabled(),true));
  await a.getByRole('heading',{name:'共享年龄 · v1 · 已定稿',exact:true}).waitFor();report.checks.sharing_invalid_correction_and_unknown_retry='passed';
  await b.goto(base+'/questions?scope=shared');await b.getByRole('link',{name:'打开题目',exact:true}).click();await b.getByText('只读共享：可选用已定稿版本。',{exact:true}).waitFor();
  assert.equal(await b.getByRole('button',{name:'派生新版',exact:true}).count(),0);assert.equal(await b.getByRole('button',{name:'保存共享名单',exact:true}).count(),0);
  await c.goto(base+'/questions?question='+qid);await c.getByRole('alert').filter({hasText:'题目不存在'}).waitFor();assert.equal(await c.locator('#version-editor').count(),0);report.checks.three_accounts_shared_readonly_and_denied='passed';
  await b.goto(base+'/question-banks');await b.getByRole('button',{name:'创建题库',exact:true}).click();await b.getByRole('alert').filter({hasText:'输入格式不正确'}).waitFor();assert.equal(await b.getByLabel('新题库名称',{exact:true}).isDisabled(),false);
  await b.getByLabel('新题库名称',{exact:true}).fill('乙的常用题库');
  await loseOnce(b,'**/api/v1/question-banks','POST',()=>b.getByRole('button',{name:'创建题库',exact:true}).click(),()=>b.getByRole('button',{name:'创建题库',exact:true}).click(),async()=>assert.equal(await b.getByLabel('新题库名称',{exact:true}).isDisabled(),true));
  await b.locator('#bank-editor').waitFor();const bid=new URL(b.url()).searchParams.get('bank');report.bank_id=bid;
  await b.getByRole('button',{name:'加入题库',exact:true}).click();await b.getByRole('heading',{name:'共享年龄 · 默认 v1',exact:true}).waitFor();
  await shot(b,'management-bank-v1-mobile.png');report.checks.bank_creation_add_and_explicit_default='passed';
  async function createFromBank(title){await b.goto(base+'/surveys');await b.getByRole('button',{name:'创建问卷',exact:true}).click();await b.waitForURL('**/edit');const sid=new URL(b.url()).pathname.split('/')[2];await b.getByLabel('问卷标题',{exact:true}).fill(title);await b.getByLabel('允许匿名填写',{exact:true}).check();await b.getByRole('button',{name:'从题库选入',exact:true}).click();await b.locator('fieldset[data-kind="number"]').waitFor();return sid;}
  const sid=await createFromBank('乙的旧年龄卷');report.surveys.push(sid);
  await b.getByRole('button',{name:'从题库选入',exact:true}).click();await b.locator('fieldset[data-kind="number"]').nth(1).waitFor();
  await b.getByLabel('允许向题目所有者提供汇总：你的年龄',{exact:true}).check();await b.getByRole('button',{name:'保存本家族授权',exact:true}).click();await b.getByText('你的年龄：已开启',{exact:true}).waitFor();
  await b.getByRole('button',{name:'发布问卷',exact:true}).click();await b.waitForURL('**/manage');
  const old=await api(b,'/surveys/'+sid);assert.equal(old.questions.length,2);assert.notEqual(old.questions[0].id,old.questions[1].id);assert.equal(old.question_result_grants[0].allowed_at_publish,true);
  await c.goto(base+'/survey/'+sid);await c.getByText(/以下题目的汇总结果将提供给题目所有者/).waitFor();
  for(const value of ['18','30']){await c.getByLabel('数字答案',{exact:true}).fill(value);await c.getByRole('button',{name:'下一步',exact:true}).click();}
  await c.getByRole('heading',{name:'确认答案',exact:true}).waitFor();await c.getByLabel('匿名提交（创建者看不到账号身份）',{exact:true}).check();await c.getByRole('button',{name:'确认提交',exact:true}).click();await c.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
  await b.goto(base+'/surveys/'+sid+'/statistics');await b.getByText('平均值：18.00',{exact:false}).waitFor();await b.getByText('平均值：30.00',{exact:false}).waitFor();const oldStats=await api(b,'/surveys/'+sid+'/statistics');
  await api(a,'/surveys/'+sid+'/statistics','GET',undefined,404);report.checks.double_instances_grant_publish_notice_fill_and_owner_statistics='passed';
  await a.goto(base+'/questions?question='+qid+'&version='+report.versions[0]);await loseOnce(a,'**/versions','POST',()=>a.getByRole('button',{name:'派生新版',exact:true}).click(),()=>a.getByRole('button',{name:'派生新版',exact:true}).click(),async()=>{assert.equal(await a.getByRole('button',{name:'保存共享名单',exact:true}).isDisabled(),true);assert.equal(await a.getByRole('button',{name:'保存标签',exact:true}).isDisabled(),true);});await a.getByRole('heading',{name:'共享年龄 · v2 · 草稿',exact:true}).waitFor();report.checks.derive_unknown_key_retry_locks_other_management='passed';
  await a.locator('#version-editor').getByLabel('题干',{exact:true}).fill('年龄（扩展范围）');await a.locator('#version-editor').getByLabel('最大值',{exact:true}).fill('200');await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('heading',{name:'共享年龄 · v2 · 已定稿',exact:true}).waitFor();report.versions.push(new URL(a.url()).searchParams.get('version'));
  await a.getByRole('link',{name:'v1 已定稿',exact:true}).click();await a.getByRole('heading',{name:'共享年龄 · v1 · 已定稿',exact:true}).waitFor();
  await loseOnce(a,'**/versions','POST',()=>a.getByRole('button',{name:'恢复此版为独立新版本',exact:true}).click(),()=>a.getByRole('button',{name:'恢复此版为独立新版本',exact:true}).click());
  await a.getByRole('heading',{name:'共享年龄 · v3 · 草稿',exact:true}).waitFor();report.versions.push(new URL(a.url()).searchParams.get('version'));
  await a.getByRole('button',{name:'定稿版本',exact:true}).click();await a.getByRole('heading',{name:'共享年龄 · v3 · 已定稿',exact:true}).waitFor();await a.getByText('操作：restore · 父版本：'+report.versions[0]+' · 恢复来源：'+report.versions[0],{exact:true}).waitFor();await shot(a,'management-history-restore-desktop.png');
  assert.deepEqual(await api(b,'/surveys/'+sid),old);assert.deepEqual(await api(b,'/surveys/'+sid+'/statistics'),oldStats);report.checks.history_branch_restore_key_retry_and_old_snapshot='passed';
  await b.goto(base+'/question-banks?bank='+bid);await b.getByLabel('明确选择默认版：共享年龄',{exact:true}).selectOption(report.versions[1]);await b.getByRole('button',{name:'更新此题默认版',exact:true}).click();await b.getByRole('heading',{name:'共享年龄 · 默认 v2',exact:true}).waitFor();assert.deepEqual(await api(b,'/surveys/'+sid),old);
  const next=await createFromBank('乙的换版卷');report.surveys.push(next);await b.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).waitFor();assert.equal(await b.locator('fieldset[data-kind=number]').getByLabel('题干',{exact:true}).inputValue(),'年龄（扩展范围）');
  await b.getByLabel('问卷标题',{exact:true}).fill('先保存再换版');
  let implicitSaves=0;const countSave=request=>{if(request.url().endsWith('/draft')&&request.method()==='PUT')implicitSaves++;};b.on('request',countSave);
  await b.getByRole('button',{name:'换用版本并清空本题规则',exact:true}).click();await b.getByRole('alert').filter({hasText:'请先保存草稿'}).waitFor();assert.equal(implicitSaves,0);assert.equal(await b.getByLabel('问卷标题',{exact:true}).inputValue(),'先保存再换版');b.off('request',countSave);report.checks.unsaved_survey_guard_has_no_implicit_write='passed';
  await b.getByRole('button',{name:'添加规则',exact:true}).click();await b.getByRole('button',{name:'保存草稿',exact:true}).click();await b.getByRole('status').filter({hasText:'草稿已保存'}).waitFor();
  const initial=await api(b,'/surveys/'+next),iid=initial.questions[0].id;
  await b.getByLabel('换用同家族版本',{exact:true}).selectOption(report.versions[0]);
  await loseOnce(b,'**/source','PUT',()=>b.getByRole('button',{name:'换用版本并清空本题规则',exact:true}).click(),()=>b.getByRole('button',{name:'换用版本并清空本题规则',exact:true}).click(),async()=>assert.equal(await b.getByLabel('问卷标题',{exact:true}).isDisabled(),true));
  await b.waitForFunction(()=>document.querySelector('fieldset[data-kind=number] input[type=text]')?.value==='你的年龄');const swapped=await api(b,'/surveys/'+next);assert.equal(swapped.questions[0].id,iid);assert.deepEqual(swapped.questions[0].rules,[]);assert.equal(swapped.questions[0].validation.max_value,'120');
  await b.getByRole('button',{name:'查看确切来源',exact:true}).click();await b.getByText('v1 · create · 父版本：无 · 恢复来源：无',{exact:true}).waitFor();
  await b.getByRole('button',{name:'发布问卷',exact:true}).click();await b.waitForURL('**/manage');report.checks.bank_default_update_swap_same_iid_clear_rules_no_propagation='passed';
  await a.goto(base+'/questions?question='+qid);await a.locator('#usage-list').getByText('共 2 份问卷；仅显示允许查看的使用关系。',{exact:true}).waitFor();const usage=await api(a,'/questions/'+qid+'/usages');assert.equal(usage.total,2);assert.equal(usage.items.find(x=>x.survey_id===sid).versions[0].instance_count,2);
  for(const x of usage.items)assert.deepEqual(Object.keys(x).sort(),['survey_id','title','state','expired','versions'].sort());await shot(a,'management-usage-desktop.png');
  await shareInput().fill('');await a.getByRole('button',{name:'保存共享名单',exact:true}).click();await a.getByRole('heading',{name:'共享年龄 · v3 · 已定稿',exact:true}).waitFor();
  await b.goto(base+'/questions?scope=shared');assert.equal((await api(b,'/questions?scope=shared')).total,0);await api(b,'/questions/'+qid+'/versions/'+report.versions[0],'GET',undefined,404);
  const exact=await api(b,'/surveys/'+next+'/questions/'+iid+'/source');assert.equal(exact.id,report.versions[0]);
  await b.goto(base+'/question-banks?bank='+bid);await b.getByRole('heading',{name:'使用权已撤回：'+qid,exact:true}).waitFor();await b.getByRole('button',{name:'从题库移除',exact:true}).click();await b.locator('#bank-editor').waitFor();assert.deepEqual((await api(b,'/question-banks/'+bid)).entries,[]);
  await b.goto(base+'/surveys/'+sid+'/manage');await loseOnce(b,'**/question-result-grants/*','PUT',()=>b.getByRole('button',{name:'撤回本家族授权',exact:true}).click(),()=>b.getByRole('button',{name:'撤回本家族授权',exact:true}).click());await b.getByText('你的年龄：已关闭',{exact:true}).waitFor();assert.equal(await b.getByRole('button',{name:'保存本家族授权',exact:true}).count(),0);await shot(b,'management-revoked-grant-mobile.png');
  const revoked=await api(b,'/surveys/'+sid);await api(b,'/surveys/'+sid+'/question-result-grants/'+qid,'PUT',{revision:revoked.revision,enabled:true},409);assert.deepEqual(await api(b,'/surveys/'+sid+'/statistics'),oldStats);
  report.checks.usage_minimum_revoked_history_exact_source_bank_remove_and_grant_revoke='passed';
  for(const p of[a,b,c]){assert.equal(await p.title(),'MongoDB 问卷系统');assert.ok((await p.locator('main').innerText()).length>20);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await p.locator('vite-error-overlay,nextjs-portal').count(),0);}
  assert.deepEqual(errors,[]);assert.ok(report.console.every(x=>/Failed to load resource: the server responded with a status of (404|409|422)/.test(x.text)),JSON.stringify(report.console));report.checks.identity_nonblank_overlay_console_responsive='passed';
 }catch(e){report.failure=e.stack;throw e;}
 finally{await browser.close();fs.writeFileSync(path.join(output,'browser-management.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
