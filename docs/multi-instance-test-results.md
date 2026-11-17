# 多实例协调验证（2026-11-17）

使用项目专用 PostgreSQL 测试库；没有修改生产数据库 schema。

## 通过的检查

- `tests/test_coordination.py`：7 passed。
  - 独立 Python 子进程持锁时，父进程不能获取同一会话锁；释放后可重新获取。
  - 不同用户会话不互相阻塞。
  - 两个独立 API app state 同时 resume，只有一个进入图执行，另一个返回 409。
  - 相同审批决定并发重试可接受；相反决定竞争只有一个胜出。
  - 新建 Coordinator 后仍可读取持久化输入；按 checkpoint 中的输入 ID 过滤重复。
  - 数据库连接失效后锁释放，原持有者检查失败。
- 聊天 API、运行治理、日程 API、图路由和当时的 6 项协调测试：56 passed。
- 幂等、锁释放、检查点注入和当时的 4 项协调测试：26 passed。
- 以上批次存在重叠，不能相加为独立测试数量。

## 尚未证明的边界

- 未部署 Nginx 和三个真实 HTTP 服务进行端到端压力测试。
- 专用持锁连接按秒探活，不能据此宣称网络分区下严格的 fencing / exactly-once。
- 输入在最后一个检查点之后到达时会持久保留，可能需要下一次请求驱动；没有后台调度器自动唤醒。
- 审批决定不可变，但没有单独的 pending/processing/completed 状态列；执行进度仍以 checkpoint 为准。
- 当前测试基于 ORM 创建测试表；上线前需执行并验证 Alembic 迁移。
- 持久化输入及 applied_input_ids 尚无归档策略，长会话需要后续治理。

部署前在 backend 下执行 `alembic upgrade head`，所有 API 实例共享数据库、checkpoint 存储及令牌签名密钥。
