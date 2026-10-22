"""File intent exports and user-scoped search smoke checks."""
import pytest
from uuid import UUID

OPERATOR = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_files_intent_search_empty(client, db_conn):
    from app.intents import files as intent

    results = await intent.search(OPERATOR, "zzznomatchtoken", limit=5)
    assert isinstance(results, list)


def test_files_intent_module_exports():
    """All expected public functions are present on the intent module."""
    from app.intents import files as intent

    for fn in (
        "list_files",
        "list_recursive",
        "download",
        "exists",
        "stat",
        "upload",
        "delete",
        "signed_url",
        "signed_upload_url",
        "move",
        "search",
        "index_file",
        "index_user",
    ):
        assert callable(getattr(intent, fn, None)), f"missing: {fn}"
