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

若进程可能在 Tool 已产生副作用、但 Tool Result 尚未可靠记录时退出，resume 不假设成功、不假设失败，也不自动 replay。Runtime 移除无法与 Tool Result 合法配对的尾部 uncertain `tool_use`，加入最小 recovery context，要求模型通过 `read_file`、`grep`、`bash`、`git diff`、`pytest` 等已有工具检查真实 workspace 后继续。该 context 不伪装成 Tool Result。

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
- 429/5xx retry、Attempt 映射、Retry-After、backoff/jitter 和硬上限；
- 五次调用后 retry exhausted，current Run 进入 failed；
- Tool/User Error 不触发 API retry。

实施结果：V05 baseline 165 项通过；V06 当前全量 189 项通过。

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
- 验收：同一 run_id/round 恢复；模型收到 recovery context，先检查真实 workspace；write_file 不被 Runtime 自动 replay；最终文件和总结一致。
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

## 已知限制（Known Limitations）

- 本地 JSON 适用于当前单进程 CLI，不提供并发写入、分布式锁或数据库事务。
- 原子 replace 不解决 workspace 与 state 的跨文件事务；uncertain Tool side effect 依靠恢复后检查。
- Runtime 不恢复已消失的 Shell 子进程，也不保证 Tool exactly-once。
- transient 分类只覆盖当前真实需要，不含 fallback model、circuit breaker 或通用恢复注册表。
- state schema 不兼容时明确失败，本版不实现迁移框架。
- V05 的 Tool、Permission、Hook、Todo 及模型工具选择波动等限制继续存在。

## 为什么需要下一版本（Why Next Version）

V06 能可靠延续 Session/Run，但 messages 与 Tool Result 仍会持续增长。Context Budget 与 Compact 属于 V07；本版完成后停止，等待用户检查，不自动进入 V07。
