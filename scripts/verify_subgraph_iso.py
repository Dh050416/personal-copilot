"""子图状态隔离验证：证明子图（SubGraphState）与主图（AgentState）按 channel 名映射后——
1) 主图调度字段（active_agent/skills/has_pending）子图运行后保持原值不被清空/污染；
2) 子图私有字段（_bill_rows 等）不进入主图 state；
3) 输出映射正常：messages/events/final/last_query/tool_calls_done 写回主图。
"""
import asyncio
import importlib.util
import pathlib
import sys
import tempfile

sys.path.insert(0, 'D:/LifeManage/backend')

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from app.agent.graph import initial_input, make_graph  # noqa: E402
from app.agent.tools import WRITE_TOOLS, build_tools  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.models import Bill  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'tgr', 'D:/LifeManage/backend/tests/test_graph_routing.py')
tgr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tgr)


async def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    db = Database(tmp / 'iso.db')
    await db.initialize()
    tools = build_tools(db)

    # 造一条 7 月账单，供 query_bill 命中
    async with db.sessions() as session:
        async with session.begin():
            session.add(Bill(
                tx_no='T1', tx_time='2026-07-01T10:00:00', merchant='测试商户',
                amount_cents=1234, direction='expense', transaction_type='商户消费',
                category='餐饮', source='test', import_batch_id='b1',
                source_sheet='s1', source_row='r1'))

    llm = tgr.fake_llm(
        tgr.tool_call_message('query_bill', {'start_date': '2026-07-01', 'end_date': '2026-07-31'}),
        AIMessage(content='查询完成。'),
    )
    graph = make_graph(llm, tools, WRITE_TOOLS, MemorySaver())
    config = {'configurable': {'thread_id': 'iso-1'}}
    await tgr.run_to_end(graph, initial_input('iso-1', '查询七月份账单'), config)
    snap = await graph.aget_state(config)
    vals = snap.values

    print('=== 输出映射（子图 -> 主图）===')
    print('final  =', repr(vals.get('final')))
    print('messages 共', len(vals.get('messages', [])), '条，含 ToolMessage:',
          any(type(m).__name__ == 'ToolMessage' for m in vals.get('messages', [])))
    print('last_query =', vals.get('last_query'))
    print('tool_calls_done =', vals.get('tool_calls_done'))
    print('=== 主图调度字段保持（子图不可见、未污染）===')
    print('active_agent =', vals.get('active_agent'))
    print('skills =', vals.get('skills'))
    print('has_pending =', vals.get('has_pending'))
    print('=== 子图私有字段隔离 ===')
    print('_bill_rows 在主图 state:', '_bill_rows' in vals)
    print('_search_items 在主图 state:', '_search_items' in vals)

    await db.close()


asyncio.run(main())
