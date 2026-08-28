# V01：基础运行与 Agent Loop

V01 只研究一个问题：模型请求工具之后，Runtime 如何执行工具、返回结果，并继续调用模型直到获得最终文本？

## 这和普通模型调用有什么不同

普通调用是“输入一次、响应一次”。Agent Loop 会检查响应里是否存在 `tool_use`：没有就结束；有就执行工具，把匹配 `tool_use_id` 的 `tool_result` 作为下一条 User Message 交还模型，然后继续调用。

程序实际有两个循环：外层 CLI 循环负责持续接受人的任务；内层 Agent Loop 负责完成一条任务所需的多次模型调用。多个任务共享 session 对话历史，每条任务有独立 `run_id`。

## 为什么只有 echo

`echo(text)` 原样返回字符串。它足以验证完整 Tool Use 回路，又没有文件和 Shell 副作用。工作区访问、安全约束和多个工具带来的分发问题属于 V02。

当前工具直接通过条件分支执行。这不是最终工具架构，而是 V01 最容易观察的数据流。现在创建 Registry 或 Dispatcher，只会隐藏我们尚未真正遇到的问题。

## 配置与运行

项目所有版本共用根目录 `.env`：

```bash
cp .env.example .env
```

填写：

```dotenv
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...
MODEL_ID=...
```

程序严格沿用本机 `learn-claude-code` 的接入方式：Anthropic Python SDK、Messages API、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL` 和 `MODEL_ID`。根目录 `.env.example` 同步列出了参考项目当前提供的 Anthropic、MiniMax、GLM、Kimi 和 DeepSeek 配置示例。`ANTHROPIC_BASE_URL` 指向实现 Anthropic Messages 协议的 endpoint，因此实际模型不一定是 Claude。

使用系统 Python 3.12，不要求虚拟环境：

```bash
cd v01_agent_loop
python3.12 -m pip install -r requirements.txt
python3.12 agent.py
```

如果系统 Python 由包管理器保护，安装命令可能需要用户明确选择 `--break-system-packages`；项目不会自动修改全局环境。

输入 `exit`、`quit`、空行或 EOF 结束 session。若要验证工具，可明确要求模型“使用 echo 工具重复一段非敏感文字”。

CLI 使用 Python 标准 `readline`（macOS Python 通常由 `libedit` 提供）启用行编辑。输入过程中可以使用左右方向键移动光标，并在移动后使用 Backspace/Delete 修改文字。这里沿用 `learn-claude-code` 的四项最小 macOS 输入绑定，没有引入 CLI Framework。

## 测试

```bash
cd v01_agent_loop
python3.12 -m pytest -q
```

pytest 不调用真实 API，也不读取根目录 `.env`。测试直接替换 `client.messages.create`，用按顺序返回 Anthropic 风格响应的 Fake Model 验证正常、工具错误、模型错误、协议错误、最大轮次、CLI 和日志。

Fake Model 的价值是确定性：真实模型可能选择不用工具、措辞变化或受网络影响，不适合作为单元测试断言。真实服务只用于自动测试之后的手动验收。

## 手动验收场景 / Demo Cases

### Case 1：直接文本回答

- 用户输入：`请只回答：V01 ready，不要调用工具。`
- 预期轨迹：一次 LLM 请求，`tool_calls=0`，随后完成 run。
- 预期结果：最终回答包含 `V01 ready`。
- 验收重点：真实 API、CLI 和 JSONL 基础链路。

### Case 2：单次 echo Tool Use

- 用户输入：`请务必调用 echo 工具输出 hello。`
- 预期轨迹：LLM 请求 echo → Tool Result `hello` → 下一轮 LLM Final。
- 预期结果：最终回答包含 `hello`。
- 验收重点：最小 Agent Loop。

### Case 3：连续两次 echo

- 用户输入：`请分两次调用 echo 工具，先输出 alpha，再输出 beta，最后汇总。`
- 预期轨迹：两个 echo Tool Use/Result，可能在同一轮或连续轮次，最后正常结束。
- 预期结果：最终回答包含 `alpha` 和 `beta`。
- 验收重点：多工具调用或多轮循环。

### Case 4：编辑当前输入行

- 用户输入：先输入 `你好世界`，不要回车；按左方向键移动到“世界”前，使用 Backspace/Delete 修改前面的文字，再输入替换内容并回车。
- 预期轨迹：方向键只移动光标，不显示 `^[[D`；删除键编辑光标附近字符；只有回车后的最终文本进入 run。
- 预期结果：`You>` 后显示编辑后的文字，模型收到的也是编辑后的内容。
- 验收重点：验证外层 CLI 在真实 TTY 中支持方向键和删除编辑。

## 如何阅读运行过程

终端会显示类似：

```text
[LLM] requesting model=... round=1
[LLM Result] model=... | round=1 | duration=1.24s | input_tokens=20 | output_tokens=15 | stop_reason=end_turn | tool_calls=1
[Tool Call] echo
[Tool Result] hello
[Final] completed
```

`[LLM] requesting` 只表示请求已发出、Runtime 正在等待响应，不代表能够看到模型隐藏思维过程。`[LLM Result]` 只显示本地实际计算的耗时和 API 实际返回的 token、停止原因等数据；API 没有返回的字段不会伪造。

每个 session 同时写入：

```text
v01_agent_loop/logs/<session_id>.jsonl
```

每行是独立 JSON 事件，包含 session/run ID、时间、顺序、模型轮次、token、耗时、工具摘要和最终状态。默认不保存完整 messages 或大段内容；长内容会截断，常见密钥模式会脱敏。日志写入失败只告警，不让 Agent 失败。

## 异常处理

- 缺少根 `.env`、`ANTHROPIC_API_KEY` 或 `MODEL_ID`：启动前给出配置提示。
- API 或网络失败：当前 run 失败，CLI session 可以继续。
- 非法响应或缺失 Tool Use 字段：以 `PROTOCOL_ERROR` 结束当前 run。
- 未知工具或 echo 参数错误：作为 `is_error=true` 的 Tool Result 返回模型。
- 达到最大轮次：以 `MAX_ROUNDS_EXCEEDED` 停止，避免无限循环。
- JSONL 无法写入：stderr 告警，主流程继续。
- Ctrl-C：正常结束 session，不显示无意义 traceback。
- Python 构建缺少 `readline`：Agent 仍可启动，但当前终端不提供方向键行编辑；README 手动 Case 用于发现该平台限制。

V01 不自动重试。没有真实失败数据前加入重试，会立即引出退避、幂等、重试范围等新概念；这些问题留到 V06 的状态与可靠性阶段。

## 验收摘要

- 2026-08-28：26 个 Fake Model/CLI 测试全部通过。
- 2026-08-28：使用根 `.env` 配置的 `DeepSeek-V4-Flash` 完成三个真实 API Demo。直接回答为一轮且 `tool_calls=0`；单次 echo 为 Tool Use 后第二轮完成；连续 echo 在第一轮返回两个 Tool Use，第二轮完成。
- 本次真实 Demo 日志：`logs/d2a4e167-fbd2-4791-aea2-d40e67c6301d.jsonl`。

## 开发与调试记录（Development / Debugging Notes）

### 问题 1：模型配置示例没有严格对齐参考项目

- **现象**：最初的根 `.env.example` 只有自行概括的三项通用配置，没有 `learn-claude-code` 当前文件中的 Anthropic-compatible 服务示例。
- **触发方式**：把 `/Users/mimi/Desktop/agentloop/.env.example` 与 `/Users/mimi/Desktop/oo/learn-claude-code/.env.example` 直接比较。
- **原因**：首次实现时根据概括自行编写了示例，没有先读取用户本机的参考文件。
- **解决方案**：以本机参考项目为唯一来源，逐行同步 `.env.example`；同时把 Anthropic SDK 调用参数和依赖最低版本与参考实现核对。这样修正模型接入事实，不引入 Provider 抽象。
- **影响范围**：修改根配置示例、依赖下限和 `max_tokens`；Agent Loop、echo Tool、消息格式、Observability 和 Fake Model 测试结构保持不变。
- **验证方式与结果**：使用 `cmp` 比较两个 `.env.example`，返回状态为 0；修改后的全部 pytest 通过；随后真实 `DeepSeek-V4-Flash` Demo 三项通过。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 未实际验证。配置文件逐字节比较本身不依赖终端交互。
- **状态**：已解决。

### 问题 2：`[Thinking]` 文案错误暗示隐藏思维过程

- **现象**：每次模型请求前，CLI 固定显示 `[Thinking] 正在分析任务...`，容易让用户误认为 Runtime 能看到模型隐藏思维过程。
- **触发方式**：启动 V01 并提交任意非空输入。
- **原因**：`llm.started` 的 CLI 标签和 message 被静态写成 `Thinking/正在分析任务`，没有区分“等待 API 响应”与“模型可见输出”。
- **解决方案**：请求阶段改为 `[LLM] requesting model=... round=...`；返回阶段只显示实际测量或 API 明确返回的 model、round、duration、token、stop_reason 和 tool_calls，缺失值不伪造。
- **影响范围**：只修改 CLI 事件渲染及 `llm.completed` 的工具调用计数字段；Agent Loop 决策、模型请求、Tool 行为和 JSONL 公共结构保持不变。
- **验证方式与结果**：新增测试断言 CLI 不含 `[Thinking]` 和 `正在分析任务`，并验证 API 未提供 usage 时不显示伪造 token；当时 25 项 pytest 全部通过，之后真实 API Demo 显示新的 LLM 状态格式。
- **平台/环境**：macOS、Python 3.12 和真实 `DeepSeek-V4-Flash` 已验证；Windows 未实际验证。
- **状态**：已解决。

### 问题 3：Run 完成事件存在绕过摘要策略的原始展示字段

- **现象**：开发检查发现 `run.completed` 同时保存了脱敏摘要和原始 `message`，后者可能把完整最终文本或秘密写入 JSONL。
- **触发方式**：让 Fake Model 的最终文本包含测试 API Key，再检查 session JSONL。
- **原因**：`final_result` 使用了 `summarize()`，但为了 CLI 展示而增加的 `message` 仍直接引用原始 `final_text`。
- **解决方案**：让完成事件的展示字段复用同一份脱敏、截断后的摘要，并增加最终结果脱敏测试。
- **影响范围**：只收紧 Run 完成事件的日志内容；终端最终回答、模型消息、Agent Loop 和 Tool 行为保持不变。
- **验证方式与结果**：Fake Model 返回 `sk-finalsecret123` 后，断言 JSONL 不包含原值且包含 `[REDACTED]`；测试通过。
- **平台/环境**：macOS、Python 3.12 自动测试已验证；Windows 未实际验证。
- **状态**：已解决。

### 问题 4：CLI 输入时方向键被当作普通字符

- **现象**：用户输入中文后按左方向键，终端显示 `^[[D`，光标没有移动，无法在前文位置继续编辑。
- **触发方式**：在 V01 的真实 CLI 中输入文字但不回车，然后按左方向键。
- **原因**：实际排查确认当前 V01 没有加载 Python `readline`；`input()` 所在终端因此没有启用 readline/libedit 行编辑，方向键的 escape sequence 被作为普通输入。
- **解决方案**：在 CLI 启动路径加载标准 `readline`，并沿用 `learn-claude-code` 的四项 macOS/libedit 最小绑定；没有引入 prompt-toolkit、curses 或其他终端框架。
- **影响范围**：只影响外层 CLI 输入行编辑；Agent Loop、Anthropic API 调用、echo Tool、messages 和 Observability 均未修改。
- **验证方式与结果**：新增单元测试验证四项绑定确实被配置，V01 共 26 项 pytest 全部通过；在 macOS 伪终端中输入 `abc`，发送左方向键、Backspace、`X` 后，`input()` 实际收到 `aXc`；原有 pytest 继续覆盖 EOF 和 KeyboardInterrupt。真实物理键盘上的前向 Delete、修复后的中文光标编辑尚未实际复验，不能写成已通过。
- **平台/环境**：macOS 及其 libedit 伪终端路径已验证；macOS 真实物理键盘仅验证过修复前现象，修复后完整 Case 4 待用户复验；Windows 未实际验证；Linux 未实际验证。
- **状态**：部分解决。核心左移与 Backspace 路径已通过伪终端验证，真实中文输入、前向 Delete 和其他平台仍待手动验收。

## 已知限制（Known Limitations）

- 一个 `agent.py` 集中展示完整数据流。
- 只有硬编码 echo；新增多个工具会增加校验和条件分支。
- session 仅存在于内存，退出后不能恢复。
- 没有文件、Shell、权限、Plan、Hooks、Compact 或 Memory。
- Observability 是一个同步 `emit()`，不是 Event Bus。
- 完整 Trace、日志轮转和自动重试均未实现。
- readline/libedit 的真实中文编辑和前向 Delete 尚未完成用户终端复验；Windows、Linux 未实际验证。

## 为什么需要下一版本（Why Next Version）

V01 能证明 Agent Loop，却不能完成编程任务。下一步加入读写文件、搜索和执行命令时，我们会实际看到：schema 重复、工具分支增长、路径越界、超时、非零退出和输出过长等问题。V02 的设计将由这些已经出现的问题推动，而不是为未来 MCP 或插件提前设计。
