"""长期记忆只读/管理 REST 端点：供前端「设置与记忆」页直接读写 agent_memory。

与 Agent 工具 save_memory 的区别：
- save_memory 走 Agent 图 + 模型自主决策，写的是模型认为值得记住的偏好；
- 本端点走直查，让用户能**查看、手动新增、删除**记忆，补上「模型记错了怎么改」的闭环。
两者共用同一张表，写入后下次对话即全量注入 system（无额外同步步骤）。
"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.data.models import AgentMemory, now_iso
from app.schemas import MemoryItem, MemoryList, UpsertMemoryRequest

router = APIRouter(prefix='/api/memory', tags=['memory'])


def _view(m: AgentMemory) -> MemoryItem:
    return MemoryItem(key=m.key, value=m.value, updated_at=m.updated_at)


@router.get('', response_model=MemoryList)
async def list_memory(request: Request):
    """列出全部长期记忆（按更新时间倒序），供管理界面展示。"""
    async with request.app.state.db.sessions() as session:
        rows = list(await session.scalars(
            select(AgentMemory).where(AgentMemory.user_id == request.state.user_id)
            .order_by(AgentMemory.updated_at.desc())))
    return MemoryList(items=[_view(m) for m in rows])


@router.put('', response_model=MemoryItem)
async def upsert_memory(payload: UpsertMemoryRequest, request: Request):
    """新增/覆盖一条记忆（key 冲突时覆盖 value，与 save_memory 语义一致）。"""
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            existing = await session.get(AgentMemory, (request.state.user_id, payload.key))
            if existing is not None:
                existing.value = payload.value
                existing.updated_at = now_iso()
                obj = existing
            else:
                obj = AgentMemory(user_id=request.state.user_id, key=payload.key, value=payload.value, updated_at=now_iso())
                session.add(obj)
    return _view(obj)


@router.delete('/{key}')
async def delete_memory(key: str, request: Request):
    """删除一条长期记忆（键含中文等特殊字符，走路径参数由框架解码）。"""
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            existing = await session.get(AgentMemory, (request.state.user_id, key))
            if existing is None:
                raise HTTPException(404, '记忆不存在')
            await session.delete(existing)
    return {'ok': True, 'key': key}
