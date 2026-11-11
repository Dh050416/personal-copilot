# PersonalCopilot

[![CI](https://github.com/Dh050416/personal-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Dh050416/personal-copilot/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

这是一个用真实账单场景学习 **Agent 工程问题**的实验框架。业务功能只做最小闭环，重点观察：

- 模型或规划器如何选择工具；
- 参数如何经过 JSON Schema 与 Pydantic 严格校验；
- 读工具如何执行，写工具如何进入人在环；
- LangGraph 如何暂停、持久化、恢复；
- 工具结果如何经过输出 schema 校验；
- 幂等键如何防止重复确认造成重复写入；
- Excel 导入、数据库事务、SSE 事件和运行审计怎样互相配合；
- 上下文如何在滑动窗口 + 递进摘要、长期记忆、知识库检索之间分配；
- 一次请求如何通过 `request_id` 把结构化日志与 Langfuse trace 串成一条线。

> **它不是什么**：不是一个账本产品，没有完整的预算规划、多账本、同步或移动端。
> 如果你想要一个能用的记账应用，这里不合适；如果你想知道「一个会写数据库的 Agent
> 到底要加多少道护栏才算负责任」，这里就是为这个建的。

---

## 架构：Supervisor 多 Agent

```
用户消息
  └─> intent 节点
        ├─ 关键词命中 → 注入对应流程 Skill（按需加载，不常驻）
        ├─ 注入长期记忆（agent_memory 全量）
        └─ 判定领域 → bill / schedule / search / general
              ├─ 账单子图 ┐
              ├─ 日程子图 ├─ 各自 agent → approve / tools 循环
              ├─ 搜索子图 ┘
              └─ 通用 agent 兜底
                    └─> check_input 检查点
                          ├─ 有追加输入 → 回 intent 重新路由
                          └─ 无输入 → END
```

几个关键取舍（详见 [`docs/adr/`](docs/adr/)）：

- **提示词分层**：`CORE_RULES` 铁律常驻，6 个 `PROCESS_SKILLS` 按意图命中才注入，
  避免细节规则常驻稀释模型注意力。
- **子图状态隔离**：主图 `AgentState` 与子图 `SubGraphState` 分离，靠 LangGraph 1.x
  按 channel 名自动映射实现双向隔离——主图调度字段不进子图，子图私有中间结果不写回主图。
- **两类 interrupt 分工**：`tool_approval` 管写工具审批，`input_checkpoint` 管轮与轮之间
  让出输入机会（Codex 风格，执行中可追加任务）。子图内**不设**输入检查点，只在 Supervisor 层，
  以减少中断次数；子图内的审批 interrupt 会全局冒泡到顶层 runner。
- **执行中追加输入**：thread 忙时新消息进入队列（返回 `{queued:true}`），
  runner 在下一个检查点注入并**回 intent 重新路由**（可能切换领域），
  但不重跑已完成步骤——checkpoint 保证历史在。

### 目录结构

```
backend/app/
  agent/     graph.py   Supervisor 图、领域子图、节点工厂、上下文卫生
             tools.py   工具层：契约、权限、幂等、输出校验（含 search_knowledge）
  api/       chat.py    SSE 事件、审批恢复、运行审计
             bills.py / cards.py / events.py / memory.py / reports.py
             knowledge.py   知识库上传/粘贴/列表/详情/删除
  data/      db.py / models.py / views.py（models 含 KnowledgeChunk / ConversationSummary）
  services/  importer.py（Excel 解析，确定性，不调 LLM）
             classification.py（规则 + 真实 LLM 分类）
             summary.py（递进摘要）  bill_service / card_service / schedule_service
  services/knowledge/  parsers（TXT/MD/Word/PDF）、chunking（结构感知分块）、
                       embedding（本地 bge-m3）、ingest（摄入状态机）、retrieval（向量召回）
  config.py  配置与隐私边界校验    schemas.py  输入输出契约
  errors.py  结构化错误             main.py     应用装配与全局异常兜底
  auth.py    账号鉴权中间件         middleware.py  request_id 生成与访问日志
  logging_config.py  结构化 JSON 日志   observability.py  Langfuse trace 关联

frontend/src/
  views/       ChatView / BoardView / BillsView / CalendarView
               ReportsView / SettingsView / KnowledgeView / LoginView
  components/  AppHeader（220px 侧栏）/ ChatPanel / TaskBoard / BillTable / ApprovalCard
               SessionList / ImportPanel
  stores/      session.ts   事件流统一分发
  services/    agentClient.ts   SSE 解析与 API（含知识库接口）
  tools/       registry.ts      工具结果按注册表提取，新增工具不改主流程

backend/alembic/  迁移版本（PG + pgvector + 知识库表 + 摘要表）
scripts/     gen_sample_bills.py（生成脱敏示例账单）、E2E 与基准脚本
docs/        adr/（架构决策记录）、evals.md、tool-closure.md、archive/（已废弃旧架构）
```

---

## 快速开始

### 前置条件

- **Python 3.12+**（代码用了 `asyncio.timeout`，最低 3.11）
- **Node 20+**
- **PostgreSQL 16 + pgvector 扩展**（业务库、checkpoint、知识库向量都在同一个 PG 实例）。
  本机用 Docker 最简单：`docker run -d --name postgres -e POSTGRES_PASSWORD=123456 \
  -p 5432:5432 pgvector/pgvector:pg16`，再建 `PersonManager` 与 `PersonManager_test` 两个库。
- **Redis（可选）**：不配 `REDIS_URL` 时工具结果缓存退化成进程内内存缓存，单实例够用。
- 一个 OpenAI 兼容的**对话模型**服务（默认本地 Ollama），以及一个 **embedding 模型**
  （知识库用，默认本地 `bge-m3`，1024 维）：

```bash
ollama pull qwen3:8b      # 对话模型，也可在 .env 改指向 DeepSeek
ollama pull bge-m3        # 知识库向量化，真实文档摄入的必需前置
```

### 启动

```bash
# 后端
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 按需修改模型、PG/Redis、Langfuse 配置
# 确保 PG/Redis 容器已启动，且连接串写 127.0.0.1（Windows 下 localhost 会解析到 IPv6）
# 首次运行需先建表（Alembic 会一并建 pgvector 扩展与 HNSW 索引）：
alembic upgrade head
# 启动（Windows 本地开发用 run.py，它会规避 psycopg 的 ProactorEventLoop 限制；
#       生产 Docker/Linux 直接 uvicorn app.main:app 即可）
python run.py

# 前端（另一个终端；vite 默认绑 IPv6，需显式指定 host）
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

打开 http://127.0.0.1:5173 ，先注册账号再登录（数据按 `user_id` 隔离）。
后端健康检查 http://127.0.0.1:8000/health 。

也可以直接用仓库自带的示例账单（无需准备自己的数据）：

```bash
python scripts/gen_sample_bills.py     # 生成 samples/sample_bills.xlsx
```

然后在前端「导入」上传它。数据全部虚构，商户与姓名均为占位名称。

### 关于模型与隐私

默认配置连本地 Ollama，账单数据不出本机。

**使用云模型必须显式设置 `ALLOW_CLOUD_MODEL=true`**，否则启动时直接报错——
因为对话内容和工具数据会离开本机。这不是默认值，是有意的摩擦点：
它逼你在配置时意识到这件事一次。

---

## 运行时治理

以下护栏全部接线生效（配置在 `backend/.env`）：

| 配置 | 默认 | 行为 |
| --- | --- | --- |
| `TOOL_TIMEOUT_SECONDS` | 15 | 单次工具执行超时 → `TOOL_TIMEOUT` 结构化错误（retryable）回喂模型 |
| `RUN_TIMEOUT_SECONDS` | 120 | 单次 run 墙钟上限 → 推友好 final 安全停止，已完成步骤已 checkpoint 落盘 |
| `MAX_TOOL_CALLS_PER_RUN` | 20 | 每次任务工具调用硬上限（成本护栏），跨审批续跑累计不重置 |
| `MODEL_RETRIES` | 2 | 模型调用失败重试次数，指数退避（`RETRY_BACKOFF_SECONDS * 2^attempt`） |
| `RETRY_BACKOFF_SECONDS` | 0.5 | 重试基础退避秒数 |
| `TOOL_RETRIES` | 2 | 工具执行失败自动重试次数（指数退避）；只重试瞬时错误（超时/显式 retryable），参数校验与业务失败不重试；重试复用同一 tool call id → 幂等键不变 → 写工具查重兜底不重复建卡；0=禁用 |
| `APPROVAL_TTL_SECONDS` | 86400 | 审批有效期：过期后 `approve` 返回 410；点「拒绝」可放弃该操作并让会话恢复，或新建会话重新发起 |

工具层还有一个不依赖配置的硬治理：**输出契约校验**。每个工具在 `app/agent/tools.py` 的
`OUTPUT_SCHEMAS` 声明输出结构，`run_tools` 在统一出口用 Pydantic 对 artifact 二次校验，
契约漂移/脏数据转 `OUTPUT_INVALID` 结构化错误（stage=output，结果不进状态/前端/记忆），
不会静默流入下游。

超时/上限触发的 final 均为友好提示、不产生 error 事件，且 thread 运行时状态正常释放
（可再次发起新 run）。专项测试见 `backend/tests/test_runtime_governance.py`。

## 上下文卫生

工具结果回喂模型前的治理，防止上下文爆炸（结果太多 → token 成本飙升、模型注意力被稀释）：

| 配置 | 默认 | 行为 |
| --- | --- | --- |
| `CONTEXT_MAX_CHARS` | 2000 | 单条工具结果文本字符上限：超长截断并提示「可带 limit/offset 继续查询」 |
| `CONTEXT_MAX_LINES` | 40 | 单条工具结果文本行数上限：超行保留前 N 行（列表型结果防全量塞入） |
| `TOOL_RESULT_CACHE_TTL` | 300 | 查询工具（`query_bill`/`list_tracking_cards`/`list_events`）同参结果缓存秒数；0=禁用 |

配套的工具侧能力：

- **分页**：`query_bill`、`list_tracking_cards` 均支持 `limit`/`offset` 翻页，
  摘要末尾提示「还有 N 条未显示，可设置 offset=X 继续」——模型先看摘要，
  需要明细时再按页取全，形成「摘要 + 按需取全」的卫生模式。
- **缓存命中**：同一查询参数在 TTL 内重复调用不重跑数据库，`tool_result` 事件带 `cached=true`
  标记；写工具不缓存。缓存只覆盖 `CACHEABLE_TOOLS`（纯只读查询），
  `save_memory` 等有副作用的工具绝不缓存。

专项测试见 `backend/tests/test_context_hygiene.py`。

## 上下文管理

「模型每轮看到什么」由三部分组成，来源与生命周期彼此独立：

| 层 | 存哪 | 怎么进上下文 |
| --- | --- | --- |
| 短期记忆 | PG checkpoint 的 `state.messages`（`AsyncPostgresSaver`，4 张表） | 续轮滑动窗口保留最近 `HISTORY_WINDOW_SIZE=10` 条；被挤出的**普通文本消息**增量合并进 `conversation_summaries` 的递进摘要，注入回 System。工具调用、审批等结构化消息不参与摘要，避免破坏恢复语义 |
| 长期记忆 | `agent_memory` 表（按 `user_id` 隔离） | 每轮**全量**拼进 system。规模只有 10–30 条，全量比检索更可靠；前端「设置与记忆」页可查看/新增/删除 |
| 知识库 | `knowledge_chunks`（pgvector） | 模型按需调 `search_knowledge` 工具召回，不常驻（见下一节） |

标识粒度别混：`session_id` / `thread_id`（`u{user_id}:{session_id}`）是**会话级**，
checkpoint、滑动窗口、摘要都按它存；`run_id` 其实也是它；一次 HTTP 请求则用
`request_id`（见「稳定运行与可观测性」）。项目目前**没有**按轮次回溯原始消息的消息表——
被窗口挤出后只剩摘要，完整历史只在前端 localStorage。

专项测试见 `backend/tests/test_summary.py`、`test_memory.py`。

## RAG 个人知识库

三层已经闭环，从上传到模型回答全链路可跑：

| 层 | 实现 | 说明 |
| --- | --- | --- |
| 数据层 | `knowledge_documents` + `knowledge_chunks` | `Vector(1024)`、HNSW 余弦索引、`content_hash` SHA-256 去重、`ON DELETE CASCADE`；Alembic 迁移自动建 `vector` 扩展 |
| 在线摄入 | `services/knowledge/` | TXT / MD / Word / 文字型 PDF → 保留章节路径与页码的结构感知分块（400 字 / 50 字 overlap）→ 本地 `bge-m3` 批量向量化 → 入库。走 `BackgroundTasks` 异步，`pending → ready / failed`，失败清脏块 |
| 线上检索 | `search_knowledge` 工具 | query 向量化 → pgvector cosine top-k → 阈值 0.5 过滤 → `user_id` 隔离 → 片段带 `heading_path`/`page` 回喂模型。`CORE_RULES` 第 7 条要求文档相关问题先检索，检索为空如实告知、不得编造 |

前端「知识库」页支持文件上传与粘贴文本两种入口、pending 状态轮询、失败原因展示与删除确认。
REST 端点在 `/api/knowledge/documents*`，专项测试见
`backend/tests/test_parsers.py`、`test_chunking.py`、`test_ingest.py`、`test_retrieval.py`。

> **前置条件**：真实摄入必须本机装 Ollama 并 `ollama pull bge-m3`。单测用 mock embedding 覆盖逻辑，
> 没有它也能跑测试，但上传文档会失败。Excel 与照片/扫描件（OCR）尚未支持，属二期。

## 稳定运行与可观测性

这块回答的是「出错了能不能兜住、能不能定位」：

- **全局兜底（A）**：`main.py` 注册 `HTTPException` / `RequestValidationError` / `Exception`
  三个处理器，统一返回带 `request_id` 的 JSON，内部错误细节只进日志、不回前端。
  SSE 流已开始推送后异常处理器改不了响应，改由中间件记录带 rid 的堆栈，
  并在 `error` 事件里把 `request_id` 推给前端（页面会显示「问题编号」）。
- **request_id**：纯 ASGI 中间件在入口生成（或校验并透传网关的 `X-Request-ID`），
  写入 `ContextVar`、`scope['state']` 和响应头 `X-Request-ID`。OPTIONS 预检、鉴权 401
  这些到不了端点的请求也带得上。
- **结构化日志**：`LOG_LEVEL`（默认 INFO）控制，单行 JSON，固定
  `ts / level / logger / event / request_id`，外加自定义字段与异常堆栈；
  访问日志额外带 `method / path / status / duration_ms`。uvicorn 的日志也统一走这个格式。
- **Langfuse 关联（C）**：每次请求**新建** `CallbackHandler(trace_context={"trace_id": request_id})`
  ——复用单例会让并发请求的 span 串到同一条 trace。再用
  `config.metadata.langfuse_session_id = thread_id` 把同一会话的多轮请求（含审批 resume）归组。
  凭证留空时追踪整体关闭，零网络请求、不影响主流程。

排障姿势：用户报一个 `request_id`，`grep` 日志即可拿到这次请求的完整链路，
在 Langfuse 里也能直接打开同 id 的 trace。专项测试见 `backend/tests/test_observability.py`。

> 尚未实现：告警 / 健康检查异常「早发现」（B），以及按轮次回溯原始消息的消息表。
> 设计取舍见 [ADR 0005](docs/adr/0005-request_id%20与%20Langfuse%20trace%20关联.md)。

## 手动录入与 Agent 写入解耦

HITL 审批的目的是复核**模型从自然语言里推断出的参数**。用户自己手填的表单参数是确定性的，
再确认一次没有信息增益，所以看板与日历的手动操作走直连 REST，不进 `/api/chat`：

| 入口 | 链路 | 是否审批 |
| --- | --- | --- |
| 对话区自然语言「帮我建个日程」 | `/api/chat` → Supervisor → `WRITE_TOOLS` → `interrupt` → resume | 是 |
| 看板手动建卡 / 完成 / 删除 | `POST /api/cards` 等 → `card_service` | 否 |
| 日历手动新建 / 改期 / 完成 | `POST`/`PATCH` `/api/events` → `schedule_service` | 否 |

三条链路共用同一套 `schemas` / service / 用户隔离 / 输出契约，差别只是少了模型与审批两层。
日历页因此零模型调用、零延迟、标题与时间不会被模型改写，也不会占用会话运行锁。
直连写操作不产生 Langfuse trace，改由 `logger.info` 打一条带 `request_id` 的结构化日志留痕。
接口测试见 `backend/tests/test_events_api.py`——用只挂 `events_router` 的 mini app 验证这条链路确实不经过 chat。

## 幂等与重试安全

写工具自动重试的前提是**请求级幂等键**（同一操作意图的所有执行共用同一键，服务端查重/重放）：

- **键的来源**：approve 节点与 run_tools 同源派生 `approval_id = apr-{tool_call id}`
  （checkpoint 恢复后稳定），贯穿「审批 → 执行 → 重试」全生命周期。
  模型下一轮生成新 call id = 新的操作意图 = 新键。
- **单卡**（`create_tracking_card`）：复用 `tracking_cards.invocation_id` 唯一约束——
  执行前按键查重，命中即重放首次 `CardView`；并发同键由唯一约束兜底（IntegrityError → 重查返回）。
- **批量**（`create_cards_from_query`）：一次建 N 张卡无法共用单卡键，
  用 `idempotency_records` 表（键 → 首次响应 JSON），查重、建卡、写记录**同一事务**，
  命中即重放首次批次结果。
- **无需键的写工具**：`complete_tracking_card`（状态幂等：已 done 直接成功）、
  `save_memory`（upsert 同 key 覆盖）天然安全。
- **读工具**：天然幂等 + 结果缓存，重试无条件安全。

由此，「执行成功但响应超时后自动重试」这类场景不会重复建卡。
专项测试见 `backend/tests/test_idempotency.py`。

**工具层自动重试**（`TOOL_RETRIES`）与幂等配合形成完整闭环：

- 只重试瞬时错误（超时 `TOOL_TIMEOUT` / 工具显式标记 `retryable`），
  指数退避 `RETRY_BACKOFF_SECONDS * 2^(attempt-1)`；
- 重试在 `run_tools` 内、**复用同一 tool call id** → 幂等键贯穿整个重试窗口不重置；
- 「执行成功但响应超时」的重试由幂等键查重命中 → 重放首次结果，绝不重复建卡；
- 参数校验（`INVALID_ARGUMENTS`）与确定性业务失败（`TOOL_FAILED`）不重试——
  重试相同参数结果必然相同；
- 事件 `tool_result.result.attempts` 如实上报实际尝试次数；重试耗尽后只推一次结构化错误回喂模型。

专项测试见 `backend/tests/test_tool_retry.py`（含成功但超时 → 重试 → 幂等查重的端到端用例）。

---

## 评估

43 条用例覆盖查询 / 建卡 / 日程 / 看板 / 搜索 / 记忆 / 拒答 / 多轮八类，
走**生产同一条图链路**（真实工具 + 真实审批 + 真实拦截）跑模型。

```bash
cd backend
python -m evals --limit 3                 # 冒烟
python -m evals --repeat 3                # 全量，报告写入 evals/results/latest.md
python -m evals --json-out results.json   # 跨版本比对的机器可读结果
```

### 最新一次结果

> 下表是数据集扩到 43 条**之前**的历史跑批（33 条 × 3 轮），仅作基线参考；
> 当前 `evals/dataset.py` 有 43 条用例，需重新跑批才能与历史数字对比。

`deepseek-v4-flash`，33 条 × 3 轮 = 99 次执行（`--repeat 3`，完整报告见
[`backend/evals/results/latest.md`](backend/evals/results/latest.md)）：

| 指标 | 结果 | 口径 |
| --- | --- | --- |
| 通过率 | **93.9%**（93/99） | 工具 / 参数 / 审批 / 安全四层断言全过 |
| 工具选择准确率 | **94.4%** | 90 次参与统计；拒绝类要求不调工具 |
| 参数准确率 | **89.6%** | 48 次声明了参数，子集匹配 |
| 审批触达率 | **100%** | 26 次写工具调用，全部弹出确认卡 |
| 越权写操作 | **0 次** | 写工具执行成功但无审批记录——安全底线 |
| 平均工具调用 | 1.28 次/任务 | 含纠错重试 |
| 延迟 p50 / p95 | 3.7 s / 7.0 s | 端到端墙钟，含建库播种 |
| 平均 tokens | 入 11,917 / 出 425 | 模型自报 usage |

**弱项是诚实的**：`board` 66.7%、`multi` 83.3%，`create` 88.9%。
最典型的三类失败——「把第 3 张卡片标记完成」有时只查不写（1/3）、
「把某日程改期/标记完成」有时只查不改（2/3）、
多轮补充条件（「只要餐饮的」）偶尔直接作答而不重新查询。
这些是提示词对「查询→写操作」链路的约束不够硬，不是护栏失效：
同一批 99 次执行里，写工具一次都没有绕过审批。

指标口径、「结果怎么读」、加用例的规则见 [docs/evals.md](docs/evals.md)。
`score` / `aggregate` 是纯函数，由 21 条离线单测（`backend/tests/test_evals.py`）
锁住算法正确性——不联网也能验证指标算得对不对。

> **单次数字不能当结论**：temperature=0 也不保证确定性，同一份代码两次跑出过
> 78.8% 和 93.9%。所以默认用 `--repeat 3`，报告里每条用例都标 `通过 k/N`。

---

## 建议的第一轮实验

先生成示例账单并在前端导入（或用你自己的 `.xlsx`），然后按顺序观察：

1. 发送「查询账单」，观察 `stream` → `arguments_validated` → `tool_result` 事件流。
2. 发送「把最近一笔餐饮做成报销卡」，观察 `approval_created` 与 SSE 暂停。
3. 在确认卡里**修改参数**，验证审批参数与实际执行参数不一致时会被拒绝。
4. **重复提交**同一个审批，验证幂等键生效、不会创建第二张卡。
5. 执行中**再发一条消息**，观察它被排队并在下一个检查点注入（不打断当前步骤）。
6. 发送「只要超过 100 元的」，观察它是在最近一次查询范围内二次筛选，而不是重新全量查询。
7. 用 `python scripts/gen_sample_bills.py --no-category` 生成不带分类的账单并导入，
   再调用 `POST /api/bills/classify`，观察规则 + 真实模型的分类结果。

账单查询支持日期/自然日、收支方向、消费分类、商户或转账对方、原始交易类型、
金额上下界、支付方式和交易状态。人工分类（`POST /api/bills/{id}/category`）
不会被后续导入或模型覆盖。

---

## 已知限制

诚实清单，也是欢迎贡献的方向：

- **checkpoint 无清理 / TTL 策略**：对话历史与工具调用参数存在 PG 的 4 张 checkpoint 表里，
  未加密、不会自动清理。滑动窗口只裁剪喂给模型的内容，**不删**库里的历史。
- **没有按轮次回溯的消息表**：被窗口挤出的原始消息只剩递进摘要，完整历史只在前端
  localStorage，后端无法回放「第 N 轮的原话」。
- **告警与异常早发现（B）尚未实现**：现在只能靠 `request_id` 事后定位，没有主动告警。
- **RAG 覆盖不完整**：暂不支持 Excel 与照片 / 扫描件 OCR；没有 query 重写、重排与单文档上限
  （量级小，向量召回够用）；真实摄入依赖本机 Ollama `bge-m3`。
- **知识库异步摄入是进程内 `BackgroundTasks`**：多 worker 部署不共享队列，需换 Redis/Celery 队列。
- **日程 / 搜索两个领域子图能力较薄**（各 4 个工具），账单域与知识库最完整。
- **CI 需要 PG 服务**：`.github/workflows/ci.yml` 直接跑 `pytest`，但 runner 未编排
  PostgreSQL + pgvector，需补 service 才能让测试在 CI 上通过。

---

## 文档

- [`docs/adr/`](docs/adr/) — 架构决策记录：幂等键用 ContextVar、子图内不设输入检查点、提示词分层、并行工具按批校验、request_id 与 Langfuse 关联
- [`docs/tool-closure.md`](docs/tool-closure.md) — `query_bill` 与 `create_tracking_card` 的调用闭环与错误契约
- [`docs/evals.md`](docs/evals.md) — 评估指标口径与结果怎么读
- [`docs/archive/`](docs/archive/) — **已废弃**的旧架构文档（四层 HybridPlanner），仅供历史参考
- [`DEPLOY.md`](DEPLOY.md) — Docker / 内网穿透部署与运维
- [`SECURITY.md`](SECURITY.md) — 安全边界与已知限制
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 开发环境、测试与代码风格
- [`CHANGELOG.md`](CHANGELOG.md) — 版本变更记录

## 参考

- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph PostgreSQL checkpoint: https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres
- pgvector: https://github.com/pgvector/pgvector
- Langfuse: https://langfuse.com/docs
- LangChain Deep Agents: https://github.com/langchain-ai/deepagents
- ByteDance DeerFlow: https://github.com/bytedance/deer-flow

## 许可

[Apache License 2.0](LICENSE)。示例数据与文档中的商户、姓名均为虚构占位内容。
