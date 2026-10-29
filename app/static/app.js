'use strict';
const root = document.querySelector('#app');
const sid = document.body.dataset.survey;
const page = document.body.dataset.page;
const LIMITS=JSON.parse(document.querySelector('#limits').textContent);
let requestSequence = 0;
function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function clear() { root.replaceChildren(); document.querySelector('#error').hidden = true; }
function fail(error) {
  const box = document.querySelector('#error');
  box.textContent = error.message;
  box.hidden = false; box.focus();
}
async function api(path, method = 'GET', body) {
  const options = {method, headers: {}};
  if (method !== 'GET') {
    options.headers = {'Content-Type': 'application/json', 'X-Survey-Request': '1'};
    options.body = JSON.stringify(body ?? {});
  }
  let response;
  try { response = await fetch('/api/v1' + path, options); }
  catch { throw Object.assign(new Error('网络结果未知，请使用原内容重试。'), {unknown: true}); }
  let data;
  try { data = response.status === 204 ? null : await response.json(); }
  catch { throw Object.assign(new Error('响应不完整，结果未知，请使用原内容重试。'), {unknown: true}); }
  if (!response.ok) {
    if (response.status === 401 && !['login', 'register'].includes(page)) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
    }
    const message = data.error.message + data.error.fields.map(f => '\n' + f.message).join('');
    throw Object.assign(new Error(message), {status: response.status, unknown: response.status >= 500});
  }
  return data;
}
function button(text, action, parent = root, primary = false) {
  const node = el('button', text, {type: 'button'});
  if (primary) node.className = 'primary';
  node.addEventListener('click', async () => {
    node.disabled = true;
    document.querySelector('#error').hidden = true;
    try { await action(); } catch (e) { fail(e); } finally { node.disabled = false; }
  });
  parent.append(node); return node;
}
function link(text, href, parent = root) { parent.append(el('a', text, {href})); }
let fieldNumber = 0;
function field(label, value, parent, type = 'text') {
  const id = 'field-' + (++fieldNumber);
  const input = el(type === 'textarea' ? 'textarea' : 'input', undefined, {id});
  if (type !== 'textarea') input.type = type;
  if (type === 'checkbox') input.checked = Boolean(value); else input.value = value ?? '';
  const lab = el('label', label, {for: id});
  if (type === 'checkbox') { lab.prepend(input); parent.append(lab); }
  else parent.append(lab, input);
  return input;
}
function move(array, index, amount) {
  const destination = index + amount;
  if (destination >= 0 && destination < array.length) [array[index], array[destination]] = [array[destination], array[index]];
}
function safeNext(value) {
  return value && /^\/(?!\/)/.test(value) && !/[\\\u0000-\u001f]/.test(value) ? value : '/surveys';
}
async function auth() {
  clear(); const registering = page === 'register';
  root.append(el('h1', registering ? '注册账号' : '登录'));
  root.append(el('p', '用户名3—32个中文、英文字母、数字或下划线，英文字母统一小写。密码8—128字符，保留首尾空格。'));
  const form = el('form'); root.append(form);
  const name = field('用户名', '', form); name.autocomplete = 'username'; name.required = true;
  const password = field('密码', '', form, 'password'); password.autocomplete = registering ? 'new-password' : 'current-password'; password.required = true;
  const submit = el('button', registering ? '注册' : '登录', {type: 'submit', class: 'primary'}); form.append(submit);
  form.addEventListener('submit', async event => {
    event.preventDefault(); submit.disabled = true;
    try {
      const length = Array.from(password.value).length;
      if (length > 128 || (registering && length < 8)) throw new Error('密码需8—128个字符，首尾空格也计入。');
      await api('/auth/' + (registering ? 'register' : 'login'), 'POST', {username: name.value, password: password.value});
      const next = safeNext(new URLSearchParams(location.search).get('next'));
      location.href = registering ? '/login?next=' + encodeURIComponent(next) : next;
    } catch (e) { fail(e); } finally { submit.disabled = false; }
  });
  const target = registering ? 'login' : 'register';
  link(registering ? '已有账号，去登录' : '创建新账号', '/' + target + location.search);
}
let listOffset = 0;
async function list() {
  const data = await api('/surveys?offset=' + listOffset); clear(); root.append(el('h1', '我的问卷'));
  button('创建问卷', async () => { const survey = await api('/surveys', 'POST', {}); location.href = '/surveys/' + survey.id + '/edit'; }, root, true);
  root.append(el('p', '共 ' + data.total + ' 份问卷。'));
  for (const s of data.items) {
    const card = el('section', undefined, {class: 'card'});
    const status=surveyState(s);
    card.append(el('h2', s.title || '未命名问卷'), el('p', status));
    const links = el('div', undefined, {class: 'links'}); card.append(links);
    link(s.state === 'draft' ? '编辑草稿' : '管理与分享', '/surveys/' + s.id + (s.state === 'draft' ? '/edit' : '/manage'), links);
    link('查看统计', '/surveys/' + s.id + '/statistics', links); root.append(card);
  }
  if (listOffset > 0) button('上一页', async () => { listOffset -= 20; await list(); });
  if (listOffset + 20 < data.total) button('下一页', async () => { listOffset += 20; await list(); });
}
function validationDefaults(kind) {
  return {single:{},multiple:{min_choices:null,max_choices:null},text:{min_length:null,max_length:null},number:{min_value:null,max_value:null,integer_only:false}}[kind];
}
function newQuestion(kind) {
  return {id:crypto.randomUUID(),kind,title:'',required:false,validation:validationDefaults(kind),rules:[],
    options:['single','multiple'].includes(kind)?[{id:crypto.randomUUID(),text:''},{id:crypto.randomUUID(),text:''}]:[]};
}
function selectField(label, choices, value, parent) {
  const id='field-'+(++fieldNumber), input=el('select',undefined,{id});
  if(!choices.some(([key])=>key===value)) choices=[...choices,[value,'失效或不适用：'+value]];
  for(const [key,text] of choices) input.append(el('option',text,{value:key}));
  input.value=value; parent.append(el('label',label,{for:id}),input); return input;
}
async function edit() {
  let survey=await api('/surveys/'+sid);
  let pendingImport=null,pendingImportAction=null,importInFlight=false;
  const available=[];
  for(const scope of ['owned','shared'])for(const family of await allItems('/questions?scope='+scope)){
    for(const version of await allItems('/questions/'+family.id+'/versions'))if(version.state==='sealed')available.push({family,version});
  }
  const personalBanks=await allItems('/question-banks');
  const bankChoices=personalBanks.flatMap(bank=>bank.entries.filter(x=>x.available).map(x=>({bank,entry:x})));
  let deadlineRaw=shanghaiInput(survey.deadline_at),deadlineInput;
  function editorState(){return JSON.stringify([survey.title,survey.description,survey.allow_anonymous,survey.allow_repeat,deadlineRaw,survey.questions]);}
  let persisted=editorState();
  function requireSaved(){if(editorState()!==persisted)throw new Error('请先保存草稿，再选入题目、更换来源或设置授权。');return survey;}
  const kinds=[['single','单选'],['multiple','多选'],['text','文本'],['number','数字']];
  function render() {
    clear();root.append(el('h1','编辑问卷'));
    if(survey.state!=='draft'){root.append(el('p','已发布内容被冻结。'));link('管理与分享','/surveys/'+sid+'/manage');return;}
    const config=el('section',undefined,{class:'card'});root.append(config);
    for(const [key,label,type,max] of [['title','问卷标题','text',LIMITS.survey_title],['description','问卷说明','textarea',LIMITS.description]]){
      const input=field(label,survey[key],config,type);input.oninput=()=>survey[key]=input.value;
    }
    for(const [key,label] of [['allow_anonymous','允许匿名填写'],['allow_repeat','允许每个账号多次填写']]){
      const input=field(label,survey[key],config,'checkbox');input.onchange=()=>survey[key]=input.checked;
    }
    deadlineInput=field('截止时间（北京时间，可留空）',deadlineRaw,config,'datetime-local');deadlineInput.step='0.001';deadlineInput.oninput=()=>deadlineRaw=deadlineInput.value;
    if(available.length){
      const sourceBox=el('section',undefined,{class:'card'});root.append(sourceBox);
      sourceBox.append(el('h2','选用常用题'),el('p','选入会保存确切版本的副本。题目所有者可查看问卷ID、标题、状态、版本和实例数。'));
      const sourceSelect=selectField('已有题目版本',available.map((x,i)=>[String(i),x.family.label+' · v'+x.version.version_no+' · '+x.version.payload.title]),'0',sourceBox);
      button('选入题目',async()=>{
        if(importInFlight)return;
        if(!pendingImport){const chosen=available[Number(sourceSelect.value)];requireSaved();pendingImport={revision:survey.revision,instance_id:crypto.randomUUID(),question_id:chosen.family.id,question_version_id:chosen.version.id};pendingImportAction='选入题目';}
        importInFlight=true;render();
        try{survey=await api('/surveys/'+sid+'/question-imports','POST',pendingImport);pendingImport=null;pendingImportAction=null;importInFlight=false;persisted=editorState();render();}
        catch(e){importInFlight=false;if(!e.unknown){pendingImport=null;pendingImportAction=null;persisted=editorState();}render();throw e;}
      },sourceBox);
    }
    if(bankChoices.length){
      const box=el('section',undefined,{class:'card'});root.append(box);
      const choice=selectField('从题库选用默认版',bankChoices.map((x,i)=>[String(i),x.bank.title+' · '+x.entry.label+' · v'+x.entry.version_no]),'0',box);
      button('从题库选入',async()=>{
        if(importInFlight)return;
        if(!pendingImport){const x=bankChoices[Number(choice.value)].entry;requireSaved();pendingImport={revision:survey.revision,instance_id:crypto.randomUUID(),question_id:x.question_id,question_version_id:x.default_version_id};pendingImportAction='从题库选入';}
        importInFlight=true;render();
        try{survey=await api('/surveys/'+sid+'/question-imports','POST',pendingImport);pendingImport=null;pendingImportAction=null;importInFlight=false;persisted=editorState();render();}
        catch(e){importInFlight=false;if(!e.unknown){pendingImport=null;pendingImportAction=null;persisted=editorState();}render();throw e;}
      },box);
    }
    grantControls(survey,root,async()=>requireSaved(),async updated=>{survey=updated;persisted=editorState();render();});
    survey.questions.forEach((q,index)=>{
      const card=el('fieldset',undefined,{'data-question-id':q.id,'data-kind':q.kind});card.append(el('legend','第 '+(index+1)+' 题'));root.append(card);
      if(q.source){card.append(el('p','来源版本：'+q.source.question_version_id+'。内容已定稿；修改题意请到我的题目派生新版。'));}
      const kind=selectField('题型',kinds,q.kind,card);
      kind.onchange=()=>{const fresh=newQuestion(kind.value);Object.assign(q,{kind:fresh.kind,validation:fresh.validation,options:fresh.options,rules:[]});render();};
      const title=field('题干',q.title,card);title.oninput=()=>q.title=title.value;
      const required=field('必须回答',q.required,card,'checkbox');required.onchange=()=>q.required=required.checked;
      q.options.forEach((option,oi)=>{
        const row=el('div',undefined,{class:'row'});card.append(row);
        const text=field('选项 '+(oi+1),option.text,row);text.oninput=()=>option.text=text.value;
        button('上移选项',()=>{move(q.options,oi,-1);render();},row).disabled=oi===0;
        button('下移选项',()=>{move(q.options,oi,1);render();},row).disabled=oi===q.options.length-1;
        button('删除选项',()=>{q.options.splice(oi,1);render();},row);
      });
      if(['single','multiple'].includes(q.kind))button('添加选项',()=>{q.options.push({id:crypto.randomUUID(),text:''});render();},card).disabled=q.options.length>=LIMITS.options;
      const limits={multiple:[['min_choices','最少选择数'],['max_choices','最多选择数']],text:[['min_length','最少字数'],['max_length','最多字数']],number:[['min_value','最小值'],['max_value','最大值']]}[q.kind]??[];
      for(const [key,label] of limits){
        const input=field(label,q.validation[key],card);input.inputMode=q.kind==='number'?'decimal':'numeric';
        input.oninput=()=>{q.validation[key]=input.value===''?null:q.kind==='number'?input.value:/^\d{1,5}$/.test(input.value)?Number(input.value):input.value;};
      }
      if(q.kind==='multiple')card.append(el('p','恰好选择 k 项：最少和最多都填 k。非必答可留空，一旦作答须满足数量限制。',{class:'muted'}));
      if(q.kind==='text')card.append(el('p','字数不计首尾空白，部分表情会计作多个字符。',{class:'muted'}));
      if(q.kind==='number'){
        const integer=field('仅允许整数',q.validation.integer_only,card,'checkbox');integer.onchange=()=>q.validation.integer_only=integer.checked;
      }
      if(q.source){
        for(const control of card.querySelectorAll('input,select,button'))control.disabled=true;
      }
      if(q.kind==='text'&&q.rules.length)button('清除不适用规则',()=>{q.rules=[];render();},card);
      if(q.kind!=='text'){
        const rules=el('section',undefined,{class:'rules'});card.append(rules,el('p','规则按顺序取首条命中；未命中或留空走下一题。只能跳到后续题或结束。',{class:'muted'}));
        q.rules.forEach((rule,ri)=>{
          const box=el('div',undefined,{class:'card','data-rule-index':ri});box.append(el('h3','规则 '+(ri+1)));rules.append(box);
          const ops={single:[['eq','等于']],multiple:[['any','包含任一'],['all','包含全部'],['exact','完全匹配']],number:[['eq','等于'],['ne','不等于'],['gt','大于'],['gte','大于等于'],['lt','小于'],['lte','小于等于']]}[q.kind];
          const op=selectField('匹配方式',ops,rule.op,box);op.onchange=()=>rule.op=op.value;
          if(q.kind==='single'){
            const operand=selectField('匹配选项',q.options.map(o=>[o.id,o.text||'未命名选项']),rule.operand,box);operand.onchange=()=>rule.operand=operand.value;
          }else if(q.kind==='multiple'){
            for(const o of q.options){const input=field('条件选项：'+(o.text||'未命名'),rule.operand.includes(o.id),box,'checkbox');input.onchange=()=>{rule.operand=input.checked?[...rule.operand,o.id]:rule.operand.filter(id=>id!==o.id);};}
          }else{const input=field('条件数值',rule.operand,box);input.inputMode='decimal';input.oninput=()=>rule.operand=input.value;}
          const targets=[['END','结束（进入确认页）'],...survey.questions.slice(index+1).map((target,offset)=>[target.id,'第 '+(index+offset+2)+' 题：'+(target.title||'未命名')])];
          const target=selectField('跳转目标',targets,rule.target,box);target.onchange=()=>rule.target=target.value;
          button('上移规则',()=>{move(q.rules,ri,-1);render();},box).disabled=ri===0;
          button('下移规则',()=>{move(q.rules,ri,1);render();},box).disabled=ri===q.rules.length-1;
          button('删除规则',()=>{q.rules.splice(ri,1);render();},box);
        });
        button('添加规则',()=>{q.rules.push({op:q.kind==='multiple'?'any':q.kind==='number'?'gte':'eq',operand:q.kind==='multiple'?(q.options[0]?[q.options[0].id]:[]):q.kind==='number'?'0':q.options[0]?.id??crypto.randomUUID(),target:'END'});render();},card).disabled=q.rules.length>=LIMITS.rules;
      }
      if(q.source){
        const box=el('section');card.append(box);
        button('查看确切来源',async()=>{const source=await api('/surveys/'+sid+'/questions/'+q.id+'/source');box.append(el('p','v'+source.version_no+' · '+source.operation+' · 父版本：'+(source.parent_version_id??'无')+' · 恢复来源：'+(source.restored_from_version_id??'无')));},box);
        const choices=available.filter(x=>x.family.id===q.source.question_id);
        if(choices.length){
          const chosen=selectField('换用同家族版本',choices.map(x=>[x.version.id,'v'+x.version.version_no+' · '+x.version.payload.title]),q.source.question_version_id,box);
          frozenButton('换用版本并清空本题规则',box,async()=>{const target=chosen.value;requireSaved();return {revision:survey.revision,question_id:q.source.question_id,question_version_id:target};},
            body=>api('/surveys/'+sid+'/questions/'+q.id+'/source','PUT',body),async updated=>{survey=updated;persisted=editorState();render();});
        }
      }
      button('上移题目',()=>{move(survey.questions,index,-1);render();},card).disabled=index===0;
      button('下移题目',()=>{move(survey.questions,index,1);render();},card).disabled=index===survey.questions.length-1;
      button('删除题目',()=>{survey.questions.splice(index,1);render();},card);
    });
    for(const [kind,label] of kinds)button('添加'+label+'题',()=>{survey.questions.push(newQuestion(kind));render();}).disabled=survey.questions.length>=LIMITS.questions;
    button('保存草稿',async()=>{await save();document.querySelector('#notice').textContent='草稿已保存；待修复项：'+survey.publish_issues.map(v=>{const index=survey.questions.findIndex(q=>q.id===v.question_id);return(index>=0?'第'+(index+1)+'题：':'')+v.message;}).join('；');},root,true);
    button('发布问卷',async()=>{await save();await api('/surveys/'+sid+'/publish','POST',{revision:survey.revision});location.href='/surveys/'+sid+'/manage';},root,true);
    if(pendingImport)for(const control of root.querySelectorAll('input,textarea,select,button'))if(importInFlight||control.textContent!==pendingImportAction)control.disabled=true;
  }
  async function save(renderAfter=true){
    survey.deadline_at=deadlineUTC(deadlineInput);
    const body=Object.fromEntries(['revision','title','description','allow_anonymous','allow_repeat','deadline_at','questions'].map(k=>[k,survey[k]]));
    survey=await api('/surveys/'+sid+'/draft','PUT',body);deadlineRaw=shanghaiInput(survey.deadline_at);persisted=editorState();if(renderAfter)render();
  }
  render();
}
function shanghaiInput(value){return value?new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,23):'';}
function deadlineUTC(input){
  if(!input.checkValidity())throw new Error('截止时间格式不正确');
  if(!input.value)return null;
  const date=new Date(input.value+'+08:00');
  if(!Number.isFinite(date.getTime()))throw new Error('截止时间格式不正确');
  return date.toISOString();
}
function deadlineText(value){return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(value))+'（北京时间）':'未设置';}
function surveyState(survey){return survey.state==='draft'?'草稿':survey.state==='closed'?'已关闭':survey.deadline_at&&new Date(survey.deadline_at)<=new Date()?'已截止':'接收中';}
async function manage(){
  const survey=await api('/surveys/'+sid);clear();root.append(el('h1',survey.title),el('p',survey.description),el('p','状态：'+surveyState(survey)+'。发布后内容冻结。'),el('p','当前截止：'+deadlineText(survey.deadline_at),{id:'current-deadline'}));
  const input=field('分享链接',location.origin+'/survey/'+sid,root);input.readOnly=true;
  const links=el('div',undefined,{class:'links'});root.append(links);link('打开填写页','/survey/'+sid,links);link('查看统计','/surveys/'+sid+'/statistics',links);
  grantControls(survey,root,async()=>survey,async()=>manage());
  for(const q of survey.questions.filter(q=>q.source)){button('查看来源：'+q.title,async()=>{const v=await api('/surveys/'+sid+'/questions/'+q.id+'/source');root.append(el('p','来源 v'+v.version_no+' · '+v.operation+' · 父版本：'+(v.parent_version_id??'无')+' · 恢复来源：'+(v.restored_from_version_id??'无')));});link('跨问卷统计：'+q.title,'/questions/'+q.source.question_id+'/statistics?version='+q.source.question_version_id);}
  if(survey.state==='published'){
    const deadline=field('截止时间（北京时间，可留空）',shanghaiInput(survey.deadline_at),root,'datetime-local');deadline.step='0.001';
    root.append(el('p','截止时间可留空或设为未来时间。到期后可延长截止，关闭后不能重新开放。',{class:'muted'}));
    button('保存截止时间',async()=>{await api('/surveys/'+sid+'/deadline','PATCH',{revision:survey.revision,deadline_at:deadlineUTC(deadline)});await manage();},root,true);
    button('清除截止时间',async()=>{await api('/surveys/'+sid+'/deadline','PATCH',{revision:survey.revision,deadline_at:null});await manage();});
    button('关闭问卷',async()=>{await api('/surveys/'+sid+'/close','POST',{revision:survey.revision});await manage();});
  }else if(survey.state==='draft')link('编辑草稿','/surveys/'+sid+'/edit');
}
async function fill() {
  const survey=await api('/fill/'+sid);
  let answers={},key=crypto.randomUUID(),path=[survey.questions[0].id],shown=survey.questions[0].id,frozenBody=null,anonymousChoice=false;
  const questions=Object.fromEntries(survey.questions.map(q=>[q.id,q]));
  function heading(){clear();root.append(el('h1',survey.title),el('p',survey.description));
    if(survey.result_sharing_notice?.enabled){const ids=new Set(survey.result_sharing_notice.question_instance_ids);root.append(el('p','以下题目的汇总结果将提供给题目所有者，不包含账号身份或逐条内容：'+survey.questions.filter(q=>ids.has(q.id)).map(q=>q.title).join('、')));}
  }
  if(!survey.accepting||(!survey.allow_repeat&&survey.already_submitted)){
    heading();root.append(el('p',!survey.accepting?'问卷已关闭或截止。':'此账号已完成填写。'));return;
  }
  async function evaluate(){
    const seq=++requestSequence,snapshot=structuredClone(answers);
    const inputs=Array.from(root.querySelectorAll('fieldset input,fieldset textarea,fieldset select'));
    inputs.forEach(n=>n.disabled=true);
    let result;
    try{result=await api('/fill/'+sid+'/evaluate','POST',{content_version:survey.content_version,answers:snapshot});}
    catch(e){if(seq!==requestSequence)return;inputs.forEach(n=>n.disabled=false);throw e;}
    if(seq!==requestSequence)return;
    answers={...result.pending_answers,...result.active_answers};path=result.path;shown=result.current_question_id;
    if(result.errors.length&&shown in snapshot)answers[shown]=snapshot[shown];
    render();
    if(result.errors.length)fail(new Error(result.errors.map(e=>e.message).join('\n')));
  }
  function goBack(qid){++requestSequence;if(path.includes(qid)){shown=qid;render();}}
  function displayAnswer(q,value){
    if(value==null)return '未回答';
    if(q.kind==='single')return q.options.find(o=>o.id===value)?.text??'';
    if(q.kind==='multiple')return value.map(id=>q.options.find(o=>o.id===id)?.text??'').join('、');
    return value;
  }
  function render(){
    heading();
    if(shown){
      const q=questions[shown],card=el('fieldset',undefined,{'data-question-id':q.id,'data-kind':q.kind});card.append(el('legend',q.title+(q.required?'（必答）':'（可跳过）')));root.append(card);
      let selected=answers[q.id]??(q.kind==='multiple'?[]:q.kind==='single'?null:'');
      if(q.kind==='single'||q.kind==='multiple'){
        for(const option of q.options){
          const label=el('label'),input=el('input',undefined,{type:q.kind==='single'?'radio':'checkbox',name:q.id,value:option.id});
          input.checked=q.kind==='single'?selected===option.id:selected.includes(option.id);
          input.onchange=()=>{selected=q.kind==='single'?option.id:Array.from(card.querySelectorAll('input:checked')).map(n=>n.value);};
          label.append(input,document.createTextNode(option.text));card.append(label);
        }
        if(q.kind==='multiple')card.append(el('p','选择数量：最少 '+(q.validation.min_choices??'不限')+'，最多 '+(q.validation.max_choices??'不限')+'；非必答可留空。',{class:'muted'}));
        if(!q.required)button('清除选择 / 留空',()=>{selected=q.kind==='single'?null:[];card.querySelectorAll('input').forEach(n=>n.checked=false);},card);
      }else{
        const input=field(q.kind==='text'?'回答内容':'数字答案',selected,card,q.kind==='text'?'textarea':'text');
        const hint=el('p',undefined,{class:'muted'});card.append(hint);
        if(q.kind==='number'){
          input.inputMode='decimal';hint.textContent='最小值 '+(q.validation.min_value??'不限')+'，最大值 '+(q.validation.max_value??'不限')+(q.validation.integer_only?'，仅整数':'，可小数')+'。';
          input.oninput=()=>selected=input.value;
        }else{
          function count(){selected=input.value;hint.textContent='字数（不计首尾空白）：'+Array.from(input.value.trim()).length+'；最少 '+(q.validation.min_length??'不限')+'，最多 '+(q.validation.max_length??'不限')+'。';}
          input.oninput=count;count();
        }
      }
      button('下一步',async()=>{answers[q.id]=selected;await evaluate();},root,true);
      const index=path.indexOf(q.id);if(index>0)button('返回上一题',()=>goBack(path[index-1]));
    }else{
      root.append(el('h2','确认答案'));
      const list=el('ul');for(const qid of path)list.append(el('li',questions[qid].title+'：'+displayAnswer(questions[qid],answers[qid])));root.append(list);
      const anonymous=survey.allow_anonymous?field('匿名提交（创建者看不到账号身份）',anonymousChoice,root,'checkbox'):null;
      if(anonymous)anonymous.onchange=()=>anonymousChoice=anonymous.checked;
      root.append(el('p',survey.allow_repeat?'允许多次填写。提交结果未知时，请先重试原提交。':'每个账号只能有效提交一份。'));
      button('返回修改',()=>goBack(path[path.length-1]));
      for(const qid of path)button('修改：'+questions[qid].title,()=>goBack(qid));
      const submit=button('确认提交',async()=>{
        frozenBody??={content_version:survey.content_version,submission_key:key,anonymous:anonymous?.checked??false,answers:Object.fromEntries(path.map(qid=>[qid,answers[qid]]))};
        root.querySelectorAll('button,input,textarea,select').forEach(n=>n.disabled=true);
        try{
          const receipt=await api('/fill/'+sid+'/responses','POST',frozenBody);
          heading();root.append(el('h2','提交成功'),el('p','收据：'+receipt.response_id));
          if(survey.allow_repeat)button('再填一份',async()=>fill());link('我的问卷','/surveys');
        }catch(e){
          if(e.unknown){submit.textContent='重试原提交';submit.disabled=false;}
          else{frozenBody=null;root.querySelectorAll('button,input,textarea,select').forEach(n=>n.disabled=false);}
          throw e;
        }
      },root,true);
    }
  }
  render();
}
async function stats(){
  const data=await api('/surveys/'+sid+'/statistics');clear();root.append(el('h1',data.title+' · 统计'));
  root.append(el('p','有效答卷 '+data.submission_count+' 份 · 去重填写 '+data.respondent_count+' 人',{id:'totals'}));
  root.append(el('p','单选人数按账号去重，次数按有效答卷计。重复填写选择不同选项时，各选项人数合计可能超过该题回答人数。多选按选项出现次数统计，数字均值包含0、排除空答。'));
  for(const q of data.questions){
    const card=el('section',undefined,{class:'card','data-stat-question-id':q.id,'data-kind':q.kind});root.append(card);card.append(el('h2',q.title));
    if(q.kind==='single'||q.kind==='multiple'){
      card.append(el('p',q.kind==='single'?'回答 '+q.respondent_count+' 人 / '+q.submission_count+' 次':'有效回答 '+q.answered_submission_count+' 次'));
      const table=el('table'),head=el('tr');for(const title of(q.kind==='single'?['选项','选择人数','答卷次数']:['选项','被选择次数']))head.append(el('th',title,{scope:'col'}));table.append(head);
      for(const option of q.options){const row=el('tr');for(const value of(q.kind==='single'?[option.text,option.respondent_count,option.submission_count]:[option.text,option.selection_count]))row.append(el('td',String(value)));table.append(row);}card.append(table);
    }else{
      card.append(el('p','有效回答 '+q.answered_submission_count+' 次'));
      if(q.kind==='number')card.append(el('p','平均值：'+q.average_display,{class:'average'}));
      const box=el('div',undefined,{class:'contents'});card.append(box);await loadContents(q,box,0,20);
    }
  }
  button('刷新统计',stats,root,true);link('管理与分享','/surveys/'+sid+'/manage');
}
async function loadContents(q,box,offset,limit){
  const data=await api('/surveys/'+sid+'/questions/'+q.id+'/contents?offset='+offset+'&limit='+limit);
  box.replaceChildren(el('p','全部内容 '+data.total+' 条；当前 '+(data.total?offset+1:0)+'—'+Math.min(offset+limit,data.total)+' 条'));
  const table=el('table'),head=el('tr');for(const title of['内容','填写者','提交时间（北京时间）'])head.append(el('th',title,{scope:'col'}));table.append(head);
  for(const item of data.items){const row=el('tr');row.append(el('td',item.value,{class:'content-value'}),el('td',item.anonymous?'匿名':item.username),el('td',deadlineText(item.created_at)));table.append(row);}box.append(table);
  const controls=el('div');box.append(controls);
  const size=selectField('每页条数',[['2','2'],['20','20'],['50','50'],['100','100']],String(limit),controls);
  size.onchange=async()=>{try{await loadContents(q,box,0,Number(size.value));}catch(e){fail(e);}};
  button('上一页',()=>loadContents(q,box,Math.max(0,offset-limit),limit),controls).disabled=offset===0;
  button('下一页',()=>loadContents(q,box,offset+limit,limit),controls).disabled=offset+limit>=data.total;
}

async function questionStats(){
  clear();root.append(el('h1','跨问卷题目统计'));
  const qid=sid,query=new URLSearchParams(location.search),initial=query.get('version')??'all';
  const controls=el('section'),results=el('section',undefined,{id:'cross-results'});root.append(controls,results);
  root.append(el('p','仅纳入已发布、已关闭及到期问卷；草稿只出现在使用清单。空答和未访问题不计，0有效。同一答卷的不同实例分别计回答次数，答卷按份去重，人数跨卷按账号去重（含匿名）。数字为回答次数分布；不同版本仅并列，不加总人数。'));
  root.append(el('p','每次刷新重新核对授权。授权核对后撤回的在途响应可能完成，后续请求排除；各次读取不构成同一数据快照。'));
  const available=await api('/questions/'+qid+'/statistics');
  const selector=selectField('统计版本',[['all','全部版本（并列分组）'],...available.groups.map(g=>[g.version_id,'v'+g.version_no+' · '+g.title])],initial,controls);
  if(initial!=='all'&&!available.groups.some(g=>g.version_id===initial)){throw new Error('该版本不可用于统计。');}
  let sequence=0;
  async function refresh(){
    const ticket=++sequence;results.replaceChildren(el('p','正在读取并核对统计范围…'));
    document.querySelector('#error').hidden=true;
    try{
      const selected=selector.value;
      const data=await api('/questions/'+qid+'/statistics?'+(selected==='all'?'versions=all':'version_id='+selected));
      if(ticket!==sequence)return;
      results.replaceChildren(el('p',data.scope==='owned_and_granted'?'统计范围：所有本人及有效授权问卷。其他卷仅按发布前授权向题目所有者提供本题汇总。':'统计范围：仅本人引用该版本的问卷。',{id:'cross-scope'}));
      if(!data.groups.length)results.append(el('p','暂无可统计的已定稿版本。'));
      for(const group of data.groups){
        if(ticket!==sequence)return;
        const card=el('section',undefined,{class:'card','data-version-id':group.version_id});results.append(card);
        card.append(el('h2','v'+group.version_no+' · '+group.title),el('p','确切版本：'+group.version_id));
        const summary=el('div',undefined,{class:'cross-summary'});card.append(summary);
        function counts(g){summary.replaceChildren(el('p','纳入 '+g.included_survey_count+' 份问卷 / '+g.included_instance_count+' 个实例'),el('p','非空回答 '+g.answer_count+' 次 · 含回答答卷 '+g.answered_submission_count+' 份 · 跨卷去重 '+g.respondent_count+' 人'));
          if(g.kind==='number')summary.append(el('p','平均值：'+g.average_display,{class:'average'}));}
        counts(group);
        if(group.options){
          const table=el('table'),head=el('tr');for(const name of ['选项','选择次数',...(group.kind==='single'?['选择人数']:[])])head.append(el('th',name,{scope:'col'}));table.append(head);
          for(const option of group.options){const row=el('tr');for(const value of [option.text,option.selection_count,...(group.kind==='single'?[option.respondent_count]:[])])row.append(el('td',String(value)));table.append(row);}card.append(table);
        }
        if(group.kind==='text')card.append(el('p','文本仅提供计数。本人问卷原文可在原问卷统计页查看。'));
        if(group.kind==='number'){
          const distributionControls=el('div'),values=el('div',undefined,{class:'distribution'});card.append(distributionControls,values);
          const size=selectField('频数每页条数',[['2','2'],['20','20'],['100','100']],'20',distributionControls);
          const edges=field('区间边界（逗号分隔，严格递增）','',distributionControls);
          edges.placeholder='例如：0,18,30,60,121';
          distributionControls.append(el('p','区间左闭右开，范围外数值单列。边界由你指定，所有区间均按回答次数计。'));
          let read=0;
          async function distribution(mode,offset=0){
            const turn=++read;values.replaceChildren(el('p','正在读取分布…'));summary.replaceChildren();
            const params=new URLSearchParams({mode});
            if(mode==='exact'){params.set('offset',String(offset));params.set('limit',size.value);}
            else params.set('boundaries',JSON.stringify(edges.value.split(/[,，]/).map(x=>x.trim())));
            try{
              const d=await api('/questions/'+qid+'/versions/'+group.version_id+'/distribution?'+params);
              if(turn!==read||ticket!==sequence)return;
              counts(d);values.replaceChildren();
              const table=el('table'),head=el('tr');for(const name of [mode==='exact'?'数值':'区间','回答次数'])head.append(el('th',name,{scope:'col'}));table.append(head);
              function row(label,count){const tr=el('tr');tr.append(el('td',label),el('td',String(count)));table.append(tr);}
              if(mode==='exact'){
                values.append(el('p','精确频数：共 '+d.total+' 种数值；当前 '+(d.items.length?d.offset+1:0)+'—'+Math.min(d.offset+d.limit,d.total)+' 种'));
                for(const item of d.items)row(item.value,item.count);
              }else{
                row('小于 '+d.buckets[0].lower,d.underflow);
                for(const bucket of d.buckets)row('['+bucket.lower+', '+bucket.upper+')',bucket.count);
                row('大于等于 '+d.buckets.at(-1).upper,d.overflow);
              }
              values.append(table);
              if(mode==='exact'){
                if(d.offset)button('上一页频数',()=>distribution('exact',Math.max(0,d.offset-d.limit)),values);
                if(d.offset+d.limit<d.total)button('下一页频数',()=>distribution('exact',d.offset+d.limit),values);
              }
            }catch(e){if(turn!==read||ticket!==sequence)return;values.replaceChildren(el('p','分布未更新，请修正输入或重新读取。'));throw e;}
          }
          size.onchange=()=>distribution('exact').catch(fail);
          button('读取精确频数',()=>distribution('exact'),distributionControls);
          button('计算区间分布',()=>distribution('buckets'),distributionControls);
          await distribution('exact');
          if(ticket!==sequence)return;
        }
      }
    }catch(e){if(ticket!==sequence)return;results.replaceChildren(el('p','统计未更新，请重新刷新。'));throw e;}
  }
  selector.onchange=()=>refresh().catch(fail);
  button('刷新跨卷统计',refresh,controls,true);
  await refresh();
}
(async()=>{
  try {
    if(['login','register'].includes(page)) {await auth(); return;}
    const account=await api('/auth/me'); document.querySelector('#account').textContent=account.username;
    const logout=document.querySelector('#logout'); logout.hidden=false;
    logout.onclick=async()=>{try{await api('/auth/logout','POST',{});location.href='/login';}catch(e){fail(e);}};
    await ({list,edit,manage,fill,catalog,banks,statistics:stats,'question-statistics':questionStats}[page])();
  } catch(e) {root.replaceChildren(el('h1','暂时无法打开页面'));fail(e);}
})();

async function catalog(){
  const scope=new URLSearchParams(location.search).get('scope')==='shared'?'shared':'owned';
  const families=await allItems('/questions?scope='+scope);
  clear();root.append(el('h1',scope==='shared'?'共享给我的题目':'我的题目'));link('我的题目','/questions');link('共享给我的题目','/questions?scope=shared');link('我的题库','/question-banks');
  const createBox=el('section',undefined,{class:'card'});root.append(createBox);
  const label=field('题目标签','',createBox);
  const kind=selectField('新题类型',[['single','单选'],['multiple','多选'],['text','文本'],['number','数字']],'number',createBox);
  const familyKey=crypto.randomUUID(),versionKey=crypto.randomUUID();
  let pendingCreate=null,versionDirty=false,releaseCreate=null;
  function requireClean(){if(versionDirty)throw new Error('请先保存题目草稿，再进行其他管理操作。');}
  const createButton=button('创建常用题',async()=>{
    requireClean();
    if(!pendingCreate){const fresh=newQuestion(kind.value);delete fresh.id;delete fresh.rules;pendingCreate={family:{label:label.value,creation_key:familyKey},version:{creation_key:versionKey,payload:fresh}};}
    if(!releaseCreate)releaseCreate=lockControls(createButton);
    try{
      const family=await api('/questions','POST',pendingCreate.family);
      const version=await api('/questions/'+family.id+'/versions','POST',pendingCreate.version);
      location.href='/questions?question='+family.id+'&version='+version.id;
    }catch(e){if(!e.unknown){pendingCreate=null;releaseCreate?.();releaseCreate=null;}throw e;}
  },createBox,true);
  for(const family of families){
    const box=el('section',undefined,{class:'card'});root.append(box);
    box.append(el('h2',family.label));
    link('打开题目','/questions?question='+family.id+'&scope='+scope,box);
  }
  const params=new URLSearchParams(location.search),qid=params.get('question');
  if(!qid)return;
  const family=await api('/questions/'+qid),versions=[];
  for(let offset=0;;offset+=100){const rows=await api('/questions/'+qid+'/versions?offset='+offset+'&limit=100');versions.push(...rows.items);if(offset+100>=rows.total)break;}
  const selected=versions.find(v=>v.id===params.get('version'))??versions[0];
  const familyBox=el('section',undefined,{class:'card',id:'family-management'});root.append(familyBox);
  familyBox.append(el('h2',family.label));
  link('查看跨问卷统计','/questions/'+qid+'/statistics',familyBox);
  if(family.role==='owner'){
    const labelInput=field('修改管理标签',family.label,familyBox);
    frozenButton('保存标签',familyBox,async()=>{requireClean();return {revision:family.revision,label:labelInput.value};},body=>api('/questions/'+qid,'PATCH',body),async()=>catalog());
    familyBox.append(el('p','共享只授予使用权。接收者不能编辑或转共享；撤回不删除已保存的问卷副本。'));
    const sharing=field('共享给指定用户名（逗号分隔，留空撤回全部）',(family.shared_with??[]).join(', '),familyBox);
    frozenButton('保存共享名单',familyBox,async()=>{requireClean();return {revision:family.revision,usernames:sharing.value.split(/[,，]/).map(x=>x.trim()).filter(Boolean)};},body=>api('/questions/'+qid+'/sharing','PUT',body),async()=>catalog());
  }else familyBox.append(el('p','只读共享：可选用已定稿版本。'));
  const uses=el('section',undefined,{id:'usage-list'});familyBox.append(uses);
  async function usages(offset=0){const data=await api('/questions/'+qid+'/usages?limit=20&offset='+offset);uses.replaceChildren(el('h3','使用清单'),el('p','共 '+data.total+' 份问卷；仅显示允许查看的使用关系。'));
    for(const item of data.items)uses.append(el('p',item.title+' · '+item.survey_id+' · '+surveyState({state:item.state,deadline_at:item.expired?'2000-01-01':null})+' · '+item.versions.map(x=>x.version_id+'：'+x.instance_count+'个实例').join('；')));
    if(offset)button('上一页使用记录',()=>usages(Math.max(0,offset-20)),uses);
    if(offset+20<data.total)button('下一页使用记录',()=>usages(offset+20),uses);
  }
  await usages();
  if(!selected)return;
  const area=el('section',undefined,{class:'card',id:'version-editor'});root.append(area);
  area.append(el('h2',family.label+' · v'+selected.version_no+' · '+(selected.state==='sealed'?'已定稿':'草稿')));
  for(const version of versions)link('v'+version.version_no+' '+(version.state==='sealed'?'已定稿':'草稿'),'/questions?question='+qid+'&scope='+scope+'&version='+version.id,area);
  const value=structuredClone(selected.payload);
  const content=el('fieldset');content.addEventListener('input',()=>{versionDirty=true;});content.append(el('legend','版本内容'));area.append(content);
  const title=field('题干',value.title,content);title.oninput=()=>value.title=title.value;
  const required=field('必须回答',value.required,content,'checkbox');required.onchange=()=>value.required=required.checked;
  const optionsBox=el('section');content.append(optionsBox);
  function renderOptions(){
    optionsBox.replaceChildren();
    value.options.forEach((o,i)=>{
      const row=el('div',undefined,{class:'row'});optionsBox.append(row);
      const input=field('选项 '+(i+1),o.text,row);input.oninput=()=>o.text=input.value;
      if(selected.state==='draft'){
        button('上移选项',()=>{move(value.options,i,-1);versionDirty=true;renderOptions();},row).disabled=i===0;
        button('下移选项',()=>{move(value.options,i,1);versionDirty=true;renderOptions();},row).disabled=i===value.options.length-1;
        button('删除选项',()=>{value.options.splice(i,1);versionDirty=true;renderOptions();},row);
      }
    });
    if(['single','multiple'].includes(value.kind)&&selected.state==='draft')button('添加选项',()=>{value.options.push({id:crypto.randomUUID(),text:''});versionDirty=true;renderOptions();},optionsBox).disabled=value.options.length>=LIMITS.options;
  }
  renderOptions();
  const limits={multiple:[['min_choices','最少选择数'],['max_choices','最多选择数']],text:[['min_length','最少字数'],['max_length','最多字数']],number:[['min_value','最小值'],['max_value','最大值']]}[value.kind]??[];
  for(const [key,text] of limits){const input=field(text,value.validation[key],content);input.oninput=()=>value.validation[key]=input.value===''?null:value.kind==='number'?input.value:/^\d{1,5}$/.test(input.value)?Number(input.value):input.value;}
  if(value.kind==='number'){const input=field('仅允许整数',value.validation.integer_only,content,'checkbox');input.onchange=()=>value.validation.integer_only=input.checked;}
  area.append(el('p','操作：'+selected.operation+' · 父版本：'+(selected.parent_version_id??'无')+' · 恢复来源：'+(selected.restored_from_version_id??'无')));
  if(selected.state==='sealed'){
    for(const control of content.querySelectorAll('input,button'))control.disabled=true;
    if(family.role==='owner'){
    const key=crypto.randomUUID();
    frozenButton('派生新版',area,async()=>({creation_key:key,operation:'derive',parent_version_id:selected.id}),body=>api('/questions/'+qid+'/versions','POST',body),async result=>{location.href='/questions?question='+qid+'&version='+result.id;});
    const restoreKey=crypto.randomUUID();
    frozenButton('恢复此版为独立新版本',area,async()=>({creation_key:restoreKey,operation:'restore',parent_version_id:selected.id,restored_from_version_id:selected.id}),body=>api('/questions/'+qid+'/versions','POST',body),async result=>{location.href='/questions?question='+qid+'&version='+result.id;});
    }
    link('创建问卷并选入','/surveys',area);
  }else{
    let revision=selected.revision,pendingSeal=null,pendingSave=null,releaseSeal=null,releaseSave=null;
    async function save(){
      if(!pendingSave)pendingSave={revision,payload:structuredClone(value)};
      try{const result=await api('/questions/'+qid+'/versions/'+selected.id+'/draft','PUT',pendingSave);revision=result.revision;versionDirty=false;pendingSave=null;return result;}
      catch(e){if(!e.unknown)pendingSave=null;throw e;}
    }
    const saveButton=button('保存题目草稿',async()=>{
      if(!releaseSave)releaseSave=lockControls(saveButton);
      try{const result=await save();releaseSave();releaseSave=null;document.querySelector('#notice').textContent='题目草稿已保存'+(result.seal_issues.length?'；'+result.seal_issues.map(x=>x.message).join('；'):'');}
      catch(e){if(!e.unknown){releaseSave?.();releaseSave=null;}throw e;}
    },area,true);
    const sealButton=button('定稿版本',async()=>{
      try{
        if(!releaseSeal)releaseSeal=lockControls(sealButton);
        if(!pendingSeal){await save();pendingSeal={revision};content.disabled=true;saveButton.disabled=true;}
        await api('/questions/'+qid+'/versions/'+selected.id+'/seal','POST',pendingSeal);await catalog();
      }catch(e){if(!e.unknown){pendingSeal=null;content.disabled=false;releaseSeal?.();releaseSeal=null;}throw e;}
    },area,true);
  }
}

async function allItems(path){
  const items=[];
  for(let offset=0;;offset+=100){const rows=await api(path+(path.includes('?')?'&':'?')+'limit=100&offset='+offset);items.push(...rows.items);if(offset+100>=rows.total)return items;}
}
function lockControls(except){
  const states=Array.from(root.querySelectorAll('input,textarea,select,button')).map(node=>[node,node.disabled]);
  for(const [node]of states)if(node!==except)node.disabled=true;
  return ()=>{for(const [node,disabled]of states)if(node!==except)node.disabled=disabled;};
}
function frozenButton(text,parent,prepare,send,done){
  let pending=null,release=null;
  const control=button(text,async()=>{
    try{
      if(!release)release=lockControls(control);
      if(!pending)pending=structuredClone(await prepare());
      const result=await send(pending);pending=null;await done(result);
    }catch(e){
      if(!e.unknown){pending=null;release?.();release=null;}
      throw e;
    }
  },parent);
  return control;
}
function grantControls(survey,parent,before,done){
  const external=(survey.result_grant_options??[]).filter(x=>x.external);
  if(!external.length)return;
  const box=el('section',undefined,{class:'card',id:'result-grants'});parent.append(box);
  box.append(el('h2','单题汇总授权'),el('p','默认关闭。开启后，题目所有者可汇总本卷该家族全部实例的回答，不含身份或逐条内容。发布后仅可撤回，撤回后不能重开。'));
  for(const x of external){
    const grant=survey.question_result_grants.find(g=>g.question_id===x.question_id);
    const titles=[...new Set(survey.questions.filter(q=>q.source?.question_id===x.question_id).map(q=>q.title))].join('、');
    const row=el('section');box.append(row,el('p',titles+'：'+(grant.enabled?'已开启':'已关闭')));
    if(survey.state==='draft'||grant.enabled){
      const enabled=survey.state==='draft'?field('允许向题目所有者提供汇总：'+titles,grant.enabled,row,'checkbox'):null;
      frozenButton(survey.state==='draft'?'保存本家族授权':'撤回本家族授权',row,async()=>{
        const target=enabled?enabled.checked:false,current=await before();
        return {revision:current.revision,enabled:target};
      },body=>api('/surveys/'+survey.id+'/question-result-grants/'+x.question_id,'PUT',body),done);
    }
  }
}
async function banks(){
  const rows=await allItems('/question-banks');clear();root.append(el('h1','我的题库'));
  const createBox=el('section',undefined,{class:'card'});root.append(createBox);
  const title=field('新题库名称','',createBox),key=crypto.randomUUID();
  frozenButton('创建题库',createBox,async()=>({title:title.value,creation_key:key}),body=>api('/question-banks','POST',body),async result=>{location.href='/question-banks?bank='+result.id;});
  for(const item of rows){const box=el('section',undefined,{class:'card'});root.append(box);box.append(el('h2',item.title));link('管理题库','/question-banks?bank='+item.id,box);}
  const bid=new URLSearchParams(location.search).get('bank');if(!bid)return;
  const bank=await api('/question-banks/'+bid),choices=[];
  for(const scope of ['owned','shared'])for(const family of await allItems('/questions?scope='+scope)){
    for(const version of await allItems('/questions/'+family.id+'/versions'))if(version.state==='sealed')choices.push({family,version});
  }
  const area=el('section',undefined,{class:'card',id:'bank-editor'});root.append(area);area.append(el('h2',bank.title),el('p','每个家族保留一个明确默认版；更新默认版或移除条目不会改变已选入的问卷。'));
  const name=field('题库名称',bank.title,area);
  const entries=bank.entries.map(x=>({question_id:x.question_id,default_version_id:x.default_version_id}));
  function body(next=entries){return {revision:bank.revision,title:name.value,entries:next};}
  frozenButton('保存题库名称',area,async()=>body(),x=>api('/question-banks/'+bid,'PUT',x),async()=>banks());
  for(const entry of bank.entries){
    const row=el('section',undefined,{class:'card'});area.append(row);
    row.append(el('h3',entry.available?entry.label+' · 默认 v'+entry.version_no:'使用权已撤回：'+entry.question_id));
    if(entry.available){
      const versions=choices.filter(x=>x.family.id===entry.question_id);
      const version=selectField('明确选择默认版：'+entry.label,versions.map(x=>[x.version.id,'v'+x.version.version_no+' · '+x.version.payload.title]),entry.default_version_id,row);
      frozenButton('更新此题默认版',row,async()=>body(entries.map(x=>x.question_id===entry.question_id?{...x,default_version_id:version.value}:x)),x=>api('/question-banks/'+bid,'PUT',x),async()=>banks());
    }
    frozenButton('从题库移除',row,async()=>body(entries.filter(x=>x.question_id!==entry.question_id)),x=>api('/question-banks/'+bid,'PUT',x),async()=>banks());
  }
  const candidates=choices.filter(x=>!entries.some(y=>y.question_id===x.family.id));
  if(candidates.length){
    const selected=selectField('加入题库的确切版本',candidates.map((x,i)=>[String(i),x.family.label+' · v'+x.version.version_no]),'0',area);
    frozenButton('加入题库',area,async()=>{const x=candidates[Number(selected.value)];return body([...entries,{question_id:x.family.id,default_version_id:x.version.id}]);},x=>api('/question-banks/'+bid,'PUT',x),async()=>banks());
  }
  link('创建问卷并从题库选题','/surveys',area);
}
