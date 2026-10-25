"""Measure browser upload overhead against an exact pre-change source snapshot.

Uses disposable PostgreSQL and local Moto S3, never AWS. Serve mode is a child
process instrumenting the real API; measure mode uses real Chromium and the
bundled production uploadFile function. Headers/TCP/TLS and worker parsing are
excluded from API body-byte and upload-confirmation measurements.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def serve(args):
    sys.path.insert(0, str(args.app_root))
    from app.auth import _sentinel_user, require_user
    from app.main import create_app
    from fastapi.responses import HTMLResponse, FileResponse
    import uvicorn

    app = create_app()
    app.dependency_overrides[require_user] = lambda: _sentinel_user()
    metrics = {"request_body_bytes": 0, "upload_body_bytes": 0, "response_body_bytes": 0}

    @app.get("/__bench")
    async def page():
        return HTMLResponse('<script type="module">import {uploadFile} from "/__bench/client.js"; window.uploadFile=uploadFile;</script>')

    @app.get("/__bench/client.js")
    async def client():
        return FileResponse(args.client_bundle, media_type="text/javascript")

    @app.get("/__bench/metrics")
    async def counters():
        return metrics

    class BodyCounts:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            measured = scope["type"] == "http" and scope["path"].startswith("/api/files/")

            async def incoming():
                message = await receive()
                if measured:
                    amount = len(message.get("body", b""))
                    metrics["request_body_bytes"] += amount
                    if scope["path"] == "/api/files/upload-target":
                        metrics["upload_body_bytes"] += amount
                return message

            async def outgoing(message):
                if measured:
                    metrics["response_body_bytes"] += len(message.get("body", b""))
                await send(message)
            await self.app(scope, incoming, outgoing)

    uvicorn.run(BodyCounts(app), host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


@contextmanager
def api_process(args, mode, dsn, s3_endpoint, temporary):
    root = args.baseline_root if mode == "local_before" else ROOT
    environment = {**os.environ, "DATABASE_URL": dsn, "SESSION_SECRET": "benchmark-local-session-only",
        "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "6", "PGSSLMODE": "disable",
        "STORAGE_BACKEND": "local" if mode == "local_before" else "s3",
        "STUDY_ROOT": str(temporary / "courses"), "S3_BUCKET": "classport-benchmark-materials",
        "S3_ENDPOINT_URL": s3_endpoint, "AWS_REGION": "us-east-2", "AWS_DEFAULT_REGION": "us-east-2",
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_EC2_METADATA_DISABLED": "true"}
    for name in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SESSION_TOKEN", "PGSSLROOTCERT"):
        environment.pop(name, None)
    (temporary / "courses").mkdir()
    api_port = port()
    with (temporary / "api.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve",
            "--app-root", str(root), "--port", str(api_port), "--client-bundle", str(args.client_bundle)],
            cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{api_port}"
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError((temporary / "api.log").read_text()[-2000:])
                try:
                    if httpx.get(base + "/__bench/metrics", timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("benchmark API did not start")
                time.sleep(0.1)
            yield process, base
        finally:
            process.terminate()
            process.wait(timeout=15)


async def trial(args, browser, mode, size_mib, concurrency, dsn, endpoint):
    with tempfile.TemporaryDirectory(prefix="classport-upload-bench-") as directory:
        with api_process(args, mode, dsn, endpoint, Path(directory)) as (process, base):
            page = await browser.new_page()
            await page.goto(base + "/__bench")
            await page.wait_for_function("typeof window.uploadFile === 'function'")
            # Warm the DB pool and, for direct uploads, the SDK before the measured batch.
            await page.evaluate("""async mode => {
                const file = new File(['warmup'], 'warmup.txt', {type:'text/plain'});
                let target;
                if (mode === 's3_direct') target = await window.uploadFile(file, 'warmup.txt');
                else {
                    target = await (await fetch('/api/files/upload-url', {method:'POST',
                        headers:{'Content-Type':'application/json'},body:JSON.stringify({path:'warmup.txt'})})).json();
                    const put = await fetch(target.url,{method:'PUT',body:file,headers:{'Content-Type':'text/plain'}});
                    if (!put.ok) throw new Error('warmup PUT failed');
                }
                const done=await fetch(`/api/files/${target.file_id}/versions/${target.version_id}/complete`,
                    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({object_version:target.object_version})});
                if(!done.ok) throw new Error('warmup completion failed '+await done.text());
            }""", mode)
            before = httpx.get(base + "/__bench/metrics", trust_env=False).json()
            proc = psutil.Process(process.pid)
            start_rss = proc.memory_info().rss
            peak = [start_rss]
            stop = threading.Event()

            def sample():
                while not stop.wait(0.005):
                    peak[0] = max(peak[0], proc.memory_info().rss)
            sampler = threading.Thread(target=sample)
            sampler.start()
            cpu = proc.cpu_times()
            try:
                result = await page.evaluate("""async ({mode,size,concurrency}) => {
                    const digest=crypto.subtle.digest.bind(crypto.subtle);
                    let hashMs=0;
                    crypto.subtle.digest=async (...args)=>{const t=performance.now(); const result=await digest(...args); hashMs+=performance.now()-t; return result;};
                    const data = new Uint8Array(size); data.fill(97);
                    const started=performance.now();
                    const durations = await Promise.all(Array.from({length:concurrency}, async (_,i)=>{
                        const t=performance.now(), path=`batch-${i}.txt`;
                        const file=new File([data],path,{type:'text/plain'});
                        let target;
                        if(mode==='s3_direct') target=await window.uploadFile(file,path);
                        else {
                            target=await(await fetch('/api/files/upload-url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})})).json();
                            const put=await fetch(target.url,{method:'PUT',headers:{'Content-Type':'text/plain',...target.headers},body:file});
                            if(!put.ok) throw new Error('PUT failed '+put.status);
                        }
                        const done=await fetch(`/api/files/${target.file_id}/versions/${target.version_id}/complete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({object_version:target.object_version})});
                        if(!done.ok) throw new Error('completion failed '+await done.text());
                        return performance.now()-t;
                    }));
                    return {batch_ms:performance.now()-started, file_ms:durations, browser_hash_ms:hashMs};
                }""", {"mode": mode, "size": size_mib * 1024**2, "concurrency": concurrency})
            finally:
                stop.set()
                sampler.join()
            after_cpu = proc.cpu_times()
            after = httpx.get(base + "/__bench/metrics", trust_env=False).json()
            await page.close()
            return {"mode": mode, "size_mib": size_mib, "concurrency": concurrency,
                **result, "api_cpu_ms": 1000 * (after_cpu.user + after_cpu.system - cpu.user - cpu.system),
                "api_baseline_rss_bytes": start_rss, "api_peak_rss_bytes": peak[0],
                "api_peak_rss_increase_bytes": peak[0] - start_rss,
                **{f"api_{key}": after[key] - before[key] for key in after}}


async def measure(args):
    import boto3
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    from moto.server import ThreadedMotoServer
    from moto.s3.responses import S3Response
    import psycopg
    from playwright.async_api import async_playwright
    from testcontainers.postgres import PostgresContainer
    sys.path.insert(0, str(ROOT))
    from tests.s3_emulator import rest_checksums

    S3Response._get_checksum = rest_checksums()
    s3_port = port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=s3_port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{s3_port}"
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-2",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    bucket = "classport-benchmark-materials"
    client.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["http://127.0.0.1:*"], "AllowedMethods": ["PUT", "GET", "HEAD"],
        "AllowedHeaders": ["content-type", "x-amz-checksum-sha256"],
        "ExposeHeaders": ["x-amz-version-id", "x-amz-checksum-sha256"]}]})
    postgres = PostgresContainer("postgres:16-alpine", username="benchmark", password="testing", dbname="upload_benchmark")
    if args.docker_network:
        postgres.with_kwargs(network=args.docker_network)
        postgres.get_container_host_ip = lambda: postgres.get_wrapped_container().reload() or postgres.get_wrapped_container().attrs["NetworkSettings"]["Networks"][args.docker_network]["IPAddress"]
        postgres.get_exposed_port = lambda port: str(port)
    rows = []
    try:
        with postgres:
            dsn = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
            subprocess.run([sys.executable, str(ROOT / "scripts/run_migrations.py")], check=True,
                           env={**os.environ, "MIGRATION_DATABASE_URL": dsn, "PGSSLMODE": "disable"}, stdout=subprocess.DEVNULL)
            with psycopg.connect(dsn) as connection:
                connection.execute("INSERT INTO users(id,email,display_name) VALUES ('00000000-0000-0000-0000-000000000001','benchmark@example.test','Benchmark') ON CONFLICT DO NOTHING")
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                browser_version = browser.version
                for size in args.sizes:
                    for concurrency in args.concurrency:
                        for repeat in range(args.repeats):
                            # Alternate order to reduce order and cache bias.
                            modes = ["local_before", "s3_direct"]
                            if repeat % 2:
                                modes.reverse()
                            for mode in modes:
                                row = await trial(args, browser, mode, size, concurrency, dsn, endpoint)
                                row["repeat"] = repeat + 1
                                rows.append(row)
                                print(json.dumps(row), flush=True)
                                with psycopg.connect(dsn) as connection:
                                    connection.execute("TRUNCATE files CASCADE")
                                versions = client.list_object_versions(Bucket=bucket).get("Versions", [])
                                if versions:
                                    client.delete_objects(Bucket=bucket, Delete={"Objects": [
                                        {"Key": item["Key"], "VersionId": item["VersionId"]} for item in versions]})
                await browser.close()
        report = {"environment": "local Chromium + PostgreSQL + Moto; loopback HTTP, no AWS",
            "baseline_source": str(args.baseline_root), "browser_version": browser_version,
            "python_version": sys.version.split()[0], "repeats": args.repeats,
            "limits": ["API bytes count HTTP bodies, exclude headers/TCP/TLS",
                       "Memory is API RSS sampled every 5 ms; fresh API and one warmup per trial",
                       "Elapsed time includes browser file read, hashing, upload and completion; excludes parsing",
                       "Moto REST checksum-header correction enabled; AWS IAM/signature/CORS acceptance remains separate"],
            "results": rows}
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    child = sub.add_parser("serve")
    child.add_argument("--app-root", type=Path, required=True)
    child.add_argument("--port", type=int, required=True)
    child.add_argument("--client-bundle", type=Path, required=True)
    parent = sub.add_parser("measure")
    parent.add_argument("--baseline-root", type=Path, required=True)
    parent.add_argument("--client-bundle", type=Path, required=True)
    parent.add_argument("--docker-network")
    parent.add_argument("--sizes", nargs="+", type=int, default=[1, 16, 64])
    parent.add_argument("--concurrency", nargs="+", type=int, default=[1, 4])
    parent.add_argument("--repeats", type=int, default=3)
    parent.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    serve(args) if args.action == "serve" else asyncio.run(measure(args))


if __name__ == "__main__":
    main()
