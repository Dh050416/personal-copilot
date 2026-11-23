"""memory tool adapters; business persistence lives in services."""

from langchain_core.tools import tool

from app.data.models import AgentMemory, now_utc
from app.schemas import (
    SaveMemoryArgs,
    SaveMemoryResult,
)
from app.tools.context import current_user_id


def build_memory_tools(db, embedding_client=None):
    @tool("save_memory", args_schema=SaveMemoryArgs, response_format="content_and_artifact")
    async def save_memory(**kwargs):
        """保存长期记忆：记录用户明确表达的偏好或事实（如「以后报销默认其他」「章鱼大王是常去的餐饮店」）。

        只记录用户当面明确表达的内容，不得推测、归纳或编造；
        key 用简短可读的中文短语；重复保存同一 key 会覆盖更新。
        """
        args = SaveMemoryArgs.model_validate(kwargs)
        user_id = current_user_id()
        async with db.sessions() as session:
            async with session.begin():
                existing = await session.get(AgentMemory, (user_id, args.key))
                if existing is not None:
                    existing.value = args.value
                    existing.updated_at = now_utc()
                else:
                    session.add(
                        AgentMemory(user_id=user_id, key=args.key, value=args.value, updated_at=now_utc())
                    )
        return f"已记住：{args.key} = {args.value}", SaveMemoryResult(key=args.key, value=args.value)

    return [save_memory]
