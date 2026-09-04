# V04：Hooks

V04 只研究一个问题：当 Tool Runtime 已经出现 Permission、Tool Call/Result Observability、duration 和 result summary 等横切职责后，Agent Loop 如何只声明“工具生命周期到了哪里”，而不直接依赖这些具体实现？

V04 以已经验收的 V03 为 baseline。Anthropic Messages API、通用 Agent Loop、六个工具、静态 `TOOL_HANDLERS`、handler、`tool_use/tool_result` 回路、Permission Rule 与 CLI/JSONL 数据模型全部保留；本版只增加最小 Hook 注册表并改变横切职责的接入位置，不进入 V05 Plan/Todo。

## 架构与范围

```text
LLM tool_use
→ trigger_hooks("PreToolUse", context)
   → Tool Call Observability
   → Permission（allow / ask / deny）
→ handler（仅在 PreToolUse 未阻止时执行一次）
→ Runtime 构造 Tool Result 与 duration
→ trigger_hooks("PostToolUse", context)
   → Tool Result Observability + result summary
→ Anthropic tool_result
```

注册表是简单的 `dict[str, list[callback]]`。`register_hook(event, callback)` 追加 callback；`trigger_hooks(event, context)` 按注册顺序把同一个可变 context 传给 callback。当前只接受两个已经由 V03 真实职责证明需要的事件：

- `PreToolUse`：Tool Use 已验证并取得 `tool_name`、`tool_input` 后，handler 前触发。
- `PostToolUse`：Runtime 已取得成功、工具错误、未知工具或权限拒绝的 Tool Result，并计算 duration 后触发。

默认 Hook 顺序保持 V03 的既有可观察顺序：Tool Call 先记录，再进行 Permission；得到结果后统一记录 Tool Result。Permission callback 可在 context 写入 `tool_result` 来阻止 handler。Agent Loop 只判断是否已有结果，不包含 Permission、Tool Call 展示或 Tool Result summary 的具体实现。

本版不引入 HookManager、Middleware、Hook DSL、Plugin、动态加载、通用 Event Bus、独立 Error Hook 或 Hook 状态机。EventLogger 仍负责事件结构、CLI 展示与 JSONL 落盘；Hook 只负责生命周期调用时机。

## Callback 异常语义

- 任一 `PreToolUse` callback 抛出异常时，Runtime fail closed，构造 `HOOK_ERROR` Tool Result，绝不执行 handler；随后仍进入一次 `PostToolUse`，因此错误保留统一结果闭环。
- `PostToolUse` callback 抛出异常时，handler 不重试，真实 Tool Result 不被改写；Runtime 向 stderr 输出脱敏 Hook Warning，并继续把真实结果返回模型。
- callback 按注册顺序执行；某 callback 抛出后，本次 `trigger_hooks()` 立即失败，后续 callback 不再执行。

这样只定义当前版本需要的三个保证：Permission 不会因 Hook 异常被绕过、handler 不会重复执行、真实执行结果不会被虚假改写。

## 使用方式

在项目根目录准备共享 `.env`，然后：

```bash
cd v04_hooks
python3.12 -m pip install -r requirements.txt
python3.12 -m pytest -q
python3.12 agent.py
```

CLI 每个 session 写入 `logs/<session_id>.jsonl`。日志内容与终端来自同一次 `EventLogger.emit()`，不记录完整模型 prompt 或完整工具输出；参数、结果和异常经过摘要与脱敏。

## 自动测试

测试使用 Fake Model 替换生产代码同一个 `client.messages.create` 调用表面，不读取 `.env`，不访问网络。覆盖 V03 的 Agent Loop、工具、Permission、CLI/JSONL 回归，以及 Hook 顺序、context、事件隔离、四类结果闭环和 callback 异常边界。

实际结果：

- 2026-09-03，实施前在 `v03_permission/` 执行全量测试，131 项通过（其中 1 项来自当前未跟踪的 `demo_workspace/test_calc.py`，正式 `tests/test_agent.py` 为 130 项）。
- 2026-09-03，V04 首轮回归与新增 Hook 测试共 142 项通过。
- 2026-09-04，生命周期事件纯命名重构为 `PreToolUse` / `PostToolUse` 后，V04 全量 142 项再次通过。

## 手动 Demo Cases

以下 Demo 使用根目录 `.env` 指向的真实 Anthropic-compatible 服务。验收以 Tool、Permission、文件副作用、CLI 和 JSONL 的可观察事实为准，不要求模型文本逐字一致。

### Case 1：成功工具调用的 Hook 顺序

- **用户输入**：`读取 README.md，并告诉我标题。`
- **预期轨迹**：LLM → `PreToolUse`（Tool Call、Permission Allow）→ read_file → `PostToolUse`（成功、duration、summary）→ LLM Final。
- **预期效果**：最终回答标题 `# V04：Hooks`；CLI 与 JSONL 顺序为 `tool.started → permission.allowed → tool.completed`。
- **验收重点**：成功路径只通过 Hook 接入既有横切职责。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：通过。模型第一轮调用 `read_file("README.md")`，CLI 显示 Tool Call 与 147 行/10.0 KB 的成功摘要，第二轮正确返回标题 `V04：Hooks`。JSONL 顺序为 `tool.started → permission.allowed → tool.completed`，结果事件同时包含 duration 和有限 summary。日志：`logs/6eacb2d0-3cbe-4859-83ae-4dffaa57e9f1.jsonl`。

### Case 2：工具失败仍进入统一结果 Hook

- **最小前置条件**：`hook_missing.txt` 不存在。
- **用户输入**：`读取 hook_missing.txt；如果失败，告诉我具体原因，不要创建文件。`
- **预期轨迹**：`PreToolUse` → read_file 返回 `FILE_NOT_FOUND` → Runtime 构造错误 Tool Result → `PostToolUse` 记录 `tool.failed`、duration 和摘要 → LLM Final。
- **预期效果**：文件仍不存在；终端和 JSONL 明确记录工具错误。
- **验收重点**：成功和错误共享 `PostToolUse`，无需 Error Hook。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：通过。模型调用一次 `read_file("hook_missing.txt")`；handler 返回 `FILE_NOT_FOUND`，CLI 显示 Tool Error，错误 Tool Result 回传模型后得到准确最终说明。目标仍不存在。JSONL 为 `tool.started → permission.allowed → tool.failed`，失败事件包含 duration、error code 和 result summary。日志：`logs/b713db1a-47c8-4eaa-b12a-bf6014ba029d.jsonl`。

### Case 3：Permission 拒绝仍保持结果闭环

- **最小前置条件**：`secrets/hook_denied.txt` 不存在。使用敏感目录是因为 V03 已验收策略会 Ask；普通 `hook_denied.txt` 默认 Allow。
- **用户输入**：`请只使用一次 write_file，将 denied 写入 secrets/hook_denied.txt；出现权限确认时我会拒绝，拒绝后立即说明结果，不要尝试其他工具。`
- **确认输入**：`n`
- **预期轨迹**：`PreToolUse` 中 Tool Call → Permission Ask → 用户拒绝 → handler 不执行 → Permission Denied Tool Result → `PostToolUse` 记录 `tool.failed` → LLM Final。
- **预期效果**：目标不存在，JSONL 区分 Permission Denied 与 handler 工具错误，不产生成功事件。
- **验收重点**：V03 Permission 行为不变，只改变接入方式。
- **实际结果（2026-09-03，DeepSeek-V4-Flash，确认输入 `n`）**：通过。模型按提示只调用一次 `write_file`；`PreToolUse` 先记录 Tool Call，再触发敏感路径 Ask。拒绝后 handler 未执行，`PostToolUse` 记录唯一 `tool.failed`，error code 为 `PERMISSION_DENIED`、`success=false` 且包含 duration/summary。第二轮模型说明拒绝并结束，目标文件不存在，无 `tool.completed`。日志：`logs/46ff60a8-ccbb-4f2a-bcb7-08e311d22d62.jsonl`。

## 验收摘要

- 2026-09-03：V03 当前全量 baseline 131 项通过。
- 2026-09-03：V04 Fake Model/Runtime 测试 142 项通过。
- 2026-09-03：根 `.env` 的 `DeepSeek-V4-Flash` 完成成功、工具错误、Permission 拒绝 3 个真实 Demo。
- 3 份最终 Demo JSONL 均逐条解析检查；CLI 与 JSONL 的事件类型、顺序、error code、success、duration 和 result summary 一致，文件副作用符合预期。
- 2026-09-04：用户确认手动 Demo 验收通过；完成最终代码、测试、README、PLAN 和 JSONL 一致性复核，V04 正式收尾。

## 相对 V03：新增、修改和保留

**新增**：`HOOKS`、`register_hook()`、`trigger_hooks()`、`PreToolUse`、`PostToolUse`，以及最小 callback 异常语义。

**修改**：Permission 和 Tool Call Observability 注册到 `PreToolUse`；Tool Result Observability、duration 和 result summary 注册到 `PostToolUse`；Agent Loop 改为构造 context 并触发 Hook。

**保留**：V03 的 API 调用方式、TOOLS、TOOL_HANDLERS、六个 handler、Permission Rule 和判断顺序、用户确认、Tool Result、最大轮次、CLI/JSONL schema 与展示、工作区和 Shell 技术安全底线。

## 开发与调试记录（Development / Debugging Notes）

### 记录 1：V03 baseline 验证

- **现象**：V04 开发前需要确认 V03 当前实现稳定。
- **触发方式**：在 `v03_permission/` 执行 `python3.12 -m pytest -q`。
- **原因**：版本继承规则要求先验证 baseline，不是代码故障。
- **解决方案**：从 V03 当前实现复制必要生产代码和正式测试，只在独立 `v04_hooks/` 增加 Hooks。
- **影响范围**：V03 代码和其未跟踪 Demo 文件均未修改。
- **验证方式与结果**：131 项通过，用时 1.08 秒；其中 `tests/test_agent.py` 130 项，另 1 项为未跟踪 Demo 测试。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

### 记录 2：复制 baseline 时携带运行产物

- **现象**：首次复制 V03 目录时一并带入 `.pytest_cache`、`__pycache__`、历史 logs 和未跟踪 demo_workspace。
- **触发方式**：检查新目录文件列表和 Git 状态。
- **原因**：目录级复制包含了源码之外的本地运行状态。
- **解决方案**：在任何 V04 开发与 Demo 前删除新目录内这些复制品，只保留必要实现、测试、依赖与 README；没有删除或修改 V03 原件。
- **影响范围**：只清理尚未使用的 V04 新目录副本。
- **验证方式与结果**：重新运行正式测试，生产代码和 130 项继承测试完整。
- **平台/环境**：macOS 已验证。
- **状态**：已解决。

### 记录 3：首次真实 API 调用受沙箱网络限制

- **现象**：Case 1 首次运行在第一轮返回 `Connection error.`，未到达 Tool Use。
- **触发方式**：受限沙箱中以非 TTY stdin 运行 `python3.12 agent.py`。
- **原因**：执行环境默认限制网络；不是 Hook、Agent Loop 或服务协议故障。
- **解决方案**：获得网络执行授权后以相同根 `.env` 和 prompt 重跑，不修改模型配置。
- **影响范围**：无代码变化；失败 session 的 JSONL 保留为事实记录。
- **验证方式与结果**：失败日志为 `logs/5f6b734e-c8da-412e-871c-f25978acbafe.jsonl`。获授权后 `logs/9e6e74fa-eaad-4ee5-851f-350e4ff62040.jsonl` 成功调用 read_file；因当时 README 尚为复制的 V03 内容，返回 V03 标题，故该次只证明 API 可用，不计入最终 Case 1 验收。README 更新后重跑的最终 Case 1 通过。
- **平台/环境**：Codex macOS 受限沙箱、Python 3.12 已观察。
- **状态**：已解决。

### 记录 4：V04 最终回归与真实 Demo

- **现象**：Hook 重接线后需要证明 V03 行为未回归，并验证真实模型的成功、工具错误与权限拒绝都进入统一生命周期。
- **触发方式**：执行 V04/V03 全量 pytest、`py_compile`、CLI 三个 Demo，并解析最终三份 JSONL。
- **原因**：这是 V04 完成门槛，不是代码故障。
- **解决方案**：增加 12 项 V04 Hook 测试；真实 Demo 使用非 TTY stdin，逐项检查终端、事件序列、结构字段及文件副作用。
- **影响范围**：V04 新增 Hook 注册、接入与测试；V03 生产代码、Permission Rule、工具和已有用户文件不变。
- **验证方式与结果**：V04 142 项、V03 131 项全部通过；`agent.py` 与测试 `py_compile` 通过；3 个 Demo 通过。三份最终 JSONL 分别含 11、11、12 条合法 JSON，公共字段完整、sequence 连续且不含当前 API Key；拒绝文件和缺失文件均不存在。
- **平台/环境**：macOS、Python 3.12、DeepSeek-V4-Flash 已验证；Windows、Linux 和其他模型未实际验证。
- **状态**：已解决。

### 记录 5：生命周期事件命名与参考项目对齐

- **现象**：V04 最初使用本项目的 snake_case 事件名，与所参考的 `learn-claude-code` s04 生命周期事件名称不一致，增加源码概念对照成本。
- **触发方式**：用户在手动 Demo 完成后进行命名一致性检查。
- **原因**：首轮实现采用了语义等价的本项目命名，没有沿用参考项目事件名。
- **解决方案**：只将事件 key、默认注册、触发位置、错误文本、测试、README 和 PLAN 机械重命名为 `PreToolUse` / `PostToolUse`；没有改变 callback 顺序、context、Permission 或 Tool Result 控制流。
- **影响范围**：V04 Hook 事件公开名称及对应文档发生变化；V03、V05、handler、Permission Rule、Observability schema 和 Demo JSONL 事件类型均不变。
- **验证方式与结果**：2026-09-04，V04 142 项、V03 131 项全部通过；全项目搜索无旧事件名残留；三份真实 Demo JSONL 的关键事件序列与 README 一致。
- **平台/环境**：macOS、Python 3.12 已验证；Windows、Linux 未实际验证。
- **状态**：已解决。

## 已知限制（Known Limitations）

- Hook 注册表是进程级可变状态，不支持移除、优先级、运行时配置或并发隔离。
- context 是简单可变 dict，没有 schema 或静态类型保证；错误 key 会按 callback 异常语义处理。
- `trigger_hooks()` 遇到首个异常即停止，当前没有“继续其他 callback”或聚合异常策略。
- `PostToolUse` Hook 失败只有 stderr warning；为了不虚假改写真实结果，本版不另造 Hook Error 事件。
- V03 的显式正则 Bash matcher、单次 Ask、源码内规则和真实模型工具选择波动等限制全部继承。
- 自动化 PTY 中文输入的 readline/libedit 限制仍存在，Demo 使用非 TTY stdin。

这些限制符合 V04 的最小范围；本版不为未来插件或中间件预建框架。

## 为什么需要下一版本（Why Next Version）

Hooks 已解耦当前真实存在的工具生命周期横切职责，但 Agent 仍只靠 messages 隐式维持复杂任务进度，可能遗漏步骤、重复行动或过早结束。这才形成 V05 Plan/Todo 的动机。V04 完成后必须停下等待用户验收，不自动进入 V05。
