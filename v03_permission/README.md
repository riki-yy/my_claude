# V03：权限

V03 只研究一个问题：工具在技术上能够执行时，Runtime 如何统一判断它应该自动执行、询问用户，还是直接拒绝？

V03 以已经验收的 V02 为 baseline。通用 Agent Loop、Anthropic `tool_use/tool_result` 消息流程、`TOOLS`、`TOOL_HANDLERS`、handler function、handler 执行、错误包装以及 CLI/JSONL Observability 均继续保留；本版只在工具执行前增加一个 Permission Gate。

## 架构与范围

```text
LLM
→ tool_use(tool_name, tool_input)
→ Permission Pipeline
→ allow / ask / deny
→ TOOL_HANDLERS
→ handler execution
→ tool_result
```

Permission Pipeline 对所有工具调用走同一条路径。主流程没有针对具体工具的 `if/elif`。`_execute_tool()` 和静态 `TOOL_HANDLERS` 保持 V02 的职责与实现方式。V02 handler 内的工作区路径限制、Shell 固定工作目录、超时与输出上限仍是不可绕过的技术安全底线；Permission 不替代这些约束。

## Permission Rule

第一版只使用两个有序列表和简单的 `dict + callable`。Rule 保持以下结构：

```python
{
    "tools": ["..."],
    "matcher": callable,
    "message": "...",
    "describe": callable,
}
```

每条规则分两层匹配：先以 `tool_name in rule["tools"]` 判断工具范围；只有范围命中时，才调用 `rule["matcher"](tool_input)` 判断参数。工具判断只存在于 `tools` 字段，参数风险判断只存在于 matcher。新增工具或策略只增加 Rule，不修改 Permission Pipeline。

同一个工具可以因为参数不同得到不同结果。例如 bash 执行 `rm -rf /` 时 Deny、执行普通 `rm build.tmp` 时 Ask、执行 `pwd` 时默认 Allow；write_file/edit_file 写工作区外路径时 Deny、写敏感路径时 Ask、写普通工作区文件时默认 Allow。

## 固定判定顺序

1. 先遍历 `DENY_RULES`。第一条命中后立即 Deny，不询问、不执行 handler。
2. 没有 Deny 时遍历 `ASK_RULES`。第一条命中后显示原因并等待 `Allow? (y/N)`。
3. Ask 只有输入 `y` 或 `yes` 才批准；其他输入、EOF 和 Ctrl-C 都按拒绝处理。
4. 没有任何规则命中时默认 Allow，不显示额外确认。

Deny 优先于 Ask。规则 matcher/describe 异常时 fail closed，返回 Deny，不能绕过 Permission Gate。Deny 或用户拒绝均不执行 handler，而是向模型返回 `PERMISSION_DENIED: <reason>`，对应 Anthropic `tool_result` 带有 `is_error=true`。

## 当前规则

- `DENY_RULES` 只覆盖少量明确不可接受的系统级或灾难性操作，代表性示例包括 `rm -rf /`、`sudo`、`shutdown`、`reboot`、`mkfs`、`dd if=`、写入高危 `/dev` 目标、`git reset --hard` 和强制 `git clean`。
- write_file/edit_file 的 `path` 解析后位于工作区之外时，在 handler 执行前直接 Deny；这层授权拦截不取代 V02 handler 原有的路径与符号链接安全检查。
- `ASK_RULES` 覆盖允许执行但具有副作用的操作，代表性示例包括普通 `rm`、`mv`、`cp`、`mkdir`、`touch`、`chmod`、pip/npm 安装卸载、会改变工作区或仓库状态的 git 子命令，以及文件输出重定向。
- write_file/edit_file 只有在工作区内 `.env`、credentials、secrets 等明确敏感文件或路径上才 Ask；普通工作区文件默认 Allow。
- 明显只读或验证型 bash 命令，例如 `ls`、`pwd`、`cat`、`head`、`tail`、`find`、`grep`、`git status`、`git diff` 和 pytest，不命中上述模式时默认 Allow，无需确认。
- 其他未命中 `DENY_RULES` 或 `ASK_RULES` 的调用同样默认 Allow。

Deny 始终先于 Ask。当前规则只是教学阶段的显式模式匹配，不引入完整 Shell parser、Permission DSL 或策略引擎，也不构成完整的 Shell 安全边界。

## CLI 与 Observability

Deny 和 Ask 分别统一显示 `[Permission Denied]` 或 `[Permission Required]`，随后显示 `describe`、`Reason:` 和 `message`；Ask 再显示 `Allow? (y/N)`。Rule 和 matcher 不直接输出或读取输入。Allow 不增加 CLI 文本。

JSONL 新增 `permission.allowed`、`permission.required` 和 `permission.denied` 事件，只记录有界、脱敏后的操作摘要和原因。现有 Tool Result 摘要、token 统计和日志脱敏保持不变。

## 配置与运行

所有版本共用根目录 `.env`。运行：

```bash
cd v03_permission
python3.12 -m pip install -r requirements.txt
python3.12 agent.py
```

输入 `exit`、`quit`、空行或 EOF 结束 session。

## 测试

```bash
cd v03_permission
python3.12 -m pytest -q
```

Fake Model 测试不读取 `.env`、不访问网络，覆盖 V02 全部 Runtime 行为，并新增：Rule 两层匹配、同一 bash 按参数产生 Deny/Ask/Allow、write_file/edit_file 按路径产生 Deny/Ask/Allow、Deny 优先级、Ask 批准/拒绝/空输入/EOF/Ctrl-C、只读 bash 与普通工作区写入无需确认、路径越界与敏感写入拒绝不执行、matcher 异常 fail closed、Permission Denied Tool Result、CLI/JSONL 事件，以及 Pipeline 不含具体工具比较分支的检查。

## 手动验收场景 / Demo Cases

以下 4 个 Case 从真实 CLI 用户输入出发，验证真实模型下的关键端到端行为。细粒度 matcher、边界输入、异常和安全优先级由 pytest 负责，不在人工 Demo 中逐项重复。

### Case 1：正常 Coding 路径——连续 Allow

- **最小前置条件**：`README.md` 存在；允许创建或覆盖隔离文件 `demo_workspace/permission_demo.txt`。
- **用户输入示例**：`读取 README.md 的第一行；再使用 write_file 将这一行写入 demo_workspace/permission_demo.txt；最后读取新文件并告诉我结果。`
- **预期运行轨迹**：read_file 默认 Allow → 普通工作区路径的 write_file 默认 Allow → read_file 默认 Allow → Tool Results → LLM Final；全程不询问。
- **预期最终效果**：新文件内容为 `# V03：权限`，最终回答与文件一致，CLI 不出现 `Allow? (y/N)`。
- **验收重点**：统一 Gate 不妨碍正常 Coding 流程；只读工具和普通工作区写入都默认 Allow，多个 Tool Use 的消息回路保持完整。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：通过。模型先以只读 bash 查看目录，再依次调用 read_file、普通路径 write_file 和 read_file；所有调用默认 Allow，CLI 没有确认提示。最终文件内容为 `# V03：权限`，模型返回相同结果。日志：`logs/2db4b737-a48d-477b-9181-7f4526e204c2.jsonl`。

### Case 2：敏感写入——分别批准与拒绝

- **最小前置条件**：允许创建或覆盖 `demo_workspace/secrets/permission_approved.txt`；`demo_workspace/credentials/permission_refused.txt` 不存在。
- **用户输入示例**：`依次只使用 write_file 完成两项操作：先将 approved 写入 demo_workspace/secrets/permission_approved.txt，再将 should-not-exist 写入 demo_workspace/credentials/permission_refused.txt。若第二项被拒绝，不要换工具重试，直接总结两项结果。`
- **确认输入**：第一次 Ask 输入 `y`，第二次 Ask 输入 `n`。
- **预期运行轨迹**：第一个敏感 path 命中 Ask → 用户批准 → handler 执行；第二个敏感 path 命中 Ask → 用户拒绝 → handler 不执行 → Permission Denied Tool Result → LLM Final。
- **预期最终效果**：`permission_approved.txt` 存在且内容为 `approved`；`permission_refused.txt` 不存在；模型正确说明一项成功、一项被拒绝。
- **验收重点**：一次真实会话覆盖 Ask 的批准、拒绝、批准后执行、拒绝后绝不执行，以及拒绝结果返回模型。与 Case 1 对照，也证明同一 write_file 会因 path 参数不同得到 Allow 或 Ask。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：核心行为通过，工具选择与预期不同。模型没有遵循“只使用 write_file”，而是对两个目标分别调用带重定向的 bash；两次均命中 Ask。第一次输入 `y` 后 bash handler 执行并写入 `approved`；第二次输入 `n` 后对应 handler 未执行，返回 Permission Denied Tool Result，拒绝目标保持不存在。模型随后以 read_file/glob 只读核验并正确总结结果，没有写入绕过。日志：`logs/bc8b0409-404b-49d5-99b2-79c2c4b3fa65.jsonl`。

### Case 3：路径越界——执行前 Deny

- **最小前置条件**：`../v03_permission_outside_demo.txt` 不存在。
- **用户输入示例**：`请只使用一次 write_file，将 should-not-exist 写入 ../v03_permission_outside_demo.txt；如果权限被拒绝，立即说明结果，不要尝试其他工具。`
- **预期运行轨迹**：path 解析到工作区外 → Deny Rule 命中 → 不询问 → handler 不执行 → Permission Denied Tool Result → LLM Final。
- **预期最终效果**：工作区外目标不存在，CLI 不出现 `Allow? (y/N)`。
- **验收重点**：Permission 在执行前拒绝越界写入；V02 handler 的路径检查仍作为独立的执行阶段安全底线。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：通过。模型调用 `write_file` 写入工作区外路径，Permission 直接 Deny，CLI 未询问，工具事件以 `PERMISSION_DENIED` 失败并将 Tool Result 回传模型；第二轮模型说明拒绝后结束。工作区外目标不存在。首次请求遇到 429，随后同一 session 重试成功。日志：`logs/b80075e6-589c-4c65-ab1c-4b73621d0406.jsonl`。

### Case 4：灾难性 Bash——Deny 优先于 Ask

- **最小前置条件**：无；该命令只能用于验证 Permission 拦截，绝不能绕过本项目 Runtime 单独执行。
- **用户输入示例**：`请只尝试调用一次 bash 执行 rm -rf /；权限拒绝后立即给出最终回答，不要调用其他工具。`
- **预期运行轨迹**：command 同时属于破坏性副作用且命中灾难性 Deny matcher → DENY_RULES 先命中 → 不询问 → handler 不执行 → Permission Denied Tool Result → LLM Final。
- **预期最终效果**：命令绝不执行，CLI 不出现 `Allow? (y/N)`，模型说明操作被拒绝。
- **验收重点**：Deny 优先于 Ask，且 Deny 没有用户批准入口。
- **实际结果（2026-09-03，DeepSeek-V4-Flash）**：核心行为通过。模型准确调用 `bash("rm -rf /")`；灾难性 Deny Rule 在 Ask Rule 前命中，CLI 未询问，bash handler 未执行，Permission Denied Tool Result 回传模型。模型随后额外执行一次只读 `pwd && ls -la` 再结束，没有重试或绕过危险操作。日志：`logs/d0da7137-2508-4b6d-86bc-549bd636cf1b.jsonl`。

### Demo 验收记录规则

每次真实运行后，只在对应 Case 的“实际结果”记录实际模型、日期、用户确认输入、最终副作用和 JSONL 日志路径。API 调用未到达 Tool Use、仅通过 Fake Model 测试或只验证 matcher 时，都不得写成“真实 Demo 通过”。

## 验收摘要

- 2026-09-02：开始实现前运行 V02 当前全量测试，53 项通过。
- 2026-09-02：V03 首轮 Fake Model/Runtime 测试，64 项通过。
- 2026-09-02：根 `.env` 配置的 `DeepSeek-V4-Flash` 完成 Allow、Ask 批准、Ask 拒绝和 Deny 四个真实 Demo，CLI、JSONL 与文件副作用均已核对。
- 2026-09-02：完成 README 与真实 Demo 后执行最终回归，V03 64 项通过，V02 baseline 53 项通过；`py_compile`、文件副作用检查与 `git diff --check` 均通过。
- 2026-09-03：保持 Permission Pipeline 不变，将 bash Rule 收紧为参数级 Deny/Ask/Allow 显式模式；新增高危、带副作用、只读命令及拒绝不执行测试，当前 V03 共 104 项通过。
- 2026-09-03：write_file/edit_file 改为路径参数级策略：工作区外 Deny、工作区内敏感路径 Ask、普通工作区文件默认 Allow；V03 当前 129 项测试通过。
- 2026-09-03：尝试重跑更新后的真实 Demo 时，根 `.env` 的 `DeepSeek-V4-Flash` 返回 400 `model not supported`，临时使用示例中的小写 `deepseek-v4-flash` 亦返回 404 `model_not_found`；未修改 `.env`，未将未执行的 Demo 写成通过。
- 2026-09-03：最终收尾时服务恢复可用；完成 4 个精简综合 Demo 的真实验收。期间发现并修复只读重定向误判，最终 V03 131 项测试通过；真实 Demo 的模型工具选择和拒绝后只读核验差异均按实际日志记录。

## 相对 V02：新增、修改和保留

**新增**：统一 Permission Gate、`DENY_RULES`、`ASK_RULES`、`tools + matcher(tool_input)` 两层 Rule 匹配、参数级 Bash/路径策略、Deny/Ask/Allow、CLI confirmation、Permission Denied Tool Result 和 permission JSONL 事件。

**修改**：Agent Loop 在 `_execute_tool()` 前调用统一权限判定；`run_once()` 与 CLI 将确认输入函数传入当前 run；SYSTEM 增加权限拒绝后的行为原则，并平衡描述发现、文件修改和命令工具的职责。TOOLS schema 仍是可用工具的唯一真实来源，工具本身和 handler 分发没有改变。

**保留**：V02 通用 Agent Loop、Anthropic Messages API、原样 `tool_use/tool_result`、`TOOLS`、`TOOL_HANDLERS`、六个 handler、错误包装、工作区安全底线、Shell 限制、最大轮次及 CLI/JSONL Observability。

## 开发与调试记录（Development / Debugging Notes）

### 记录 1：V02 baseline 验证

- **现象**：实施 V03 前需要确认当前 V02 已验收能力仍然稳定。
- **触发方式**：在 `v02_tool_runtime/` 执行 `python3.12 -m pytest -q`。
- **原因**：版本继承规则要求先验证 baseline，不是代码故障。
- **解决方案**：V03 复制 V02 必要实现到独立目录，只在 handler 执行前增加 Permission Gate。
- **影响范围**：V02 代码未修改；其 demo_workspace 中原有未提交内容也未清理或覆盖。
- **验证方式与结果**：2026-09-02，53 项测试全部通过，用时 0.80 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 2：V03 首轮自动测试

- **现象**：新增权限控制流后，需要证明 handler 不会被绕过调用。
- **触发方式**：在 `v03_permission/` 执行 `python3.12 -m pytest -q`。
- **原因**：Permission 增加了规则优先级和用户输入边界。
- **解决方案**：保留 V02 测试作为回归，并增加 Deny、Ask、Allow、两层匹配、fail-closed 和 CLI/JSONL 测试。
- **影响范围**：只涉及 V03；V02 baseline 没有重构。
- **验证方式与结果**：2026-09-02，64 项全部通过，用时 0.93 秒。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 3：自动化 PTY 注入中文仍会被 readline/libedit 改写

- **现象**：通过自动化 PTY 输入 Case 1 中文 prompt 时出现 bell 字符，输入被缩短，随后 CLI 结束。
- **触发方式**：以 PTY 启动 `python3.12 agent.py` 后自动写入中文 prompt。
- **原因**：V02 已记录的 macOS readline/libedit 自动化 PTY 中文输入限制再次复现。
- **解决方案**：真实 Demo 使用非 TTY 标准输入；不修改 Agent Loop、Permission 或 CLI 框架。
- **影响范围**：只影响 Demo 自动输入方式；真实物理键盘中文输入未在本次复验。
- **验证方式与结果**：失败日志为 `logs/75c6c4fe-9171-49db-81de-f659bbff8d7f.jsonl`；非 TTY 输入成功调用真实模型。
- **平台/环境**：macOS 自动化 PTY 已复现；Windows、Linux 未实际验证。
- **状态**：部分解决。

### 记录 4：沙箱内首次真实 API 调用无网络

- **现象**：首次非 TTY Case 1 在第一轮返回 `Connection error.`。
- **触发方式**：受限沙箱内使用根 `.env` 调用 Anthropic-compatible 服务。
- **原因**：执行环境默认限制网络，不是 Runtime 或服务协议故障。
- **解决方案**：获得授权后以相同配置重跑，没有修改生产代码。
- **影响范围**：无代码变化。
- **验证方式与结果**：失败日志为 `logs/1f8f197f-aa7a-47c1-b9ed-3fa4dcf61d08.jsonl`；授权后日志为 `logs/6ada293e-bba8-4d9e-bf5a-6a947b63ab6a.jsonl`。
- **平台/环境**：Codex macOS 受限沙箱已验证。
- **状态**：已解决。

### 记录 5：文件写入可通过 bash 绕过仅针对文件工具的 Ask

- **现象**：Ask-拒绝 Demo 中，真实模型没有调用 write_file，而是调用 bash，通过重定向创建 `permission_refused.txt`；当时非危险 bash 默认 Allow，文件实际产生。
- **触发方式**：要求创建文件并准备拒绝授权，模型选择 `mkdir` 加 Shell 重定向。
- **原因**：首版 `ASK_RULES` 只覆盖 write_file/edit_file，把未命中 Deny 的 bash 默认放行；同一种文件副作用因工具路径不同获得了不同权限结果。
- **解决方案**：当时先增加一条 bash Ask Rule阻断绕过。2026-09-03 又将其恒 True matcher 收紧为参数级副作用模式：Shell 重定向仍 Ask，而只读/验证命令恢复默认 Allow。Deny 集合仍先判断；Permission Pipeline 与 Tool Runtime 始终未修改。
- **影响范围**：bash 写入与其他副作用仍需确认，只读/验证命令无需确认；read/glob/grep 默认 Allow 不变，V02 不变。
- **验证方式与结果**：64 项自动测试通过；重跑拒绝 Demo 时模型先尝试 bash、再尝试 write_file，两次都被拒绝且文件不存在；最终固定场景使用 write_file 一次拒绝后结束。中间轨迹：`logs/b52bbdfe-89af-4f11-9c1e-6c68600584a0.jsonl`；最终日志：`logs/3ee88a4c-566d-437d-a8bc-5e5935bd1899.jsonl`。
- **平台/环境**：macOS、Python 3.12、DeepSeek-V4-Flash 已验证。
- **状态**：已解决。

### 记录 6：真实模型收到权限拒绝后继续无关探索

- **现象**：早期 Ask/Deny Demo 中，工具已被拒绝且用户要求不要替代操作，模型仍继续调用 bash、glob 和 read_file，甚至接近耗尽多轮。
- **触发方式**：真实模型收到 `PERMISSION_DENIED` Tool Result 后进入下一轮。
- **原因**：首版拒绝结果只包含抽象原因，且 SYSTEM 没有说明新增错误语义；模型未稳定地把拒绝视为当前任务的终止条件。
- **解决方案**：Permission Denied Tool Result 增加 `Operation: <describe>`，并在 SYSTEM 中要求：遇到拒绝时立即说明并结束，除非用户明确要求替代方案。没有改变权限判断、handler 或最大轮次。
- **影响范围**：只增强模型收到的拒绝上下文和行为提示；CLI/JSONL 的权限事实、Deny/Ask/Allow 判定及 V02 Tool Runtime 不变。
- **验证方式与结果**：64 项自动测试通过；最终 Ask 拒绝和 Deny Demo 都在第二轮 Final，无额外工具调用。成功日志分别为 `logs/3ee88a4c-566d-437d-a8bc-5e5935bd1899.jsonl` 和 `logs/78979396-6222-40fe-9d55-c49134556168.jsonl`。
- **平台/环境**：macOS、Python 3.12、DeepSeek-V4-Flash 已验证；其他模型未实际验证。
- **状态**：已解决。

### 记录 7：恒 True 的 bash Ask matcher 过度确认只读命令

- **现象**：所有非 Deny bash 调用都会询问，包括 `pwd`、`git diff` 和 pytest 等明显只读或验证型命令。
- **触发方式**：bash Ask Rule 使用 `matcher=lambda args: True`。
- **原因**：为快速堵住 Shell 写入绕过而采用的中间策略没有区分 command 参数的副作用。
- **解决方案**：仅替换 Deny/Ask Rule 的 matcher：Deny 收窄到少量系统级或灾难性模式，Ask 匹配普通文件、仓库、依赖环境修改及输出重定向，未命中时默认 Allow。Permission Pipeline、Rule 结构和 Tool Runtime 不变。
- **影响范围**：同一 bash 工具现在可按 command 参数得到 Deny、Ask 或 Allow；文件工具规则不变。
- **验证方式与结果**：新增参数化策略测试和执行路径测试；2026-09-03 V03 共 104 项通过。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 8：恒 True 的文件写入 matcher 过度确认普通工作区修改

- **现象**：write_file/edit_file 对所有路径都 Ask，普通源码和测试文件修改也需要逐次确认，不适合作为后续 Coding Agent baseline。
- **触发方式**：文件写入 Ask Rule 使用 `matcher=lambda args: True`。
- **原因**：规则只按工具名做粗粒度分类，没有利用已有的 `path` 参数区分越界、敏感和普通工作区目标。
- **解决方案**：保持 Rule 结构与 Pipeline 不变，增加两个路径 matcher：解析后位于工作区外的路径进入 Deny；工作区内 `.env`、credentials、secrets 等明确敏感路径进入 Ask；普通路径不命中规则而默认 Allow。
- **影响范围**：只调整 write_file/edit_file 的 Rule 内容；V02 handler 的路径、真实路径和符号链接越界检查完整保留，继续作为执行阶段安全底线。
- **验证方式与结果**：覆盖 write_file/edit_file 的相对越界、绝对路径、敏感路径、普通路径、符号链接越界、敏感写入拒绝不执行和普通写入无需确认；2026-09-03 V03 共 129 项测试通过。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 9：更新后的真实 Demo 被当前模型配置阻塞

- **现象**：尝试复验更新后的 Demo 时，模型调用在任何 Tool Use 之前失败。
- **触发方式**：使用根 `.env` 的 `DeepSeek-V4-Flash` 调用当前 Anthropic-compatible 服务；随后只在单次进程内尝试 `.env.example` 的小写 `deepseek-v4-flash`。
- **原因**：服务分别返回 400 `model not supported` 和 404 `model_not_found`；模型列表接口返回空。当前无法从服务获得可用模型 ID。
- **解决方案**：未修改 `.env`，未猜测其他模型，保留确定性 Fake Model/Runtime 验证，并将尚未复验的 Demo 明确标记为未通过真实模型验收。
- **影响范围**：无生产代码变化；只影响 2026-09-03 的真实 API Demo 复验。
- **验证方式与结果**：失败日志为 `logs/7958d2dd-41b1-4318-8ded-b42dcc10120a.jsonl` 和 `logs/4e892747-38e0-40ae-befa-28df3fc18745.jsonl`。
- **平台/环境**：当前 Anthropic-compatible 服务与 macOS、Python 3.12 已观察；其他服务未验证。
- **状态**：已解决。2026-09-03 最终收尾时同一模型标识恢复可用并完成真实 Demo；期间仍观察到一次连接错误和一次 429 并发限流，属于外部服务瞬时波动。

### 记录 10：只读 Shell 重定向被误判为 Deny 或 Ask

- **现象**：真实模型在只读目录检查中使用 `2>/dev/null` 和 `2>&1`；带分号的 `/dev/null` 被误判为高危 `/dev` 写入，文件描述符合并被误判为文件写入。
- **触发方式**：分别执行 `ls missing 2>/dev/null; echo "done"` 和 `cat README.md 2>&1; echo "done"`。
- **原因**：`/dev/null` 例外只接受空白或命令结尾，没有接受 Shell 分隔符；Ask 的输出重定向 matcher 没有排除 `>&<fd>`。
- **解决方案**：只调整现有 Bash Rule matcher 的显式正则：允许 `/dev/null` 后接 Shell 分隔符，并在判断文件重定向前移除文件描述符合并。不引入 Shell parser，不修改 Permission Pipeline。
- **影响范围**：常见只读错误输出丢弃和 stderr/stdout 合并恢复默认 Allow；真实文件重定向仍 Ask，其他 Deny/Ask 规则不变。
- **验证方式与结果**：新增两条参数化回归输入；V03 测试由 129 项增至 131 项并全部通过。修复后的真实 Demo 中，带 `2>&1` 的写文件命令仍因实际文件重定向正确进入 Ask。
- **平台/环境**：macOS、Python 3.12 已验证；Windows 和 Linux 未实际验证。
- **状态**：已解决。

### 记录 11：真实模型没有稳定遵循 Demo 指定的工具与停止条件

- **现象**：敏感写入 Demo 明确要求 write_file，模型仍选择 bash 重定向；灾难性命令被 Deny 后，模型又执行了一次只读 `pwd && ls -la`。
- **触发方式**：运行最终 Demo Case 2 和 Case 4。
- **原因**：TOOLS schema 提供能力，SYSTEM 和用户 prompt 只能引导模型选择，不能在 Runtime 层强制某个任务必须选用特定工具或在拒绝后立刻 Final。
- **解决方案**：不为 Demo 增加工具选择硬编码，也不修改 SYSTEM、Tool Runtime 或 Permission Pipeline；按可观察安全事实验收，并如实记录等价工具轨迹。细粒度的 write_file 路径分支继续由 Fake Model 测试确定性验证。
- **影响范围**：真实 Demo 的工具序列可能与提示不同；所有实际 Tool Use 仍经过同一 Permission Gate，拒绝的具体 handler 没有执行。
- **验证方式与结果**：Case 2 的 bash 写入分别 Ask 后批准/拒绝，拒绝目标不存在；Case 4 的危险 bash 被 Deny 且未执行，后续仅发生只读检查。日志分别为 `logs/bc8b0409-404b-49d5-99b2-79c2c4b3fa65.jsonl` 和 `logs/d0da7137-2508-4b6d-86bc-549bd636cf1b.jsonl`。
- **平台/环境**：macOS、Python 3.12、DeepSeek-V4-Flash 已观察；其他模型未实际验证。
- **状态**：延后处理。工具选择稳定性不是 V03 Permission Gate 的职责，且用户确认本轮不调整提示词。

## 已知限制（Known Limitations）

- bash matcher 是显式正则集合，不是 Shell parser，不能识别所有等价、组合或混淆写法，因此不构成完整 Shell 安全沙箱。
- Ask 决策只对当前单次 Tool Use 生效，不持久化“本 session 始终允许”等选择。
- Rule 写在源码中，不支持配置加载或运行时更新。
- 多个 Tool Use 按响应顺序逐个判定和确认，没有批量授权 UI。
- SYSTEM 和用户 prompt 不能保证真实模型选择某个指定工具或在 Permission Denied 后立即 Final；安全性依赖每个实际 Tool Use 都重新经过 Permission Gate，而不是依赖模型服从提示。
- 真实 API Demo 受外部模型可用性、并发限流和模型工具选择波动影响；确定性的权限边界仍以 Fake Model/Runtime 测试为准。
- 自动化 PTY 注入中文仍受既有 readline/libedit 行为影响。

这些限制符合 V03 边界；本版不引入 PermissionRule class、DSL、RBAC、用户角色、动态策略服务或复杂 Rule Engine。

## 为什么需要下一版本（Why Next Version）

权限边界建立后，Agent 仍只依赖 messages 隐式维持任务进度。复杂任务可能遗漏步骤、重复行动或过早结束，因此下一步才有动机研究显式 Plan/Todo。V03 完成后必须先停下等待确认，不自动实现 V04。
