# V02：工具与文件操作

V02 只研究一个问题：Agent 如何观察和修改工作区，并以一致方式把工具成功或失败结果返回模型？

V02 以已经验收的 V01 为 baseline。V01 的外层 CLI、核心 Agent Loop、Anthropic Messages API 调用、session messages、run ID、最大轮次保护以及 CLI/JSONL Observability 均继续保留；本版只把无副作用的单一演示工具扩展为完成最小编程任务所需的多工具 Runtime。

## 最直接设计

工具定义和工具执行保持两个明确的数据结构：

```python
TOOLS = [
    # Anthropic tool definitions / input_schema
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read_file,
    "write_file": run_write_file,
    "edit_file": run_edit_file,
    "glob": run_glob,
    "grep": run_grep,
}
```

Agent Loop 遍历模型响应中的 `tool_use` block，校验 `id`、`name` 和对象形式的 `input`，再使用 `TOOL_HANDLERS.get(name)` 查找 handler。handler 统一接收 `dict`，Runtime 统一构造匹配 `tool_use_id` 的 Anthropic `tool_result`。未知工具进入 `UNKNOWN_TOOL` 错误路径。

```text
LLM response
→ tool_use block
→ name + input
→ TOOL_HANDLERS.get(name)
→ handler(input)
→ Runtime 构造 tool_result
→ 追加回 messages
→ 下一轮 LLM
```

`TOOL_HANDLERS` 只是 `dict[str, Callable]` 的静态映射，不是 ToolRegistry class。V02 没有动态注册、自动发现、依赖注入、Plugin 或 MCP。

## 工具

- `read_file(path)`：读取工作区内的 UTF-8 文本文件。
- `write_file(path, content)`：在工作区内创建或覆盖 UTF-8 文本文件，必要时创建父目录。
- `edit_file(path, old_text, new_text)`：只在 `old_text` 精确出现一次时替换。
- `glob(pattern)`：按工作区相对 glob pattern 查找路径。
- `grep(pattern, path=".")`：使用正则表达式搜索一个文件或目录中的 UTF-8 文本文件。
- `bash(command, timeout_seconds=10)`：以版本目录为固定工作目录执行 Shell 命令；超时最多允许 30 秒。

新增普通工具时，原则上只增加 handler function、`TOOLS` 中的 schema 和 `TOOL_HANDLERS` 中的映射，不修改 Agent Loop 中针对具体工具的分支。

## 工作区和安全底线

V02 的工作区固定为 `v02_tool_runtime/`。

- 文件工具只接受相对路径。
- 解析后的真实路径必须仍位于工作区内；绝对路径、`..` 越界以及通过符号链接越界都会拒绝。
- `glob` 拒绝绝对 pattern 和包含 `..` 路径段的 pattern，并过滤越界的符号链接结果。
- `bash` 的 `cwd` 固定为工作区，默认超时 10 秒，最大超时 30 秒，合并后的 stdout/stderr 最多返回 12,000 字符。

这些是工具自身不可绕过的技术约束，不是 V03 的授权策略。V02 不实现 `allow / ask / deny`，写文件和执行 Shell 不会请求用户确认。

## 配置与运行

所有版本共用根目录 `.env`：

```dotenv
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...
MODEL_ID=...
```

运行：

```bash
cd v02_tool_runtime
python3.12 -m pip install -r requirements.txt
python3.12 agent.py
```

输入 `exit`、`quit`、空行或 EOF 结束 session。模型调用继续直接使用 Anthropic Python SDK 和 Messages API，不经过 Provider、Adapter 或自定义消息模型。

## 工具结果和异常

可恢复的工具失败不会终止 Agent Loop，而是以 `is_error=true` 的 Tool Result 返回模型，使模型可以修改参数或选择其他工具。

- `UNKNOWN_TOOL`：工具名没有对应 handler。
- `INVALID_TOOL_INPUT`：缺少参数、参数类型错误或 Shell timeout 越界。
- `FILE_NOT_FOUND`、`NOT_A_FILE`、`FILE_NOT_TEXT`：文件路径或内容不符合要求。
- `PATH_OUTSIDE_WORKSPACE`：文件路径、glob pattern 或 grep path 越界。
- `EDIT_TARGET_NOT_FOUND`、`EDIT_TARGET_NOT_UNIQUE`：edit 目标不存在或并非唯一。
- `INVALID_PATTERN`：grep 正则表达式非法。
- `SHELL_TIMEOUT`、`SHELL_NONZERO_EXIT`、`SHELL_EXECUTION_ERROR`：Shell 超时、非零退出或无法启动。
- `TOOL_EXECUTION_ERROR`：handler 出现未预期异常；Runtime 将其包装为可恢复错误。

Shell 成功但没有输出时返回 `(no output)`。Shell 输出超过上限时截断并附带原始字符数。文件读取和搜索遇到无法解码的二进制文件时不会把二进制内容送入模型。

V01 已有的配置错误、模型调用错误、协议错误、最大轮次、JSONL 写入失败和 Ctrl-C 行为保持不变。V02 不自动重试。V02 按全局约定使用 `MAX_ROUNDS = 20`：它只是防止无限 Tool Use/死循环的安全上限，不是目标轮数；达到上限仍明确返回 `MAX_ROUNDS_EXCEEDED` 并保留 CLI/JSONL 错误记录。

## CLI Tool Observability

返回模型的 Tool Result 与 CLI 展示彼此分离：模型收到 handler 的完整结果，CLI 只显示工具名、关键参数、成功/错误、数量、长度、耗时和有限 preview 等必要摘要；JSONL 只保存结构化摘要、原始长度和截断信息，不保存大段原始结果。没有专用格式的新工具也必须使用有界 fallback。

在支持颜色的 TTY 中，完整的 `[Tool Call] <tool_name>` 使用同一种高亮色，后续参数保持终端默认颜色；非 TTY、`TERM=dumb` 或设置 `NO_COLOR` 时自动输出纯文本，JSONL 不包含 ANSI 转义字符。该展示不改变 Agent Loop、工具执行结果或模型收到的内容。

## Token Observability

CLI `[LLM Result]` 和 JSONL `llm.completed.data.input_tokens` 使用同一定义：当前单次 LLM 请求实际处理的输入 token 总量。

```text
input_tokens =
    usage.input_tokens
    + usage.cache_creation_input_tokens
    + usage.cache_read_input_tokens
```

API 未返回的 cache 字段按 0 处理。该数值用于 Observability，帮助观察每轮 Agent Loop 的输入规模；它不是整个 session 的累计 token，也不是当前阶段的 Context Window 精确管理指标。`output_tokens` 仍保持 API `usage.output_tokens` 的现有含义。Context Budget、tokenizer、Compact 和 session token 汇总留给后续 Context Management 版本。

## 测试

```bash
cd v02_tool_runtime
python3.12 -m pytest -q
```

pytest 使用脚本化 Fake Model，不读取根 `.env`，也不访问网络。测试覆盖：

- V01 Agent Loop、消息顺序、多轮工具、session/run、CLI 和 Observability 回归。
- 单次请求 `input_tokens` 对普通输入、cache creation input 和 cache read input 的累加，以及 CLI/JSONL 一致性。
- Tool Result 对模型保持完整而 CLI/JSONL 使用有界摘要，以及 Tool Call 在 TTY 中高亮、非 TTY/禁用颜色时无 ANSI 的退化行为。
- `TOOLS` 与 `TOOL_HANDLERS` 名称一致，所有 schema 输入均为 object。
- handler map 查表分发，新增测试 handler 时无需修改 Agent Loop。
- 未知工具、handler 未预期异常以及工具错误反馈模型后的恢复。
- 文件读写、父目录创建、路径越界、符号链接越界和非 UTF-8 文件。
- edit 成功、目标不存在和匹配不唯一。
- glob、grep 正常结果、无结果、非法正则及越界。
- Shell 固定 cwd、无输出、非零退出、超时、参数错误和输出截断。

## 手动验收场景 / Demo Cases

### Case 1：探索并读取现有文件

- **用户输入示例**：`查找当前工作区中的 README.md，读取它，并用三点总结 V02 新增的能力。`
- **预期运行轨迹**：LLM 调用 glob/grep 或等价搜索工具 → read_file → LLM Final。
- **预期最终效果**：返回基于本 README 内容的摘要，不修改文件。
- **验收重点**：工作区探索、文件读取、既有 Agent Loop 和 Observability。
- **实际结果（2026-08-31，DeepSeek-V4-Flash）**：通过。原样 prompt 通过非 TTY 标准输入进入 CLI；实际轨迹为 bash 查找 README → read_file → 第三轮 Final，最终严格总结三点。模型选择 bash 而非 glob 查找，但关键机制和最终效果等价。日志：`logs/1a70717c-77ed-41e1-aae5-6992ca8f6ed7.jsonl`。

### Case 2：创建代码并执行测试

- **最小前置条件**：`demo_workspace/` 不存在，或删除前一次验收生成的同名目录后重新启动。
- **用户输入示例**：`在 demo_workspace 中创建 calc.py，实现 add(a, b)，再创建 test_calc.py，并运行 pytest 验证。`
- **预期运行轨迹**：write_file 创建两个文件 → bash 执行 pytest → Tool Result 返回测试结果 → LLM Final。
- **预期最终效果**：两个文件存在且 pytest 通过；最终回答总结修改和测试。
- **验收重点**：写文件、运行命令以及结果反馈组成的最小 Coding Loop。
- **实际结果（2026-08-31，DeepSeek-V4-Flash）**：通过。同一轮调用两次 write_file，第二轮执行 `python3.12 -m pytest`，第三轮 Final；生成的 `calc.py` 和 `test_calc.py` 均存在，Runtime 外再次执行测试得到 `1 passed in 0.00s`。日志：`logs/bb827f7d-8457-4e8c-9db3-6ad2a8350c55.jsonl`。

### Case 3：从可恢复工具错误中修正

- **最小前置条件**：`demo_workspace/not_found.txt` 不存在。
- **用户输入示例**：`先读取 demo_workspace/not_found.txt；如果不存在，请创建它并写入 recovered，然后重新读取确认。`
- **预期运行轨迹**：read_file 返回 `FILE_NOT_FOUND` → 模型调用 write_file → 再次 read_file → LLM Final。
- **预期最终效果**：首次失败不导致 run 崩溃；文件最终存在且内容为 `recovered`。
- **验收重点**：可恢复工具错误在当前 run 内反馈模型并得到修正。
- **实际结果（2026-08-31，DeepSeek-V4-Flash）**：通过。实际轨迹严格为 read_file `FILE_NOT_FOUND` → write_file → read_file 返回 `recovered` → 第四轮 Final；Runtime 外读取文件也确认为 `recovered`。日志：`logs/c9a15f1b-60dd-4614-9f7e-77d1bede2bd2.jsonl`。

## 验收摘要

- 2026-08-31：开始实施前重新运行 V01 全部测试，26 项通过。
- 2026-08-31：V02 首轮 40 项 Fake Model/工具测试通过。
- 2026-08-31：使用根 `.env` 配置的 `DeepSeek-V4-Flash` 完成全部三个真实 API Demo；三个最终验收日志见各 Case。
- 2026-08-31：Demo 产物保留后执行 V02 全量 pytest，共 41 项通过（40 项 Runtime/Fake Model 测试，加上 Demo 2 生成并被 pytest 自动发现的 1 项示例测试）；V01 回归仍为 26 项通过。
- 2026-09-01：最终验收重新执行当前 V02 全量 pytest，53 项通过；同时重新执行 V01 baseline，26 项通过。保留上述各阶段的历史测试数字，不用本次结果覆盖它们。

## 相对 V01：新增、修改和保留

**新增**：六个编程工具、每个工具的 Anthropic schema、静态 `TOOL_HANDLERS`、工作区路径限制、Shell cwd/超时/输出上限，以及 V02 工具正常和异常测试。

**修改**：V01 的单一硬编码 echo 分发被替换为按 `tool_use.name` 查找 handler；SYSTEM prompt 改为说明当前工作区工具能力；工具事件现在覆盖六类真实工具及统一错误码。

**保留**：外层 CLI、核心 Agent Loop、Anthropic SDK/Messages API 调用、原样 messages、Tool Use/Result 配对、多轮循环、session/run、最大轮次、协议错误、模型错误、日志脱敏与截断、CLI/JSONL 以及 readline/libedit 最小绑定。

## 开发与调试记录（Development / Debugging Notes）

### 记录 1：V01 baseline 验证

- **现象**：开始 V02 前需要确认 V01 已验收能力仍然稳定。
- **触发方式**：在 `v01_agent_loop/` 执行 `python3.12 -m pytest -q`。
- **原因**：这是版本继承规则要求的 baseline 检查，不是代码故障。
- **解决方案**：未修改 V01；V02 从其必要文件复制后做增量修改。
- **影响范围**：无生产行为变化；确认 V01 Agent Loop、CLI 和 Observability 可继承。
- **验证方式与结果**：2026-08-31，26 项测试全部通过，用时 0.73 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 2：首轮 V02 自动测试

- **现象**：加入六个工具、handler map 和安全边界后，需要验证 V01 回归与 V02 新路径。
- **触发方式**：在 `v02_tool_runtime/` 执行 `python3.12 -m pytest -q`。
- **原因**：多工具引入了不同参数、文件副作用和 Shell 错误，需要确定性验证。
- **解决方案**：沿用 Fake Model 调用表面，并增加直接工具测试；没有创建模型注入接口、Registry 类或跨版本共享包。
- **影响范围**：V02 新增工具和测试；V01 目录保持不变。
- **验证方式与结果**：2026-08-31，首轮 40 项测试全部通过，用时 0.69 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 3：自动化 TTY 注入中文时 prompt 被 readline/libedit 改写

- **现象**：第一次以伪终端向真实 CLI 自动写入 Case 1 中文 prompt 时，终端出现 bell 字符，JSONL 的 `run.started.data.task.summary` 只剩 ` README.md V02 `；模型没有收到原始任务。另一次伪终端复验也发生同类缩短。
- **触发方式**：以 PTY 启动 `python3.12 agent.py`，再由自动化终端写入完整 Case 1 中文 prompt。
- **原因**：V01 已记录的 readline/libedit 中文行编辑路径在自动化 PTY 注入中仍不可靠；本次通过比较 JSONL 中的 task 摘要与原 prompt 确认输入在模型调用前已经变化。
- **解决方案**：真实 Demo 自动化改用非 TTY 标准输入，把同一条原样中文 prompt 交给 `agent.py`；成功日志中的 task 摘要与原 prompt 完全一致。没有修改 Agent Loop 或引入 CLI Framework。
- **影响范围**：只改变本次 Demo 的输入方式；Tool Runtime、模型调用、messages、CLI/JSONL 和实际用户 prompt 保持不变。
- **验证方式与结果**：失败/偏差轨迹见 `logs/b7111c73-c44f-40a3-b981-0dae4945aed8.jsonl` 和 `logs/bd50818f-87bf-49f6-94dd-a1c0f7ec1e81.jsonl`；非 TTY 原样输入成功轨迹见 `logs/1a70717c-77ed-41e1-aae5-6992ca8f6ed7.jsonl`。
- **平台/环境**：macOS 自动化 PTY 与非 TTY 标准输入已验证；真实物理键盘中文输入仍未复验；Windows 和 Linux 未实际验证。
- **状态**：部分解决。自动化 Demo 已有可靠输入方式，V01 遗留的真实终端中文编辑限制仍存在。

### 记录 4：真实模型无关探索导致最大轮次耗尽

- **现象**：Case 1 首次有效调用和 Case 2 首次运行中，模型在已经拥有足够信息后仍反复使用 bash 检查目录、Git 或父目录。Case 2 到第 8 轮才完成测试工具调用，Runtime 随后以 `MAX_ROUNDS_EXCEEDED` 结束，未产生 Final。
- **触发方式**：使用根 `.env` 的 `DeepSeek-V4-Flash` 执行 Case 1 和 Case 2 原始 prompt。
- **原因**：最初 SYSTEM 只说明“使用工具完成任务”，没有明确要求指定文件任务直接执行、避免无关探索并在验证后立即 Final；真实模型把通用 coding-agent 探索习惯带入了简单 Demo。
- **解决方案**：只收紧 SYSTEM：禁止未经请求检查父目录、Git、环境文件和 Runtime 源码；指定文件创建任务直接使用 write_file；探索优先使用专用工具；验证完成后立即 Final。没有提高最大轮次，也没有改变 Agent Loop 或 handler map。
- **影响范围**：只修改模型可见的执行约束；工具 schema、工具实现、消息结构、最大轮次、Observability 和错误路径保持不变。
- **验证方式与结果**：Case 2 首次失败日志为 `logs/a3555526-8b93-447d-b322-69a2c888e33c.jsonl`；修正后 Case 1 三轮完成、Case 2 三轮完成、Case 3 四轮完成。修正后 V02 40 项核心测试继续通过。
- **平台/环境**：macOS、Python 3.12、真实 `DeepSeek-V4-Flash` 已验证；其他模型和平台未实际验证。
- **状态**：已解决。

### 记录 5：真实 Shell 环境没有裸 pytest 命令

- **现象**：Case 2 首次运行中，模型调用 bash 执行裸 `pytest`，返回 `SHELL_NONZERO_EXIT`，具体退出码为 127；模型随后改用 `python3 -m pytest` 并成功运行，但额外一轮加剧了轮次耗尽。
- **触发方式**：真实 `DeepSeek-V4-Flash` 首次执行 Case 2。
- **原因**：pytest 安装在 Python 环境中，但真实 Shell 的 PATH 没有名为 `pytest` 的可执行文件；项目统一约定本来就是通过 Python 3.12 模块方式运行。
- **解决方案**：SYSTEM 明确要求使用 `python3.12 -m pytest`，与 PLAN 和 README 的 Python 3.12 约定一致；没有修改 PATH、安装额外依赖或在 bash handler 中重写用户命令。
- **影响范围**：只增加模型提示约束；bash 仍原样执行模型提供的 command，其非零退出语义保持不变。
- **验证方式与结果**：首次失败记录在 `logs/a3555526-8b93-447d-b322-69a2c888e33c.jsonl`；修正后的 Case 2 使用 Python 3.12，真实 Demo 显示 `1 passed`，Runtime 外复验为 `1 passed in 0.00s`。
- **平台/环境**：当前 macOS Shell 与 Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 6：长 Tool Result 直接倾倒到终端

- **现象**：手动使用 V02 时，`read_file` 等大输出工具会把大段甚至整份源码直接显示在终端的 Tool Result 位置。
- **触发方式**：让 Agent 读取内容较长的源码文件，观察 CLI 工具执行轨迹。
- **影响**：工具输出淹没轮次、工具名和成败状态，执行轨迹难以阅读，也不利于用户快速判断 Agent 行为。
- **原因**：CLI 的 `tool.completed`/`tool.failed` 展示直接使用 handler 原始结果的摘要字段，Tool Result 与用户展示没有分离。
- **解决方案**：在 CLI/Observability 层分别构造 Tool Call 和 Tool Result 的有限摘要；返回 LLM 的 `tool_result.content` 仍保持 handler 完整结果。`read_file` 显示行数、字节数和有限 preview，写入/编辑显示字符或替换数，搜索只显示有限条目，Shell 显示 exit code、耗时和有限输出；未专门定义的工具使用有界 fallback。JSONL 只保存结构化摘要、原始长度和截断标记。
- **影响范围**：只修改 CLI/Observability rendering 与相关测试；Agent Loop、`TOOLS`、`TOOL_HANDLERS`、handler 结果和发给模型的 Tool Result 语义不变，没有引入 Renderer Framework。
- **验证方式与结果**：新增的自动测试明确比较模型可见的完整文件内容、CLI 有限摘要和 JSONL 结构化摘要，V02 共 45 项测试通过。真实 Demo 读取了 29,450 字节、730 行的 `agent.py`，模型第三轮正确列出六个工具并说明 Tool Result 回传链路；CLI 事件只显示行数、字节数和短 preview，JSONL 仅保存结构化摘要并标记 `truncated=true`。无文件修改工具被调用。验收日志：`logs/b5b6b73f-07b3-4b17-8a5b-add7b24eba3d.jsonl`。
- **平台/环境**：macOS、Python 3.12 和根 `.env` 配置的真实模型已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 7：Anthropic-compatible usage 的 input_tokens 不一定包含 cache 输入

- **现象**：真实 API 多轮日志中的 `response.usage.input_tokens` 会下降，不能直接把该字段解释为本次请求处理的完整输入规模。
- **触发方式**：检查当前 Anthropic-compatible API 的解析对象和原始 usage；连续两次相同请求中，一次为 `input_tokens=643`，另一次为 `input_tokens=131`、`cache_read_input_tokens=512`，两者完整输入规模同为 643。
- **原因**：该服务把普通输入、cache creation input 和 cache read input 分字段返回；`usage.input_tokens` 本身不保证包含 cache 输入。
- **解决方案**：Observability 的 `input_tokens` 统一计算为 `usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens`，缺失字段按 0 处理；CLI 与 JSONL 复用同一结果。没有引入 tokenizer、Context Budget 或 session 累计。
- **影响范围**：只修改 token 指标语义和计算；`output_tokens`、Agent Loop、messages、Tool Runtime 和模型收到的内容保持不变。
- **验证方式与结果**：自动测试分别覆盖仅有普通输入、cache read、cache creation，以及 CLI/JSONL 同值；2026-09-01 当前 V02 全量 53 项测试通过。
- **平台/环境**：当前 Anthropic-compatible 服务、macOS、Python 3.12 已验证；其他服务和平台未实际验证。
- **状态**：已解决。完整 Context Window 管理延后到 Context Management 版本。

## 已知限制（Known Limitations）

- V02 没有 Permission；工具在技术边界允许时会直接执行，尚不能按风险 `allow / ask / deny`。
- Shell 仅固定 cwd、限制 timeout 和输出长度，不是操作系统级沙箱；命令本身仍可能引用工作区外资源。
- session、messages 仍只存在于内存，退出后不能恢复。
- `grep` 是简单的 Python UTF-8 正则逐文件扫描，不支持 `.gitignore`、二进制搜索或 `rg` 的完整语义。
- 文件读取结果没有 Context Budget；大型 Tool Result 的上下文控制属于 V07。
- 搜索最多返回 200 条结果；没有分页。
- 没有 Plan/Todo、Hooks、Compact、Memory、Plugin、MCP 或多 Agent。
- 不自动重试模型调用或 Shell 命令。
- readline/libedit 的真实中文编辑和前向 Delete 仍未完成用户终端复验；Windows、Linux 未实际验证。

## 为什么需要下一版本（Why Next Version）

V02 让 Agent 技术上能够读写文件和执行命令，也实际形成了最小 Coding Loop；但 Runtime 仍无法区分“能够执行”与“用户是否授权执行”。写文件和 Shell 都会立即运行，固定工作区和超时不能表达用户意图或风险等级。因此 V03 只需要在已经验证的 `TOOLS + TOOL_HANDLERS + 通用 Agent Loop` 基线上增加 `allow / ask / deny` 权限判断，而不是重写 Tool Runtime。
