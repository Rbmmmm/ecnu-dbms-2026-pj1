from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from app.decimal_values import decimal_value, canonical_decimal
from app.domain import matches_rule
from tests.test_p2 import register,login,question
from tests.fixtures_f02 import *


def configure(client,questions,repeat=True):
    created=client.post('/api/v1/surveys',json={'title':'W-007固定样本','allow_repeat':repeat}).json()
    body={key:created[key] for key in ['revision','title','description','deadline_at','allow_repeat','allow_anonymous']}
    body['questions']=questions
    saved=client.put('/api/v1/surveys/'+created['id']+'/draft',json=body)
    assert saved.status_code==200,saved.text
    return saved.json(),body


def published(context,questions):
    _,client,_=context
    register(client,'owner');login(client,'owner')
    survey,_=configure(client,questions)
    response=client.post('/api/v1/surveys/'+survey['id']+'/publish',json={'revision':survey['revision']})
    assert response.status_code==200,response.text
    return client,response.json()


def evaluate(client,survey,answers):
    response=client.post('/api/v1/fill/'+survey['id']+'/evaluate',json={'content_version':1,'answers':answers})
    assert response.status_code==200,response.text
    return response.json()


def submit(client,survey,answers,key=None):
    return client.post('/api/v1/fill/'+survey['id']+'/responses',json={'content_version':1,'submission_key':key or str(uuid4()),'anonymous':False,'answers':answers})


def only_question(kind,validation=None,required=False):
    return {'id':J1,'kind':kind,'title':'边界题','required':required,'validation':validation or {},
            'options':[{'id':A,'text':'A'},{'id':B,'text':'B'},{'id':END_OPTION,'text':'C'}] if kind in ['single','multiple'] else [],'rules':[]}


def test_T03_F02_paths_priority_and_storage(context):
    _,_,db=context
    client,survey=published(context,f02_questions())
    cases=[({J1:A,J3:'18',J5:None},[J1,J3,J5],2),
           ({J1:B,J2:[X,Y],J4:'完成',J5:None},[J1,J2,J4,J5],3)]
    for answers,path,size in cases:
        result=evaluate(client,survey,answers)
        assert result['path']==path and result['ready_to_submit'] is True
        assert result['errors']==[] and result['pending_answers']=={}
        response=submit(client,survey,result['active_answers'])
        assert response.status_code==201,response.text
        saved=db.survey_responses.find_one({'_id':ObjectId(response.json()['response_id'])})
        assert saved['path']==path and len(saved['answers'])==size
    saved=db.survey_responses.find_one({'path':[J1,J3,J5]})
    assert saved['answers'][1]=={'question_id':J3,'value':Decimal128('18')}
    config=db.survey_surveys.find_one({'_id':ObjectId(survey['id'])})['questions'][2]
    assert isinstance(config['validation']['max_value'],Decimal128)
    assert isinstance(config['rules'][0]['operand'],Decimal128)
    wire=client.get('/api/v1/fill/'+survey['id']).json()['questions'][2]
    assert wire['validation']=={'min_value':'0','max_value':'120','integer_only':True}
    assert wire['rules'][0]['operand']=='18'


def test_T03_F02_backtrack_pending_and_no_resurrection(context):
    client,survey=published(context,f02_questions())
    original={J1:B,J2:[Y],J3:'18',J5:'保留'}
    assert evaluate(client,survey,original)['path']==[J1,J2,J3,J5]
    changed={**original,J1:A}
    result=evaluate(client,survey,changed)
    assert result['removed_question_ids']==[J2]
    assert result['active_answers']=={J1:A,J3:'18',J5:'保留'}
    changed={**result['active_answers'],J3:'17'}
    result=evaluate(client,survey,changed)
    assert result['path']==[J1,J3,J4] and result['current_question_id']==J4
    assert result['pending_answers']=={J5:'保留'} and result['ready_to_submit'] is False
    changed={**result['active_answers'],**result['pending_answers'],J4:'完成'}
    result=evaluate(client,survey,changed)
    assert result['path']==[J1,J3,J4,J5] and result['ready_to_submit'] is True
    assert result['active_answers'][J5]=='保留'
    result=evaluate(client,survey,{**result['active_answers'],J1:END_OPTION})
    assert result['path']==[J1] and result['ready_to_submit'] is True
    assert result['removed_question_ids']==[J3,J4,J5] and result['pending_answers']=={}
    result=evaluate(client,survey,{**result['active_answers'],J1:A})
    assert result['path']==[J1,J3] and result['current_question_id']==J3
    assert result['active_answers']=={J1:A} and result['pending_answers']=={}


def test_T03_F02_stale_branches_and_pending_cannot_submit(context):
    _,_,db=context
    client,survey=published(context,f02_questions())
    invalid=[{J1:A,J3:'18',J5:None,J4:'旧分支'}, {J1:A,J3:'18'}, {J1:A,J3:'17',J5:'候选'}, {uid(999):None}, {J1:B,J2:[X,X]}]
    for answers in invalid:
        response=submit(client,survey,answers)
        assert response.status_code==422,response.text
    assert submit(client,survey,invalid[0]).json()['error']['code']=='STALE_BRANCH_ANSWERS'
    assert db.survey_responses.count_documents({})==0
    # Off-path business-invalid content is cleaned; unknown options/types are structural errors globally.
    result=evaluate(client,survey,{J1:END_OPTION,J4:'超过十码点的旧分支内容应忽略'})
    assert result['path']==[J1] and result['errors']==[] and result['ready_to_submit'] is True
    assert result['removed_question_ids']==[J4]
    assert submit(client,survey,result['active_answers']).status_code==201


@pytest.mark.parametrize('op,expected',[
    ('eq',[False,True,False]),('ne',[True,False,True]),('gt',[False,False,True]),
    ('gte',[False,True,True]),('lt',[True,False,False]),('lte',[True,True,False])])
def test_T03_number_operators_and_null(context,op,expected):
    q=only_question('number');q['rules']=[{'op':op,'operand':'18','target':'END'}]
    end={'id':J2,'kind':'text','title':'后续','required':True,'validation':{},'options':[],'rules':[]}
    client,survey=published(context,[q,end])
    for value,matched in zip(['17','18','19'],expected):
        result=evaluate(client,survey,{J1:value})
        assert result['path']==([J1] if matched else [J1,J2])
        assert result['ready_to_submit']==matched
    result=evaluate(client,survey,{J1:None})
    assert result['path']==[J1,J2] and result['ready_to_submit'] is False


@pytest.mark.parametrize('op,operand,answer,expected',[
    ('any',[A,B],[A],True),('any',[A],[B],False),('all',[A,B],[A],False),('all',[B,A],[A,B],True),
    ('exact',[B,A],[A,B],True),('exact',[A,B],[A],False)])
def test_T03_multiple_match_modes(context,op,operand,answer,expected):
    q=only_question('multiple');q['rules']=[{'op':op,'operand':operand,'target':'END'}]
    end={'id':J2,'kind':'text','title':'后续','required':True,'validation':{},'options':[],'rules':[]}
    client,survey=published(context,[q,end])
    result=evaluate(client,survey,{J1:answer})
    assert result['path']==([J1] if expected else [J1,J2])
    assert matches_rule('multiple',None,q['rules'][0]) is False


def test_T03_configuration_change_drives_behavior(context):
    _,client,_=context
    register(client,'owner');login(client,'owner')
    first,_=configure(client,f02_questions())
    second_questions=f02_questions();second_questions[0]['rules'][0]['target']=J4
    second,_=configure(client,second_questions)
    for survey in [first,second]:
        assert client.post('/api/v1/surveys/'+survey['id']+'/publish',json={'revision':survey['revision']}).status_code==200
    assert evaluate(client,first,{J1:A})['current_question_id']==J3
    assert evaluate(client,second,{J1:A})['current_question_id']==J4
    reordered=f02_questions();reordered[1]['rules'].reverse()
    third,_=configure(client,reordered)
    assert client.post('/api/v1/surveys/'+third['id']+'/publish',json={'revision':third['revision']}).status_code==200
    assert evaluate(client,first,{J1:B,J2:[X,Y]})['current_question_id']==J4
    assert evaluate(client,third,{J1:B,J2:[X,Y]})['current_question_id']==J5


@pytest.mark.parametrize('kind,validation,valid,invalid',[
    ('single',{},[A,None,'   '],['unknown',[],True]),
    ('multiple',{'min_choices':2,'max_choices':2},[[A,B],None,[]],[[A],[A,B,END_OPTION],[A,A],['unknown'],A]),
    ('text',{'min_length':4,'max_length':4},['  中文 A  ',None,'   '],['中文','中文 AB',[]]),
    ('number',{'min_value':'0','max_value':'120','integer_only':True},['0','120','18.0',None,''],['-1','121','18.5','NaN','Infinity','1E6145','1E-6177',18,True])])
def test_T04_constraints_optional_values_and_types(context,kind,validation,valid,invalid):
    _,_,db=context
    client,survey=published(context,[only_question(kind,validation)])
    for value in valid:
        response=submit(client,survey,{J1:value});assert response.status_code==201,response.text
    before=db.survey_responses.count_documents({})
    for value in invalid:
        response=submit(client,survey,{J1:value});assert response.status_code==422,(kind,value,response.text)
    assert db.survey_responses.count_documents({})==before
    if kind=='text':
        assert db.survey_responses.find_one({'answers.value':'中文 A'})['answers'][0]['value']=='中文 A'


@pytest.mark.parametrize('kind,empty', [('single',None),('multiple',[]),('text','  '),('number',None)])
def test_T04_required_all_types_and_missing_key(context,kind,empty):
    client,survey=published(context,[only_question(kind,required=True)])
    for answers in [{},{J1:empty}]:assert submit(client,survey,answers).status_code==422


def test_T04_unicode_codepoints(context):
    client,survey=published(context,[only_question('text',{'min_length':1,'max_length':1})])
    assert submit(client,survey,{J1:'𠮷'}).status_code==201
    assert submit(client,survey,{J1:'👩‍💻'}).status_code==422
    assert len('👩‍💻')==3


def test_T04_decimal_precision_bson_and_equivalent_retry(context):
    _,_,db=context
    client,survey=published(context,[only_question('number')])
    exact='1234567890123456789012345678901234'
    for value in [exact,'1E-6176','-2.5','1.005']:
        response=submit(client,survey,{J1:value});assert response.status_code==201,response.text
        stored=db.survey_responses.find_one({'_id':ObjectId(response.json()['response_id'])})['answers'][0]['value']
        assert isinstance(stored,Decimal128) and stored.to_decimal()==Decimal(value)
    assert canonical_decimal(exact)==exact
    assert submit(client,survey,{J1:'12345678901234567890123456789012345'}).status_code==422
    key=str(uuid4());first=submit(client,survey,{J1:'18.0'},key);retry=submit(client,survey,{J1:'18'},key)
    assert first.status_code==201 and retry.status_code==200 and first.json()==retry.json()


@pytest.mark.parametrize('mutate',[
    'min_gt_max','optional_max_zero','count_exceeds_options','text_zero','integer_empty_range',
    'forward_reference_missing','backward','duplicate_text','duplicate_rule_operand','empty_rule_operand',
    'unknown_rule_option','wrong_operator','text_rule','text_options'])
def test_T04_incomplete_draft_saves_but_publish_rejects(context,mutate):
    _,client,_=context
    register(client,'owner');login(client,'owner')
    questions=f02_questions()
    if mutate=='min_gt_max':questions[1]['validation']={'min_choices':2,'max_choices':1}
    elif mutate=='optional_max_zero':questions[1]['required']=False;questions[1]['validation']={'max_choices':0}
    elif mutate=='count_exceeds_options':questions[1]['validation']={'min_choices':4}
    elif mutate=='text_zero':questions[4]['validation']={'max_length':0}
    elif mutate=='integer_empty_range':questions[2]['validation']={'min_value':'0.1','max_value':'0.9','integer_only':True}
    elif mutate=='forward_reference_missing':questions[0]['rules'][0]['target']=uid(999)
    elif mutate=='backward':questions[0],questions[2]=questions[2],questions[0]
    elif mutate=='duplicate_text':questions[0]['options'][1]['text']=' A '
    elif mutate=='duplicate_rule_operand':questions[1]['rules'][0]['operand']=[X,X]
    elif mutate=='empty_rule_operand':questions[1]['rules'][0]['operand']=[]
    elif mutate=='unknown_rule_option':questions[0]['rules'][0]['operand']=uid(999)
    elif mutate=='wrong_operator':questions[0]['rules'][0]['op']='ne'
    elif mutate=='text_rule':questions[4]['rules']=[{'op':'eq','operand':'x','target':'END'}]
    elif mutate=='text_options':questions[4]['options']=[{'id':uid(999),'text':'残留'}]
    survey,_=configure(client,questions)
    assert survey['publish_issues']
    response=client.post('/api/v1/surveys/'+survey['id']+'/publish',json={'revision':survey['revision']})
    assert response.status_code==422,response.text


def test_T04_structure_engineering_and_decimal_configuration_rejected(context):
    _,client,_=context
    register(client,'owner');login(client,'owner')
    survey,body=configure(client,f02_questions())
    cases=[]
    for value in [True,1.5,'2',-1,101]:
        invalid=deepcopy(body);invalid['questions'][1]['validation']['min_choices']=value;cases.append(invalid)
    for value in ['NaN','Infinity','1E6145','1E-6177','12345678901234567890123456789012345',True,18]:
        invalid=deepcopy(body);invalid['questions'][2]['validation']['min_value']=value;cases.append(invalid)
    invalid=deepcopy(body);invalid['questions'][4]['validation']['max_length']=10001;cases.append(invalid)
    invalid=deepcopy(body);invalid['questions'][0]['rules']*=17;cases.append(invalid)
    invalid=deepcopy(body);invalid['questions'][0]['options'][1]['id']=A;cases.append(invalid)
    invalid=deepcopy(body);invalid['questions'][0]['rules'][0]['target']='bad-id';cases.append(invalid)
    for invalid in cases:
        invalid['revision']=survey['revision']
        response=client.put('/api/v1/surveys/'+survey['id']+'/draft',json=invalid)
        assert response.status_code==422,response.text


def test_T04_fractional_integer_bounds_with_feasible_integer(context):
    client,survey=published(context,[only_question('number',{'min_value':'17.5','max_value':'18.5','integer_only':True})])
    assert submit(client,survey,{J1:'18.0'}).status_code==201
    assert submit(client,survey,{J1:'18.5'}).status_code==422


def test_T04_canonical_empty_and_equivalent_multiple_number_retries(context):
    _,client,db=context
    register(client,'owner');login(client,'owner')
    q1=only_question('multiple');q2=only_question('number');q2['id']=J2
    survey,_=configure(client,[q1,q2])
    assert client.post('/api/v1/surveys/'+survey['id']+'/publish',json={'revision':survey['revision']}).status_code==200
    key=str(uuid4())
    first=submit(client,survey,{J1:[B,A],J2:'-0.0'},key)
    retry=submit(client,survey,{J1:[A,B],J2:'0'},key)
    assert first.status_code==201 and retry.status_code==200 and first.json()==retry.json()
    saved=db.survey_responses.find_one({'_id':ObjectId(first.json()['response_id'])})
    assert saved['answers']==[{'question_id':J1,'value':[A,B]},{'question_id':J2,'value':Decimal128('0')}]
    empty=submit(client,survey,{J1:[],J2:'   '})
    assert empty.status_code==201
    saved=db.survey_responses.find_one({'_id':ObjectId(empty.json()['response_id'])})
    assert saved['path']==[J1,J2] and saved['answers']==[]
