---
name: v08-memory-recall-architecture
description: "V08的Memory Recall在run_once中同步调用，先读取索引，经LLM选择或兜底，校验正文后渲染为PersistentMemory块，拼接进每次LLM请求的system提示。"
type: project
---

V08的Memory Recall调用链：每次新用户请求在run_once()内同步调用MemoryPipeline.recall()。流程为：1) MemoryStore.read_index()读取索引（MEMORY.md目录）；2) 若仓库非空，优先用LLM(SELECTOR_SYSTEM提示)选择0-5个相关记忆ID，失败时降级为词重叠打分兜底（_fallback_ids），最多取5条；3) 对选中的ID逐条store.read()读取正文文件，并校验正文内嵌的id/summary/type与索引条目一致，不一致则丢弃该条；4) 将有效记忆渲染为以标签包裹的PersistentMemory上下文块，格式为'[类型:ID] 描述 正文'，作为RecallResult.system_context返回；5) run_once将其与基础SYSTEM提示拼接为request_system，随后通过agent_loop在每次LLM请求中作为system参数注入。写入侧由extract_and_store在每次Run完成后增量分析新消息（用last_memory_message_index游标记录进度），并低频执行consolidate进行去重、合并和裁剪。
