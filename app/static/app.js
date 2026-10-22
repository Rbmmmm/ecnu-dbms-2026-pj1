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
      if (length > 128 || (registering && length < 8)) throw new Error('密码需8—128个Unicode码点，保留首尾空格。');
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
  root.append(el('p', '共 ' + data.total + ' 份问卷；此处只显示当前账号创建的问卷。'));
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
  let deadlineRaw=shanghaiInput(survey.deadline_at),deadlineInput;
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
    root.append(el('p','工程上限：'+LIMITS.questions+'题，每题'+LIMITS.options+'选项、'+LIMITS.rules+'条规则；标题'+LIMITS.survey_title+'、说明'+LIMITS.description+'、题干'+LIMITS.question_title+'、选项'+LIMITS.option_text+'、文本答案'+LIMITS.text_answer+'个码点。草稿可不完整，发布前需修复所有问题。',{class:'muted'}));
    survey.questions.forEach((q,index)=>{
      const card=el('fieldset',undefined,{'data-question-id':q.id,'data-kind':q.kind});card.append(el('legend','第 '+(index+1)+' 题'));root.append(card);
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
      if(q.kind==='text')card.append(el('p','首尾空白去除后按Unicode码点计数，组合表情可能计多个；上下界含端点。',{class:'muted'}));
      if(q.kind==='number'){
        const integer=field('仅允许整数',q.validation.integer_only,card,'checkbox');integer.onchange=()=>q.validation.integer_only=integer.checked;
        card.append(el('p','上下界含端点；18.0视作整数，0有效。数字按十进制字符串传输，须在Decimal128有限精度范围内无损表示。',{class:'muted'}));
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
      button('上移题目',()=>{move(survey.questions,index,-1);render();},card).disabled=index===0;
      button('下移题目',()=>{move(survey.questions,index,1);render();},card).disabled=index===survey.questions.length-1;
      button('删除题目',()=>{survey.questions.splice(index,1);render();},card);
    });
    for(const [kind,label] of kinds)button('添加'+label+'题',()=>{survey.questions.push(newQuestion(kind));render();}).disabled=survey.questions.length>=LIMITS.questions;
    button('保存草稿',async()=>{await save();document.querySelector('#notice').textContent='草稿已保存；待修复项：'+survey.publish_issues.map(v=>{const index=survey.questions.findIndex(q=>q.id===v.question_id);return(index>=0?'第'+(index+1)+'题：':'')+v.message;}).join('；');},root,true);
    button('发布问卷',async()=>{await save();await api('/surveys/'+sid+'/publish','POST',{revision:survey.revision});location.href='/surveys/'+sid+'/manage';},root,true);
  }
  async function save(){
    survey.deadline_at=deadlineUTC(deadlineInput);
    const body=Object.fromEntries(['revision','title','description','allow_anonymous','allow_repeat','deadline_at','questions'].map(k=>[k,survey[k]]));
    survey=await api('/surveys/'+sid+'/draft','PUT',body);deadlineRaw=shanghaiInput(survey.deadline_at);render();
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
  if(survey.state==='published'){
    const deadline=field('截止时间（北京时间，可留空）',shanghaiInput(survey.deadline_at),root,'datetime-local');deadline.step='0.001';
    root.append(el('p','只能设置未来时间或清除。到期后可延长恢复接收；手动关闭后不能重开。提交以服务端最后受理检查为准。',{class:'muted'}));
    button('保存截止时间',async()=>{await api('/surveys/'+sid+'/deadline','PATCH',{revision:survey.revision,deadline_at:deadlineUTC(deadline)});await manage();},root,true);
    button('清除截止时间',async()=>{await api('/surveys/'+sid+'/deadline','PATCH',{revision:survey.revision,deadline_at:null});await manage();});
    button('关闭问卷',async()=>{await api('/surveys/'+sid+'/close','POST',{revision:survey.revision});await manage();});
  }else if(survey.state==='draft')link('编辑草稿','/surveys/'+sid+'/edit');
}
async function fill() {
  const survey=await api('/fill/'+sid);
  let answers={},key=crypto.randomUUID(),path=[survey.questions[0].id],shown=survey.questions[0].id,frozenBody=null,anonymousChoice=false;
  const questions=Object.fromEntries(survey.questions.map(q=>[q.id,q]));
  function heading(){clear();root.append(el('h1',survey.title),el('p',survey.description));}
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
          input.inputMode='decimal';hint.textContent='最小值 '+(q.validation.min_value??'不限')+'，最大值 '+(q.validation.max_value??'不限')+(q.validation.integer_only?'，仅整数':'，可小数')+'。0有效，数字以十进制字符串传输。';
          input.oninput=()=>selected=input.value;
        }else{
          function count(){selected=input.value;hint.textContent='去首尾空白后 '+Array.from(input.value.trim()).length+' 个码点；最少 '+(q.validation.min_length??'不限')+'，最多 '+(q.validation.max_length??'不限')+'。以服务端校验为准。';}
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
      root.append(el('p',survey.allow_repeat?'允许多次填写。刷新后视为一次新填写；结果未知时请先重试原提交。':'每个账号只能有效提交一份。'));
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
(async()=>{
  try {
    if(['login','register'].includes(page)) {await auth(); return;}
    const account=await api('/auth/me'); document.querySelector('#account').textContent=account.username;
    const logout=document.querySelector('#logout'); logout.hidden=false;
    logout.onclick=async()=>{try{await api('/auth/logout','POST',{});location.href='/login';}catch(e){fail(e);}};
    await ({list,edit,manage,fill,statistics:stats}[page])();
  } catch(e) {root.replaceChildren(el('h1','暂时无法打开页面'));fail(e);}
})();
