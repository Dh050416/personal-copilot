"""把指标渲染成 Markdown：一张总表 + 一张分类表 + 失败明细 + 用例清单。

报告是给人看的，所以顺序刻意是「先结论、再口径、最后细节」——
面试或 review 时只看前 15 行就够，需要定位问题时再往下翻。

多次运行（`--repeat`）时，逐条用例显示 `通过 k/N`：单次全过的用例和
三次里过一次的用例，可信度差得远，这个 N 不能藏起来。
"""
from datetime import datetime
from typing import Any

from evals.dataset import Case
from evals.metrics import CaseResult


def _pct(value: float | None) -> str:
    return '—' if value is None else f'{value * 100:.1f}%'


def _num(value: Any, suffix: str = '') -> str:
    return '—' if value is None else f'{value}{suffix}'


def _tally(results: list[CaseResult]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = counts.setdefault(r.case_id, {'n': 0, 'passed': 0})
        bucket['n'] += 1
        bucket['passed'] += int(r.passed)
    return counts


def _want(case: Case) -> str:
    """把期望翻译成人话。四种形态分开写，避免把「只读或写」误标成「不调工具」。"""
    expect = case.expect
    if expect.tools:
        text = '、'.join(expect.tools)
    elif expect.any_of:
        text = ' 或 '.join(expect.any_of)
    elif expect.no_tools:
        text = '不调工具'
    elif expect.forbid:
        text = '允许只读查询'
    else:
        text = '—'
    if expect.args:
        text += ' + 参数'
    if expect.approval:
        text += ' + 审批'
    return text


def render(summary: dict[str, Any], results: list[CaseResult], cases: list[Case],
           *, model_name: str, base_url: str) -> str:
    counts = _tally(results)
    repeats = max((c['n'] for c in counts.values()), default=1)
    lines: list[str] = []
    add = lines.append

    add('# 评估报告')
    add('')
    add(f'- 模型：`{model_name}`（{base_url}）')
    add(f'- 生成时间：{datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")}')
    if repeats > 1:
        add(f'- 用例：{len(cases)} 条 × {repeats} 次 = {summary["n_cases"]} 次执行，'
            f'通过 {summary["passed"]} 次')
    else:
        add(f'- 用例：{summary["n_cases"]} 条，通过 {summary["passed"]} 条')
    add('')

    add('## 总表')
    add('')
    add('| 指标 | 值 | 口径 |')
    add('| --- | --- | --- |')
    add(f'| 通过率 | {_pct(summary["pass_rate"])} | 四层断言（工具/参数/审批/安全）全过 |')
    add(f'| 工具选择准确率 | {_pct(summary["tool_selection_accuracy"])} '
        f'| {summary["n_routing_cases"]} 次参与统计；拒绝类要求不调工具 |')
    add(f'| 参数准确率 | {_pct(summary["arg_accuracy"])} '
        f'| {summary["n_arg_cases"]} 次声明了参数，子集匹配 |')
    add(f'| 审批触达率 | {_pct(summary["approval_hit_rate"])} '
        f'| {summary["n_approval_cases"]} 次写工具真被调用（共期望 {summary["n_approval_expected"]} 次） |')
    add(f'| 越权写操作 | {summary["policy_violations"]} 次 '
        '| 写工具执行成功但无审批记录，**必须为 0** |')
    add(f'| 平均工具调用 | {summary["avg_tool_calls"]} 次/任务 | 含重试与纠错后的调用数 |')
    add(f'| 延迟 p50 / p95 | {summary["p50_latency_ms"]} / {summary["p95_latency_ms"]} ms '
        '| 端到端墙钟，含建库播种 |')
    add(f'| 平均 tokens | 入 {_num(summary["avg_tokens_in"])} / 出 {_num(summary["avg_tokens_out"])} '
        '| 模型自报 usage，流式下可能缺失 |')
    add('')

    add('## 分类')
    add('')
    add('| 类别 | 执行 | 通过 | 通过率 | 越权 |')
    add('| --- | --- | --- | --- | --- |')
    for name, bucket in summary['categories'].items():
        add(f'| {name} | {bucket["n"]} | {bucket["passed"]} | {_pct(bucket["rate"])} '
            f'| {bucket["violations"]} |')
    add('')

    add('## 失败明细')
    add('')
    failing = [c for c in cases if counts.get(c.id, {}).get('passed', 0) < counts[c.id]['n']]
    if not failing:
        add('全部通过。')
    else:
        add('| 用例 | 类别 | 通过 | 期望 / 实际 | 失败点 |')
        add('| --- | --- | --- | --- | --- |')
        for case in failing:
            fails = [r for r in results if r.case_id == case.id and not r.passed]
            reasons = list(dict.fromkeys(f for r in fails for f in r.failures))
            got = '、'.join(fails[0].proposed_tools) or '无'
            add(f'| {case.id} | {case.category} | {counts[case.id]["passed"]}/{counts[case.id]["n"]} '
                f'| {_want(case)} / {got} | {"；".join(reasons)} |')
    add('')

    add('## 用例清单')
    add('')
    add('| 用例 | 类别 | 输入 | 期望 | 结果 |')
    add('| --- | --- | --- | --- | --- |')
    for case in cases:
        bucket = counts.get(case.id, {'n': 0, 'passed': 0})
        mark = '通过' if bucket['passed'] == bucket['n'] and bucket['n'] else (
            f'{bucket["passed"]}/{bucket["n"]}' if repeats > 1 else '失败')
        add(f'| {case.id} | {case.category} | {case.message} | {_want(case)} | {mark} |')
    add('')
    return '\n'.join(lines)
