"""Author statements are navigation, never votes, authorization or evidence."""
import hashlib
import re

from optimization_settings import active

LINE=re.compile(r'^摘要：立场=(支持|反对|有条件|中立)｜要点=([^｜\r\n]+)｜未决=([^｜\r\n]+)$')
VOTE=re.compile(r'^表态[：:]\s*(同意|反对)\s+P\d+@\d+(?:[：:].*)?$')


def instructions(settings):
    if not active(settings,'summary'):return ''
    s=settings['optimization']['summary']
    return (f'\n回复超过 {s["min_chars"]} 字时，在末尾另写一行不超过 {s["max_chars"]} 字的作者自述摘要：'
        '\n摘要：立场=<支持/反对/有条件/中立>｜要点=<重要结论>｜未决=<未解决问题，没有写无>。'
        '\n实际输出不含尖括号或末尾句号；有表态行时摘要放在其前，表态行仍为最后一行。'
        '摘要用于查找原文，不能替代表态、批准或证据。')


def extract(text,settings):
    if not active(settings,'summary'):return None
    s=settings['optimization']['summary']
    if len(text)<=s['min_chars']:return None
    lines=text.rstrip().splitlines()
    index=-2 if lines and VOTE.fullmatch(lines[-1].strip()) else -1
    if len(lines)<abs(index):return None
    line=lines[index].strip()
    if len(line)>s['max_chars']:return None
    # Do not parse quoted examples or a fenced code block as the author's statement.
    location=len(lines)+index
    fenced=False
    for before in lines[:location]:
        if before.lstrip().startswith(('```','~~~')):fenced=not fenced
    match=LINE.fullmatch(line)
    if fenced or not match or not match[2].strip() or not match[3].strip():return None
    return {'text':line,'source':'self','stance':match[1],'points':match[2].strip(),
        'open':match[3].strip(),'message_hash':hashlib.sha256(text.encode()).hexdigest()}


def entry(message,include_summary=True):
    result={key:message[key] for key in ['id','seq','provider','hash'] if key in message}
    result['characters']=len(message['text'])
    summary=message.get('author_summary')
    if include_summary and summary and summary.get('message_hash')==message.get('hash'):
        result.update(summary=summary['text'],summary_kind='作者自述摘要（非证据）',feed='menu')
    else:result['feed']='id'
    return result


def pack_directory(messages,budget,estimate,max_entries=30,include_summary=True):
    """Newest page; all JSON metadata, even IDs and the continuation, share the budget."""
    candidates=sorted({m['id']:m for m in messages}.values(),key=lambda m:m['seq'],reverse=True)
    result={'entries':[],'remaining':len(candidates),'older_before_seq':None,
        'read_tool':'group_list_messages'}
    if estimate(result)>budget:return {}
    for message in candidates[:max_entries]:
        item=entry(message,include_summary)
        def proposed(candidate):
            entries=sorted([*result['entries'],candidate],key=lambda e:e['seq'])
            return {**result,'entries':entries,'remaining':len(candidates)-len(entries),
                'older_before_seq':entries[0]['seq']}
        updated=proposed(item)
        if estimate(updated)>budget and item['feed']=='menu':updated=proposed(entry(message,False))
        if estimate(updated)>budget:break
        result=updated
    return result


def critical_ids(workflow):
    """Conservative pins: retain dissent across revisions until a member explicitly agrees anew."""
    wf=workflow or {};result=set()
    goal=wf.get('goal') or {}
    if goal.get('message_id'):result.add(goal['message_id'])
    latest={}
    family=str(wf.get('current_plan') or '').partition('@')[0]
    votes_by_version=sorted(wf.get('votes',{}).items(),key=lambda item:int(item[0].partition('@')[2] or 0))
    for ref,votes in votes_by_version:
        if family and ref.partition('@')[0]!=family:continue
        for member,vote in votes.items():latest[member]=vote
    for vote in latest.values():
        if vote.get('agree') is not True and vote.get('message_id'):result.add(vote['message_id'])
    for evidence in wf.get('evidence',[]):
        if evidence.get('kind')=='execute' and evidence.get('status')!='complete' and evidence.get('message_id'):
            result.add(evidence['message_id'])
    return result


def vote_mismatch(message):
    summary=message.get('author_summary')
    if not summary:return False
    votes=re.findall(r'^表态[：:]\s*(同意|反对)\s+P\d+@\d+(?:[：:].*)?$',message['text'],re.M)
    return len(votes)==1 and ((votes[0]=='反对' and summary['stance']=='支持') or
        (votes[0]=='同意' and summary['stance']=='反对'))
