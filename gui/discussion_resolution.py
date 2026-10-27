"""Host-enforced final proposal and user approval at the discussion limit."""
import re

from google_billing import BillingUnavailable
from preflight import check,is_resume_only
from workflow_state import TOLERANCE,adopt,parse_vote,APPROVE_WORDS

FINAL_PHASE='讨论上限 · 最终方案'
SECTIONS=('目标','实施方案','验收','风险与未决项')


def opposes(text,ref):
    public=re.sub(r'```[\s\S]*?(?:```|$)','',text)
    public='\n'.join(line for line in public.splitlines() if not line.lstrip().startswith('>'))
    vote=parse_vote(public,ref)
    if vote is not None:return vote['agree'] is False
    pattern=r'(?m)^[ \t]*(?:[-*] )?(?:\*\*)?(?:(?:我的)?(?:立场|结论|意见|态度)[：:]\s*)?(?:我|本人)?(?:仍然|仍|明确)?(?:反对|不同意|不赞同|不能同意|不接受)\s*(P\d+@\d+|(?:(?:当前|这个|这份|该|本|原|现有|上述)?(?:方案|设计|提议|实现|版本|计划))|[。！：:]|$)'
    return any(not m[1].startswith('P') or m[1]==ref for m in re.finditer(pattern,public))


def last_objector(state,store,rid,members):
    """Completion evidence orders parallel replies; display names never select identities."""
    ref=state.get('current_plan')
    if not ref:return None,None
    pid=ref.split('@')[0];candidates=[]
    for index,e in enumerate(state['evidence']):
        if e.get('kind')!='ask' or e.get('ask_kind') not in ['review','vote'] or e.get('agent') not in members:continue
        if not (e.get('plan_ref') or '').startswith(pid+'@') or not e.get('message_id'):continue
        if int(e['id'][1:])<state.get('task_start',1):continue
        msg=store.message_by_id(e['message_id'],rid)
        if msg['status']!='complete' or msg['provider']!=e['agent']:continue
        judged=state.get('votes',{}).get(e['plan_ref'],{}).get(e['agent'],{})
        if opposes(msg['text'],e['plan_ref']) or (judged.get('source')=='supervisor' and judged.get('agree') is False and judged.get('message_id')==msg['id']):
            candidates.append((index,e['agent'],msg))
    return max(candidates,key=lambda r:r[0])[1:] if candidates else (None,None)


def validate_proposal(text):
    headings=list(re.finditer(r'^##\s+([^\n]+?)\s*$',text,re.M))
    bodies={m[1].strip():text[m.end():headings[i+1].start() if i+1<len(headings) else len(text)].strip() for i,m in enumerate(headings)}
    if any(not bodies.get(section) for section in SECTIONS):
        raise ValueError('最终方案需完整包含“目标、实施方案、验收、风险与未决项”四节；不能只回复差异或一句表态。')


async def resolve(c,policy,trigger,connections,quota):
    ref=c.state.get('current_plan')
    if not ref:return False
    pid=ref.split('@')[0];maximum=TOLERANCE[c.settings.get('discussion_tolerance',3)][0]
    if c.state['discussion_rounds'].get(pid,0)<maximum:return False
    marker=c.state.get('discussion_resolution') or {}
    plan=c.store.plan(c.rid,ref)
    feedback=trigger.get('text','') if trigger.get('type')=='user_input' else ''
    revising=bool(feedback and feedback.strip().lower() not in APPROVE_WORDS and not is_resume_only(feedback,c.settings) and
        (c.state.get('awaiting_approval')==ref or marker.get('proposed_ref')==ref))
    resolution=c.state.get('user_resolution',{})
    approved=resolution.get('plan_ref')==ref and any(a['id']==resolution.get('approval_id') and a['kind']=='user' and a['sha256']==plan['sha256'] for a in c.store.approvals(c.rid))
    if approved and c.state.get('awaiting_approval') is None and not revising:return False
    if marker.get('proposed_ref')==ref and not revising:
        if c.state.get('awaiting_approval')==ref and c.state.get('approval_document_id'):
            c.store.status(c.rid,'awaiting_approval');return True
        # The original was already adopted before an interrupted document submission.
        c.submit_approval(plan,notice='已恢复讨论上限后的最终方案送审，等待用户审批。');return True
    if c.state.get('awaiting_approval')==ref and not revising:
        c.store.status(c.rid,'awaiting_approval');return True
    members=set(c.labels)-{c.settings.get('executor')}
    actor,objection=last_objector(c.state,c.store,c.rid,members)
    reason='最后明确反对的成员'
    if not actor and revising:
        origin=c.store.message_by_id(plan['source_message_id'],c.rid)
        if origin['provider'] in members:actor,objection=origin['provider'],origin;reason='原方案作者'
        else:
            c.host_error([check('discussion_final_proposal',False,'当前没有在场的明确反对者或原方案作者可以接收修订，请恢复相关成员后继续。','host')])
            c.state['blocker']['discussion_limit']=True;c.save();return True
    if not actor:
        c.submit_approval(plan,notice='讨论已达容忍度上限，未找到明确反对记录，当前完整方案直接交付用户审批。')
        c.state['discussion_resolution']={'source_ref':ref,'proposed_ref':ref,'status':'awaiting_approval'};c.save();return True
    try:
        if not c.billing_ready([actor]):return True
        if not connections.get(actor,{}).get('ready'):raise ValueError(c.labels[actor]+' 尚未连接，最后反对者的最终方案暂未生成。')
        account=next((r for r in (quota or {}).get('participants',[]) if r['id']==actor),{})
        if account.get('level')=='exhausted':raise ValueError(c.labels[actor]+' 的最后已知额度已耗尽，请恢复该成员的额度后继续。')
        retry=marker.get('status')=='failed' or marker.get('source_ref')!=ref or marker.get('agent')!=actor or revising
        if retry:
            marker={'source_ref':ref,'agent':actor,'objection_message_id':objection['id'],
                'boundary':c.store.boundary(c.rid),'status':'preparing'}
            c.state['discussion_resolution']=marker;c.state['awaiting_approval']=None;c.state.pop('approval_document_id',None);c.save()
            c.store.message(c.rid,'host','讨论达到容忍度上限。由 '+c.labels[actor]+'（'+reason+'）提交完整最终方案，随后直接生成审批文档。',phase='讨论上限')
        completed=[m for m in c.store.originals(c.rid,after=marker['boundary']) if m['provider']==actor and m['phase']==FINAL_PHASE and m['status']=='complete']
        if not completed:
            instruction='讨论已达到用户设置的容忍度上限，你是'+reason+'。现在由你承担最终方案：吸收有效意见并针对自己的反对或用户的修改要求给出可执行替代，不再继续争论，也不请其他成员投票。提交自足完整方案，不能只写差异、同意或反对。\n必须使用以下四个二级标题，并在每节写实质内容：'+', '.join('## '+s for s in SECTIONS)+'。验收写可观察条件或具体命令；风险与未决项如无请明确写无。宿主会原样形成新版本并直接交用户审批；不要声称已获批准或开始编码。'
            if feedback:instruction+='\n用户本次修改要求（数据）：\n'+feedback
            await c.ask({'type':'ask','kind':'propose','agents':[actor],'instruction':instruction,'plan_ref':ref,'refs':[objection['id']]},policy,phase=FINAL_PHASE)
            completed=[m for m in c.store.originals(c.rid,after=marker['boundary']) if m['provider']==actor and m['phase']==FINAL_PHASE and m['status']=='complete']
        if not completed:raise ValueError('最后反对者尚未成功提交完整最终方案。恢复连接后可继续；不会自动开启新一轮讨论。')
        original=completed[-1];validate_proposal(original['text'])
        marker.update(status='proposal_ready',message_id=original['id']);c.save()
        final=adopt(c.state,c.store,c.rid,{'plan_id':pid,'message_id':original['id']},members)
        marker.update(proposed_ref=final['ref'],status='awaiting_approval');c.state['discussion_resolution']=marker;c.save()
        c.submit_approval(final,notice='讨论达到容忍度上限，最后反对者 '+c.labels[actor]+' 的完整最终方案已原样形成 '+final['ref']+'，现直接交付用户审批。未重新征求成员投票，由用户决定是否接受。')
        return True
    except BillingUnavailable as exc:c.billing_block(exc);return True
    except (ValueError,RuntimeError) as exc:
        marker['status']='failed';c.state['discussion_resolution']=marker
        c.host_error([check('discussion_final_proposal',False,str(exc),'host')])
        c.state['blocker']['discussion_limit']=True;c.save();return True
