"""Verify a restored installation using actual login, queries and PDF bytes."""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx

from smoke_release import check_release


async def check_restore(base, config, token, pdf_path, study_root=Path("/opt/courses")):
    await check_release(base, config["SMOKE_EMAIL"], config["SMOKE_PASSWORD"], token)
    async with httpx.AsyncClient(base_url=base, timeout=20, trust_env=False) as client:
        response = await client.post("/api/auth/login", json={"email": config["OPERATOR_EMAIL"],
                                                             "password": config["OPERATOR_PASSWORD"]})
        response.raise_for_status()
        if response.json().get("authed") is not True or not response.cookies.get("study_session"):
            raise ValueError("Operator login did not create a session; provide current recovery-check credentials")
        client.headers["Cookie"] = "study_session=" + response.cookies["study_session"]
        for endpoint in ("/api/courses", "/api/tasks"):
            response = await client.get(endpoint)
            response.raise_for_status()
            if not isinstance(response.json(), list):
                raise ValueError("Restored business query did not return a list")
        root = (study_root / "00000000-0000-0000-0000-000000000001").resolve()
        sample = (root / pdf_path).resolve()
        if not sample.is_relative_to(root) or sample.suffix.lower() != ".pdf":
            raise ValueError("Choose a restored operator PDF for the exercise")
        expected = sample.read_bytes()
        response = await client.get("/api/files/raw", params={"path": pdf_path})
        response.raise_for_status()
        if response.content != expected or response.headers.get("content-type") != "application/pdf":
            raise ValueError("Downloaded PDF differs from the recovered file")
        if "inline" not in response.headers.get("content-disposition", ""):
            raise ValueError("Recovered PDF cannot be previewed inline")
        import pymupdf
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            if document.page_count < 1:
                raise ValueError("Recovered PDF has no readable pages")
            document[0].get_pixmap()


if __name__ == "__main__":
    try:
        config = json.loads(Path("/run/secrets/application.json").read_text())
        asyncio.run(check_restore(os.environ["SMOKE_BASE_URL"], config,
                                 os.environ["SMOKE_MCP_TOKEN"], os.environ["RESTORE_PDF_PATH"]))
    except Exception:
        print("restore verification failed; credentials and response bodies omitted", file=sys.stderr)
        raise SystemExit(1) from None
    print("restore verification passed: login, courses, tasks, PDF preview/download and MCP")
