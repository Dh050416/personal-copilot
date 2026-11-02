# 部署上线（个人部署 / 朋友试用）

本文档说明如何把 PersonalCopilot 部署成「几个朋友能通过公网访问」的状态。

两种方案（推荐用方案一，真正「上线」）：

- **方案一：Docker + 阿里云服务器**（推荐，稳定、开机即用、朋友 24h 可访问）
- **方案二：内网穿透**（轻量，无需买服务器，但你电脑要一直开机）

> 前提：你已能在本机正常跑通（后端 `uvicorn` + 前端 `npm run dev`）。
> 模型默认走云端 DeepSeek（`backend/.env` 已配），因此**朋友无需装 Ollama、无需 GPU**。

---

## 方案一：Docker + 阿里云服务器（推荐）

### 整体架构

```
朋友浏览器 ──HTTPS──> 阿里云 ECS（80/443 端口）
                        ├─ frontend 容器（nginx）：托管静态页面 + 反代 /api
                        └─ backend 容器（FastAPI uvicorn :8000，仅内网可达）
                              └─ DeepSeek 云模型
                              └─ copilot_data 数据卷（账单 + 对话历史持久化）
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

关键项：

| 变量 | 说明 |
|---|---|
| `MODEL_API_KEY` | 你的 DeepSeek Key（从本地 `backend/.env` 抄） |
| `MODEL_NAME` | `deepseek-v4-flash`（与你本地一致） |
| `ALLOW_CLOUD_MODEL` | `true`（云端模型必开） |
| `AUTH_SECRET` | 登录令牌签名密钥（可选；留空则首次启动自动生成并持久化） |

> 鉴权已改为**账号密码体系**：朋友打开页面会先看到注册/登录页，各自注册账号即可使用，
> 数据按账号自动隔离，**无需再分发密令**。

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
docker compose logs -f              # 看日志
docker compose restart              # 重启
docker compose up -d --build        # 更新代码后重建
docker compose down                 # 停止（数据卷保留，不丢数据）
```

**数据备份**：数据存在 Docker 卷 `copilot_data` 里，可用：
```bash
# 备份（把卷导出到宿主机）
docker run --rm -v copilot_data:/data -v $(pwd):/backup alpine tar czf /backup/copilot_data.tar.gz -C /data .
# 或直接进 backend 容器跑项目自带的备份脚本
docker compose exec backend python scripts/backup.py
```

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
                               ├─ 前端（静态文件，由穿透服务暴露）
                               └─ 后端（uvicorn :8000，带访问令牌鉴权）
                                     └─ DeepSeek 云模型（账单数据会上云）
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

后端（在 `backend/` 目录，用项目根 venv）：

```bash
cd D:/LifeManage/backend
D:/LifeManage/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 去掉 --reload（生产不要热重载）；--host 127.0.0.1 表示只本机监听，由穿透服务转发
```

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

账单/卡片/记忆都在 `backend/data/personal_copilot.db`（很小，几 MB）。
对话历史在 `backend/data/checkpoints.db`（会随对话快速增长，可能数百 MB）。

**备份（在 `backend/` 目录）**：

```bash
D:/LifeManage/.venv/Scripts/python.exe scripts/backup.py            # 只备份核心数据（推荐）
D:/LifeManage/.venv/Scripts/python.exe scripts/backup.py --with-ckpt # 连对话历史一起
```

产出在 `backend/data/backups/`，文件名带时间戳。

**恢复**：停止后端 → 用备份文件覆盖 `backend/data/personal_copilot.db` → 重启。

> 建议养成习惯：每次大版本改动前、或朋友开始大量用之前，跑一次备份。
> 也可以把备份脚本挂个定时任务（Windows 任务计划程序）每天自动跑。

---

## 七、隐私边界（重要，务必告知朋友）

- 模型用的是**云端 DeepSeek**，账单数据和对话内容会发送到 DeepSeek 服务器。
- 这是「朋友试用」可接受的权衡（换来免 GPU、免 Ollama 的简单部署），但**不是**完全本地隐私。
- 若要让账单数据完全不出本机，把 `backend/.env` 切回本地 Ollama（`MODEL_BASE_URL=http://127.0.0.1:11434/v1`、`ALLOW_CLOUD_MODEL=false`），代价是你的电脑要一直开机跑 Ollama。

---

## 八、安全清单（暴露公网前逐项确认）

- [ ] `AUTH_SECRET` 已配置（或接受自动生成；建议生产固定一个强随机值）
- [ ] `CORS_ORIGINS` 已包含穿透后的前端域名
- [ ] 后端 `--host 127.0.0.1`（只本机监听，不直接 `0.0.0.0`）
- [ ] 备份脚本已跑过一次，`backend/data/backups/` 有产物
- [ ] 已告知朋友「账单数据会上云」
- [ ] 电脑不休眠、后端常驻（可用 `nssm` 或任务计划把 uvicorn 注册成服务）

---

## 常见问题

**Q：朋友打开页面但请求失败（401）？**
A：登录态失效（令牌被清、或后端 `AUTH_SECRET` 变了导致旧令牌失效）。让朋友重新登录即可。

**Q：跨域报错（CORS）？**
A：把朋友实际访问的前端域名加进后端 `CORS_ORIGINS`，重启后端。

**Q：checkpoints.db 太大了怎么办？**
A：它只存对话历史，不影响账单数据。可删（会丢失对话历史），或备份后清理。
后续可加 TTL 自动清理策略（见 README「已知限制」）。
