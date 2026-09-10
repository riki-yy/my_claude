# V06：状态与可靠性

V06 完整继承 V05 的 Agent Loop、七个工具、Permission、Hooks、Todo 和 Observability，只解决两个可靠性问题：

1. 进程仍存活时，从 retryable LLM/API 瞬态错误中有限恢复；
2. Ctrl-C、异常退出或重启后，从本地状态文件恢复原 Session/Run，并在 Tool 副作用不确定时先检查真实 workspace。

本版不实现 Context Compact、Memory、Background Task、Multi-Agent、数据库、Event Sourcing、exactly-once Tool execution、完整 Tool Call Registry或通用状态机。

## 目录与启动

在项目根目录准备共享 `.env`，然后：

```bash
cd v06_state_reliability
python3.12 -m pip install -r requirements.txt
python3.12 -m pytest -q
python3.12 agent.py
```

普通启动会生成 `state/<session_id>.json` 可恢复状态及 `logs/<session_id>.jsonl` 事件日志。恢复指定 Session：

```bash
python3.12 agent.py --resume state/<session_id>.json
```

显式 resume 遇到损坏、截断、schema 不兼容或必需字段非法的状态时，会输出 `State Load Error`、写入 `state.load_failed` 并以退出码 3 结束。Runtime 不覆盖原状态，不静默创建空 Session，也不进入 Y/N 新建流程；需要新 Session 时应使用普通启动命令。

## Persisted Runtime State

V06 使用一个最小 JSON 对象，不引入 StateManager/Repository 类层次：

```text
schema_version
session_id
messages
todos
current_run
  run_id
  status
  round
interruption_info
updated_at
```

`current_run.status` 只允许：

- `running`：Run 正常执行中；旧 state 保持此值说明上个进程未正常结束；
- `completed`：Run 正常结束，resume 后只恢复 Session 并等待新输入；
- `failed`：Run 已明确失败，resume 后保留事实但不自动继续；
- `interrupted`：Runtime 捕获中断且成功 checkpoint，resume 后继续同一 Run/round。

`updated_at` 只用于观察和调试。恢复正确性不依赖时间戳，也不持久化 `workspace_modified` 之类猜测外部状态的布尔值。

## Checkpoint 与原子保存

Checkpoint 是 Runtime 自动动作，不是 Tool。以下一致性边界会保存：Session 创建和 user message 追加；Run status/round 变化；LLM response 可靠追加；Tool Result 可靠记录；Todo State 成功整体替换；`interruption_info` 设置/清除；Run completed、failed 或 interrupted。

每次写入遵循：

```text
serialize
→ same-directory unique temp file
→ write
→ flush/fsync
→ close
→ os.replace
```

正式 state file 从不被直接覆盖。写入失败会删除可删除的临时文件并保留旧正式文件。该原子性只保护 state file 自身，不提供 workspace 与 state 的跨文件事务。

## interruption_info 与 Workspace Reconciliation

`interruption_info` 是当前 in-flight 执行现场，不是错误消息或 Tool Result。最小 phase 为 `llm_call / permission_wait / tool_execution / tool_result_recording`。Tool phase 额外保存 `tool_name` 和 `tool_use_id`。进入关键阶段前先设置并 checkpoint；结果可靠记录后清除。

所有 unfinished Run 恢复时，Runtime 都会把公共 recovery context 作为独立、模型可见的 `text block` 注入；它不拼接进 `tool_result.content`，也不伪装成 Tool Result。公共 context 要求模型以已恢复的 messages、Todo State 和可靠 Tool Results 为依据，继续当前工作，不要仅因进程重启而重新探索已经确认的 workspace 状态。Runtime 不自行总结“什么已经完成、下一步做什么”等业务进度。

具体恢复行为由 `interruption_info.phase` 决定：

- `llm_call`：只注入公共 context，不要求 workspace reconciliation；
- `tool_execution / tool_result_recording`：在公共 context 后追加独立的 uncertain Tool context。Runtime 移除无法与 Tool Result 合法配对的尾部 uncertain `tool_use`；模型不假设 Tool 成功或失败、不盲目 replay，只检查与被中断 Tool 直接相关的状态并 reconcile，不重新扫描无关 workspace；
- `permission_wait`：在公共 context 后追加独立的 permission-specific context；模型不假设之前已允许或拒绝，恢复 pending operation 的 permission flow，不执行无关 workspace reconciliation。

该 refinement 不改变 Restart Recovery 与 In-Process Recovery 的边界：429、timeout、retryable 5xx 等 LLM/API 瞬态错误仍只在同一 Round 内 retry，不注入 recovery context。Recovery 仍只依赖已恢复的 messages、Todo、可靠 Tool Results 和 `interruption_info`，不扩展 persisted state schema。

## LLM/API transient retry

只有 LLM/API 层的 retryable transient error 会重试。当前覆盖 408、409、429、5xx、连接错误和超时；真实动机包括 DeepSeek-V4-Flash 的 `429 model_concurrency_rate_limit_exceeded`。

固定参数：

```text
MAX_RETRIES = 4
BASE_DELAY = 1.0s
MAX_DELAY = 16.0s
JITTER_RATIO = 0.25
```

`MAX_RETRIES` 不包含 initial request。一次 Round 最多产生五次实际 API 调用：

```text
attempt=1  initial request
attempt=2  retry 1
attempt=3  retry 2
attempt=4  retry 3
attempt=5  retry 4
```

所有 Attempt 属于同一 Round，不增加 Round，也不额外消耗 `MAX_ROUNDS`。

无有效 `Retry-After` 时，retry 1–4 的基础 delay 为 `1s / 2s / 4s / 8s`，再加入 `0~25%` 正 jitter。加入 jitter 后的最终 sleep 受 16 秒硬上限约束。有效 `Retry-After` 优先，但同样截断到 16 秒。

四次 retry 全部失败后记录 `llm.retry_exhausted`，结果为 `RETRY_EXHAUSTED`，current Run checkpoint 为 `failed`，不再调用模型。`FILE_NOT_FOUND`、`INVALID_TOOL_INPUT`、`PERMISSION_DENIED` 等 Tool/User Error 仍作为 Tool Result 交给 LLM，不触发 API retry。

## Observability

V06 延续 EventLogger、CLI 和 JSONL，并新增或补强 `checkpoint.saved`、`state.restored`、`state.load_failed`、`run.interrupted`、`run.resumed`、`llm.retry` 和 `llm.retry_exhausted`。

`llm.started/completed/failed` 均记录 Round 和 Attempt；`llm.retry` 记录下一 Attempt、retry 序号、最终实际 delay、delay 来源及分类后的 error code。日志用于解释过程，不作为恢复正确性的事实来源。

## 自动测试

测试继续在生产代码的 `client.messages.create` 表面使用 Fake Model，不读取 `.env`、不访问网络。除完整继承 V05 的 165 项测试外，V06 覆盖：

- messages/Todo/session/run/round/interruption_info save/restore 和 SDK block JSON 序列化；
- atomic replace 失败时旧文件不变；
- corrupted、truncated、schema incompatible 和必需字段非法；
- 显式 resume 的错误输出、事件、原文件保护、无交互和 non-zero exit；
- 四种 Run status round-trip 和 restore 分流；
- KeyboardInterrupt 保存 `interrupted` 与当前 phase；
- running/interrupted 恢复相同 run_id 和 round；
- uncertain Tool side effect 不自动 replay；
- `llm_call` 只注入公共 recovery text block，不要求 reconciliation；
- `tool_execution/tool_result_recording` 追加 targeted uncertain Tool context，`permission_wait` 追加 permission-specific context；
- recovery context 保持独立 text block，且不破坏已有 Anthropic Tool Result 消息结构；
- multi-tool Round 中逐项持久化真实 Tool Result，恢复时安全区分 completed、uncertain 与 not-executed Tool；
- pairing preflight 阻止 incomplete、错序或 ID 不匹配的 Tool Result 消息进入 LLM/API；
- persisted-message validation 在 state load 边界区分合法 partial batch、合法完整 recovery message 与非法 pairing；
- 完整 Tool Results 后带独立 reminder/recovery text 的状态可再次 resume，且不会重复生成 synthetic Results；
- 429/5xx retry、Attempt 映射、Retry-After、backoff/jitter 和硬上限；
- 五次调用后 retry exhausted，current Run 进入 failed；
- Tool/User Error 不触发 API retry。

实施结果：V05 baseline 165 项通过；V06 当前全量 220 项通过。

## 手动 Demo Cases

以下 Case 使用根目录 `.env` 指向的真实 Anthropic-compatible 服务。模型文本允许有差异，验收以 state、workspace、CLI/JSONL 和实际 API 调用事实为准。

### Case 1：正常 Session 恢复

- 首次输入：`记住本次任务标记是 STATE-42，并建立一个未完成 Todo：读取 README。`
- 普通退出后，以生成的 state file 执行 `--resume`，再输入：`继续上一任务，并告诉我任务标记。`
- 验收：messages、Todo、session 和 completed Run 恢复；旧 Run 不自动重跑；模型能回答标记并继续上下文。
- **实际结果（2026-09-08，agnes-2.0-flash）**：通过。首次 Run 创建 `STATE-43` 与 `○ Read README` 后 completed；`--resume` 时 CLI 只输出 `[Restore] restored` 并等待新输入，没有自动调用模型或重跑旧 Run。新输入要求不使用工具，模型直接从恢复历史回答 marker 为 `STATE-43`、Todo 为 pending。JSONL 共 33 条，恢复点记录 `state.restored`，两个用户 Run 各自 `run.completed`，sequence 从 1 到 33 连续。state/log：`state/e9ce7dbe-55a2-4ed0-9d87-52c768063bf1.json`、`logs/e9ce7dbe-55a2-4ed0-9d87-52c768063bf1.jsonl`。

### Case 2：Tool 执行附近中断后 reconcile

- 输入：`创建 state_demo.txt，内容为 V06-RECOVERY，并读取确认。`
- 在 `tool_execution/tool_result_recording` 边界注入等价 Ctrl-C，使文件可能已写入但 Tool Result 未可靠保存；随后把 Run checkpoint 为 interrupted 并 resume。
- 验收：同一 run_id/round 恢复；模型收到 uncertain Tool recovery context，只检查与中断 Tool 直接相关的真实 workspace 状态；write_file 不被 Runtime 自动 replay；最终文件和总结一致。
- **实际结果（2026-09-08，agnes-2.0-flash）**：通过。真实模型在 Round 1 返回 `write_file + read_file`，首个 `write_file` 已把 `V06-RECOVERY` 写入 `state_demo.txt` 后，在 `tool_result_recording` 前注入中断。状态保存为 interrupted，随后以相同 run_id、Round 1 resumed。恢复后的模型先调用 `read_file state_demo.txt` 和只读 `bash ls` 核对真实 workspace，没有 Runtime 自动重放 `write_file`，Round 2 正常完成。JSONL 顺序包含 `run.interrupted → state.restored → run.resumed`。state/log：`state/7090dfe4-0995-497a-9064-ed9fb45ec74a.json`、`logs/7090dfe4-0995-497a-9064-ed9fb45ec74a.jsonl`。

### Case 3：429/transient retry

- 在真实 API client 前注入一次结构等价的 `429 model_concurrency_rate_limit_exceeded`，下一 Attempt 委托根 `.env` 的真实 client。
- 验收：同一 Round 出现 attempt 1/2，记录 retry delay 和分类；第二次真实 API 调用成功；不增加 Agent Round。
- retry exhaustion、完整 `attempt=1..5`、1/2/4/8 秒基础序列、jitter 和 16 秒上限由确定性自动测试覆盖，避免真实 Demo 故意向服务发送五次失败请求。
- **实际结果（2026-09-08，agnes-2.0-flash）**：通过。Round 1 attempt 1 收到注入的 `429 model_concurrency_rate_limit_exceeded`，分类为 `MODEL_CONCURRENCY_RATE_LIMIT_EXCEEDED`；`llm.retry` 记录 attempt 2 和 1.186 秒 backoff，attempt 2 委托真实 API 并返回 `V06 retry recovered.`。结果为 completed、rounds=1、实际调用数=2。state/log：`state/f3ff6ecd-b690-4412-874c-223aebff66f9.json`、`logs/f3ff6ecd-b690-4412-874c-223aebff66f9.jsonl`。

### Case 4：Tool/User Error 不触发 retry

- 输入：`读取 definitely_missing.txt，不要创建；告诉我错误。`
- 验收：`FILE_NOT_FOUND` 形成 Tool Result并由模型处理；相邻两次 LLM 调用分别是不同 Round 的 initial Attempt，不出现 `llm.retry`。
- Permission 分支可用敏感路径写入并在提示时拒绝，验收 `PERMISSION_DENIED` 同样不触发 retry。
- **实际结果（2026-09-08，agnes-2.0-flash）**：通过。Round 1 attempt 1 调用 `read_file` 并得到 `FILE_NOT_FOUND`；模型在 Round 2 attempt 1 明确报告文件不存在。22 条 JSONL 中没有 `llm.retry`，目标文件未创建，Run completed。state/log：`state/5712271c-7ca9-4ae2-b4b4-274dc482f0b3.json`、`logs/5712271c-7ca9-4ae2-b4b4-274dc482f0b3.jsonl`。

## 相对 V05：新增、修改和保留

**新增**：最小 Runtime State、JSON validate/load/save、原子 checkpoint、四值 Run status、interruption phase、resume/recovery context、LLM/API 最小分类、有限 retry 和相关事件。

**修改**：Agent Loop 的模型调用增加同 Round Attempt；Tool/LLM/Run 一致性边界接入 checkpoint；CLI 支持 `--resume`；permission wait 的 Ctrl-C 作为 Process Interruption 传播，不再伪装成用户拒绝。

**保留**：V05 的 System Prompt、工具 schema/handler、Permission 决策、Hook 生命周期、Todo 全量原子更新、Tool Result 错误回路、Workspace 技术边界、`MAX_ROUNDS` 和日志脱敏/限长策略。

## 开发与调试记录（Development / Debugging Notes）

### 记录 1：V05 baseline

- **触发方式**：在 `v05_plan_todo/` 执行 `python3.12 -m pytest -q`。
- **结果**：165 项通过，用时 0.80 秒。
- **结论**：V06 从已验证的 V05 独立目录继承，不修改 V01–V05。

### 记录 2：V06 首轮继承测试

- **现象**：首轮出现两个断言差异并在旧 Permission 参数化 Case 遇到 KeyboardInterrupt。
- **原因**：连接错误已进入 retry；LLM CLI 新增 Attempt；permission wait 的 Ctrl-C 按 V06 要求传播为 Process Interruption。
- **解决**：只更新这些继承预期，并新增专门的 KeyboardInterrupt 持久化测试，不把中断改回 `PERMISSION_DENIED`。
- **结果**：补齐 V06 初始测试后全量 188 项通过，用时 0.83 秒。

### 记录 3：真实 API Demo

- **现象**：默认沙箱首次真实调用无法访问网络，触发五次 Connection Error；Runtime 按 attempt 1–5 执行四次 bounded retry 后产生 retry exhausted，并把 current Run 保存为 failed。
- **原因**：执行环境网络限制，不是远端 API 返回。该次只能作为真实进程内故障证据，不能冒充服务端 Demo。
- **解决**：获得联网执行授权后，以相同根 `.env` 和生产调用路径执行四个正式 Demo；429 Case 仅在真实 client 前注入一个等价异常，第二 Attempt 委托真实服务。
- **结果**：正常 Session restore、uncertain Tool reconciliation、429→真实 API retry、FILE_NOT_FOUND 不 retry 全部通过；正式日志路径已写入各 Case。
- **平台/环境**：macOS、Python 3.12、`agnes-2.0-flash`、Anthropic-compatible endpoint。

### 记录 4：resume 后 JSONL sequence 重置

- **现象**：第一次 completed Session 恢复功能正确，但最终审计发现新 EventLogger 向同一个 JSONL 追加时 sequence 从 1 重新开始。
- **原因**：V05 的 logger 只面向单进程 Session，构造函数固定以 `sequence = 0` 启动；V06 恢复沿用了旧假设。
- **解决**：EventLogger 初始化时读取既有 JSONL 的最大合法 sequence，并从下一值继续；日志读取失败只影响观测起点，不参与 state 恢复判断。新增恢复 logger 回归测试。
- **验证**：V06 全量 189 项通过；重新执行 Case 1 后，跨进程同一 JSONL 的 sequence 为 1–33 连续。

### 记录 5：复杂贪吃蛇任务恢复后失去当前工作焦点

- **测试场景**：真实 API 执行一个从零实现原生 HTML/CSS/JavaScript 网页版贪吃蛇的复杂多步任务。Run `ea81d6a7-7a35-4b78-9b34-1c641c2d8c0a` 执行到 Round 11 的 `llm_call` 阶段时 Ctrl-C，保存为 `interrupted`；随后使用 `--resume` 继续，最终在 Round 20 仍返回 Tool Call，因没有 Round 21 用于读取最后 Tool Result 并收尾，触发 `MAX_ROUNDS_EXCEEDED`。证据位于 `logs/f0bcfa4b-3223-453a-a4f5-94b542f0f276.jsonl` 和同名 state file。
- **正向验证**：resume 恢复了相同 `run_id` 和相同 Round 11，并恢复了 messages、Todo State 及历史 Tool Result；没有自动 replay Tool。日志事件顺序为 `run.interrupted` sequence 129 → `state.restored` sequence 131 → `run.resumed` sequence 132，说明 V06 Restart Recovery 的身份、轮次和历史恢复本身工作正常。
- **暴露的问题——中断前**：在实际创建目标文件前，模型先执行了项目根目录列举、读取 V06 README、列举 `demos/tests/state` 等与贪吃蛇实现无关的探索，到 Round 4 才创建 Todo。后续又两次错误怀疑 CSS 被污染，原样重写并重读确认，导致中断前已消耗 10 个完成的 Agent Round。中断时 workspace 的实际状态是：目录、HTML 和 CSS 已完成且 Tool Result 已可靠记录，`script.js` 尚未创建。
- **暴露的问题——Todo**：Todo 只有“规划”、“创建目录并实现 HTML/CSS/JS 三文件”、“自查验证”三项。第二项粒度过粗，无法表达“HTML/CSS 已完成、JS 尚未完成”这一真实进度。resume 后模型完成了 JS 创建、修正与多项验证，却没有再调用 `todo_write`；最终 state 仍为第二项 `in_progress`、第三项 `pending`，逐渐与真实 workspace 失真，无法成为恢复后的进度锚点。
- **暴露的问题——recovery context**：本次 interruption phase 为 `llm_call`，Round 10 的 `read_file(index.html)` Result 已可靠记录，没有 in-flight Tool，也没有 uncertain Tool side effect。但当前通用 recovery context 仍要求模型 `inspect the real workspace`，且没有指出当前 Todo 重点。因此恢复后 Round 11 重新列举目录，Round 12 又重读刚在中断前读取过的 HTML，属于不必要的重新探索。
- **暴露的问题——resume 后执行**：Round 13 创建 JS 时模型生成的 Tool Input 本身含有非法文本，Round 14–16 用于读取、重写和确认，这部分修正是必要的；Round 17 的 `node --check` 和 Round 18 的 HTML/JS ID 一致性检查也有直接价值。但 Round 19 继续逐项检查 CSS class，Round 20 又重复检查 restart ID 和已在 Round 17 通过的 JS 语法；在已有充分证据后仍未更新 Todo 或返回无 Tool Call 的最终答复。
- **根因判断**：主要是模型的自我怀疑、重复探索和未及时收尾，其次是 Todo 粒度过粗且未持续维护，以及 recovery context 没有按 interruption phase 区分恢复策略。System Prompt 已要求证据足够后停止探索并显式更新 Todo，但复杂任务和恢复后的焦点约束仍偏弱。`MAX_ROUNDS_EXCEEDED` 只是最终暴露问题的安全边界，不是根因；不通过简单提高 `MAX_ROUNDS` 解决。
- **后续处理**：V06 本次不修改 Runtime、System Prompt、Todo 或恢复策略，仅保留真实运行证据。恢复后 Round 11 的 input context 约为 11,435 tokens，到 Round 20 增长为约 23,964 tokens；其中包含完整 README、文件内容、重复写入和多次检查结果。这作为 V07 Context Budget / Compact 的真实问题来源记录，不在 V06 提前解决。

### 记录 6：Todo refinement 与 maintenance nag 的问题驱动演进

- **原始问题**：记录 5 的贪吃蛇 Demo 不只是暴露了恢复后探索过多，也暴露了 Todo 自身的可用性问题。模型到 Round 4 才创建 Todo；列表中包含“规划并拆解贪吃蛇小游戏任务”这种元任务；“创建 demo_workspace 目录并实现 HTML/CSS/JS 三文件”又把进度可能不同的多个交付物合成一项，无法表达中断时“HTML/CSS 已完成、JS 尚未完成”的真实状态。随后模型在创建、修复和验证 JS 的过程中长期没有再次调用 `todo_write`，最终 Todo 仍停留在第二项 `in_progress`、第三项 `pending`，与真实 workspace 逐渐失真。
- **第一版优化**：System Prompt / Todo 使用指导增加最小 refinement：复杂、多步骤、多文件任务即使用户没有显式要求“规划”或“使用 Todo”，也应优先主动调用 `todo_write`；Todo 应描述实际交付阶段，不把“规划任务”本身列为 Todo；每项保持可独立推进和判断完成的适中粒度，多文件组件在进度可能分离时分别表达，同时避免把简单操作拆成大量微型 Todo。Runtime 另增加 maintenance reminder：存在 active Todo 时，连续三个包含 Tool Call 的 Agent Round 没有成功调用 `todo_write`，向模型提醒检查和更新 Todo；只有成功的 `todo_write` 才能 reset，Runtime 不代替模型修改 Todo。
- **第一版暴露的问题**：第一版 reminder 被直接拼接进某个 `tool_result.content` 字符串，结构上不够醒目；文案较长且偏条件式，更像“如果需要则检查”的建议；每次提醒后计数又从零开始，模型忽略一次提醒便会重新获得三个 Tool Round 的静默窗口。真实 reading-tracker / expense-splitter Demo 表明模型能够看到 reminder，也可能在某次提醒后更新 Todo，但经常继续调用其他工具并忽略后续提醒，Todo 仍会逐渐落后于 workspace。旧 reading-tracker 轨迹中 reminder 仅出现在 R4、R9、R12、R15、R18；模型只在第一次提醒后于 R6 更新过 Todo，之后没有持续维护，最终到 Round 20 仍未正确收尾。
- **原因分析**：与 `learn-claude-code` 的 Todo nag 实际源码逐项对比后，发现关键差异不在于是否设置“三轮”阈值，而在模型可见结构、命令强度和阈值后的行为：其 reminder 是 Tool Result 聚合消息中的独立 `text block`，使用短而直接的 `<reminder>Update your todos.</reminder>`，达到阈值后每个仍未维护 Todo 的 Tool Round 都继续 nag，而不是提醒一次后重新等待三轮。第一版较弱的结构和措辞、以及间歇式提醒，均可能降低模型立即响应的概率。
- **第二版优化**：保留既有 Todo planning refinement，只调整 nag。所有同一 Round 的 `tool_result` 仍按 Anthropic 消息协议聚合，reminder 作为这些 Tool Result 之后的独立模型可见 `text block`，不再成为 `tool_result.content` 的一部分；文案固定为 `<reminder>Update your todos.</reminder>`；active Todo 连续三个 Tool Round 未被成功维护后进入持续 nag，第三轮开始提醒，之后每个仍未成功调用 `todo_write` 的 Tool Round 继续提醒，直到一次成功的 `todo_write` 更新完整 TodoList 后停止 nag 并 reset，随后重新累计。多 Tool Call 仍按一个 Agent Round 计数；Tool Error、Permission Denied 或失败/非法的 `todo_write` 不被误判为成功维护。
- **为什么保持 soft constraint**：reminder 只把维护信号送入模型上下文。Runtime 不自动推断某项是否 completed、不自动改写 Todo State、不阻止模型调用其他 Tool，也不把 Todo 扩展为强制 Workflow、Task Graph 或状态机。因此模型仍可忽略提醒；V06 的目标是提高主动维护概率并保留模型判断，而不是保证百分之百服从。
- **自动测试**：确定性测试覆盖第 1、2 个未维护 Tool Round 不提醒，第 3 个开始提醒，第 4、5 轮及以后持续提醒；成功 `todo_write` reset，reset 后重新累计三轮；多 Tool Round 只计一次；Tool Error、Permission Denied、非法 `todo_write` 和没有 active Todo 等边界；并直接断言 reminder 是独立 `text block`、不在 `tool_result.content` 内。最终 V06 全量测试结果为 `194 passed`，原有行为未回归。
- **真实 Demo 前后对比——unit converter**：任务具有多步骤、多文件特征，用户没有显式要求规划、拆解或使用 Todo，但模型没有创建 active Todo，因此 nag 没有启动。这说明 Todo creation 与已有 Todo 的 maintenance 是两个不同问题：nag 只能维护已经存在的 active Todo，不能替代模型最初选择 `todo_write`。
- **真实 Demo 前后对比——project dashboard**：同样在用户未显式要求 Todo 的复杂多文件任务中，模型在 R1 主动创建了 5 项实际工作 Todo，分别覆盖目录与 HTML、CSS、数据、交互逻辑和验证，没有“规划任务”元 Todo。R2–R4 连续三个未维护 Tool Round 后，R4 首次注入 reminder；模型虽然没有立即响应，但 R4–R14 每个 Tool Round 都持续收到 nag。R7 同一 Round 出现 3 个 Tool Call，持久化消息保持为 3 个 `tool_result` block，随后只有 1 个独立 reminder text block。R15 模型调用 `todo_write`，将前 4 项标为 completed、验证项标为 `in_progress`，nag 随即 reset；R16–R17 完成验证，尚未重新达到三轮阈值；R18 再次调用 `todo_write` 将全部 5 项标为 completed，R19 返回无 Tool Call 的 final，Run 正常完成。
- **改善判断**：相较旧 reading-tracker 的间歇提醒、后续 Todo 失真和 Round 20 未收尾，project dashboard 的持续 nag 虽然被模型连续忽略多轮，但最终促成 R15 的进度重新同步，并在 R18 完成 Todo 收尾、R19 正常结束。该结果验证了独立 text block、直接文案和持续 nag 对 Todo 持续维护及最终收尾的实际改善；判断依据不是模型是否完全停止其他探索，而是 reminder 可见结构、注入节奏、reset 语义、Todo 与 workspace 的重新同步及最终完成状态均符合设计。
- **当前结论与剩余限制**：Todo refinement / nag 已达到 V06 当前的 soft-constraint 设计目标，本阶段不继续增强。复杂任务是否在开头主动创建 Todo 仍存在模型行为波动，unit converter 已提供反例；Runtime 不应因此自动创建 Todo。模型在已有充分证据后仍可能重复读取、过度验证或继续 Tool Call，这是独立的 Agent exploration / completion 问题，不再归因于 Todo nag，也不在本次通过强制 Workflow、提高 `MAX_ROUNDS`、修改 Recovery Context 或提前进入 V07 来解决。

### 记录 7：Recovery Context refinement

- **原问题**：记录 5 的贪吃蛇任务在 Round 11 的 `llm_call` 阶段中断。中断前 Round 10 的 `read_file(index.html)` Result 已可靠记录，既没有 in-flight Tool，也没有 uncertain Tool side effect；但旧的统一 recovery context 对所有 phase 都要求 `inspect the real workspace`。模型 resume 后因此在 R11 重新列举目录、R12 再次读取刚刚可靠确认的 HTML，没有直接聚焦尚未完成的 JS。这不是 Restart Recovery 的身份或持久化错误：相同 `run_id`、Round、messages、Todo 和 Tool Results 都已正确恢复，问题出在恢复指令没有区分是否真的需要 reconciliation。
- **原因分析**：Restart Recovery 存在两类本质不同的现场。`llm_call` 中断时，之前的 messages 和 Tool Results 已构成可靠事实，没有理由仅因 restart 扫描 workspace；`tool_execution/tool_result_recording` 则可能存在已发生但未可靠记录的 Tool 副作用，必须做最小 reconciliation；`permission_wait` 的不确定性是权限决定，而不是 workspace 事实。旧 context 把三者统一成广泛 workspace inspection，扩大了 `llm_call` 恢复的探索范围，也没有约束 uncertain Tool 恢复只检查直接相关状态。
- **V1 的实际构造与注入结构**：修改前代码由 `_recovery_text(state) -> str` 构造一个普通字符串；`prepare_recovery_messages()` 通过 `messages.append({"role": "user", "content": _recovery_text(state)})` 把它追加为一条 user message。因此 V1 的 `content` 本身是字符串，不是 Anthropic content-block list，也没有独立的 `{"type": "text", "text": ...}` block。对 `permission_wait/tool_execution/tool_result_recording`，函数会先移除尾部无法合法配对的 uncertain `tool_use`；这项清理在 V2 中继续保留。
- **V1 的实际文案与行为**：`_recovery_text()` 在同一个字符串里写入 `run_id`、`round`、`phase`，存在 Tool 信息时再写入 `tool_name` 和 `tool_use_id`；随后对所有 phase 使用同一组指令：不要假设 in-flight Tool 成功或失败、不要盲目 replay，并使用可用的 read/search/bash/git diff/test tools `Inspect the real workspace`，再根据观察事实继续原任务。代码没有为 `llm_call`、uncertain Tool 和 `permission_wait` 生成不同指令。
- **V2 的实际消息结构变化**：修改后 `_recovery_text() -> str` 被三个文本常量和 `_recovery_blocks(state) -> list[dict[str, str]]` 取代；`prepare_recovery_messages()` 改为追加 `{"role": "user", "content": _recovery_blocks(state)}`。因此 V2 的 user message `content` 是 block list：第一项固定为 `{"type": "text", "text": COMMON_RECOVERY_CONTEXT}`；`tool_execution/tool_result_recording` 时第二项为独立 `UNCERTAIN_TOOL_RECOVERY_CONTEXT` text block，`permission_wait` 时第二项为独立 `PERMISSION_RECOVERY_CONTEXT` text block，而 `llm_call` 只有第一项。diff 同时表明 recovery 文本没有被写入任何 `tool_result.content`，既有 uncertain `tool_use` 清理代码未改动。
- **优化方案**：所有 unfinished Run 先注入独立、模型可见的公共 `text block`，要求从 restored messages、Todo State 和可靠 Tool Results 继续当前 `in_progress` 工作，不要只因 restart 重探已经确认的 workspace。`llm_call` 只使用公共块，不要求 reconciliation。`tool_execution/tool_result_recording` 再追加独立的 targeted reconciliation 块：不假设成功或失败、不盲目 replay，只检查中断 Tool 直接相关的状态。`permission_wait` 则追加独立 permission-specific 块：不假设已允许或拒绝，恢复 pending operation 的 permission flow，不做无关 workspace reconciliation。Runtime 仍不生成业务进度摘要，所有 context 均不是 Tool Result，也不写入 `tool_result.content`；原有 uncertain 尾部 `tool_use` 清理和可靠 Tool Result 聚合结构保持不变。
- **自动测试**：新增确定性测试验证 `llm_call` 恢复消息只有公共 text block，且不包含 targeted inspection、reconciliation 或 permission 指令；参数化覆盖 `tool_execution` 和 `tool_result_recording` 追加 uncertain Tool 块；覆盖 `permission_wait` 追加 permission-specific 块；同时断言 recovery context 是独立 block、已可靠记录的 Tool Result 列表不被改写、uncertain Tool 不会自动 replay。恢复相关定向测试 `15 passed`，V06 全量测试最终为 `198 passed`，原有 resume、state、Todo、retry 行为无回归。
- **真实 Demo 前后对比**：新 Demo 使用复杂四文件 habit analytics Web 应用。Session `3f1da2e2-15ae-4435-8f37-0e8e96c40756`、Run `68ab3b8d-debf-40c1-86d7-28510c53d1f1` 在 R3 已可靠写入 `index.html` 和 `styles.css`，进入 R4 `llm_call` 后 Ctrl-C；state 保存为相同 Run 的 `interrupted / round=4 / phase=llm_call`。`--resume` 后日志依次记录 sequence 48 `run.interrupted`、50 `state.restored`、51 `run.resumed`，恢复消息只含一个独立公共 recovery text block。恢复后的第一个 Tool Call 在 R4 直接写入尚未完成的 `data.js`，没有像旧贪吃蛇 Demo 那样因 restart 立即执行 `ls`，也没有重读刚刚可靠确认的 HTML/CSS。Run 最终在 R18 正常完成。
- **结论与剩余限制**：对 `llm_call` 中断，phase-specific context 已把恢复行为从“先全面检查 workspace”收窄为“依据可靠历史直接继续”，真实 Demo 在 restart 边界显示了相对旧贪吃蛇轨迹的明确改善。本次模型没有创建 Todo，因此无法用该 Demo 验证是否围绕 restored Todo 的 `in_progress` 项继续；这是复杂任务主动选择 Todo 的模型波动，不是 Recovery Context 缺陷。恢复后模型仍在 R5–R6 重复写入 `data.js`、R7 读取它，并在后期进行多轮读取和验证，R15 才再次 `ls`；这些动作不是 recovery context 要求的 reconciliation，而是已经单独识别的 exploration/completion 波动。本 refinement 不声称解决该问题，也不通过修改 Todo nag、`MAX_ROUNDS` 或进入 V07 来处理。

### 记录 8：最终验收发现并修复 multi-tool interruption durability / recovery safety 缺口

- **最终验收发现的问题**：Recovery Context refinement 完成后，V06 全量测试已达到 `198 passed`。最终验收没有只以测试全绿作为结论，而是按 PLAN 的 recovery safety requirement 继续审查实际 multi-tool 执行与 checkpoint 调用链，由此构造出此前测试未覆盖的 failure window：同一 assistant Round 返回 Tool A、B、C → A 执行完成且可能产生 workspace 副作用 → Runtime 开始 B → B 在 `tool_execution` 或 Result 可靠记录前发生 Ctrl-C/crash → restart/`--resume`。当时的 uncertain-side-effect 测试只覆盖单个 Tool 中断，没有覆盖 `A completed → B interrupted → C not executed`，因此没有暴露这个持久化不变量缺口。
- **原因**：旧执行流程先创建进程内局部 `tool_results` 列表，再依次执行同一 Round 的全部 Tool；A 的 Result 只 append 到该局部列表，直到 A/B/C 全部结束后才整体追加为 user Tool Result message 并 checkpoint。开始 B 时，`interruption_info` 已改写为 B；如果此时中断，A 的副作用可能已经存在，但 A Result 未进入 persisted messages，恢复状态只知道当前现场是 B。旧 recovery cleanup 又会删除尾部 assistant message 中整批 `tool_use`，无法分别保留 A 已完成的调用事实、B 当前 uncertain 的调用和 C 尚未执行的原始语义。因此它虽然避免了直接 replay 当前 Tool，却不能安全恢复整个 multi-tool batch。
- **方案 A**：不扩展 persisted state schema，也不引入 Tool Call Registry。保留原 assistant multi-tool message，并把紧邻它的同一条 user Tool Result 聚合消息作为可持久化的执行中间态；已经可靠完成的 Result 是该聚合消息中的有序前缀。partial multi-tool state 可以存在于 state file，但它只是 Runtime durable checkpoint，不是允许直接提交给模型的 API-ready messages。
- **实现**：每个 Tool handler 和 PostToolUse 路径结束后，Runtime 立即把该 Tool 的真实 Result append 到当前聚合 user message，并执行 `tool_result.recorded` checkpoint，成功后才把它视为 durable completed。进入下一个 Tool 前再持久化新的 `permission_wait/tool_execution` 现场。resume 不再删除整批 `tool_use`，而是以 persisted Result 为第一事实来源：已有真实 Result 的 Tool 为 completed，保留结果且绝不重复执行；`interruption_info` 指向、但没有 persisted Result 的当前 `tool_execution/tool_result_recording` Tool 为 uncertain，不盲目 replay；其后的 unmatched Tool 为 not-executed，也不由 Runtime 自动执行。原 assistant Tool Uses、ID、输入和顺序全部保留，使尚未执行 Tool 的语义仍对模型可见。
- **Synthetic Result 语义**：为了在恢复后形成完整合法的 multi-tool 配对，uncertain Tool 获得 `INTERRUPTED_TOOL_OUTCOME_UNKNOWN`，not-executed Tool 获得 `TOOL_NOT_EXECUTED_AFTER_INTERRUPTION`；两者均为 `is_error: true`。前者明确表示 Runtime 无法确认副作用是否发生，并不声称成功或失败；后者只表示 Runtime 确认该调用没有开始。它们只描述各自 `tool_use_id` 的 execution outcome 状态。如何检查 uncertain 副作用和继续任务仍由其后的独立公共/phase-specific recovery text blocks 指示，Recovery Context 没有被塞入或伪装成 Tool Result。最终 user message 始终按“全部 Tool Result blocks 在前、独立 recovery text blocks 在后”的结构组织。
- **Pairing preflight**：唯一的 `client.messages.create()` 调用边界前新增 message protocol preflight，不把正确性只寄托在 resume 调用者上。它验证 assistant Tool Use IDs 非空且唯一、下一条必须是 user content-block message、Result IDs 必须与前一批 Tool Uses 完整且同序一一对应、不得出现缺失/重复/未知 ID，并要求所有 Tool Result blocks 位于 text blocks 之前。正常执行只有 A/B/C Results 全部完成后才回到下一轮 LLM call；restart 则必须先识别并补全 real/uncertain/not-executed 配对、追加独立 recovery text、checkpoint 成功，再进入 Agent Loop。任何 incomplete 或非法 pairing 都在本地返回 `PROTOCOL_ERROR`，不会产生 API request。这建立了明确不变量：persisted state 可以保存 partial batch，但任何实际 LLM call 前 messages 都必须是完整合法配对。
- **测试验证**：新增核心确定性回归真实走过 `A completed → B executed but Result not durable → interruption → load state → resume`：A 的真实 Result 已在 state 中，A/B 的 workspace 副作用各只发生一次，C 未执行；恢复后的消息分别包含 A real Result、B outcome-unknown synthetic Result、C not-executed synthetic Result及其后的独立 recovery blocks，且没有自动 replay。测试同时覆盖首个 Tool 尚未开始时全部标记 not-executed、durable Result 优先于残留 `tool_result_recording` phase，以及 incomplete pairing、错误 ID、错误 block 顺序在零 API 调用的情况下被 preflight 拒绝；合法 multi-tool Results 后接独立 text block 仍可正常调用模型。V06 全量测试最终为 `206 passed in 0.93s`，`git diff --check` 通过。
- **当前边界**：该修复提供的是当前单进程、单个 in-flight multi-tool batch 的最小 durability 与安全恢复，不承诺 workspace + state 跨文件事务或 exactly-once Tool execution；uncertain Tool 仍必须由模型针对直接相关真实状态做 reconciliation。state schema 未改变，也没有引入通用状态机、Event Sourcing 或完整 Tool Call Registry。最终验收发现的第二个 `--resume` 入口顺序问题不属于本次修复范围，留待单独处理。

### 记录 9：最终验收发现并修复显式 `--resume` 的入口顺序缺口

- **最终验收发现的问题**：记录 8 的 multi-tool recovery safety blocker 修复后，最终验收继续按 PLAN 检查显式 resume 的 State Load Failure 要求，并沿真实入口审查 `__main__ → argparse → cli(resume_path=...)` 调用链。检查发现当时 `cli()` 的顺序实际是 `configure_model() → load_state()`。因此当用户显式指定的 state file 已损坏、同时 `.env` 或模型配置缺失时，进程会先输出 `CONFIG_ERROR` 并返回 exit code 2，根本不会执行 state load/validation，也就不会产生 PLAN 要求的 `State Load Error`、`state.load_failed` 和对应的 non-zero resume failure 语义。
- **原测试为什么没有覆盖**：原 `test_explicit_resume_load_failure_is_observable_nonzero_and_noninteractive` 在调用 `cli()` 前把 `configure_model` monkeypatch 成了 no-op。该测试只能证明“假设模型配置已经成功，损坏 state 会被拒绝”，无法覆盖“state 与模型配置同时无效时哪个错误先发生”的真实入口顺序。因此 state load failure、原文件保护、无交互和 non-zero exit 的断言虽然通过，仍掩盖了配置错误抢先返回的问题。
- **依赖确认与最小修复**：`load_state()` 及 `validate_runtime_state()` 只负责文件读取、JSON/schema/structure 校验，不依赖 `client` 或 `MODEL`；用于失败路径的 `EventLogger("state-load-failure", ... )` 同样不依赖模型配置，可以在尚未配置模型时正常输出并写入 `state.load_failed`。修复只调整 `cli()` 初始化顺序：显式 `--resume` 先 load/validate state，失败则使用独立 failure logger 记录事件并返回 exit code 3；state 合法后才执行 `configure_model()`，随后再建立恢复 Session 的 logger、恢复 Todo 并进入 `resume_run()`。普通非 resume 启动仍保持原顺序：先配置模型，成功后才创建新 Session/state、记录 `session.started` 和 initial checkpoint。
- **补充的入口回归测试**：原显式 resume load-failure 测试改为不替换 `configure_model()`，而是把 `ROOT_DIR` 指向没有 `.env` 的临时路径，同时提供 truncated state。测试断言真实配置检查尚未获得执行机会：CLI 返回 3、输出 `State Load Error`、stderr 不含 `CONFIG_ERROR`、写入 `state.load_failed`、不请求任何用户输入且 state 原始字节不变。另新增“valid resume state + missing model configuration”测试，确认 state 校验成功后返回 `CONFIG_ERROR`/exit code 2，不写 `state.load_failed`、不进入交互且不修改 state。普通启动的 missing-configuration 测试也补充断言：不读取用户输入，并且不创建 Session state directory，确认非 resume 行为没有回归。
- **验证结果与结论**：入口相关定向测试为 `6 passed, 201 deselected`；V06 全量测试最终为 `207 passed in 0.84s`；`git diff --check` 通过。显式 resume 现在稳定遵守“先验证用户指定的恢复状态，再配置模型”的错误优先级，而普通启动仍保持配置成功后才创建 Session 的原行为。由最终验收发现的第二个 blocker 至此关闭。

### 记录 10：multi-tool recovery 后续的 persisted-message consistency 修复

- **作为前次修复的 follow-up 被发现**：记录 8 建立了 multi-tool partial batch 的 durability、synthetic Result 补全和 LLM-call pairing preflight；记录 9 修正了显式 `--resume` 的入口顺序。继续按同一 recovery safety 不变量检查“恢复完成后的 state 再次被 `--resume`”以及“非法 persisted messages 从真实入口载入”时，又发现两个 consistency 缺口。第一，合法的完整 Tool Result blocks 后跟独立 Todo reminder 或 recovery text blocks，虽然能通过 API 前的 pairing preflight，却会在再次 resume 时被 recovery preparation 拒绝。第二，错序、错误 ID、partial Results 后夹带 text 等非法 persisted pairing 能通过 `load_state()`，直到 resume preparation 才抛出异常；该异常原先不属于 state-load failure 路径，因此不能稳定得到 `State Load Error + state.load_failed + exit 3`。
- **根因**：消息协议的两个边界使用了不一致的结构判断。`validate_llm_message_protocol()` 已允许合法结构为“完整、同序的 Tool Result blocks 在前，独立 text blocks 在后”，但旧 `prepare_recovery_messages()` 把紧邻 assistant multi-tool message 的 user content 当作纯 Tool Result 列表处理，遇到合法 trailing text 也会报错。与此同时，`validate_runtime_state()` 只校验顶层 state、message role/content 等基本结构，没有校验 persisted Tool Use/Result pairing；这使本应由 `load_state()` 拒绝的非法 state 延迟到 mutation-oriented recovery preparation 才暴露。原测试分别覆盖了 API preflight、首次 partial recovery 和 state 基本结构，但没有覆盖“带独立 text 的完整 recovery message 再次恢复”以及“非法 pairing 必须在 load 边界失败”。
- **修复边界**：不扩展 state schema，也不重新设计 multi-tool recovery。新增共享的 Tool Result/text 分段校验：user Tool Result message 只能由前置 `tool_result` blocks 和可选尾随 `text` blocks 组成，text 后不得再出现 Tool Result。`validate_runtime_state()` 现在调用 persisted-message protocol validation，使 `load_state()` 成为 persisted 合法性的主要判断边界：完整 batch 必须 ID 唯一、同序且全部配对，可以带 trailing text；unfinished Run 的最后一批可以保存无 text 的有序 Result 前缀；completed Run 不得保存 incomplete batch；`llm_call` 不得与 incomplete batch 共存；Tool/permission phase 指向的 ID 必须是下一项 unmatched Tool。API 前的 `validate_llm_message_protocol()` 仍承担更严格的不变量，只允许完整 pairing 进入 `client.messages.create()`。
- **再次恢复与 CLI defensive fallback**：`prepare_recovery_messages()` 现在保留合法 trailing reminder/recovery text；若 batch 尚未完整，则先临时分离 text、补齐 uncertain/not-executed synthetic Results，再恢复原 text 并追加本次独立 recovery blocks。因此已经补全过的 A/B/C 不会在再次 resume 时重复生成 synthetic Results。显式 resume 在模型配置和 `state.restored` 之前完成 load/validate 与 recovery preparation；其中 CLI 对 preparation 阶段 `StateLoadError` 的 catch 只是 defensive fallback，负责统一输出 `State Load Error`、记录 `state.load_failed`、保持原 state 不变并返回 exit 3，不能替代 `load_state() → validate_runtime_state()` 对 persisted message 合法性的主要校验。
- **新增回归测试**：新增测试验证完整 A/B Results 后的独立 Todo reminder 能通过 `llm_call` recovery 并保持 block 顺序；验证首次恢复形成 `A real + B outcome-unknown + C not-executed + recovery text` 后再次 resume 不丢结果、不重复 synthetic Results且仍通过 API preflight；参数化拒绝错误 Result ID/顺序、text-before-result、incomplete-plus-text 和 completed-with-incomplete 等非法 persisted pairing；真实 CLI 测试确认非法 pairing 即使同时缺少模型配置，也优先走 `State Load Error`、写入 `state.load_failed`、不产生 `state.restored`、不请求输入且不覆盖原文件；另有测试直接触发 preparation 的 defensive fallback，确认同一失败契约。新增定向测试结果为 `8 passed, 207 deselected`。
- **验证结果与当前边界**：V06 全量测试最终为 `215 passed in 1.05s`，`git diff --check` 通过；手工纯函数复现同时确认合法完整 Results + reminder 可重复恢复，错误配对会由 `load_state()` 直接拒绝。该修复只统一 persisted validation、recovery preparation 与 API preflight 的消息协议边界；partial multi-tool batch 仍可作为 unfinished Runtime checkpoint 存在，但绝不能直接发送给 LLM。没有扩展 persisted schema，没有改变 synthetic Result、checkpoint、Recovery Context、retry 或 Todo nag 的既有设计。

### 记录 11：最终验收发现并修复 checkpoint transition failure windows

- **发现的问题**：记录 10 完成后，V06 已有 `215 passed`，但最终验收继续按 PLAN 检查每个实际 checkpoint 前后的 crash/Ctrl-C window，而不是只检查最终状态。沿 `agent_loop()` 的真实执行顺序发现三类由 Runtime 自己产生的不自洽 durable snapshot。第一，multi-tool batch 中 A Result 已 append 并以 `tool_result.recorded` checkpoint 成功后，到下一次循环设置 `permission_wait/B` 之前，persisted `interruption_info` 仍可能是 `tool_result_recording/A`；实际用当前 `save_state() → load_state()` 复现时，messages 已含 A Result、unmatched Tools 为 B/C，但 validator 因 current ID 是 A、下一 unmatched ID 是 B 而返回 `StateLoadError: interrupted tool must be the next unmatched tool use`。第二，最后一个 Tool Result 先 checkpoint，`current_run.round = N + 1` 和 interruption 清除稍后才 checkpoint；若在两者之间中断，完整 Tool batch 已持久化但 round 仍是 N，resume 会再次从 N 调用模型，在 `N = MAX_ROUNDS` 时可因 restart 多获得一次该 Round。第三，无 Tool 的 final assistant response 先随 `interruption.cleared` checkpoint 保存，当时 Run 仍是 `running`；`completed` 原本要等 `agent_loop()` 返回 `run_once()/resume_run()` 后才保存，因此该窗口中断会把已有可靠 final response 的 Run 当作 unfinished 再次交给模型。
- **共同根因**：这不是三个互不相关的功能错误，而是同一种 checkpoint transition ordering 问题：代码先持久化一部分“新事实”，随后才更新描述该事实的 `interruption_info`、`round` 或 `status`，并依赖第二次 checkpoint 收敛。原子 JSON replace 只能保证单次 state file 写入完整，不能让两个分开的 checkpoint 自动成为事务；因此中间 snapshot 虽然 JSON 完整，却不能被 Runtime 正确解释。此次确立的最小不变量是：每个成功 checkpoint 都必须单独构成自洽、可加载且可安全恢复的状态，messages 中已 durable 的事实必须与 phase、Round 和 Run status 描述同一个一致性边界。
- **multi-tool transition 修复**：非末尾 Tool 的真实 Result append 后，不再带着旧 Tool 的 `tool_result_recording` phase 直接保存；Runtime 先把 `interruption_info` 更新为下一 Tool 的 `permission_wait`，再用一次 `tool_result.recorded` checkpoint 提交。例如 A 的新 snapshot 同时表达“A Result 已可靠记录”和“B 尚未执行、正等待 permission”，因此 checkpoint 前失败仍按 A uncertain 恢复，checkpoint 后失败则保留 A completed 并从 B pending 恢复。最后一个 Tool Result 不再提前单独保存，而是在 Todo reminder 处理完成后，与 `round = N + 1`、`interruption_info = None` 一次 checkpoint；完整 batch 一旦 durable，下一模型 Round 也已经 durable，不再存在重复 Round N 的窗口。
- **terminal transition 修复**：新增最小 `_finish_agent_loop()` helper，只负责把 Agent Loop 的 terminal result 转换成一个 durable Run snapshot：同步设置 `status`、`round`、清除 `interruption_info`，并以 `run.completed` 或 `run.failed` checkpoint 后再返回。normal completion、non-retryable LLM/API failure、retry exhausted、protocol failure 和 `MAX_ROUNDS_EXCEEDED` 均使用该边界；`run_once()` 与 `resume_run()` 不再承担返回后的首次 terminal state 保存。无 Tool 的 response 会先确认存在有效 final text，再把 assistant response 与 `status=completed` 一次提交，因此 durable state 不再出现“final response 已存在但 Run 仍为 running”。有 Tool 的 assistant response 也会在持久化前先完整校验 Tool Use 的 ID、name 和 input，避免把 malformed Tool batch 写入正式 state。
- **新增回归测试**：新增五个针对实际窗口的确定性测试。`test_checkpoint_after_completed_tool_points_to_next_pending_tool` 在 A Result 的原 checkpoint 已成功、B 尚未进入时立即中断，断言 state 可加载、A real Result 保留、phase 已指向 `permission_wait/B`、B/C 未执行且 resume 不 replay；`test_last_tool_result_checkpoint_advances_round_before_interruption` 在最后 Result checkpoint 后立即中断，断言 persisted round 已是 2、interruption 已清除，恢复后的模型调用只出现 Round 1、2；`test_completed_response_is_durable_before_control_returns` 在 `run.completed` checkpoint 后中断，断言 state 已是 completed、final response 存在且 `resume_run()` 不调用模型；`test_max_round_boundary_after_tool_batch_cannot_repeat_last_round` 在最大 Round 的 Tool batch durable 后中断，恢复从 `MAX_ROUNDS + 1` 边界直接失败，不追加 API call；`test_every_successful_checkpoint_in_multi_tool_run_is_loadable` 包装真实 checkpoint，在正常 multi-tool 轨迹每次保存后立即执行 `load_state()`，防止以后再次出现“最终测试成功但中间正式 snapshot 不可恢复”。
- **验证结果与当前边界**：上述定向测试为 `5 passed, 215 deselected`；V06 全量测试最终为 `220 passed in 0.63s`，`git diff --check` 通过。修复只重排现有 messages、`current_run` 和 `interruption_info` 的提交时机，并增加一个 terminal commit helper；没有扩展 persisted state schema，没有引入 StateManager、通用状态机或跨文件事务，也没有修改 Recovery Context、pairing/synthetic Result 语义、Todo nag、retry 参数或 `MAX_ROUNDS`。workspace 与 state 仍不具备 exactly-once 或跨文件事务保证，uncertain Tool 仍按既有 targeted reconciliation 处理。

## 已知限制（Known Limitations）

- 本地 JSON 适用于当前单进程 CLI，不提供并发写入、分布式锁或数据库事务。
- 原子 replace 不解决 workspace 与 state 的跨文件事务；uncertain Tool side effect 依靠恢复后检查。
- Runtime 不恢复已消失的 Shell 子进程，也不保证 Tool exactly-once。
- transient 分类只覆盖当前真实需要，不含 fallback model、circuit breaker 或通用恢复注册表。
- state schema 不兼容时明确失败，本版不实现迁移框架。
- V05 的 Tool、Permission、Hook、Todo 及模型工具选择波动等限制继续存在。

## 为什么需要下一版本（Why Next Version）

V06 能可靠延续 Session/Run，但 messages 与 Tool Result 仍会持续增长。Context Budget 与 Compact 属于 V07；本版完成后停止，等待用户检查，不自动进入 V07。
