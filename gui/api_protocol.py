"""Lossless Gemini Interactions conversion; no credentials or session globals."""
import copy
import json
from urllib.parse import urlsplit


def is_gemini(url):
    return urlsplit(url).hostname=='generativelanguage.googleapis.com'


def gemini_request(model,messages,tools=(),max_tokens=32768):
    steps=[];system=[];calls={}
    for m in messages:
        role=m.get('role')
        if role=='system':system.append(m['content'])
        elif role=='user':steps.append({'type':'user_input','content':[{'type':'text','text':m['content']}]})
        elif role=='assistant':
            if '_gemini_steps' not in m:raise ValueError('Gemini 历史缺少完整原生响应块，不能伪造签名。')
            steps.extend(copy.deepcopy(m['_gemini_steps']))
            for c in m.get('tool_calls',[]):calls[c['id']]=c['function']['name']
        elif role=='tool':
            cid=m['tool_call_id']
            if cid not in calls:raise ValueError('Gemini 工具结果缺少对应调用。')
            steps.append({'type':'function_result','name':calls[cid],'call_id':cid,
                'result':[{'type':'text','text':m['content']}]})
    payload={'model':model.removeprefix('models/'),'store':False,'input':steps,
        'system_instruction':'\n\n'.join(system),'generation_config':{'max_output_tokens':max_tokens}}
    if tools:payload['tools']=[{'type':'function',**t['function']} for t in tools]
    return payload


def gemini_response(data):
    status=data.get('status')
    if status not in ('completed','requires_action'):
        raise ValueError('Gemini 返回未完成状态：'+str(status))
    steps=copy.deepcopy(data.get('steps',[]));texts=[];calls=[]
    for s in steps:
        if s.get('type')=='model_output':
            texts.extend(c.get('text','') for c in s.get('content',[]) if c.get('type')=='text')
        elif s.get('type')=='function_call':
            calls.append({'id':s['id'],'type':'function','function':{'name':s['name'],
                'arguments':json.dumps(s['arguments'],ensure_ascii=False)}})
    message={'role':'assistant','content':'\n'.join(texts),'_gemini_steps':steps}
    if calls:message['tool_calls']=calls
    u=data.get('usage',{})
    usage={}
    if isinstance(u.get('total_input_tokens'),int):usage['prompt_tokens']=u['total_input_tokens']
    if isinstance(u.get('total_output_tokens'),int) and isinstance(u.get('total_thought_tokens'),int):
        usage['completion_tokens']=u['total_output_tokens']+u['total_thought_tokens']
    return {'choices':[{'message':message,'finish_reason':'tool_calls' if calls else 'stop'}],
        'usage':usage,'gemini_usage':u}


GEMINI_URL='https://generativelanguage.googleapis.com/v1beta/interactions'
