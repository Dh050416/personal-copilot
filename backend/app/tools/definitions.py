"""Single contract for execution policy and result validation."""

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    output_schema: type[BaseModel]
    write: bool = False
    cacheable: bool = False
    permissions: tuple[str, ...] = ()
