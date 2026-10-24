# Agent Group Room token 优化实施方案

Oct 22, 2026 · @Thodmc

2026-10-22 实施补充：成员摘要与工具输出优化以 [成员摘要与上下文优化路线图](成员摘要与上下文优化路线图.md) 为当前执行规格。历史草稿不表示外部组件已安装或效果已验收。

本方案合并 Claude 的优化提案与 ChatGPT 的修订意见，分 A–D 四个阶段共 15 项改动，给出到函数级的伪代码。先做 A（计量、持久状态、宿主预检）：它不改变任何角色看到的内容，却能拦住 New folder 中受阻阶段的 120 万输入 token 里的大部分空转。

## 基线与不变的边界

采用 ChatGPT 更正后的口径（Codex 按会话累计 total 取增量，已由 Claude 用同一数据库复算一致）：New folder 协作室共输入 1,975,318 token，受阻阶段占 1,205,819（61%）。

| 身份 | 输入 token | 占比 |
| --- | --- | --- |
| 监工（GPT + Gemini） | 1,123,542 | 56.9% |
| ChatGPT 成员 | 685,011 | 34.7% |
| Claude 成员 | 111,956 | 5.7% |
| Gemini 成员 | 54,809 | 2.8% |
| DeepSeek 执行者 | 约 0（启动失败） | — |

Gemini 的缓存明细未进入统计，记为未知；API 工具循环的中间请求可能仍有遗漏。本方案不承诺固定节省量，只承诺按同角色、同适配器、同任务对比。

以下边界任何改动都不得突破：

- 讨论成员评审、表态所需的当前方案和相关未决异议保留原文；较早一般发言可以按房间开关使用作者摘要与原文指针。摘要不能替代表态、批准或证据，也不生成监工代述来冒充成员原话。
- 指定执行者不接收群聊原文，也没有群消息读取工具；方案引用由宿主展开为契约正文。
- 私有推理、原生签名和非公开工具历史只留在所属身份的会话内。
- 用户批准只能来自用户原文，执行成功只能来自真实证据；模型摘要不能创造这两者。
- 未发言不等于同意；影响结论的方案修改会使既有同意失效。
- 不默认降低任何成员的推理强度。

## 改动总览

15 项改动按依赖排列；同一阶段内无依赖的项可以并行开发。

| 编号 | 改动 | 主要文件 | 依赖 | 预期效果 |
| --- | --- | --- | --- | --- |
| A1 | 用量归一化记录 | provider\_sessions、api\_protocol、store | — | 每次调用的真实用量可按路径统计 |
| A2 | 持久工作流状态 | store、orchestrator | — | 重新运行不再从空记录开始，批准和证据编号连续 |
| A3 | 宿主预检与阻断去重 | 新增 preflight.py、orchestrator、workflows | A2 | 同一环境故障不再唤醒模型；修复后直接续接原契约 |
| B1 | 监工输出协议（公开发言 / 控制记录） | supervision、orchestrator、web/app.js | A2 | 监工输出约减半；控制 JSON 不再进入成员上下文 |
| B2 | 方案库与批准记录 | store、orchestrator | A2 | 方案正文只存一份，契约和表态绑定版本 |
| B3 | 执行契约只组装一次 | orchestrator | B2 | 执行请求内不再重复契约正文 |
| B4 | 发言按任务类型组包 | orchestrator、supervision | B2 | 表态轮不再附带项目快照 |
| B5 | 监工“宿主检查”动作 | supervision、orchestrator、git\_access | A3 | 排查类问题不再安排模型去跑命令 |
| B6 | 讨论按差异修订、明确表态 | supervision、orchestrator | B2 | 成员输出缩短，共识绑定版本 |
| C1 | 监工控制上下文模式 | context、orchestrator、room\_settings | A2、B1 | 监工单轮输入有上界，不随轮数线性增长 |
| C2 | 投递覆盖范围与缺口 | context、store | — | 既不重复投递，也不静默跳过未加载原文 |
| C3 | 稳定指令只发一次 | provider\_sessions、supervision | C1 | 监工说明不再每轮追加 |
| D1 | 交接包与有界会话 | context、provider\_sessions | A2、C1、C2 | 换监工或轮换时不从第 0 条重放 |
| D2 | 估算校准 | context、budget\_control | A1 | 中文不再被高估导致误停 |
| D3 | 流式事件合并 | store | — | 数据库写入量下降（不算 token 收益） |

## 阶段 A：计量、持久状态、宿主预检

A 阶段不改变任何角色看到的内容，只让宿主记住事实、测准用量，并由程序处理确定的环境故障。

### A1 用量归一化记录

每次服务商请求记一行；Codex 的 total 是会话累计，必须取增量。缺失字段记为未知，不填 0。

```python
# store.py
CREATE TABLE usage_records(id INTEGER PRIMARY KEY, room, identity, role, session_key, branch,
    adapter, model, request_ref, input, cached_read, cache_write, output, reasoning,
    status,            # complete | failed | interrupted
    composition,       # JSON: 宿主组装的各部分估算，见下
    created)

# usage.py
def normalize(adapter, raw, previous_total):
    if adapter == 'codex':                       # raw.total 按 thread 累计
        total = raw['total']
        base = previous_total or ZERO
        if total['inputTokens'] < base['inputTokens']:   # 新 thread
            base = ZERO
        return delta(total, base), total             # 记录增量； total 存入 native.usage_total
    if adapter == 'claude':
        return {'input': raw.input_tokens + raw.cache_creation_input_tokens + raw.cache_read_input_tokens,
                'cached_read': raw.cache_read_input_tokens, 'cache_write': raw.cache_creation_input_tokens,
                'output': raw.output_tokens, 'reasoning': raw.thinking_tokens or None}, None
    if adapter in ('gemini', 'openai_compatible'):   # 每个 HTTP 请求一条，工具循环逐次记
        return {'input': raw.prompt_tokens, 'cached_read': raw.cached or None,
                'output': raw.completion_tokens, 'reasoning': raw.reasoning or None}, None

# provider_sessions.py 各适配器在拿到用量处（包括异常分支）调用
record_usage(turn, adapter, raw, status, composition=turn.composition)
```

composition 由 ContextTurn.compose 填写，分项为：instructions、originals、state（监工账本）、project（规则与快照）、contract、tools。网络正文之外的历史与基础开销以服务商报告为准，差额标为“未归因”。

### A2 持久工作流状态

把现在只存在 supervised() 局部变量里的 records、rules 和待执行动作落盘，每次运行从上次状态续接。

```python
# store.py
CREATE TABLE workflow_state(room PRIMARY KEY, revision INTEGER, data TEXT, updated)
def load_workflow(rid) -> dict | None
def save_workflow(rid, data, expect_revision)   # 乐观锁；revision 不符则重读后重试

# 状态结构（只存当前值与来源，不存聊天正文）
state = {
  'goal': {'text', 'message_id'},
  'policy_revision': 3,
  'rules': {'policy_revision': 3, 'items': [{'id','text','criterion'}]},
  'checks': {'R1': {'status', 'evidence_ids', 'note', 'bound_to': {'plan': 'P2@2', 'code': '<git tree hash>'}}},
  'evidence': [{'id': 'E07', 'kind': 'ask|execute|check|user', 'agent', 'message_id', 'status', 'bound_to'}],
  'next_evidence': 8,
  'plans': {...}, 'approvals': [...],          # 见 B2
  'pending_action': None | {'type': 'execute', 'task', 'plan_ref', 'contract_hash', 'approval_ids'},
  'blocker': None | {...},                      # 见 A3
}

# orchestrator.py：supervised()
state = store.load_workflow(rid) or new_state(goal, policy)
if policy.revision != state.rules.policy_revision:
    state.rules = None                          # 政策变化：监工重新生成规则
    mark_all_checks(state, 'stale')
records = state.evidence                        # 不再从 [] 开始
new_id = lambda: f"E{state['next_evidence']:02d}"   # 跨运行单调递增
# 每步之后：state = apply_step(state, ...); store.save_workflow(rid, state, rev)

def refresh_bindings(state, root):
    code = git_tree_hash(root)                  # git_access.run_host('write-tree') 或工作区哈希
    for c in state.checks.values():
        if c.bound_to.plan != current_plan_ref(state) or c.bound_to.code != code:
            c.status = 'stale'                  # 依赖变化：旧结论待复核
```

finish 的校验改为：每条规则 status 为 met 或 not\_applicable，且 bound\_to 等于当前方案版本与代码哈希。

### A3 宿主预检与阻断去重

派执行前和用户报告修复时，由程序在执行者的同一环境里检查；同一故障未变化就不调用任何模型。

```python
# preflight.py（新增）
async def preflight(root, settings) -> list[Check]:
    ex = roster.member(settings, settings['executor'])
    checks = [
      await git_check(root, env='host'),                     # git_access.run_host rev-parse == root
      await git_check(root, env=tool_environment(root)),     # 执行者 Bash 的同一环境
      await executor_start_check(root, ex),                  # deepseek_session 启动 + ping，20 秒超时
      quota_check(ex),                                       # level != 'exhausted'
      await recovery_check(root, ex),                        # 无待核实写入记录
    ]
    return checks                                            # Check(name, ok, detail, env)

def fingerprint(failed):
    norm = [(c.name, re.sub(r'\d+|[A-Za-z]:\\\S+', '#', c.detail)[:300], c.env.version) for c in failed]
    return sha256(json.dumps(norm))

# orchestrator.py：监工决定 execute 之后、真正执行之前
failed = [c for c in await preflight(root, settings) if not c.ok]
if failed:
    state.blocker = {'checks': failed, 'fingerprint': fingerprint(failed), 'since': now()}
    state.pending_action = action                  # 保存已获准动作，不丢弃
    store.message(rid, 'system', render(failed), phase='环境阻断', status='error')
    save; store.status(rid, 'blocked'); return     # 不调用执行者，也不回到监工

# workflows.py：协作室处于 blocked 时收到用户消息，或点击新按钮“复查并继续”
async def on_blocked_input(rid, text, settings):
    state = store.load_workflow(rid)
    failed = [c for c in await preflight(root, settings) if not c.ok]
    if failed and fingerprint(failed) == state.blocker.fingerprint:
        store.message(rid, 'system', '复查仍失败（未调用模型）：' + render(failed)); return
    if not failed and is_resume_only(text) and action_still_valid(state):
        state.blocker = None; save
        return await execute_supervised(rid, state.pending_action)   # 续接原契约，不重开讨论
    change = {'trigger': 'blocker_changed' if failed else 'user_input',
              'checks': failed, 'user_text': text}
    return await supervised(rid, ..., trigger=change)    # 新故障或新指令才交监工

def is_resume_only(text):    # 按钮触发，或短消息且只含“已修复/继续/continue”
    return text == RESUME_BUTTON or (len(text) <= 20 and RESUME_WORDS.fullmatch(text.strip()))

def action_still_valid(state):
    a = state.pending_action
    return (a and a.plan_ref == current_plan_ref(state)
            and approvals_cover(state, a.plan_ref) and state.policy_revision == a.policy_revision
            and settings.executor == a.executor and a.allowed_paths == task_paths(a))
```

用户消息带新要求时（不是单纯“继续”），复查结果和用户原文一起交监工，由监工决定；程序不替监工解读语义。

**宿主环境错误消息与“复查并继续”按钮（已定）。** 新增消息类型 host\_error，红色显示，只由宿主程序写入（预检失败、执行器启动失败、配置错误），不经过监工转述；“复查并继续”按钮显示在该消息下方。例如 10-22 的 `max_output_tokens must be between 1 and 16384` 属于宿主配置错误，应由 executor\_start 预检拦下并以红色消息展示，而不是经监工判断后以“监工暂停”出现。

```python
# store.py：宿主错误消息
store.message(rid, 'host', render(failed), phase='宿主环境错误', status='host_error',
              actions=[{'id': 'recheck', 'label': '复查并继续'}])
# web/app.js：status === 'host_error' → 红色样式；渲染 actions 按钮，点击 POST /rooms/{rid}/recheck
# server.py：/recheck → workflows.on_blocked_input(rid, RESUME_BUTTON, settings)

# room_settings：可编辑的续接短语表，初始只有“继续”
resume_words = ['继续']           # 用户可在协作室信息中增删，例如“已修复”“好了”
def is_resume_only(text):
    return text == RESUME_BUTTON or text.strip() in settings['resume_words']
```

## 阶段 B：去掉显性重复

B 阶段把“方案、批准、规则”这些确定内容交给宿主保存和展开，模型只输出引用和变化。

### B1 监工输出协议：公开发言与控制记录分开

```python
# supervision.py：新的返回结构
{
  "public_message": "给群看的安排、判断和证据引用，简洁",
  "control": {
    "rules": [...],          # 仅当 state.rules 为空（首轮或政策变化）时提供
    "checks_patch": [{"rule_id", "status", "evidence_ids", "note"}],   # 只写状态变化的项
    "action": {"type": "ask|execute|check|adopt_plan|request_approval|finish|wait", ...}
  }
}

def validate_decision(value, state):
    c = value['control']
    if state.rules is None:
        require(1 <= len(c.rules) <= 16)                 # 首轮必须给规则
    elif 'rules' in c:
        raise Correction('规则只在政策变化时重写，本轮请只给 checks_patch。')
    for p in c.checks_patch:
        require(p.rule_id in rule_ids(state)); require(all(e in evidence_ids(state) for e in p.evidence_ids))
        p.bound_to = current_refs(state)                # 宿主绑定当前方案版本与代码哈希
    if c.action.type == 'finish':
        require(covers_all_rules(c.action.full_checks))  # finish 必须完整核对一次
        require(all_bound_to_current(c.action.full_checks))

# orchestrator.py：发布
raw = await reply(...)                                  # 监工自己的原生历史仍保留完整 JSON
store.archive_raw(rid, 'supervisor', raw)               # 完整原始返回归档，可审计
mid = store.message(rid, 'supervisor', d.public_message, phase='安排与检查')   # 只有这部分进入群原文
store.event(rid, {'type': 'control', 'message_id': mid, 'control': d.control})       # 界面规则面板读这里
```

provider\_sessions.reply 增加 publish 参数：监工调用时不自动把完整答复写成群消息，由 orchestrator 在校验通过后发布 public\_message。旧消息不改写；界面用不同样式区分公开发言和控制记录。

### B2 方案库与批准记录

方案正文来自成员原文的确定复制，不让模型改写；批准绑定方案版本的哈希。

```python
# store.py
CREATE TABLE plans(room, plan_id, version, text, sha256, source_message_id, created,
                   PRIMARY KEY(room, plan_id, version))
CREATE TABLE approvals(room, plan_id, version, sha256, who, kind, message_id, created)  # kind: member|user

# 监工动作：采纳某条成员原文为方案新版本
{"type": "adopt_plan", "plan_id": "P2", "message_id": "<成员消息 ID>"}
def adopt_plan(state, a):
    msg = store.message_by_id(a.message_id)
    require(msg.room == rid and msg.provider in member_ids and msg.status == 'complete')
    text = msg.text                                    # 原文复制；可选 section 只取标记段落
    version = store.next_plan_version(rid, a.plan_id)
    store.save_plan(rid, a.plan_id, version, text, sha256(text), a.message_id)
    state.plans[a.plan_id] = {'version': version, 'sha256': sha256(text)}
    mark_checks_bound_to_plan_stale(state, a.plan_id)  # 旧版本的同意与检查不延续

# 用户批准：监工发起，用户点击或简短确认语完成
request_approval -> store.status(rid, 'awaiting_approval', plan_ref='P2@3')
on_user_message: if room awaiting_approval and (button == 'approve' or APPROVE_WORDS.fullmatch(text)):
    store.add_approval(rid, 'P2', 3, sha, who='user', kind='user', message_id=user_mid)
```

execute 动作必须携带 plan\_ref 和 approval\_ids，宿主核对这些批准存在且哈希等于当前版本；监工无法凭叙述创造批准。

### B3 执行契约只组装一次

```python
# orchestrator.py：execute_supervised（现在 task 与 context 都含契约正文）
contract_text = render_contract(task, plan=store.plan(rid, task.plan_ref))   # 宿主展开方案正文
context_text = execution.compose(project_rules(root) + '\n' + execution_state(state, root))
#   execution_state：已完成步骤、当前文件哈希、未通过测试、待核实写入（不含群原文）
started = session.call_tool('start_deepseek', {'task': contract_text, 'context': context_text, ...})
state.pending_action.contract_hash = sha256(contract_text)
execution.record_contract(contract_text)               # 投递记录保留契约全文供审计
```

上游执行器每个任务都要求完整契约，所以首次契约正文仍会占输入，这部分不算节省。

### B4 发言按任务类型组包

```python
# ask 动作增加 kind、plan_ref、refs、files
{"type": "ask", "kind": "propose|review|vote|clarify", "agents": [...], "instruction": "...",
 "plan_ref": "P2@3", "refs": ["<message_id>"], "files": ["相对路径"]}

MATERIALS = {
  'propose': lambda: rules_once() + snapshot(files=a.files or DEFAULT_INDEX),
  'review':  lambda: plan_for(agent, a.plan_ref) + refs(a.refs) + snapshot(files=a.files),
  'vote':    lambda: plan_for(agent, a.plan_ref) + open_questions(state),
  'clarify': lambda: refs(a.refs),
}

def plan_for(agent, ref):
    seen = session_assets(agent).get('plan:' + ref.plan_id)       # 本会话分支已收到的版本
    if seen and seen.branch == current_branch(agent):
        return diff(seen.version, ref.version)                     # 已看过旧版：只发差异
    return full_text(ref)                                          # 新会话或未看过：发全文

def rules_once():
    h = sha256(project_rules(root))
    return '' if session_assets(agent).get('rules') == h else project_rules(root)   # 规则变化才重发
```

只有确认本会话分支已经收到正文时才省略；哈希只用来判断变化，不替代正文。

### B5 监工“宿主检查”动作

```python
{"type": "check", "checks": ["git_status", "git_diff_stat", "executor_start",
                             "file_exists:DesktopIconToggle/build.cmd", "acceptance:<命令>"]}
async def host_check(state, a):
    results = [await HOST_CHECKS[name](root, arg) for name, arg in parse(a.checks)]   # 白名单
    ev = add_evidence(state, kind='check', status='complete', output=results)       # 新证据编号
    store.message(rid, 'system', render(results), phase='宿主检查')
    # 不调用任何成员模型；下一轮监工看到的是这条证据
```

acceptance 类检查复用 validate\_command 与 verify\_commands，其余复用 git\_access 与 preflight。

### B6 讨论按差异修订、明确表态

```python
VOTE_TEMPLATE = ('请对 {ref}（sha {sha8}）表态。修订只写需要替换的条款及理由，引用条款编号，不重写全文。'
                 '最后一行必须是：表态：同意 {ref} 或 表态：反对 {ref}：<理由>')

def record_vote(state, agent, reply, ref):
    m = re.search(r'^表态：(同意|反对) (P\d+)@(\d+)(?:：(.*))?$', reply, re.M)
    if not m or f'{m[2]}@{m[3]}' != ref.text:
        return add_evidence(state, kind='ask', agent=agent, status='no_vote')      # 未表态≠同意
    if m[1] == '同意':
        store.add_approval(rid, m[2], int(m[3]), ref.sha256, who=agent, kind='member', message_id=mid)

def consensus(state, ref):    # 宿主计算，放进监工账本；监工不自行宣称一致
    return {m: approved(m, ref) for m in non_executor_members}
```

初次提案不限制完整表达；差异修订由监工指定一位成员整合成新版本后，再用 adopt\_plan 入库。宿主只应用可验证的确定修订，不自行合并语义。

**表态采用混合模式，并增加“讨论容忍度”滑块（已定）。** 有格式行以格式行为准；没有时宿主只向那一位追问一次；仍没有才交监工判断，并在用户确认方案前标出“此项由监工判定”。容忍度是协作室设置里的 1–5 档滑块，由宿主执行，控制讨论长度。

| 容忍度 | 初次提案后最多评审/表态轮数 | 修订轮单条回复上限 |
| --- | --- | --- |
| 1 紧 | 1 | 800 字 |
| 2 | 2 | 1,500 字 |
| 3 默认 | 3 | 2,500 字 |
| 4 | 5 | 4,000 字 |
| 5 宽 | 8 | 不限 |

```python
# supervision.py：B6 混合模式
async def collect_vote(agent, reply, ref):
    if (v := parse_vote(reply, ref)):                  # 有格式行
        return record(v, source='format')
    reply2 = await ask_once(agent, f'请只补一行：表态：同意 {ref} 或 表态：反对 {ref}：<理由>')
    if (v := parse_vote(reply2, ref)):
        return record(v, source='format_after_reask')
    return record(pending_supervisor_judgement(agent, ref), source='supervisor')   # 界面标“监工判定”

# 容忍度：宿主统计同一 plan_id 的评审/表态轮数
TOLERANCE = {1: (1, 800), 2: (2, 1500), 3: (3, 2500), 4: (5, 4000), 5: (8, None)}
max_rounds, char_cap = TOLERANCE[room.discussion_tolerance]
if action.type == 'ask' and action.kind in ('review', 'vote') and rounds(state, plan_id) >= max_rounds:
    raise Correction('已达讨论容忍度：请指定一位成员整合最终版，或把剩余分歧列出交用户决定。')
instruction += f'\n本轮回复不超过 {char_cap} 字。' if char_cap else ''
```

轮数用尽时不会把未一致当作一致：监工只能安排整合最终版，或把分歧交用户决定。字数上限只约束修订轮，初次提案不限。

## 阶段 C：按角色组包

监工增长的主要来源不是新增原文（已按游标增量投递），而是每轮重发的完整说明与状态。C 阶段把它改成“首次完整、之后只发变化”，并修正游标会跳过未加载消息的问题。

### C1 监工控制上下文模式

协作室新增设置 supervisor\_context：`state`（账本 + 新增原文）或 `full`（保持现状）。所有协作室默认 state（含已有协作室），可在协作室信息中随时切换为 full。该设置只影响监工；较早发言摘要由路线图规定的独立开关控制，full 模式不通过“新成员补看”绕过原文策略。

```python
# supervision.py：替代现在每轮完整拼接的 supervisor_request()
def supervisor_payload(turn, state, trigger):
    last = turn.state.get('ledger_digest')                 # 本会话分支上次已发送的账本摘要
    ledger = ledger_view(state)                            # 目标、政策版本、规则、检查、方案版本、批准、
                                                           # 共识（B6）、证据目录末页、待执行动作、阻断
    body = {'trigger': trigger, 'decision_needed': next_decision(state)}
    if last is None:
        body['ledger'] = ledger                            # 新分支：完整当前状态，不是补丁串
    else:
        body['ledger_patch'] = json_diff(last, ledger)     # 续轮：只发变化字段
    if project_digest(root) != turn.state.get('project_digest'):
        body['project'] = project_index(root)              # 项目索引变化才重发
    body['volatile'] = {'connections': ..., 'quota': ...}  # 易变字段放最后，不带时间戳
    turn.state['ledger_digest'] = ledger
    return json.dumps(body, ensure_ascii=False)

# context.py：监工在 state 模式下的原文选择顺序
PRIORITY = [user_messages, replies_to_last_ask, system_blockers_and_checks, others_newest_first]
# 超预算时依次放入，余下的进 gaps，并在请求中列出 message_id 供工具读取
```

账本自身的证据目录分页：只带与当前方案、未满足规则和最近一步相关的条目，其余用 evidence\_read(id) 工具读取，避免账本变成新的无限历史。

### C2 投递覆盖范围与缺口

现在 complete() 把 cursor 直接推到 boundary，因预算被省略的消息以后不会再出现。改为按区间记录实际投递。

```python
# context.py（保留 cursor 字段兼容旧会话）
state['delivered'] = IntervalSet.load(state.get('delivered', [[0, state['cursor']]]))
state['gaps'] = IntervalSet.load(state.get('gaps', []))

def compose(...):
    pool = [m for m in store.originals(rid, before=boundary)
            if m.seq not in state.delivered and not (native and m.provider == identity)]
    selected, omitted = pack_by_priority(pool, budget)          # C1 的顺序；讨论成员按时间顺序
    prompt.not_loaded = [ref(m) for m in omitted] + [ref(seq) for seq in state.gaps]

def complete(...):
    state.delivered |= seqs(selected)
    state.gaps = (state.gaps | seqs(omitted)) - state.delivered
    state.cursor = state.delivered.contiguous_end()             # 旧字段只表示连续前缀

# room_tools / execution_context：通过 group_get_message 读到缺口消息时记为已投递
on_tool_read(session_key, seq): delivered |= {seq}; gaps -= {seq}
```

投递记录（context\_deliveries）同时写入 delivered/gaps 快照，界面可显示“未加载 N 条”。原生压缩覆盖的范围另记 compacted，不标为“全文仍在上下文中”。

### C3 稳定指令只发一次

```python
# provider_sessions.py：只在新分支建立时设置角色指令
ROLE_INSTRUCTIONS = {'supervisor': SYSTEM + SUPERVISOR_PROMPT, 'discussion': SYSTEM, 'executor': EXECUTOR_SYSTEM}
codex:  thread/start developerInstructions = ROLE_INSTRUCTIONS[role]   # 已有字段，补上监工说明
claude: --append-system-prompt ROLE_INSTRUCTIONS[role]
api:    native.messages[0] = {'role': 'system', 'content': ROLE_INSTRUCTIONS[role]}

# supervision.py：续轮不再把 SUPERVISOR_PROMPT 拼在每轮用户消息前
prompt = supervisor_payload(turn, state, trigger)      # C1
```

不支持单独系统指令的适配器，在会话建立时的第一条消息里提供一次。换模型、换连接会新建分支，指令随之重新设置。

## 阶段 D：有界会话、估算校准、存储

只发变化仍会让历史缓慢增长，D 阶段给每个角色的会话设上界：到达阈值先用原生压缩，不支持时用交接包开新分支，不再从第 0 条重放。

### D1 交接包与有界会话

```python
# context.py
MAINTAIN_AT = room.context['maintain_at']            # 默认 0.6，协作室设置可调

async def maintain(turn):
    history = last_request_input(turn)               # 取自 A1：本分支最近一次实际输入
    if history < MAINTAIN_AT * turn.budget['soft']:
        return
    if turn.adapter.supports_native_compaction:      # Codex thread/compact、Claude 自动压缩
        await turn.adapter.compact(turn)
        turn.state['compacted'] = turn.state['delivered'].copy()   # 压缩覆盖范围，不等于全文仍在
    else:                                            # Gemini / 兼容 API：签名历史不能切片
        rotate_with_handoff(turn, '历史达到维护阈值')

def rotate_with_handoff(turn, reason):
    old = turn.state; store.archive_session(turn.key, old)
    pkg = build_handoff(turn.role, store.load_workflow(turn.rid), old)
    turn.state = {'native': {}, 'branch': new_id(), 'handoff': pkg.meta,
                  'delivered': IntervalSet(pkg.included_seqs),      # 只算交接包里真正包含的
                  'gaps': IntervalSet(pkg.omitted_seqs)}            # 其余列为未加载，可按需读取
    turn.pending_prefix = pkg.text                                    # 下一次 compose 时放在最前

def build_handoff(role, wf, old):
    if role == 'supervisor':
        return Pkg(ledger_view(wf), recent=last_originals(8), gaps=older_refs())
    if role == 'discussion':
        return Pkg(full_plan(current_plan_ref(wf)), my_votes(wf, identity), open_items_for(identity),
                   own_last_answer=old.get('last_answer'), recent=last_originals(8), gaps=older_refs())
    if role == 'executor':
        return Pkg(full_contract(wf.pending_action), execution_state(wf, root), recovery_records(root))

# ContextTurn.__init__：换监工模型或连接产生新的绑定键时
if store.session(key) is None and (prev := store.latest_session(rid, identity, role)):
    rotate_with_handoff(self, '模型或连接已更换')    # 不从 cursor 0 重放全群
```

交接包只搬运公开事实与该身份自己的公开答复；另一身份的原生历史不当作本人会话。执行者的交接包不含任何群原文。

两个参数都放进协作室上下文设置（context\_settings），界面可调：maintain\_at 默认 0.6，范围 0.3–0.9；handoff\_recent（交接包内近期原文条数）默认 8，范围 0–50，代码中的 last\_originals(8) 均读取此值。

### D2 估算校准

与正在进行的“上下文预算可配置、超预算暂停”改动（budget\_control.py、context\_settings.py）合并；不扩大窗口冒充节省。

```python
# context.py
def estimate_v2(text):
    cjk = sum(1 for ch in text if is_cjk(ch))
    rest = len(text.encode('utf-8')) - 3 * cjk
    return ceil(cjk * 1.0 + rest / 4)               # 初始权重，由下式校准

def calibrated(adapter, model, request_estimate):
    f = calibration.get((adapter, model))            # 来自 A1：EWMA(实际输入 / 完整请求估算)
    return request_estimate * (f.value if f and f.samples >= 20 else 1.0)

# 影子模式：先记录旧估算、新估算与实际值，只用于界面提示；
# 同一适配器连续 20 次误差 ≤ ±20% 后，再用于预算拦截
```

CLI 适配器只能估算宿主可见部分，其余以服务商报告的上一次实际输入为准。

### D3 流式事件合并

```python
# store.py：现在每次 update() 都把全文写成一条 events 行（60 条消息 → 3,263 行）
def update(mid, text, status='streaming'):
    write_message_row(mid, text, status)
    if status == 'streaming':
        live.publish(room, {'type': 'message', 'message': row})   # 内存广播给 SSE，不落盘
        return
    event(room, {'type': 'message', 'message': row})               # 结束状态才写 events

# server.py：SSE 先回放 events，再订阅 live；重连时流式中的消息从 messages 表补当前正文
```

这是存储优化，不计入 token 收益。

## 验收用例与度量指标

每个阶段完成后，现有 118 项回归必须全部通过，再加下表对应用例。节省一律按同角色、同适配器、同任务对比，不设统一的单轮 token 上限。

| 用例 | 覆盖 | 做法 | 通过标准 |
| --- | --- | --- | --- |
| New folder 故障重放 | A2、A3 | 离线模拟 Git 拒绝和执行者启动失败，用户连续回复“已修复” | 同一故障复查失败时模型调用 0 次；修复后续接原契约，不重开讨论、不重新请求已有批准 |
| 环境正常的完整任务 | 全部 | 用 New folder 的原始需求在临时项目实跑一次 | 保留异议、明确共识、用户确认和实际交付；总输入、输出和无效请求均低于改动前同任务基线 |
| 角色隔离 | B3、C2、D1 | 检查最终请求体 | 讨论成员收到带作者的新增原文；执行者请求不含群历史、无群读取工具、契约正文只出现一次 |
| 长会话与缺口 | C2、D1 | 用小预算制造消息省略，再触发轮换 | 未加载消息始终列在 not\_loaded；读取后标为已投递；交接不误标为全文已投递 |
| 方案与证据变更 | A2、B2、B6 | P2 获批后采纳 P3；改动代码后再 finish | 旧同意、旧批准、旧检查自动失效；监工无法引用不存在或过期的批准 |
| 换监工与中断恢复 | A2、D1 | 中途把监工从 GPT 换成 Gemini，并强制停止一次 | 交接保留当前方案、批准、分歧、待执行动作与真实文件状态；不重放整群 |
| 监工输出协议 | B1 | 连续 5 轮监工决策 | 规则只在首轮输出；群原文只含 public\_message；完整原始返回可在归档中查到 |

度量指标（均取自 A1 的 usage\_records）：

- 每个任务的总输入、总输出，按角色和适配器拆分。
- 无效请求数：调用后工作流状态无变化的模型调用。
- 达成共识所用轮数与返工次数。
- 缓存命中率单列，不与输入节省合并计算。

离线重放只验证组包与状态；实际缓存、服务商用量和交付质量必须用真实运行确认。

## 与进行中改动的协调、未决问题

开工前先处理两件事：等工作区里未提交的“协作室控制”改动完成并提交，再修好 DeepSeek 执行者启动失败。

- **工作区冲突：** context.py、server.py、provider\_sessions.py 等文件正在被“上下文预算可配置、超预算暂停”改动修改（新增 budget\_control.py、context\_settings.py）。A1、C1–C3、D1–D2 都会改 context.py，两边同时改会互相覆盖；D2 直接并入该改动。
- **执行者启动失败：** 已修复（提交 bf974be）。显示出的真实异常是协作室输出预算 32768 超过执行器上限 16384，现已在传给执行器前限制。更早两次 TaskGroup 失败发生在该设置引入之前，原因未核实。

需要用户或双方确认的问题：

- [x] 已定：supervisor\_context 默认 state，保留切换为 full 的开关。
- [x] 已定：“复查并继续”放在红色宿主环境错误消息下方；续接短语表初始只有“继续”，可在设置中增删。
- [x] 已定：表态采用混合模式，并增加 1–5 档“讨论容忍度”滑块控制讨论长度（见 B6）。
- [x] 已定：MAINTAIN\_AT（默认 0.6）与交接包近期原文条数（默认 8）都做成协作室可调设置。
- [ ] 缓存 token 如何计入各家账户额度尚未核实，暂不纳入收益计算。
