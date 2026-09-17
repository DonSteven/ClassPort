"""MCP test fixtures (auto-discovered by pytest under tests/mcp/)."""
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

from app.mcp_tools import register_tools


@pytest_asyncio.fixture
async def mcp_server() -> FastMCP:
    """A fresh FastMCP server with all ClassPort tools registered.

    Function-scoped — every test gets a clean registration to keep
    failures isolated.
    """
    server = FastMCP("classport-test")
    register_tools(server)
    return server
