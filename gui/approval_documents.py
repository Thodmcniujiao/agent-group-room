"""Host-authored, immutable approval packets over exact versioned member originals."""
import hashlib
import html
import json
from pathlib import Path
import uuid

from markdown_it import MarkdownIt
from store import now,WorkflowConflict


def create(store,rid,state,settings,judgements=()):
    ref=state['awaiting_approval'];plan=store.plan(rid,ref);room=store.room(rid)
    names={a['id']:a['name'] for a in settings['agents']}
    names.update({m['id']:m['name'] for m in settings['members']})
    origin=store.message_by_id(plan['source_message_id'],rid)
    votes=state.get('votes',{}).get(ref,{})
    participants=[m['id'] for m in settings['members'] if m['id']!=settings.get('executor')]
    positions=[{'agent':names.get(a,a),'agree':votes.get(a,{}).get('agree'),'reason':votes.get(a,{}).get('reason',''),
        'source':votes.get(a,{}).get('source','no_vote'),'message_id':votes.get(a,{}).get('message_id')} for a in participants]
    title=next((s.strip().lstrip('#').strip() for s in plan['text'].splitlines() if s.strip()),'方案 '+ref)[:140]
    payload={'title':title,'room_title':room['title'],'project':room['project'],'plan_ref':ref,'plan_sha256':plan['sha256'],
        'source_message_id':plan['source_message_id'],'author':names.get(origin['provider'],origin['provider']),
        'plan_created':plan['created'],'policy_revision':state['policy_revision'],'policy_text':state.get('policy_text',settings['cooperation_policy']),
        'positions':positions,'judgements':list(judgements),'original':plan['text'],'created':now()}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    document={'id':uuid.uuid4().hex,'room':rid,'plan_ref':ref,'plan_sha256':plan['sha256'],
        'document_sha256':hashlib.sha256(encoded.encode()).hexdigest(),'data':payload,'created':payload['created']}
    store.save_approval_document(document)
    return document


def ensure_pending(store,rid,settings):
    for _ in range(3):
        state,revision=store.load_workflow(rid)
        if not state or not state.get('awaiting_approval'):return None
        if state.get('current_plan')!=state['awaiting_approval']:raise ValueError('待审批方案已过期。')
        identifier=state.get('approval_document_id')
        if identifier:
            document=store.approval_document(rid,identifier)
            if document['plan_ref']==state['awaiting_approval'] and document['data']['policy_revision']==state['policy_revision']:return document
        document=create(store,rid,state,settings)
        state['approval_document_id']=document['id']
        try:store.save_workflow(rid,state,revision);return document
        except WorkflowConflict:continue
    raise ValueError('方案正在更新，请重新打开审批文档。')


def info(store,rid,document):
    state,_=store.load_workflow(rid);state=state or {}
    approved=any(a['kind']=='user' and (a.get('source')=='button' and any(x['id']==a.get('action_id') and x['payload'].get('document_id')==document['id'] for x in store.host_actions(rid))
        or a.get('source')!='button' and f"{a['plan_id']}@{a['version']}"==document['plan_ref']) for a in store.approvals(rid))
    pending=state.get('awaiting_approval')==document['plan_ref'] and state.get('approval_document_id')==document['id']
    return {'id':document['id'],'plan_ref':document['plan_ref'],'title':document['data']['title'],'document_sha256':document['document_sha256'],
        'plan_sha256':document['plan_sha256'],'created':document['created'],'author':document['data']['author'],
        'status':'pending' if pending else 'approved' if approved else 'superseded','can_approve':pending,
        'url':f"/api/rooms/{rid}/approval-documents/{document['id']}/content"}


def action(document):
    return {'id':'review_plan','label':'打开审批文档','plan_ref':document['plan_ref'],'document_id':document['id'],'title':document['data']['title']}


def present_message(store,rid,message):
    if message.get('phase')=='确认方案':
        legacy=next((a for a in message.get('actions',[]) if a.get('id')=='approve'),None)
        document=store.approval_document_for_plan(rid,legacy['plan_ref']) if legacy else None
        if document:return {**message,'text':'方案审批文档已生成。请打开文档核对完整方案后批准。','actions':[action(document)]}
    return message


def markdown(document):
    p=document['data']
    text='# 方案审批书\n\n## '+p['title']+'\n\n'
    for label,value in [('协作室',p['room_title']),('项目目录',p['project']),('方案版本',p['plan_ref']),('原文作者',p['author']),('文档生成时间',p['created']),('方案 SHA-256',p['plan_sha256']),('审批文档 SHA-256',document['document_sha256'])]:text+=f'- {label}：{value}\n'
    text+='\n## 审批说明\n\n本文件由宿主程序生成，方案正文原样引用版本库，不由模型重新转述。批准仅适用于下列版本；不表示代码已经执行或验收通过。\n'
    text+='\n## 成员表态\n\n'
    for row in p['positions']:
        label='同意' if row['agree'] is True else '反对' if row['agree'] is False else '未表态'
        text+='- '+row['agent']+'：'+label+('（监工判定）' if row['source']=='supervisor' else '')+('；'+row['reason'] if row['reason'] else '')+'\n'
    if any(row['agree'] is not True for row in p['positions']):text+='\n**存在反对或未表态。批准表示你决定采用此版本，成员原有意见仍保留。**\n'
    if p['judgements']:text+='\n'+'\n'.join(p['judgements'])+'\n'
    text+='\n## 方案正文（原文）\n\n'+p['original']+'\n\n## 本次合作规则\n\n'+p['policy_text']+'\n'
    return text


def render(document):
    md=MarkdownIt('commonmark',{'html':False}).enable('table')
    md.add_render_rule('image',lambda renderer,tokens,index,*args:'<span class="image-note">[图片：'+html.escape(tokens[index].content)+']</span>')
    p=document['data'];body=md.render(markdown(document))
    css=Path(__file__).with_name('web').joinpath('approval-document.css').read_text(encoding='utf-8')
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(p['title'])+' · '+p['plan_ref']+'</title><style>'+css+'</style></head><body><main class="approval-paper">'+body+'<footer>Agent Group Room · 宿主生成的版本化审批文档</footer></main></body></html>'
