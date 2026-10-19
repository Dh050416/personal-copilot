from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.data.models import TrackingCard
from app.data.views import card_view as full_card_view
from app.schemas import CardList, CardView

router = APIRouter(prefix='/api/cards', tags=['cards'])


@router.get('', response_model=CardList)
async def list_cards(request: Request,
                     status: str | None = None,
                     limit: int = 200,
                     offset: int = 0):
    """只读直查事务卡片（不走 Agent 对话，省 token）：按 id 倒序。
    status ∈ active|done（缺省返回全部）；limit 上限 500。"""
    if status not in (None, 'active', 'done'):
        raise HTTPException(400, 'status 仅支持 active|done')
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    filters = []
    if status is not None:
        filters.append(TrackingCard.status == status)
    async with request.app.state.db.sessions() as session:
        cards = list(await session.scalars(
            select(TrackingCard).where(*filters)
            .order_by(TrackingCard.id.desc()).offset(offset).limit(limit)))
    return CardList(items=[CardView(**full_card_view(c)) for c in cards])


@router.post('/{card_id}/complete')
async def complete_card(card_id: int, request: Request):
    """将跟踪卡标记为已完成（对应看板拖拽到「已完成」区）。
    幂等：重复提交同一张卡仍返回成功，不重复产生副作用。
    """
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            card = await session.get(TrackingCard, card_id)
            if card is None:
                raise HTTPException(404, '卡片不存在')
            if card.status != 'done':
                card.status = 'done'
            view = full_card_view(card)
    return view


@router.delete('/{card_id}')
async def delete_card(card_id: int, request: Request):
    """删除「已完成」状态的事务卡片（清理看板已完成区；不可恢复）。
    仅允许删除 status=done 的卡片，防止误删仍处于激活跟踪中的卡。
    """
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            card = await session.get(TrackingCard, card_id)
            if card is None:
                raise HTTPException(404, '卡片不存在')
            if card.status != 'done':
                raise HTTPException(400, '仅可删除「已完成」状态的卡片')
            await session.delete(card)
    return {'ok': True, 'id': card_id}
