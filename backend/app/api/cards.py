from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.data.models import TrackingCard
from app.data.views import card_view as full_card_view
from app.schemas import CardList, CardView, CreateTrackingCardArgs
from app.services.card_service import create_card

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
            select(TrackingCard).where(TrackingCard.user_id == request.state.user_id, *filters)
            .order_by(TrackingCard.id.desc()).offset(offset).limit(limit)))
    return CardList(items=[CardView(**full_card_view(c)) for c in cards])


@router.post('', response_model=CardView)
async def create_new_card(payload: CreateTrackingCardArgs, request: Request):
    """看板直连新建跟踪卡（确定性录入，不走 Agent/审批）。

    不关联账单（bill_id 留空）时即纯手动卡：用途/分类/金额/对方/备注由用户显式填写；
    调用 create_card 服务层复用同一套快照与幂等（invocation_id 自动生成）。
    """
    return await create_card(request.app.state.db, request.state.user_id, payload)


@router.post('/{card_id}/complete')
async def complete_card(card_id: int, request: Request):
    """将跟踪卡标记为已完成（对应看板拖拽到「已完成」区）。
    幂等：重复提交同一张卡仍返回成功，不重复产生副作用。
    """
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            card = await session.get(TrackingCard, card_id)
            if card is None or card.user_id != request.state.user_id:
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
            if card is None or card.user_id != request.state.user_id:
                raise HTTPException(404, '卡片不存在')
            if card.status != 'done':
                raise HTTPException(400, '仅可删除「已完成」状态的卡片')
            await session.delete(card)
    return {'ok': True, 'id': card_id}
