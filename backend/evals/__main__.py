"""评估入口：`python -m evals [--limit N] [--category query] [--out path.md]`。

默认读 `backend/.env` 里的模型配置（与线上同一条链路），逐条跑用例并输出：
Markdown 报告（人看）+ JSON（机器比对，用来跨版本回归）。
"""
import argparse
import asyncio
import json
from pathlib import Path

from langchain_openai import ChatOpenAI

from app.config import Settings
from evals.dataset import CASES, by_category
from evals.harness import run_case
from evals.metrics import aggregate, score
from evals.report import render


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='evals', description='跑 Agent 评估集，产出指标表')
    parser.add_argument('--model', help='覆盖 MODEL_NAME')
    parser.add_argument('--base-url', help='覆盖 MODEL_BASE_URL')
    parser.add_argument('--api-key', help='覆盖 MODEL_API_KEY')
    parser.add_argument('--limit', type=int, default=None, help='只跑前 N 条（冒烟用）')
    parser.add_argument('--category', action='append', default=None,
                        help='只跑指定类别，可重复：--category query --category create')
    parser.add_argument('--id', action='append', default=None, help='只跑指定用例 ID，可重复')
    parser.add_argument('--concurrency', type=int, default=3, help='并发用例数（默认 3，避免打爆限流）')
    parser.add_argument('--timeout', type=float, default=120.0, help='单条用例超时秒数')
    parser.add_argument('--out', default='evals/results/latest.md', help='Markdown 报告输出路径')
    parser.add_argument('--json-out', default=None, help='JSON 结果输出路径（用于跨版本比对）')
    return parser.parse_args()


def select_cases(args: argparse.Namespace):
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


async def main() -> None:
    args = parse_args()
    settings = Settings()
    base_url = args.base_url or settings.model_base_url
    model_name = args.model or settings.model_name
    model = ChatOpenAI(
        api_key=args.api_key or settings.model_api_key,
        base_url=base_url,
        model=model_name,
        temperature=0,
        max_tokens=settings.model_output_tokens,
        timeout=settings.model_timeout_seconds,
        max_retries=1,
    )

    cases = select_cases(args)
    if not cases:
        raise SystemExit('筛选后没有用例')
    print(f'模型 {model_name} @ {base_url}')
    print(f'用例 {len(cases)} 条，分布 {by_category()}')

    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def one(case):
        async with semaphore:
            run = await run_case(case, model, timeout=args.timeout)
        result = score(case, run)
        print(f'  {result.case_id:5s} {"通过" if result.passed else "失败"}  '
              f'{len(result.proposed_tools)} 次调用  {result.latency_ms:.0f}ms')
        return result

    results = await asyncio.gather(*(one(c) for c in cases))
    summary = aggregate(list(results), cases)

    report = render(summary, list(results), cases, model_name=model_name, base_url=base_url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding='utf-8')

    if args.json_out:
        payload = {'model': model_name, 'summary': summary,
                   'cases': [{'id': r.case_id, 'category': r.category, 'passed': r.passed,
                              'proposed': r.proposed_tools, 'approvals': r.approval_tools,
                              'latency_ms': round(r.latency_ms),
                              'failures': r.failures} for r in results]}
        json_out = Path(args.json_out)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    print()
    print(f'通过率        {summary["passed"]}/{summary["n_cases"]} = {summary["pass_rate"]}')
    print(f'工具选择准确率 {summary["tool_selection_accuracy"]}')
    print(f'参数准确率     {summary["arg_accuracy"]}（{summary["n_arg_cases"]} 条）')
    print(f'审批触达率     {summary["approval_hit_rate"]}（{summary["n_approval_cases"]} 条）')
    print(f'越权写操作     {summary["policy_violations"]} 次')
    print(f'延迟 p50/p95   {summary["p50_latency_ms"]}/{summary["p95_latency_ms"]} ms')
    print(f'报告已写入     {out}')


if __name__ == '__main__':
    asyncio.run(main())
