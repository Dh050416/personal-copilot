# 更新日志

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **日历手动录入与 Agent/HITL 解耦**：`api/events.py` 新增 `POST /api/events`、
  `PATCH /api/events/{id}`、`POST /api/events/{id}/complete` 三个直连端点，复用
  `schedule_service` 与现有 `CreateEventArgs` / `UpdateEventArgs` 契约，`ToolError` 转 REST 404；
  `CalendarView.vue` 删除「把表单拼成中文指令发 /api/chat」与整套审批状态机，改直调 REST。
  手填参数无需模型推断，因此零模型调用、零延迟、不会被模型改写，也不占用会话运行锁；
  对话区自然语言创建日程仍走 `WRITE_TOOLS` + HITL。直连写操作补带 rid 的结构化日志留痕。
  新增 `backend/tests/test_events_api.py`（9 条，含真实 app 端到端与跨用户隔离）。
- **稳定运行 + 可观测性（A + C 的一半）**：纯 ASGI 中间件生成 `request_id`（可透传网关的
  `X-Request-ID`），写入 ContextVar / `request.state` / 响应头；结构化单行 JSON 日志；
  `HTTPException` / `RequestValidationError` / `Exception` 三个全局异常处理器统一返回带 rid 的
  JSON、内部堆栈只进日志；SSE `error` 事件与前端错误提示都带问题编号。
  Langfuse 改为**每次请求新建** `CallbackHandler(trace_context={"trace_id": request_id})`，
  并用 `langfuse_session_id = thread_id` 归组同会话多轮。凭证留空整体降级、零网络请求。
  见 [ADR 0005](docs/adr/0005-request_id%20与%20Langfuse%20trace%20关联.md)，
  测试见 `backend/tests/test_observability.py`。
- **RAG 个人知识库三层闭环**：PG + pgvector（`Vector(1024)` / HNSW / 内容哈希去重 /
  级联删除）→ 多格式解析（TXT/MD/Word/文字型 PDF）与结构感知分块（保留章节路径、页码）→
  本地 `bge-m3` 向量化异步摄入（pending → ready / failed）→ `search_knowledge` 工具向量召回
  → 片段回喂模型。前端新增「知识库」页（上传 / 粘贴 / 状态轮询 / 失败原因 / 删除）。
  真实摄入需 `ollama pull bge-m3`；Excel 与 OCR 属二期。
- **短期记忆：滑动窗口 + 递进摘要**：续轮保留最近 `HISTORY_WINDOW_SIZE=10` 条原始消息，
  被挤出的普通文本消息增量合并进 `conversation_summaries` 并注入回 System；
  工具调用 / 审批等结构化消息不参与摘要。长期记忆仍全量注入，并补齐「设置与记忆」管理页。
- **存储层换成 PostgreSQL**：业务库、LangGraph checkpoint（`AsyncPostgresSaver`）、
  知识库向量统一进 PG 16 + pgvector；引入 Alembic 管理 schema（含 `timestamptz` 时间戳迁移），
  彻底移除 SQLite。工具结果缓存可选接 Redis，未配置时回退进程内缓存。
- **账号体系与多用户隔离**：注册 / 登录 + HMAC 签名令牌（密钥可落盘持久化），
  `user_id` 贯穿业务表、checkpoint `thread_id` 前缀、知识库文档与工具层 ContextVar。
- **前端**：仿极简黑白风格重构（220px 分组侧栏、无阴影渐变）；补齐消费报表页与设置/记忆页；
  新增知识库页与登录页。
- **敏感词拦截接线**：`sensitive_terms` 在 intent 节点命中时走拒答路径，不再只是配置项。
- **评估集扩到 43 条**；评估改用 PG 测试库（`PersonManager_test`）做用例隔离，不再用临时 SQLite 库。
- **文档同步（2026-11-11）**：README / DEPLOY / CONTRIBUTING / SECURITY / `.env.example` /
  `.env.production` / docs 全量对齐当前代码，修正过时的「无鉴权 / SQLite / 无迁移工具 /
  占位页 / 敏感词未接线」等描述。

- **评估集与指标报告**：33 条用例（8 类）+ 六项指标 + Markdown 报告。
  首轮实测 `deepseek-v4-flash`：通过率 93.9%、工具选择 94.4%、参数 89.6%、
  审批触达 100%、越权写操作 0。见 [docs/evals.md](docs/evals.md)。
- 铁律新增第 6 条：写操作必须单独提交（一条响应里只放一个写操作调用），
  批量建卡用 `create_cards_from_query` 一次完成。修复了模型把「查询 + 建卡」
  并行提交、被 `PARALLEL_WRITE_BLOCKED` 拦下后不重试的问题。

### 变更

- 0.1.0 列出的若干已知限制现已解决：报表与记忆管理页已实现、`sensitive_terms` 已接线、
  schema 变更走 Alembic、API 已有账号鉴权、checkpoint 落在 PG 而非 `checkpoints.db`。
- `LOG_LEVEL` 控制结构化日志级别；本地启动改为 `python run.py`（Windows 事件循环兼容），
  vite 需 `--host 127.0.0.1`。

### 修复

- **HITL 可被并行工具调用绕过（安全）**：路由只按 `tool_calls[0]` 判定是否审批，
  而 `run_tools` 遍历全部调用，导致 `[query_bill, create_tracking_card]` 这类混合批
  中的写操作未经用户确认直接落库。现在确立不变量「并行调用只能全只读或仅含一个写工具」，
  混合批只执行第一个调用，其余以 `PARALLEL_WRITE_BLOCKED` 回执退回模型逐个重发。
  见 [ADR 0004](docs/adr/0004-并行工具调用按批校验.md)。
- **跨域预检被鉴权拦成 401**：`AuthMiddleware` 位于 `CORSMiddleware` 外层，
  OPTIONS 请求不带令牌时直接返回 401 且无 CORS 头，浏览器拒绝全部跨域请求。
  现在 OPTIONS 直接放行给内层，前端 API 基址默认同源（`/api` 由 vite 代理），
  从源头消除预检失败。
- **Langfuse 单例 handler 导致并发 trace 串台**：改为每次请求新建 handler 并绑定
  `trace_id = request_id`（见 ADR 0005）。

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
