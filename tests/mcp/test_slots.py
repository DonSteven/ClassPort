"""MCP tool tests — schedule_slot entity (4 tools).

Coverage: list_schedule_slots, create_schedule_slot, update_schedule_slot,
delete_schedule_slot.
"""
import pytest

from tests.mcp._harness import get_tool_fn


@pytest.mark.asyncio
async def test_list_slots_empty(client, db_conn, mcp_server):
    list_schedule_slots = get_tool_fn(mcp_server, "list_schedule_slots")
    result = await list_schedule_slots()
    assert result == []


@pytest.mark.asyncio
async def test_create_then_list(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    create_schedule_slot = get_tool_fn(mcp_server, "create_schedule_slot")
    list_schedule_slots = get_tool_fn(mcp_server, "list_schedule_slots")

    await create_course(code="SLOTA", full_name="Slot Course A", ects=5)

    created = await create_schedule_slot(
        course_code="SLOTA",
        kind="lecture",
        weekday=1,
        start_time="10:00",
        end_time="12:00",
        room="A1.01",
    )
    assert created["course_code"] == "SLOTA"
    assert created["weekday"] == 1

    listed = await list_schedule_slots(course_code="SLOTA")
    assert len(listed) == 1
    assert listed[0]["course_code"] == "SLOTA"
    assert listed[0]["room"] == "A1.01"


@pytest.mark.asyncio
async def test_update_slot(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    create_schedule_slot = get_tool_fn(mcp_server, "create_schedule_slot")
    update_schedule_slot = get_tool_fn(mcp_server, "update_schedule_slot")

    await create_course(code="SLOTB", full_name="Slot Course B", ects=4)
    created = await create_schedule_slot(
        course_code="SLOTB",
        kind="lecture",
        weekday=2,
        start_time="08:00",
        end_time="10:00",
        room="B2.02",
    )
    slot_id = created["id"]

    updated = await update_schedule_slot(
        slot_id=slot_id,
        room="C3.03",
        weekday=3,
    )
    assert updated["room"] == "C3.03"
    assert updated["weekday"] == 3
    # Untouched fields should persist.
    assert updated["course_code"] == "SLOTB"


@pytest.mark.asyncio
async def test_delete_slot(client, db_conn, mcp_server):
    create_course = get_tool_fn(mcp_server, "create_course")
    create_schedule_slot = get_tool_fn(mcp_server, "create_schedule_slot")
    delete_schedule_slot = get_tool_fn(mcp_server, "delete_schedule_slot")
    list_schedule_slots = get_tool_fn(mcp_server, "list_schedule_slots")

    await create_course(code="SLOTC", full_name="Slot Course C", ects=3)
    created = await create_schedule_slot(
        course_code="SLOTC",
        kind="exercise",
        weekday=4,
        start_time="14:00",
        end_time="16:00",
    )
    slot_id = created["id"]

    assert len(await list_schedule_slots(course_code="SLOTC")) == 1
    result = await delete_schedule_slot(slot_id=slot_id)
    assert result == {"deleted": slot_id}
    assert await list_schedule_slots(course_code="SLOTC") == []
