"""把本地评测集接进 Langfuse：Dataset 托管用例 + Experiment 跑批 + 双轨打分。

## 为什么不是「用 Langfuse 替代本地 evals」

本地 `evals/` 打的是**确定性分**：工具选没选对、参数对不对、审批有没有触发。
这类断言有唯一正确答案，用规则判比让模型判可靠得多，也便宜得多——
换成 Langfuse 的 LLM-as-a-judge 只会引入噪声。

Langfuse 真正补上的是本地做不到的三件事：

1. **跨版本对比**：改 prompt / 换模型后重跑同一批用例，UI 里直接并排看分数变化，
   本地 Markdown 报告只能自己 diff JSON。
2. **LLM-as-a-judge**：回答有没有真的答到点上、数字是不是编的——
   这些没有唯一正确答案，规则判不了。
3. **失败可下钻**：某条用例挂了，点进去能看到完整 trace（每次 LLM 调用的输入输出、
   token、耗时），本地报告只有一句「缺少期望工具 [...]」。

所以这里的分工是：**确定性指标照旧在本地算，同时作为 score 推给 Langfuse**；
judge 只判规则判不了的部分。两边口径一致，跑一次两边都有数。

## 用法

```bash
# 1) 同步用例到 Langfuse Dataset（幂等，用 case.id 作 item id）
python -m evals.langfuse_eval --sync-only

# 2) 跑一轮（默认带 judge；冒烟用 --limit 4 --no-judge）
python -m evals.langfuse_eval --limit 4 --no-judge
python -m evals.langfuse_eval --run-name baseline-v1

# 3) 改完 prompt 再跑一次，UI 里对比
python -m evals.langfuse_eval --run-name prompt-v2
```

## SDK 契约（已实测，别照抄 v2 教程）

Langfuse v4 服务端 + Python SDK 3.x 与网上大多数教程(v2)不兼容，踩过的坑：

- 入口是 `run_experiment()`，不是 v2 的 `create_experiment()`
- `task(*, item, ...)` 收到的 `item` 是 **dict**，用 `item['input']`，不是 `item.input`
- `evaluator(*, input, output, expected_output, metadata, ...)` ——
  参数是拆开的，**没有 `item` 参数**（写成 item 会报 missing keyword-only argument）
- evaluator 必须返回 `Evaluation` **对象**（有 `.name` 属性），
  返回 dict 会报 `'dict' object has no attribute 'name'`（docstring 写返回 dict，是错的）
- `Evaluation` 必须关键字构造

这些差异是实际跑出来的，不是从文档抄的。
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 系统代理会劫走 127.0.0.1，导致本地 Langfuse 连不上。
# 必须在 import langfuse 之前设好，否则 httpx 会走代理。
os.environ.setdefault('NO_PROXY', 'localhost,127.0.0.1')
os.environ.setdefault('no_proxy', 'localhost,127.0.0.1')

from langchain_core.messages import ToolMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langfuse import Langfuse  # noqa: E402
from langfuse.experiment import Evaluation, LocalExperimentItem  # noqa: E402

from app.config import Settings  # noqa: E402
from app.observability import client, flush as lf_flush, init_langfuse  # noqa: E402
from evals.dataset import CASES, Case, find  # noqa: E402
from evals.harness import run_case  # noqa: E402
from evals.metrics import score  # noqa: E402

DEFAULT_DATASET = 'personalcopilot-agent-eval'


# ---------- Dataset 同步 ----------
def _expect_view(case: Case) -> dict:
    """把期望结构摊平成可 JSON 序列化的形式（Case/Expect 是 frozen dataclass）。"""
    e = case.expect
    return {
        'tools': list(e.tools), 'any_of': list(e.any_of), 'args': e.args,
        'approval': e.approval, 'no_tools': e.no_tools, 'forbid': list(e.forbid),
        'answer_contains': list(e.answer_contains),
    }


def sync_dataset(lf: Langfuse, name: str, cases) -> int:
    """把用例推到 Langfuse Dataset。用 case.id 作 item id，因此可重复执行（幂等覆盖）。"""
    try:
        lf.create_dataset(name=name, description='PersonalCopilot Agent 评测集（与本地 evals/dataset.py 同源）')
    except Exception as exc:  # noqa: BLE001 - 已存在时会抛，忽略即可
        print(f'  dataset 已存在或创建跳过：{type(exc).__name__}')

    for case in cases:
        lf.create_dataset_item(
            dataset_name=name,
            id=case.id,
            input={'case_id': case.id, 'message': case.message, 'turns': list(case.turns)},
            expected_output=_expect_view(case),
            # note 截断：超过 200 字符会被 Langfuse 直接丢弃（实测报 Dropping value），
            # 与其整条 metadata 丢掉，不如主动截短保留关键信息
            metadata={'category': case.category, 'note': (case.note or '')[:60],
                      'seeds': list(case.seeds), 'tags': list(case.tags)},
        )
    lf.flush()
    print(f'  已同步 {len(cases)} 条用例 -> dataset `{name}`')
    return len(cases)


# ---------- Task：跑真实 Agent ----------
def build_task(model, *, timeout: float):
    """task 跑的是**生产同一条链路**（run_case → make_graph），不是裸调模型。

    这样护栏（审批、拦截、上下文卫生）的失效会真实反映在分数里；
    换成裸调模型等于绕开被测系统，测出来的不是系统能力。
    """

    async def task(*, item, **kwargs):
        # item 可能是 dict（LocalExperimentItem）也可能是 DatasetItemClient（远端拉取），
        # 两者取 input 的方式不同，这里兼容；id 的兜底顺序也一并覆盖。
        raw = item['input'] if isinstance(item, dict) else item.input
        payload = raw if isinstance(raw, dict) else {}
        case_id = (payload.get('case_id')
                   or getattr(item, 'id', None)
                   or (item.get('item_id') if isinstance(item, dict) else None))
        case = find(case_id)

        run = await run_case(case, model, timeout=timeout)
        result = score(case, run)

        # 工具返回正文：judge 判断「数字是不是编的」需要它作为依据。
        # 从 messages 里的 ToolMessage 取，不用改 harness，零侵入。
        tool_texts: list[str] = []
        for turn in run.turns:
            for msg in turn.messages:
                if isinstance(msg, ToolMessage):
                    text = str(getattr(msg, 'content', ''))
                    if text:
                        tool_texts.append(text[:1200])

        return {
            'case_id': case.id,
            'category': case.category,
            'passed': result.passed,
            'routing_ok': result.routing_ok,
            'args_ok': result.args_ok,
            'approval_ok': result.approval_ok,
            'answer_ok': result.answer_ok,
            'forbid_ok': result.forbid_ok,
            'policy_violation': result.policy_violation,
            'latency_ms': round(result.latency_ms),
            'tokens_in': result.tokens_in,
            'tokens_out': result.tokens_out,
            'proposed': result.proposed_tools,
            'failures': result.failures,
            'final': (run.last.final if run.turns else '') or '',
            'tool_texts': tool_texts[:5],
            'error': run.error,
        }

    return task


# ---------- Evaluators ----------
def _bool_eval(name: str, key: str, note: str = ''):
    """确定性打分：直接把本地算好的断言结果搬上来，口径与本地报告完全一致。"""

    def fn(*, input, output, expected_output, metadata, **kwargs):
        val = output.get(key)
        if val is None:
            return None  # 不适用（该用例没声明这个断言），不参与分母
        return Evaluation(name=name, value=1.0 if val else 0.0,
                          comment=note or '; '.join(output.get('failures') or [])[:300])
    return fn


def build_judge(judge_model):
    """LLM-as-a-judge：只判规则判不了的两件事。

    - **忠实度**：回答里的数字能不能在工具返回里找到。抓的是幻觉——
      Agent 编一个「¥1234.00」出来，工具选择、参数、审批全对，本地规则照样判通过。
    - **相关性**：有没有真的回答用户的问题（不是答非所问，也不是无理由拒答）。
    """
    template = (
        '你是账单助手的评测裁判。只依据【工具返回】判断，不要用自己的常识补事实。\n\n'
        '【用户问题】\n{question}\n\n'
        '【工具返回】（可能为空）\n{tools}\n\n'
        '【助手回答】\n{answer}\n\n'
        '回答两个问题，严格按 JSON 输出，不要多余文字：\n'
        '{{"faithful": 0或1, "relevant": 0或1, "reason": "一句话理由"}}\n\n'
        'faithful=1：回答中的金额/日期/商户等事实都能在【工具返回】中找到依据；'
        '工具返回为空时，回答若声称查到了具体数据则 faithful=0。\n'
        'relevant=1：回答直接回应了用户所问（或合理地说明查不到/需要澄清）。\n'
    )

    async def judge(*, input, output, expected_output, metadata, **kwargs):
        question = input.get('message') or ''
        answer = (output.get('final') or '')[:1500]
        tools = '\n---\n'.join(output.get('tool_texts') or [])[:2500] or '（本次未调用工具或未返回内容）'
        if not answer:
            return [Evaluation(name='answer_faithfulness', value=0.0, comment='无最终回答'),
                    Evaluation(name='answer_relevance', value=0.0, comment='无最终回答')]
        try:
            resp = await judge_model.ainvoke(
                template.format(question=question, tools=tools, answer=answer))
            raw = str(getattr(resp, 'content', ''))
            match = re.search(r'\{.*\}', raw, re.S)
            if not match:
                return None
            data = json.loads(match.group(0))
            reason = str(data.get('reason', ''))[:300]
            return [Evaluation(name='answer_faithfulness',
                               value=1.0 if data.get('faithful') else 0.0, comment=reason),
                    Evaluation(name='answer_relevance',
                               value=1.0 if data.get('relevant') else 0.0, comment=reason)]
        except Exception as exc:  # noqa: BLE001 - judge 失败不应让整轮评测崩掉
            return Evaluation(name='judge_error', value=0.0,
                              comment=f'{type(exc).__name__}: {str(exc)[:200]}')

    return judge


# ---------- CLI ----------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog='evals.langfuse_eval', description='Langfuse Dataset/Experiment 评测')
    p.add_argument('--dataset', default=DEFAULT_DATASET, help='Langfuse dataset 名')
    p.add_argument('--sync-only', action='store_true', help='只同步用例，不跑')
    p.add_argument('--run-name', default=None, help='实验名（不传则用 model+时间戳）')
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--category', action='append', default=None)
    p.add_argument('--id', action='append', default=None)
    p.add_argument('--concurrency', type=int, default=3)
    p.add_argument('--timeout', type=float, default=120.0)
    p.add_argument('--no-judge', action='store_true', help='跳过 LLM-as-a-judge（省一次模型调用）')
    p.add_argument('--json-out', default=None, help='同时输出 JSON，用于跨版本比对')
    return p.parse_args()


def select_cases(args):
    cases = list(CASES)
    if args.id:
        wanted = set(args.id)
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise SystemExit(f'未知用例 ID：{sorted(unknown)}')
        cases = [c for c in cases if c.id in wanted]
    if args.category:
        cases = [c for c in cases if c.category in set(args.category)]
    if args.limit:
        cases = cases[:args.limit]
    return cases


def main() -> int:
    args = parse_args()
    settings = Settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        print('未配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY（backend/.env），已退出。',
              file=sys.stderr)
        return 2
    init_langfuse(settings)
    lf = client()
    if lf is None:
        print('Langfuse 客户端不可用，已退出。', file=sys.stderr)
        return 2

    model = ChatOpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url,
                       model=settings.model_name, temperature=0,
                       max_tokens=settings.model_output_tokens,
                       timeout=settings.model_timeout_seconds, max_retries=1)

    cases = select_cases(args)
    if not cases:
        raise SystemExit('筛选后没有用例')

    print(f'Langfuse {settings.langfuse_host} | dataset `{args.dataset}` | 用例 {len(cases)} 条')
    print('[1/3] 同步 Dataset')
    sync_dataset(lf, args.dataset, cases)

    if args.sync_only:
        print('（--sync-only，跳过执行）')
        return 0

    run_name = args.run_name or f'{settings.model_name}-{datetime.now():%m%d-%H%M}'
    print(f'[2/3] 跑 Experiment `{run_name}`')

    # ⚠️ 必须拉远端 dataset 的 items 来跑，不能直接用本地数据构造。
    # 实测：用 LocalExperimentItem 跑出来的 experiment，其 datasetId 是 null、且每条用例
    # 被拆成一个独立 run（itemCount=1），UI 的 dataset 详情页里根本找不到这批实验。
    # 用 get_dataset().items 跑，run 才会挂到 dataset 下，也才能做跨版本对比。
    wanted_ids = {c.id for c in cases}
    data = None
    try:
        remote = lf.get_dataset(args.dataset)
        items = list(getattr(remote, 'items', None) or [])
        picked = [it for it in items
                  if (it.input or {}).get('case_id') in wanted_ids]
        if picked:
            data = picked
            print(f'  使用远端 dataset items {len(picked)}/{len(items)} 条')
    except Exception as exc:  # noqa: BLE001
        print(f'  拉取远端 items 失败（{type(exc).__name__}），回退本地数据')

    if data is None:
        print('  回退：本地构造 items（实验将无法关联 dataset，UI 里看不到）')
        data = [LocalExperimentItem(item_id=c.id, input={'case_id': c.id, 'message': c.message},
                                    expected_output=_expect_view(c),
                                    metadata={'category': c.category, 'note': (c.note or '')[:60]})
                for c in cases]

    # 前四项是确定性断言，值从本地 score() 直接搬上来，口径与本地报告一致。
    # 注意 policy_violation 的语义是「1 = 发生了越权」，所以这个分越低越好、必须为 0，
    # 它和前三项（越高越好）方向相反，看 UI 时别混在一起读。
    evaluators = [
        _bool_eval('tool_routing', 'routing_ok'),
        _bool_eval('arg_accuracy', 'args_ok'),
        _bool_eval('approval_triggered', 'approval_ok'),
        _bool_eval('policy_violation', 'policy_violation'),
        _bool_eval('answer_correct', 'answer_ok'),
    ]

    if not args.no_judge:
        evaluators.append(build_judge(model))

    result = lf.run_experiment(name=args.dataset, run_name=run_name,
                               data=data, task=build_task(model, timeout=args.timeout),
                               evaluators=evaluators, max_concurrency=args.concurrency)

    print('[3/3] 汇总')
    rows = []
    for item_result in result.item_results:
        out = getattr(item_result, 'output', None) or {}
        rows.append({'case_id': out.get('case_id'),
                     'category': out.get('category'),
                     'passed': out.get('passed'),
                     'proposed': out.get('proposed'),
                     'latency_ms': out.get('latency_ms'),
                     'failures': out.get('failures'),
                     'error': out.get('error')})
        mark = 'OK  ' if out.get('passed') else 'FAIL'
        print(f'  {mark} {out.get("case_id")}  {out.get("latency_ms")}ms  {out.get("proposed")}')
        # 失败原因直接打到终端：定位「哪一层坏了」比只知道「没过」有用得多。
        # 前缀只用 ASCII——Windows 控制台默认 GBK，'↳' 这类字符会直接 UnicodeEncodeError。
        for failure in (out.get('failures') or []):
            print(f'         -> {failure}')
        if out.get('error'):
            print(f'         -> 运行错误：{out["error"]}')

    n = len(rows)
    passed = sum(1 for r in rows if r.get('passed'))
    print()
    print(f'通过 {passed}/{n}' + (f' = {passed / n:.2%}' if n else ''))
    print(f'Experiment: {run_name}')
    if getattr(result, 'dataset_run_url', None):
        print(f'URL: {result.dataset_run_url}')

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'run_name': run_name, 'model': settings.model_name,
                                    'rows': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'JSON 已写入 {path}')

    lf_flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
