# Memory Index

- name: explanation-order-preference
  description: 用户偏好先讲调用链和设计意图，再讲代码细节
  type: user

- name: pytest-command-convention
  description: 项目测试固定使用 python -m pytest -q
  type: project

- name: v08-memory-recall-architecture
  description: V08的Memory Recall在run_once中同步调用，先读取索引，经LLM选择或兜底，校验正文后渲染为PersistentMemory块，拼接进每次LLM请求的system提示。
  type: project
