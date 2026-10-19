# ADR 0001：幂等键用 `ContextVar` 传递，而不是作为工具参数

- 状态：已接受
- 日期：2026-10-19
- 相关：`backend/app/agent/tools.py`、`backend/app/agent/graph.py`

## 背景

写工具需要在「审批 → 执行 → 自动重试」的整个生命周期里共用一个幂等键，
否则「执行成功但响应超时 → 自动重试」会造成重复建卡。

键必须**跨节点稳定**：从 checkpoint 恢复后仍然是同一个值。因此键派生自
`approval_id = apr-{tool_call id}`——`tool_call id` 由模型生成并保存在 checkpoint 中，
恢复执行时不变。

问题在于：这个键怎么从 `run_tools` 节点传到工具内部？

## 决策

用 `ContextVar`（`_IDEMPOTENCY_KEY`）作为注入通道，而不是把键塞进工具参数。

## 理由

两条路都被框架堵死了：

1. **工具的 `args_schema` 全是 `extra='forbid'` 的 `StrictModel`**。
   这是刻意的设计——严格校验是本项目观察的重点之一，任何未声明字段都会被引擎拒绝，
   否则模型可以随意塞参数。
2. **`StructuredTool.arun` 会过滤多余 kwargs**。即使绕过 schema，
   LangChain 的工具调用层也会把未声明的 kwargs 丢掉。

所以「加一个隐藏参数」这条最直观的路走不通。`ContextVar` 是 asyncio 原生的
上下文传递机制，能在不改变工具签名、不放松 schema 的前提下把键送进去。

## 代价与替代方案

**代价**：

- 键的传递是隐式的，读代码时不容易发现（因此这条 ADR 存在）；
- 依赖调用发生在同一个异步上下文内——如果将来工具执行跑到别的线程/进程，
  `ContextVar` 不会跟着走，需要改成显式参数或消息传递。

**已否决的替代方案**：

- *把键写进 `config['configurable']`*：LangGraph 支持，但工具层拿不到 config，
  仍需节点中转，比 `ContextVar` 更绕。
- *在工具内部用「参数指纹」派生键*：参数相同就当作同一操作。听起来更优雅，
  但「用户就是要连续建两张一模一样的卡」是合法需求，会被误判成重复。
- *放松 schema 加可选字段*：等于为了传键牺牲参数严格性，得不偿失。

## 验证

`backend/tests/test_idempotency.py` 与 `backend/tests/test_tool_retry.py`
（含「成功但超时 → 重试 → 幂等查重」的端到端用例）。
