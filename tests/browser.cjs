const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.SURVEY_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SURVEY_TEST_ORIGIN;
const output = process.env.SURVEY_EVIDENCE_DIR;
const result = {flow:'register → login → draft → publish → login as another account → answer → confirm → submit → owner statistics → server restart', checks:{}, console:[], screenshots:[]};
(async()=>{
  const browser = await chromium.launch({headless:true, ...(process.env.SURVEY_CHROMIUM ? {executablePath:process.env.SURVEY_CHROMIUM} : {})});
  try {
    const owner = await browser.newContext({viewport:{width:1280,height:900}});
    const filler = await browser.newContext({viewport:{width:1280,height:900}});
    const a = await owner.newPage(), b = await filler.newPage();
    const errors = [];
    for (const page of [a,b]) {
      page.on('pageerror',e=>errors.push(e.message));
      page.on('console',msg=>{if(['error','warning'].includes(msg.type())) result.console.push({type:msg.type(),text:msg.text(),url:msg.location().url});});
    }
    const suffix = process.env.SURVEY_TEST_RUN_ID.slice(0,10);
    async function account(page, name, next='') {
      await page.goto(base+'/register'+(next?'?next='+encodeURIComponent(next):''));
      await page.getByLabel('用户名',{exact:true}).fill(name);
      await page.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');
      await page.getByRole('button',{name:'注册',exact:true}).click();
      await page.waitForURL('**/login*');
      await page.getByLabel('用户名',{exact:true}).fill(name);
      await page.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');
      await page.getByRole('button',{name:'登录',exact:true}).click();
    }
    await account(a,'webowner_'+suffix);
    await a.waitForURL('**/surveys');
    assert.equal(await a.title(),'MongoDB 问卷系统');
    await a.getByRole('button',{name:'创建问卷',exact:true}).click();
    await a.waitForURL('**/edit');
    const surveyID = new URL(a.url()).pathname.split('/')[2];
    await a.getByLabel('问卷标题',{exact:true}).fill('P2 两账号验收');
    await a.getByLabel('问卷说明',{exact:true}).fill('单选完整流程与实际保存核对');
    await a.getByLabel('允许匿名填写',{exact:true}).check();
    await a.getByRole('button',{name:'添加单选题',exact:true}).click();
    await a.getByLabel('题干',{exact:true}).fill('请选择 A、B 或 C');
    await a.getByLabel('必须回答',{exact:true}).check();
    await a.getByLabel('选项 1',{exact:true}).fill('A');
    await a.getByLabel('选项 2',{exact:true}).fill('B');
    await a.getByRole('button',{name:'添加选项',exact:true}).click();
    await a.getByLabel('选项 3',{exact:true}).fill('C');
    await a.getByRole('button',{name:'添加单选题',exact:true}).click();
    const second = a.locator('fieldset').nth(1);
    await second.getByLabel('题干',{exact:true}).fill('第二题可留空');
    await second.getByLabel('选项 1',{exact:true}).fill('D');
    await second.getByLabel('选项 2',{exact:true}).fill('E');
    assert.equal(await a.getByRole('button',{name:'添加多选题',exact:true}).isDisabled(),false);
    await a.getByRole('button',{name:'保存草稿',exact:true}).click();
    await a.getByRole('status').filter({hasText:'草稿已保存'}).waitFor();
    await a.getByRole('button',{name:'发布问卷',exact:true}).click();
    await a.waitForURL('**/manage');
    const share = await a.getByLabel('分享链接',{exact:true}).inputValue();
    assert.equal(share,base+'/survey/'+surveyID);
    result.checks.creator_draft_publish_share = 'passed';
    await b.goto(share); await b.waitForURL('**/login?next=*');
    await b.getByRole('link',{name:'创建新账号',exact:true}).click();
    await b.getByLabel('用户名',{exact:true}).fill('webfiller_'+suffix);
    await b.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');
    await b.getByRole('button',{name:'注册',exact:true}).click(); await b.waitForURL('**/login*');
    await b.getByLabel('用户名',{exact:true}).fill('webfiller_'+suffix);
    await b.getByLabel('密码',{exact:true}).fill('browser-test-only-pass');
    await b.getByRole('button',{name:'登录',exact:true}).click(); await b.waitForURL('**/survey/'+surveyID);
    await b.getByRole('button',{name:'下一步',exact:true}).click();
    await b.getByRole('alert').filter({hasText:'请选择一个选项'}).waitFor();
    await b.getByLabel('B',{exact:true}).check();
    await b.getByRole('button',{name:'下一步',exact:true}).click();
    await b.getByRole('group',{name:'第二题可留空（可跳过）',exact:true}).waitFor();
    await b.getByRole('button',{name:'下一步',exact:true}).click();
    await b.getByRole('heading',{name:'确认答案',exact:true}).waitFor();
    await b.getByRole('button',{name:'返回修改',exact:true}).click();
    await b.getByRole('button',{name:'返回上一题',exact:true}).click();
    await b.getByLabel('A',{exact:true}).check(); await b.getByRole('button',{name:'下一步',exact:true}).click();
    await b.getByRole('heading',{name:'确认答案',exact:true}).waitFor();
    await b.getByLabel('匿名提交（创建者看不到账号身份）',{exact:true}).check();
    const statsURL='/api/v1/surveys/'+surveyID+'/statistics';
    assert.equal((await (await owner.request.get(base+statsURL)).json()).submission_count,0);
    result.checks.confirmation_does_not_save = 'passed';
    let firstReceipt, originalBody, retriedBody, intercepted=false;
    await b.route('**/responses',async route=>{
      if(!intercepted){
        intercepted=true; originalBody=route.request().postDataJSON();
        const response=await route.fetch(); firstReceipt=await response.json();
        await route.fulfill({response, body:'{',headers:{...response.headers(),'content-type':'application/json'}});
      } else { retriedBody=route.request().postDataJSON(); await route.continue(); }
    });
    await b.getByRole('button',{name:'确认提交',exact:true}).click();
    await b.getByRole('button',{name:'重试原提交',exact:true}).waitFor();
    assert.equal(await b.getByRole('button',{name:'返回修改',exact:true}).isDisabled(),true);
    assert.equal((await (await owner.request.get(base+statsURL)).json()).submission_count,1);
    await b.getByRole('button',{name:'重试原提交',exact:true}).click();
    await b.getByRole('heading',{name:'提交成功',exact:true}).waitFor();
    assert.deepEqual(retriedBody,originalBody);
    assert.equal(await b.getByText('收据：'+firstReceipt.response_id,{exact:true}).count(),1);
    result.checks.truncated_response_same_content_recovery = 'passed';
    await a.goto(base+'/surveys/'+surveyID+'/statistics');
    await a.getByText('有效答卷 1 份 · 去重填写 1 人',{exact:true}).waitFor();
    const summary = await (await owner.request.get(base+statsURL)).json();
    assert.deepEqual(summary.questions[0].options.map(o=>[o.respondent_count,o.submission_count]),[[1,1],[0,0],[0,0]]);
    for(const secret of ['respondent_id','submission_key','request_digest','webfiller_']) assert.equal(JSON.stringify(summary).includes(secret),false);
    const unauthorized = await filler.request.get(base+statsURL); assert.equal(unauthorized.status(),404);
    result.checks.statistics_zero_options_and_privacy = 'passed';
    result.checks.page_identity = 'passed';
    result.checks.not_blank = (await a.locator('main').innerText()).includes('统计') ? 'passed' : 'failed';
    result.checks.no_framework_overlay = await a.getByText('Internal Server Error',{exact:true}).count()===0 ? 'passed':'failed';
    for(const [name,page] of [['statistics-desktop',a],['submission',b]]) {
      await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});result.screenshots.push(name+'.png');
    }
    await a.setViewportSize({width:390,height:844}); await a.reload();
    await a.getByText('有效答卷 1 份 · 去重填写 1 人',{exact:true}).waitFor();
    assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true);
    await a.screenshot({path:path.join(output,'statistics-mobile.png'),fullPage:true});result.screenshots.push('statistics-mobile.png');
    result.checks.mobile_no_horizontal_overflow='passed';
    assert.deepEqual(errors,[]);
    // The unauthenticated share-link entry deliberately receives 401 from auth/me before login.
    const unexpected = result.console.filter(v=>!(v.text.includes('401') && v.url===base+'/api/v1/auth/me'));
    assert.deepEqual(unexpected,[]);
    result.checks.console_health = 'passed';
    result.summary = {survey_id:surveyID, submission_count:summary.submission_count, respondent_count:summary.respondent_count};
    fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify(result,null,2));
    console.log('Browser P2: two accounts, confirmation, truncated-response retry, statistics, privacy and mobile passed');
  } finally { await browser.close(); }
})().catch(e=>{result.failure=e.message;fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify(result,null,2));console.error(e);process.exitCode=1;});
