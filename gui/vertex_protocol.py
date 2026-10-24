"""Vertex generateContent adapter, preserving all signed model parts verbatim."""
import copy
import json
from urllib.parse import urlsplit, quote


def is_vertex(url):
    parsed=urlsplit(url);host=parsed.hostname or ''
    return parsed.scheme=='https' and (host=='aiplatform.googleapis.com' or host.endswith('-aiplatform.googleapis.com'))


def endpoint(base,model):
    model=model.removeprefix('models/')
    return base.rstrip('/')+'/publishers/google/models/'+quote(model,safe='-_.')+':generateContent'


def headers(base,credential):
    import re
    match=re.search(r'/projects/([a-z0-9-]+)/locations/',base+'/')
    return {'Authorization':'Bearer '+credential,**({'x-goog-user-project':match[1]} if match else {})}


def request(model,messages,tools=(),max_tokens=32768,effort=None):
    contents=[];system=[];calls={}
    for m in messages:
        role=m.get('role')
        if role=='system':system.append(m['content'])
        elif role=='user':contents.append({'role':'user','parts':[{'text':m['content']}]})
        elif role=='assistant':
            if '_vertex_content' not in m:raise ValueError('Vertex 历史缺少完整签名块，请创建新会话。')
            contents.append(copy.deepcopy(m['_vertex_content']))
            for call in m.get('tool_calls',[]):
                native_id=next((p['functionCall']['id'] for p in m['_vertex_content'].get('parts',[])
                    if p.get('functionCall',{}).get('id')==call['id']),None)
                calls[call['id']]=(call['function']['name'],native_id)
        elif role=='tool':
            matched=calls.get(m['tool_call_id'])
            if not matched:raise ValueError('Vertex 工具结果缺少对应调用。')
            name,native_id=matched
            part={'functionResponse':{'name':name,'response':{'result':m['content']},**({'id':native_id} if native_id else {})}}
            if contents and contents[-1]['role']=='user' and 'functionResponse' in contents[-1]['parts'][0]:contents[-1]['parts'].append(part)
            else:contents.append({'role':'user','parts':[part]})
    body={'contents':contents,'generationConfig':{'maxOutputTokens':max_tokens}}
    if system:body['systemInstruction']={'parts':[{'text':'\n\n'.join(system)}]}
    if effort:body['generationConfig']['thinkingConfig']={'thinkingLevel':effort.upper()}
    if tools:
        declarations=[]
        for tool in tools:
            f=tool['function'];item={'name':f['name']}
            if 'description' in f:item['description']=f['description']
            if 'parameters' in f:item['parametersJsonSchema']=copy.deepcopy(f['parameters'])
            declarations.append(item)
        body['tools']=[{'functionDeclarations':declarations}]
    return body


def response(data):
    candidates=data.get('candidates',[])
    if not candidates:raise ValueError('Vertex 未返回候选答复，可能被内容过滤。')
    candidate=candidates[0];reason=candidate.get('finishReason')
    if reason!='STOP':raise ValueError('Vertex 返回未完成状态：'+str(reason))
    content=copy.deepcopy(candidate.get('content',{}));calls=[];texts=[]
    for i,part in enumerate(content.get('parts',[])):
        if part.get('text') and not part.get('thought'):texts.append(part['text'])
        if 'functionCall' in part:
            call=part['functionCall']
            calls.append({'id':call.get('id') or 'vertex-'+str(i),'type':'function','function':{
                'name':call['name'],'arguments':json.dumps(call.get('args',{}),ensure_ascii=False)}})
    message={'role':'assistant','content':'\n'.join(texts),'_vertex_content':content}
    if calls:message['tool_calls']=calls
    u=data.get('usageMetadata',{});usage={}
    if isinstance(u.get('promptTokenCount'),int):usage['prompt_tokens']=u['promptTokenCount']
    if isinstance(u.get('candidatesTokenCount'),int):usage['completion_tokens']=u['candidatesTokenCount']+u.get('thoughtsTokenCount',0)
    raw={}
    for old,new in [('promptTokenCount','total_input_tokens'),('candidatesTokenCount','total_output_tokens'),
        ('thoughtsTokenCount','total_thought_tokens'),('cachedContentTokenCount','total_cached_tokens')]:
        if old in u:raw[new]=u[old]
    return {'choices':[{'message':message,'finish_reason':'tool_calls' if calls else 'stop'}],'usage':usage,'gemini_usage':raw}
