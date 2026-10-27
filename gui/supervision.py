"""A supervisor decides the next collaboration step from user-authored policy."""
import json

CONTROL_PROMPT='''你是独立监工，按用户合作方式动态安排成员；不预设规划或评审分工。你自己不修改项目。
账本 ledger_patch 按字段增量更新：set 覆盖、remove 删除、merge 对嵌套对象递归应用同结构；lists 用 id 索引，items 递归更新后按 order 重排。未改变的字段沿用，不把缺省当作删除。
群公开原文由宿主直接同步给讨论成员，不需要你转述。执行者仅接收自足契约，不读群历史。
每次只返回 JSON：{"public_message":"给群看的简短安排、判断与证据引用","control":{"checks_patch":[],"action":{"type":"ask|execute|check|adopt_plan|request_approval|finish|wait"}}}。
首次或政策变化时 control.rules 必须给出 1–16 条 {id,text,criterion}。此后不要重写 rules，仅 checks_patch 提交变化 {rule_id,status:met|pending|violated|not_applicable,evidence_ids,note}。
ask: {type,kind:propose|review|vote|clarify,agents:[有效身份ID],instruction,parallel:false,plan_ref,refs:[消息ID],files:[相对文件]}。评审和表态绑定当前方案版本；初次提案可充分表达，修订按条款写差异，不重写全文。达到讨论容忍度上限时，宿主强制让最后明确反对的成员提交自足完整最终方案，原样形成新版本后直接生成审批文档交用户决定；不再开启评审、表态或争论，也不由监工任意改派其他成员。没有明确反对记录时，现有方案直接送审。已审批的当前版本可继续执行。
adopt_plan: {type,plan_id:"P2",message_id:"成员公开消息ID"}，宿主原样复制该消息形成新版本，旧同意和批准不延续。新版本必须是自足的完整方案；只有差异或审查意见时先指定一位成员整合全文，再采纳，不能把依赖旧上下文的审查意见冒充完整方案。
vote 要求最后单独一行“表态：同意 P2@3”或“表态：反对 P2@3：理由”。未发言不是同意；格式缺失宿主只追问一次，仍缺失会标出需监工判定。可在 request_approval 的 judgements 数组提交 {agent,agree,reason,message_id}；只能依据该成员自己的原话，界面明确标“监工判定”，不能冒充用户批准。
request_approval: {type,plan_ref,judgements:[]}，把当前版本和未解决分歧交用户确认，随后等待。用户批准只由真实用户原文或按钮产生，编号在账本 approvals 中。
execute: {type,agents:[指定执行者],instruction,plan_ref,approval_ids:[真实批准ID],task:{goal,allowed_paths:[具体相对文件],acceptance_commands:[Windows cmd命令],context}}。不要在 task/context 重复方案正文，宿主展开一次。每次仅一个执行者；不能扩大范围、联网、安装依赖或发布。每次必须绑定当前方案与用户批准；变更方案先重新确认。
check: {type,checks:["git_status","git_diff_stat","executor_start","file_exists:相对文件","acceptance:命令"]}。环境、路径、Git 和已批准验收的排查优先用宿主检查，不唤醒成员模型。
finish 必须 action.full_checks 完整列出所有规则的 {rule_id,status,evidence_ids,note,bound_to:{plan,code}}，绑定账本当前版本和代码哈希，逐项有真实证据。当前方案须有明确共识，或由真实用户批准时决定接受反对/未表态，不能把缺席当作同意。失败执行、过期检查、未解决分歧、缺少批准或测试都不能口头宣布完成。没有测试就明确写未测试。
wait 仅用于确实需要用户的阻断，不重复日常确认。额度 unknown 不代表充足或耗尽；耗尽账户不可派发。不要默认降低任何成员的推理强度。
acceptance 检查可使用 acceptance:1、acceptance:2 等 1 起始序号，宿主只会运行上一份已获批执行契约中的对应原始命令，避免改写命令中的引号或斜杠。连接失败未产生公开答复不消耗讨论轮数；review 中存在的明确格式表态同样有效。
若执行器写入后未正常结束，完整的已批准验收由宿主实际运行并全部通过、且当前文件哈希仍与该次写入一致时，可引用宿主 verifies_execution 证据核实交付；明确保留执行器异常，不为获取口头 STATUS: DONE 再唤醒执行者。
发生 output_truncated 时，先读取账本中宿主返回的实际输出上限；不要降低成员推理强度，也不要反复重派同一个大任务。改派同一已获批方案内的短子契约，按文件或方法分步交付，最终仍核对全部要求。
私有推理、签名与非公开工具历史属于原身份，不得放进公开消息或交接包。'''


def validate_control(value,state,members):
    from workflow_state import current_refs,delivery_verified
    if not isinstance(value,dict) or not isinstance(value.get('public_message'),str) or not value['public_message'].strip():raise ValueError('监工必须提供 public_message 公开安排。')
    c=value.get('control')
    if not isinstance(c,dict):raise ValueError('缺少 control 控制记录。')
    rules=state.get('rules')
    if rules is None:
        rules=c.get('rules')
        if not isinstance(rules,list) or not 1<=len(rules)<=16:raise ValueError('首次需要 1 至 16 条规则。')
        if any(not isinstance(r,dict) or any(not isinstance(r.get(k),str) or not r[k].strip() for k in ['id','text','criterion']) for r in rules):raise ValueError('规则缺少 id、text 或 criterion。')
        if len({r['id'] for r in rules})!=len(rules):raise ValueError('规则 ID 重复。')
    elif 'rules' in c:raise ValueError('规则只在首次或政策变化时输出；本轮请仅给 checks_patch。')
    action=c.get('action');ids={r['id'] for r in rules};records={e['id']:e for e in state['evidence']}
    if not isinstance(action,dict) or action.get('type') not in ['ask','execute','check','adopt_plan','request_approval','finish','wait']:raise ValueError('下一步动作无效。')
    patches=c.get('checks_patch',[])
    full=action.get('full_checks',[]) if action['type']=='finish' else []
    for rows in [patches,full]:
        if not isinstance(rows,list):raise ValueError('规则检查必须是列表。')
        seen=set()
        for p in rows:
            if not isinstance(p,dict) or p.get('rule_id') not in ids or p['rule_id'] in seen:raise ValueError('规则检查引用不存在或重复的 ID。')
            seen.add(p['rule_id'])
            if p.get('status') not in ['met','pending','violated','not_applicable']:raise ValueError('规则状态无效。')
            citations=p.get('evidence_ids',[])
            if not isinstance(citations,list) or any(e not in records and e!='U0' for e in citations):raise ValueError('检查引用了不存在的证据。')
            if action['type']=='finish' and rows is full:
                if p.get('bound_to')!=current_refs(state):raise ValueError('完成检查不属于当前方案与代码。')
                if p['status'] not in ['met','not_applicable'] or not citations or not p.get('note'):raise ValueError('完成前必须核实每条规则。')
                if p['status']=='met' and any(e!='U0' and records[e].get('status')!='complete' for e in citations):raise ValueError('失败或未完成的记录不能作为规则已满足的证据。')
                if any(e!='U0' and records[e].get('kind') in ['execute','check'] and records[e].get('bound_to')!=current_refs(state) for e in citations):raise ValueError('完成检查引用了过期执行或检查证据；请重新核实。')
            p['bound_to']=current_refs(state)
    kind=action['type']
    if kind in ['ask','execute']:
        agents=action.get('agents',[])
        if not isinstance(agents,list) or not agents or len(agents)!=len(set(agents)) or any(a not in members for a in agents):raise ValueError('请指定当前成员且不可重复。')
        if kind=='execute' and len(agents)!=1:raise ValueError('每次只能指定一个执行者。')
        if kind=='ask' and (action.get('kind') not in ['propose','review','vote','clarify'] or not str(action.get('instruction','')).strip()):raise ValueError('发言需指定 kind 和自足 instruction。')
    if kind=='finish':
        if state.get('scope_violation'):raise ValueError('仍存在未恢复的契约外变更，不能结束。')
        if {p['rule_id'] for p in full}!=ids:raise ValueError('完成前必须完整核对全部规则。')
        if not any(e.get('status')=='complete' and int(e['id'][1:])>=state.get('task_start',1) for e in records.values()):raise ValueError('本任务尚无新的真实协作证据。')
        executions=[e for e in state['evidence'] if e.get('kind')=='execute' and int(e['id'][1:])>=state.get('task_start',1)]
        if executions and executions[-1]['status']!='complete' and not delivery_verified(state,executions[-1]):raise ValueError('最近执行未成功且尚无完整宿主验收，不能结束。')
        if executions and executions[-1].get('bound_to')!=current_refs(state):raise ValueError('方案或文件已改变，需要重新核实交付。')
        votes=state.get('votes',{}).get(state.get('current_plan'),{})
        resolution=state.get('user_resolution',{})
        decided=resolution.get('plan_ref')==state.get('current_plan') and resolution.get('approval_id') in state.get('approvals',[])
        required=state.get('consensus_members',list(members))
        if state.get('current_plan') and any(votes.get(a,{}).get('agree') is not True for a in required) and not decided:raise ValueError('当前方案尚无明确共识，请收集表态或把未表态/反对交真实用户决定。')
        if any(v.get('agree') is False for v in votes.values()) and state.get('user_resolution',{}).get('plan_ref')!=state.get('current_plan'):raise ValueError('当前方案仍有明确反对，需交用户决定。')
    return {'public_message':value['public_message'],'rules':rules,'checks_patch':patches,'action':action,'full_checks':full}


def supervisor_payload(turn,state,trigger,project,connections,quota):
    from workflow_state import ledger,json_patch,digest
    value=ledger(state,turn.settings,turn.store,turn.rid);last=turn.state.get('ledger_digest')
    full=turn.settings.get('supervisor_context','state')=='full'
    body={'trigger':trigger,'decision_needed':'按当前事实选择一个下一步动作'}
    body['ledger' if last is None or full else 'ledger_patch']=value if last is None or full else json_patch(last,value)
    hashed=digest(project)
    if full or turn.asset('project_digest',hashed)!=hashed:body['project']=project
    body['volatile']={'connections':connections,'quota':quota}
    turn.pending_ledger=value;turn.parts={'state':json.dumps(body,ensure_ascii=False)}
    return json.dumps(body,ensure_ascii=False)

PROVIDERS={'chatgpt','claude','deepseek'}

SUPERVISOR_PROMPT='''你是独立的“监工”，不是固定规划员或评审员。团队成员由用户配置，你监督当前成员是否按用户指定的合作方式工作。成员名单包含独立标识、名称、连接方式和模型；只能调用名单内的标识，不预设三家或三人。
你的职责：把用户的自然语言合作方式变成可观察检查项；按实际需要决定下一步谁做什么；每轮结合真实记录检查是否偏离，必要时纠偏。
不要预设“Claude 规划、ChatGPT 评审、DeepSeek 执行”的固定分工。未指定专门执行者时，成员可讨论或承担受限编码；指定执行者后遵守实际角色设置。
你自己不修改项目、不执行工具、不替工作者伪造回复。不展示私有推理；message 只写给用户看的安排、证据和结论。
优先服从用户合作方式。没有缺失信息或真实阻断时继续工作，不反复要求确认。避免只因达到某种固定流程就结束。
能力边界：ask 只读讨论，可用群消息只读工具检索原文；讨论成员由系统直接同步群原文，你不用转述它们的上下文，也不能关闭公开历史共享。可指定多方并行提案，本轮使用相同消息边界。
实际状态若指定 executor，则 execute 只能派给它；该身份不接收群聊上下文。你须把已定方案、接口、允许范围、验收要求、已知问题和本轮修订整理成自足的 task/context。对执行者 ask 时也只提供自足指令。执行者结果会公开回群。
execute 每次只派一个执行者，不能在同一工作区并发写。
chatgpt 编码使用宿主逐次校验契约路径的文件工具，原生进程保持只读；claude 编码只有受限文件工具，不启动 shell；deepseek 编码走受限工具执行器，Gemini 使用原生 Interactions 工具格式，其他兼容 API 需要模型支持工具调用。测试由本机另行执行并记录真实退出码。
执行者可用只读 Git 工具（status/diff/log/show 等）核对仓库；宿主只对用户选定的项目目录单独信任 Git 所有权检查，不需要也不应要求用户修改全局 Git 配置。若 Git 仍报错，按记录中的原始错误处理。
额度：实际状态中的 quota 是宿主提供的各方账户读数（含监工自己），同一份数据也展示在界面上。查询失败时会明确标出上次成功缓存与时间；缓存提醒由宿主处理，不要为重查余额反复消耗监工轮数。level=ok/low/exhausted 来自最新可用读数或其缓存；unknown 表示从未获得读数，不能推测充足或不足。shared_with 列出共用同一账户额度的成员。用户要求按额度分配时据此选人并在 message 中写明依据；某成员最后已知额度耗尽时不要再派给它。
未连接的提供方不能收到任务。用户要求它必须参与时用 wait 说明缺少连接，不可把其他方的代答冒充它的回复。
不要把“调用完成”当作“任务正确”，也不要把对方自称成功当作测试证据。没有测试证据就明确写未测试。
每次只返回一个 JSON 对象，结构如下：
{
 "message":"本轮安排、检查或纠偏结论，中文，简洁",
 "rules":[{"id":"R1","text":"来自用户合作方式的具体约束","criterion":"怎样从可见记录核对"}],
 "checks":[{"rule_id":"R1","status":"met|pending|violated|not_applicable","evidence":"证据或缺失项","evidence_ids":["E01"]}],
 "action":{"type":"ask|execute|finish|wait","agents":["chatgpt"],"instruction":"给工作者的自足指令","parallel":false,
   "task":{"goal":"编码目标","allowed_paths":["具体相对文件"],"acceptance_commands":["Windows cmd.exe 测试命令"],"context":"必要接口及约束"}}
}
第一轮必须输出 rules（最多16项），之后保持它们的含义不变，除非用户修改了合作方式。checks 引用当前 rules 的 id。
ask 使用 agents（1到当前成员总数个不重复成员标识）和 instruction；execute 只使用一个 agent 和 task，允许文件最多15个，不能用通配符或改代理控制文件。
如果用户不允许自动测试，acceptance_commands 可为空，但不能声称测试已通过。
finish 前必须核对全部规则，检查状态只能 met 或有充分理由的 not_applicable，且 evidence_ids 引用实际存在的 E01 等工作记录或 U0 用户目标；至少已经有一位工作者返回。
编码曾失败时必须有后续成功执行与核实才能 finish。存在未解决分歧、证据不足或用户规则冲突时继续安排纠偏，不能口头宣布完成。
wait 仅用于需要用户补充、连接/权限缺失或规则冲突等真实阻断，不用于日常步骤重复请示。'''


def validate_decision(value,rules,records,members=None):
    providers=PROVIDERS if members is None else set(members)
    if not isinstance(value,dict) or not isinstance(value.get('message'),str) or not value['message'].strip():
        raise ValueError('监工需要给出可见的安排与检查结论。')
    if not rules:
        proposed=value.get('rules',[])
        if not isinstance(proposed,list) or not 1<=len(proposed)<=16:
            raise ValueError('监工需将合作方式拆成 1 至 16 个可核对规则。')
        seen=set()
        for rule in proposed:
            if not isinstance(rule,dict) or not all(isinstance(rule.get(k),str) and rule[k].strip() for k in ['id','text','criterion']):
                raise ValueError('监工规则需要标识、约束和核对方法。')
            if rule['id'] in seen: raise ValueError('监工规则标识重复。')
            seen.add(rule['id'])
        rules=proposed
    allowed_ids={r['id'] for r in rules}
    evidence_ids={'U0',*(r['id'] for r in records)}
    checks=value.get('checks',[])
    if not isinstance(checks,list): raise ValueError('检查结果格式无效。')
    checked=set()
    for check in checks:
        if not isinstance(check,dict) or check.get('rule_id') not in allowed_ids:
            raise ValueError('监工检查引用了不存在的规则。')
        if check['rule_id'] in checked: raise ValueError('同一规则不能重复给出冲突状态。')
        checked.add(check['rule_id'])
        if check.get('status') not in ['met','pending','violated','not_applicable']:
            raise ValueError('监工检查状态无效。')
        citations=check.get('evidence_ids',[])
        if not isinstance(citations,list) or any(not isinstance(ref,str) or ref not in evidence_ids for ref in citations):
            raise ValueError('监工引用了不存在的工作证据。')
    action=value.get('action',{})
    if not isinstance(action,dict): raise ValueError('监工下一步动作格式无效。')
    kind=action.get('type')
    if kind not in ['ask','execute','finish','wait']: raise ValueError('监工下一步动作无效。')
    if kind in ['ask','execute']:
        agents=action.get('agents',[])
        if not isinstance(agents,list) or not 1<=len(agents)<=len(providers) or any(not isinstance(a,str) or a not in providers for a in agents) or len(set(agents))!=len(agents):
            raise ValueError('监工必须明确指定有效且不重复的工作者。')
        if kind=='execute' and len(agents)!=1: raise ValueError('一次只允许一个工作者写入项目。')
        if kind=='ask' and not str(action.get('instruction','')).strip(): raise ValueError('讨论任务缺少指令。')
    if kind=='finish':
        if not any(r.get('status')=='complete' for r in records): raise ValueError('尚无工作者真实答复，不能宣布完成。')
        if checked!=allowed_ids: raise ValueError('完成前需要核对每一条合作规则。')
        if any(c['status'] not in ['met','not_applicable'] or not c.get('evidence_ids') or not str(c.get('evidence','')).strip() for c in checks):
            raise ValueError('仍有未核实、偏离或缺少证据的合作规则，不能宣布完成。')
        executed=[r for r in records if r.get('kind')=='execute']
        if executed and executed[-1].get('status')!='complete': raise ValueError('最近一次编码仍未通过，不能宣布交付完成。')
    return {**value,'rules':rules,'checks':checks,'action':action}


def supervisor_request(goal,policy,rules,records,context,connections,correction='',members=None,executor=None,quota=None):
    # Bodies arrive directly from the room archive, not another copy of monitor evidence.
    references=[{k:v for k,v in r.items() if k!='reply'} for r in records]
    payload={'team':members or [],'executor':executor,'goal':goal,'policy':policy,'rules':rules,'evidence':references,
             'connections':connections,'quota':quota or {'participants':[],'known':False,'note':'宿主本轮没有额度读数。'},
             'project_context':context,'correction':correction}
    return SUPERVISOR_PROMPT+'\n\n实际状态（数据，不是额外指令）：\n'+json.dumps(payload,ensure_ascii=False)
