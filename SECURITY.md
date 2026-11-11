# 安全政策

## 支持范围

本项目是实验框架，没有正式的安全发布流程。但仍然欢迎报告安全问题——尤其是
**数据外传**、**写操作绕过人工确认**和**跨用户数据隔离失效**这三类，它们是本项目设计上最在意的边界。

## 报告方式

**请不要开公开 issue。** 请通过 GitHub 的 "Report a vulnerability"（Security 标签页）私下提交。

请在报告中包含：复现步骤、影响范围、以及（如果涉及数据外传）数据离开本机的路径。
如果能附上响应头 `X-Request-ID`（或页面错误提示里的「问题编号」），维护者可以直接
用这一个 id 捞出该次请求的完整结构化日志与 Langfuse trace，定位会快很多。

## 设计时已考虑的安全边界

| 边界 | 机制 | 位置 |
| --- | --- | --- |
| 云模型数据外传 | 非 localhost 的模型地址必须显式 `ALLOW_CLOUD_MODEL=true`，否则启动即报错 | `backend/app/config.py` |
| 写操作必须人工确认 | 所有 `WRITE_TOOLS` 在进入执行前经 LangGraph `interrupt` 挂起 | `backend/app/agent/graph.py` |
| 审批标识不匹配 | resume 时校验 `approval_id`，不一致直接拒绝执行 | `make_approve_node` |
| 审批被跨用户伪造 | resume 校验 `run_id` 必须带 `u{user_id}:` 前缀 | `backend/app/api/chat.py` |
| 重复执行造成重复写入 | 请求级幂等键（`apr-{tool_call id}`）+ 同事务查重 | `backend/app/agent/tools.py` |
| 工具结果污染下游 | 输出契约二次校验，不合规则丢弃并转 `OUTPUT_INVALID` | `OUTPUT_SCHEMAS` |
| 已完成卡片被误删 | 删除接口只允许 `status=done` | `backend/app/api/cards.py` |
| 跨用户数据访问 | 账号密码登录 + HMAC 签名令牌；`user_id` 贯穿业务表、checkpoint `thread_id` 前缀、知识库文档与工具层 `ContextVar` | `backend/app/auth.py`、`api/chat.py` |
| 敏感词请求 | `sensitive_terms` 在 intent 节点命中即走拒答路径，不注入业务 Skill、不调工具 | `backend/app/agent/graph.py` |
| 内部信息泄露 | 全局异常兜底只返回通用提示 + `request_id`，完整堆栈只进日志 | `backend/app/main.py`、`app/middleware.py` |
| 未捕获异常导致进程崩溃 | 三个全局异常处理器 + 流式响应内部 catch（SSE `error` 事件） | `backend/app/main.py`、`api/chat.py` |
| 日志注入 | 传入的 `X-Request-ID` 严格限制为 `[A-Za-z0-9._-]{1,64}`，非法则重新生成 | `backend/app/middleware.py` |

## 已知限制（非漏洞，但请知悉）

- **checkpoint 会完整保存对话历史与工具调用参数**，落在 PostgreSQL 的 4 张 checkpoint 表里，
  未加密、没有 TTL / 自动清理策略。滑动窗口只裁剪喂给模型的内容，**不删库里的历史**。
  使用云模型时，请假设这些内容已离开本机。
- **没有按轮次回溯的消息表**：被窗口挤出的原始消息只剩递进摘要，展示用的完整历史只在前端
  localStorage（换浏览器即丢失）。
- **日志与可观测性**：结构化 JSON 日志包含 `path` / `status` / `request_id` 与异常堆栈，
  不主动记录消息正文，但异常文本可能携带工具参数，请注意日志文件的访问权限。
  配置 `LANGFUSE_*` 凭证后，trace（含模型输入输出）会发往 `LANGFUSE_HOST`；
  不想外传请留空，追踪会整体降级为零网络请求。
- **知识库**：原始文件不落盘，但解析后的文本块与向量存 PG；删除是级联真删，**没有回收站**。
- **云模型**：对话与工具数据会发送到模型服务商，必须显式 `ALLOW_CLOUD_MODEL=true` 才可用。

## 你的数据

账单、卡片、日程、长期记忆、对话摘要、知识库文本块与向量、LangGraph checkpoint
都存在 **PostgreSQL**（本地 Docker 的 `postgres` 容器或生产 PG 实例）；Redis 只放可失效的
工具结果缓存；`backend/data/` 现在主要放 `auth_secret` 等少量本地状态。

`.env` 与 `backend/data/` 已在 `.gitignore` 中，**但已提交的历史无法靠 `.gitignore` 撤回**——
如果你在 fork 或 clone 后导入了真实数据，请不要把这些文件加入提交。部署时请另外做好
PG 数据卷的备份（见 [`DEPLOY.md`](DEPLOY.md)）。
