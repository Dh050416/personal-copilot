"""探针：连续两次带 tcc 的 astream 调用。"""
import asyncio
import json
import traceback

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class FakeChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        tool_call_chunks = None
        if getattr(msg, 'tool_calls', None):
            tool_call_chunks = [
                {'name': tc['name'], 'args': json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        yield ChatGenerationChunk(message=AIMessageChunk(
            content=msg.content or '', tool_call_chunks=tool_call_chunks))


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


async def one_call(bound, label):
    chunks = []
    try:
        async for chunk in bound.astream([AIMessage(content='查')]):
            chunks.append(chunk)
        merged = chunks[0] if chunks else None
        for c in chunks[1:]:
            merged = merged + c
        print(f'{label}: ok, n_chunks={len(chunks)}',
              'content=', repr(getattr(merged, 'content', None)),
              'tool_calls=', getattr(merged, 'tool_calls', None))
    except Exception:
        print(f'{label}: FAILED')
        traceback.print_exc()


async def main():
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    bound = llm.bind_tools([])
    await one_call(bound, 'call1')
    await one_call(bound, 'call2')
    try:
        print('after both: next =', repr(getattr(next(llm.messages), 'content', None)))
    except StopIteration:
        print('after both: EMPTY')


asyncio.run(main())
