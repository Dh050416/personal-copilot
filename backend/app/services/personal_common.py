from collections import defaultdict
from collections.abc import Iterable
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import now_utc

T = TypeVar('T')


async def owned(session: AsyncSession, model: type[T], user_id: int, item_id: int, *, lock=False) -> T:
    query = select(model).where(model.id == item_id, model.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise HTTPException(404, '记录不存在')
    return row


def require_version(row, version: int) -> None:
    if row.version != version:
        raise HTTPException(409, '该记录已变更，请刷新后重试')


def touch(row, at=None) -> None:
    row.version += 1
    row.updated_at = at or now_utc()


def group_by(rows: Iterable[T], attribute: str) -> dict[int, list[T]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[getattr(row, attribute)].append(row)
    return grouped
