from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy import select

from app.agent.graph import ToolResultCache, make_graph
from app.agent.tools import CACHEABLE_TOOLS, OUTPUT_SCHEMAS, WRITE_TOOLS, build_tools
from app.api.bills import router as bills_router
from app.api.cards import router as cards_router
from app.api.chat import router as chat_router
from app.api.events import router as events_router
from app.config import Settings
from app.data.db import Database
from app.data.models import AgentMemory


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / 'personal_copilot.db')
    await db.initialize()
    checkpoint_cm = AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.db'))
    checkpointer = await checkpoint_cm.__aenter__()
    model = ChatOpenAI(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model=settings.model_name,
        temperature=0,
        max_tokens=settings.model_output_tokens,
        timeout=settings.model_timeout_seconds,
        max_retries=2,
    )

    async def load_memory_text() -> str | None:
        """长期记忆全量加载（agent_memory 表 → 注入文本）。规模小（10-30 条），全量优于检索。"""
        async with db.sessions() as session:
            rows = list(await session.scalars(
                select(AgentMemory).order_by(AgentMemory.updated_at.desc())))
        if not rows:
            return None
        return '；'.join(f'{r.key}={r.value}' for r in rows)

    tool_cache = ToolResultCache(settings.tool_result_cache_ttl) if settings.tool_result_cache_ttl else None
    graph = make_graph(model, build_tools(db), WRITE_TOOLS, checkpointer,
                       memory_loader=load_memory_text,
                       tool_timeout=settings.tool_timeout_seconds,
                       model_retries=settings.model_retries,
                       retry_backoff=settings.retry_backoff_seconds,
                       tool_retries=settings.tool_retries,
                       output_schemas=OUTPUT_SCHEMAS,
                       context_max_chars=settings.context_max_chars,
                       context_max_lines=settings.context_max_lines,
                       tool_cache=tool_cache,
                       cacheable_tools=CACHEABLE_TOOLS)
    app.state.settings = settings
    app.state.db = db
    app.state.checkpointer = checkpointer
    app.state.graph = graph
    app.state.tool_cache = tool_cache
    # 线程运行时状态：thread_id → {active, pending(追加输入队列), waiting_approval}
    # 检查点注入（执行中可追加任务）的队列宿主；单进程内存即可（本机单实例）
    app.state.thread_runtime = {}
    try:
        yield
    finally:
        await checkpoint_cm.__aexit__(None, None, None)
        await db.close()


app = FastAPI(title='PersonalCopilot', lifespan=lifespan)
# CORS 白名单在应用创建时就要确定（此时 lifespan 尚未运行），单独读一次配置。
# 部署到非默认端口/域名时设置环境变量 CORS_ORIGINS（JSON 数组）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=Settings().cors_origins,
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
    allow_headers=['*'],
)
app.include_router(chat_router)
app.include_router(bills_router)
app.include_router(cards_router)
app.include_router(events_router)


@app.get('/health')
async def health():
    return {'status': 'ok', 'model_provider': app.state.settings.model_provider}
