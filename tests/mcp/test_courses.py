"""MCP tool tests — courses entity (5 tools).

Coverage: list_courses, get_course, create_course, update_course, delete_course.
"""
import pytest

from tests.mcp._harness import get_tool_fn


@pytest.mark.asyncio
async def test_list_courses_empty(client, db_conn, mcp_server):
    list_courses = get_tool_fn(mcp_server, "list_courses")
    result = await list_courses()
    assert result == []


@pytest.mark.asyncio
async def test_create_then_list(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    list_courses = get_tool_fn(mcp_server, "list_courses")

    created = await create_course(code="MCPC1", full_name="MCP Course 1", ects=5)
    assert created["code"] == "MCPC1"
    assert created["full_name"] == "MCP Course 1"

    listed = await list_courses()
    assert len(listed) == 1
    assert listed[0]["code"] == "MCPC1"


@pytest.mark.asyncio
async def test_get_course(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    get_course = get_tool_fn(mcp_server, "get_course")

    await create_course(code="MCPC2", full_name="MCP Course 2", ects=4)

    got = await get_course(code="MCPC2")
    assert got["code"] == "MCPC2"
    assert got["full_name"] == "MCP Course 2"


@pytest.mark.asyncio
async def test_get_course_missing(client, db_conn, mcp_server):
    get_course = get_tool_fn(mcp_server, "get_course")
    # The tool's contract: return a structured "not found" rather than raise.
    # If it does raise, this test will surface the behaviour clearly.
    try:
        result = await get_course(code="NOPE")
    except Exception as exc:
        # Accept either: typed not-found ValueError OR returns None/{} dict.
        assert "not found" in str(exc).lower() or "no such" in str(exc).lower()
        return
    assert result is None or result == {} or "error" in (result or {})


@pytest.mark.asyncio
async def test_update_course(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    update_course = get_tool_fn(mcp_server, "update_course")

    await create_course(code="MCPC3", full_name="Original", ects=3)
    updated = await update_course(code="MCPC3", full_name="Renamed")
    assert updated["full_name"] == "Renamed"
    assert updated["code"] == "MCPC3"


@pytest.mark.asyncio
async def test_delete_course(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    delete_course = get_tool_fn(mcp_server, "delete_course")
    list_courses = get_tool_fn(mcp_server, "list_courses")

    await create_course(code="MCPC4", full_name="Doomed", ects=1)
    assert len(await list_courses()) == 1
    await delete_course(code="MCPC4")
    assert await list_courses() == []
