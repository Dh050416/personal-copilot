"""Registry contracts that do not require a database or remote model."""

import pytest

from app.tools import CACHEABLE_TOOLS, OUTPUT_SCHEMAS, TOOL_PERMISSIONS, WRITE_TOOLS, build_tools, registry
from app.tools.context import _USER_ID, current_user_id


def test_all_registered_tools_have_execution_contracts():
    tools = build_tools(db=None)
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)) == 20
    assert set(names) == set(OUTPUT_SCHEMAS) == set(registry.TOOL_DEFINITIONS)
    assert "add_bill" in names and "add_bill" in WRITE_TOOLS
    assert not WRITE_TOOLS & CACHEABLE_TOOLS
    assert set(TOOL_PERMISSIONS) == set(WRITE_TOOLS)
    for tool in tools:
        assert tool.args_schema.model_json_schema()["additionalProperties"] is False
        assert tool.response_format == "content_and_artifact"
        assert tool.description


def test_duplicate_factory_registration_is_rejected(monkeypatch):
    duplicate = build_tools(db=None)[0]
    monkeypatch.setattr(registry, "build_bill_tools", lambda *args: [duplicate, duplicate])
    with pytest.raises(ValueError, match="Duplicate tool"):
        registry.build_tools(db=None)


def test_missing_factory_registration_is_rejected(monkeypatch):
    monkeypatch.setattr(registry, "build_bill_tools", lambda *args: [])
    with pytest.raises(ValueError, match="do not match"):
        registry.build_tools(db=None)


def test_domains_share_the_trusted_request_context():
    token = _USER_ID.set(42)
    try:
        assert current_user_id() == 42
    finally:
        _USER_ID.reset(token)
