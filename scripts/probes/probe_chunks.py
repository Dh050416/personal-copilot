# -*- coding: utf-8 -*-
"""Print raw chunk structure during resume with updates mode."""
import asyncio
import pathlib
import tempfile

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
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
    tools = build_tools(db)
    llm = FakeChatModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'create_tracking_card',
                                           'args': {'category': '餐饮', 'purpose': '报销'},
                                           'id': 'c1', 'type': 'tool_call'}]),
        AIMessage(content='卡片已创建。'),
    ]))
    graph = make_graph(llm, tools, WRITE_TOOLS, MemorySaver())
    config = {'configurable': {'thread_id': 'r1'}}
    await graph.ainvoke(initial_input('r1', '建卡'), config)
    snapshot = await graph.aget_state(config)
    aid = snapshot.tasks[0].interrupts[0].value['approval_id']

    print('--- resume raw chunks ---')
    async for chunk in graph.astream(Command(resume={'decision': 'approve', 'approval_id': aid}),
                                     config, stream_mode='updates'):
        for node_name, payload in chunk.items():
            events = payload.get('events') if isinstance(payload, dict) else None
            print(f'chunk node={node_name} events={[e["type"] for e in events] if events else None} '
                  f'final={payload.get("final") if isinstance(payload, dict) else None}')
    await db.close()


asyncio.run(main())
