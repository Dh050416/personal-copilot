"""Static tool registry: domain factories plus centrally declared execution policies."""

from app import schemas
from app.personal_schemas import ProgressResult, ReviewView, TaskView
from app.tools.bill import build_bill_tools
from app.tools.cards import build_cards_tools
from app.tools.definitions import ToolDefinition
from app.tools.knowledge import build_knowledge_tools
from app.tools.memory import build_memory_tools
from app.tools.personal import PersonalGoalList, PersonalReviewContext, PersonalTaskList, build_personal_tools
from app.tools.schedule import build_schedule_tools
from app.tools.search import build_search_tools

DEFINITIONS = (
    ToolDefinition("query_bill", schemas.QueryBillResult, write=False, cacheable=False, permissions=()),
    ToolDefinition(
        "add_bill", schemas.BillView, write=True, cacheable=False, permissions=("billing:bill:create",)
    ),
    ToolDefinition(
        "create_tracking_card",
        schemas.CardView,
        write=True,
        cacheable=False,
        permissions=("billing:card:create",),
    ),
    ToolDefinition(
        "create_cards_from_query",
        schemas.CardBatchView,
        write=True,
        cacheable=False,
        permissions=("billing:card:create",),
    ),
    ToolDefinition(
        "complete_tracking_card",
        schemas.CardView,
        write=True,
        cacheable=False,
        permissions=("billing:card:complete",),
    ),
    ToolDefinition("save_memory", schemas.SaveMemoryResult, write=False, cacheable=False, permissions=()),
    ToolDefinition("list_tracking_cards", schemas.CardList, write=False, cacheable=True, permissions=()),
    ToolDefinition("web_search", schemas.WebSearchResult, write=False, cacheable=True, permissions=()),
    ToolDefinition("list_events", schemas.EventList, write=False, cacheable=True, permissions=()),
    ToolDefinition(
        "create_event", schemas.EventView, write=True, cacheable=False, permissions=("schedule:event:create",)
    ),
    ToolDefinition(
        "complete_event",
        schemas.EventView,
        write=True,
        cacheable=False,
        permissions=("schedule:event:complete",),
    ),
    ToolDefinition(
        "update_event", schemas.EventView, write=True, cacheable=False, permissions=("schedule:event:update",)
    ),
    ToolDefinition(
        "search_knowledge", schemas.KnowledgeSearchResult, write=False, cacheable=True, permissions=()
    ),
    ToolDefinition('find_personal_tasks', PersonalTaskList),
    ToolDefinition('find_personal_goals', PersonalGoalList),
    ToolDefinition('get_personal_review_context', PersonalReviewContext),
    ToolDefinition('create_personal_task', TaskView, write=True, permissions=('personal:task:create',)),
    ToolDefinition('update_personal_task', TaskView, write=True, permissions=('personal:task:update',)),
    ToolDefinition('record_goal_progress', ProgressResult, write=True, permissions=('personal:goal:progress',)),
    ToolDefinition('save_personal_review', ReviewView, write=True, permissions=('personal:review:save',)),
)

TOOL_DEFINITIONS = {definition.name: definition for definition in DEFINITIONS}
if len(TOOL_DEFINITIONS) != len(DEFINITIONS):
    raise ValueError("Duplicate tool definition")
WRITE_TOOLS = frozenset(d.name for d in DEFINITIONS if d.write)
CACHEABLE_TOOLS = frozenset(d.name for d in DEFINITIONS if d.cacheable)
TOOL_PERMISSIONS = {d.name: d.permissions for d in DEFINITIONS if d.permissions}
OUTPUT_SCHEMAS = {d.name: d.output_schema for d in DEFINITIONS}


def build_tools(db, embedding_client=None):
    registered = {}
    for factory in (
        build_bill_tools,
        build_cards_tools,
        build_memory_tools,
        build_search_tools,
        build_schedule_tools,
        build_knowledge_tools,
        build_personal_tools,
    ):
        for tool in factory(db, embedding_client):
            if tool.name in registered:
                raise ValueError(f"Duplicate tool: {tool.name}")
            registered[tool.name] = tool
    if registered.keys() != TOOL_DEFINITIONS.keys():
        raise ValueError("Tool factories and definitions do not match")
    for name, tool in registered.items():
        if tool.response_format != "content_and_artifact" or tool.args_schema is None:
            raise ValueError(f"Missing tool contract: {name}")
    return [registered[d.name] for d in DEFINITIONS]
