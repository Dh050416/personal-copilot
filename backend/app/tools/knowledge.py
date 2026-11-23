"""knowledge tool adapters; business persistence lives in services."""

from langchain_core.tools import tool

from app.errors import ToolError
from app.schemas import (
    KnowledgeSearchItem,
    KnowledgeSearchResult,
    SearchKnowledgeArgs,
)
from app.services.knowledge.retrieval import format_results_for_model, retrieve
from app.tools.context import current_user_id


def build_knowledge_tools(db, embedding_client=None):
    @tool("search_knowledge", args_schema=SearchKnowledgeArgs, response_format="content_and_artifact")
    async def search_knowledge(**kwargs):
        """检索个人知识库文档，返回最相关的片段。当用户问到已上传文档/资料/政策/笔记内容时使用。

        适合"我上传的XX文档怎么说的"、"医保报销政策"、"会议纪要里提到的"等需要从已上传文档
        获取信息的问题。账单/收支类查询不要用本工具，用 query_bill。
        如果检索结果为空，如实告诉用户知识库中没有相关内容，不要编造。
        """
        args = SearchKnowledgeArgs.model_validate(kwargs)
        if embedding_client is None:
            raise ToolError(
                "KNOWLEDGE_UNAVAILABLE",
                "知识库检索服务未配置 embedding 模型，请联系管理员。",
                stage="execution",
                retryable=False,
            )
        user_id = current_user_id()
        results = await retrieve(db, user_id, args.query, embedding_client, top_k=args.top_k)
        summary = format_results_for_model(results)
        artifact = KnowledgeSearchResult(
            query=args.query,
            count=len(results),
            items=[
                KnowledgeSearchItem(
                    chunk_id=r.chunk_id,
                    content=r.content,
                    heading_path=r.heading_path,
                    page=r.page,
                    document_id=r.document_id,
                    distance=round(r.distance, 4),
                )
                for r in results
            ],
        )
        return summary, artifact

    return [search_knowledge]
