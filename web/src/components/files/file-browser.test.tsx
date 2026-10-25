import { webcrypto } from "node:crypto";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "i18next";
import "@/lib/i18n";
import { FileBrowser } from "./file-browser";

const clients: QueryClient[] = [];
beforeEach(async () => { vi.stubGlobal("crypto", webcrypto); await i18n.changeLanguage("en"); });
afterEach(() => clients.splice(0).forEach((client) => client.clear()));

function renderBrowser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}><FileBrowser /></QueryClientProvider>);
}
function json(data: unknown) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}
function selectFiles(container: HTMLElement, ...files: File[]) {
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files } });
}

describe("upload and processing feedback", () => {
  it("confirms the same transferred version after a lost completion response without uploading again", async () => {
    let confirmations = 0;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/files/upload-url") {
        return json({ url: "/put", headers: {}, file_id: "file-1", version_id: "version-1" });
      }
      if (path === "/put") return json({});
      if (path === "/api/files/file-1/versions/version-1/complete") {
        if (++confirmations === 1) throw new TypeError("Network response lost");
        return json({ job_id: "job-1", job_status: "queued" });
      }
      if (path === "/api/jobs/job-1") return json({ status: "succeeded", can_retry: false });
      return json([]);
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = renderBrowser();
    selectFiles(container, new File(["quantum mechanics"], "notes.txt"));
    await screen.findByRole("button", { name: "Confirm again" });
    expect(screen.getByText(/File transferred; confirmation result unavailable/)).toBeTruthy();
    expect(screen.queryByText(/Upload failed:/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Confirm again" }));
    await screen.findByText("Processing complete");
    expect(confirmations).toBe(2);
    expect(fetch.mock.calls.filter(([path]) => path === "/api/files/upload-url")).toHaveLength(1);
    expect(fetch.mock.calls.filter(([path]) => path === "/put")).toHaveLength(1);
  });

  it("keeps tracking after changing folders and refreshes an empty search when processing finishes", async () => {
    let finished = false;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/files/upload-url") {
        return json({ url: "/put", headers: {}, file_id: "file-1", version_id: "version-1" });
      }
      if (url.pathname === "/put") return json({});
      if (url.pathname.endsWith("/complete")) return json({ job_id: "job-1", job_status: "queued" });
      if (url.pathname === "/api/jobs/job-1") {
        return json({ status: finished ? "succeeded" : "running", can_retry: false });
      }
      if (url.pathname === "/api/files/search") {
        return json(finished ? [{ path: "COURSE/notes.txt", snippet: "quantum mechanics" }] : []);
      }
      if (url.pathname === "/api/files/list" && !url.searchParams.get("prefix")) {
        return json([{ file_id: "folder-1", name: "COURSE", path: "COURSE", type: "folder", job_status: null }]);
      }
      return json([]);
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = renderBrowser();
    fireEvent.click(await screen.findByRole("button", { name: "COURSE" }));
    selectFiles(container, new File(["quantum mechanics"], "notes.txt"));
    await screen.findByText("Processing");
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    fireEvent.change(screen.getByPlaceholderText("Search inside files (PDFs, notebooks, notes)…"), {
      target: { value: "quantum" },
    });
    await screen.findByText('No matches for "quantum".');
    finished = true;
    await waitFor(() => expect(screen.getByRole("button", { name: /COURSE\/notes.txt/ })).toBeTruthy(), { timeout: 4000 });
    expect(screen.getByText("Processing complete")).toBeTruthy();
  });

  it("continues a batch after one transfer fails and shows the other file's processing result", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/files/upload-url") {
        const name = JSON.parse(String(init?.body)).path;
        return json({ url: `/put/${name}`, headers: {}, file_id: name, version_id: "version-1" });
      }
      if (path === "/put/bad.txt") return new Response("unavailable", { status: 503 });
      if (path === "/put/good.txt") return json({});
      if (path.endsWith("/complete")) return json({ job_id: "good-job", job_status: "queued" });
      if (path === "/api/jobs/good-job") return json({ status: "succeeded", can_retry: false });
      return json([]);
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = renderBrowser();
    selectFiles(container, new File(["bad"], "bad.txt"), new File(["good"], "good.txt"));
    await screen.findByText("Processing complete");
    expect(screen.getByText(/bad.txt · Upload failed/)).toBeTruthy();
    expect(fetch.mock.calls.filter(([path]) => String(path).endsWith("/complete"))).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Upload" }).hasAttribute("disabled")).toBe(false);
  });
});

it("retains the S3 object version when confirmation must be repeated", async () => {
  let confirmations = 0;
  const bodies: unknown[] = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/files/upload-url") {
      const metadata = JSON.parse(String(init?.body));
      expect(metadata.size).toBe(4);
      expect(metadata.sha256).toMatch(/^[0-9a-f]{64}$/);
      return json({ url: "https://materials.example.test/content", headers: {
        "Content-Type": "text/plain", "x-amz-checksum-sha256": "signed-checksum",
      }, file_id: "file-1", version_id: "version-1", storage_backend: "s3" });
    }
    if (path === "https://materials.example.test/content") {
      expect(init?.credentials).toBe("omit");
      expect(init?.headers).toEqual({ "Content-Type": "text/plain", "x-amz-checksum-sha256": "signed-checksum" });
      return new Response(null, { headers: { "x-amz-version-id": "fixed-s3-version" } });
    }
    if (path.endsWith("/complete")) {
      bodies.push(JSON.parse(String(init?.body)));
      if (++confirmations === 1) throw new TypeError("Response lost");
      return json({ job_id: "job-1", job_status: "queued" });
    }
    if (path === "/api/jobs/job-1") return json({ status: "succeeded", can_retry: false });
    return json([]);
  });
  vi.stubGlobal("fetch", fetch);
  const { container } = renderBrowser();
  selectFiles(container, new File(["data"], "notes.txt", { type: "text/plain" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm again" }));
  await screen.findByText("Processing complete");
  expect(bodies).toEqual([{ object_version: "fixed-s3-version" }, { object_version: "fixed-s3-version" }]);
  expect(fetch.mock.calls.filter(([path]) => path === "https://materials.example.test/content")).toHaveLength(1);
});
