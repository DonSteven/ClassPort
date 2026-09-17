"""PDF tool responses must remain useful when ChatGPT receives text only."""
from unittest.mock import AsyncMock

import fitz
import pytest

from app.mcp_tools import files_intent


@pytest.mark.asyncio
async def test_pdf_response_includes_only_requested_page_text_and_image(mcp_server, monkeypatch):
    with fitz.open() as doc:
        for label in ("First page only", "Second page only", "Third page only"):
            doc.new_page().insert_text((72, 72), label)
        pdf = doc.tobytes()
    download = AsyncMock(return_value=pdf)
    monkeypatch.setattr(files_intent, "download", download)

    content = await mcp_server.call_tool(
        "read_course_file", {"path": "ASB/slides.pdf", "pages": "2"}
    )

    text = "\n".join(item.text for item in content if item.type == "text")
    assert "Page 2:" in text
    assert "Second page only" in text
    assert "First page only" not in text
    assert "Third page only" not in text
    images = [item for item in content if item.type == "image"]
    assert len(images) == 1
    assert images[0].mimeType == "image/png"
    assert images[0].data
    assert download.await_args.args[1] == "ASB/slides.pdf"


@pytest.mark.asyncio
async def test_pdf_without_extractable_text_explains_needed_attachment(mcp_server, monkeypatch):
    with fitz.open() as doc:
        doc.new_page()
        pdf = doc.tobytes()
    monkeypatch.setattr(files_intent, "download", AsyncMock(return_value=pdf))

    content = await mcp_server.call_tool(
        "read_course_file", {"path": "ASB/scan.pdf", "pages": "1"}
    )

    text = "\n".join(item.text for item in content if item.type == "text")
    assert "Page 1: No extractable text" in text
    assert "Attach the PDF or page image" in text
    assert len([item for item in content if item.type == "image"]) == 1
