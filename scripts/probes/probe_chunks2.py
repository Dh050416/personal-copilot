# -*- coding: utf-8 -*-
"""Print raw chunks with AsyncSqliteSaver during resume."""
import asyncio
import pathlib
import tempfile

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.agent.graph import initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database


class FakeChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def main():
    db = Database(pathlib.Path(tempfile.mkdtemp()) / 'x.db')
    await db.initialize()
    tmp = pathlib.Path(tempfile.mkdtemp()) / 'ckpt.db'
    cm = AsyncSqliteSaver.from_conn_string(str(tmp))
    checkpointer = await cm.__aenter__()
    tools = build_tools(db)
    llm = FakeChatModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'create_tracking_card',
                                           'args': {'category': '餐饮', 'purpose': '报销'},
                                           'id': 'c1', 'type': 'tool_call'}]),
        AIMessage(content='卡片已创建。'),
    ]))
    graph = make_graph(llm, tools, WRITE_TOOLS, checkpointer)
    config = {'configurable': {'thread_id': 'r1'}}
    await graph.ainvoke(initial_input('r1', '建卡'), config)
    snapshot = await graph.aget_state(config)
    aid = snapshot.tasks[0].interrupts[0].value['approval_id']

    print('--- resume raw chunks (sqlite saver) ---')
    async for chunk in graph.astream(Command(resume={'decision': 'approve', 'approval_id': aid}),
                                     config, stream_mode='updates'):
        for node_name, payload in chunk.items():
            events = payload.get('events') if isinstance(payload, dict) else None
            print(f'chunk node={node_name} events={[e["type"] for e in events] if events else None} '
                  f'final={payload.get("final") if isinstance(payload, dict) else None}')
    await cm.__aexit__(None, None, None)
    await db.close()


asyncio.run(main())
