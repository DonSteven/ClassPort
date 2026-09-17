"""MCP tool tests — exams entity (2 tools).

Coverage: list_exams, update_exam (upsert — one row per course).
"""
import pytest

from tests.mcp._harness import get_tool_fn


@pytest.mark.asyncio
async def test_list_exams_empty(client, db_conn, mcp_server):
    list_exams = get_tool_fn(mcp_server, "list_exams")
    result = await list_exams()
    assert result == []


@pytest.mark.asyncio
async def test_update_exam_inserts_on_first_call(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    update_exam = get_tool_fn(mcp_server, "update_exam")
    list_exams = get_tool_fn(mcp_server, "list_exams")

    await create_course(code="EXAMA", full_name="Exam Course A", ects=5)

    upserted = await update_exam(course_code="EXAMA", location="Hall 1")
    assert upserted["course_code"] == "EXAMA"
    assert upserted["location"] == "Hall 1"

    listed = await list_exams()
    assert len(listed) == 1
    assert listed[0]["course_code"] == "EXAMA"
    assert listed[0]["location"] == "Hall 1"


@pytest.mark.asyncio
async def test_update_exam_updates_on_second_call(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    update_exam = get_tool_fn(mcp_server, "update_exam")
    list_exams = get_tool_fn(mcp_server, "list_exams")

    await create_course(code="EXAMB", full_name="Exam Course B", ects=4)

    first = await update_exam(course_code="EXAMB", location="Hall 2", duration_min=90)
    assert first["location"] == "Hall 2"
    assert first["duration_min"] == 90

    second = await update_exam(course_code="EXAMB", location="Hall 9")
    assert second["location"] == "Hall 9"
    # Untouched field persists from first upsert.
    assert second["duration_min"] == 90

    listed = await list_exams()
    # Same row, no duplicate.
    assert len(listed) == 1
    assert listed[0]["course_code"] == "EXAMB"
    assert listed[0]["location"] == "Hall 9"
