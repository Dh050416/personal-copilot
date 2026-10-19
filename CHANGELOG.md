# 更新日志

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-10-19

首个对外可运行版本。在此之前项目为个人实验状态，未做版本记录。

### 新增

- **Supervisor 多 Agent 路由**：`intent` 节点按关键词注入流程 Skill 并判定领域，
  分发到账单 / 日程 / 搜索三个子图，通用 agent 兜底
- **领域子图状态隔离**：主图与子图使用各自的 state schema，靠 LangGraph channel 名映射实现双向隔离
- **执行中追加输入**（Codex 风格）：thread 忙时消息进入队列，在下一个输入检查点注入并重新路由
- **提示词分层**：铁律常驻 + 6 个流程 Skill 按需注入
- **运行时治理**：工具超时、run 墙钟上限、工具调用硬上限、模型/工具指数退避重试、审批 TTL
- **上下文卫生**：工具结果字符/行数双阈值裁剪、只读工具结果缓存、分页
- **请求级幂等**：幂等键贯穿审批→执行→重试全生命周期；批量建卡用 `idempotency_records` 同事务查重
- **输出契约校验**：`OUTPUT_SCHEMAS` 在统一出口二次校验 artifact，脏数据转 `OUTPUT_INVALID`
- **结构化错误**：统一回执带 `code` / `stage` / `retryable`，区分"可重试"与"重试也没用"
- 工具集：账单查询、单卡/批量建卡、完成卡片、看板列表、日程增删改查、联网搜索、长期记忆
- 前端：对话、看板、账单、日程日历四个可用页面，SSE 流式与审批确认卡

### 修复

- `web_search` 失败时抛出未定义的 `NameError`（`ToolError` 未导入），
  导致错误被降级为 `TOOL_FAILED` 且失去重试能力；现正确抛出 `SEARCH_UNAVAILABLE`
- `query_bill` 的摘要中明细段落被拼接两次，浪费 token 并干扰模型定位

### 变更

- 移除零引用依赖 `mcp` / `apscheduler` / `loguru`
- 前端 API 基址改为可通过 `VITE_API_BASE` 配置，不再硬编码
- `docs/hybrid-routing.md` 移入 `docs/archive/`——它描述的是已被替换的四层 HybridPlanner 架构

### 已知限制

- 消费报表页与设置/记忆管理页仍是占位
- `sensitive_terms` 已定义但未接线
- checkpoint 数据库无清理策略，会随使用增长
- 数据库无迁移工具，schema 变更靠 `ALTER TABLE` 手工兼容
