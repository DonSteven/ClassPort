"""Shared MCP test helpers.

The tools live in `app/mcp_tools.py` as nested `async def`s inside
`register_tools(server)`. To unit-test them, the `mcp_server` fixture
(in `conftest.py`) instantiates a fresh `FastMCP` per test, registers
all tools onto it, and tests invoke each tool's `.fn(...)` attribute
directly via `_tool_manager.get_tool(name)`.

Why direct `.fn()` and not `server.call_tool()`?  FastMCP's `call_tool`
return shape varies by the tool's declared return type (`tuple` for
typed returns, `list[ContentBlock]` for `dict` returns), making test
assertions painful. Calling `.fn()` bypasses MCP serialization and
gives us the raw Python return value — what actually needs testing
(did the tool call its service correctly?).

The downside: we don't exercise the MCP protocol layer. That's fine
because (a) the protocol layer is upstream's responsibility, and
(b) the OAuth E2E test in `test_integration_full_flow.py` calls
`/mcp/` over real HTTP and exercises the protocol path.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def get_tool_fn(server: FastMCP, name: str):
    """Return the underlying async function for a registered tool.

    Raises AssertionError with a helpful message if the tool isn't
    registered — surfaces typos in test code immediately.
    """
    tool = server._tool_manager.get_tool(name)
    if tool is None:
        raise AssertionError(
            f"Tool {name!r} not registered. Registered tools: "
            f"{list(server._tool_manager._tools.keys())}"
        )
    return tool.fn
