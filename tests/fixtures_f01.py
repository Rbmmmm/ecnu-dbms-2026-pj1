from tests.fixtures_f02 import uid

Q1,Q2,Q3,Q4 = [uid(i) for i in range(101,105)]
A,B,C = [uid(i) for i in range(201,204)]
APPLE,BANANA,GRAPE = [uid(i) for i in range(211,214)]


def f01_questions():
    return [
        {'id':Q1,'kind':'single','title':'Q1 单选','required':False,'validation':{},'rules':[],
         'options':[{'id':A,'text':'A'},{'id':B,'text':'B'},{'id':C,'text':'C'}]},
        {'id':Q2,'kind':'multiple','title':'Q2 水果','required':False,'validation':{'min_choices':1,'max_choices':2},'rules':[],
         'options':[{'id':APPLE,'text':'苹果'},{'id':BANANA,'text':'香蕉'},{'id':GRAPE,'text':'葡萄'}]},
        {'id':Q3,'kind':'text','title':'Q3 文本','required':False,'validation':{'min_length':1,'max_length':10},'options':[],'rules':[]},
        {'id':Q4,'kind':'number','title':'Q4 数字','required':False,'validation':{'min_value':'0','max_value':'120','integer_only':True},'options':[],'rules':[]}
    ]
