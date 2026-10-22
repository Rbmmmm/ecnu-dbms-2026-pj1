from uuid import UUID


def uid(number):
    return str(UUID(int=number))


J1,J2,J3,J4,J5 = [uid(i) for i in range(1,6)]
A,B,END_OPTION = [uid(i) for i in range(11,14)]
X,Y,Z = [uid(i) for i in range(21,24)]


def f02_questions():
    return [
        {'id':J1,'kind':'single','title':'J1 分支选择','required':True,'validation':{},
         'options':[{'id':A,'text':'A'},{'id':B,'text':'B'},{'id':END_OPTION,'text':'结束'}],
         'rules':[{'op':'eq','operand':A,'target':J3},{'op':'eq','operand':B,'target':J2},{'op':'eq','operand':END_OPTION,'target':'END'}]},
        {'id':J2,'kind':'multiple','title':'J2 多选条件','required':True,'validation':{'min_choices':None,'max_choices':None},
         'options':[{'id':X,'text':'x'},{'id':Y,'text':'y'},{'id':Z,'text':'z'}],
         'rules':[{'op':'any','operand':[X],'target':J4},{'op':'all','operand':[X,Y],'target':J5}]},
        {'id':J3,'kind':'number','title':'J3 年龄','required':False,'validation':{'min_value':'0','max_value':'120','integer_only':True},
         'options':[],'rules':[{'op':'gte','operand':'18','target':J5}]},
        {'id':J4,'kind':'text','title':'J4 完成说明','required':True,'validation':{'min_length':1,'max_length':10},'options':[],'rules':[]},
        {'id':J5,'kind':'text','title':'J5 补充','required':False,'validation':{'min_length':1,'max_length':10},'options':[],'rules':[]}
    ]
