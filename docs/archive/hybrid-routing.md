# 四层意图路由（已废弃 · 仅供历史参考）

> **本文描述的架构已被移除，保留在 `docs/archive/` 仅供历史参考。**
>
> 当前的意图处理改由 LLM tool-calling + Supervisor 领域子图完成：
> `intent` 节点按关键词注入流程 Skill 并判定领域，再分发到账单 / 日程 / 搜索子图。
> 对应的 `app/agent/planner.py`、`app/agent/nlu.py`、`app/agent/intent_examples.json`
> 均已删除，`config.py` 里的 `NLU_*` 与 `PARAMETER_RETRIES` 配置也已无任何引用。
> 现行架构见 [README](../../README.md)。

以下为原文。

本次把单次 LLM Planner 改为一个 HybridPlanner，保留工具注册表和 HITL 执行边界。

```text
输入 → 规则层 → 本地小模型层 → LLM 层 → 澄清兜底
         ↓           ↓            ↓
                  gate 参数校验
                     ↓ 错误
        error + previous → LLM 参数修正（默认最多一次）
                     ↓ 成功
           只读执行 / 创建审批 → interrupt → 确认 → 写入
```

## 四层的放行条件

| 层 | 职责 | 未放行时 |
|---|---|---|
| 规则 | 全句固定话术、常用日期/分类查询、明确建卡；优先匹配敏感词 | 未命中去小模型；敏感词直接澄清 |
| 小模型 | 本地字符 1/2-gram TF-IDF 最近样例分类，支持查账单、列卡片 | 分数、margin、词汇覆盖率、槽位覆盖任一不足就去 LLM |
| LLM | 长尾表达、复杂请求理解及参数纠错；使用现有 OpenAI-compatible 配置 | 无工具、多工具、超时、模型错误 → 澄清 |
| 兜底 | 产生 clarification 事件和可读问题 | 结束本轮，不访问工具 |

`MODEL_PROVIDER` 统一为 `openai_compatible`，LLM 层始终接入 `.env` 中配置的真实模型。规则与本地小模型保留，固定查询仍可以直接执行，低置信和复杂请求调用 LLM。模型失败时澄清，不静默切换到演示模式。测试通过显式注入 mock 隔离网络，不提供运行时的离线模式。

## 本地小模型的范围

这是可运行的传统 NLU 基线，不是预训练小语言模型。样例在 `backend/app/agent/intent_examples.json`，加载后构造 TF-IDF 向量，按各意图最相似样例评分。日期、分类先归一成占位符，真实槽值由确定性解析器提取。

`confidence` 是余弦匹配分数，不代表 82% 的正确概率。目前样例规模小，未经过真实用户数据校准，不能声称已达到生产准确率。新增真实标注数据后，应分开训练集、阈值验证集和最终测试集，重点评估：误执行率、各意图 precision、澄清率、LLM 调用比例和延迟。保持 `predict()` 契约即可替换为 fastText、线性分类器或其他本地分类模型。

轻量槽位解析只支持上月/本月/今天/昨天、内置分类和收入/支出。日期按 Asia/Shanghai，月份按自然月，本月截至今天。不能识别的剩余条件（商户、金额过滤、否定、多意图等）不会被丢弃后放行。

示例：

- “上月交通费”：规则直接解析日期和分类。
- “帮我看看本月的餐饮消费”：小模型识别查询，确定性提取槽位。
- “为什么这个月超支，和去年相比呢”：交给 LLM；现有单工具不能完成复杂比较时澄清，不声称已实现多步分析。
- “随便处理一下”：不默认查询、不默认建卡。
- “创建一张订阅退订卡”：生成审批提案，仍须确认。

“查询账单”的固定默认口径是支出、全时段、明细最多 20 条；运行事件显示完整筛选参数。小模型层目前仅允许只读意图。

## 反馈与重试

默认最多修正一次，可设置 `PARAMETER_RETRIES=0..3`。gate 的 INVALID_ARGUMENTS/UNKNOWN_TOOL，或只读工具返回的 INVALID_ARGUMENTS，会把原始请求、原工具与参数、结构化错误返回 LLM。修正后重新经过 gate，且不允许把参数修正变成另一个工具操作。

修正失败、LLM 不可用或预算耗尽则澄清。写提案在确认前可修正，修正后仍需审批。已确认写操作、用户拒绝、业务错误、执行超时、输出 schema 错误不会自动重试。用户改坏审批参数时直接澄清，不让模型偷偷改变已确认内容。

当前是单轮路由及有界参数纠错，不是完整多工具 ReAct 循环；澄清后的补充需作为完整请求重新提交，尚不具备跨轮槽位继承。LLM 参数能通过 schema 不代表语义一定正确，长尾理解仍需要真实评测，写操作继续以 HITL 兜底。敏感词规则也只是明确的词表策略，不是完整的提示词注入防护。

## 观察与测试

前端现有“运行事件”面板直接显示 `intent_route`：选中层、分数、原因和逐层 trace；澄清通过 `clarification` / `final` 展示。小模型低分、歧义和覆盖率不足的证据也在 trace 中。运行状态保存为 `needs_clarification`。

```powershell
# 项目根目录运行；不调用真实模型、不消耗 API 费用
.\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml backend/tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check backend/app backend/tests --no-cache
```

回归覆盖四层分流、时间槽位、否定/过滤条件保留、参数修正上限、审批拒绝、改参错误、SQLite 重启恢复、重复确认、SSE 事件和 LLM 无工具输出。LLM 使用 mock 验证接口及错误路径，未声称验证了真实模型的语义准确率。
