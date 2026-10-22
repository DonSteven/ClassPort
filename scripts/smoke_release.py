"""Exercise the frontend, authenticated REST API and MCP through one origin."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import os
import sys
from uuid import uuid4
from urllib.parse import urljoin, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get("src") if tag == "script" else attrs.get("href")
        if tag in ("script", "link") and value and value.startswith("/assets/"):
            self.urls.append(value)


async def check_release(base_url: str, email: str, password: str, token: str) -> None:
    origin = urlsplit(base_url)
    if (
        origin.scheme not in ("http", "https") or not origin.hostname
        or origin.username or origin.password or origin.path not in ("", "/")
        or origin.query or origin.fragment
    ):
        raise ValueError("SMOKE_BASE_URL must be an HTTP(S) origin")
    if origin.scheme == "http" and origin.hostname not in ("frontend", "localhost", "127.0.0.1"):
        raise ValueError("use HTTPS outside the private frontend or loopback")
    base_url = base_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
        print("smoke: login page and static assets", flush=True)
        page = await client.get("/login")
        page.raise_for_status()
        if "text/html" not in page.headers.get("content-type", ""):
            raise ValueError("login page is not HTML")
        assets = _Assets()
        assets.feed(page.text)
        if not any(url.endswith(".js") for url in assets.urls):
            raise ValueError("login page has no application bundle")
        for url in assets.urls:
            response = await client.get(url)
            response.raise_for_status()
            if "text/html" in response.headers.get("content-type", "") or not response.content:
                raise ValueError("static asset is missing or returned the SPA fallback")

        print("smoke: authenticated course API", flush=True)
        login = await client.post("/api/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        cookie = login.cookies.get("study_session")
        if login.json().get("authed") is not True or not cookie:
            raise ValueError("login did not create a session")
        # The same origin is used throughout. Forward explicitly for private HTTP
        # CI, where the application's Secure cookie would otherwise be withheld.
        courses = await client.get("/api/courses", headers={"Cookie": f"study_session={cookie}"})
        courses.raise_for_status()
        if not isinstance(courses.json(), list):
            raise ValueError("course API did not return a list")
        print("smoke: upload, durable processing, search and download", flush=True)
        client.headers["Cookie"] = f"study_session={cookie}"
        marker = "smoke" + uuid4().hex
        path = f"_release_checks/{marker}.txt"
        content = f"{marker} file processing check".encode()
        try:
            target = await client.post("/api/files/upload-url", json={"path": path})
            target.raise_for_status()
            upload = target.json()
            received = await client.put(upload["url"], content=content,
                                        headers={"Content-Type": "text/plain", **upload["headers"]})
            received.raise_for_status()
            completed = await client.post(f"/api/files/{upload['file_id']}/versions/{upload['version_id']}/complete")
            completed.raise_for_status()
            job_id = completed.json()["job_id"]
            async with asyncio.timeout(35):
                while True:
                    state = await client.get(f"/api/jobs/{job_id}")
                    state.raise_for_status()
                    status = state.json()["status"]
                    if status == "succeeded":
                        break
                    if status in ("failed", "cancelled"):
                        raise ValueError("file processing failed")
                    await asyncio.sleep(0.5)
            result = await client.get("/api/files/search", params={"q": marker})
            result.raise_for_status()
            if not any(hit["file_id"] == upload["file_id"] for hit in result.json()):
                raise ValueError("processed file is not searchable")
            downloaded = await client.get("/api/files/raw", params={"file_id": upload["file_id"],
                                                                    "version_id": upload["version_id"]})
            downloaded.raise_for_status()
            if downloaded.content != content:
                raise ValueError("processed file download differs")
            print("smoke: MCP initialization, tools, courses and file read", flush=True)
            async with streamablehttp_client(
                urljoin(base_url, "/mcp"), headers={"Authorization": f"Bearer {token}"},
                timeout=10, sse_read_timeout=15,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if not {"list_courses", "read_course_file"}.issubset({tool.name for tool in tools.tools}):
                        raise ValueError("MCP required tools missing")
                    for name, arguments in (("list_courses", {}), ("read_course_file", {"path": path})):
                        result = await session.call_tool(name, arguments)
                        if result.isError or (name == "read_course_file" and marker not in str(result.content)):
                            raise ValueError("MCP read failed")
        finally:
            await client.delete("/api/files", params={"path": path})

    print("smoke: PASS", flush=True)


def main() -> int:
    names = ("SMOKE_BASE_URL", "SMOKE_EMAIL", "SMOKE_PASSWORD", "SMOKE_MCP_TOKEN")
    if any(not os.environ.get(name) for name in names):
        print("smoke: required settings missing; see .env.smoke.example", file=sys.stderr)
        return 1

    async def run():
        async with asyncio.timeout(60):
            await check_release(*(os.environ[name] for name in names))

    try:
        asyncio.run(run())
    except Exception:
        # HTTP/MCP exceptions can contain response bodies or credential headers.
        print("smoke: FAIL at the check shown above (credentials and bodies omitted)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
