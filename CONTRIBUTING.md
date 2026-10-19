# 贡献指南

感谢你愿意参与。这是一个 **Agent 工程实验框架**，不是产品。所以贡献的重心不在"多做一个功能"，
而在**把某类 Agent 落地问题治理得更清楚**：参数校验、HITL、幂等、可观测性、上下文成本。

## 项目边界（重要）

`README.md` 开头列出的「观察点」就是本项目的范围。以下改动**不在**范围内，提 PR 前请先开 issue 讨论：

- 增加新的业务领域（如股票、健康、邮件）——本项目刻意只保留账单这个最小载体
- 用更重的框架替换 LangGraph
- 引入云端托管服务或账号体系（本项目定位是纯本地运行）

需要新增工具时，请一并补上：`args_schema`（Pydantic StrictModel）、`OUTPUT_SCHEMAS` 输出契约、
是否属于 `WRITE_TOOLS`（写工具必须走人工审批）、以及 `tests/` 下的专项用例。

## 环境准备

需要 **Python 3.12+**（代码用了 `asyncio.timeout`，最低 3.11）和 **Node 20+**。

```bash
# 后端
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 按需修改模型配置
uvicorn app.main:app --reload

# 前端
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173 ，后端健康检查 http://127.0.0.1:8000/health 。

### 模型

默认连接本地 Ollama（`qwen3:8b`）：

```bash
ollama pull qwen3:8b
```

也可以用任何 OpenAI 兼容服务（包括云模型）。**云模型必须显式设置 `ALLOW_CLOUD_MODEL=true`**，
否则启动时会报错——因为对话内容和工具数据会离开本机。这个项目处理的是个人账单，请勿在
未经确认的情况下指向云端。

## 跑测试

```bash
cd backend
pytest                  # 147 个用例
ruff check .            # lint
ruff format .           # 格式化

cd ../frontend
npm run typecheck       # vue-tsc
npm run build
```

> Windows 上 `pytest` 若报 `PermissionError [WinError 5]`（系统 Temp 目录不可写），
> 已在 `pyproject.toml` 通过 `addopts = "--basetemp=.pytest-tmp"` 规避。
> 如仍遇到问题，手动加 `--basetemp=.pytest-tmp`。

## 代码风格

- 行宽 110，由 `ruff` 强制（`backend/pyproject.toml`）
- 关键设计取舍请**写在代码注释里**，说明「为什么这么做」而不是「做了什么」。
  这是本项目最被看重的部分——很多取舍（例如用 `ContextVar` 传递幂等键）不看注释根本猜不出来
- 新增工具或治理逻辑时，同步更新 README 对应章节和 `docs/adr/`

## 提交与 PR

- 一个 PR 只做一件事
- 描述里说明**解决了什么 Agent 工程问题**，以及是否新增/修改了工具契约
- 涉及工具输出结构变更时，必须同步 `OUTPUT_SCHEMAS`，否则会在统一出口被判为 `OUTPUT_INVALID`

## 适合上手的点

- 前端 `ReportsView.vue`（消费报表）和 `SettingsView.vue`（设置与记忆）目前是占位页
- 后端 `save_memory` 工具已完整实现，但没有任何管理界面——用户无法查看或删除已记住的偏好
- `sensitive_terms` 配置项已在 `config.py` 定义，但尚未在任何环节接线

## 安全与隐私

不要提交真实账单、`.env`、数据库文件或含个人信息的日志。`backend/data/` 与 `*.db`
已在 `.gitignore` 中。若发现仓库里存在泄露内容，请按 `SECURITY.md` 的方式私下联系维护者。
