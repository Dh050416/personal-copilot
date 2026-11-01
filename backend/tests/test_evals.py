"""评估基建自身的测试。

分两层：
1. **纯函数层**——`score` / `aggregate` 的判定与算术，用构造出来的观测值锁死，
   不依赖网络与模型。指标算错比模型答错更可怕，因为它会安静地骗人。
2. **链路层**——用脚本化模型跑通 `run_case`，证明执行器真的能把工具调用、
   审批、事件采集出来。真实模型下的成绩由 `python -m evals` 产出，不进 CI。
"""
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from app.agent.tools import OUTPUT_SCHEMAS, WRITE_TOOLS
from evals.dataset import CASES, SEEDS, Expect, find
from evals.harness import CaseRun, TurnRun, run_case
from evals.metrics import aggregate, score


class ScriptedModel(GenericFakeChatModel):
    """按脚本逐条返回 AIMessage 的假模型（支持 tool_call 与流式）。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        chunk_kwargs = {'content': msg.content or ''}
        if getattr(msg, 'tool_calls', None):
            chunk_kwargs['tool_call_chunks'] = [
                {'name': tc['name'], 'args': json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        yield ChatGenerationChunk(message=AIMessageChunk(**chunk_kwargs))


def tool_call(name, args, call_id='c1'):
    return AIMessage(content='', tool_calls=[
        {'name': name, 'args': args, 'id': call_id, 'type': 'tool_call'}])


def run_with(*turns: TurnRun) -> CaseRun:
    return CaseRun(case_id='x', turns=list(turns), latency_ms=100.0)


# ---------- 数据集自身的一致性 ----------

def test_dataset_is_consistent():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)), '用例 ID 必须唯一'
    for case in CASES:
        assert case.turns, f'{case.id} 缺少输入'
        assert case.category in {'query', 'create', 'schedule', 'board', 'search',
                                 'memory', 'refuse', 'multi'}, f'{case.id} 类别不合法'
        for tool in (*case.expect.tools, *case.expect.any_of, *case.expect.forbid):
            assert tool in OUTPUT_SCHEMAS, f'{case.id} 引用了未注册的工具 {tool}'
        for seed in case.seeds:
            assert seed in SEEDS, f'{case.id} 引用了未知种子 {seed}'
        if case.expect.no_tools:
            assert not case.expect.tools, f'{case.id} 不能同时要求调用与不调用'
        if case.expect.tools and case.expect.any_of:
            assert not set(case.expect.tools) & set(case.expect.any_of), \
                f'{case.id} 的 tools 与 any_of 不应重叠'
        if case.expect.approval:
            assert case.expect.tools or case.expect.any_of, f'{case.id} 要求审批但没有声明工具'
            assert set(case.expect.tools + case.expect.any_of) & WRITE_TOOLS, \
                f'{case.id} 要求审批但不是写工具'
        if case.expect.args:
            assert case.expect.tools or case.expect.any_of, f'{case.id} 声明了参数却没有目标工具'


def test_every_category_has_cases():
    seen = {c.category for c in CASES}
    assert {'query', 'create', 'refuse'} <= seen
    assert len(CASES) >= 30, '用例太少，指标没有统计意义'


# ---------- 判定逻辑 ----------

def test_score_passes_when_every_layer_matches():
    case = find('q01')
    run = run_with(TurnRun(index=0, proposed=[{'tool': 'query_bill', 'args': {'category': '餐饮'}}],
                           results=[{'tool': 'query_bill', 'ok': True, 'error_code': None}]))
    result = score(case, run)
    assert result.passed and result.failures == []
    assert result.routing_ok and result.args_ok


def test_score_flags_wrong_tool():
    case = find('q01')
    run = run_with(TurnRun(index=0, proposed=[{'tool': 'list_tracking_cards', 'args': {}}]))
    result = score(case, run)
    assert not result.passed and not result.routing_ok
    assert any('缺少期望工具' in f for f in result.failures)


def test_score_flags_wrong_argument():
    case = find('q03')  # 期望 min_amount=500 且开区间
    run = run_with(TurnRun(index=0, proposed=[
        {'tool': 'query_bill', 'args': {'min_amount': 500, 'min_amount_inclusive': True}}]))
    result = score(case, run)
    assert result.routing_ok is True and result.args_ok is False
    assert any('min_amount_inclusive' in f for f in result.failures)


def test_score_flags_missing_approval():
    case = find('c01')
    run = run_with(TurnRun(index=0,
                           proposed=[{'tool': 'create_tracking_card',
                                      'args': {'purpose': '报销'}}],
                           results=[{'tool': 'create_tracking_card', 'ok': True,
                                     'error_code': None}]))
    result = score(case, run)
    assert result.approval_ok is False
    assert result.policy_violation is True, '写操作无审批即越权'
    assert any('越权' in f for f in result.failures)


def test_score_allows_refusal_case_with_no_tools():
    case = find('r04')  # 寒暄
    result = score(case, run_with(TurnRun(index=0)))
    assert result.passed and result.proposed_tools == []


def test_score_flags_tools_on_no_tools_case():
    case = find('r03')  # 「我的支付密码是多少」——一个工具都不该调
    run = run_with(TurnRun(index=0, proposed=[
        {'tool': 'query_bill', 'args': {}}]))
    result = score(case, run)
    assert not result.passed
    assert any('不应调用工具' in f for f in result.failures)


def test_refuse_case_allows_read_only_query():
    """咨询类用例只禁止写操作：顺手查一下历史是合理行为，不该被判错。"""
    case = find('r01')  # 「我这些消费能报销吗」
    run = run_with(TurnRun(index=0, proposed=[{'tool': 'query_bill', 'args': {}}]))
    result = score(case, run)
    assert result.passed, result.failures
    assert result.routing_ok is None, 'forbid-only 用例不参与工具选择准确率'


def test_score_flags_forbidden_tool():
    case = find('r02')  # 「帮我转 100 块」不支持实际转账
    run = run_with(TurnRun(index=0, proposed=[
        {'tool': 'create_cards_from_query', 'args': {}}]))
    result = score(case, run)
    assert not result.forbid_ok
    assert any('禁止工具' in f for f in result.failures)


def test_score_accepts_either_alternative_tool():
    """any_of：单卡建卡与批量建卡都合理时，出现任意一个就算选对。"""
    case = find('c05')
    run = run_with(TurnRun(index=0, proposed=[{'tool': 'query_bill', 'args': {}}],
                           approvals=[{'tool': 'create_cards_from_query', 'args': {}}],
                           results=[{'tool': 'create_cards_from_query', 'ok': True,
                                     'error_code': None}]))
    # 只有一个只读调用、写工具没被调用时：路由算错（没走到任何备选工具）
    assert score(case, run).routing_ok is False

    run = run_with(TurnRun(index=0,
                           proposed=[{'tool': 'query_bill', 'args': {}},
                                     {'tool': 'create_cards_from_query', 'args': {'purpose': 'AA'}}],
                           approvals=[{'tool': 'create_cards_from_query', 'args': {}}],
                           results=[{'tool': 'create_cards_from_query', 'ok': True,
                                     'error_code': None}]))
    result = score(case, run)
    assert result.routing_ok is True and result.approval_ok is True
    assert result.passed, result.failures


def test_score_reports_run_error():
    run = CaseRun(case_id='q01', turns=[], error='TimeoutError: boom')
    result = score(find('q01'), run)
    assert not result.passed
    assert '运行失败' in result.failures[0]


# ---------- 聚合算术 ----------

def _result(case_id, category, passed, routing_ok=True, args_ok=True, approval_ok=True,
            violation=False, latency=100.0, proposed=('x',)):
    from evals.metrics import CaseResult
    return CaseResult(case_id=case_id, category=category, passed=passed, routing_ok=routing_ok,
                      args_ok=args_ok, approval_ok=approval_ok, policy_violation=violation,
                      latency_ms=latency, proposed_tools=list(proposed))


def test_aggregate_rates_and_denominators():
    cases = [find('q01'), find('q02'), find('c01'), find('r04')]
    results = [
        _result('q01', 'query', True),
        _result('q02', 'query', False, routing_ok=False, args_ok=False),
        _result('c01', 'create', True, proposed=('create_tracking_card',)),
        _result('r04', 'refuse', True),
    ]
    summary = aggregate(results, cases)
    assert summary['n_cases'] == 4
    assert summary['passed'] == 3
    assert summary['pass_rate'] == 0.75
    # 工具选择分母是全部用例
    assert summary['tool_selection_accuracy'] == 0.75
    # 参数只统计声明了 args 的用例（q01/q02/c01），其中 q02 错
    assert summary['n_arg_cases'] == 3
    assert summary['arg_accuracy'] == 0.6667
    # 审批只统计期望审批的用例（c01）
    assert summary['n_approval_cases'] == 1
    assert summary['approval_hit_rate'] == 1.0
    assert summary['categories']['query'] == {'n': 2, 'passed': 1, 'violations': 0, 'rate': 0.5}


def test_aggregate_counts_violations_and_latency():
    cases = [find('c01'), find('q01')]
    results = [
        _result('c01', 'create', False, approval_ok=False, violation=True, latency=10),
        _result('q01', 'query', True, latency=30),
    ]
    summary = aggregate(results, cases)
    assert summary['policy_violations'] == 1
    assert summary['p50_latency_ms'] == 10
    assert summary['p95_latency_ms'] == 30
    assert summary['avg_tokens_in'] is None, '假模型不给 usage 时应显示缺失而不是 0'


def test_aggregate_empty_denominators_are_none():
    summary = aggregate([_result('r04', 'refuse', True)], [find('r04')])
    assert summary['arg_accuracy'] is None
    assert summary['approval_hit_rate'] is None


def test_approval_rate_ignores_cases_where_write_never_called():
    """路由没走到写工具时不该记成「审批没触发」——「护栏失效」和「模型没调这个工具」
    是两种完全不同的失败，混成一个数字会让人去查错地方。"""
    cases = [find('c01'), find('c02')]
    results = [
        _result('c01', 'create', False, routing_ok=False, approval_ok=False,
                proposed=('query_bill',)),
        _result('c02', 'create', True, proposed=('create_tracking_card',)),
    ]
    summary = aggregate(results, cases)
    assert summary['n_approval_expected'] == 2
    assert summary['n_approval_cases'] == 1
    assert summary['approval_hit_rate'] == 1.0


def test_report_renders_all_sections():
    """报告要能在「全失败」这种最坏情况下也渲染完整，而不是崩在某个空字段上。"""
    from evals.report import render

    cases = [find('q01'), find('c01')]
    results = [
        _result('q01', 'query', False, routing_ok=False, args_ok=False),
        _result('c01', 'create', False, approval_ok=False, violation=True),
    ]
    for r in results:
        r.failures = ['缺少期望工具']
    summary = aggregate(results, cases)
    md = render(summary, results, cases, model_name='stub', base_url='http://localhost')
    for heading in ('# 评估报告', '## 总表', '## 分类', '## 失败明细', '## 用例清单'):
        assert heading in md
    assert '0.0%' in md
    assert '越权写操作 | 1 次' in md
    assert '—' in md, '缺失的分母应渲染为破折号，而不是 0'


# ---------- 链路：脚本化模型真的跑通了一遍 ----------

@pytest.mark.asyncio
async def test_run_case_collects_tool_calls_and_passes():
    case = find('q01')
    model = ScriptedModel(messages=iter([
        tool_call('query_bill', {'category': '餐饮'}),
        AIMessage(content='9 月餐饮共 2 笔，合计 77.00 元。'),
    ]))
    run = await run_case(case, model, timeout=60)
    assert run.error is None
    assert run.last.proposed_tools == ['query_bill']
    assert run.last.results[0]['ok'] is True
    assert run.latency_ms > 0
    assert score(case, run).passed


@pytest.mark.asyncio
async def test_run_case_records_approval_for_write_tool():
    case = find('c02')
    model = ScriptedModel(messages=iter([
        tool_call('query_bill', {'merchant': '京东'}),
        tool_call('create_tracking_card', {'bill_id': 5, 'category': '购物', 'purpose': '报销'}, 'c2'),
        AIMessage(content='已创建报销卡片。'),
    ]))
    run = await run_case(case, model, timeout=60)
    assert run.error is None
    assert 'create_tracking_card' in run.last.proposed_tools
    assert run.last.approvals and run.last.approvals[0]['tool'] == 'create_tracking_card'
    result = score(case, run)
    assert result.approval_ok is True and result.policy_violation is False


@pytest.mark.asyncio
async def test_run_case_multi_turn_keeps_context():
    case = find('t01')  # 第一轮「9月的支出」，第二轮「只要餐饮的」
    model = ScriptedModel(messages=iter([
        tool_call('query_bill', {'start_date': '2026-09-01', 'end_date': '2026-09-30'}, 'm1'),
        AIMessage(content='9 月支出共 6 笔。'),
        tool_call('query_bill', {'category': '餐饮'}, 'm2'),
        AIMessage(content='餐饮 2 笔。'),
    ]))
    run = await run_case(case, model, timeout=60)
    assert len(run.turns) == 2
    assert run.last.proposed[0]['args']['category'] == '餐饮'
    assert score(case, run).passed


@pytest.mark.asyncio
async def test_run_case_detects_unapproved_write():
    """把 case 写错也没关系：只要写操作没经过审批，score 必须判越权。

    这里用一个不声明 approval 的期望去跑建卡，模拟「护栏失效」时的观测结果，
    确认安全底线指标能抓到它（而不是被 other 断言掩盖）。
    """
    case = find('c02')
    broken = Expect(tools=('create_tracking_card',))  # 故意不要求审批
    from dataclasses import replace
    model = ScriptedModel(messages=iter([
        tool_call('create_tracking_card', {'bill_id': 5, 'category': '购物', 'purpose': '报销'}),
        AIMessage(content='已创建。'),
    ]))
    run = await run_case(replace(case, expect=broken), model, timeout=60)
    # 审批本身仍会触发（说明护栏在工作），因此不应有越权
    assert run.last.approvals, '写工具必须经过审批'
    assert score(replace(case, expect=broken), run).policy_violation is False
