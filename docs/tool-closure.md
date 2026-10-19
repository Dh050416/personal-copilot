# `query_bill` 与 `create_tracking_card` 调用闭环

两个工具都经过同一条网关：路由提案 → 参数 schema 校验 → 写操作策略检查 → 业务执行 → 输出 schema 校验 → 统一回执。

## 统一回执

```json
{
  "ok": true,
  "tool": "query_bill",
  "invocation_id": "唯一调用 ID",
  "attempts": 1,
  "data": {},
  "error": null
}
```

失败时 `ok=false` 且必须有 `error`：

```json
{
  "code": "INVALID_ARGUMENTS",
  "message": "工具参数未通过校验，请按 schema 修正",
  "stage": "validation",
  "retryable": true,
  "details": {}
}
```

错误阶段分为 `routing`、`validation`、`policy`、`business`、`execution`、`output` 和 `approval`。只有带 `retryable=true` 的参数问题可以进入反馈修复；业务错误、超时、输出错误、审批错误和用户拒绝不会被模型偷偷改写后重试。

## 写操作边界

`create_tracking_card` 必须先产生审批提案。恢复时会再次校验用户编辑后的参数，并要求执行参数与已确认参数一致。每个审批 ID 保存一份 `ToolReceipt`，重复恢复只返回原回执，不重复创建卡片。

## 有界反馈回路

默认最多修复一次（由 `parameter_retries` 控制，范围 0～3）。相同错误不会重复提交；修复不能切换工具；修复无变化时直接澄清。故障实验覆盖 malformed JSON、缺字段、类型错误、日期逆序、未知工具、账单不存在、输出 schema 错误、超时、数据库异常、重复审批、用户拒绝和非法人工编辑。

## 语义评估

评估样例区分执行类成功和拒答类成功：执行类要求工具名及关键参数正确；`abstain`/`sensitive` 要求不调用工具并产生澄清。评估使用临时 SQLite 数据库，不读取或修改真实账单库。

## 原始交易类型与业务分类

导入账单时，微信的 `交易类型` 写入 `Bill.transaction_type`，保留原始值；源文件明确提供的 `分类` 写入 `Bill.category` 并标记为文件提供。未提供分类时，规则与模型分类仅更新 `category` 和来源/置信度，不覆盖 `transaction_type`；无法可靠判断的保留“其他/待分类”。

导入还保留 `payment_method`、`transaction_status`、`merchant_tx_no` 和 `note`。`BillClassifier` 先用可解释规则分类，再把证据不足的消费记录按有界批次交给真实 LLM；转账、红包、充值、提现等资金流记录默认保持“其他/待分类”，避免把资金转移误判为消费。模型低置信、返回非法 key 或超出调用预算时保持原分类。

`query_bill` 支持金额边界、商户包含/精确匹配、交易类型、支付方式和状态筛选。`transaction_type=转账` 加 `merchant=某人` 可查询历史转账；查询历史是只读行为，不会触发实际付款。聊天中的自然语言查询结果会生成用户可读的合计、笔数和明细提示。

默认每次导入/批量分类最多调用模型5批，每批12组不同的分类依据，相同商户、商品、类型和方向共享结果。可通过 `CLASSIFICATION_MAX_BATCHES` 调整上限，`CLASSIFICATION_THRESHOLD` 默认为0.85（模型自报分数，不是实际准确率）。超出预算或失败的记录保留待分类，可通过前端按钮继续处理；一次服务失败会终止本轮后续模型调用，避免连续超时。

旧库启动时补齐新列。原文件里未保存过的支付方式/状态不能凭空恢复，需要重新上传原始文件；重传同一交易只补缺失字段，不改变金额、时间或人工分类。查询对象默认按名称包含匹配，重名或昵称变更应使用账单中的原始名称核对；总额是原始金额合计，不是退款抵扣后的净支出。
