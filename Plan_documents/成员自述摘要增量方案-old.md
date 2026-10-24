# token 优化增量方案：成员自述摘要与外部借鉴

Oct 22, 2026 · @Thodmc

非监工成员每条公开回复末尾附一行自述摘要；宿主按固定规则选择投喂档位，原文始终可按编号读取。自 2026-10-22 修订起，成员发言不再禁止压缩：压缩版与原文同时可得，由下游模型自选摄入，并留下摄入记录（第六部分）；原文永不删除，只改变"默认投给模型的是哪一版"。

本文是《Agent Group Room token 优化实施方案》的独立增量。**唯一一处修改主方案边界**：主方案规定"讨论成员之间的公开原文由宿主直接投递，不改成监工摘要"，本方案第六部分改为"原文与摘要双投递、模型自选、留摄入记录"，因此该条须先同步修订主方案，否则两份文档冲突。除此之外不修改主方案其他条目，只在 A1、B4、B6、C1、C2、C3、D1 的接口上挂接。

本文分七部分：第一部分是成员自述摘要（S1–S3）；第二部分是从 GitHub 高星项目借鉴的六项改动（E1–E6）；第三部分说明哪些通用能力应当直接调用现成开源组件、以及端侧专用摘要模型这条路线怎么落；第四部分把这些项目按数据流位置分类；第五部分给出净节省核算与评估口径；第六部分是原文与摘要的双投递、自选摄入与摄入记录；第七部分规定实验路线的观测契约。

第三到第七部分只给思路与边界，不给实现细节；落地时按"能直接调用就不自己写"处理（见第三部分第一节），并受第七部分的观测契约约束。

## 第一部分：成员自述摘要

## 范围

摘要只用于"了解大意、决定要不要读全文"的场合；需要核对细节、结论或授权的场合用原文。**投喂档位由宿主按场合定，档位之内读哪一版由模型定**（第六部分）。下表给的是默认档位，不是禁令。

| 场合 | 用什么 | 理由 |
| --- | --- | --- |
| U1 监工看较早轮次的成员发言 | 摘要 + 编号 | 监工只需要进展脉络；本轮新回复仍给全文 |
| U2 未加载消息清单 | 编号 + 作者 + 摘要 | 现在只有编号，读者无法判断是否补读 |
| U3 换监工、会话轮换的交接包 | 近期原文 + 更早的摘要 | 不重放全群也能保留脉络 |
| U4 新加入成员补看历史 | 当前方案全文 + 其余摘要 | 可按需补读全文 |
| 成员评审他人方案、提出异议 | 原文 + 摘要（双投递） | 两份都给，由模型自选；宿主不再单方面删掉原文 |
| 表态与共识 | B6 格式行 | 摘要可能把“基本同意但……”写成同意；共识仍由宿主解析表态行 |
| 执行证据、测试结果、用户批准 | 原文与真实记录 | 摘要不是证据；压缩版可以投喂，不能被引用 |
| 执行者 | 只收契约 | 不接触群聊，也不接触摘要 |

摘要标为“作者自述摘要”，不能作为批准、表态或证据被引用——无论它是否与原文一同投喂。监工自己的发言不生成摘要（其状态已由账本承载）。

## S1 生成与存储

只要求讨论成员写摘要（监工、执行者不写）；短回复本身就是摘要，不要求额外一行。原文按模型输出原样保存，摘要是从原文里解析出的副本。

```python
# supervision.py / orchestrator.py：讨论成员每次发言的指令末尾追加（长回复才需要）
SUMMARY_RULE = ('如果你的回复超过 {min_chars} 字，在正文后单独写一行摘要，不超过 {max_chars} 字：\n'
                '摘要：立场=<支持/反对/有条件/中立>｜要点=<最重要的一两点>｜未决=<仍未解决的问题，没有写无>\n'
                '表态轮里，摘要行放在表态行之前，表态行仍是最后一行。')

# store.py：messages 增加两列（旧消息为 NULL，不回填）
ALTER TABLE messages ADD COLUMN summary TEXT
ALTER TABLE messages ADD COLUMN summary_source TEXT   # self | reask | short | none

# orchestrator.py：成员答复发布为 complete 之后
SUMMARY_LINE = re.compile(r'^摘要：立场=(?P<stance>[^｜]+)｜要点=(?P<points>[^｜]+)｜未决=(?P<open>.+)$', re.M)

async def attach_summary(mid, agent, text, settings):
    s = settings['summary']
    if not s['enabled'] or role_of(agent) != 'discussion':
        return
    if len(text) <= s['min_chars']:
        return store.set_summary(mid, text, 'short')          # 短回复：全文即摘要
    m = last_match(SUMMARY_LINE, text)
    if m and len(m.group(0)) <= s['max_chars'] + 8:
        return store.set_summary(mid, normalize(m), 'self')
    if s['reask']:                                            # 缺失或超长：只追问一次
        extra = await reply(agent, '请只补一行摘要，格式：摘要：立场=…｜要点=…｜未决=…',
                            phase='补充摘要', publish=False)     # 不作为新群消息发布
        m = last_match(SUMMARY_LINE, extra)
        if m:
            return store.set_summary(mid, normalize(m), 'reask')
    store.set_summary(mid, None, 'none')                       # 交由 S2 的回退规则处理

def normalize(m):
    return f"立场={m['stance'].strip()}｜要点={m['points'].strip()}｜未决={m['open'].strip()}"
```

补问是该成员会话内的一次短调用，结果只写入 summary 列，不进入群原文；该次用量记入 A1 的 usage\_records，composition 标为 summary\_reask。

## S2 使用：四个场合的组包

四个场合共用一个“摘要条目”形式；只收到摘要的消息另记为 digested，不计入 delivered，不会被当成“全文已投递”。预算不足时的优先顺序是：本轮必须的全文 → 摘要条目 → 只列编号。

```python
# context.py：统一的摘要条目
def digest_entry(m, settings):
    if m.summary:
        body, kind = m.summary, '作者自述摘要'
    elif len(m.text) <= settings['summary']['fallback_full_chars']:   # 短消息直接给原文
        body, kind = m.text, '原文（较短）'
    else:
        body, kind = None, '无摘要，需要时按编号读取'
    return {'id': m.id, 'seq': m.seq, 'provider': m.provider, 'phase': m.phase,
            'characters': len(m.text), 'summary': body, 'summary_kind': kind}

def pack_digests(entries, budget):        # 超预算时保留较新的摘要，其余只留编号
    kept, ids_only = take_newest_within(entries, budget)
    return kept + [{'id': e['id'], 'seq': e['seq'], 'provider': e['provider']} for e in ids_only]

# U1：监工（主方案 C1 的 state 模式）
def supervisor_originals(turn, pool, state):
    full = [m for m in pool if m.provider == 'you'                 # 用户消息
            or m.status == 'host_error'                             # 宿主错误
            or answers_last_ask(m, state)]                         # 本轮新回复
    older = [m for m in pool if m not in full and is_discussion(m.provider)]
    digests = pack_digests([digest_entry(m, settings) for m in older], turn.digest_budget)
    turn.state['digested'] |= seqs(older)                           # 只给了摘要，不计入 delivered
    return {'messages': full, 'earlier_digest': digests}

# U2：未加载清单（主方案 C2），由只列编号改为摘要条目
prompt.not_loaded = pack_digests(
    [digest_entry(m, settings) for m in omitted] +
    [digest_entry(store.message_by_seq(rid, s), settings) for s in state.gaps],
    turn.digest_budget)

# U3：交接包（主方案 D1）
def build_handoff(role, wf, old):
    recent = last_originals(settings['context']['handoff_recent'])             # 近期仍给原文
    older = [m for m in originals_before(recent) if is_discussion(m.provider)]
    pkg = ...                                                                  # 主方案原有内容不变
    pkg.digest = pack_digests([digest_entry(m, settings) for m in older], digest_budget)
    pkg.included_seqs, pkg.digested_seqs = seqs(recent), seqs(older)
    return pkg                                                                 # 执行者分支不加 digest

# U4：新加入（或新分支）的讨论成员补看历史
if turn.role == 'discussion' and turn.state['delivered'].empty() and store.boundary(rid) > 0:
    head = full_plan(current_plan_ref(wf))                                     # 当前方案全文
    recent = last_originals(settings['context']['handoff_recent'])
    digest = pack_digests([digest_entry(m, settings) for m in originals_before(recent)], turn.digest_budget)
```

读者通过群消息工具读取某条全文后，该条从 digested 移到 delivered（复用主方案 C2 的 on\_tool\_read）。

## S3 设置、界面与计量

所有参数都放在协作室设置，可整体关闭，也可单独关闭某个场合。

```python
# context_settings.py：新增 summary 分组
DEFAULT_SUMMARY = {
    'enabled': True,
    'min_chars': 400,              # 回复超过这个长度才要求摘要；范围 0–5000
    'max_chars': 120,              # 摘要行上限；范围 40–400
    'reask': True,                 # 缺摘要时是否追问一次
    'fallback_full_chars': 600,    # 无摘要的短消息直接给原文；范围 0–3000
    'digest_budget_ratio': 0.15,   # 摘要条目最多占本次输入预算的比例；范围 0.05–0.5
    'uses': {'supervisor_history': True, 'not_loaded': True, 'handoff': True, 'catch_up': True},
}
def summary_settings(value):     # 与 context_settings() 同样校验类型与范围，越界拒绝保存
    ...

# context.py
turn.digest_budget = int(turn.input_budget * settings['summary']['digest_budget_ratio'])
```

界面：

- 成员消息卡片底部显示一行灰色“作者自述摘要”，来源为补问时标注“补问生成”。
- 协作室信息增加“成员摘要”一组：总开关、四个场合各自的开关，以及上面的数值。
- 上下文投递记录分别显示“全文 N 条 / 摘要 N 条 / 仅编号 N 条”。

计量（接入主方案 A1）：

- composition 增加 digests 和 summary\_reask 两项。
- 每次组包记录“被摘要替代的原文估算”，只作参考，实际节省仍以服务商用量对比为准。
- 统计“看了摘要后又读全文”的比例；若持续偏高，说明摘要质量不足或场合不合适。

## 与主方案的接入点

本增量依赖主方案的 A1、C1、C2、D1，可在它们完成后单独合入；S1 的生成与存储不依赖任何项，可以先做。

| 主方案项 | 接入方式 | 注意 |
| --- | --- | --- |
| A1 用量记录 | composition 增加 digests、summary\_reask | 补问调用单独记录，便于核对摘要本身的成本 |
| B1 监工输出协议 | 不变 | 监工不写摘要；其状态在账本里 |
| B6 表态 | 表态行仍为最后一行，摘要行放在其前 | 共识只看表态行；摘要立场与表态不一致时以表态行为准，并在界面标出 |
| C1 监工控制上下文 | supervisor\_originals() 增加 earlier\_digest | 只在 state 模式下生效；full 模式保持全文 |
| C2 投递覆盖 | 新增 digested 区间集；not\_loaded 改为摘要条目 | digested 不计入 delivered |
| D1 交接包 | pkg.digest 与 digested\_seqs | 执行者交接包不加摘要 |

不改变的边界：成员之间评审与异议仍直接共享原文；摘要不能作为批准、表态或证据；执行者不接触群聊与摘要。

## 验收用例

每条用例在摘要开关打开和关闭时各跑一次，关闭时行为必须与合入前完全一致。

| 用例 | 做法 | 通过标准 |
| --- | --- | --- |
| 解析与补问 | 构造有摘要、无摘要、摘要超长、短回复四种答复 | 分别记为 self、reask（或 none）、reask、short；补问只发一次且不产生新群消息 |
| 表态不被摘要覆盖 | 摘要写“立场=支持”，表态行写“反对 P2@3” | 共识记为反对；界面提示两者不一致 |
| 监工历史用摘要 | state 模式下连续 3 轮讨论 | 本轮回复、用户消息、宿主错误为全文，更早成员发言为摘要；full 模式不受影响 |
| 未加载清单 | 小预算制造省略 | 清单条目带摘要；超出摘要预算的只留编号；读取后从 digested 移到 delivered |
| 交接与补看 | 换监工；新增一位讨论成员 | 近期为原文，更早为摘要；不误标为全文已投递 |
| 角色隔离 | 检查执行者最终请求 | 不含任何摘要或群原文 |
| 收益对比 | 同一任务开关各跑一次 | 监工与交接路径的实际输入下降，且共识轮数和返工次数不增加；补问成本单列 |

## 第二部分：外部借鉴

六项改动均来自已核对的 GitHub 项目做法；节省比例是各项目自报，本项目的收益以 A1 实测为准。

| 编号 | 借鉴来源 | 解决什么 | 优先级 |
| --- | --- | --- | --- |
| E1 | [rtk](https://github.com/rtk-ai/rtk)、[headroom](https://github.com/headroomlabs-ai/headroom) | 执行者命令输出、验收输出、Git 工具输出整段进入上下文 | 高 |
| E2 | [aider 代码地图](https://aider.chat/docs/repomap.html)、[repomix](https://github.com/yamadashy/repomix) | 提案和评审轮附带最多 12 万字的项目快照 | 高 |
| E3 | headroom 的前缀对齐 | 压缩、摘要或易变字段破坏缓存前缀 | 高 |
| E4 | [toon](https://github.com/toon-format/toon) | 监工账本里的结构化列表用 JSON 表示偏长 | 中 |
| E5 | [caveman](https://github.com/JuliusBrussee/caveman) 的实测经验 | 回复冗长，又不想引入长篇风格规则 | 中 |
| E6 | [ccusage](https://github.com/ryoppippi/ccusage) | 用量只有原始记录，没有按会话、时间窗、缓存读写汇总 | 中 |

暂不采用：[mem0](https://github.com/mem0ai/mem0) 式自动记忆提取（每次多一次模型调用且结果不确定，宿主账本更可靠）；[mcp-compressor](https://github.com/atlassian-labs/mcp-compressor) 式工具定义按需加载（本项目各角色工具很少，收益可忽略）。

[LLMLingua](https://github.com/microsoft/LLMLingua) / LLMLingua-2 的 Token 级压缩原先因"原文直接共享"被排除。这条禁令已按第六部分取消：它可以作用于成员发言，但必须同时满足三件事——压缩版与原文一并投喂、原文随时可回取、摄入行为留记录。仍不建议把它当作群原文的默认手段，理由从"违反政策"变成工程质量：它对代码、路径与标点的破坏是不可逆的，而成员发言里恰恰充满这些。

## E1 命令与验收输出：分类压缩，原文存档可取回

现在执行者 Bash 结果最多 50,000 字符、验收输出最多 30,000 字符、Git 工具输出最多 40,000 字符整段进入上下文。改为按命令类型只保留有用部分，完整输出存档并编号，需要时用工具取回（headroom 的可逆压缩）。

```python
# output_compress.py（新增，纯函数，宿主和执行器子进程共用）
KEEP = re.compile(r'error|fail|exception|traceback|assert|warning|错误|失败', re.I)

RULES = [   # (命令匹配, 压缩函数)，按顺序取第一个匹配
  (r'\b(pytest|python -m unittest|npm (run )?test|node \S*test|go test|cargo test|dotnet test)\b', tests_failures_only),
  (r'\b(build\.cmd|msbuild|csc|dotnet build|npm run build|tsc|cargo build)\b', build_errors_only),
  (r'^git (status|log|diff|show)\b', git_compact),
  (r'.*', generic),
]

def tests_failures_only(text):      # 失败用例完整保留，通过用例折叠为计数，保留末尾汇总行
    ...
def build_errors_only(text):        # 只留含 error/warning 的行及其前后 2 行，加最后 20 行
    ...
def git_compact(text):              # status 按状态分组；log 只留哈希/作者/标题；diff 超长时先给 --stat
    ...
def generic(text):                  # 合并连续重复行（“×N”），单行超 400 字截断，头 40 行 + 尾 80 行 + 中间所有 KEEP 行
    ...

def compress(command, text, exit_code, limit=6000):
    if len(text) <= limit:
        return text, None                                   # 短输出不动
    fn = next(f for pattern, f in RULES if re.search(pattern, command, re.I | re.M))
    out = fn(text)
    out = ensure_kept(out, text, KEEP, last_lines=40)       # 含错误关键词的行与最后 40 行必须保留
    if len(out) > 0.8 * len(text):
        return text[:limit] + TRUNC_NOTE, 'raw'              # 压不下来就不压（质量闸门），只做原有截断
    return out, fn.__name__

# output_archive.py：完整输出存档（宿主状态目录，不在项目内）
def archive(room, owner_session, command, exit_code, text):
    oid = 'out_' + sha256(text)[:12]
    path = STATE / 'outputs' / room / (oid + '.txt'); write_once(path, text)
    store.save_output(oid, room, owner_session, command, exit_code, len(text), path)
    return oid

def present(room, owner_session, command, exit_code, text):
    body, method = compress(command, text, exit_code)
    if method is None:
        return text
    oid = archive(room, owner_session, command, exit_code, text)
    return f'{body}\n[已压缩（{method}），完整输出 {len(text)} 字符：output_read("{oid}")]'
```

三个接入点：

```python
# 1) 执行者 Bash：tool_child_env.py 包装 bash_tool.execute_bash 的返回值
result = original_execute_bash(args, config, **kw)
return present(ROOM, SESSION, args['command'], parse_exit(result), result)

# 2) 宿主验收：workflows.verify_commands 把完整 captured 存档，证据里只放 present() 的结果
evidence.append({'command': c, 'exit_code': code, 'output': present(rid, 'host', c, code, text), 'output_id': oid})

# 3) Git 工具：git_access.run_readonly 对 diff/log/show 同样走 present()

# 取回工具（宿主执行，与 Git 工具同样挂在 execution_context / project_tools / room_tools）
OUTPUT_READ = {'name': 'output_read', 'parameters': {'id': str, 'offset': int, 'limit': int, 'grep': str}}
def output_read(session, oid, offset=0, limit=8000, grep=None):
    rec = store.output(oid)
    require(rec.room == session.room)
    if session.role == 'executor':
        require(rec.owner_session in (session.key, 'host'))      # 执行者只能读自己的和验收输出
    text = read(rec.path)
    if grep:
        text = '\n'.join(l for l in text.splitlines() if re.search(grep, l))
    return text[offset:offset + limit] + next_offset_hint(text, offset, limit)
```

压缩只在输出产生时做一次，结果写入历史后不再改写（见 E3）。验收是否通过只看真实退出码，压缩不影响判定。协作室设置增加 output\_compress：开关、limit（默认 6000，范围 2000–50000）。

## E2 代码地图替代项目快照，配只读项目工具

现在提案和评审轮附带 build\_snapshot（最多 120,000 字符的路径、差异和文件正文）。改为按依赖排序、有 token 预算的签名地图；具体文件由成员用只读工具按需读取。这是主方案 B4 中 snapshot() 的具体实现。

```python
# code_map.py（新增）
EXCLUDE = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.tiered', 'dist', 'build', 'bin', 'obj'}

def extract(path, text):
    if path.endswith('.py'):
        return python_ast_symbols(text)            # 标准库 ast：类、函数签名、模块级常量、import
    if HAS_TREE_SITTER and lang_of(path):
        return tree_sitter_symbols(path, text)     # 可选依赖；未安装时走下一行
    return regex_symbols(path, text)               # js/ts/cs/go/rs/java 的定义行正则
    # 返回 Symbols(defs=[(name, signature, line)], refs={identifier, ...}, imports=[...])

def build_graph(files):
    defs = {name: f for f, s in files.items() for name, _, _ in s.defs}
    edges = Counter()
    for f, s in files.items():
        for ident in s.refs | set(s.imports):
            if (g := defs.get(ident)) and g != f:
                edges[(f, g)] += 1                 # f 引用了 g 定义的符号
    return edges

def rank(files, edges, focus, iterations=30, damping=0.85):
    # 个性化 PageRank：跳转分布集中在 focus 文件（任务允许路径、action.files、被引用消息里提到的路径）
    ...

def code_map(root, budget_tokens, focus=()):
    key = (git_tree_hash(root), tuple(sorted(focus)), budget_tokens)
    if key in CACHE: return CACHE[key]
    files = {p: extract(p, read(p)) for p in walk(root, EXCLUDE) if is_source(p) and size(p) < 512_000}
    scores = rank(files, build_graph(files), focus)
    lines, used = [], 0
    for f in sorted(files, key=lambda f: -scores[f]):
        block = f + ':\n' + '\n'.join('  ' + sig for _, sig, _ in files[f].defs)
        cost = estimate(block)
        if used + cost > budget_tokens:
            block = f + '（签名省略）'; cost = estimate(block)
            if used + cost > budget_tokens: break
        lines.append(block); used += cost
    CACHE[key] = '\n'.join(lines) + f'\n[代码地图，预算 {budget_tokens} token；正文用 project_read 读取]'
    return CACHE[key]

# 主方案 B4 的 MATERIALS 改为
MATERIALS['propose'] = lambda: rules_once() + code_map(root, room.code_map_tokens, focus=a.files)
MATERIALS['review']  = lambda: plan_for(agent, a.plan_ref) + refs(a.refs) + code_map(root, room.code_map_tokens, focus=a.files) + full_files(a.files)
```

只读项目工具（ChatGPT 在一轮调研中指出各讨论适配器没有统一的项目读取能力）：

```python
# room_tools.py / context.TOOL_SCHEMAS：给讨论成员与监工增加（执行者已有自己的 Read）
project_read(path, offset=0, limit=400)     # 复用 ProjectTools 的路径校验：不越出项目，不读 .git/.env/凭据/代理配置
project_search(pattern, glob=None)         # 复用 Grep，结果走 E1 的 present()
# Codex 成员：加入 dynamicTools；Claude CLI 成员：加入 room MCP 服务器；API 成员：加入工具循环
```

协作室设置增加 code\_map\_tokens（默认 2000，范围 500–20000）。tree-sitter 为可选依赖，引入时需要更新 requirements.lock.txt 与第三方许可清单；不引入也能用。

## E3 前缀稳定：只压缩新内容，检测缓存断点

各家缓存都要求前缀完全一致。规则：压缩（E1）、摘要（S1）、编码（E4）只在内容产生时做一次；已发出的历史不再改写；易变字段只放在末尾。宿主对自己维护历史的 API 路径做自动检测。

```python
# context.py：API 路径（Gemini / DeepSeek / 兼容 API）每次请求前
def check_prefix(turn, messages):
    hashes = [sha256(canonical(m)) for m in messages]           # canonical：json.dumps(sort_keys=True, ensure_ascii=False)
    prev = turn.state.get('prefix_hashes', [])
    common = 0
    while common < min(len(prev), len(hashes)) and prev[common] == hashes[common]:
        common += 1
    if prev and common < len(prev):                               # 不是纯追加：有历史被改写
        turn.composition['prefix_break'] = {'at': common, 'of': len(prev), 'reason': turn.rotation_reason}
    turn.state['prefix_hashes'] = hashes

# 易变字段的规定（写进开发规范，并由测试检查）
STABLE = ['角色指令', '工具定义（固定顺序）', '交接包', '已发送的历史']
VOLATILE_LAST = ['额度', '连接状态', '本轮触发原因', '纠正说明']
# - 系统/角色指令与工具定义中禁止出现时间戳、随机 ID、额度数字
# - account_usage.team_quota 的 checked_at 不进入模型请求（只存事件）
# - 所有发给模型的 JSON 使用 sort_keys=True
# - 工具列表按名称排序后再发送
```

Codex、Claude 的原生会话由各自运行器拼接请求，宿主无法逐字节检测；以 A1 记录的缓存读写比例判断：同一分支连续调用中缓存命中突然归零，且距上次调用未超过该服务商的缓存时长，就记一条 prefix\_break\_suspected，供排查。

## E4 账本列表的紧凑表格编码

监工账本里的证据表、检查项、批准记录、各方额度都是字段一致的对象数组，改用“表头 + 行”编码。嵌套、不规则的部分仍用紧凑 JSON；两种编码取更短的一种。

```python
# ledger_encode.py（新增）
def encode_table(name, rows):
    fields = list(rows[0])
    if len(rows) < 3 or any(list(r) != fields for r in rows) or any(isinstance(v, (dict, list)) for r in rows for v in r.values()):
        return None                                         # 不规则或太短：不用表格
    head = f"{name}[{len(rows)}]{{{','.join(fields)}}}:"
    body = ['  ' + ','.join(cell(r[f]) for f in fields) for r in rows]   # cell：含逗号/换行/引号时加引号并转义
    return '\n'.join([head, *body])

def encode_ledger(ledger):
    parts = []
    for key, value in ledger.items():                       # 键顺序固定（E3）
        compact = json.dumps({key: value}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        table = encode_table(key, value) if is_list_of_dicts(value) else None
        parts.append(table if table and estimate(table) < estimate(compact) else compact)
    return '\n'.join(parts)

# supervision.supervisor_payload（主方案 C1）中 ledger / ledger_patch 的列表字段走 encode_ledger
# 角色指令（C3，只发一次）增加一句说明：
#   "name[N]{a,b,c}: 表示 N 行、字段依次为 a,b,c 的表格。"
```

只影响账本这几块，不动群原文与契约。A1 的 composition 同时记录两种编码的估算长度，若实测不省就关闭（协作室设置 ledger\_table\_encoding，默认开启）。

## E5 输出简洁约束（借鉴 caveman）

caveman 的核心不是“说话像原始人”，而是用一条很短的系统指令换来输出长度的大幅下降。输出 token 单价通常是输入的 4–8 倍，而且成员的发言会被其他人反复读到，所以压短输出是“一次省、处处省”。这里只取它的思路：一句短指令 + 按发言类型的长度上限 + 实测。

```python
# role_instructions.py（主方案 C3，只在会话开头发一次，进入缓存前缀）
BREVITY = ("发言只写结论、依据和需要别人做的事；"
           "不复述题目和别人的话；不写客套、不写总结性重复；"
           "代码与命令照常完整。")              # 约 40 token，不再放长篇风格规则

# 按发言类型的字符上限，随讨论容忍度滑块（主方案 B6 TOLERANCE）缩放
KIND_CAP = {'opinion': 1200, 'vote': 300, 'review': 1600, 'summary': 200}
SCALE    = {1: 0.6, 2: 0.8, 3: 1.0, 4: 1.3, 5: None}       # 5 = 不限

def cap_for(kind, tolerance):
    s = SCALE[tolerance]
    return None if s is None else int(KIND_CAP[kind] * s)

# 每条成员发言写库之后
def after_reply(store, room, member, kind, text):
    store.record_output_size(room.id, member, kind, len(text), estimate(text))   # 进 A1 usage_records
    cap = cap_for(kind, room.tolerance)
    if cap and len(text) > cap * 1.5 and not store.reminded(room.id, member, kind):
        store.queue_note(room.id, member, f"上条{kind}发言约 {len(text)} 字，本类建议不超过 {cap} 字。")
        store.mark_reminded(room.id, member, kind)               # 每成员每类只提醒一次
```

规则：

- 原文永远完整入库，不截断。上限只用来提醒，以及作为 S1 是否必须附自述摘要的判断依据（超过上限的发言必须带摘要）。
- 执行者不加 BREVITY：它的输出是工具调用和契约结果，压缩会伤到正确性。
- 监工的最终裁决、契约文本不受上限约束。
- 开关：协作室设置 brevity（默认开），关掉后 BREVITY 和提醒都不发。

## E6 用量报表（借鉴 ccusage）

ccusage 的价值在于：把本地日志按天、会话、5 小时窗口拆开，并把缓存读和缓存写分开列出。这次 New folder 的 token 数字一开始算错，就是因为没有这样的报表，只能事后手算差值。这里直接基于主方案 A1 的 usage\_records 做报表，不另起数据源。

```python
# usage_report.py（新增，只读）
FIELDS = ('input', 'cached_read', 'cache_write', 'output', 'reasoning')

def normalize(provider, raw):           # 各家字段统一到 FIELDS
    if provider == 'codex':      # usage.total 是线程累计值：取与上一条的差值
        return delta(raw['total'], previous_total(raw['thread_id']))
    if provider == 'claude':
        return {'input': raw['input_tokens'], 'cached_read': raw['cache_read_input_tokens'],
                'cache_write': raw['cache_creation_input_tokens'], 'output': raw['output_tokens']}
    if provider == 'deepseek':
        return {'input': raw['prompt_cache_miss_tokens'], 'cached_read': raw['prompt_cache_hit_tokens'],
                'cache_write': 0, 'output': raw['completion_tokens']}
    if provider == 'gemini':     # 字段名实现前需对照 Interactions 返回实测确认
        return {'input': raw['input_tokens'] - raw.get('cached_tokens', 0),
                'cached_read': raw.get('cached_tokens', 0), 'cache_write': 0,
                'output': raw['output_tokens'], 'reasoning': raw.get('thought_tokens', 0)}

def report(store, room_id=None, by='member'):      # by ∈ member | session | day | window5h | kind
    rows = store.usage_records(room_id)
    groups = groupby(rows, key=KEYS[by])
    return [{by: k, **sum_fields(g), 'hit_rate': hit(g)} for k, g in groups]

def hit(g):
    s = sum_fields(g)
    total = s['input'] + s['cached_read']
    return round(s['cached_read'] / total, 3) if total else None

def window5h(record):           # 订阅制 CLI（Codex / Claude）的 5 小时额度窗口
    return floor_to(record.at, hours=5, anchor=first_record_in_window(record.member))
```

接入：

- 界面：协作室设置页加“用量”标签，默认按成员显示；另可切到按天、按会话、按 5 小时窗口、按发言类型（kind 来自 E5 的记录）。
- 命令行：scripts/usage\_report.py --room \<id> --by member --json，方便直接贴给 GPT 或 Claude 复核。
- 告警：某成员 hit\_rate 连续 3 轮低于 0.3 时，在活动流记一条“疑似缓存断点”，与 E3 的 prefix\_break\_suspected 合并显示，不重复报。
- 报表只读，不参与调度决策，避免统计口径错误影响讨论流程。

## 接入点与验收（第二部分）

| 增量 | 依赖主方案 | 改动文件 | 开关（默认） |
| --- | --- | --- | --- |
| E1 输出压缩与原文回取 | B4 工具结果裁剪 | output\_compress.py（新）、execution\_context.py | output\_compress（开） |
| E2 代码地图 | C1 监工载荷 | code\_map.py（新）、project\_tools.py | code\_map\_tokens=2000 |
| E3 前缀稳定 | A1 用量记录、C3 角色指令 | context\_turn.py、supervision.py | 无，属于修正 |
| E4 账本表格编码 | C1 监工载荷 | ledger\_encode.py（新）、supervision.py | ledger\_table\_encoding（开） |
| E5 输出简洁 | C3 角色指令、B6 容忍度 | role\_instructions.py、workflows.py | brevity（开） |
| E6 用量报表 | A1 用量记录 | usage\_report.py（新）、server.py、前端设置页 | 无，只读 |

建议顺序：E3 → E6 → E1 → E5 → E4 → E2。前两项先做，是因为它们提供测量手段，后面每一项的效果都要靠它们确认；E2 改动最大，放最后。

验收用例：

1. E1：执行者跑出 2 万行测试日志 → 送入上下文的不超过 6000 字，失败行与最后 40 行都在；调用 output\_read(id, 起止行) 能取回任意原文段；执行者读取其他成员的输出 → 拒绝。
2. E1：压缩后保留率低于 0.8 的规则 → 自动退回原文，并在 A1 中记录 compress\_fallback。
3. E2：在本仓库生成代码地图，不超过 2000 token；改动 engines.py 后再生成，engines.py 相关符号排在前面；没有 tree-sitter 时走 ast 和正则，不报错。
4. E3：连续两轮监工请求，若中间只追加了新事件，check\_prefix 的稳定段哈希相同；人为在稳定段插入时间戳 → 记 prefix\_break\_suspected。
5. E4：5 行证据表 → 表格编码比紧凑 JSON 短；2 行或含嵌套字段 → 保持 JSON；监工能正确引用表格中的某一行。
6. E5：容忍度 1 时 vote 上限 180 字；成员连续超长只提醒一次；入库原文长度与原始回复一致。
7. E6：用 New folder 的历史记录重算，总输入为 1,975,318、监工占 56.9%，与人工核对一致；Codex 累计值按差值计算，不重复累加。
8. 全部开关关闭时，行为与只实施主方案完全一致（回归测试 test\_git\_bridge 与 run\_context\_checks 全部通过）。

---

## 第三部分：直接调用的开源组件与端侧专用摘要模型

### 一、实现方式的取舍：能直接调用就不自己写

第二部分的六项改动，多数有成熟上游可以替代。取舍原则：

- **通用能力直接接入**：工具输出压缩、代码地图、表格编码、用量报表、工具输出沙箱、跨会话记忆、专用记忆压缩，一律使用官方包、MCP 服务或本地守护进程，不在宿主内重写算法。
- **业务语义自己实现**：S1–S3 的立场与表态隔离、交接包、角色权限、共识状态机，以及 E5 的按容忍度提醒这类房间级策略，没有通用上游，仍由宿主实现。
- **语义脆弱的部分尤其不要自研**：用正则裁剪日志或 diff、手写 AST 提取、手写账本转义，在边界情况上极易造成不可逆的数据损坏，而这类损坏事后无法通过回滚发现。

按这个原则，第二部分各项的落点可以对上（接口与验收不变，只换实现来源）：

| 项 | 本文第二部分的宿主实现 | 可直接调用的上游 |
| --- | --- | --- |
| E1 工具输出压缩与回取 | output_compress.py | Headroom（可恢复压缩） |
| E2 代码地图 | code_map.py | aider 的 RepoMap |
| E4 账本表格编码 | ledger_encode.py | TOON 官方序列化器 |
| E6 用量报表 | usage_report.py | ccusage |
| 新增：专用记忆压缩 | — | CoMem 路线（本地小模型） |
| 新增：工具输出沙箱 | — | Context Mode（MCP 服务） |
| 新增：跨会话记忆 | — | Claude-Mem（插件或本地服务） |

判断标准只有一条：上游能覆盖这项需求的主要场景、且效果可回退，就直接接入；覆盖不到房间语义的，留在宿主。直接调用不等于零成本——把外部组件接进 payload 的代价，在第五部分单独核算。

### 二、三类摘要模型路线

| 路线 | 核心机制 | 优势 | 主要问题 | 代表 |
| --- | --- | --- | --- | --- |
| 生成式摘要 | 小模型把历史改写成一段摘要 | 可读性好，容易训练 | 容易丢细节 | 普通 instruct 模型 |
| Token 级压缩 | 判断哪些 Token 值得保留 | 快，适合放在输入管线最前面 | 可能破坏代码语法与结构 | LLMLingua-2 |
| 行动一致性训练 | 以"压缩后下游能否正确行动"为训练目标 | 更贴合 Agent | 训练与评估都更复杂 | CoMem |

两个要点：

- **上下文窗口大，不等于压缩能力强**。窗口决定"能读多少"，不决定"该留什么"。把 4B 模型的窗口开到 128K、256K，KV Cache 与推理开销会一起涨，并不会因此变得更会挑信息。
- **通用模型优化的是"读起来顺"**，Agent 需要的是"下游还能不能做对决定"。这两件事经常冲突：一段读起来很顺的摘要，可能恰好把"这个方案已经失败过"删掉了。

第二部分原先以"原文直接共享"排除了 Token 级压缩。该禁令已取消（见第六部分）：压缩可以作用于成员发言，但必须与原文一同投喂、由模型自选、并留下摄入记录。算法本身仍只建议嵌在工具输出这一层——成员发言上的压缩是"可选"，不是"默认"。

### 三、CoMem：以行动一致性为训练目标

- **结构**：专用 4B 记忆模型 + 主模型。记忆模型把长历史压成任务状态，主模型只做决策，两者解耦。
- **训练目标**：压缩之后，主模型能否作出与全量上下文一致的行动。奖励来自下游行为，不是摘要文笔。这解释了它为什么更贴合 Agent——它被优化去保留"会改变决策"的信息。
- **工程形态**：k-step-off 异步流水线，记忆模型在后台压缩，主 Agent 不等待。这一条对本地部署是关键：同步等待等于把压缩延迟直接加进每轮响应，4B 模型处理几千 token 是秒级开销，而在一个多成员讨论里，"每轮多等几秒"会直接改变使用体验。
- **采用前须核对的两点**（已核对）：
  - 公开权重在 Hugging Face 的 `YWZBrandon/*` 名下（SFT 与几个 GRPO 变体），不在项目组织名下，需要自行转换与量化；
  - 公开口径是"约 1.4 倍延迟收益 + 保留大部分性能"，SWE-Bench-Verified 上三个主干里有两个的解决率下降约 6 个百分点。

  结论：它是**研究起点**，不是即插即用的组件。接入前先用本房间自己的历史跑一次忠实度自测（口径见第五部分第十节）。

- **硬件前提**：本机显卡是 RTX 5080（16GB），不是 32GB。4B 模型 4-bit 量化约 2.5–3GB，可用；按 bf16 直接加载约 8.8GB，会挤掉 KV Cache。部署一律走量化路径。

### 四、Coding Agent 里真正要保留的信息

以下五类，删掉任何一类都会让下游重做：

1. 已修改的文件路径与关键代码位置；
2. 当前故障的准确报错原文；
3. 已经失败并排除的修复方案；
4. 测试结果与未完成事项；
5. 用户明确提出的约束。

推论：**用普通小模型反复"总结聊天记录"，结果常常比结构化记忆更差**——它没有被训练成"这五类必须保留"，只会被训练成"写得像摘要"。这也说明为什么第一部分的作者自述摘要要带固定字段（立场/要点/未决），而不是让模型自由发挥。

### 五、本地部署与回退

| 组件 | 建议 |
| --- | --- |
| 本地推理服务 | llama.cpp / Ollama，统一走量化后的 GGUF |
| 专用压缩模型 | 从 CoMem 的 4B 路线起步，作为研究项而非依赖项 |
| 压缩算法参考 | Token 级压缩只放在工具输出层，不碰群原文与契约 |
| 中间层 | Headroom 这类可恢复压缩，原文始终可回取 |
| 持久记忆 | 本地数据库 + 原文文件索引，宿主自己掌握路径 |
| 云端主模型 | 保持现有成员不变，不在这一步换模型 |
| 安全回退 | 本地不可用时退回 S1/S2 自述摘要，不阻塞主流程 |

两条工程约束：

- **不要每轮重读全史**。可行做法是分块压缩、增量更新、按需回读；持续把整段历史喂给本地模型，等于把成本从云端搬到本地，并没有省掉。
- **回退必须计数**。静默降级是这类方案最大的隐患：服务挂了三个月，账面上一直"在省"，实际一直在用退路。

### 六、接入点（第三部分）

| 组件 | 接入方式 | 宿主触点 | 默认 |
| --- | --- | --- | --- |
| CoMem 本地记忆服务 | 本地推理服务直连 | 组包前生成记忆块 | 关（需端侧服务就绪） |
| Headroom | 本地代理或中间层 | 工具输出侧、或 provider 出口 | 关，灰度后再开 |
| Context Mode | MCP 服务 | 执行者工具面 | 关（会扩大执行者能力面，须先改主方案边界） |
| Claude-Mem | 插件或本地服务 | 会话启动与结束 | 关 |
| 可选的 Token 级压缩 | 算法库调用 | 仅工具输出层 | 关 |

这些组件本身不产生主模型的输入 token，但会改变 payload 的形状与长度，因此每一项都要记入 A1 的 composition，否则净账永远算不出来。

---

## 第四部分：按数据流位置分类

### 一、五层定义

| 层 | 位置 | 优化对象 | 典型手段 |
| --- | --- | --- | --- |
| ① 上下文输入 | 发给主模型之前 | 历史消息、系统指令、记忆、代码快照 | 摘要、筛选、代码地图、记忆注入 |
| ② 工具输入 | 工具调用之前 | 参数、读取范围、检索策略、执行方式 | 沙箱重定向、批处理、按需读取 |
| ③ 工具输出 | 工具返回之后、进入模型之前 | 日志、JSON、diff、网页、命令输出 | 分类压缩、结构化折叠、原文索引 |
| ④ 生命周期与记忆 | 跨轮、跨会话 | 长期历史、操作记录、结论 | 外部存储、分层摘要、按需检索 |
| ⑤ 模型输出 | 主模型生成时 | 客套、复述、冗余总结 | 简短输出指令、输出长度限制 |

### 二、项目覆盖矩阵

符号：● 核心功能　◐ 次要或间接支持　— 不属于主要功能

| 项目 | ① 上下文输入 | ② 工具输入 | ③ 工具输出 | ④ 长期记忆 | ⑤ 模型输出 |
| --- | :---: | :---: | :---: | :---: | :---: |
| CoMem | ● | — | ◐ | ● | — |
| Token 级压缩（LLMLingua-2） | ● | — | ● | — | — |
| Headroom | ● | ◐ | ● | ◐ | ● |
| Context Mode | ◐ | ● | ● | ◐ | — |
| Claude-Mem | ● | ◐ | ◐ | ● | — |

两点读法：这张表只说功能覆盖，不代表默认启用；另外 ② 层的"工具输入"专指调用方式本身（参数、范围、策略），不包括工具返回的内容。

### 三、每层优化什么

- **① 压缩已有知识**：一百多 K 的历史消息进来，输出几千 token 的结构化记忆。CoMem 侧重记忆压缩，Token 级压缩是可插入管线的细粒度算法，Claude-Mem 重在跨会话检索。
- **② 改变工具怎么工作**：原本 `cat huge.log` 会把整份日志灌进上下文，改成在沙箱里解析、只回错误统计与异常片段。**这一层的节省来自改变调用路径，不是训练摘要模型**，因此它同时是开销最高的一层（见第五部分）。
- **③ 拦截大块数据**：一次 API 返回 50,000 token 的 JSON，先筛关键字段、折叠重复，原结果缓存待取。Headroom 与 Context Mode 在这一层重叠最多。
- **④ 避免历史重复进入**：Claude-Mem 用会话钩子捕获操作与决策，压缩成可检索的条目；后续按需检索少量条目，而不是载入整段对话。
- **⑤ 限制不必要的生成**：针对主模型自己产生的 token。要与会后截短区分开——截短追不回已经付过的输出费用。

Context Mode 的特点是要同时占 ② 和 ③：先在调用前把工具引导进沙箱，再在沙箱内处理大结果。

### 四、组合互补性

| 组合 | 实际覆盖 | 互补程度 |
| --- | --- | --- |
| CoMem + Token 级压缩 | 长期记忆 + 输入细粒度压缩 | 中，存在重叠 |
| Headroom + Context Mode | 工具执行路径 + 工具结果压缩 | 高，但有重叠 |
| Claude-Mem + Headroom | 跨会话记忆 + 当前会话数据压缩 | 高 |
| CoMem + Context Mode | 历史记忆 + 工具 I/O | 很高 |
| 全部一起 | 覆盖多数环节 | 理论最全，重复压缩与相互覆盖的风险最高 |

**组合越多，越需要一条仲裁规则**：同一段内容只允许被压一次，且以最先拿到原文的那一层为准；后一层不再改写前一层的结果。没有这条规则，全部一起上就是反效果。

### 五、推荐的三层结构

1. **工具调用之前**：用 Context Mode 这类沙箱把大输出挡在产生之前，尽量不让它进入模型。
2. **进入上下文之前**：用 Headroom 这类可恢复压缩处理必须进来的工具输出，原文留在本地。
3. **跨轮与跨会话**：用 CoMem 路线或 Claude-Mem 处理历史与记忆。

顺序原则：**先阻止无用 token 进入，再压缩必须进入的内容，最后管理跨轮历史**。这个顺序比对整个 prompt 反复做摘要更稳定，也不会破坏前缀。

可选算法（Token 级压缩）只嵌在合适的那一层，不要叠加到每条消息上。

---

## 第五部分：净节省核算、二维分类与评估口径

前四部分都在讲"能省多少"，这一部分讲"省下来的会不会被吃回去"。

### 一、净节省

   净节省 Token = 避免的 Token − 新增的 Token

只看压缩率是不够的。任何一个需要主模型配合的方案，都要先把新增开销扣掉再看收益。

### 二、新增开销的五个来源

1. 优化规则、系统提示与工具说明的常驻占用；
2. 新增工具的 Schema 与参数定义；
3. 模型为路由、检索、重试而多产生的规划与调用；
4. 压缩之后为了核对而发起的回取请求及其结果；
5. 为维持记忆而反复注入的摘要本身。

### 三、谁需要向主模型解释规则

| 优化位置 | 是否需要解释规则 | 新增输入成本 |
| --- | --- | --- |
| 透明式上下文压缩 | 不需要 | 很低 |
| 透明式工具输出压缩 | 不需要 | 很低 |
| 工具调用前的主动优化 | 通常需要 | 中～高 |
| 让模型主动使用记忆检索 | 通常需要 | 中 |
| 自动注入相关记忆 | 不需要说明规则 | 记忆内容本身有成本 |
| 模型输出行为优化 | 通常需要 | 中 |
| 在 API 层直接限制输出长度 | 不需要 | 接近零 |

### 四、主动式与透明式的差别

- **主动式工具优化**：主模型要先拿到一段规则（"可能返回大量内容时优先调用沙箱……"），这段规则每轮都在上下文里。它把成本从工具输出转移到了系统提示，而且依赖模型遵守。
- **透明式工具输出优化**：主模型照常调用原有工具，宿主在事后过滤。主模型不需要知道压缩是怎么发生的。

在保留质量相近的前提下，透明式的成本结构更好。这也是第三部分把通用能力尽量放到工具输出侧的原因。

### 五、逐项目开销核对

| 项目 | 额外指令开销 | 额外工具定义 | 净节省潜力 |
| --- | --- | --- | --- |
| CoMem | 低 | 低 | 高，取决于历史长度 |
| Token 级压缩 | 极低 | 无 | 高，尤其是长输入 |
| Headroom | 低；部分功能需要 | 启用可恢复检索时会多一个检索工具 | 高 |
| Context Mode | 中～高 | 有 | 高度依赖任务 |
| Claude-Mem | 低～中 | 记忆检索工具 | 长会话时更明显 |

三个容易漏掉的细节：

- Headroom 的普通压缩对主模型基本透明，但启用可恢复检索会注入一个回取工具，启用输出简洁引导会往系统提示尾部加一句短指令；
- Context Mode 的调用前拦截可以阻止、改写调用，也可以向模型追加指导；它自己还专门设计了"减少重复指导"的机制，说明这类路由开销确实值得控制；
- Claude-Mem 会把相关记忆注入后续会话，减少了对完整历史的依赖，但注入的记忆本身仍然占输入上下文。

### 六、一个反例

假设某个中间层每轮新增 1,000 token 的规则与工具说明，平均每 5 轮才成功省下一次 3,000 token 的工具结果，任务跑 20 轮：

- 新增输入：1,000 × 20 = 20,000 token；
- 节省输入：3,000 × 4 = 12,000 token；
- 净结果：**多花 8,000 token**。

这不是极端假设。任何"每轮常驻、偶尔见效"的优化都会落在这个形状里。

### 七、缓存折价的例外

如果新增的规则位于可稳定缓存的前缀里，实际增加的费用会明显低于上面算出的 token 量——因为命中缓存的部分按折扣价计。但要注意两点：

- **上下文占用并没有减少**，只是单价降低；预算与延迟的限制依然存在；
- 一旦规则或工具定义发生变动，前缀失效，这笔折扣立刻消失。这正是第二部分 E3 要守住的东西。

### 八、更坏的情况：执行路径被改变

比"多花 token"更糟的是：额外的工具调用改变了 Agent 的执行路径。模型多走一轮推理、多调一次工具、多问一次澄清，即使输入 token 下降了，输出费用与总延迟仍可能上升，任务成功率也可能下降。

所以净节省必须和"轮数、返工次数"一起看，不能只比输入 token。

### 九、二维分类

单看"优化了哪部分 token"不够，还要看"靠什么机制优化的"。

| 机制 | 主模型是否需要主动配合 | 典型例子 |
| --- | --- | --- |
| 透明拦截 | 否 | 自动压缩工具输出 |
| 静态规则注入 | 是 | 指导模型少输出、少读文件 |
| 工具 Schema 扩展 | 是 | 新增检索或沙箱工具 |
| Hook 强制路由 | 不一定 | 拦截并改写工具调用 |
| 外部模型处理 | 否 | 本地小模型压缩历史 |
| 按需记忆检索 | 通常是 | 模型主动检索历史记忆 |

最值得区分的是 **Hook 强制路由与 Prompt 指导路由**：前者由程序直接执行，不必给模型解释一大段规则；后者依赖模型遵守，必然产生提示成本。

这也给出了本地专用模型的额外优势：**它在主模型之外独立运行，不要求主模型学习一套压缩协议**。但如果它需要新增一个检索工具，这笔 Schema 与往返开销仍要计入。

### 十、四个独立指标

比较任何一项优化，都用这四个各自独立的指标，不用项目宣传的压缩率排名：

1. **净 Token 节省率**；
2. **净 API 费用节省率**（计入缓存折扣与输出单价差异）；
3. **任务成功率**（同一任务、同一成员、同一适配器）；
4. **总执行延迟**（含本地推理与回取往返）。

这四项要一起报，缺一项就不能下结论。第五部分第一节的公式只覆盖第一项，第二项还要乘上各自的单价。

---

## 不变的边界（补充）

前五部分新增了外部记忆与外部压缩，以下边界不随之改变：

- **成员发言不再禁止压缩，但原文必须同时可得**：压缩版与原文一并投喂，由模型自选，摄入行为留记录（见第六部分）。原文永不删除，改变的只是"默认投给模型的是哪一版"。
- **外部记忆块与自述摘要同等对待**：CoMem 的记忆块、Claude-Mem 的 observation、Headroom 的跨会话记忆，都是宿主或第三方模型对成员发言的二次综述，既不是原文也不是作者自述。它们只能用于"了解大意"，**不得作为表态、批准或证据被引用**；监工计算共识时只看表态行与账本。
- **记忆块要绑定方案版本**：方案版本变化后，旧记忆块作废重建，不得继续引用。
- **原文始终可回取**：任何压缩都不改变原文入库，读取接口与权限校验照旧。
- **执行者仍只收契约**：不为接入沙箱而把群聊原文或摘要交给执行者；若某项组件需要扩大执行者的能力面（例如可执行任意代码的工具），应当先改主方案的边界，而不是夹在 token 优化里悄悄放开。

## 验收用例（第三至第五部分）

1. **直接调用后行为对齐**：E1 换成 Headroom 后，同一份命令输出在送入上下文前被压缩，且原文可回取；执行者读取其他成员输出仍被拒绝。
2. **代码地图不空**：E2 换成 RepoMap 后，对一个已知仓库返回非空且按重要性排序的签名地图，长度在预算的 1.15 倍以内；聚焦文件若未随消息发出，必须仍出现在地图中。
3. **表格编码确实更短**：E4 换成官方序列化器后，对规整的 5 行证据表输出更短；对嵌套或不规则账本回退为紧凑 JSON；监工能正确引用表中某一行。
4. **用量可对账**：E6 换成 ccusage 后，其输出与 A1 的 usage_records 在同一时间窗内差额小于 1%；按房间、按角色的归因仍以 A1 为准。
5. **净节省为负时自动关停**：任一外部组件连续两周在 A1 中算出的净节省为负，或回退次数超过阈值，自动关闭并在活动流记一条事件。
6. **记忆块不得越权**：构造一个记忆块把"有保留的同意"写成"同意"，确认共识仍按表态行计算，界面标出不一致。
7. **回退可观测**：停掉本地记忆服务，系统退回 S1/S2 自述摘要且不阻塞；回退计数在报表中可见。
8. **全部关闭后的回归**：所有外部组件关闭时，行为与只实施第一、二部分完全一致，test\_git\_bridge 与 run\_context\_checks 全部通过。

---

## 第六部分：原文与摘要的双投递、自选摄入与摄入记录

### 一、原则变化

- **旧原则**：原文一律直投；摘要只用于"了解大意"的四个场合；任何压缩都不作用于成员发言与方案正文。
- **新原则**：原文与压缩版都做成可得；宿主决定**投喂档位**，模型决定**这一轮摄入哪一版**；原文永不删除；所有压缩都必须可回取；摄入行为全程留痕。

这条修改了主方案"讨论成员之间的公开原文由宿主直接投递"的边界。**主方案须先同步修订**，否则两份文档冲突；这是本文唯一一处越出"只挂接口"的地方。

### 二、三档投喂

| 档位 | 投给模型的内容 | 是否省 token | 默认场合 |
| --- | --- | --- | --- |
| `full` | 原文全文 | 不省 | 用户消息、宿主错误、本轮新回复、表态轮、执行证据 |
| `dual` | 摘要 + 原文全文 | **不省，反而多** | 评审、异议、方案对照这类要判断又怕误读的场合 |
| `menu` | 摘要 + 编号/哈希指针 | 省 | 较早轮次的一般发言、未加载清单、交接包 |

要点必须写死：**只有 `menu` 档的"自主选择"真的省 token**。`dual` 档两份都在上下文里，省的是模型的注意力与误读概率，不是钱。把这两件事混起来，就会得出"双投递能降成本"的错误结论。

### 三、"自主选择"落在哪一步

- `dual` 档：模型自己决定以哪一版为准，宿主不干预；但两份都已计费、都已占上下文。
- `menu` 档：模型调用读取工具取全文，宿主按编号回取。**选择发生在 payload 之后，不改变已经发出的前缀**，因此不击穿 E3。
- 结论：自主选择的实现方式是"可寻址 + 按需回取"，不是"两份都塞进去让模型挑"。

### 四、档位必须冻结

同一分支内，每条消息的档位在消息入库时算一次并随消息记录，分支内不再改变。理由：档位一变，payload 的字节序列就变，E3 的前缀缓存当场失效，省下的会被缓存损失吃回去。要换档位就开新分支，并在交接包里写明换档原因。

### 五、预算闸门与降档

- `dual` 档只在本轮输入预算的 30% 以内使用；超出部分自动降为 `menu`。
- 降档由宿主执行，不需通知模型，但要在前端与投递记录里可见，并统计"因预算降档"的条数。
- 反向也要有：若某房间长期出现"`menu` 回取率极高"，说明摘要质量不足或该场合不该降档，应把默认档位改成 `dual`。

### 六、摄入记录与前端

每条消息、每一轮投喂记一行：

- 房间、轮次、消息编号、档位（full / dual / menu）、投喂字数与估算 token；
- `menu` 档是否被回取、回取时间、回取范围；
- 该轮回复是否引用了这条（以编号是否出现在回复中作弱信号，只作参考，不作判定）。

前端：

- 消息卡片底部显示"本轮投喂：档位／被回取 N 次"；
- 上下文投递记录页增加"档位"与"回取"两列，原来的"全文／摘要／仅编号"三项计数并入这两列；
- 按房间统计 `menu` 回取率，作为摘要质量与档位设定的常规指标。

计量（接入 A1）：composition 增加 `feed_full`、`feed_dual`、`feed_menu` 三项计数与各自估算。没有这三项，净账算不出来。

### 七、不随政策改变的三条

1. **表态与共识仍由宿主解析表态行**，与模型读到哪一版无关。共识不能因为模型读了摘要而改变。
2. **证据与批准仍以原文与真实记录为准**。压缩版可以投喂，不能被引用为证据。
3. **执行者仍只收契约**，不接触群聊、摘要或记忆块。

这三条约束的是"解析与授权"，不是"投喂"，与本次原则变化不冲突。

### 八、失效模式（做之前先认下来）

- 双投递不加预算闸门 → 输入明显上升，净节省可能为负；
- 档位不冻结 → 缓存全废，省下的被缓存损失吃回去；
- 只记"投喂"不记"回取" → 无法判断模型到底看了哪一版，整套机制等于没做；
- 把 `dual` 当省钱手段 → 方向性错误，`dual` 的收益在返工率，不在 token。

---

## 第七部分：实验路线的观测契约（监控后门）

### 一、原则：没有埋点不得开启

凡是"需要实测才能取舍"的路线，上来就要留观测手段，而不是事后补。**默认规则：没有埋点的功能不得开启，哪怕是灰度。**

理由是这条路线本身的特性：它的收益只有实测能定；而事后补埋点意味着原始样本已经过去，无法复算。三个月后你只会知道"它开着"，不会知道"它省没省"。

"后门"在这里指旁路埋点、对照开关、样本留存与一键回退，不是安全意义上的后门；它不改变主链路行为，关掉后不影响功能。

### 二、五件必备

| 项 | 要求 |
| --- | --- |
| 旁路计数 | 输入/输出规模、压缩比、关键行保留率、回退次数、延迟 p50 与 p95 |
| 对照开关 | 可按房间或按成员二选一，同一任务对照；不做随机化，保证可复现 |
| 样本留存 | 脱敏后的原始 payload 与压缩结果，默认各 200 条，本地存储，默认保留 14 天，可一键清空 |
| 归因字段 | 写进 A1 的 composition，能分别回答"省了多少"与"花了多少" |
| 退出路径 | 一个开关关掉即回退旧实现，回退后行为与开启前逐字节一致 |

### 三、必须开监控的路线

| 路线 | 要回答的问题 | 预置决策阈值（示例） |
| --- | --- | --- |
| Headroom 在工具输出侧的真实压缩比 | 压缩比与关键行保留率的分布 | 中位压缩比 < 30% 或保留率 < 100% → 关 |
| aider RepoMap 的覆盖效率 | 同预算下覆盖的定义数 | 低于自研版 80% → 关 |
| 双投递（第六部分） | 额外 token 与返工、回取次数的变化 | 净 token 为负且返工未降 → 降为 menu |
| CoMem 记忆块 | 关键事实保留率与下游决策一致率 | 一致率 < 95% → 关 |
| Context Mode 沙箱 | 拦截成功率、误拦率、执行者能力面变化 | 误拦 > 5% → 关 |
| ccusage 对账 | 与 A1 同期总量的差额 | 差额 > 1% → 报警，不接管主链路 |
| TOON 编码 | 逐字段长度对比 | 当场可比，不必常驻监控 |

阈值是预置的默认值，可以按房间改；但**必须先有阈值再看数据**，否则会变成"看到数字再找理由"。

### 四、开启顺序

先把监控打开 → 灰度一个房间 → 拿到对照数据 → 再决定默认值。反过来（先开功能，再想怎么测）是这类方案最常见的失败方式，也是本文第三、第五部分反复强调"实测为准"的原因。

### 五、留存与隐私

- 样本目录放在宿主状态目录下，不进项目仓库、不进 git、不上传；
- 留存前脱敏：凭据、绝对路径中的用户名、`.env` 内容一律不落盘；
- 默认 14 天自动清理，房间可手动清空；
- 前端提供"查看留存样本"的入口，只有用户可见，模型不可调用。

### 六、验收

- 任何默认开启的外部组件，都能在 A1 中查到它的净节省与回退次数；
- 关掉监控开关不影响主链路，但会在活动流记一条"无观测运行"，提醒当前数据不可用于取舍；
- 抽样复跑：从留存样本中取 20 条，离线重放，压缩结果与线上一致。

---

## 验收用例（第六、第七部分）

1. **三档不串**：同一房间同时存在 `full`、`dual`、`menu` 三种投喂；投递记录中每条消息的档位与实际 payload 一致，且三项计数之和等于投喂总数。
2. **档位冻结**：同一分支内连续三轮，同一条消息的档位不变；人为改档位后，`check_prefix` 记录到前缀断裂，而不是静默重算。
3. **`menu` 回取生效**：模型调用读取工具取回某条全文后，该条从"仅摘要"变为"已回取"，前端计数加一，A1 中 `feed_menu` 与回取次数分别可见。
4. **预算降档**：把本轮预算压到只够 `menu` 的水平，确认 `dual` 被自动降档，且"因预算降档"计数正确。
5. **表态不受影响**：摘要写"支持"、原文写"反对但可接受"，表态行写"反对"；共识仍记为反对，前端标出摘要与表态不一致。
6. **没有埋点不能开**：把某个外部组件的监控开关关掉再尝试启用它，系统拒绝启用并记录一条事件。
7. **对照可复现**：同一任务在两个房间分别开启与关闭 Headroom，两边的旁路计数都可导出，样本条数与留存天数符合设置。
8. **一键退出**：关闭某组件后重跑同一任务，payload 与开启前逐字节一致（用 E3 的哈希对比验证）。
