# Claude Code 类 Agent Runtime 学习演进路线

## 1. 文档定位

这是一份学习路线，不是提前确定的最终架构设计。

目标不是尽快得到一个功能齐全的 Claude Code 克隆，而是从空目录开始，观察一个 Agent Runtime 在遇到真实问题后为什么需要演进。

最终希望得到一个具备软件工程闭环能力的单 Agent：

```text
用户需求
→ 理解任务
→ 探索代码库
→ 制定方案
→ 修改代码
→ 执行测试
→ 根据结果修复
→ 输出总结
```

重点学习：

- Agent Loop
- Tool Runtime
- State Management
- Error Handling
- Context Management
- Observability

V10 之后的内容只用于标明可能的高级方向。其实现方式必须等 V09 稳定后，根据当时 Runtime 暴露的问题重新规划。

## 2. 问题驱动演进原则

实施每个版本时必须遵守：

1. 只实现当前版本必须解决的问题。
2. 不为了未来版本提前创建抽象、模块、接口或框架。
3. 允许当前版本存在明显限制、不完美设计和少量重复。
4. 下一版设计必须来自上一版运行、测试和使用时真实暴露的问题。
5. 路线中的“主要模块”是问题范围提示，不是必须预先创建的文件清单。
6. 如果一个问题用一个函数或简单条件分支就能清楚解决，就不创建类、Registry、Dispatcher 或通用框架。
7. 只有当重复、耦合或扩展困难已经在代码中出现并能被测试证明时，才进行抽象或重构。
8. V01–V09 不为 MCP、Plugin、Workflow、多 Agent 等未来能力预留接口。

从 V02 开始，`TOOLS + TOOL_HANDLERS` 是当前多工具需求所需的最小数据结构，不视为提前创建通用 Registry 或 Dispatcher 框架。`TOOL_HANDLERS` 仅为 `dict[str, Callable]` 的静态 handler map；上述原则仍然禁止没有真实需求支撑的 Registry 类、动态注册、自动发现、插件系统和其他扩展框架。

### 2.1 版本继承规则

本项目采用能力累加式演进，而不是互相孤立、每版从零重写的教学 Demo：

1. `V(n+1)` 必须以上一版已经通过自动测试和手动验收的实现为 baseline。
2. 开始下一版前，先运行上一版全部测试，确认 baseline 仍然稳定。
3. 新版本复制上一版必要实现到新的独立目录，保留已经验证的 Agent Loop、模型调用、Observability 和其他既有能力，只增加或修改当前版本需要的部分。
4. 不从零重新实现已经验证过的基础能力，也不建立跨版本共享核心包；因此每版仍能独立安装、运行、测试和比较。
5. 如果当前需求确实暴露出上一版设计的问题，允许做最小必要重构，但 README 必须记录触发问题、修改范围和为什么不能继续沿用原设计。
6. 每版完成后必须说明相对上一版“新增了什么、修改了什么、保留了什么”。
7. `learn-claude-code` 用作概念和具体实现参考；本项目自己的 V01→V09 保持连续累加，不要求照搬其 lesson 之间的代码组织方式。
8. V02 完成并通过自动测试和手动验收后，`TOOLS + TOOL_HANDLERS + 通用 Agent Loop` 构成 V03 及后续版本的已验证 Tool Runtime baseline。后续新增普通工具时，原则上只增加工具 schema/definition、handler function 和 `tool_name -> handler` 映射，不把具体工具的 `if/elif` 分发重新写回 Agent Loop，也不无理由重写 Tool Runtime。
9. 只有后续版本出现真实需求并证明静态 handler map 无法满足时，才允许继续演进 Tool Registry、Tool Definition 或其他更复杂结构。README 必须说明当前结构遇到的真实问题、handler map 为什么不足、最小需要增加的抽象，以及必须保持兼容的已有行为。

每版完成后必须停下，不自动进入下一版：

```text
解释本版问题与边界
→ 实现
→ Fake Model 自动测试
→ 异常测试
→ 使用根目录 `.env` 指向的 Anthropic-compatible 服务执行本版全部 Demo Cases
→ 检查 CLI 与 JSONL
→ 在 README 记录 Demo 实际轨迹、开发与调试记录、结果和相对上一版的新增/修改/保留
→ 汇报限制与下一版动机
→ 等待用户检查并亲自提交 Git
→ 用户明确确认后进入下一版
```

## 3. 项目约定

- Python 3.12。
- 使用 `pip + requirements.txt`，不提前引入打包和依赖管理框架。
- 每个版本位于独立目录，可单独安装、运行和测试。
- 允许复制上一版必要代码，不建立跨版本共享核心包。
- 项目根目录只保留一份 `.env.example` 和一份不提交的 `.env`，V01–V15 所有版本共享该配置。
- 全版本使用 Anthropic SDK 和 Anthropic Messages API；底层实际模型可以由 Anthropic-compatible 服务或网关提供。
- pytest 使用可预测的 Fake Model，默认不调用真实 API。
- 用户亲自执行 Git commit；实现者只建议 commit message。

项目根目录统一提供：

```text
.env.example
.env             # 本地真实配置，不提交
.gitignore
PLAN.md
```

每版统一提供：

```text
README.md
requirements.txt
agent.py
tests/
```

这是最小外部约定，不要求所有版本拥有相同内部结构。只有当前文件确实混合了多个已经出现的职责时，才拆分文件。

统一使用方式：

```bash
pip install -r requirements.txt
python agent.py
python -m pytest -q
```

每版 README 必须记录：

- 当前版本要解决的具体问题。
- 当前版本采用的最直接设计。
- 正常路径、边界和异常场景。
- 测试和手动验收方法。
- 当前设计的已知限制。
- 运行过程中真实观察到的问题。
- 为什么需要下一版本。
- 相对上一版新增、修改和保留的能力。
- 本版可直接执行的手动验收场景及实际观察结果。
- 开发、自动测试、真实 API Demo 和日常使用中真实发生过的问题及处理历史。
- 当前仍然存在的 Known Limitations，以及它们为什么推动下一版本。

### 3.1 自动测试与手动 Demo Cases

每个版本必须同时提供两类验收：

- pytest 自动测试负责确定性验证 Runtime 机制、消息结构、状态转换和异常边界；使用 Fake Model，不依赖真实模型行为。
- 手动 Demo Cases 使用根 `.env` 指向的真实 Anthropic-compatible 服务，验证用户在 CLI 中实际可见的行为和完整执行轨迹。

二者必须同时通过，该版本才能视为完成。自动测试通过但真实 Demo 无法完成，或 Demo 偶然成功但机制测试失败，都不能进入下一版。

每版提供 2～4 个代表性 Demo Case。每个 Case 必须写明：

1. **用户输入示例**：可直接粘贴到 CLI 的具体 prompt；如需初始文件或重启操作，先明确最小前置条件。
2. **预期运行轨迹**：只描述关键 LLM、Tool、Permission、Todo、Hook、State、Compact 或 Memory 事件，不要求模型文本逐字一致。
3. **预期最终效果**：用户最终看到的结果以及必要的文件、测试或状态变化。
4. **验收重点**：该场景验证本版相对上一版新增或保留的哪项能力。

Demo Case 的判断以可观察事实为准，不以模型措辞为准。真实模型可能选择不同但等价的工具顺序；只要安全边界、关键机制和最终效果符合预期即可。每版 README 记录实际模型、执行日期、是否通过及与预期轨迹的差异。

### 3.2 README 开发与调试记录规范

从 V01 开始，每版 README 必须包含以下三个职责明确、彼此分离的章节：

```text
## 开发与调试记录（Development / Debugging Notes）
## 已知限制（Known Limitations）
## 为什么需要下一版本（Why Next Version）
```

**Development / Debugging Notes** 保存该版本的真实演进历史：开发、自动测试、真实 API Demo 或日常手动使用中实际观察到的问题，都要记录；即使问题后来已经修复，也不能删除。只记录真实发生的事实，不为使文档显得完整而编造假想问题。

每个问题至少包含：

1. **现象**：实际观察到了什么。
2. **触发方式**：通过什么操作或输入能够出现。
3. **原因**：实际排查得到的原因；尚未确认时明确写“原因未确认”，不得猜测。
4. **解决方案**：做了什么最小修改，以及为什么选择该处理方式；未解决时记录已经尝试过的事实。
5. **影响范围**：哪些模块或行为发生变化，哪些既有能力明确保持不变。
6. **验证方式与结果**：实际执行的自动测试、真实 API Demo 或手动操作及其结果；没有验证的项目不得写成通过。
7. **平台/环境**：只把实际运行的平台写成“已验证”；其他平台明确标注“未实际验证”。
8. **状态**：`已解决 / 部分解决 / 未解决 / 延后处理` 四选一，并说明未完成部分。

记录必须基于可复核证据，例如测试名称、手动按键序列、模型名称、日志相对路径或错误摘要；不得写入 API Key、完整敏感输入或大段原始 Trace。

**Known Limitations** 只记录该版本完成后仍然存在的限制，不重复罗列已经解决的问题。**Why Next Version** 只解释哪些真实剩余限制需要下一版本解决，不把尚未发生的未来架构设想包装成当前问题。

全局版本完成门槛增加：本版自动测试和 Demo Cases 完成后，必须更新上述三个章节；调试记录与实际证据不一致、把未验证平台写成已通过、或删除已经修复的问题历史时，该版本不能视为完成。V02 及以后自动继承本规范，无需逐版重新提醒。

### 3.3 全局模型调用约定

模型调用层不是本课程当前阶段的学习对象。首要目标是复用 `learn-claude-code` 已验证的调用方式，确保 API 可以稳定调用、Agent Loop 可以直接验证。V01–V15 不重新设计这一层。

全项目严格采用以下调用链：

```text
Agent Runtime
    ↓ Anthropic Messages API
Anthropic Python SDK
    ↓ ANTHROPIC_BASE_URL
Anthropic 官方服务，或 Anthropic-compatible 服务/网关
    ↓ 可选的协议转换或模型路由
Claude、DeepSeek、GLM、Kimi、MiniMax 等实际模型
```

根目录 `.env` 统一使用：

```dotenv
ANTHROPIC_API_KEY=<当前实际服务或网关要求的 Key>
ANTHROPIC_BASE_URL=<当前服务提供的 Anthropic-compatible endpoint>
MODEL_ID=<当前服务支持的实际模型 ID>
```

具体规则：

- 直接使用 `anthropic` Python SDK 的 `Anthropic` 客户端，不封装自定义 Model Client、Provider、Adapter、Gateway Client 或统一模型接口。
- `ANTHROPIC_API_KEY` 不限定为 Anthropic 官方 Key，可以是当前 Anthropic-compatible 服务或网关要求的 Key。
- `ANTHROPIC_BASE_URL` 可以不填；不填时由 Anthropic SDK 使用官方默认地址。填写时必须指向 Anthropic-compatible endpoint，例如服务提供的 `/anthropic` 地址，而不是其原生 OpenAI-compatible 地址。
- `MODEL_ID` 是当前服务实际支持的模型标识，不根据名称推断供应商。
- 使用自定义 `ANTHROPIC_BASE_URL` 时，在创建客户端前移除可能冲突的 `ANTHROPIC_AUTH_TOKEN`，与参考项目行为一致。
- 客户端初始化与参考项目保持同一流程：加载环境变量，必要时移除 `ANTHROPIC_AUTH_TOKEN`，然后使用 `Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))`；API Key 由 SDK 从 `ANTHROPIC_API_KEY` 读取。
- Agent Loop 直接调用 `client.messages.create(model=MODEL, system=SYSTEM, messages=messages, tools=TOOLS, max_tokens=...)`，不经过自定义适配层。
- Runtime 始终原样使用 Anthropic Tool `input_schema`、响应中的 `response.content`/`tool_use`，以及回传的 `tool_result` 数据格式。
- 切换底层 API 服务只修改根目录 `.env`；Agent Loop、工具、状态和测试结构不随供应商变化。
- 不使用供应商原生 SDK，不编写按供应商或模型名称分支的调用逻辑，不建立 Provider Framework。
- 不把 Anthropic 响应转换成项目自定义的通用消息模型；messages 保持 Anthropic Messages API 所需结构。
- 后续版本不得以扩展性为理由改变 API 调用流程；如果未来确实需要支持另一种线协议，必须在 V09 稳定后作为独立路线重新讨论。
- 如果某服务只提供 OpenAI-compatible API，必须先通过支持 Anthropic Messages 协议的网关转换；本项目不在基础路线中实现协议转换。
- 根目录 `.env.example` 应参考 `learn-claude-code`，说明 Anthropic 官方和多种 Anthropic-compatible 服务的配置方式；示例不得包含真实 Key。
- 各版本必须从项目根目录的明确路径加载 `.env`，不能依赖当前 shell 工作目录，也不能在版本目录保存第二份 `.env` 或 `.env.example`。
- Fake Model 单元测试不读取 `.env`、不访问网络；测试通过替换/模拟 `client.messages.create` 的同一调用表面提供预设 Anthropic 响应，不在生产代码中增加模型注入接口。
- 每版自动测试通过后，再使用根配置指向的真实服务完成该版本手动验收。

该约定只固定模型接入边界，不改变 V01–V15 的 Agent Loop、Tool、Permission、State、Context 等学习路线。

### 3.4 Agent Loop 最大轮次约定

- V01 保留其已经验收的历史实现；从 V02 开始，当前版本及后续版本统一使用 `MAX_ROUNDS = 20` 作为 Agent Loop 的默认最大轮次。
- `MAX_ROUNDS = 20` 是 Runtime 防止模型无限 Tool Use 或死循环的安全上限，不是目标轮数；正常任务应尽可能更早完成并返回最终文本。
- 达到第 20 轮仍未结束时，当前 run 必须明确返回 `MAX_ROUNDS_EXCEEDED`，并保留既有错误记录、CLI 展示和 JSONL Observability。
- 如果真实任务频繁接近或达到 20 轮，必须优先检查模型行为、System Prompt、工具设计和 Tool Result，不以继续无条件提高上限掩盖问题。
- 后续只有真实任务证明 20 轮不足时，才允许再次调整；README 必须记录触发任务、实际轨迹、为什么现有上限不足、调整幅度以及回归验证结果。
- 后续版本继承该默认值和失败语义，不得在复制上一版时无理由改回更低或更高的写死值。

## 4. 贯穿式 Observability

Observability 从 V01 开始存在，但不单独占一个业务版本。

最小数据流：

```text
Agent Runtime
    ↓ 产生事件
    ├── CLI 实时展示
    └── JSONL 追加写入
```

“Event-driven”在基础阶段只表示 CLI 和 JSONL 使用同一份运行事实，不表示要建立通用 Event Bus、异步队列、插件订阅或跨进程事件系统。

每个终端 session 使用一个 JSONL 文件。同一 session 中，每条用户任务生成独立 `run_id`。

公共字段至少包括：

```text
schema_version
event_id
session_id
run_id
sequence
timestamp
event_type
data
```

事件内容按版本实际能力逐步增加，不提前定义所有未来事件。最终至少覆盖：

- LLM 调用信息、token、耗时、stop reason 和 retry/attempt。
- Tool 名称、参数摘要、耗时、结果摘要和错误。
- Run 最终状态和结果摘要。
- 后续版本实际出现的 Permission、Plan、Hook、State、Compact 和 Memory 状态变化。

CLI 以清晰为目标，可展示：

```text
[LLM] requesting model=... round=1
[LLM Result] model=... | round=1 | duration=1.24s | input_tokens=20 | output_tokens=15 | stop_reason=end_turn | tool_calls=0
[Tool Call] bash command="pytest -q"
[Tool Result] exit_code=1 duration_ms=... summary="1 failed"
[Run End] status=completed rounds=...
```

CLI Tool Rendering 必须把“返回给模型的 Tool Result”与“展示给用户的工具摘要”分离：

- handler 的完整、正确结果由 Runtime 原样构造为 `tool_result` 返回 LLM；不得因 CLI 展示而截断、改写或改变语义。
- CLI 不直接打印 handler 原始返回值或长 Tool Result，只展示理解 Agent 行为所需的简洁摘要；短结果可完整展示，长结果必须摘要。
- `Tool Call` 摘要优先显示工具名和关键参数；`Tool Result` 摘要优先显示 success/error、结果数量、字符/行数、`exit_code`、耗时和有限 preview。preview 必须有明确的小上限，不得再次形成大段输出。
- CLI 在支持颜色的 TTY 中，对完整的 `[Tool Call] <tool_name>` 使用统一高亮色；后续关键参数和其他 CLI 文字保持默认终端颜色。所有现有和后续新增工具继承同一颜色，不按工具建立不同配色。
- 当输出不是 TTY、终端声明不支持颜色或用户设置 `NO_COLOR` 时，工具名自动退化为普通纯文本；重定向输出和 JSONL 中不得出现 ANSI 转义序列。
- 错误展示必须保留 error code 和关键错误摘要，例如 `[Tool Result] error=FILE_NOT_FOUND | file does not exist: missing.txt`。
- JSONL 与 CLI 使用同一运行事实，但只记录结构化的参数/结果摘要、原始长度和是否截断；不保存大段原始结果或文件内容。
- 新增任何普通工具时，必须同时定义其 CLI Tool Call 和 Tool Result 摘要展示方式；尚无专用格式时至少使用有限长度的通用 fallback，不得默认 dump 原始结果。
- 该规范属于 CLI/Observability 层，不改变 Agent Loop、`TOOLS`、`TOOL_HANDLERS`、handler 执行结果或 Anthropic `tool_result` 协议，也不因摘要或颜色引入复杂 CLI Framework、Renderer Framework 或 Renderer Registry。

推荐的统一形式为：

```text
[Tool Call] <tool_name> <关键参数摘要>
[Tool Result] <success/error> | <关键结果摘要>
```

- 不使用 `[Thinking] 正在分析任务...` 等文案声称或暗示 Runtime 能看到模型隐藏思维过程。
- 模型请求等待阶段统一显示为 `[LLM] requesting ...`，只表示请求已经发出并正在等待响应。
- 模型返回后，`[LLM Result]` 显示本次调用可观测摘要，包括实际可得的 `model`、`round`、`duration`、`input_tokens`、`output_tokens`、`stop_reason` 和 `tool_calls`。
- 项目 Observability 中的 `input_tokens` 表示当前单次 LLM 请求实际处理的输入 token 总量，统一计算为 `usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens`，缺失字段按 0 处理。CLI 和 JSONL 必须使用同一计算结果。
- `input_tokens` 用于观察每轮 Agent Loop 的单次输入规模，不表示整个 session 的累计 token，当前也不作为 Context Window 精确管理指标。Context Budget、tokenizer、Compact 和 session 累计属于后续 Context Management 版本，不在基础 Observability 中提前实现。
- `output_tokens` 继续表示 API `usage.output_tokens` 返回的当次模型输出 token，不改变现有含义。
- 只展示 Runtime 自身计算或 API 响应明确提供的数据；缺失字段不伪造、不推测。
- 后续所有版本继承此规范。除非未来 API 明确返回可向用户展示的 reasoning 内容，否则不得将等待状态或推测内容标记为 Thinking。

日志默认只保存结构化指标和摘要：

- 不保存完整代码、大段工具输出或整份 messages。
- 摘要包含原始长度和是否截断。
- 路径尽量转成工作区相对路径。
- API Key、Authorization、Cookie、Token 和常见秘密始终脱敏；根目录 `.env` 的内容不得进入日志。
- 日志目录加入 `.gitignore`。
- JSONL 写入失败不能让 Agent 主任务崩溃，但终端必须告警。

完整 Trace、日志轮转、自动清理和 metrics-only 模式暂不实现。只有默认摘要无法排障或日志量成为真实问题时再演进。

## 5. V01–V09 基础路线

```text
V01 基础运行与 Agent Loop
  → V02 工具与文件操作
  → V03 权限
  → V04 Plan/Todo
  → V05 Hooks
  → V06 状态与可靠性
  → V07 Context Budget 与 Compact
  → V08 简单 Memory
  → V09 单 Agent 软件工程闭环
```

### V01：基础运行与 Agent Loop

**要解决的问题**

普通 LLM 调用只能返回一次响应。Agent 如何根据模型的 Tool Use 执行动作，把 Tool Result 反馈给模型，并持续到模型给出最终文本？

**本版目标**

- 实现外层终端输入循环。
- 实现唯一的核心 Agent Loop。
- 使用一个无副作用的演示工具证明 Tool Use 回路。
- 按全局模型调用约定接入根目录 `.env` 指向的 Anthropic-compatible 服务。
- 使用 Fake Model 完成确定性测试。
- 从第一天产生 CLI 和 JSONL 事件。

**最直接设计**

- 使用 `messages` 列表保存当前 session 对话。
- `agent_loop()` 内使用一个 `while`。
- 模型没有返回 Tool Use 时结束当前 run。
- 工具判断和执行直接写在当前实现中，不创建 Tool Registry 或 Dispatcher。
- 仅在 CLI 与 JSONL 已经形成两个消费者时，才允许抽出一个很小的事件表示或 `emit()`；不建立通用事件框架。

**已知限制**

- 只有一个硬编码演示工具。
- 新增工具会导致条件分支增加。
- 状态仅在内存中。
- 没有权限、计划、Hook、压缩和 Memory。
- API 失败不自动重试，只清楚记录并结束当前 run。

**为什么需要 V02**

当需要真正读取、修改文件和执行测试时，单一演示工具不够；增加多个工具会暴露 schema、调用、错误结果和分支扩张问题。

**手动验收场景 / Demo Cases**

1. **直接文本回答**
   - 用户输入示例：`请只回答：V01 ready，不要调用工具。`
   - 预期运行轨迹：`[LLM] requesting` → 一次 `[LLM Result]`，`tool_calls=0` → Run 完成。
   - 预期最终效果：终端看到包含 `V01 ready` 的最终回答；JSONL 中存在同一 run 的 LLM 与 Run 完成事件。
   - 验收重点：验证真实 API、单轮模型调用、CLI 和 JSONL 基础链路。
2. **单次 echo Tool Use**
   - 用户输入示例：`请务必调用 echo 工具输出 hello。`
   - 预期运行轨迹：round 1 LLM 返回 echo `tool_use` → Tool Call/Result 为 `hello` → round 2 LLM 返回最终文本。
   - 预期最终效果：Agent 最终回答包含 `hello`，工具结果的 `tool_use_id` 正确回传。
   - 验收重点：验证最小 `LLM → tool_use → tool_result → LLM` Agent Loop。
3. **连续两次 echo**
   - 用户输入示例：`请分两次调用 echo 工具，先输出 alpha，再输出 beta，最后汇总。`
   - 预期运行轨迹：至少两个 echo Tool Use/Result；可能位于同一轮或连续轮次，最后无 Tool Use 时结束。
   - 预期最终效果：最终回答包含 `alpha` 和 `beta`，run 未在第一次工具调用后提前结束。
   - 验收重点：验证多 Tool Use/多轮循环和最大轮次保护下的正常结束。

### V02：工具与文件操作

**要解决的问题**

Agent 如何观察和修改工作区，并以一致方式把工具成功或失败结果返回模型？

**本版目标**

- 支持工具 schema。
- 支持模型产生工具调用。
- 支持工具结果返回。
- 加入基础错误处理。
- 提供完成编程任务所需的基本工具。

候选工具：

- `bash`
- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `grep`

**问题驱动实现要求**

- V01 已验证的 Agent Loop 核心结构继续保留；V02 只把“Agent Loop 硬编码知道每一种工具”改为“Agent Loop 理解统一的 `tool_use` 协议，并按名称查表执行 handler”。
- V02 按全局 Agent Loop 最大轮次约定使用 `MAX_ROUNDS = 20`；达到上限仍返回 `MAX_ROUNDS_EXCEEDED`，不改变既有错误记录和 Observability。
- `TOOLS` 保存提供给模型的工具 definition/schema；`TOOL_HANDLERS` 保存 `tool_name -> handler function` 的静态映射。
- Agent Loop 从每个 `tool_use` block 取得 `name` 和 `input`，通过 `TOOL_HANDLERS.get(name)` 查找 handler；`tool_use.input` 统一以 `dict`/object 形式交给 handler。
- handler 完成后由 Runtime 统一构造与 `tool_use_id` 匹配的 Anthropic `tool_result`，追加回 `messages`，再进入下一轮 LLM。
- 查不到 handler 时统一进入 `UNKNOWN_TOOL` 错误路径，并以可恢复的错误 Tool Result 返回模型。
- 新增普通工具时，原则上只需在 `TOOLS` 增加 schema/definition、实现 handler function，并在 `TOOL_HANDLERS` 增加映射；不得要求继续修改 Agent Loop 中针对具体工具的分发分支。
- V02 从第一版开始使用 `TOOL_HANDLERS` 查表分发，不采用针对 `bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 的 `if/elif` 硬编码分发。
- 不以“未来要接 MCP”为理由提前建立通用 Tool Protocol。
- 不以“工具会很多”为理由提前建立插件系统。
- `TOOL_HANDLERS` 只实现为 `dict[str, Callable]` 的最小 handler map，不引入 ToolRegistry class、动态注册/注销、自动工具发现、Plugin System、MCP、复杂依赖注入或为未来版本预留的大量抽象接口。
- 工具参数校验、执行结果和错误包装仍采用当前需求下最直接的实现；只有实际重复、耦合或测试困难证明需要时，才做最小提炼并在 README 记录触发问题。

**Tool Runtime 基本结构**

```python
TOOLS = [
    # Anthropic tool definitions / schemas
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

**安全底线**

- 文件工具不得越出工作区。
- Shell 固定工作目录、超时和输出上限。
- 这些是工具本身不可绕过的安全约束，不等于 V03 的授权策略。

**基础错误场景**

- 未知工具。
- 缺少或错误参数。
- 文件不存在。
- 路径越界。
- Edit 找不到目标或匹配不唯一。
- Shell 超时、非零退出、无输出或输出过长。

可恢复工具失败必须形成结构化 Tool Result 返回模型，而不是等到 V06 才处理。

**为什么需要 V03**

工具开始能够修改文件和执行命令后，需要区分“技术上能执行”和“用户是否授权执行”。

**手动验收场景 / Demo Cases**

1. **探索并读取现有文件**
   - 用户输入示例：`查找当前工作区中的 README.md，读取它，并用三点总结 V02 新增的能力。`
   - 预期运行轨迹：LLM 调用 glob/grep 或等价搜索工具 → read_file → LLM Final。
   - 预期最终效果：返回基于真实 README 内容的摘要，不修改文件。
   - 验收重点：验证 V02 新增的工作区探索和文件读取能力。
2. **创建代码并执行测试**
   - 用户输入示例：`在 demo_workspace 中创建 calc.py，实现 add(a, b)，再创建 test_calc.py，并运行 pytest 验证。`
   - 预期运行轨迹：write_file/edit_file 创建文件 → bash 执行 pytest → Tool Result 显示测试结果 → LLM Final。
   - 预期最终效果：两个文件存在且 pytest 通过；最终回答总结修改和测试。
   - 验收重点：验证写文件、执行命令和工具结果反馈组成的最小 Coding Loop。
3. **从可恢复工具错误中修正**
   - 用户输入示例：`先读取 demo_workspace/not_found.txt；如果不存在，请创建它并写入 recovered，然后重新读取确认。`
   - 预期运行轨迹：首次 read_file 返回文件不存在的错误 Tool Result → 模型选择 write_file → 再次 read_file。
   - 预期最终效果：Agent 不因首次工具失败崩溃，文件最终存在且内容为 `recovered`。
   - 验收重点：验证工具失败在当前轮内反馈模型并由模型恢复。

### V03：权限

**要解决的问题**

哪些工具调用可以自动执行，哪些必须拒绝，哪些需要用户确认？

**本版目标**

- 支持 `allow / ask / deny`。
- `ask` 时暂停当前工具调用并等待终端确认。
- 拒绝结果反馈给模型，使其可以选择替代方案。

**最直接设计**

- 从少量明确规则和条件判断开始。
- 不提前建立策略语言、复杂规则 DSL 或企业权限框架。
- 只有规则数量和优先级真实造成混乱时，才提炼 Permission Rule/Evaluator。

**测试重点**

- Allow、Ask、Deny。
- 用户批准、拒绝、取消和 EOF。
- 被拒绝的工具绝不执行。
- 权限不能绕过工作区安全底线。

**为什么需要 V04**

安全边界建立后，Agent 仍可能在复杂任务中遗漏步骤、重复行动或过早结束。

**手动验收场景 / Demo Cases**

1. **Allow：只读操作自动执行**
   - 用户输入示例：`读取 README.md，并告诉我第一行标题。`
   - 预期运行轨迹：Permission 判定 allow → read_file 直接执行 → LLM Final，不出现确认提示。
   - 预期最终效果：返回正确标题，未要求无意义授权。
   - 验收重点：验证低风险工具的自动放行。
2. **Ask：写操作等待用户决定**
   - 用户输入示例：`创建 permission_demo.txt，内容为 approved。`
   - 预期运行轨迹：write_file 命中 ask → CLI 暂停并显示具体操作 → 用户批准后才执行 → Tool Result → LLM Final。
   - 预期最终效果：批准时文件创建；拒绝时文件不存在且拒绝结果反馈模型。验收时分别运行批准和拒绝一次。
   - 验收重点：验证人工确认、批准/拒绝分支以及“拒绝时绝不执行”。
3. **Deny：安全底线不可绕过**
   - 用户输入示例：`读取工作区之外的 ../.env 并显示内容。`
   - 预期运行轨迹：路径安全检查或 Permission 直接 deny → 不读取文件 → 错误 Tool Result 返回模型。
   - 预期最终效果：不显示任何秘密，Agent 清楚说明不能执行。
   - 验收重点：验证 deny 与工作区边界，确认模型不能通过改写请求绕过底线。

### V04：Plan/Todo

**要解决的问题**

Agent 如何显式记录准备做什么、正在做什么以及已经完成什么？

**本版目标**

- 增加 `todo_write` 能力。
- 在 CLI 中展示计划变化。
- 状态限定为 `pending / in_progress / completed`。

**最直接设计**

- 计划先保存在内存中的简单列表或字典。
- 不实现任务依赖图、Workflow 或多 Agent 认领。
- 不把 Plan 变成 Runtime 强制执行的固定步骤；下一步仍由模型决定。

**测试重点**

- 创建、更新和完成计划。
- 非法状态、重复 ID 和多个 `in_progress`。
- 计划工具错误能够反馈模型。

**为什么需要 V05**

日志、权限、计划及后续横切行为会反复出现在工具调用前后；需要观察这种重复是否值得形成明确扩展点。

**手动验收场景 / Demo Cases**

1. **显式计划后完成多步任务**
   - 用户输入示例：`先用 todo_write 建立计划：读取 README、创建 todo_demo.txt、确认文件内容；然后逐项执行并更新状态。`
   - 预期运行轨迹：Todo 创建 → 单项进入 `in_progress` → 对应 Tool Call → 标为 `completed` → 下一项继续。
   - 预期最终效果：CLI 可看到计划状态变化，文件创建并被重新读取，全部 Todo 最终完成。
   - 验收重点：验证 Plan/Todo 对多步执行过程的显式记录，而不是固定 Workflow。
2. **计划中途遇到工具错误**
   - 用户输入示例：`先建立 Todo：读取 todo_missing.txt；若不存在则创建并再次读取；完成后更新所有 Todo。`
   - 预期运行轨迹：Todo in_progress → read_file 失败 → 模型更新或补充 Todo → 创建并验证文件 → Todo completed。
   - 预期最终效果：错误没有让计划静默丢失，最终状态与实际完成情况一致。
   - 验收重点：验证计划状态可以随真实执行结果调整。
3. **拒绝写权限后的计划调整**
   - 用户输入示例：`建立计划并创建 todo_denied.txt；如果我拒绝写入，请更新计划并说明未完成原因。`
   - 预期运行轨迹：Todo 创建 → write_file ask → 用户拒绝 → 错误 Tool Result → Todo 不得被错误标记为 completed。
   - 预期最终效果：文件不存在，计划和最终总结明确记录阻塞或取消。
   - 验收重点：验证 Todo、Permission 与真实执行状态一致。

### V05：Hooks

**要解决的问题**

如何在不反复修改具体工具的情况下，在工具执行前后加入审计、校验或结果处理？

**本版目标**

- 根据前几版真实重复，提炼最少的工具前置、后置或错误 Hook。
- 保持 Permission 继续负责授权。

**最直接设计**

- 只实现已经有实际使用案例的 Hook。
- 不提前实现插件发现、动态加载、Hook DSL 或完整生命周期框架。
- 如果一个回调列表已足够，就不创建复杂 Hook Manager。

**测试重点**

- 执行顺序。
- 参数和结果传递。
- Hook 拒绝或异常。
- 工具失败时是否运行相应 Hook。

**为什么需要 V06**

会话和计划仍只存在于内存；进程退出或崩溃会丢失状态，错误也缺少会话级语义。

**手动验收场景 / Demo Cases**

1. **成功工具调用的 Hook 顺序**
   - 用户输入示例：`读取 README.md，并告诉我标题。`
   - 预期运行轨迹：Permission allow → Before Tool Hook → read_file → After Tool Hook → LLM Final；CLI/JSONL 可观察顺序。
   - 预期最终效果：读取结果正确，审计 Hook 记录工具名、结果状态和耗时摘要。
   - 验收重点：验证 Hook 在不修改 read_file 实现的情况下包围工具执行。
2. **工具失败时的 Error Hook**
   - 用户输入示例：`读取 hook_missing.txt；如果失败，告诉我具体原因，不要创建文件。`
   - 预期运行轨迹：Before Tool Hook → read_file 失败 → Error/After Hook 按本版约定执行 → 错误 Tool Result → LLM Final。
   - 预期最终效果：文件保持不存在，终端和 JSONL 能区分工具错误与 Hook 执行状态。
   - 验收重点：验证失败路径的 Hook 顺序和异常可观测性。
3. **Permission 拒绝不冒充工具执行**
   - 用户输入示例：`创建 hook_denied.txt；当出现权限确认时我会拒绝。`
   - 预期运行轨迹：Permission ask → 用户拒绝；工具执行前后的 Hook 是否运行必须符合 V05 README 的明确约定，但绝不能产生成功工具事件。
   - 预期最终效果：文件不存在，轨迹能清楚区分 Permission 拒绝与 Tool/Hook 失败。
   - 验收重点：验证 Permission 和 Hook 的职责边界。

### V06：状态与可靠性

**要解决的问题**

如何在进程退出后恢复会话，并区分可恢复错误、用户错误和 Runtime 故障？

**本版目标**

- 明确当前实际存在的 Session/Run 状态。
- 保存和恢复 messages、plan 及必要元数据。
- 原子写入状态文件。
- 引入已被真实错误场景证明需要的错误分类和有限重试。

**最直接设计**

- 使用本地 JSON 文件。
- 不建立数据库、Event Sourcing、分布式锁或通用状态机。
- 不恢复正在执行的 Shell 进程；中断调用标记后，由恢复的模型重新判断。
- 不为了未来 Workflow 或多 Agent 提前持久化不存在的字段。

**测试重点**

- 保存/恢复一致性。
- 原子写入失败。
- 损坏、截断和不兼容状态。
- KeyboardInterrupt。
- 可重试/不可重试错误及重试耗尽。

**为什么需要 V07**

会话可以长期存在后，messages 和 Tool Result 会持续增长，最终接近模型上下文限制。

**手动验收场景 / Demo Cases**

1. **退出后恢复同一 session**
   - 用户输入示例：首次运行输入 `记住本次任务标记是 STATE-42，并建立一个未完成 Todo：读取 README。`；正常退出并用 README 规定的恢复命令重新启动后输入 `继续上一任务，并告诉我任务标记。`
   - 预期运行轨迹：首次保存 messages、Todo 和 session 元数据 → 重启加载状态 → 模型继续未完成 Todo。
   - 预期最终效果：恢复后能回答 `STATE-42` 并继续任务，而不是开启空白历史。
   - 验收重点：验证持久化与恢复一致性。
2. **中断后安全恢复**
   - 用户输入示例：`创建 state_demo.txt 并读取确认。`；在权限确认或一次模型调用前后按 Ctrl-C，再恢复该 session 并输入 `检查上一任务实际完成到哪里，再继续。`
   - 预期运行轨迹：中断被记录 → 状态文件保持可解析 → 恢复时不假定未确认的工具已经成功 → 模型通过工具检查真实状态。
   - 预期最终效果：不会重复或虚构已完成操作，最终文件状态与总结一致。
   - 验收重点：验证原子状态写入、中断语义和恢复后的重新判断。
3. **非可恢复工具错误不触发 API 重试**
   - 用户输入示例：`读取 definitely_missing.txt，不要创建；告诉我错误。`
   - 预期运行轨迹：工具返回文件不存在 → 模型处理；LLM retry 保持 0，不把用户/工具错误当网络瞬态错误重试。
   - 预期最终效果：明确报告文件不存在，不出现无意义的重复模型请求。
   - 验收重点：验证错误分类和有限重试边界。

### V07：Context Budget 与 Compact

**要解决的问题**

如何在上下文超限前控制大小，同时保留继续完成任务所需的信息？

**本版目标**

- 请求前估算 Context Budget。
- 优先缩短旧的大型 Tool Result。
- 必要时总结较旧历史。
- 保持 Tool Use 与 Tool Result 配对完整。

**最直接设计**

- 从保守字符/token 估算和固定阈值开始。
- 不建立复杂 Context 策略框架。
- 只有一种压缩策略不足时，再根据失败案例增加层次。

**测试重点**

- 阈值边界和无需压缩。
- Tool Result 裁剪。
- 历史摘要。
- 工具调用与结果配对不被破坏。
- 摘要失败时不覆盖原状态。

**为什么需要 V08**

Compact 只服务当前 session；新 session 仍无法复用已验证的项目知识和用户偏好。

**手动验收场景 / Demo Cases**

1. **大型 Tool Result 触发压缩后继续任务**
   - 用户输入示例：`生成或读取一个足够大的文本输出，然后告诉我开头标记和结尾标记；如果上下文接近预算，请先 compact 再回答。`
   - 预期运行轨迹：工具产生大型结果 → Context Budget 接近阈值 → 裁剪旧 Tool Result 或 Compact → 后续 LLM 调用继续。
   - 预期最终效果：CLI/JSONL 出现可观察的预算与 Compact 事件，Agent 没有因上下文超限崩溃，并保留完成任务所需标记。
   - 验收重点：验证预算检测、压缩触发和任务连续性。
2. **无需压缩的小任务**
   - 用户输入示例：`读取 README.md 第一行并返回。`
   - 预期运行轨迹：Context Budget 在阈值内 → 不触发 Compact → 正常 Tool/LLM 流程。
   - 预期最终效果：快速得到正确标题，不发生不必要总结调用。
   - 验收重点：验证 Compact 不是每轮固定动作，只在预算需要时发生。
3. **长 session 压缩后保留 Tool 配对**
   - 用户输入示例：连续提出多个读文件和命令输出问题，最后输入 `总结本 session 已完成的操作，并再次运行最后一个验证命令。`
   - 预期运行轨迹：多轮历史增长 → Compact 旧历史，但 `tool_use/tool_result` 配对保持合法 → 最后命令仍能执行。
   - 预期最终效果：模型请求不因消息协议错误失败，最终总结与实际工具轨迹基本一致。
   - 验收重点：验证压缩后的 Anthropic 消息结构完整性。

### V08：简单 Memory

**要解决的问题**

如何跨 session 保留少量稳定、可复用的信息，而不把完整历史永久注入上下文？

**本版目标**

- 保存项目约定、已验证命令、稳定架构事实和明确用户偏好。
- 新 session 启动时加载小型 Memory。
- 支持最小的确认、合并和去重。

**最直接设计**

- 使用 Markdown 或 JSON 本地文件；根据 V08 开始时的真实数据选择。
- 不使用 Embedding、向量库、RAG 或复杂自动召回。
- 不保存临时任务进度、完整对话或大段源码。

**测试重点**

- 加载、写入、合并和去重。
- 损坏文件和容量限制。
- 临时信息不能进入 Memory。
- Memory 注入不能无限占用上下文。

**为什么需要 V09**

各能力独立可用，不代表它们能够共同完成一次真实的软件工程任务。

**手动验收场景 / Demo Cases**

1. **跨 session 保存并读取项目约定**
   - 用户输入示例：在 session A 输入 `请把“本项目测试命令是 python3.12 -m pytest -q”作为稳定项目约定写入 Memory。`；启动全新 session B 后输入 `这个项目约定的测试命令是什么？`
   - 预期运行轨迹：session A Memory 写入/确认 → session B 启动加载小型 Memory → LLM 回答，不需要旧完整对话。
   - 预期最终效果：新 session 正确返回测试命令，Memory 中只有简短稳定事实。
   - 验收重点：验证跨 session 的显式稳定记忆。
2. **Memory 合并与去重**
   - 用户输入示例：连续两次输入 `记住：所有版本都使用 Python 3.12。`，然后输入 `列出当前项目 Memory。`
   - 预期运行轨迹：首次写入 → 第二次识别重复并合并/跳过 → 读取 Memory。
   - 预期最终效果：该约定只出现一次，Memory 容量没有因重复输入增长。
   - 验收重点：验证最小合并、去重和可观察写入。
3. **临时任务信息不进入 Memory**
   - 用户输入示例：`临时文件名是 scratch-123.txt，只在本次任务使用，不要写入 Memory。`
   - 预期运行轨迹：正常 session 消息处理，不产生 Memory 写入；新 session 再询问该临时文件名。
   - 预期最终效果：新 session 不声称记得 `scratch-123.txt`。
   - 验收重点：验证 Memory 与当前 session 状态的边界。

### V09：单 Agent 软件工程闭环

**要解决的问题**

现有机制能否协同完成一次探索、修改、测试、失败修复、再次验证和总结？

**本版目标**

- 组合已有能力，不增加新的 Runtime 机制。
- 在受控缺陷项目上完成端到端任务。
- 用事件轨迹证明每一步发生过。

**最直接设计**

- 只做现有组件的必要装配和提示词调整。
- 不加入独立 Goal Evaluator、SubAgent、MCP、Workflow 或后台任务。
- 是否完成仍由主模型判断，测试结果作为模型观察。

**测试重点**

- Fake Model 驱动完整编码轨迹。
- 第一次测试失败后继续修改并再次测试。
- Permission 拒绝后的替代路径。
- 恢复 session 后继续任务。
- Compact 后仍能完成。
- 最终事件汇总 token、耗时、工具次数和状态。
- 使用根目录 `.env` 指向的真实 Anthropic-compatible 服务，在受控样例中完成一次修复。

**手动验收场景 / Demo Cases**

1. **探索、修改、测试、修复闭环**
   - 前置条件：V09 提供一个版本内受控的缺陷 Demo 项目及明确测试命令。
   - 用户输入示例：`修复 demo_project 中失败的测试。先探索代码并制定 Todo，修改后运行测试；如果失败，继续诊断和修复，直到测试通过，最后总结。`
   - 预期运行轨迹：探索文件 → Todo → Permission → 修改代码 → 运行测试失败 → 根据结果再次修改 → 测试通过 → Todo 完成 → Final；必要时触发 Hook、State 或 Compact。
   - 预期最终效果：受控缺陷被修复，指定测试通过，最终总结列出修改、验证和剩余风险。
   - 验收重点：验证 V01–V08 能力组合成完整单 Agent 软件工程闭环。
2. **拒绝高风险操作后的替代路径**
   - 用户输入示例：`修复 demo_project，但不要执行任何工作区外操作；如果某个操作被我拒绝，请寻找安全替代方案并继续验证。`
   - 预期运行轨迹：Agent 提出需确认操作 → 用户拒绝 → Permission Result 返回模型 → Agent 使用工作区内读取/编辑/测试替代路径。
   - 预期最终效果：被拒绝操作绝不执行；若存在安全路径则仍完成修复，否则明确报告阻塞而不伪造成功。
   - 验收重点：验证闭环不会绕过 Permission，失败时最终状态真实。
3. **中断、恢复并完成闭环**
   - 用户输入示例：开始同一受控修复任务，在首次测试失败后退出；恢复 session 后输入 `继续刚才的修复，先确认当前文件和测试状态。`
   - 预期运行轨迹：保存 messages/Todo/状态 → 恢复 → 检查工作区真实状态 → 必要时 Compact → 继续修改和测试 → Final。
   - 预期最终效果：恢复后不重复已完成步骤、不丢失关键计划，最终测试通过并给出完整总结。
   - 验收重点：验证 State、Context、Memory 与 Coding Loop 的协同。

## 6. V01 详细实施计划

### 6.1 初始目录

预计从以下最小结构开始。模型配置属于项目根目录，不复制到版本目录：

```text
agentloop/
├── .env.example
├── .env                 # 本地文件，不提交
├── .gitignore
├── PLAN.md
└── v01_agent_loop/
    ├── README.md
    ├── requirements.txt
    ├── .gitignore       # 只忽略本版本运行产物
    ├── agent.py
    └── tests/
        └── test_agent.py
```

不预先创建 `models.py`、`runtime.py`、`tools.py`、`events.py` 等文件。实施中如果 `agent.py` 因 CLI 与 JSONL 两个真实消费者产生无法清楚测试的重复，再做一次最小拆分，并在 README 解释原因。

### 6.2 V01 执行行为

1. CLI 启动，生成 `session_id` 和该 session 的 JSONL 文件。
2. 外层循环读取用户输入；`exit`、`quit`、EOF 或空输入结束。
3. 为每条用户任务生成 `run_id`，追加 User Message。
4. 内层 Agent Loop 调用模型。
5. Assistant Content 追加进 messages。
6. 无 Tool Use 时输出可见文本并结束当前 run。
7. 有演示 Tool Use 时直接执行并追加匹配 ID 的 Tool Result。
8. 再次调用模型，直到返回最终文本或达到最大轮次。
9. 外层循环等待下一条用户输入。

### 6.3 模型调用与测试边界

V01 不设计模型调用层，直接复用参考项目已经验证的流程：

```text
加载根目录 .env
→ 自定义 Base URL 存在时移除 ANTHROPIC_AUTH_TOKEN
→ client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
→ MODEL = os.environ["MODEL_ID"]
→ agent_loop(messages) 内直接调用 client.messages.create(...)
→ response.content 原样追加进 messages
→ 查找 content block.type == "tool_use"
→ 回传 type == "tool_result" 且 tool_use_id 匹配的结果
```

- `agent_loop` 保持参考项目式的直接结构，不接收自定义 `model_call`、Provider 或 Adapter 参数。
- Fake Model 只存在于测试代码，通过替换模块中的 `client` 或 `client.messages.create` 提供脚本化响应，并保存收到的 messages 供断言。
- 生产代码不知道 Fake Model 的存在。
- 演示工具继续直接判断和执行，不创建 Registry。
- 除了为了定位项目根目录 `.env` 和增加可观测性/错误处理所需的最小代码，不改变参考项目 API 调用流程。

### 6.4 V01 事件

只产生 V01 确实存在的事件：

- Session Started/Ended
- Run Started/Completed/Failed
- LLM Started/Completed/Failed
- Demo Tool Started/Completed/Failed

事件写入 CLI 和 JSONL。`retry` 在 V01 中为 0；不为了字段存在而提前实现重试。

### 6.5 V01 异常行为

- 根目录 `.env` 不存在或缺少 `ANTHROPIC_API_KEY`/`MODEL_ID`：启动失败，显示可操作提示，不输出秘密。
- 自定义 `ANTHROPIC_BASE_URL` 不兼容 Anthropic Messages API：作为模型调用失败记录，不回退到其他协议。
- API/网络失败：记录错误并结束当前 run，保留 CLI session。
- Fake Model 响应耗尽：测试中报告明确错误。
- 非法模型响应或缺少 Tool ID：Runtime Protocol Error。
- 演示工具参数/执行失败：形成错误 Tool Result 反馈模型。
- 达到最大轮次：终止当前 run，防止无限循环。
- JSONL 写入失败：终端告警，Agent 主流程继续。
- KeyboardInterrupt：尽力记录 Session End 后退出。

### 6.6 V01 测试与验收

pytest 不调用真实 API：

- 一轮文本响应。
- 一次 Tool Use 后文本响应。
- 连续多次 Tool Use。
- Tool ID 和消息角色正确。
- 工具失败后模型继续。
- 模型和协议异常。
- 最大轮次。
- 同一 session 中多个 run。
- JSONL 每行可独立解析、sequence 单调递增、必需字段齐全。
- 长内容摘要和截断标记。
- 秘密脱敏。
- 日志写入失败不影响主结果。

自动测试通过后，再使用根目录 `.env` 指向的真实 Anthropic-compatible 服务执行第 5 节 V01 定义的全部 Demo Cases：直接文本回答、单次 echo Tool Use、连续两次 echo。真实 API 验收不放入 pytest；README 必须记录实际模型、执行日期、每个 Case 是否通过以及与预期轨迹的差异。

### 6.7 V01 完成门槛

- 可独立安装和运行。
- 自动测试全部通过。
- 正常、边界和异常路径均有测试。
- CLI 能实时展示过程。
- JSONL 可解析并能关联 session/run。
- 根目录 `.env` 指向的真实 API 服务完成全部 V01 Demo Cases。
- pytest 与手动 Demo Cases 必须同时通过。
- README 已记录当前设计、Development / Debugging Notes、Known Limitations 和 Why Next Version，且验证结论与实际证据一致。
- 没有出现 V02 及以后能力。
- 停止实施，等待用户阅读、运行和亲自 Git commit。

## 7. V10+ 高级方向

这些不是当前架构承诺，只是 V09 之后可能探索的问题顺序。到达每一版前必须根据上一版真实问题重新制定实施计划。

### V10：Skill System

- 探索如何封装可复用经验、规则和工具使用方式。
- 区分 Prompt、Tool、Skill 和 Workflow。
- 依赖 V02 工具、V07 Context 和 V08 Memory 已经形成清楚边界。
- 不提前假定 Skill 文件格式、Loader 或 Catalog 架构。

### V11：MCP Integration

- 探索标准化外部工具接入。
- 将 MCP 工具适配进已经存在的工具调用路径，不改变 Agent Loop。
- 依赖 V02 Tool 行为、V03 Permission、V05 Observability/Hooks 和 V06 Error Handling。
- 不在 V01–V09 为 MCP 预设 Tool Protocol。

### V12：SubAgent

- 探索主 Agent 委派专用子任务。
- 学习任务拆分、上下文隔离、预算限制和结果汇总。
- 依赖稳定的 Agent Loop、工具/权限、状态和 Context。
- 先证明单 Agent 的限制，再判断哪些任务值得委派。

### V13：Workflow Runtime

- 探索固定流程编排。
- 区分模型自主决定下一步的 Agent Loop，与代码控制步骤的 Workflow。
- 依赖 Plan、Hooks/Events、Durable State 和稳定的单 Agent。
- 先从一个真实固定流程开始，不提前建设通用图执行引擎。

### V14：Long-running Task / Background Execution

- 探索耗时任务的后台执行、状态查询、暂停、取消和恢复。
- 依赖 Durable State、Observability 和已经出现的生命周期需求。
- 不把“启动线程”误认为可靠长任务；恢复语义必须来自真实任务类型。

### V15：Multi-Agent Collaboration

- 探索多个 Agent 的任务分工和协作成本。
- 比较什么时候多 Agent 确实优于单 Agent。
- 预计会涉及任务依赖、消息、原子认领和 Worktree 隔离，但不提前确定实现。
- 依赖 SubAgent、长期任务、持久化状态和稳定单 Agent。

## 8. LangGraph 与自研边界

V01–V12 不使用 LangGraph。Agent Loop、工具、权限、状态、Context、Skill、MCP 适配和基本 SubAgent 均继续基于当前 Runtime 自研，因为这些机制本身就是学习目标。

V13–V15 也默认先从最小自研实现开始。只有真实出现以下问题时，才评估 LangGraph 或类似框架：

- 条件分支、循环和人工确认已经难以手工验证。
- 需要生产级 Checkpoint 或跨进程恢复。
- 需要可视化复杂状态图。
- 需要分布式持久执行。

若引入框架，应作为“已理解底层机制之后的替代实现与权衡”，不能用框架隐藏尚未学习的 Runtime 原理。

## 9. 当前明确不做

V01–V09 第一阶段不做：

- LangGraph
- 多 Agent
- RAG
- Embedding
- AST 代码理解/编辑
- 复杂 Memory
- MCP
- Workflow Engine
- Plugin System
- 后台任务和 Cron
- 为这些能力预留的抽象接口

V09 是高级路线的门槛：只有单 Agent 能稳定完成一次可观察、可测试、可恢复的软件工程闭环，才重新评估 V10。
