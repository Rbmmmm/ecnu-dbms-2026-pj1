const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.SURVEY_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SURVEY_TEST_ORIGIN,output=process.env.SURVEY_EVIDENCE_DIR;
const f=JSON.parse(fs.readFileSync(path.join(output,'fixture.json'),'utf8'));
const report={checks:{},console:[],screenshots:[],viewports:{desktop:[1280,900],mobile:[390,844]}},errors=[];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SURVEY_CHROMIUM});
 try{
  const contexts=await Promise.all([1280,390,390].map(width=>browser.newContext({viewport:{width,height:width===1280?900:844}})));
  const [a,b,c]=await Promise.all(contexts.map(x=>x.newPage()));
  for(const p of[a,b,c]){p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(['error','warning'].includes(m.type()))report.console.push({type:m.type(),text:m.text()});});}
  async function login(p,name){await p.goto(base+'/login');await p.getByLabel('用户名',{exact:true}).fill(name);await p.getByLabel('密码',{exact:true}).fill('stage2-test-pass');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForURL('**/surveys');}
  async function api(p,url,status=200){const r=await p.request.get(base+'/api/v1'+url);assert.equal(r.status(),status,await r.text());return r.json();}
  async function shot(p,name){await p.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name);}
  await login(a,'s2_owner');await login(b,'用户乙');await login(c,'用户丙');
  await a.goto(base+'/questions?question='+f.question_id);
  await a.getByRole('link',{name:'查看跨问卷统计',exact:true}).click();await a.waitForURL('**/questions/*/statistics');
  const v1=()=>a.locator('[data-version-id="'+f.versions[0]+'"]');
  await v1().getByText('平均值：37.71',{exact:true}).waitFor();
  await a.locator('[data-version-id="'+f.versions[2]+'"] .distribution table').waitFor();
  assert.equal(await a.locator('[data-version-id]').count(),3);
  assert.equal(await a.getByText('平均值：40.00',{exact:true}).count(),1);
  assert.equal(await a.getByText('平均值：60.00',{exact:true}).count(),1);
  assert.ok((await a.locator('#cross-scope').innerText()).includes('所有本人及有效授权问卷'));
  await v1().getByText('纳入 2 份问卷 / 3 个实例',{exact:true}).waitFor();
  await v1().getByText('非空回答 7 次 · 含回答答卷 5 份 · 跨卷去重 2 人',{exact:true}).waitFor();
  await shot(a,'cross-all-versions-desktop.png');report.checks.shared_multiple_surveys_all_versions_and_scope='passed';
  await a.getByLabel('统计版本',{exact:true}).selectOption(f.versions[0]);await v1().locator('.distribution table').waitFor();
  assert.equal(await a.locator('[data-version-id]').count(),1);
  let distSignal,distRelease,distFinished,distHeld=false;const distSeen=new Promise(r=>distSignal=r),distResume=new Promise(r=>distRelease=r),distDone=new Promise(r=>distFinished=r);
  const firstDistribution='**/api/v1/questions/'+f.question_id+'/versions/'+f.versions[0]+'/distribution*';
  await a.route(firstDistribution,async route=>{const response=await route.fetch();if(!distHeld){distHeld=true;distSignal();await distResume;await route.fulfill({response});distFinished();}else await route.fulfill({response});});
  await a.getByLabel('统计版本',{exact:true}).selectOption('all');await distSeen;
  await a.getByLabel('统计版本',{exact:true}).selectOption(f.versions[1]);await a.getByText('平均值：40.00',{exact:true}).waitFor();
  distRelease();await distDone;await a.waitForLoadState('networkidle');await a.unroute(firstDistribution);
  await a.waitForFunction(()=>document.querySelectorAll('[data-version-id]').length===1);
  assert.equal(await a.locator('[data-version-id]').getAttribute('data-version-id'),f.versions[1]);
  await a.getByLabel('统计版本',{exact:true}).selectOption(f.versions[0]);await v1().locator('.distribution table').waitFor();
  await v1().getByLabel('频数每页条数',{exact:true}).selectOption('2');await v1().getByText('精确频数：共 5 种数值；当前 1—2 种',{exact:true}).waitFor();
  await v1().getByLabel('频数每页条数',{exact:true}).selectOption('20');await v1().getByText('精确频数：共 5 种数值；当前 1—5 种',{exact:true}).waitFor();
  assert.equal(await v1().locator('.distribution tr').count(),6);assert.equal(await v1().getByRole('button',{name:'下一页频数',exact:true}).count(),0);
  let sizeSignal,sizeRelease,sizeFinished,sizeHeld=false;const sizeSeen=new Promise(r=>sizeSignal=r),sizeResume=new Promise(r=>sizeRelease=r),sizeDone=new Promise(r=>sizeFinished=r);
  await a.route(firstDistribution,async route=>{const response=await route.fetch();if(!sizeHeld){sizeHeld=true;sizeSignal();await sizeResume;await route.fulfill({response});sizeFinished();}else await route.fulfill({response});});
  await v1().getByLabel('频数每页条数',{exact:true}).selectOption('2');await sizeSeen;
  await v1().getByLabel('频数每页条数',{exact:true}).selectOption('20');await v1().getByText('精确频数：共 5 种数值；当前 1—5 种',{exact:true}).waitFor();
  sizeRelease();await sizeDone;await a.waitForLoadState('networkidle');await a.unroute(firstDistribution);assert.equal(await v1().locator('.distribution tr').count(),6);
  report.checks.delayed_all_versions_and_page_size_changes_do_not_skip_or_restore_old_cards='passed';
  await v1().getByLabel('频数每页条数',{exact:true}).selectOption('2');await v1().getByRole('button',{name:'读取精确频数',exact:true}).click();
  await v1().getByText('精确频数：共 5 种数值；当前 1—2 种',{exact:true}).waitFor();
  const observed=[];
  for(let index=0;index<3;index++){
   observed.push(...await v1().locator('.distribution tr').allTextContents().then(rows=>rows.slice(1)));
   await v1().getByText('平均值：37.71',{exact:true}).waitFor();
   if(index<2){await v1().getByRole('button',{name:'下一页频数',exact:true}).click();await v1().getByText('精确频数：共 5 种数值；当前 '+(index===0?'3—4':'5—5')+' 种',{exact:true}).waitFor();}
  }
  assert.deepEqual(observed,['01','183','301','601','1201']);report.checks.exact_frequency_pagination_preserves_population='passed';
  await v1().getByLabel('区间边界（逗号分隔，严格递增）',{exact:true}).fill('0,0');await v1().getByRole('button',{name:'计算区间分布',exact:true}).click();
  await a.getByRole('alert').filter({hasText:'严格递增'}).waitFor();assert.equal(await v1().locator('.distribution table').count(),0);
  await v1().getByLabel('区间边界（逗号分隔，严格递增）',{exact:true}).fill('0,18,30,60,121');await v1().getByRole('button',{name:'计算区间分布',exact:true}).click();
  await v1().getByRole('cell',{name:'大于等于 121',exact:true}).waitFor();
  assert.deepEqual(await v1().locator('.distribution tr').allTextContents().then(rows=>rows.slice(1)),['小于 00','[0, 18)1','[18, 30)3','[30, 60)1','[60, 121)2','大于等于 1210']);
  await shot(a,'cross-explicit-buckets-desktop.png');report.checks.strict_boundary_error_correction_and_all_buckets='passed';
  await a.setViewportSize({width:390,height:844});assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await shot(a,'cross-buckets-mobile.png');
  await c.goto(base+'/questions/'+f.question_id+'/statistics');await c.getByRole('alert').filter({hasText:'题目不存在'}).waitFor();assert.equal(await c.locator('[data-version-id]').count(),0);
  for(const suffix of ['', '/statistics','/questions/00000000-0000-4000-8000-000000000001/contents'])await api(a,'/surveys/'+f.surveys.b+suffix,404);
  report.checks.mobile_width_third_actor_and_old_owner_only='passed';
  const statsRoute='**/api/v1/questions/'+f.question_id+'/statistics*';let corrupted=false;
  await a.route(statsRoute,async route=>{const response=await route.fetch();if(!corrupted){corrupted=true;await route.fulfill({response,body:'{'});}else await route.fulfill({response});});
  await a.getByRole('button',{name:'刷新跨卷统计',exact:true}).click();await a.getByRole('alert').filter({hasText:'响应不完整'}).waitFor();assert.equal(await a.locator('[data-version-id]').count(),0);
  await a.getByRole('button',{name:'刷新跨卷统计',exact:true}).click();await v1().getByText('平均值：37.71',{exact:true}).waitFor();await a.unroute(statsRoute);report.checks.failed_refresh_clears_stale_results_and_recovers='passed';
  let signal,release,finished,held=false;const received=new Promise(r=>signal=r),resume=new Promise(r=>release=r),completed=new Promise(r=>finished=r);
  await a.route(statsRoute,async route=>{const response=await route.fetch();if(!held){held=true;signal();await resume;await route.fulfill({response});finished();}else await route.fulfill({response});});
  await a.getByRole('button',{name:'刷新跨卷统计',exact:true}).click();await received;
  await b.goto(base+'/surveys/'+f.surveys.b+'/manage');await b.getByRole('button',{name:'撤回本家族授权',exact:true}).click();await b.getByText('你的年龄：已关闭',{exact:true}).waitFor();
  await a.getByLabel('统计版本',{exact:true}).selectOption('all');await v1().getByText('平均值：25.20',{exact:true}).waitFor();release();await completed;await a.waitForLoadState('networkidle');await a.unroute(statsRoute);
  await v1().getByText('非空回答 5 次 · 含回答答卷 3 份 · 跨卷去重 2 人',{exact:true}).waitFor();
  await a.getByRole('button',{name:'刷新跨卷统计',exact:true}).click();await v1().getByText('平均值：25.20',{exact:true}).waitFor();
  const usage=await api(a,'/questions/'+f.question_id+'/usages?version_id='+f.versions[0]);assert.equal(usage.total,2);assert.deepEqual(usage.items.map(x=>x.survey_id).sort(),Object.values(f.surveys).sort());
  await a.getByLabel('统计版本',{exact:true}).selectOption(f.versions[0]);await v1().getByText('平均值：25.20',{exact:true}).waitFor();await shot(a,'cross-revoked-refresh-mobile.png');
  report.checks.revocation_refresh_and_delayed_old_response_cannot_replace_new='passed';
  await b.getByRole('link',{name:'跨问卷统计：你的年龄',exact:true}).click();await b.getByText('平均值：69.00',{exact:true}).waitFor();
  assert.ok((await b.locator('#cross-scope').innerText()).includes('仅本人引用'));
  for(const p of[a,b,c]){assert.equal(await p.title(),'MongoDB 问卷系统');if(p===c){assert.equal(await p.getByRole('heading',{name:'暂时无法打开页面',exact:true}).count(),1);assert.equal(await p.getByRole('alert').innerText(),'题目不存在');assert.equal(await p.locator('[data-version-id]').count(),0);}else assert.ok((await p.locator('main').innerText()).length>20);assert.equal(await p.locator('vite-error-overlay,nextjs-portal').count(),0);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
  assert.deepEqual(errors,[]);assert.ok(report.console.every(x=>/Failed to load resource: the server responded with a status of (404|422)/.test(x.text)),JSON.stringify(report.console));
  report.checks.identity_nonblank_overlay_console_and_interaction='passed';
 }catch(e){report.failure=e.stack;throw e;}
 finally{await browser.close();fs.writeFileSync(path.join(output,'browser-statistics.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
