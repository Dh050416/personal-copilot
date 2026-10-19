# 脚本

## gen_sample_bills.py

生成脱敏示例账单 `samples/sample_bills.xlsx`，让新用户不用拿真实账单就能跑通实验。

```bash
python scripts/gen_sample_bills.py                 # 默认 60 条
python scripts/gen_sample_bills.py --count 120
python scripts/gen_sample_bills.py --no-category   # 不带「分类」列，用于实验批量分类
```

需要 `openpyxl`（后端依赖里已有）。数据全部虚构，商户与姓名均为占位名称。

## e2e_supervisor.py

Supervisor 端到端验收：走完「查询 → 建卡 → 审批 → 完成」主链路。

## bench_batch_cards.py

批量建卡性能基准。

## verify_subgraph_iso.py

验证领域子图的状态隔离性质：主图调度字段不进子图、子图私有中间结果不写回主图。

## probes/

开发期临时探针（`probe_chunks.py` / `probe_chunks2.py` / `probe_http.py` / `probe_stream.py`），
用于调试流式输出与分块行为。**无保留价值，可安全删除**——留在这里只是避免直接删掉别人的文件。

---

上面几个脚本依赖 `app` 包，需在 `backend/` 目录下运行并把它加入模块搜索路径：

```bash
cd backend
PYTHONPATH=. python ../scripts/e2e_supervisor.py
```
