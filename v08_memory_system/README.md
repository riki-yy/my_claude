# V08：Memory System

V08 在 V07 已验收的 Agent Loop、Tool Runtime、Permission、Hooks、Todo、Observability、checkpoint/resume、Context Budget、MicroCompact、Full Compact 和 Prompt Too Long Recovery 上，增加基于本地文件系统的跨 Session 长期 Memory。

Memory 只保存未来 Session 仍有价值的稳定知识，例如用户偏好、已确认反馈、项目约定、架构事实和可复用参考信息。当前任务进度、Todo、一次性路径、完整对话、大段源码和未经确认的推测仍由当前 Session 的 messages、Todo、Run 与 checkpoint 表达，不写入长期 Memory。

V08 不实现 Vector DB、Embedding、RAG、Memory Graph、importance score、TTL/decay、后台 worker、异步任务、durable Memory workflow、current-task Memory、完整会话归档或自动修改 `AGENTS.md`。

## 启动与测试

在项目根目录准备共享 `.env`，然后：

```bash
cd v08_memory_system
python3.12 -m pip install -r requirements.txt
python3.12 -m pytest -q
python3.12 agent.py
```

恢复方式继续沿用 V06/V07：

```bash
python3.12 agent.py --resume state/<session_id>.json
```

Memory Store 固定在当前版本目录：

```text
v08_memory_system/
└── memory/
    ├── MEMORY.md
    └── <memory-id>.md
```

`MEMORY.md` 只包含 Selector 所需的轻量索引，不保存正文。每条正文独立存入 `<memory-id>.md`。索引格式固定为：

```yaml
# Memory Index

- name: explanation-order-preference
  description: 用户偏好解释 Agent 代码时，先讲调用链和设计意图，再讲具体代码细节
  type: user

- name: project-test-command
  description: 项目完整测试统一使用 python -m pytest -q
  type: project
```

每条索引只保留 `name / description / type`。`name` 与正文文件名 stem 一致，三个字段也必须与正文 YAML frontmatter 完全一致；`id / title / summary / created_at / updated_at` 不写入索引。

## 完整调用链

每个新的 User Request 最多执行一次 Recall；Agent 内部 Round、Tool Result、普通 retry 和 PTL Recovery 不重复召回：

```text
New User Request
        ↓
Recall：User Request + MEMORY.md index
        ↓
Selector：选择 0～5 个 Memory ID
        ↓
校验并读取独立正文文件
        ↓
构造本 Run 临时 system context
        ↓
Main Agent Run（继续使用 V07 request preflight）
        ↓
最终无 tool_use Assistant Response
        ↓
Extract：完整 active messages + MEMORY.md index + scope prompt
        ↓
Validate / Deduplicate / Atomic Store
        ↓
全部 Candidate 成功后推进 cursor
        ↓
达到阈值时低频 Consolidate
```

Recall 产生的 Memory context 只存在于当前 `run_once()` 调用链，不追加到 conversation messages，不写入 checkpoint，也不会进入 V07 Structured Summary。实际 Main Agent 请求和 V07 Context Budget 使用同一份“基础 System Prompt + Recall Memory”，避免估算内容与真实请求不一致。

## Memory 类型与文件格式

第一版只允许四种类型：

- `user`：稳定用户偏好；
- `feedback`：用户已确认的纠正或反馈；
- `project`：项目约定、架构事实和固定工作方式；
- `reference`：未来可以复用的参考信息。

Extractor Candidate 继续使用稳定 ID、固定类型、短标题、索引摘要和非空正文的既有模型 schema。Store 将其最小映射为 `name / description / type` 索引，并校验 name/文件名安全性、字段结构、类型、正文大小及重复/冲突；任意类型扩展、路径穿越、空正文、超限正文或相同 name 的冲突内容都会被拒绝。

正文文件使用只含 `name / description / type` 的 YAML frontmatter 与独立 Markdown 正文。索引保存相同三个字段，不复制正文。Recall 只有在索引引用与正文文件同时存在、结构合法且 frontmatter 一致时才把内容注入模型。

## Recall 与 Selector

Selector 输入只有当前 User Request 和 `MEMORY.md` 索引。正常路径使用一次 LLM 语义选择，要求：

- 不确定时返回空列表；
- 最多选择 5 个 ID；
- 不重复 ID；
- 不编造索引中不存在的 ID。

Selector 调用失败、响应格式非法、返回未知/重复/超量 ID 时，Runtime 使用确定性的简单文本匹配 fallback，不使用向量能力。fallback 按请求词与索引 name、description、type 的重合度排序，再以 name 保证稳定顺序，最多返回 5 条。

选中正文缺失、损坏或与索引不一致时，该条内容不会进入 system context；Runtime 记录 `memory.recall.item_failed`，Main Agent 在不使用该条 Memory 的情况下继续。

## Extract、cursor 与请求预算

Extract 只在一个 Run 已正常得到最终、非 `tool_use` 的 Assistant Response 后同步执行一次。模型/API/协议错误、权限等待、中断、`MAX_ROUNDS_EXCEEDED` 和其他未完整结束路径不 Extract。

Session State 顶层新增唯一字段：

```text
last_memory_message_index: integer | null
```

它表示上一次 Extract 与本轮全部 Store 成功后，最后一条 model-visible active message 的零基下标。它不是 Memory phase、operation ID 或额外 transcript。

Extract 始终接收 Run 结束时的完整 active messages，不创建第二套 history slicing。cursor 只用于计算最近 `N` 条消息，并在尾部 prompt 中限定新 Memory 的来源：

```text
Analyze the most recent ~{N} model-visible messages above
and use them to update persistent memory.

You MUST only use content from the last ~{N} messages
as the source of new or updated memories.

Earlier conversation may only be used to:
- understand context
- resolve references
- avoid semantic duplicates
```

完整 active messages、索引和尾部 prompt 都计入 Extract 自身预算。估算达到 `EXTRACT_SAFE_INPUT_TOKENS=96000` 时本次 Extract 明确失败，不调用 Extractor、不裁剪、不递归 compact，cursor 保持不动；后续 Main Agent 的 V07 Full Compact 可以自然缩短 active messages。

Extractor 返回空 Candidate 列表也是成功处理，cursor 可以推进。只要任意 Candidate 校验或 Store 失败，cursor 就不推进；此前已经 committed 的 Candidate 保留。下次成功 Run 会重新覆盖旧 cursor 后的消息，通过稳定 ID、正文与摘要语义去重跳过已提交项，再继续处理未完成项。

Memory 后处理是 best-effort：Extract、Store、cursor checkpoint 或 Consolidate 失败会产生事件，但不会把已经成功的 Main Agent Run 改成失败。

## 原子 Store、失败回滚与清理

单条 Store 的提交顺序为：

```text
Candidate
→ Validate
→ write same-directory .tmp
→ flush/fsync/read-back verify
→ atomic replace <id>.md
→ build MEMORY.md temp
→ flush/fsync/read-back verify
→ atomic replace MEMORY.md
→ committed
```

Store 状态边界：

- `.tmp-*`：未提交临时文件，可以清理；
- 正文合法且 `MEMORY.md` 有一致引用：committed。

正文与对应索引项共同构成单条 Candidate 的提交边界。如果本次新建正文已经成功落盘、索引更新随后失败，Runtime 将当前 Store 判定为失败，先恢复 Store 前的索引，再删除本次新建正文及相关 `.tmp`；此前 committed 的正文与索引不受影响。若旧索引也无法恢复，则停止删除正文并明确报告 rollback failure，避免主动制造索引引用缺失正文。cursor 保持旧值，下一次成功 Run 后从旧 cursor 覆盖的 active history 重新 Extract、Validate 和 Store。

进程在正文 atomic rename 后、索引提交或异常回滚前被强制终止，仍可能留下 crash orphan。它未 committed、不参与 Recall，只作为未提交残留清理，不用于补建索引或恢复 Candidate。

## Consolidate

Consolidate 只在 committed 条目数达到 50，或索引达到 24,000 字符时低频触发，不在每个 Run 或每次 Store 后无条件执行。允许的动作只有去重、合并和剪枝。

执行前先验证当前 Store 并创建 snapshot；模型生成完整候选 Store 后，Runtime 在 staging 目录逐条执行同样的结构校验、重复检查、正文/索引一致性检查，再替换正式 Store。任一步失败都会 rollback 到 Consolidate 开始前的 committed 状态，因此本轮刚刚成功 Store 的内容不会因整理失败而消失。成功 snapshot 只保留有限数量。

## 与 V07 Compact / V06 Resume 的边界

- Tool Result Budget 与 MicroCompact 只原位缩减消息内容，不改变 cursor；
- Full Compact 用新 Structured Summary 替换旧 prefix，因此必须在同一次 checkpoint 中把 cursor 重置为 `null`；
- Full Compact 或 checkpoint 失败时，messages 与 cursor 一起恢复；
- reset 后的下一次 Extract 使用 compact 后全部可见 messages 作为最近 `N` 条；
- Recall context 不进入 messages，因此 Full Compact 不读取、总结或持久化长期 Memory；
- unfinished Run resume 时不重新 Recall；同一 Run 正常完成后才执行一次 Extract；
- Run completed checkpoint 与 cursor checkpoint 之间崩溃时，旧 cursor 会让下次 Extract 保守重处理，Store dedup 防止重复条目。

## Observability

V08 复用现有 CLI/JSONL，新增或补强以下事件：

- `memory.recall.started/completed/failed`；
- `memory.selector.failed` 与 selector path `llm/fallback`；
- `memory.recall.item_failed`；
- `memory.extract.started/completed/failed`；
- `memory.store.added/skipped/failed`；
- `memory.cursor.advanced/reset`；
- `memory.consolidate.triggered/snapshot/committed/rollback`。

事件记录索引候选数、选中数、最近消息数、Candidate 数、Store outcome、cursor 状态、阈值结果及错误类型，不记录完整 Memory 正文、完整 selector/extractor prompt 或敏感内容。

## 自动测试

自动测试继续在生产代码的 `client.messages.create` 表面使用 Fake Model，不读取 `.env`、不访问网络、不调用真实 API。

覆盖范围包括：

- V07 的 Agent、Tool、Permission、Hooks、Todo、state/resume、retry、pairing、Context Budget、MicroCompact、Full Compact 和 PTL Recovery 回归；
- 每个新请求 Recall 恰好一次，多 Round/Tool 不重复 Recall；
- 0～5 条选择边界、非法/未知/重复 ID、LLM failure 与文本 fallback；
- 正文缺失、损坏、frontmatter 与索引不一致时不注入；
- Recall Memory 只进入临时 system context，messages 中没有持久 Memory block；
- Extract 接收完整 active messages、索引和固定 scope prompt；
- cursor 初始值、增量 `N`、空 Candidate 推进、失败不推进及 Full Compact reset；
- Extract 超预算不调用模型、不 Store、不推进 cursor；
- 四种类型、字段、ID/路径、正文大小与重复/冲突校验；
- `.tmp`、crash orphan、committed 边界，以及索引失败后的正文/temp 回滚；
- partial commit 后重试时跳过已 committed Candidate 并完成剩余项；
- Consolidate snapshot、完整候选验证及失败 rollback；
- failed/interrupted Run 不 Extract，resume 正常完成后才 Extract。

当前实际结果：V08 全量 Fake Model 自动测试 `285 passed`。

```text
285 passed in 2.21s
```

## 真实 API Demo Cases

以下 Case 面向根目录 `.env` 指向的真实 Anthropic-compatible 服务。模型措辞和具体 Candidate ID 允许变化，验收以 Memory 文件、state、实际请求次数及 CLI/JSONL 事件为准。

本轮实现按要求没有运行真实 API Demo，因此以下内容是可执行的验收步骤、预期轨迹和可能结果，不是已通过声明。

### Case 1：跨 Session 召回项目测试命令

Session A 输入：

```text
请确认并记住：这个项目约定的自动测试命令是
PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q -p no:cacheprovider。
只回复确认，不要运行。
```

退出后检查 `memory/MEMORY.md` 与新增正文，再启动全新 Session B：

```text
这个项目约定的自动测试命令是什么？不要读取仓库文件。
```

预期轨迹：

```text
Session A completed
→ memory.extract.started
→ project Candidate validated/stored
→ cursor advanced
→ Session B recall once
→ Selector selects the project Memory
→ body enters temporary system context
→ Main Agent answers without reading old conversation
```

验收：正文位于独立 `<id>.md`；索引不包含完整正文；Session B messages 与 state 中没有 `<PersistentMemory>`；Main Agent 的实际 system context 包含召回内容；同一 Run 的后续 Round 不再执行 Selector。

可能结果：Extractor 认为用户只是在给当前任务下临时指令而返回空列表。此时 Session A cursor 仍应正常推进，但 Session B 不会召回；这属于模型长期价值判断差异，需要检查 Extract 响应和事件，不能伪造 Store 成功。

### Case 2：稳定用户偏好与不相关请求的空召回

Session A 输入：

```text
以后给我汇报代码修改时，请始终先写测试结果，再写修改摘要。这是长期偏好。
```

Session B 输入相关请求：

```text
汇报这个目录当前实现的内容。
```

Session C 输入不相关请求：

```text
计算 17 乘以 23。
```

预期：Session B Selector 选择 `user` Memory，回答遵循“测试结果优先”；Session C Selector 返回空列表，不因为 Memory 存在就无条件注入。

验收：两个新请求各自只有一次 `memory.recall.started/completed`；B 的 `selected_count=1`，C 的 `selected_count=0`；Memory 不追加为 user/assistant message。

可能结果：Selector 对 Session B 认为偏好相关并选中多条 Memory，但仍不得超过 5 条；如果返回未知或重复 ID，Runtime 应拒绝 LLM 结果并转入 deterministic fallback。

### Case 3：重复事实与 partial Store 重试

第一次输入：

```text
项目约定：所有 Fake Model 测试都不能访问网络。
```

第二次用不同措辞再次确认同一事实：

```text
再次确认，本项目的模拟模型测试必须完全离线。
```

预期：第一次生成一条 `project` Memory；第二次 Extract 可以返回空列表，或返回语义相同 Candidate 后被 Store 记为 `skipped`，不会产生重复正文。

故障注入扩展：在两个 Candidate 的 Extract 响应中，让第一条索引提交成功、第二条索引更新失败。预期第一条保持 committed，第二条本次新建正文和相关 temp 被回滚，cursor 不推进；恢复正常写入后再次完成一个成功 Run，Extract 会覆盖旧增量，第一条 dedup skip，第二条重新 Validate/Store，最后才推进 cursor。

可能结果：真实模型可能为同一语义生成不同 ID 和差异较大的摘要/正文，使第一版简单语义去重不能识别。这属于 Known Limitation，需要保留证据，而不是增加 Embedding 或自行扩展 PLAN。

### Case 4：损坏正文不污染 Main Agent

先通过 Case 1 创建一条 committed Memory，再手工把对应正文改为非法结构或删除文件，然后发起相关新请求。

预期：Selector 可能仍从索引选中该 ID，但读取/frontmatter 一致性校验失败；Runtime 记录 `memory.recall.item_failed`，不注入不可信正文，Main Agent 仍正常执行。

验收：Run 不因单条 Memory 损坏而失败；system context 中没有损坏正文；state/messages 没有该正文副本；日志不记录完整正文。

可能结果：如果 `MEMORY.md` 自身损坏，Recall 整体记录 `memory.recall.failed` 并在无 Memory 情况下继续。后续 Extract 也可能因无法读取索引而失败，但已经成功的 Main Agent Run 仍保持 completed。

### Case 5：长 Session、Full Compact 与 cursor reset

在同一 Session 中制造足够长的合法多 Round history，使 V07 Full Compact 成功，然后正常结束 Run。

预期轨迹：

```text
context.summary.completed
→ compacted messages committed
→ memory.cursor.reset
→ Main Agent completed
→ Extract sees compacted full active messages
→ N equals compact 后全部 model-visible messages
→ Store succeeds or empty-success
→ cursor points to compact 后末尾
```

验收：compact checkpoint 中 messages 与 `last_memory_message_index=null` 同时出现；不存在 compact 后 messages 配旧 cursor 的交叉状态；Recall Memory 没有被写入 Structured Summary。

可能结果：V07 Full Compact 可能因没有合法 boundary、Summary schema 不合法或无法达到 `<8K` target 而失败。此时 messages 与旧 cursor 必须共同回滚，Main Run 按既有 V07 语义报告 Context Compact failure，不执行 Extract。

### Case 6：Extract 请求超过安全预算

构造长 active history，使 Main Agent 仍能完成，但 `完整 messages + MEMORY.md + Extract prompt` 的估算达到 96K。

预期：Main Agent 先正常 completed；Memory 后处理记录 `memory.extract.failed`，错误说明超过 safe input budget；不发送 Extract API 请求、不写 Candidate、cursor 不推进。

可能结果：V07 request preflight 可能在 Main Agent 请求前已经执行 Full Compact，使 Extract 输入降到 96K 以下并正常运行。若要稳定验证超预算分支，应使用 Fake Model/确定性 estimate 注入；不能把未触发的真实 Case 写成超预算通过。

## Development / Debugging Notes

### 记录 1：V07 baseline 与 V08 初始实现

- **继承范围**：复制 V07 Runtime、requirements 和两组自动测试到独立 `v08_memory_system/`，不修改 V01–V07 目录。
- **新增模块**：`memory_store.py` 承担文件校验、原子提交、失败回滚、残留清理和 Consolidate；`memory_pipeline.py` 承担 Selector、Recall、Extract 与同步编排；`agent.py` 只加入最小插入点。
- **初始结果**：V07 继承测试 `250 passed`。

### 记录 2：Candidate Store 索引失败后的事务式回滚

- **现象**：旧实现中正文成功写入、索引更新失败会保留 orphan；但 Extractor 看不到未被索引引用的正文，只能依赖下一轮非确定性地再次生成相同 ID，恢复链路不闭合。
- **原因**：正文和 `MEMORY.md` 是两个顺序执行的原子替换，不是跨文件事务；旧实现把中间正文当作可复用恢复材料，却没有 orphan catalog、Candidate 持久化或确定性重放机制。
- **修复**：索引更新失败时，单条 Candidate Store 失败并删除本次新建正文和相关 temp；只保留此前已经 committed 的条目。cursor 不推进，下一次成功 Run 后从旧 cursor 重新 Extract。强制终止产生的 crash orphan 仍只作为未提交残留清理。
- **验证**：覆盖 body success + index failure 回滚、temp 清理、cursor unchanged、既有 committed Memory 不变、partial batch 保留前序提交，以及后续重新 Extract/Store 成功。

### 记录 3：SDK content block 的 Extract 复制

- **现象**：集成测试中 Main Agent messages 包含 Fake/SDK 风格对象 block，Extract 为复制完整 history 使用 JSON round-trip 时出现对象不可序列化，导致 cursor 不推进。
- **原因**：V07 正常保留 SDK `TextBlock/ToolUseBlock` 对象；Extract 不应把完整历史先强制 JSON 序列化。
- **修复**：改用 `copy.deepcopy(messages)` 保留现有消息表示，再追加尾部 Extract prompt；预算函数仍通过 Runtime 的 `_jsonable()` 生成确定性估算表示。
- **验证**：多 Round Tool Run 的 Recall→Main Agent→Extract 集成测试确认 Selector 一次、Main Agent 两轮、Extract 一次，Memory 未进入 messages，cursor 最终指向末尾。

### 记录 4：最终 Fake Model 验收

- **命令**：`PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q -p no:cacheprovider`
- **结果**：Candidate Store 失败语义收敛后为 `285 passed in 2.21s`。
- **边界**：本轮没有读取 `.env`、没有访问网络、没有执行真实 API Demo，也没有进行 Git 操作。

## Known Limitations

- Selector 与 Extract 都依赖模型遵循严格 JSON schema；失败时 Recall 可以文本 fallback，但 Extract 不做启发式提取。
- deterministic fallback 只是词面重合，不理解同义词、跨语言语义或复杂指代。
- 重复检查只使用稳定 ID，以及规范化后的同类型正文/摘要相等；没有 Embedding，措辞差异较大的语义重复可能同时存在，等待低频 Consolidate 处理。
- Memory Store 没有多进程并发写锁；V08 假设单 Runtime 进程同步写入当前版本目录。
- 单条 Store 的正文和索引是有顺序的两个原子文件替换，不是跨文件系统事务；可捕获的索引失败会回滚本次正文和 temp。进程在两次替换之间被强制终止仍可能留下需清理的 crash orphan，但不会留下索引指向缺失正文。
- Consolidate 使用 snapshot + staging + rollback 提供第一版批量一致性，但不是数据库事务；进程在多文件整体替换的极窄窗口被强制终止时，需要依据 snapshot 人工检查恢复。
- Recall context 不持久化。Main Agent Run 中断并 resume 时不会重新召回，也不会恢复当时的临时 Memory system context；这是 PLAN 明确规定的 V08 边界。
- Extract 失败没有后台重试、独立 finalization workflow 或 operation ID；依赖旧 cursor 在后续成功 Run 后保守重处理。
- `MEMORY.md` 或正文损坏时 Runtime 选择不注入，而不会自动修复、猜测正文或重建索引。
- 50 条/24,000 字符 Consolidate threshold、最多 5 条 Recall 和 12,000 字符单正文限制都是 V08 第一版确定性工程参数，不代表通用最佳值。
