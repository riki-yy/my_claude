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

1. 每个版本只解决当前版本的问题，不提前实现未来版本能力。下一版设计必须来自上一版运行、测试和使用时真实暴露的问题；V01–V09 不为 MCP、Plugin、Workflow、多 Agent 等未来能力预留接口。
2. 当前版本采用“最小但完整”的设计：
   - **最小**：不为未来能力提前创建框架、接口、Registry、DSL、Plugin、通用 Engine 或扩展点。
   - **完整**：当前版本要学习和验证的核心机制必须真实、完整地体现在生产代码、测试和可观察行为中。
3. “最小实现”描述的是需求范围和抽象范围，不是代码质量下限；它不等于代码行数最少、大量硬编码、恒真 matcher、粗粒度工具分类、为了省事跳过参数级/状态级/规则级判断，或用临时捷径替代当前版本本来就需要的架构。
4. 如果当前版本的核心问题本身需要数据驱动分发、参数级规则匹配、状态转换、生命周期边界或统一执行管线，第一版就必须真实实现这些机制。可以限制规则数量、状态数量和覆盖范围，但不能省略机制本身。
5. “不提前设计未来”与“当前版本保持良好架构”是两个独立约束：
   - 不为尚未出现的需求预留复杂抽象。
   - 当前已经明确需要的职责分离、数据结构和调用边界，应当直接正确实现，而不是延后到未来版本补救。
6. 允许当前版本存在 Known Limitations、有限覆盖和少量重复，但这些限制不能削弱本版本的核心学习目标，不能造成当前需求下明显可避免的结构性缺陷，也不能以“以后再优化”为理由保留已知错误边界。
7. 抽象和重构仍由真实需求驱动：不因为未来可能需要就提前抽象；但如果当前版本已经出现多个同类分支、统一规则、多个真实消费者或明确职责边界，则允许采用当前版本所需的最小合理抽象。是否采用，应以当前职责能否被清晰表达、隔离变化并完整测试为判断依据，而不是机械追求代码、文件或层数最少。
8. `learn-claude-code` 继续作为演进思想和具体实现参考：每一版只增加当前需要的能力，但该能力本身应当完整、清晰、可验证；不得为了教学上的“简单”而故意实现成弱化版本。

路线中的“主要模块”只是问题范围提示，不是必须预先创建的文件清单。文件、函数、数据结构或小型抽象是否拆分，继续服从上述“最小但完整”原则。

从 V02 开始，`TOOLS + TOOL_HANDLERS` 是当前多工具问题所需的最小但完整的数据驱动分发结构，不视为提前创建通用 Registry 或 Dispatcher 框架。`TOOL_HANDLERS` 仅为 `dict[str, Callable]` 的静态 handler map；它完整表达当前版本的统一分发机制，同时不引入没有真实需求支撑的 Registry 类、动态注册、自动发现、插件系统和其他未来扩展框架。

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
- 当前版本采用的最小但完整设计，以及它如何完整体现本版核心机制。
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
  → V04 Hooks
  → V05 Plan/Todo
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

**最小但完整设计**

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
- 工具参数校验、执行结果和错误包装采用当前需求下最小但完整的实现：不为未来协议建立通用框架，但当前已经需要的统一校验边界、错误语义和 Tool Result 行为必须完整且可测试。当前职责若已出现重复、耦合或测试困难，则做最小合理提炼并在 README 记录触发问题。

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

- 在 V02 已验证的工具分发与 handler 执行之间插入唯一的 Permission Gate。
- 所有 `tool_use` 都以同样的 `tool_name + tool_input` 进入 Permission Pipeline，并通过“工具范围 + 参数匹配”两层判断得到 `allow / ask / deny` 之一。
- `ask` 时由 CLI 展示统一的权限说明并等待用户确认。
- 规则命中或用户拒绝后不执行工具，而是返回 Permission Denied Tool Result 给模型，使其可以说明失败或选择替代方案。

**与 V02 baseline 的关系**

- V03 从已经验收的 V02 复制必要实现到独立目录，保留通用 Agent Loop、Anthropic `tool_use/tool_result` 消息流程、`TOOLS`、`TOOL_HANDLERS`、handler function 及 handler 执行方式。
- V03 不重新设计 Tool Runtime，不改变工具 schema、handler map 分发或 Runtime 统一构造 `tool_result` 的既有职责。
- 唯一新增的执行阶段位于“解析出 `tool_name + tool_input`”之后、“调用 `TOOL_HANDLERS` 中的 handler”之前：

```text
LLM
→ tool_use(tool_name, tool_input)
→ Permission Pipeline
→ allow / ask / deny
→ TOOL_HANDLERS
→ handler execution
→ tool_result
```

- V02 handler 内已有的工作区路径限制、Shell 固定工作目录、超时和输出上限继续作为不可绕过的工具安全底线；Permission 是新增的授权判断，不取代或削弱这些约束。

**Permission Rule**

- 第一版仅使用两个有序规则集合：`DENY_RULES` 与 `ASK_RULES`。
- 每条规则使用简单 `dict + callable`，包含：
  - `tools`：该规则适用的工具名列表；工具范围判断只能存在于这个字段。
  - `matcher(tool_input)`：只根据工具输入参数判断规则是否命中。
  - `message`（或 `reason`）：说明触发规则的原因。
  - `describe(tool_input)`：生成面向用户的具体操作摘要，供 CLI 统一展示。
- Permission 主流程只负责按固定优先级遍历规则并返回结果，不得出现 `if tool_name == "bash"`、`if tool_name == "write_file"` 等针对具体工具的分支。
- 对每条规则，Pipeline 先以通用表达式判断 `tool_name` 是否属于 `rule["tools"]`；只有属于时才调用 `rule["matcher"](tool_input)`。matcher 返回 `True` 表示命中，命中后的 action 仅由规则所在的 `DENY_RULES` 或 `ASK_RULES` 集合决定。
- 工具判断只存在于 Rule 的 `tools` 字段，参数风险判断只存在于 matcher；matcher 不再接收或判断 `tool_name`。
- 新增工具或权限策略时不修改 Permission Pipeline，只增加相应 Rule。
- 规则的 matcher 或 describe 发生异常时必须安全失败，不得因此绕过 Permission Gate；具体错误语义在实现与测试中采用当前版本所需的最小处理，并记录在 README。

概念结构（不要求为此创建类或通用 Rule Engine）：

```python
DENY_RULES = [
    {
        "tools": ["bash"],
        "matcher": lambda args: contains_destructive_command(
            args.get("command", "")
        ),
        "message": "Potentially destructive command",
        "describe": lambda args: f"Blocked command: {args.get('command')}",
    },
    {
        "tools": ["write_file", "edit_file"],
        "matcher": path_is_outside_workspace,
        "message": "File path is outside the workspace",
        "describe": lambda args: f"Blocked path: {args.get('path')}",
    },
]

ASK_RULES = [
    {
        "tools": ["write_file", "edit_file"],
        "matcher": path_is_sensitive,
        "message": "Sensitive file modification requires approval",
        "describe": lambda args: f"Modify sensitive path: {args.get('path')}",
    },
]
```

文件写入规则必须体现路径参数级判断：解析后位于工作区之外的 write_file/edit_file 调用由 Permission 在执行前 Deny；工作区内 `.env`、credentials、secrets 等明确敏感路径进入 Ask；普通工作区文件不命中规则，默认 Allow。V02 handler 已有的相对路径、真实路径和符号链接越界检查继续保留，并在执行阶段再次强制安全底线；Permission 的提前 Deny 不能取代或删除 handler 校验。

**Permission Pipeline 与固定优先级**

1. 先按顺序检查 `DENY_RULES`。第一条命中后立即返回 `deny`；不执行工具、不询问用户，Runtime 构造 Permission Denied Tool Result 返回模型。
2. 没有 deny 时，按顺序检查 `ASK_RULES`。第一条命中后返回 `ask`，CLI 统一显示操作摘要与原因，并提示 `Allow? (y/N)`：
   - 用户明确允许：继续原有 `TOOL_HANDLERS` 查表与 handler 执行流程。
   - 用户拒绝、取消或输入 EOF：不执行工具，Runtime 构造 Permission Denied Tool Result 返回模型。
3. 没有命中任何规则时默认 `allow`，不显示额外确认，直接进入原有 `TOOL_HANDLERS` 与 handler 执行流程。

同一个调用即使同时满足 Ask 与 Deny，也必须由 Deny 优先拦截，且不能向用户提供批准机会。

**CLI 展示边界**

- matcher 和规则本身不得 `print` 或读取用户输入。
- CLI 只根据 Permission Result 统一展示；`allow` 不增加提示。
- Deny 的统一形态：

```text
[Permission Denied]
<describe>
Reason:
<message>
```

- Ask 的统一形态：

```text
[Permission Required]
<describe>
Reason:
<message>

Allow? (y/N)
```

**本版边界**

- 只实现 `DENY_RULES`、`ASK_RULES`、Rule 的 `tools` 字段、matcher function、`message/reason`、`describe`、固定优先级的 Permission Pipeline 与 CLI confirmation。
- Rule 第一版只使用简单 `dict + callable`；不实现 `PermissionRule` class、Permission DSL、企业 RBAC、用户角色系统、动态策略服务或复杂 Rule Engine。
- 不为未来能力引入 Rule Registry、动态加载、配置热更新或跨版本共享权限包。
- 核心思想与 `learn-claude-code` 一致：所有 Tool 执行前经过统一 Permission Gate；本项目的区别是 Deny 和 Ask 都以 Rule 的 `tools` 范围加 `matcher(tool_input)` 参数判断完成匹配，Permission 主流程不硬编码具体工具。

**测试重点**

- 所有 `tool_use` 都经过同一个 Permission Pipeline，且主流程不含具体工具分支。
- `DENY_RULES` 优先于 `ASK_RULES`；Deny 不询问用户、不调用 handler，并返回 Permission Denied Tool Result。
- Ask 的用户批准、拒绝、取消和 EOF；只有明确批准才调用 handler。
- 无规则命中时默认 Allow，不询问并保持 V02 原有 handler 结果和消息顺序。
- 规则统一先匹配 `tools`，再调用 `matcher(tool_input)`；工具范围和参数风险判断不会混入 Permission 主流程，CLI 由 Permission Result 统一展示而非由规则输出。
- write_file/edit_file 根据 `path` 参数分别覆盖工作区外 Deny、敏感路径 Ask、普通工作区路径默认 Allow；Permission 与 handler 两层路径安全检查均存在。
- 被拒绝的工具绝不执行；权限层不能绕过 V02 handler 的工作区与 Shell 安全底线。
- V02 全部自动测试继续通过，并新增 Permission 的 Fake Model 测试。

**为什么需要 V04**

V03 完成后，Tool Runtime 已经在工具执行前后真实出现 Permission、Tool Call/Tool Result Observability、duration 和 result summary 等横切逻辑。Agent Loop 如果继续直接依赖这些具体实现，每增加一种工具生命周期职责都要再次修改核心循环；因此下一步应先提炼最小 Hook 接入点，而不必等 Todo 出现后再证明动机。

**手动验收场景 / Demo Cases**

人工 Demo 只验证真实模型下的关键端到端行为；细粒度 matcher、边界输入、异常与规则优先级继续由 pytest 确定性覆盖，不因精简 Demo 而减少测试。

1. **正常 Coding 路径：连续 Allow**
   - 用户输入示例：读取 README 第一行，将其写入普通工作区文件，再读取新文件并返回结果。
   - 预期运行轨迹：read_file 默认 Allow → 普通路径 write_file 默认 Allow → read_file 默认 Allow → LLM Final，全程不询问。
   - 预期最终效果：普通工作区文件创建且内容正确，最终回答与文件一致。
   - 验收重点：统一 Gate 不妨碍正常 Coding 流程，并保留多轮 Tool Use/Tool Result 回路。
2. **敏感写入：一次会话分别批准与拒绝**
   - 用户输入示例：依次使用 write_file 写两个隔离的敏感路径；第一个确认时输入 `y`，第二个输入 `n`，拒绝后不得换工具重试。
   - 预期运行轨迹：第一个敏感 path 命中 Ask → 批准后 handler 执行；第二个命中 Ask → 拒绝后 handler 不执行 → Permission Denied Tool Result → LLM Final。
   - 预期最终效果：批准目标存在且内容正确，拒绝目标不存在，Agent 正确总结两项结果。
   - 验收重点：用一个综合场景验证 Ask 的批准、拒绝和“不执行”语义；与 Case 1 对照证明同一 write_file 因 path 不同产生 Allow/Ask。
3. **路径越界：执行前 Deny**
   - 用户输入示例：要求 write_file 写入一个明确位于 Demo 工作区外的隔离目标，拒绝后不得尝试其他工具。
   - 预期运行轨迹：路径 matcher 命中 `DENY_RULES` → 不询问、不调用 handler → Permission Denied Tool Result → LLM Final。
   - 预期最终效果：工作区外目标不存在，Agent 清楚说明操作被拒绝。
   - 验收重点：验证 Permission 的执行前拦截，同时确认 V02 handler 路径校验仍是独立安全底线。
4. **灾难性 Bash：Deny 优先于 Ask**
   - 用户输入示例：使用 README 明确列出的灾难性命令，只允许尝试一次，权限拒绝后立即结束；该命令绝不能绕过 Runtime 单独执行。
   - 预期运行轨迹：命令同时具有副作用且命中灾难性 matcher → `DENY_RULES` 先命中 → 不询问、不调用 bash handler → Permission Denied Tool Result → LLM Final。
   - 预期最终效果：命令绝不执行，Agent 清楚说明不能执行。
   - 验收重点：验证 Deny 优先于 Ask，并且 Deny 没有用户批准入口。

### V04：Hooks

**要解决的问题**

如何让 Agent Loop 只声明 Tool 生命周期已经到达某个位置，而不直接依赖 Permission、Tool Call/Tool Result Observability 等具体横切实现？

**本版目标**

- 参考 `learn-claude-code` s04 的核心思想，用简单 Hook 注册表保存生命周期事件对应的 callback。
- 提供 `register_hook(event, callback)` 注册 callback。
- 提供 `trigger_hooks(event, context)`，由 Agent Loop 在明确生命周期位置统一触发。
- 将 Permission、Tool Call/Tool Result Observability，以及与工具执行直接相关的 duration、result summary 接入对应 Hook。
- Agent Loop 不再直接依赖这些具体横切逻辑；工具 schema、`TOOL_HANDLERS`、handler 和 `tool_result` 回路保持原有职责。

**最小但完整设计**

- Hook 注册表只需表达 `event -> callback list`；`register_hook()` 追加 callback，`trigger_hooks()` 按注册顺序调用当前事件的 callbacks。
- 当前保留两个由 V03 真实职责证明需要的生命周期事件：
  - `PreToolUse`：解析出 `tool_name + tool_input` 后、handler 执行前触发，用于接入 Permission 和 Tool Call Observability。
  - `PostToolUse`：Runtime 得到将返回模型的 Tool Result 后触发，用于接入 Tool Result Observability、duration 和有限 result summary。
- Permission 的 Rule、`allow / ask / deny`、用户确认、Deny 优先级和 Permission Denied Tool Result 完全继承 V03 已验证行为；V04 只把 Permission 改为通过 Tool 执行前的 Hook 接入，不重写 Permission Pipeline 或 Rule。
- handler 成功、工具错误和 Permission 拒绝继续形成既有 Tool Result，并由 Tool 执行前后的 Hook 保持 Observability 闭环；当前没有真实需要时不增加独立 Error Hook 或其他错误生命周期事件。
- callback 异常只定义本版保障 Permission 不被绕过、工具不被重复执行、结果不被误报所需的安全、确定且可测试行为；不为此设计 callback 分类、复杂返回协议、错误生命周期或 Hook 状态机。
- EventLogger、CLI、JSONL 和现有 Observability 数据模型继续承担事件表示、展示与落盘职责。V04 只把 Tool 生命周期相关的调用时机接入 Hook，不重新设计日志系统，也不把 Hook 当作 Event Bus。
- 生命周期事件以 V03 当前代码的真实职责为依据；不机械照搬参考项目的全部事件，也不预先禁止未来由真实问题证明必要的新事件。
- 不引入 HookManager、Middleware、Hook DSL、Plugin System、动态加载或通用 Hook Framework。

**测试重点**

- `register_hook()` 与 `trigger_hooks()` 的注册顺序、参数传递和事件隔离。
- `PreToolUse` 中 Permission 的 Allow、Ask、Deny 和用户确认仍保持 V03 行为，Deny 优先级不变。
- 成功、工具错误、未知工具和 Permission 拒绝都产生语义正确的 Tool Result，并触发一次 `PostToolUse`。
- Tool Call/Tool Result Observability、duration 和 result summary 经 Hook 接入后，CLI/JSONL 的既有数据模型与展示职责不变。
- Hook callback 异常不会绕过 Permission、重复执行 handler 或产生虚假的成功事件。

**为什么需要 V05**

Hook 解耦了已经出现的 Tool 生命周期横切逻辑，但 Agent 在复杂任务中仍可能遗漏步骤、重复行动或过早结束，需要一个通过现有 Tool Runtime 使用的显式 Plan/Todo 能力。

**手动验收场景 / Demo Cases**

1. **成功工具调用的 Hook 顺序**
   - 用户输入示例：`读取 README.md，并告诉我标题。`
   - 预期运行轨迹：`PreToolUse` 中 Permission Allow 与 Tool Call Observability → read_file handler → Runtime 构造 Tool Result → `PostToolUse` 记录结果、耗时和摘要 → LLM Final。
   - 预期最终效果：读取结果正确，CLI/JSONL 保持既有 Tool Call/Result 数据模型，并能观察正确顺序。
   - 验收重点：验证 Agent Loop 只触发 Hook，不直接调用 Permission 或 Tool Observability 的具体实现。
2. **工具失败仍进入统一结果 Hook**
   - 用户输入示例：`读取 hook_missing.txt；如果失败，告诉我具体原因，不要创建文件。`
   - 预期运行轨迹：`PreToolUse` → read_file 返回失败 → Runtime 构造错误 Tool Result → `PostToolUse` 记录错误、耗时和摘要 → LLM Final。
   - 预期最终效果：文件保持不存在，终端和 JSONL 能观察工具错误，且无需独立 Error Hook。
   - 验收重点：验证成功与失败共享 `PostToolUse`，并保留 V02 的可恢复错误语义。
3. **Permission 拒绝仍保持结果闭环**
   - 用户输入示例：`创建 hook_denied.txt；当出现权限确认时我会拒绝。`
   - 预期运行轨迹：`PreToolUse` 中 Permission Ask → 用户拒绝 → handler 不执行 → Runtime 构造 Permission Denied Tool Result → `PostToolUse` 记录拒绝结果 → LLM Final。
   - 预期最终效果：文件不存在，轨迹清楚区分 Permission 拒绝与工具执行失败，不产生虚假成功事件。
   - 验收重点：验证 V03 Permission 语义不变，只改变其接入 Agent Loop 的方式。

### V05：Plan/Todo

**要解决的问题**

Agent 如何显式记录准备做什么、正在做什么以及已经完成什么？

**本版目标**

- 增加普通工具 `todo_write`，像其他工具一样加入 `TOOLS` 和 `TOOL_HANDLERS`。
- `todo_write` 必须通过既有 Tool Runtime 执行：`LLM → tool_use(todo_write) → TOOL_HANDLERS → todo_write handler → tool_result → LLM`。
- `todo_write` 的 input 是新的完整 Todo List：
  ```json
  {
    "todos": [
      {
        "id": "...",
        "content": "...",
        "status": "..."
      }
    ]
  }
  ```
- 每个 Todo item 只包含最小字段 `id / content / status`；状态限定为 `pending / in_progress / completed`。
- Todo State 只由 `todo_write` handler 按工具输入整体更新；更新成功后，CLI 和 Tool Result 使用同一份渲染后的完整 TodoList 文本。

**最小但完整设计**

- `todo_write` 是普通 Tool，不是 Hook；Agent Loop 不为 Todo 增加特殊执行分支，Hook 也不负责更新 Todo State。
- 每次调用 `todo_write` 都表示提交新的完整 Todo State，不设计 `add / update / delete` 等增量 action。handler 必须先校验整个 `todos` 数组；全部通过后才整体替换当前内存状态。
- 更新具有原子性：任意一项校验失败时整次调用失败，旧 Todo State 完全不变，不允许部分新增、修改或删除。
- handler 至少校验：`id` 非空且在列表内唯一；`content` 非空；`status` 只能是三个允许值；同一时刻最多一个 Todo 为 `in_progress`；Todo 总数不超过 20。
- Todo 是否 `completed` 只由模型显式再次调用 `todo_write` 提交新完整状态决定；Runtime 不根据其他 Tool Result 自动推导或修改 Todo 状态。
- `todo_write` 更新成功后，先替换内存中的最新 TodoList，再由一个 render 函数读取这份当前最新 TodoList，并渲染为带状态符号的完整文本。状态展示映射为 `○ pending`、`› in_progress`、`✓ completed`，例如：
  ```text
  ✓ 读取 README
  › 修改代码
  ○ 运行测试
  ```
- render 产出的同一份完整 TodoList 文本同时用于 CLI 展示，以及作为 `todo_write` 的 Tool Result 返回模型并放回 `messages`；不为 CLI 和模型分别设计两套 Todo 输出格式。
- render 必须逐项完整保留 Todo item 原始的 `content`，不做摘要、改写或截断；它只负责按 `pending → ○ + 原始完整 content`、`in_progress → › + 原始完整 content`、`completed → ✓ + 原始完整 content` 添加展示符号并进行基本排版。不得为了 CLI 简洁而丢失任何 Todo content 信息。
- 展示符号不进入 Todo State；内部真实状态和工具输入协议始终使用 `pending / in_progress / completed`。
- Todo State 在 V05 只保存在内存中；持久化和恢复留给 V06。
- `todo_write` 的成功或错误由 Runtime 统一构造为与 `tool_use_id` 匹配的 Tool Result，再返回模型；同时自然经过 V04 的 Tool 生命周期 Hook。
- 不设计任务依赖、优先级、子任务树、负责人、Workflow、Task Graph 或更复杂的 Todo Framework。
- 不把 Plan 变成 Runtime 强制执行的固定步骤；下一步仍由模型决定。

**测试重点**

- `todo_write` 与其他普通工具使用同一 schema、handler map、Permission/Hook 和 Tool Result 路径，Agent Loop 没有 Todo 特殊分支。
- 完整 Todo List 可以创建状态，并在后续调用中通过整体替换表达内容修改、状态更新、项目新增或删除；不依赖增量 action。
- 空 `id`、重复 `id`、空 `content`、非法 `status`、多个 `in_progress` 和超过 20 项均被拒绝。
- 全量校验和原子替换：即使数组前部项目合法，只要任意一项失败，handler 就返回错误 Tool Result，旧 Todo State 在内容、顺序和状态上均完全不变。
- `completed` 不会因其他工具成功而自动产生；只有模型再次调用 `todo_write` 并提交合法的新完整状态后才改变。
- 成功更新时严格遵循“替换内存中的完整 TodoList → render 当前最新 TodoList → 将同一份渲染文本用于 CLI 和 Tool Result → Tool Result 放回 `messages`”的顺序。
- CLI 与 Tool Result 获得完全相同的完整 TodoList 文本，并用 `○ / › / ✓` 展示 `pending / in_progress / completed`；同时确认内存状态和工具输入协议仍使用英文状态值。
- 对包含长文本或细节信息的 `content` 验证渲染保真：CLI 和 Tool Result 中每一项都保留输入中的原始完整 `content`，没有摘要、改写或截断，render 仅增加对应状态符号和基本排版。
- Todo State 只在当前进程内有效，本版不测试或暗示跨进程恢复。

**为什么需要 V06**

会话和计划仍只存在于内存；进程退出或崩溃会丢失状态，错误也缺少会话级语义。

**手动验收场景 / Demo Cases**

1. **显式计划后完成多步任务**
   - 用户输入示例：`先用 todo_write 建立计划：读取 README、创建 todo_demo.txt、确认文件内容；然后逐项执行并更新状态。`
   - 预期运行轨迹：模型以完整列表调用 `todo_write` → 普通 Tool Runtime 调用 handler → handler 全量校验并整体写入内存状态 → render 当前最新完整 TodoList → 同一份带 `○ / › / ✓` 的文本用于 CLI 和 Tool Result → Tool Result 放回 `messages` → 模型用后续完整列表调用依次显式更新 `in_progress` 和 `completed` → 对应文件工具照常执行。
   - 预期最终效果：CLI 与模型收到相同的完整 TodoList 渲染文本，文件创建并被重新读取，全部 Todo 最终由模型显式更新为 `completed`。
   - 验收重点：验证完整状态替换、先更新后渲染、单一输出格式、原始 `content` 完整保留、显式完成，以及 Todo 通过普通 Tool Runtime 工作而非固定 Workflow。
2. **非法完整状态被原子拒绝**
   - 前置条件：先用一次合法 `todo_write` 建立至少两个 Todo，并记录 CLI 展示的旧状态。
   - 用户输入示例：`再次调用 todo_write 提交完整列表，但让两个项目同时为 in_progress；如果失败，检查原计划是否保持不变。`
   - 预期运行轨迹：`todo_write` Tool Use → handler 校验整个数组并发现多个 `in_progress` → 返回错误 Tool Result → 模型读取错误；不发生部分替换。
   - 预期最终效果：调用失败后，内存中的 Todo 内容、顺序和状态与调用前完全一致。
   - 验收重点：验证全量校验、最多一个 `in_progress` 和失败时的原子性；空字段、重复 ID、非法状态及数量上限由自动测试覆盖。
3. **拒绝写权限后不得自动完成 Todo**
   - 用户输入示例：`建立计划并创建 todo_denied.txt；如果我拒绝写入，请更新计划并说明未完成原因。`
   - 预期运行轨迹：模型用完整列表创建 Todo 并将对应项显式设为 `in_progress` → write_file ask → 用户拒绝 → Permission Denied Tool Result → Runtime 不自动修改 Todo → 模型再次调用 `todo_write` 提交合法完整状态，使该项保持 `pending` 或 `in_progress`，但不得标为 `completed`。
   - 预期最终效果：文件不存在，Todo 没有被 Runtime 根据 Tool Result 自动完成，最终总结明确说明未完成原因。
   - 验收重点：验证 Todo 只有三种状态、完成必须显式写入，以及 Todo、Permission 和 V04 Hooks 各自职责不变。

### V06：状态与可靠性

**要解决的问题**

V06 分开解决两个不能混为一谈的可靠性问题：

- **A. In-Process Recovery**：进程仍存活时，如何从 LLM/API 瞬态故障中恢复，同时不把 Tool/User Error 错当成 API 故障？
- **B. Restart Recovery**：发生 Ctrl-C、进程退出或重启后，如何恢复此前 Session/Run，并在 Tool 副作用不确定时依据真实 workspace 安全继续？

**本版目标**

- 为 LLM/API retryable error 建立最小分类和有限重试闭环。当前已经真实遇到 DeepSeek-V4-Flash 返回 `429 model_concurrency_rate_limit_exceeded`，因此本版必须处理这类 transient error，而不是只预留接口。
- 定义并持久化恢复 Session/Run 所必需的最小 Runtime State，明确它的语义、生命周期和 checkpoint 时机。
- 使用原子本地 JSON state file，使失败写入不会直接破坏上一份可恢复状态。
- restore 后依据 `current_run.status` 可靠地区分 completed、failed、running 和 interrupted Run；只对需要恢复的 running/interrupted Run 保留原 Run 和 round，并在副作用不确定时先核对 workspace。
- 在现有 EventLogger、CLI 和 JSONL 基础上，使 checkpoint、restore、retry、interruption 和 resume 可观察。

**最小但完整设计**

#### A. In-Process Recovery

- 只对分类为 retryable 的 LLM/API transient error 做有限重试；`429 model_concurrency_rate_limit_exceeded` 是当前已确认需要覆盖的真实案例。V06 只实现当前真实需要的 transient retry，不扩展成通用容错框架。
- retry 是同一个 Agent **Round** 内的 **Attempt**，不得增加 Agent Round。`MAX_ROUNDS` 约束逻辑模型轮次，不能因 API retry 被错误消耗，也不能通过 retry 或重启被绕过。
- retry 参数固定为：`MAX_RETRIES = 4`、`BASE_DELAY = 1.0s`、`MAX_DELAY = 16.0s`、`JITTER_RATIO = 0.25`。`MAX_RETRIES` 不包含 initial LLM request，表示 initial request 失败后最多额外 retry 4 次，因此同一 Round 最多发生 5 次实际 LLM API 调用；配置层使用 retry 计数，不增加 `MAX_ATTEMPTS` 常量。
- Observability 仍使用 Attempt 描述实际调用次序：`attempt=1` 是 initial request，`attempt=2` 是 retry 1，`attempt=3` 是 retry 2，`attempt=4` 是 retry 3，`attempt=5` 是 retry 4。所有 Attempt 始终属于同一 Round，不增加 Agent Round，也不额外消耗 `MAX_ROUNDS`。
- 没有有效 `Retry-After` 时，以 `BASE_DELAY` 使用 bounded exponential backoff，retry 1/2/3/4 的基础 delay 依次为 `1s / 2s / 4s / 8s`，再在基础 delay 上增加 `0~25%` jitter；加入 jitter 后的最终实际 sleep 仍受 `MAX_DELAY = 16s` 硬上限约束。如果服务端提供有效 `Retry-After`，优先采用该值，但最终实际等待同样不得超过 `MAX_DELAY = 16s`。
- 每次 retry 记录 attempt 和最终实际 delay；4 次 retry 全部失败后明确产生 retry exhausted，current Run 进入 `failed`，不悄悄吞掉错误，也不继续无限调用模型。
- Tool/User Error 不触发 LLM/API retry，包括但不限于 `FILE_NOT_FOUND`、`INVALID_TOOL_INPUT`、`PERMISSION_DENIED`。这类错误继续通过结构化 Tool Result 返回 LLM，由模型判断下一步。

#### B. Restart Recovery

- 处理 Ctrl-C、进程退出和重启后的恢复，不尝试恢复已经消失的 Python/Shell 调用栈或正在执行的子进程。
- 持久化必要 Runtime State。`current_run.status` 的最小合法值固定为 `running / completed / failed / interrupted`，不增加 `unfinished` boolean。
- `running` 表示 Run 正常执行中；restore 时若旧 state 仍为 running，说明上一个进程未正常结束，作为 unexpected unfinished Run 恢复。
- `completed` 表示 Run 已正常完成；只恢复 Session 上下文并等待下一条用户输入，不重跑旧任务。
- `failed` 表示 Run 已明确失败并结束；恢复时保留失败事实，不自动把它当作 running 继续。
- `interrupted` 表示 Runtime 捕获到中断，并已成功持久化中断状态；作为明确中断的 unfinished Run 恢复。
- running/interrupted Run 恢复同一个 `run_id` 和原 `round`，不能创建新 Run 或重置计数来绕过 `MAX_ROUNDS`；Runtime 根据 `interruption_info` 构造最小 recovery context，再让模型判断如何继续。
- 如果中断发生在可能产生 Tool 副作用的阶段，不自动重放 Tool；恢复后先让模型使用现有工具检查真实 workspace，再决定后续动作。

#### 最小 Persisted Runtime State

V06 只定义以下 State 的语义和生命周期：

```text
schema_version
session_id
messages
todos                 # V05 Todo State
current_run
  run_id
  status
  round
interruption_info
updated_at            # 可选 observability/debug metadata
```

- `schema_version` 用于加载时的结构/兼容性校验；不兼容状态必须明确失败，不能猜测恢复。
- `session_id` 标识被恢复的会话；`messages` 和 `todos` 分别恢复模型上下文与 V05 的当前完整 Todo State。
- `current_run` 描述当前或最后一个 Run；`status` 只使用 `running / completed / failed / interrupted`，不另设 `unfinished` boolean；`round` 是该 Run 已使用/正在恢复的逻辑 Round 位置。
- `interruption_info` 描述当前或最后一个 in-flight 执行现场；它不是 Tool Result，也不是错误消息。
- `updated_at` 可以用于日志、排障和人工观察，但恢复正确性不得依赖时间戳推断执行是否成功、状态谁更新或 Tool 是否产生副作用。
- 实现时根据当前代码选择最小的 `dict`、`dataclass` 或同等表达即可；本版不提前规定 `StateManager`、`StateRepository`、通用 State Machine 或复杂类层次。

#### `interruption_info` 语义

- phase 必须来自当前 Runtime 的真实一致性边界，第一版最小集合为 `llm_call`、`permission_wait`、`tool_execution`、`tool_result_recording`；若实现核对发现某个 phase 在当前调用路径不存在，应以实际代码中的最小等价阶段为准，而不是制造抽象流程。
- Tool 相关阶段只记录恢复判断必需的信息，例如 `tool_name` 和 `tool_use_id`。
- 进入可能产生不确定副作用的关键阶段前，先设置 `interruption_info` 并 checkpoint；当对应结果已经可靠记录后再清除并 checkpoint。
- 不将其扩展为通用状态机、完整 Tool Call Registry、执行历史或 Tool Result 的替代品。

#### Checkpoint

- Checkpoint 是 Runtime 自动触发的持久化动作，不是 Tool，也不由模型主动调用。
- Runtime 在关键一致性边界保存完整的最小 Runtime State，至少包括：user message 已追加；Run status 变化；LLM response 已可靠追加；Tool Result 已可靠记录；Todo State 成功变化；`interruption_info` 设置或清除；Run completed、failed 或 interrupted。
- 对可能产生不确定副作用的阶段，在进入阶段前设置 `interruption_info` 并 checkpoint。Checkpoint 的先后关系必须让恢复逻辑能够区分“尚未进入关键阶段”和“可能已经执行但结果尚未可靠记录”。
- Todo checkpoint 只发生在 V05 handler 已完成全量校验并成功整体替换之后；失败的 Todo 更新不改变持久化 Todo State。

#### Atomic Persistence

- V06 第一版使用本地 JSON state file，但这只是当前单进程版本的最小持久化选择，不是未来架构承诺。
- 保存顺序固定为：`serialize → same-directory temp file → write → flush/fsync → close → os.replace`。不得直接覆盖正式 state file。
- 临时文件与正式文件位于同一目录，以使用原子 replace 语义；写入、同步或替换失败必须明确报告，并保留上一份可解析的正式状态。
- atomic replace 只保证 Runtime State 文件自身的一致性，不提供 workspace 与 state file 之间的跨文件事务，也不能证明外部 Tool 副作用是否发生。

#### Restore / Resume

恢复流程为：

```text
restart
→ load state
→ validate schema/structure
→ restore messages/todos/session/run/round/interruption_info
```

- 显式 resume 时，如果 state file corrupted、truncated、schema incompatible 或必需字段非法，必须明确输出 `State Load Error` 并写入 state load failure Observability event；不得修改或覆盖原 state file，不得静默创建空 Session，也不进入交互式 Y/N 新建流程；当前 resume 进程以 non-zero exit 结束。用户若要新建 Session，必须使用普通启动方式显式创建。
- 如果 previous Run completed：恢复 Session 上下文，等待下一条用户输入，不重跑旧任务。
- 如果 previous Run running：视为上一个进程未正常结束的 unexpected unfinished Run，恢复同一个 Run 和 round，根据 `interruption_info` 构造最小 recovery context，重新让模型判断如何继续。
- 如果 previous Run interrupted：视为已明确记录中断的 unfinished Run，恢复同一个 Run 和 round，根据 `interruption_info` 构造最小 recovery context，重新让模型判断如何继续。
- 如果 previous Run failed：恢复 Session 上下文并保留失败事实，不自动继续该 Run；后续动作由新的用户输入触发。
- recovery context 只陈述已可靠持久化的事实、in-flight phase 及副作用不确定性；不得把 `interruption_info` 伪装成 Tool Result，也不得声称 Tool 成功或失败。

#### Workspace Reconciliation

- workspace 是真实外部状态。不得持久化并依赖 `workspace_modified=true/false` 之类布尔值作为事实来源。
- 如果 Tool 在中断前可能已经产生副作用、但 Tool Result 尚未可靠记录，则：不假设成功、不假设失败、不自动 replay。
- 恢复后的模型使用 `read_file`、`grep`、`bash`、`git diff`、`pytest` 等既有工具检查真实 workspace，并根据实际结果继续任务。
- V06 不承诺 exactly-once execution，也不引入 workspace + state 事务、完整 Tool Call Registry 或 Event Sourcing。

#### Error Handling 的可扩展边界

不建立万能 `GlobalErrorHandler`，保持三个职责层次：

```text
① Tool/User Error
   → Tool Result → LLM 处理

② LLM/API Error
   → classification → retry / fail

③ Process Interruption
   → checkpoint → restore → reconcile
```

- LLM/API Error Classification 与恢复逻辑形成当前版本所需的最小统一边界，使 Agent Loop 不直接堆叠具体供应商 API exception；分类只承载是否 retryable、可选 `Retry-After` 和形成明确失败所需的信息。
- V06 只实现真实需要的 transient retry。未来 V07 如果出现 `PROMPT_TOO_LONG`，可以在同一分类边界新增 `PROMPT_TOO_LONG → Compact → retry same Round`，而不重写 Agent Loop。
- V06 不实现 Context Compact，也不为了未来建立 ErrorHandler Framework、Recovery Registry、Strategy Pattern、Plugin 或 DSL。

#### Observability

- 复用现有 EventLogger、CLI 和 JSONL，至少观察：checkpoint/save、restore、interrupted/resumed、retry attempt、retry delay、retry exhausted、state load failure。显式 resume 加载失败时，CLI 输出 `State Load Error`，对应事件写入后进程 non-zero exit。
- 日志必须明确区分：**Round** 是 Agent 的逻辑模型轮次；**Attempt** 是同一 Round 内的实际 LLM API 调用次序。`attempt=1` 表示 initial request，`attempt=2/3/4/5` 分别表示 retry 1/2/3/4；retry 不增加 Round，也不额外消耗 `MAX_ROUNDS`。
- 日志和 `updated_at` 用于解释发生过什么，不作为恢复正确性的事实来源。

**测试重点**

- save/restore consistency，以及 Todo、messages、session、run、round 的完整恢复。
- atomic write failure 时不破坏上一份正式 state file。
- 显式 resume 遇到 corrupted、truncated、schema incompatible 或必需字段非法时，输出 `State Load Error`、写入 state load failure event、保持原文件不变并以 non-zero exit 结束；不创建空 Session，也不进入 Y/N 流程。
- `KeyboardInterrupt` 能留下可解释的 interruption 状态。
- completed Run restore 后只等待新输入，不重跑旧任务。
- running Run restore 时按 unexpected unfinished Run 处理，保留同一 Run 和 round，并重新交给模型判断。
- interrupted Run restore 时按明确中断的 unfinished Run 处理，保留同一 Run 和 round，并重新交给模型判断。
- failed Run restore 时保留失败事实，不自动继续，也不改写为 running。
- uncertain Tool side effect 不自动 replay，模型先 inspect workspace 再继续。
- retryable LLM/API error 在同一 Round 内最多产生 5 个 Attempt；覆盖真实的 429/transient 分类，并验证 `attempt=1..5` 与 initial/retry 1..4 的映射。
- 验证 `MAX_RETRIES = 4`、`BASE_DELAY = 1.0s`、`MAX_DELAY = 16.0s`、`JITTER_RATIO = 0.25`；`MAX_RETRIES` 不包含 initial request，配置层不存在 `MAX_ATTEMPTS`。
- 没有有效 `Retry-After` 时，验证 retry 1/2/3/4 的基础 delay 为 `1s / 2s / 4s / 8s`，在其上增加 `0~25%` jitter，且最终实际 sleep 不超过 `16s`。
- 有效 `Retry-After` 优先于本地 backoff，但验证最终实际等待仍受 `MAX_DELAY = 16s` 硬上限约束。
- retry 4 仍失败后明确产生 retry exhausted，current Run 进入 `failed`，且整个过程不增加 Agent Round、不额外消耗 `MAX_ROUNDS`、不继续调用模型。
- `FILE_NOT_FOUND`、`INVALID_TOOL_INPUT`、`PERMISSION_DENIED` 等 Tool/User Error 不触发 API retry。

**本版明确不做**

- Database、Event Sourcing、exactly-once Tool execution、完整 Tool Call Registry。
- Workflow / Task Graph、通用 State Machine、distributed lock。
- fallback model、circuit breaker。
- Context Compact、Memory、Background Task、Multi-Agent。
- 不恢复正在执行的 Shell 进程，不为未来 Workflow、多 Agent 或分布式执行提前持久化不存在的字段。

**为什么需要 V07**

V06 使 Session/Run 可以可靠延续，但不会控制持续增长的 messages 和 Tool Result；长期会话最终会接近模型上下文限制。因此 V07 才引入 Context Budget 与 Compact。即使未来把 `PROMPT_TOO_LONG` 接入 V06 建立的分类边界，Compact 的预算、裁剪、摘要和配对保护仍全部属于 V07，不提前进入 V06。

**手动验收场景 / Demo Cases**

1. **正常 Session 恢复**
   - 用户输入示例：首次运行输入 `记住本次任务标记是 STATE-42，并建立一个未完成 Todo：读取 README。`；正常退出并用 README 规定的恢复命令重新启动后输入 `继续上一任务，并告诉我任务标记。`
   - 预期运行轨迹：首次保存 messages、Todo、session 和四值 Run status → 重启加载并校验状态 → 恢复上下文；completed 等待新输入，running/interrupted 恢复原 Run/round，failed 保留失败事实但不自动继续。
   - 预期最终效果：恢复后能回答 `STATE-42`，Todo 和 Run 语义一致，不开启空白历史，也不重跑 completed Run。
   - 验收重点：验证持久化/恢复一致性及 `running / completed / failed / interrupted` 四种状态的恢复分流，不存在额外 `unfinished` boolean。
2. **Tool 执行附近中断后 reconcile**
   - 用户输入示例：`创建 state_demo.txt 并读取确认。`；在 permission wait、tool execution 或 tool result recording 附近按 Ctrl-C，再恢复该 Session 并输入 `检查上一任务实际完成到哪里，再继续。`
   - 预期运行轨迹：进入关键阶段前设置 `interruption_info` 并 checkpoint → 中断后 state file 仍可解析 → 恢复同一 Run/round → recovery context 明确副作用未知 → 模型使用 `read_file`、`git diff` 等既有工具检查 workspace → 根据事实继续，不自动 replay。
   - 预期最终效果：不会重复或虚构已完成操作，最终文件状态与总结一致。
   - 验收重点：验证 interruption phase、原子状态写入、同 Run/round 恢复、workspace reconciliation 和 uncertain Tool side effect 不自动重放。
3. **429 / transient LLM/API retry**
   - 前置条件：用可控 fake client 或测试注入复现 `429 model_concurrency_rate_limit_exceeded`，分别提供和不提供 `Retry-After`。
   - 预期运行轨迹：错误被分类为 retryable → `attempt=1` initial request 失败 → 最多执行 retry 1/2/3/4，对应 `attempt=2/3/4/5` → 无有效 `Retry-After` 时使用 `1s / 2s / 4s / 8s` 基础 delay，并增加 `0~25%` jitter；有有效 `Retry-After` 时优先采用 → 两条路径的最终实际等待都不超过 `MAX_DELAY = 16s` → 成功后继续，或 retry 4 后明确 retry exhausted 并将 current Run 置为 `failed`。
   - 预期最终效果：`MAX_RETRIES = 4` 且不包含 initial request，同一 Round 最多 5 次实际 LLM API 调用；瞬态恢复不增加 Agent Round，也不额外消耗 `MAX_ROUNDS`，持续故障有限失败，不无限重试。
   - 验收重点：验证分类、Round/Attempt 映射、两种 delay 来源、四个固定参数、上限和 exhaustion observability；配置层不出现 `MAX_ATTEMPTS`。
4. **Tool/User Error 不触发 retry**
   - 用户输入示例：`读取 definitely_missing.txt，不要创建；告诉我错误。`；另一次场景在写文件权限确认时拒绝。
   - 预期运行轨迹：工具分别返回 `FILE_NOT_FOUND` 或 `PERMISSION_DENIED` Tool Result → 模型处理；LLM/API retry attempt 不增加。
   - 预期最终效果：明确报告实际 Tool/User Error，不出现无意义的重复 API 请求。
   - 验收重点：验证 Tool/User Error、LLM/API Error 与 Process Interruption 三层边界。
5. **显式 resume 遇到非法 state**
   - 前置条件：分别准备 corrupted、truncated、schema incompatible 或必需字段非法的 state file，并保留其原始内容用于恢复后比对。
   - 预期运行轨迹：使用显式 resume 命令加载 → CLI 输出 `State Load Error` → EventLogger/JSONL 写入 state load failure → 当前进程 non-zero exit。
   - 预期最终效果：原 state file 内容未被修改或覆盖；没有静默创建空 Session，也没有进入交互式 Y/N 新建流程。需要新 Session 时，用户另行使用普通启动方式。
   - 验收重点：验证加载失败的输出、事件、文件保护和退出码形成一致闭环。

### V07：Context Budget 与 Compact

**要解决的问题**

如何在上下文超限前控制大小，同时保留继续完成任务所需的信息？

**本版目标**

- 请求前估算 Context Budget。
- 优先缩短旧的大型 Tool Result。
- 必要时总结较旧历史。
- 保持 Tool Use 与 Tool Result 配对完整。

**最小但完整设计**

- 当前版本必须真实实现请求前预算判断、触发决策、旧大型 Tool Result 的安全裁剪、必要的历史摘要、协议配对保护和失败回退；不能只统计长度或在超限后被动报错。
- 保守字符估算、token 估算和固定阈值都是第一版候选方案；实施时根据实际模型接口、可获得的计量信息和失败案例选择最小可验证方案，阈值必须可测试且有明确依据，但不预先固化为长期策略。
- 不建立复杂 Context 策略框架。
- 只有一种压缩策略不足时，再根据失败案例增加层次。
- 不提前设计多层 Context 策略，不等于可以省略本版从预算检测到压缩、继续调用及失败保护的完整闭环。

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

**最小但完整设计**

- 当前版本必须真实实现稳定信息的明确写入/确认、跨 session 加载、合并去重、容量控制和注入边界；不能把完整历史文件直接当作 Memory，也不能仅靠提示词声称“记住”。
- Memory 使用简单本地文件；Markdown、JSON 或其他同等简单的表示是候选方案，在 V08 开始时根据真实数据形态和所需更新语义选择，不把文件格式预先规定为未来 Memory 架构。
- 不使用 Embedding、向量库、RAG 或复杂自动召回。
- 不保存临时任务进度、完整对话或大段源码。
- 不提前设计自动召回或知识库，不等于可以省略本版稳定信息从写入到新 session 使用的完整、可验证生命周期。

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

**最小但完整设计**

- 当前版本必须真实验证现有机制形成一个由实际结果驱动的闭环：探索、计划、修改、测试、读取失败结果、继续修复、再次验证并如实总结；不能用预设成功轨迹、单次脚本或只改提示词来代替机制协同。
- 只做 V01–V08 现有组件（包括 V04 Hooks 与 V05 Plan/Todo）的必要装配、边界修正和提示词调整；具体编排方式由受控缺陷案例暴露的问题决定，不预先固化新的执行框架。
- 不加入独立 Goal Evaluator、SubAgent、MCP、Workflow 或后台任务。
- 是否完成仍由主模型判断，测试结果作为模型观察。
- 不提前设计 Goal/Workflow 能力，不等于可以省略本版失败后继续行动、以测试结果验证完成状态及事件轨迹证明闭环的完整验收。

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
- 依赖已经形成清楚边界的贯穿式 Observability、V02 工具与文件操作、V03 权限、V04 Hooks 和 V06 状态与可靠性。
- 不在 V01–V09 为 MCP 预设 Tool Protocol。

### V12：SubAgent

- 探索主 Agent 委派专用子任务。
- 学习任务拆分、上下文隔离、预算限制和结果汇总。
- 依赖稳定的 Agent Loop、工具/权限、状态和 Context。
- 先证明单 Agent 的限制，再判断哪些任务值得委派。

### V13：Workflow Runtime

- 探索固定流程编排。
- 区分模型自主决定下一步的 Agent Loop，与代码控制步骤的 Workflow。
- 依赖贯穿式 Observability、V04 Hooks、V05 Plan/Todo、V06 状态与可靠性和稳定的单 Agent。
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
