# 安全政策

## 支持范围

本项目是实验框架，没有正式的安全发布流程。但仍然欢迎报告安全问题——尤其是涉及
**数据外传**和**写操作绕过人工确认**这两类，它们是本项目设计上最在意的边界。

## 报告方式

**请不要开公开 issue。** 请通过 GitHub 的 "Report a vulnerability"（Security 标签页）私下提交。

请在报告中包含：复现步骤、影响范围、以及（如果涉及数据外传）数据离开本机的路径。

## 设计时已考虑的安全边界

| 边界 | 机制 | 位置 |
| --- | --- | --- |
| 云模型数据外传 | 非 localhost 的模型地址必须显式 `ALLOW_CLOUD_MODEL=true`，否则启动即报错 | `backend/app/config.py` |
| 写操作必须人工确认 | 所有 `WRITE_TOOLS` 在进入执行前经 LangGraph `interrupt` 挂起 | `backend/app/agent/graph.py` |
| 审批标识不匹配 | resume 时校验 `approval_id`，不一致直接拒绝执行 | `make_approve_node` |
| 重复执行造成重复写入 | 请求级幂等键（`apr-{tool_call id}`）+ 同事务查重 | `backend/app/agent/tools.py` |
| 工具结果污染下游 | 输出契约二次校验，不合规则丢弃并转 `OUTPUT_INVALID` | `OUTPUT_SCHEMAS` |
| 已完成卡片被误删 | 删除接口只允许 `status=done` | `backend/app/api/cards.py` |

## 已知限制（非漏洞，但请知悉）

- **checkpoint 会完整保存对话历史与工具调用参数**，落在 `backend/data/checkpoints.db`。
  该文件没有加密，也不会自动清理。使用云模型时，请假设这些内容已离开本机。
- **`sensitive_terms` 配置项尚未接线**。`config.py` 中定义了敏感词（密码、验证码、转账等），
  但目前没有任何环节会拦截命中这些词的请求。这是已知缺口，欢迎贡献。
- 无鉴权。API 默认监听本机，请勿直接暴露到公网。
- 数据库 schema 变更靠 `ALTER TABLE` 手工兼容，没有迁移工具。

## 你的数据

导入的账单、对话历史、长期记忆都保存在本地 `backend/data/`。
这些目录已在 `.gitignore` 中，**但已提交的历史无法靠 `.gitignore` 撤回**——
如果你在 fork 或 clone 后导入了真实数据，请不要把这些文件加入提交。
