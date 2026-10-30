# PersonalCopilot

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

这是一个用真实账单场景学习 **Agent 工程问题**的实验框架。业务功能只做最小闭环，重点观察：

- 模型或规划器如何选择工具；
- 参数如何经过 JSON Schema 与 Pydantic 严格校验；
- 读工具如何执行，写工具如何进入人在环；
- LangGraph 如何暂停、持久化、恢复；
- 工具结果如何经过输出 schema 校验；
- 幂等键如何防止重复确认造成重复写入；
- Excel 导入、数据库事务、SSE 事件和运行审计怎样互相配合。

> **它不是什么**：不是一个账本产品，没有预算分析、多账本、同步或移动端。
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
             tools.py   工具层：契约、权限、幂等、输出校验
  api/       chat.py    SSE 事件、审批恢复、运行审计
             bills.py / cards.py / events.py
  data/      db.py / models.py / views.py
  services/  importer.py（Excel 解析，确定性，不调 LLM）
             classification.py（规则 + 真实 LLM 分类）
             bill_service.py / card_service.py / schedule_service.py
  config.py  配置与隐私边界校验    schemas.py  输入输出契约
  errors.py  结构化错误             main.py     应用装配

frontend/src/
  views/       ChatView / BoardView / BillsView / CalendarView
               （ReportsView、SettingsView 目前是占位页）
  components/  ChatPanel / TaskBoard / BillTable / ApprovalCard / SessionList / ImportPanel
  stores/      session.ts   事件流统一分发
  services/    agentClient.ts   SSE 解析与 API
  tools/       registry.ts      工具结果按注册表提取，新增工具不改主流程

scripts/     gen_sample_bills.py（生成脱敏示例账单）、E2E 与基准脚本
docs/        adr/（架构决策记录）、archive/（已废弃的旧架构文档）
```

---

## 快速开始

### 前置条件

- **Python 3.12+**（代码用了 `asyncio.timeout`，最低 3.11）
- **Node 20+**
- 一个 OpenAI 兼容的模型服务。默认用本地 Ollama：

```bash
# 安装 Ollama 后拉取默认模型
ollama pull qwen3:8b
```

### 启动

```bash
# 后端
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 按需修改模型配置
uvicorn app.main:app --reload

# 前端（另一个终端）
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173 ，后端健康检查 http://127.0.0.1:8000/health 。

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
| `APPROVAL_TTL_SECONDS` | 86400 | 审批有效期：过期后 resume 返回 410，需重新发起 |

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

33 条用例覆盖查询 / 建卡 / 日程 / 看板 / 搜索 / 记忆 / 拒答 / 多轮八类，
走**生产同一条图链路**（真实工具 + 真实审批 + 真实拦截）跑模型，
输出六项指标：工具选择准确率、参数准确率、审批触达率、**越权写操作数（必须为 0）**、
平均工具调用次数、p50/p95 延迟与 tokens。

```bash
cd backend
python -m evals --limit 3                 # 冒烟
python -m evals                           # 全量，报告写入 evals/results/latest.md
python -m evals --json-out results.json   # 跨版本比对的机器可读结果
```

指标口径、「结果怎么读」、以及加用例的规则见 [docs/evals.md](docs/evals.md)。
指标算法本身有 18 条离线单测（`backend/tests/test_evals.py`）锁住——
`score` / `aggregate` 是纯函数，不联网也能验证算得对不对。

> 报告里的数字请用你的模型实跑后再写进 README 或简历。
> `--limit 3` 冒烟通过 ≠ 全量通过，换模型、换提示词都要重跑。

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

- **消费报表页与设置/记忆管理页仍是占位**。后端的 `save_memory` 工具是完整的，
  但没有任何管理界面——用户无法查看或删除已记住的偏好。
- **`sensitive_terms` 已定义但未接线**。`config.py` 里有敏感词（密码、验证码、转账等），
  但目前没有任何环节会拦截命中这些词的请求。
- **checkpoint 数据库无清理策略**，`backend/data/checkpoints.db` 会随使用持续增长
  （内含完整对话历史与工具调用参数，未加密）。
- **数据库无迁移工具**，schema 变更靠 `ALTER TABLE` 手工兼容旧库。
- 无鉴权，API 默认监听本机，请勿直接暴露到公网。
- 日程/搜索两个领域子图目前能力较薄（各 4 个工具），账单域最完整。

---

## 文档

- [`docs/adr/`](docs/adr/) — 架构决策记录：为什么用 `ContextVar` 传幂等键、为什么子图内不设输入检查点
- [`docs/tool-closure.md`](docs/tool-closure.md) — `query_bill` 与 `create_tracking_card` 的调用闭环与错误契约
- [`docs/archive/`](docs/archive/) — **已废弃**的旧架构文档（四层 HybridPlanner），仅供历史参考
- [`SECURITY.md`](SECURITY.md) — 安全边界与已知限制
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 开发环境、测试与代码风格

## 参考

- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph SQLite checkpoint: https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite
- LangChain Deep Agents: https://github.com/langchain-ai/deepagents
- ByteDance DeerFlow: https://github.com/bytedance/deer-flow

## 许可

[Apache License 2.0](LICENSE)。示例数据与文档中的商户、姓名均为虚构占位内容。
