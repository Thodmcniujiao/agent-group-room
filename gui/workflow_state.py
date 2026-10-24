"""Versioned public facts. Approval and success are verified by the host."""
import copy
import hashlib
import json
import re

TOLERANCE={1:(1,800),2:(2,1500),3:(3,2500),4:(5,4000),5:(8,None)}


def digest(value):
    return hashlib.sha256((value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()


def new_state(goal,mid,policy):
    return {'goal':{'text':goal,'message_id':mid},'policy_revision':policy['revision'],'rules':None,'checks':{},
        'evidence':[],'next_evidence':1,'plans':{},'current_plan':None,'approvals':[],
        'pending_action':None,'blocker':None,'discussion_rounds':{},'votes':{},'status':'active','step':0}


def add_evidence(state,store,rid,record):
    record=copy.deepcopy(record);record['id']=f"E{state['next_evidence']:02d}";state['next_evidence']+=1
    # Bodies remain in originals/raw audit; durable ledger stores only public references and facts.
    record.pop('reply',None)
    state['evidence'].append(record);store.event(rid,{'type':'evidence','record':record})
    return record


def current_refs(state):
    return {'plan':state.get('current_plan'),'code':state.get('code_hash')}


def delivery_verified(state,execution):
    """An interrupted worker is not success; independent complete acceptance can verify its delivery."""
    expected=execution.get('acceptance_commands',[])
    if not expected or execution.get('bound_to')!=current_refs(state):return False
    for record in reversed(state['evidence']):
        if record.get('verifies_execution')!=execution['id'] or record.get('status')!='complete' or record.get('bound_to')!=current_refs(state):continue
        tests=record.get('tests',[])
        if len(tests)==len(expected) and {t['command'] for t in tests}==set(expected) and all(t['exit_code']==0 for t in tests):return True
    return False


def refresh(state,policy,code):
    if state['policy_revision']!=policy['revision']:
        state['rules']=None;state['policy_revision']=policy['revision']
        for check in state['checks'].values():check['status']='stale'
    state['code_hash']=code
    for check in state['checks'].values():
        if check.get('bound_to')!=current_refs(state):check['status']='stale'


def adopt(state,store,rid,action,members):
    msg=store.message_by_id(action.get('message_id'),rid)
    if msg['provider'] not in members:raise ValueError('方案必须引用当前讨论成员原文。')
    if any(e.get('message_id')==msg['id'] and e.get('status')=='over_limit' for e in state['evidence']):raise ValueError('此修订超出讨论字数限制，请先由一位成员整合最终版。')
    plan=store.save_plan(rid,action.get('plan_id'),msg['id'])
    state['plans'][plan['plan_id']]={'version':plan['version'],'sha256':plan['sha256'],'ref':plan['ref']}
    state['current_plan']=plan['ref'];state['pending_action']=None;state['awaiting_approval']=None;state.pop('approval_document_id',None)
    for check in state['checks'].values():check['status']='stale'
    return plan


def valid_plan(state,store,rid,ref):
    plan=store.plan(rid,ref)
    if state['current_plan']!=ref or state['plans'].get(plan['plan_id'],{}).get('sha256')!=plan['sha256']:raise ValueError('方案已更新，旧版本的同意和批准无效。')
    return plan


def approvals_cover(state,store,rid,action):
    plan=valid_plan(state,store,rid,action.get('plan_ref'))
    ids=action.get('approval_ids')
    if not isinstance(ids,list) or not ids:raise ValueError('执行必须引用真实用户批准编号。')
    approvals={a['id']:a for a in store.approvals(rid)}
    picked=[]
    for aid in ids:
        a=approvals.get(aid)
        if not a or a['sha256']!=plan['sha256'] or a['plan_id']!=plan['plan_id'] or a['version']!=plan['version']:raise ValueError('批准不存在或不属于当前方案。')
        picked.append(a)
    if not any(a['kind']=='user' for a in picked):raise ValueError('缺少用户对当前方案版本的原文批准。')
    return plan


APPROVE_WORDS={'同意','确认','批准','可以','执行','开始执行','同意执行','approve'}


def accept_user_approval(state,store,rid,mid,button=False):
    if button:raise ValueError('按钮批准必须使用独立动作记录。')
    pending=state.get('awaiting_approval')
    if not pending:return None
    msg=store.message_by_id(mid,rid)
    if msg['provider']!='you' or (not button and msg['text'].strip().lower() not in APPROVE_WORDS):return None
    plan=valid_plan(state,store,rid,pending)
    aid=store.add_approval(rid,plan['ref'],'user','user',mid,'user_text')
    state['approvals'].append(aid);state['awaiting_approval']=None
    state['user_resolution']={'plan_ref':plan['ref'],'approval_id':aid,'message_id':mid}
    return aid


def accept_button_approval(state,store,rid,action_id):
    pending=state.get('awaiting_approval')
    if not pending:return None
    plan=valid_plan(state,store,rid,pending)
    if state.get('approval_document_id'):
        document=store.approval_document(rid,state['approval_document_id']);action=store.host_action(action_id,rid)
        if action['payload'].get('document_id')!=document['id'] or action['payload'].get('document_sha256')!=document['document_sha256']:raise ValueError('审批文档已更新，请重新打开当前文档后批准。')
    aid=store.add_approval(rid,plan['ref'],'user','user',source='button',action_id=action_id)
    state['approvals'].append(aid);state['awaiting_approval']=None
    state['user_resolution']={'plan_ref':plan['ref'],'approval_id':aid,'action_id':action_id}
    return aid


def parse_vote(text,ref):
    lines=re.findall(r'^表态[：:]\s*(同意|反对)\s+(P\d+@\d+)(?:[：:]\s*(.*))?\s*$',text,re.M)
    if len(lines)!=1 or lines[0][1]!=ref:return None
    return {'agree':lines[0][0]=='同意','reason':lines[0][2]}


def record_vote(state,store,rid,agent,ref,mid,source='format'):
    valid_plan(state,store,rid,ref);msg=store.message_by_id(mid,rid)
    if msg['provider']!=agent or msg['status']!='complete':raise ValueError('表态必须来自该成员的完整原文。')
    vote=parse_vote(msg['text'],ref)
    if vote is None:return None
    state['votes'].setdefault(ref,{})[agent]={**vote,'source':source,'message_id':mid}
    if vote['agree']:
        aid=store.add_approval(rid,ref,agent,'member',mid,source);state['approvals'].append(aid)
    return vote


def ledger(state,settings,store=None,rid=None):
    value=copy.deepcopy(state)
    relevant={eid for c in state['checks'].values() if c['status'] not in ['met','not_applicable'] for eid in c.get('evidence_ids',[])}
    evidence=state['evidence'];selected=[e for e in evidence if e['id'] in relevant or e in evidence[-8:]]
    keys=['id','kind','agent','status','message_id','bound_to','ask_kind','plan_ref','tests_run','worker_reported_done','changed_files','file_hashes','error','acceptance_commands','verifies_execution']
    value['evidence']=[{**{k:e[k] for k in keys if k in e},'tests':[{'command':t['command'],'exit_code':t['exit_code']} for t in e.get('tests',[])]} for e in selected]
    selected_ids={e['id'] for e in selected};older=[e['id'] for e in evidence if e['id'] not in selected_ids]
    value['evidence_directory']={'count':len(evidence),'older_count':len(older),'first':older[0] if older else None,'last':older[-1] if older else None,'read_tool':'evidence_read'}
    if store is not None:
        ref=state.get('current_plan');value['approvals']=[a for a in store.approvals(rid) if f"{a['plan_id']}@{a['version']}"==ref]
    else:value['approvals']=[]
    value['consensus']={m['id']:state.get('votes',{}).get(state.get('current_plan'),{}).get(m['id'],{'agree':None}) for m in settings['members'] if m['id']!=settings.get('executor')}
    # The ledger carries contract hashes and refs, never a second copy of its body.
    if value.get('pending_action'):
        a=value['pending_action'];value['pending_action']={k:v for k,v in a.items() if k not in ['task','contract_text']}
    value['team']=[{k:m[k] for k in ['id','name','backend','model']} for m in settings['members']]
    value['executor']=settings.get('executor')
    return value


def json_patch(old,new):
    patch={'set':{},'remove':[k for k in old if k not in new]}
    for key,value in new.items():
        if key in old and old[key]==value:continue
        before=old.get(key)
        if isinstance(before,dict) and isinstance(value,dict):patch.setdefault('merge',{})[key]=json_patch(before,value)
        elif isinstance(before,list) and isinstance(value,list) and before and value and all(isinstance(r,dict) and isinstance(r.get('id'),str) for r in before+value) and len({r['id'] for r in before})==len(before) and len({r['id'] for r in value})==len(value):
            a={r['id']:r for r in before};b={r['id']:r for r in value}
            patch.setdefault('lists',{})[key]={'order':[r['id'] for r in value],'items':json_patch(a,b)}
        else:patch['set'][key]=copy.deepcopy(value)
    return patch


def apply_patch(old,patch):
    result=copy.deepcopy(old)
    for key in patch['remove']:result.pop(key,None)
    result.update(copy.deepcopy(patch['set']))
    for key,value in patch.get('merge',{}).items():result[key]=apply_patch(result[key],value)
    for key,value in patch.get('lists',{}).items():
        items=apply_patch({r['id']:r for r in result[key]},value['items'])
        result[key]=[items[i] for i in value['order']]
    return result


def open_questions(state):
    return {'checks':{k:{'status':c['status'],'note':c.get('note',''),'evidence_ids':c.get('evidence_ids',[])} for k,c in state['checks'].items() if c['status'] not in ['met','not_applicable']},
        'votes':{a:v for a,v in state.get('votes',{}).get(state.get('current_plan'),{}).items() if v.get('agree') is not True},
        'missing_votes':[a for a in state.get('consensus_members',[]) if a not in state.get('votes',{}).get(state.get('current_plan'),{})],
        'blocker':state.get('blocker')}
