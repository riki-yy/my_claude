# V05：Plan/Todo

V05 只研究一个问题：Agent 如何通过现有 Tool Runtime 显式保存准备做什么、正在做什么和已经完成什么，并让用户与模型看到同一份当前计划？

V05 以已经验收的 V04 为 baseline。Anthropic Messages API、通用 Agent Loop、六个文件/命令工具、静态 `TOOL_HANDLERS`、Permission、`PreToolUse` / `PostToolUse` Hooks、CLI/JSONL Observability 和 `tool_use/tool_result` 回路全部保留；本版只增加普通工具 `todo_write` 和进程内 Todo State，不进入 V06 持久化。

## 架构与范围

```text
LLM
→ tool_use(todo_write，新的完整 TodoList)
→ PreToolUse Hooks（既有 Observability + Permission）
→ TOOL_HANDLERS
→ todo_write handler
   → 校验整个 todos 数组
   → 全部合法后整体替换内存 Todo State
   → render 当前最新完整 TodoList
→ PostToolUse Hooks（既有 Tool Result Observability）
→ 同一份 render 文本用于 CLI 与 tool_result
→ tool_result 放回 messages
→ LLM
```

`todo_write` 是第七个普通 Tool。Agent Loop 没有 Todo 专用分支，V04 Hooks 也不维护 Todo State。

每次调用必须提交新的完整 Todo State：

```json
{
  "todos": [
    {
      "id": "read",
      "content": "读取 README",
      "status": "pending"
    }
  ]
}
```

每项只包含 `id / content / status`。内部状态只允许：

- `pending`
- `in_progress`
- `completed`

handler 先校验整个数组，至少保证：

- `id` 是非空字符串且在列表内唯一；
- `content` 是非空字符串；
- `status` 是三个允许值之一；
- 同一时刻最多一个 `in_progress`；
- Todo 总数最多 20；
- input 与每个 item 不包含额外字段。

只有所有项目都合法时，handler 才整体替换当前内存 Todo State。任意项目失败都会返回 `INVALID_TOOL_INPUT`，旧状态的内容、顺序和 status 完全不变。不设计 `add / update / delete` action；新增、修改、删除和状态变化都由下一次完整列表替换表达。空列表是合法的完整状态，可用于清空 Todo。

`completed` 只能由模型再次显式调用 `todo_write` 写入。Runtime 不根据 read、write、bash 或其他 Tool Result 自动推导 Todo 状态。

## Todo render

更新成功后严格按以下顺序处理：

```text
替换内存中的最新 TodoList
→ render 当前最新 TodoList
→ CLI 展示同一份 render 文本
→ 该文本作为 todo_write Tool Result 放回 messages
```

状态符号只用于展示：

- `pending → ○`
- `in_progress → ›`
- `completed → ✓`

例如：

```text
✓ 读取 README
› 修改代码
○ 运行测试
```

render 逐项完整保留原始 `content`，不摘要、不改写、不截断，只添加状态符号和基本排版。CLI 与模型不使用两套 Todo 输出格式，也不会为了 CLI 简洁丢失 Todo content。JSONL 仍按继承的 Observability 规则只保存有限、脱敏的结果摘要，不保存完整长文本。

## 使用方式

在项目根目录准备共享 `.env`，然后：

```bash
cd v05_plan_todo
python3.12 -m pip install -r requirements.txt
python3.12 -m pytest -q
python3.12 agent.py
```

CLI 每个 session 写入 `logs/<session_id>.jsonl`。Todo State 只存在于当前 Python 进程，退出后不会恢复。

## 自动测试

测试使用 Fake Model 替换生产代码同一个 `client.messages.create` 调用表面，不读取 `.env`，不访问网络。除完整继承 V04 的 142 项测试外，V05 覆盖：

- `todo_write` schema 与 `TOOL_HANDLERS` 普通工具接入；
- 完整列表的创建、整体替换和清空；
- 空/空白 ID、重复 ID、空/空白 content、非法 status、多个 `in_progress`、额外/缺失字段、非对象 item、非法顶层 input 和超过 20 项；
- 任意校验失败时旧状态保持不变；
- render 的三态符号和长文本、换行、空格等原始 content 保真；
- CLI、Tool Result 与 messages 使用同一次完整 render；
- 其他工具成功不会自动修改 Todo State。

实际结果：

- 2026-09-04，实施前在 `v04_hooks/` 执行全量测试，142 项通过。
- 2026-09-04，V05 首轮全量测试 162 项通过；补充 20 项边界、普通 Runtime 错误回路和 JSONL 有限摘要检查后，最终全量 164 项通过。
- 2026-09-07，为真实 Demo 发现的 `todo_write no key arguments` 增加专用数量摘要和回归测试后，V05 全量 165 项通过。

## 手动 Demo Cases

以下 Demo 使用根目录 `.env` 指向的真实 Anthropic-compatible 服务。验收以 Tool、Permission、Todo、文件副作用、CLI 和 JSONL 的可观察事实为准，不要求模型文本逐字一致。

### Case 1：显式计划后完成多步任务

- **用户输入**：`先用 todo_write 建立计划：读取 README、创建 todo_demo.txt、确认文件内容；然后逐项执行并更新状态。`
- **预期轨迹**：模型提交完整 TodoList → handler 全量校验并整体替换 → render 最新 TodoList → CLI 与 Tool Result 共用渲染文本 → 模型显式推进 `in_progress / completed` → 文件工具照常执行。
- **预期效果**：创建并重新读取 `todo_demo.txt`；CLI 和模型收到相同的完整 Todo 渲染；三个项目最终显式完成。
- **验收重点**：完整状态替换、先更新后 render、单一无损输出、显式完成和普通 Tool Runtime 路径。
- **实际结果（2026-09-04，DeepSeek-V4-Flash）**：通过。模型依次把三项完整 TodoList 渲染为“第一项 `›`”到逐项推进，最终四次成功 render 的最后一次为三项全部 `✓`。期间通过继承的普通 Runtime 调用 `glob`、`read_file`、`write_file` 和 `read_file`，创建并回读 `todo_demo.txt`，最终回答正常结束。文件实际内容为 `todo_demo 文件已创建。`。JSONL 共 44 条事件、sequence 连续、公共字段完整；4 次 `todo_write` 均为 `tool.started → permission.allowed → tool.completed`。日志：`logs/0e70a49e-80fe-404f-addb-b5417a24fb6d.jsonl`。

### Case 2：非法完整状态被原子拒绝

- **最小前置条件**：同一 session 中先用合法 `todo_write` 建立至少两个 Todo。
- **用户输入**：`再次调用 todo_write 提交完整列表，但让两个项目同时为 in_progress；务必实际调用一次这个非法输入。如果工具拒绝，检查并告诉我原计划是否保持不变。`
- **预期轨迹**：handler 检查整个数组并发现多个 `in_progress` → 错误 Tool Result → 不发生部分替换 → 模型根据错误说明结果。
- **预期效果**：`todo_write` 返回 `INVALID_TOOL_INPUT`，调用前的完整 Todo State 保持不变。
- **验收重点**：全量校验、最多一个 `in_progress` 和原子失败。
- **实际结果（2026-09-04，DeepSeek-V4-Flash）**：通过。同一 session 的首个 run 建立 `› 保留第一项 / ○ 保留第二项`，第二个 run 实际提交两个 `in_progress`，handler 返回 `INVALID_TOOL_INPUT: at most one todo may be in_progress`，模型不再调用工具并明确说明旧计划仍为第一项 `in_progress`、第二项 `pending`。JSONL 共 20 条事件、两个 run 均正常完成，非法调用事件为 `tool.started → permission.allowed → tool.failed`。原子不变性同时由 Fake Model/handler 自动测试直接检查。日志：`logs/dcabf69d-0408-4b0e-8834-f3a3d223caea.jsonl`。

### Case 3：Permission 拒绝后不得自动完成 Todo

- **最小前置条件**：`secrets/todo_denied.txt` 不存在；敏感路径会命中 V03 继承的 Ask 规则。
- **用户输入**：`用 todo_write 建立一个“创建 secrets/todo_denied.txt”的 Todo 并设为 in_progress；然后只调用一次 write_file 写入 denied。权限确认时我会拒绝。拒绝后再次调用 todo_write 提交完整列表，让该项保持 pending，不要标为 completed，最后说明结果。`
- **确认输入**：`n`
- **预期轨迹**：合法 Todo 写入 → write_file Permission Ask → 用户拒绝 → Permission Denied Tool Result → Runtime 不修改 Todo → 模型显式提交完整列表使项目保持未完成。
- **预期效果**：文件不存在；Todo 没有被自动完成；最终 render 显示 `○`。
- **验收重点**：显式更新、三态边界，以及 Todo、Permission、V04 Hooks 各自职责不变。
- **实际结果（2026-09-04，DeepSeek-V4-Flash，确认输入 `n`）**：通过。模型先写入 `› 创建 secrets/todo_denied.txt`，随后只调用一次 `write_file`；敏感路径触发 Ask，拒绝后 handler 未执行，`PostToolUse` 记录 `PERMISSION_DENIED`。模型再显式提交完整列表并得到 `○ 创建 secrets/todo_denied.txt`，最终说明任务未完成且正常结束。目标文件不存在。JSONL 共 22 条事件、sequence 连续，关键顺序为 Todo 成功 → `permission.required → permission.denied → tool.failed` → Todo 成功 → run 完成。日志：`logs/0befad15-543a-4a24-8c57-6d2c4346cf97.jsonl`。

## 验收摘要

- 2026-09-04：V04 baseline 142 项通过；2026-09-07，V05 最终 Fake Model/Runtime 测试 165 项通过，`agent.py` 与测试语法编译通过。
- 根 `.env` 的 `DeepSeek-V4-Flash` 完成三个真实 Demo：多步计划全部完成、非法完整状态原子拒绝、Permission 拒绝后显式保持未完成。
- 三份最终 Demo JSONL 均可逐行解析，公共字段完整、sequence 连续且不含当前 API Key；事件数分别为 44、20、22，工具与 Permission 顺序符合预期。
- `todo_demo.txt` 已创建并回读；`secrets/todo_denied.txt` 保持不存在。V05 到此停止，不进入 V06。
- 2026-09-07：额外真实 Coding Task 验收通过；`agnes-2.0-flash` 在同一 session 中分别完成网页版番茄钟和网页版贪吃蛇，实现过程中通过完整 TodoList 显式跟踪并完成四个步骤，且能根据工具失败继续验证和收尾。日志：`logs/1169ee9f-66e2-4069-a8f1-4bf98bbfbb25.jsonl`。
- 2026-09-07：用户确认 V05 的自动测试、正式 Demo 和额外真实 Coding Task 均通过；版本完成并停在 V05，等待用户亲自提交 Git。

## 相对 V04：新增、修改和保留

**新增**：`todo_write` schema/handler、最多 20 项的进程内 `TODO_STATE`、三态常量、无损 `render_todos()`，以及 Todo 机制与端到端测试。

**修改**：System Prompt 增加完整列表和显式完成规则；既有 Tool Result 摘要函数仅为成功的 `todo_write` 保留完整 CLI display，JSONL 继续使用有限摘要。

**保留**：V04 的 API 调用方式、通用 Agent Loop、六个既有工具与 handler、静态 `TOOL_HANDLERS`、Permission Rule 与优先级、用户确认、Hooks 与异常语义、最大轮次、CLI/JSONL schema、工作区和 Shell 技术安全底线。

## 开发与调试记录（Development / Debugging Notes）

### 记录 1：V04 baseline 验证

- **现象**：V05 开发前需要确认 V04 当前实现稳定。
- **触发方式**：在 `v04_hooks/` 执行 `python3.12 -m pytest -q`。
- **原因**：版本继承规则要求先验证 baseline，不是代码故障。
- **解决方案**：只复制 V04 的必要源码、正式测试、依赖、忽略规则和 README 到独立 `v05_plan_todo/`，不复制 logs、cache、pyc 或 Demo 产物。
- **影响范围**：V01–V04 均未修改；V05 从已验证 V04 建立。
- **验证方式与结果**：V04 全量 142 项通过，用时 0.81 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

### 记录 2：V05 最小实现与首轮自动测试

- **现象**：普通工具的既有 CLI Observability 默认显示有限结果预览，而 V05 要求 Todo content 在 CLI 与模型侧使用同一份完整 render、不得截断。
- **触发方式**：对照 V05 render 规则检查 V04 `_tool_result_summary()` 和 `PostToolUse` 输出路径。
- **原因**：V04 面向文件和命令结果的 bounded preview 不能直接表达 V05 的完整 TodoList 展示要求。
- **解决方案**：保持 Agent Loop 和 Hook 接线不变；`todo_write` handler 返回 render 文本本身，既有 PostToolUse 摘要函数只对该成功结果把原文作为 CLI display，JSONL fields 仍限长。用集成测试比较 CLI display、Tool Result content、下一轮 messages 和 handler render 完全一致。
- **影响范围**：只新增 Todo Tool/State/render 和对应成功展示分支；其他工具摘要、Permission、Hook 与 Tool Result 行为不变。
- **验证方式与结果**：V05 首轮 162 项通过，用时 1.35 秒；增加恰好 20 项、非法更新普通 Runtime 回路和 JSONL 不保存完整长 Todo 的检查后，最终 164 项通过，用时 0.96 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

### 记录 3：真实 API 首次受沙箱网络限制

- **现象**：Case 1 首次运行在第一轮返回 `Connection error.`，没有到达 Tool Use。
- **触发方式**：在默认受限沙箱中以非 TTY stdin 运行 `python3.12 agent.py`。
- **原因**：执行环境默认限制网络，不是 Todo、Hook、Agent Loop 或服务协议故障。
- **解决方案**：获得网络执行授权后，继续使用同一根 `.env` 和相同 prompt 重跑；没有修改模型配置或绕过 Anthropic SDK 调用链。
- **影响范围**：无生产代码变化；失败 session 作为实际调试证据保留。
- **验证方式与结果**：失败日志 `logs/e4fbbe29-c248-4d4c-933d-18a21fa2a22b.jsonl` 明确记录 `llm.failed`；授权后的三个最终 Demo 均能调用真实服务并完成。
- **平台/环境**：Codex macOS 受限沙箱、Python 3.12 已观察。
- **状态**：已解决。

### 记录 4：真实服务间歇性 429 限流

- **现象**：前三个 Demo 的若干尝试在已完成部分 Todo 工具循环后，服务返回 `429 global_concurrency_rate_limit_exceeded`；一次 Case 3 尝试在首轮失败后，预先管道输入的 `n` 被 CLI 当作下一条用户消息，而不是权限确认。
- **触发方式**：连续使用根 `.env` 的 `DeepSeek-V4-Flash` 执行多轮 Tool Use Demo；失败日志包括 `logs/21ec0c5c-1fd2-472c-8bd8-d9402521d09e.jsonl`、`logs/403ada59-db57-4d93-a4ad-1a9a1b466a6d.jsonl`、`logs/5226b033-dc18-4c6b-b9cf-34ead46da603.jsonl`。
- **原因**：服务明确返回全局并发限流；管道输入错位是模型在到达 Permission 前失败后，CLI 按既有外层输入循环继续消费后续行的结果。
- **解决方案**：不在 V05 提前增加 V06 的 retry/state 机制；保留失败日志，并在服务可用时用相同场景重新执行，最终只把完整结束的 session 计为 Demo 通过。
- **影响范围**：无生产代码变化；模型错误处理、非 TTY 输入和 V04 Agent Loop 行为保持原样。
- **验证方式与结果**：最终 Case 1/2/3 日志分别为 `0e70a49e-...`、`dcabf69d-...`、`0befad15-...`，均无 `llm.failed` 且以 `run.completed` 结束；三个 Demo 全部通过。
- **平台/环境**：macOS、Python 3.12、DeepSeek-V4-Flash 与当前 Anthropic-compatible 服务已观察；其他服务未实际验证。
- **状态**：已解决（通过重跑完成验收；Runtime 自动重试仍按版本边界留待 V06 讨论）。

### 记录 5：完整 CLI render 与有限 JSONL 摘要的边界

- **现象**：若直接复用同一个事件 `display` 落盘，CLI 所需的完整长 Todo content 也会完整进入 JSONL，与继承的有限日志摘要规则冲突。
- **触发方式**：增加超过 `SUMMARY_LIMIT` 的 Todo content 集成测试，并检查 CLI、Tool Result、messages 与 JSONL。
- **原因**：V04 的 `EventLogger.emit()` 会同时打印并序列化同一 event；V05 的 CLI display 首次要求不截断工具结果。
- **解决方案**：EventLogger 仍先用 handler 的同一 render 打印 CLI；仅在序列化成功 `todo_write` 事件时浅复制 event，并把 JSONL `display` 换成已经生成的有限、脱敏 preview。Tool Result 和 `messages` 保持完整 render。
- **影响范围**：只影响成功 `todo_write` 的 JSONL display 落盘；CLI、模型、Agent Loop、其他工具和 event schema 不变。
- **验证方式与结果**：长 content 集成测试确认 CLI 与 Tool Result 完整相同、JSONL 不含完整长 content；最终 V05 全量 164 项通过。三份 Demo JSONL 不含当前 API Key。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

### 记录 6：todo_write Tool Call 错误显示 no key arguments

- **现象**：真实 API Demo 中，模型已经传入非空 `todos` 数组，但 CLI 的调用行显示 `[Tool Call] todo_write no key arguments`；后续 Todo Tool Result 与 handler 行为均正常。
- **触发方式**：任意让真实模型或 Fake Model 以 `{"todos": [...]}` 调用 `todo_write` 的场景；现有三份最终 Demo 日志都能观察到修复前的该文案。
- **原因**：`_tool_call_summary()` 的专用字段表只有 V04 的六个工具，没有 `todo_write`。通用 fallback 虽取得 `todos` key，但只把字符串、数字、布尔值和 `None` 加入展示；`todos` 是 list，因此 `display_fields` 为空并落入 `no key arguments`。
- **解决方案**：只在 `_tool_call_summary()` 增加 `todo_write` 专用分支。当 `todos` 是数组时仅计算并展示 `todos=<数量>`，不遍历或 dump Todo item content；完整 TodoList 继续只由既有 Tool Result render 展示。
- **影响范围**：只修改 `todo_write` 的 Tool Call CLI/JSONL 参数摘要。handler、Todo State、Hook、Permission、Tool Result、render 和 Agent Loop 均未改变。
- **验证方式与结果**：新增 `test_todo_write_tool_call_summary_shows_count_without_dumping_todos`，确认 CLI 显示 `[Tool Call] todo_write todos=3`、不再出现 `no key arguments`，结构化摘要只有数量且不含 Todo content。该测试单独 1 项通过；V05 全量 165 项通过，用时 0.49 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

### 记录 7：额外真实 Coding Task 验收

- **现象**：在正式 Demo 之外，真实模型使用 V05 连续完成了网页版番茄钟和网页版贪吃蛇两个多步 Coding Task；两个任务都建立四项完整 TodoList，并从单项 `in_progress` 推进到最终四项全部 `completed`。执行过程中，番茄钟的首个组合验证脚本返回 `SHELL_NONZERO_EXIT`，贪吃蛇首次列目录返回目录不存在，但 Agent 都读取 Tool Result 后继续执行，没有静默完成 Todo 或提前结束。
- **触发方式**：通过 CLI 要求从零使用原生 HTML/CSS/JavaScript 实现番茄钟和贪吃蛇，完成后自行检查并尽可能验证；真实 session 日志为 `logs/1169ee9f-66e2-4069-a8f1-4bf98bbfbb25.jsonl`。
- **原因**：两次工具失败分别来自模型生成的首个验证命令自身错误，以及任务开始时目标目录尚不存在；不是 `todo_write`、Hook、Permission 或 Tool Runtime 故障。
- **解决方案**：未修改 Runtime。Agent 根据失败 Tool Result 调整后续动作：番茄钟改用拆分后的 HTML、JavaScript、CSS 检查完成验证；贪吃蛇创建目标目录后继续写入文件并分别检查页面结构、游戏逻辑和样式。
- **影响范围**：只产生真实 Coding Task 的工作区文件和 JSONL 使用证据；V05 实现与测试没有因任务特例增加分支，V06 能力未引入。
- **验证方式与结果**：`agnes-2.0-flash` 的番茄钟 run 在 6 轮后以 `run.completed` 结束，Todo 最终四项全 `✓`；贪吃蛇 run 在 4 轮后以 `run.completed` 结束，Todo 最终四项全 `✓`。整个日志共 109 条事件，两个 Coding Task 均完成文件写入和多项命令检查。2026-09-07 用户确认额外真实 Coding Task 验收通过。
- **平台/环境**：macOS、Python 3.12、`agnes-2.0-flash` 与当前 Anthropic-compatible 服务已验证；Windows、Linux、浏览器端人工视觉与交互未在该日志中实际验证。
- **状态**：已解决。

## 已知限制（Known Limitations）

- Todo State 只保存在当前进程内，退出、崩溃或重启后丢失。
- Todo 只有 `id / content / status` 和三个状态，不包含依赖、优先级、子任务、负责人或 Workflow。
- 每次更新必须由模型提交完整列表；Runtime 不强制模型遵循计划，也不自动根据其他工具结果推进状态。
- Todo 上限固定为 20。
- V04 的 Hook、Permission、显式 Bash matcher、单次 Ask、真实模型工具选择波动，以及非 TTY Demo 输入方式等限制全部继承。

这些限制符合 V05 的最小范围；本版不实现持久化、Task Graph 或 Todo Framework。

## 为什么需要下一版本（Why Next Version）

Todo 与 messages 仍只存在于内存。进程退出或崩溃后无法恢复计划和会话，这个真实剩余限制形成 V06 状态与可靠性的动机。V05 完成后必须停下等待用户验收，不自动进入 V06。
