"""MCP reads the same fixed remote version used by background processing."""
from tests.services.test_s3 import s3  # noqa: F401
from tests.mcp._harness import get_tool_fn
from app.auth import SENTINEL_USER_ID
from app.services import file_records as files


async def test_mcp_lists_and_reads_without_a_material_directory(client, mcp_server, s3, monkeypatch):  # noqa: F811
    monkeypatch.setenv("STUDY_ROOT", "/nonexistent-b7-material-directory")
    await files.upload(SENTINEL_USER_ID, "REMOTE/notes.txt", b"remote quantum mechanics", "text/plain")
    result = await get_tool_fn(mcp_server, "list_course_files")(prefix="REMOTE")
    assert result[0]["path"] == "REMOTE/notes.txt"
    assert await get_tool_fn(mcp_server, "read_course_file")(path="REMOTE/notes.txt") == ["remote quantum mechanics"]
