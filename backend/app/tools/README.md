# Agent tools

- `registry.py`: domain factories, registered names, result schemas, approval/cache/permission policies.
- `definitions.py`: immutable `ToolDefinition` metadata contract.
- `context.py`: request-local user identity and idempotency keys shared by graph and tools.
- `bill.py`, `cards.py`, `schedule.py`, `search.py`, `knowledge.py`, `memory.py`: domain tool adapters.

Public construction entry: `from app.tools import build_tools`.
The graph consumes policy collections derived from the same registry definitions. Tool factories and
metadata must match; duplicate names and missing contracts fail during construction, before model execution.

To add a tool, define it in the domain factory with `@tool`, a strict input schema and
`content_and_artifact` output, return it from the factory, and add one `ToolDefinition` in `registry.py`.
For a new domain, also add its factory to `build_tools`. Model schemas currently remain in `app/schemas.py`.
Business persistence stays under `app/services`; graph orchestration stays under `app/agent`.
Frontend renderer registration is separate in `frontend/src/tools/registry.ts`.

This is startup-time registration, not a network service or runtime plugin installation mechanism.
