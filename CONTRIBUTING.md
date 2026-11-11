# 贡献指南

感谢你愿意参与。这是一个 **Agent 工程实验框架**，不是产品。所以贡献的重心不在"多做一个功能"，
而在**把某类 Agent 落地问题治理得更清楚**：参数校验、HITL、幂等、可观测性、上下文成本。

## 项目边界（重要）

`README.md` 开头列出的「观察点」就是本项目的范围。以下改动**不在**范围内，提 PR 前请先开 issue 讨论：

- 增加新的业务领域（如股票、健康、邮件）——本项目刻意只保留账单这个最小载体。
  知识库属于通用能力，不算业务领域扩展。
- 用更重的框架替换 LangGraph。
- 把「本地优先」的隐私模型改成默认上云：模型默认本地 Ollama，
  云端必须显式 `ALLOW_CLOUD_MODEL=true` 才能启动。

需要新增工具时，请一并补上：`args_schema`（Pydantic `StrictModel`）、`OUTPUT_SCHEMAS` 输出契约、
是否属于 `WRITE_TOOLS`（Agent 链路里的写工具必须走人工审批；手动表单录入走直连 REST，不进图）、是否属于 `CACHEABLE_TOOLS`（**只有只读工具**可缓存），
以及 `tests/` 下的专项用例。

## 环境准备

**Python 3.12+**（`asyncio.timeout` 最低 3.11）、**Node 20+**，以及
**PostgreSQL 16 + pgvector**（业务库 / checkpoint / 知识库向量三合一）和可选的 **Redis**。

```bash
# 数据库（本机 Docker 示例）
docker run -d --name postgres -e POSTGRES_PASSWORD=123456 -p 5432:5432 pgvector/pgvector:pg16
# 建 PersonManager 与 PersonManager_test 两个库；测试库需 CREATE EXTENSION vector

# 后端
cd backend
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # 至少确认 DATABASE_URL / CHECKPOINT_URL 指向 127.0.0.1
alembic upgrade head              # 建表 + vector 扩展 + HNSW 索引
python run.py                     # Windows 必须用 run.py

# 前端（另一个终端）
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

> **Windows 两个固定坑**：`psycopg` 异步模式不支持 ProactorEventLoop，`run.py` 会把 uvicorn
> 切到 SelectorEventLoop，直接跑 `uvicorn app.main:app --reload` 会连不上 checkpoint 库；
> vite 默认绑 IPv6，`127.0.0.1` 连不上，必须显式 `--host 127.0.0.1`。
> Linux / Docker 生产环境无此限制，可直接 `uvicorn app.main:app`。

打开 http://127.0.0.1:5173 先注册账号，健康检查在 http://127.0.0.1:8000/health 。

### 模型与隐私

默认连接本地 Ollama，需要两个模型：

```bash
ollama pull qwen3:8b      # 对话模型
ollama pull bge-m3        # 知识库 embedding（1024 维），真实文档摄入的必需前置
```

也可以用任何 OpenAI 兼容服务（包括云模型）。**云模型必须显式设置 `ALLOW_CLOUD_MODEL=true`**，
否则启动时会报错——因为对话内容和工具数据会离开本机。同理，**配置了 Langfuse 凭证就会把
trace（含模型输入输出）发到 `LANGFUSE_HOST`**；本地开发不想外传请把三个 `LANGFUSE_*` 留空。

## 跑测试

```bash
cd backend
pytest                    # 208 个用例（需要 PG 测试库可达）
ruff check .              # lint
ruff format --check .     # 格式（CI 暂不阻塞）

cd ../frontend
npm run typecheck         # vue-tsc
npm run build
```

> 测试 fixture 连 `PersonManager_test`，每个用例 `drop_all` + `create_all` 重建表，
> 并用 `NullPool` 规避 asyncpg 连接跨事件循环复用导致的报错。
> Windows 上 `pytest` 若报 `PermissionError [WinError 5]`（系统 Temp 目录不可写），
> 已在 `pyproject.toml` 通过 `addopts = "--basetemp=.pytest-tmp"` 规避。

## 代码风格

- 行宽 110，由 `ruff` 强制（`backend/pyproject.toml`）
- 关键设计取舍请**写在代码注释里**，说明「为什么这么做」而不是「做了什么」。
  这是本项目最被看重的部分——很多取舍（例如用 `ContextVar` 传递幂等键与 `request_id`）
  不看注释根本猜不出来
- 新增工具或治理逻辑时，同步更新 README 对应章节、`CHANGELOG.md` 与 `docs/adr/`
- 改动请求链路时，确认 `request_id` 仍能贯穿结构化日志、错误响应和 Langfuse trace，
  并补 `backend/tests/test_observability.py` 的用例

## 提交与 PR

- 一个 PR 只做一件事
- 描述里说明**解决了什么 Agent 工程问题**，以及是否新增/修改了工具契约
- 涉及工具输出结构变更时，必须同步 `OUTPUT_SCHEMAS`，否则会在统一出口被判为 `OUTPUT_INVALID`
- 涉及 schema 变更时，必须补 Alembic 迁移（不要手工 `ALTER TABLE`）

## 适合上手的点

- **告警 / 异常早发现（B）**：目前只有事后的 `request_id` 定位，没有主动告警
- **checkpoint TTL / 清理策略**：PG 的 4 张 checkpoint 表只增不减
- **按轮次回溯的消息表**：被滑动窗口挤出的原始消息目前只剩摘要
- **知识库**：Excel 与照片 OCR、query 重写与重排、多 worker 下用 Redis/Celery 队列替换 `BackgroundTasks`
- **CI**：给 `.github/workflows/ci.yml` 补 PostgreSQL + pgvector service，让 `pytest` 在 runner 上能跑

## 安全与隐私

不要提交真实账单、`.env`、数据库文件或含个人信息的日志。`backend/data/` 与 `*.db`
已在 `.gitignore` 中。结构化日志会记录 `method` / `path` / `status` / `request_id` 与异常堆栈，
**不要**往日志里打消息正文、令牌或账单内容。若发现仓库里存在泄露内容，请按 `SECURITY.md`
的方式私下联系维护者。
