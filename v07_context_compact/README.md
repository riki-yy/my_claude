# V07：Context Budget 与 Compact

V07 在 V06 已验收的 Agent Loop、Tool Runtime、Permission、Hooks、Todo、Observability、checkpoint/resume、Round/Attempt、错误分类和 Anthropic Tool Use/Result pairing 上增加请求前 Context Management。V06 目录保持不变；V07 不实现 Snip、Context Collapse、Session Memory、Prompt Cache/Cache Editing、priority scoring、Strategy Registry、通用 Context Manager 或 Artifact Store。

## 启动与测试

```bash
cd v07_context_compact
python3.12 -m pip install -r requirements.txt
python3.12 -m pytest -q
python3.12 agent.py
```

恢复方式仍为 `python3.12 agent.py --resume state/<session_id>.json`。

## 请求前流水线

每次普通 `client.messages.create()` 前按压力执行：

```text
Tool Result Budget
        ↓
Context Estimate
        ├─ < 16K  → normal request
        └─ >= 16K → MicroCompact（逐条处理，目标 <= 12.8K）
                         ↓
                    Re-estimate
                         ├─ < 16K  → normal request
                         └─ >= 16K → Full Compact → target < 8K → request
```

估算覆盖 `system + active messages + tool definitions` 的确定性 JSON 表示，使用偏保守的 `ceil(JSON characters / 3)`。V07 主动 Context Management 的 Full Compact trigger 为 16K estimated tokens，target watermark 为 8K；16K 不是 provider/model 的真实 context window 或 Runtime hard limit。模型输出沿用 V06 的 `max_tokens=8000`。估算并非服务端 tokenizer 的精确计数，因此真实 overflow 仍由 PTL Recovery 兜底。

## Tool Result Budget 与 MicroCompact

- 单个 raw Tool Result active payload 上限为 16,000 字符；当前 Tool batch 总上限为 32,000 字符。
- 缩减前，完整 UTF-8 内容以 flush/fsync、无覆盖原子落盘和回读确认写入 `tool_results/{session_id}/{encoded_tool_use_id}.txt`；`tool_use_id` 使用可逆 percent-encoding 形成安全文件名，不再使用 `run_id` 目录或 random UUID。active block 只保留有界首尾 preview、`original_chars` 和可读取 reference。旧 `artifacts/` 不迁移或删除，已有 checkpoint 中保存的绝对 reference 继续指向原文件；只有后续新持久化结果写入 `tool_results/`。
- 同一 session 中相同 `tool_use_id` 与相同内容会直接复用已有 artifact；若内容不同，则视为 ID 唯一性或 Runtime correctness violation 并明确失败，绝不覆盖旧文件。`tool_result` block 与 `tool_use_id` 不变；持久化或 checkpoint 失败会恢复原 active messages。
- MicroCompact 是 pressure-driven：Tool Result Budget 后的整体估算低于 16K 时不执行；达到 16K 才检查已经被后续模型交互消费的 Tool Results。当前 batch 和尚未消费的结果不参与处理。
- `MICROCOMPACT_KEEP_RECENT_RESULTS=3` 按单条已消费 Tool Result 计数，优先保留最近 3 条 active 原文。保护窗口之前的结果不再设 4K 最小长度，从最老到最新逐条处理。
- 每处理一条都重新估算；达到 `16K * 0.8 = 12.8K` 的 MicroCompact 目标即停止。若候选全部处理后仍达到 16K，才进入 Full Compact。
- 未持久化的候选先完整写入 artifact，再把 active `tool_result.content` 替换为 `[Earlier tool result saved at {path}]`。若 Tool Result Budget 已经为它生成并验证过 artifact，则复用原 path，不重复持久化；后续 preflight 也不会再次持久化已有 MicroCompact 占位符。
- 没有候选时 MicroCompact 是 no-op；不因 MicroCompact 重写 messages、写 artifact 或产生 checkpoint。所有处理路径都保持原 `tool_result` block、`tool_use_id` 和 pairing。

## Full Compact

低成本处理后估算达到 16K 时必须尝试调用内部 Summary LLM。切分点必须是完整 API Round/message boundary，切分前后均通过 V06 pairing validator。Full Compact 优先保留最近两个模型 Round；若结果无法低于 8K，则在合法 boundary 上依次改为保留一个 Round、再到只保留最小合法 current raw suffix。每次改变 boundary 都对扩大的 prefix 重新生成 Summary，recent raw 因而是滚动偏好而非永久保护区。

Summary 固定为九段：Primary Request and Intent、Key Technical Concepts、Files and Code Sections、Errors and Fixes、Problem Solving、All User Messages、Pending Tasks、Current Work、Optional Next Step。输入包含对应 boundary 之前的 history 和当前 durable Todo State。内部请求不携带 tools，也不递归 compact；独立的 Summary call safety boundary 仍要求输入估算低于 96K。响应具备全部九段、结果重新通过 pairing 校验且低于 8K watermark 后才整体替换并 checkpoint，所有合法候选均失败时保留先前 durable messages。若首次/current user input 自身达到 trigger 且没有可总结历史，则明确失败；V07 不对当前输入做 chunking 或 summarization。

## Prompt Too Long Recovery

HTTP 400/413 中的 `prompt_too_long`、`context_length_exceeded`、`maximum_context_length` 和 `too_many_tokens` 归一为 `PROMPT_TOO_LONG`，不进入 V06 exponential retry。同一逻辑 Round 最多执行一次 forced Full Compact、重新估算和 retry；compact 失败、重新估算不安全或 retry 再次 PTL 时明确终止，不增加 Round，也不形成循环。

## Persistence 与 Observability

V07 沿用 state schema 1，不增加 StateManager。active messages 仍是唯一 durable conversation state；成功改变后通过 V06 checkpoint 原子保存，resume 恢复 summary、recent raw messages 和 Tool Result references。

JSONL/CLI 新增 `context.budget`、`context.summary.started/completed`、`context.ptl_recovery` 和 `context.failed`。事件只保存估算、阈值、机制、计数、字符规模、状态和安全 reference，不保存完整 Tool Result、transcript、summary prompt 或 summary 内容。

## 自动测试与真实 API 验收

Fake Model 测试覆盖估算、no-op、单结果/batch budget、artifact/reference、确定性文件名、同内容复用与不同内容冲突、旧 `artifacts/` 绝对 reference 兼容、MicroCompact 压力门槛、recent 3 保护、oldest → newest 顺序、0.8 停止目标、短结果、重复 preflight，以及合法 Full Compact、九段校验、失败回滚、pairing、resume、一次 PTL recovery 与持续 PTL 终止。当前实际结果：V07 全部测试 `250 passed`（其中 Context Compact 专项 `30 passed`）；V06 原目录 regression tests `220 passed`。

### Development / Debugging Notes

真实 API Demo 使用 `read_file("agent.py")` 返回 2,374 行、约 96.3 KB 内容。首次运行错误触发 `CONTEXT_COMPACT_FAILED`。根因是 `_compact_result_block()` 原先通过 `"[Full Tool Result:" in raw` 这种正文子字符串判断识别 payload 是否已经缩减；由于 `agent.py` 源码本身恰好包含该 marker 字面量，原始 Tool Result 被误判为“已经 compact”，从而跳过 artifact 持久化和 active preview/reference 替换。最小修复是删除该冗余 marker 判断，保留原有类型与长度检查；重新验证后，完整 `read_file` 输出已写入约 98.6 KB 的 artifact，active Tool Result 缩减为 bounded preview/reference，Agent 可继续运行。

后续真实 API Demo 在 Full Compact Summary call 报错 `Object of type TextBlock is not JSON serializable`，并终止为 `CONTEXT_COMPACT_FAILED`。根因是正常 Agent 请求保留了 Anthropic SDK 的 `TextBlock` / `ToolUseBlock` 对象，而旧实现又对 summary prefix 执行 `json.dumps(old)`。修复后 Summary internal call 直接复用经过合法 boundary 与 pairing 校验的 Messages representation，在历史末尾追加 summary instruction，不再把历史 JSON 序列化为字符串；boundary、recent suffix、九段 schema、阈值与事务语义均未改变。包含真实 SDK block 对象的回归测试已加入；当前 Context Compact 专项 `30 passed`、V07 全量 `250 passed`、V06 regression `220 passed`。

另一次真实 API Demo 中，Full Compact 已成功执行 `context.summary.started` 与 `context.summary.completed`，但所有候选最终均未达到严格的 `<8K` watermark，因而报错 `Full Compact did not reach target watermark at any legal boundary` 并保持原 durable state。最小 legal suffix 对应的最终 estimate 为约 8,245 tokens，仅比 target 高约 245 tokens。当前 Summary call 使用固定 `SUMMARY_MAX_TOKENS=4_000`，尚未依据 `target - fixed request overhead - raw suffix - safety margin` 为每个 boundary 动态计算并同时约束 Summary prompt 与 `max_tokens`，因此 target 命中能力仍较粗。

仍需人工真实 API 验收：小任务不调用 Summary；长 session compact 后继续 Todo/约束/验证命令并可 resume；若服务端可稳定构造 PTL，再验证同 Round 一次 recovery，否则只以 Fake Model 作为机制证据。

## Known Limitations

- 字符估算是确定性的工程近似，不是模型 tokenizer；16K trigger 是主动管理水位，不代表真实服务上限。
- `bash` 仍继承 V06 的 `TOOL_OUTPUT_LIMIT=12_000`：较大的原始 shell 输出会在进入 V07 Context Management 之前被 Tool Runtime 截断，因此 V07 artifact 只能持久化已经截断的 `bash` payload。该限制不适用于 `read_file`；`read_file` 会把完整内容交给 Tool Result Budget 后再持久化和缩减 active payload。
- MicroCompact 的 recent 3 是按已消费 Tool Result 条数计算的固定保护窗口，不做内容语义或 Current Work 依赖判断。Full Compact 的最近两个模型 Round 只是首选，必要时会减少；Summary 质量仍取决于模型忠实遵循固定 schema。
- artifact 没有垃圾回收；checkpoint 失败可能留下没有 active reference 的文件，但不会留下失效 durable reference。
- Summary 输入超限、所有合法候选均无法达到 target，或 oversized current input 没有历史 boundary 时明确失败，不做递归 compact、Snip、input chunking 或消息级截断。
- Summary 输出预算当前固定为 4K，没有按每个 legal boundary 的剩余 target 空间动态收紧；因此即使 Summary 调用成功，结果也可能仅小幅超过 8K watermark 而导致整个 Full Compact 事务失败。动态 Summary budget 是后续可优化方向，V07 初版尚未实现。
