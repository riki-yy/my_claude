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

每次普通 `client.messages.create()` 前固定执行：

```text
Tool Result Budget → MicroCompact → Token Budget
                                  ├─ below threshold → normal request
                                  └─ at/above threshold → Full Compact → re-estimate
```

估算覆盖 `system + active messages + tool definitions` 的确定性 JSON 表示，使用偏保守的 `ceil(JSON characters / 3)`。V07 主动 Context Management 的 Full Compact trigger 为 16K estimated tokens，target watermark 为 8K；16K 不是 provider/model 的真实 context window 或 Runtime hard limit。模型输出沿用 V06 的 `max_tokens=8000`。估算并非服务端 tokenizer 的精确计数，因此真实 overflow 仍由 PTL Recovery 兜底。

## Tool Result Budget 与 MicroCompact

- 单个 raw Tool Result active payload 上限为 16,000 字符；当前 Tool batch 总上限为 32,000 字符。
- 缩减前，完整 UTF-8 内容以 flush/fsync、atomic replace 和回读确认写入 `artifacts/<session>/<run>/`；active block 只保留有界首尾 preview、`original_chars` 和可读取 reference。
- `tool_result` block 与 `tool_use_id` 不变。持久化或 checkpoint 失败会恢复原 active messages；已写但未被 checkpoint 引用的 artifact 是无害 orphan file。
- MicroCompact eligibility 为：结果大于 4,000 字符、已位于后续模型交互之前，并且不属于最近两个 completed Tool batches。最近两个 batch 作为 recent/current-work raw 边界。
- 无 eligible result 时不写 artifact、不改 messages、不产生 context checkpoint。

## Full Compact

低成本处理后估算达到 16K 时必须尝试调用内部 Summary LLM。切分点必须是完整 API Round/message boundary，切分前后均通过 V06 pairing validator。Full Compact 优先保留最近两个模型 Round；若结果无法低于 8K，则在合法 boundary 上依次改为保留一个 Round、再到只保留最小合法 current raw suffix。每次改变 boundary 都对扩大的 prefix 重新生成 Summary，recent raw 因而是滚动偏好而非永久保护区。

Summary 固定为九段：Primary Request and Intent、Key Technical Concepts、Files and Code Sections、Errors and Fixes、Problem Solving、All User Messages、Pending Tasks、Current Work、Optional Next Step。输入包含对应 boundary 之前的 history 和当前 durable Todo State。内部请求不携带 tools，也不递归 compact；独立的 Summary call safety boundary 仍要求输入估算低于 96K。响应具备全部九段、结果重新通过 pairing 校验且低于 8K watermark 后才整体替换并 checkpoint，所有合法候选均失败时保留先前 durable messages。若首次/current user input 自身达到 trigger 且没有可总结历史，则明确失败；V07 不对当前输入做 chunking 或 summarization。

## Prompt Too Long Recovery

HTTP 400/413 中的 `prompt_too_long`、`context_length_exceeded`、`maximum_context_length` 和 `too_many_tokens` 归一为 `PROMPT_TOO_LONG`，不进入 V06 exponential retry。同一逻辑 Round 最多执行一次 forced Full Compact、重新估算和 retry；compact 失败、重新估算不安全或 retry 再次 PTL 时明确终止，不增加 Round，也不形成循环。

## Persistence 与 Observability

V07 沿用 state schema 1，不增加 StateManager。active messages 仍是唯一 durable conversation state；成功改变后通过 V06 checkpoint 原子保存，resume 恢复 summary、recent raw messages 和 Tool Result references。

JSONL/CLI 新增 `context.budget`、`context.summary.started/completed`、`context.ptl_recovery` 和 `context.failed`。事件只保存估算、阈值、机制、计数、字符规模、状态和安全 reference，不保存完整 Tool Result、transcript、summary prompt 或 summary 内容。

## 自动测试与真实 API 验收

Fake Model 测试覆盖估算、no-op、单结果/batch budget、artifact/reference、MicroCompact recent boundary、合法 Full Compact、九段校验、失败回滚、pairing、resume、一次 PTL recovery 与持续 PTL 终止。V07 目录保留 V06 regression tests；还应单独运行 V06 原目录测试，确认已验收版本未变化。

真实 API Demo 使用 read_file("agent.py") 返回 2,374 行、约 96.3 KB 内容。首次运行错误触发 CONTEXT_COMPACT_FAILED。根因是 _compact_result_block() 通过 "[Full Tool Result:" in raw 这种正文子字符串判断识别 payload 是否已经被缩减；由于 agent.py 源码本身恰好包含该 marker 字面量，原始 Tool Result 被误判为“已经 compact”，从而跳过 artifact 持久化和 active preview/reference 替换。最小修复是删除该冗余 marker 判断，保留原有类型与长度检查；重新验证后，完整 read_file 输出已写入约 98.6 KB 的 artifact，active Tool Result 缩减为 bounded preview/reference，Agent 可继续运行。

仍需人工真实 API 验收：小任务不调用 Summary；长 session compact 后继续 Todo/约束/验证命令并可 resume；若服务端可稳定构造 PTL，再验证同 Round 一次 recovery，否则只以 Fake Model 作为机制证据。

## Known Limitations

- 字符估算是确定性的工程近似，不是模型 tokenizer；16K trigger 是主动管理水位，不代表真实服务上限。
- `bash` 仍继承 V06 的 `TOOL_OUTPUT_LIMIT=12_000`：较大的原始 shell 输出会在进入 V07 Context Management 之前被 Tool Runtime 截断，因此 V07 artifact 只能持久化已经截断的 `bash` payload。该限制不适用于 `read_file`；`read_file` 会把完整内容交给 Tool Result Budget 后再持久化和缩减 active payload。
- 最近两个 Tool batches 是 MicroCompact 的 raw 边界；Full Compact 的最近两个模型 Round 只是首选，必要时会减少。两者都不理解语义依赖，Summary 质量仍取决于模型忠实遵循固定 schema。
- artifact 没有垃圾回收；checkpoint 失败可能留下没有 active reference 的文件，但不会留下失效 durable reference。
- Summary 输入超限、所有合法候选均无法达到 target，或 oversized current input 没有历史 boundary 时明确失败，不做递归 compact、Snip、input chunking 或消息级截断。
