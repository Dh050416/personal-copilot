"""把指标渲染成 Markdown：一张总表 + 一张分类表 + 失败明细。

报告是给人看的，所以顺序刻意是「先结论、再口径、最后细节」——
面试或 review 时只看前 15 行就够，需要定位问题时再往下翻。
"""
from datetime import datetime
from typing import Any

from evals.dataset import Case
from evals.metrics import CaseResult


def _pct(value: float | None) -> str:
    return '—' if value is None else f'{value * 100:.1f}%'


def _num(value: Any, suffix: str = '') -> str:
    return '—' if value is None else f'{value}{suffix}'


def render(summary: dict[str, Any], results: list[CaseResult], cases: list[Case],
           *, model_name: str, base_url: str) -> str:
    by_id = {c.id: c for c in cases}
    lines: list[str] = []
    add = lines.append

    add('# 评估报告')
    add('')
    add(f'- 模型：`{model_name}`（{base_url}）')
    add(f'- 生成时间：{datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")}')
    add(f'- 用例：{summary["n_cases"]} 条，通过 {summary["passed"]} 条')
    add('')

    add('## 总表')
    add('')
    add('| 指标 | 值 | 口径 |')
    add('| --- | --- | --- |')
    add(f'| 通过率 | {_pct(summary["pass_rate"])} | 四层断言（工具/参数/审批/安全）全过 |')
    add(f'| 工具选择准确率 | {_pct(summary["tool_selection_accuracy"])} | 期望工具都出现过；拒绝类要求不调工具 |')
    add(f'| 参数准确率 | {_pct(summary["arg_accuracy"])} | {summary["n_arg_cases"]} 条声明了参数，子集匹配 |')
    add(f'| 审批触达率 | {_pct(summary["approval_hit_rate"])} | {summary["n_approval_cases"]} 条写操作用例，收到确认卡的比例 |')
    add(f'| 越权写操作 | {summary["policy_violations"]} 次 | 写工具执行成功但无审批记录，**必须为 0** |')
    add(f'| 平均工具调用 | {summary["avg_tool_calls"]} 次/任务 | 含重试与纠错后的调用数 |')
    add(f'| 延迟 p50 / p95 | {summary["p50_latency_ms"]} / {summary["p95_latency_ms"]} ms | 端到端墙钟，含建库播种 |')
    add(f'| 平均 tokens | 入 {_num(summary["avg_tokens_in"])} / 出 {_num(summary["avg_tokens_out"])} | 模型自报 usage，流式下可能缺失 |')
    add('')

    add('## 分类')
    add('')
    add('| 类别 | 用例 | 通过 | 通过率 | 越权 |')
    add('| --- | --- | --- | --- | --- |')
    for name, bucket in summary['categories'].items():
        add(f'| {name} | {bucket["n"]} | {bucket["passed"]} | {_pct(bucket["rate"])} | {bucket["violations"]} |')
    add('')

    failed = [r for r in results if not r.passed]
    add('## 失败明细')
    add('')
    if not failed:
        add('全部通过。')
    else:
        add('| 用例 | 类别 | 期望 / 实际 | 失败点 |')
        add('| --- | --- | --- | --- |')
        for r in failed:
            case = by_id.get(r.case_id)
            expect = case.expect if case else None
            want = '、'.join(expect.tools) if expect and expect.tools else '不调工具'
            got = '、'.join(r.proposed_tools) or '无'
            add(f'| {r.case_id} | {r.category} | {want} / {got} | {"；".join(r.failures)} |')
    add('')

    add('## 用例清单')
    add('')
    add('| 用例 | 类别 | 输入 | 期望 | 结果 |')
    add('| --- | --- | --- | --- | --- |')
    for r in results:
        case = by_id.get(r.case_id)
        message = case.message if case else ''
        expect = case.expect if case else None
        want = ('、'.join(expect.tools) if expect and expect.tools else '不调工具')
        if expect and expect.approval:
            want += ' + 审批'
        mark = '通过' if r.passed else '失败'
        add(f'| {r.case_id} | {r.category} | {message} | {want} | {mark} |')
    add('')
    return '\n'.join(lines)
