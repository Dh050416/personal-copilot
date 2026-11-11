# 部署上线（个人部署 / 朋友试用）

本文档说明如何把 PersonalCopilot 部署成「几个朋友能通过公网访问」的状态。

两种方案（推荐用方案一，真正「上线」）：

- **方案一：Docker + 阿里云服务器**（推荐，稳定、开机即用、朋友 24h 可访问）
- **方案二：内网穿透**（轻量，无需买服务器，但你电脑要一直开机）

> 前提：你已能在本机正常跑通（后端 `python run.py` + 前端 `npm run dev`）。
> 模型默认走云端 DeepSeek（`backend/.env` 已配），因此**朋友无需装 Ollama、无需 GPU**。
>
> **存储前置（容易漏）**：后端依赖 **PostgreSQL 16 + pgvector**（业务库、checkpoint、
> 知识库向量都在里面）和可选的 Redis。当前 `docker-compose.yml` **只编排 backend + frontend 两个服务**，
> 没有带 PG/Redis——上线前必须另外准备，并让 backend 容器能通过容器内网地址访问。
> 连接串写容器名（如 `postgres`），不要写 `127.0.0.1`（那是容器内自己）。

---

## 方案一：Docker + 阿里云服务器（推荐）

### 整体架构

```
朋友浏览器 ──HTTPS──> 阿里云 ECS（80/443 端口）
                        ├─ frontend 容器（nginx）：托管静态页面 + 反代 /api
                        ├─ backend 容器（FastAPI uvicorn :8000，仅内网可达）
                        │     ├─ DeepSeek 云模型
                        │     └─ JSON 结构化日志（docker logs 采集，按 request_id 检索）
                        ├─ PostgreSQL + pgvector（业务表 / checkpoint / 知识库向量，持久化卷）
                        └─ Redis（可选：只读工具结果缓存，留空则回退进程内缓存）
```

### 第 1 步：买阿里云服务器

1. 打开阿里云控制台 → 云服务器 ECS → 创建实例。
2. 选型建议（个人试用完全够用，约 50~100 元/月）：
   - **地域**：离你和朋友近的（如华东/华南）；带宽选**按量付费**，先 1Mbps 起步
   - **镜像**：Ubuntu 22.04（或 24.04）64 位
   - **规格**：2 核 2GB 即可（后端是 FastAPI，模型走 DeepSeek 云端，不吃本机 GPU/内存）
   - **系统盘**：40GB 起步
3. **安全组**（关键，别配错）：放行
   - `22`（SSH，建议只对你自己的 IP 放行）
   - `80`（HTTP）和 `443`（HTTPS，若配域名）
   - **不要**把 `8000` 放行到公网——后端只允许前端容器内网访问，compose 里已用 `expose` 隔离

### 第 2 步：SSH 登录并装 Docker

```bash
ssh root@<你的公网IP>
# 一键装 Docker（官方脚本）
curl -fsSL https://get.docker.com | sh
# 启动并开机自启
systemctl enable --now docker
# 装 docker compose 插件
apt-get install -y docker-compose-plugin
# 验证
docker --version && docker compose version
```

### 第 3 步：把代码传到服务器

两种方式任选：

**方式 A：git 克隆（推荐，后续升级方便）**
```bash
# 服务器上
git clone <你的仓库地址> personal-copilot
cd personal-copilot
```
> 注意：`.gitignore` 已排除 `.env` 和 `backend/data/`，所以你的 API Key 和账单数据不会进仓库。

**方式 B：本地打包上传**
```bash
# 本机（Windows）
# 用 scp / Xftp 把整个 D:/LifeManage 目录（除 .venv、node_modules、backend/data、.git）上传
# 或用 git 方式最省事
```

### 第 4 步：配置环境变量

```bash
cd personal-copilot
cp .env.production .env
vim .env   # 填 MODEL_API_KEY（AUTH_SECRET 可留空自动生成）
```

关键项（完整清单见 [`.env.production`](.env.production) 模板）：

| 变量 | 说明 |
|---|---|
| `MODEL_API_KEY` | 你的 DeepSeek Key（从本地 `backend/.env` 抄） |
| `MODEL_NAME` | `deepseek-v4-flash`（与你本地一致） |
| `ALLOW_CLOUD_MODEL` | `true`（云端模型必开） |
| `DATABASE_URL` | **必填**，`postgresql+asyncpg://<user>:<pw>@postgres:5432/<db>`，主机写 PG 容器名 |
| `CHECKPOINT_URL` | **必填**，`postgresql://<user>:<pw>@postgres:5432/<db>`（psycopg 方言，与业务库同库不同表） |
| `REDIS_URL` | 可选，`redis://redis:6379/0`；留空则工具缓存退化成进程内内存缓存 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL_NAME` | 知识库向量化。云端部署时本地 Ollama 不可用，需指向可达的 embedding 服务，否则上传文档会失败 |
| `LANGFUSE_*` | 可选。配了就会把 trace（含模型输入输出）发到 `LANGFUSE_HOST`，不想外传请全部留空 |
| `LOG_LEVEL` | 可选，默认 `INFO`；结构化 JSON 日志级别 |
| `AUTH_SECRET` | 登录令牌签名密钥（可选；留空则首次启动自动生成并持久化） |

> 鉴权是**账号密码体系**：朋友打开页面会先看到注册/登录页，各自注册账号即可使用，
> 数据按账号自动隔离，**无需再分发密令**。
> 首次启动前必须执行 `alembic upgrade head`（Dockerfile 的 CMD 已自动执行），
> 它会建表、`vector` 扩展与 HNSW 索引。

### 第 5 步：构建并启动

```bash
docker compose up -d --build
# 查看状态（等 backend 变 healthy）
docker compose ps
# 查看日志排查问题
docker compose logs -f backend
```

### 第 6 步：验证

```bash
# 本机（服务器上）自测
curl http://127.0.0.1/health   # 或直接浏览器打开 http://<公网IP>/
```

浏览器打开 `http://<你的公网IP>/`，会先看到**注册/登录页**。自己先注册一个账号，朋友各自注册即可，每个人的数据彼此隔离。

### 日常运维

```bash
docker compose logs -f backend       # 结构化 JSON 日志（每条带 request_id）
docker compose restart              # 重启
docker compose up -d --build        # 更新代码后重建
docker compose down                 # 停止（数据卷保留，不丢数据）
```

**排障**：页面报错时会显示「问题编号」，那就是本次请求的 `request_id`。
拿它过滤日志即可看到这次请求的完整链路（含异常堆栈）：

```bash
docker compose logs backend | grep '<request_id>'
```

日志固定字段为 `ts / level / logger / event / request_id`，访问日志另有
`method / path / status / duration_ms`。如果配了 Langfuse，同一个 id 也是 trace_id，
可在 Langfuse 里直接打开对应 trace。

**数据备份**：业务数据、checkpoint、知识库向量都在 **PostgreSQL**，不在 `copilot_data`
卷里（该卷现在只放 `auth_secret` 等少量本地状态）。用 `pg_dump`：

```bash
docker compose exec postgres pg_dump -U postgres -d PersonManager \
  | gzip > backup_$(date +%F).sql.gz
```

> 注意：这里的 `postgres` 服务需要你自己加进 compose（当前模板未编排 PG）。
> 若 PG 是外部实例，直接在宿主机对那个实例执行 `pg_dump` 即可。
> Redis 只存可失效的工具结果缓存，无需备份。

### 可选：绑定域名 + HTTPS

有域名的话，用 nginx/caddy 反代 + Let's Encrypt 免费证书，把 80/443 指向 frontend 容器的 80 端口即可。无域名先裸 IP + HTTP 也能让朋友用。

---

## 方案二：内网穿透（轻量备选）

---

## 一、整体架构

```
朋友浏览器 ──HTTPS──> 内网穿透服务（cpolar/frp/Cloudflare Tunnel）
                          │
                          └──> 你本机
                               ├─ 前端（dist/ 静态文件，由穿透服务暴露）
                               ├─ 后端（uvicorn :8000，账号密码鉴权）
                               │     └─ DeepSeek 云模型（账单数据会上云）
                               ├─ PostgreSQL + pgvector（本地 Docker 容器）
                               └─ Redis（可选，本地 Docker 容器）
```

关键点：
- **执行端始终是你本机**。朋友只是通过一个公网 URL 访问你的页面。
- **你的电脑必须开机、不休眠、后端在跑**，朋友才能用。
- 内网穿透只做「端口转发」，不改变你的代码。

---

## 二、第一步：账号鉴权（无需手动配密令）

后端已内置**账号密码登录体系**：朋友打开前端公网地址，先注册账号（用户名 + 密码），
登录后即为各自独立的生活管家，数据按 `user_id` 自动隔离，互不可见。

- 注册/登录端点：`POST /api/auth/register`、`POST /api/auth/login`。
- 登录成功后后端签发签名令牌（存浏览器 localStorage），后续请求自动携带。
- 签名密钥来自环境变量 `AUTH_SECRET`（可选）；不设置则自动生成并落到 `data/auth_secret`，
  重启后令牌仍有效。

无需再手动生成和分发密令 token。

同时把穿透后的**公网前端域名**加进 CORS 白名单（否则浏览器会拦跨域请求）：

```bash
# 假设穿透后前端域名是 https://xxxx.cpolar.cn
CORS_ORIGINS=["https://xxxx.cpolar.cn","http://127.0.0.1:5173","http://localhost:5173"]
```

改完重启后端（uvicorn `--reload` 会自动重载，但 `.env` 改动建议手动重启）。

---

## 三、第二步：启动前后端（生产模式，非 dev）

后端（在 `backend/` 目录，用 **backend 自己的 venv**）：

```bash
cd D:/LifeManage/backend
alembic upgrade head
D:/LifeManage/backend/.venv/Scripts/python.exe run.py
```

> **不要**直接 `python -m uvicorn app.main:app`：Windows 的 ProactorEventLoop 与
> checkpoint 用的 psycopg 异步模式不兼容，`run.py` 专门把它切到 SelectorEventLoop。
> `run.py` 当前带 `reload=True`（开发友好）；长期常驻建议复制一份把 `reload` 改为 `False`，
> 再用 nssm / 任务计划注册成服务。`--host` 固定 `127.0.0.1`，只本机监听、由穿透服务转发。
>
> 启动前确认 PG（5432）与 Redis（6379，若配置）容器已运行，`.env` 里连接串写
> `127.0.0.1` 而**不是** `localhost`（Windows 下后者优先解析到 IPv6，会连不上）。

前端（在 `frontend/` 目录，构建静态产物并用简单静态服务托管）：

```bash
cd D:/LifeManage/frontend
npm run build          # 产出 dist/
```

`dist/` 是纯静态文件，可用任意静态服务器托管，例如：

```bash
# 用 Node 的一个轻量静态服务器（或 npx serve，或直接交给穿透服务指向 dist）
npx serve dist -l 5173
```

> 前端通过 `/api` 代理访问后端。若你用 `VITE_API_BASE` 直连后端，需在 `.env` 里设
> `VITE_API_BASE=http://127.0.0.1:8000` 并重新 build。默认（不设）走同源 `/api` 代理，
> 需要 vite 的 dev server 或一个能做 `/api` 反代的静态服务。

---

## 四、第三步：内网穿透（暴露公网）

任选一种，都是「把本机端口映射成一个公网 URL」：

### 方案 A：cpolar（最简单，Windows 有 GUI）

1. 下载安装 cpolar，注册账号拿到 authtoken。
2. 映射两条隧道：
   - 前端 `5173` → 得到 `https://xxxx.cpolar.cn`
   - 后端 `8000` → 得到 `https://yyyy.cpolar.cn`（或让前端同源代理到后端）
3. 把前端公网域名填进后端 `CORS_ORIGINS`（见第二步）。

### 方案 B：Cloudflare Tunnel（免费、稳定、自带 HTTPS）

```bash
cloudflared tunnel --url http://127.0.0.1:5173   # 前端
cloudflared tunnel --url http://127.0.0.1:8000   # 后端
```

### 方案 C：frp（自建服务器中转，进阶）

适合长期稳定使用，需要一台有公网 IP 的 VPS 做中转。

---

## 五、朋友怎么用

1. 打开你给的前端公网 URL（如 `https://xxxx.cpolar.cn`）。
2. 首次打开会看到**注册/登录页**：点「注册」，起个用户名 + 设密码，进入即自己的管家。
3. 之后同一浏览器自动保持登录；换设备重新登录即可（数据跟着账号走，不丢）。

> 每个账号的数据（账单/卡片/日程/记忆）彼此隔离，互不可见。

---

## 六、数据备份与恢复

数据已全部迁到 **PostgreSQL**：账单 / 卡片 / 日程 / 长期记忆 / 对话摘要 /
知识库文本块与向量 / LangGraph checkpoint 都在 `PersonManager` 库里。
`backend/data/` 现在只放 `auth_secret` 等少量本地状态；Redis 是缓存，无需备份。

> 旧版本文档提到的 `scripts/backup.py` 与 `*.db` 备份方式**已随 SQLite 迁移一并移除**，
> 仓库里不再有这个脚本，别照旧命令执行。

**备份**（PG 跑在 Docker 容器 `postgres` 里时）：

```bash
docker exec postgres pg_dump -U postgres -d PersonManager \
  > backup_$(date +%F).sql
# 或压缩
docker exec postgres pg_dump -U postgres -d PersonManager | gzip > backup_$(date +%F).sql.gz
```

**恢复**：停止后端 → 建一个空库 → `psql -U postgres -d PersonManager < backup.sql`
→ 确认 `vector` 扩展存在 → 重启后端。schema 变更由 Alembic 管理，
恢复旧库后若版本落后，执行 `alembic upgrade head` 补齐。

> checkpoint 表会随对话持续增长且没有 TTL，备份前可按需单独清理，但删掉会丢对话历史。
> 建议每次大版本改动前、或朋友开始大量使用之前跑一次备份。

---

## 七、隐私边界（重要，务必告知朋友）

- 模型用的是**云端 DeepSeek**，账单数据和对话内容会发送到 DeepSeek 服务器。
- 这是「朋友试用」可接受的权衡（换来免 GPU、免 Ollama 的简单部署），但**不是**完全本地隐私。
- 若要让账单数据完全不出本机，把 `backend/.env` 切回本地 Ollama（`MODEL_BASE_URL=http://127.0.0.1:11434/v1`、`ALLOW_CLOUD_MODEL=false`），代价是你的电脑要一直开机跑 Ollama。

---

## 八、安全清单（暴露公网前逐项确认）

- [ ] `AUTH_SECRET` 已配置（或接受自动生成；建议生产固定一个强随机值）
- [ ] `CORS_ORIGINS` 已包含穿透后的前端域名
- [ ] PostgreSQL + pgvector 已就绪、`alembic upgrade head` 已执行、`DATABASE_URL` / `CHECKPOINT_URL` 指向正确的库
- [ ] PG 密码不是默认的 `123456`，且 5432 / 6379 **没有**放行到公网
- [ ] 后端 `--host 127.0.0.1`（只本机监听，不直接 `0.0.0.0`）
- [ ] `LANGFUSE_*` 若配置，已确认 trace 外传可接受；否则全部留空
- [ ] `pg_dump` 备份已跑过一次，产物能正常恢复
- [ ] 日志目录 / `docker logs` 的访问权限已限制（异常堆栈可能带工具参数）
- [ ] 已告知朋友「账单数据会上云」
- [ ] 电脑不休眠、后端常驻（可用 `nssm` 或任务计划把 `run.py` 注册成服务）

---

## 常见问题

**Q：朋友打开页面但请求失败（401）？**
A：登录态失效（令牌被清、或后端 `AUTH_SECRET` 变了导致旧令牌失效）。让朋友重新登录即可。

**Q：跨域报错（CORS）？**
A：把朋友实际访问的前端域名加进后端 `CORS_ORIGINS`，重启后端。
OPTIONS 预检已被鉴权中间件放行，不需要额外处理。

**Q：页面报「运行失败」，只有一串问题编号，怎么查？**
A：那就是 `request_id`。在 `docker compose logs backend`（或本机 stdout）里
`grep` 这个 id，能拿到该次请求的完整结构化日志与异常堆栈；
配了 Langfuse 的话，同 id 也是 trace_id，可直接打开对应 trace。

**Q：checkpoint 表太大了怎么办？**
A：它们在 PostgreSQL 的 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 表里，
只存对话历史与工具参数，不影响账单数据。目前没有 TTL / 自动清理策略，
可以先 `pg_dump` 备份，再按需清理旧 thread（会丢失对应对话历史）。
注意滑动窗口只裁剪喂给模型的内容，**不会**删库里的历史。

**Q：后端起不来，日志里是 asyncpg / psycopg 报错？**
A：多半是 PG 没启动，或连接串写了 `localhost`（Windows 优先解析 IPv6 ::1）
导致连不上 Docker 只映射的 IPv4。统一改 `127.0.0.1`；Windows 还必须用 `run.py` 启动。
