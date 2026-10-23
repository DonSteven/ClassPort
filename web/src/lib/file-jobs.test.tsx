import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { useFileJob, useFilesList, useRetryFileJob, type FileEntry, type FileJob } from "./queries";

const clients: QueryClient[] = [];
function clientWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
afterEach(() => clients.splice(0).forEach((client) => client.clear()));

const job: FileJob = {
  id: "job-1", file_id: "file-1", file_version_id: "version-1",
  status: "succeeded", error_code: null, last_error: null, can_retry: false,
};
const file: FileEntry = {
  name: "notes.txt", path: "notes.txt", type: "file", file_id: "file-1", version_id: "version-1",
  job_id: "job-1", job_status: "succeeded", job_error_code: null, job_error: null,
  job_can_retry: false, searchable: true,
};

describe("file processing queries", () => {
  it("refreshes cached files and search when the first task response is terminal, then stops polling", async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, "get").mockResolvedValue(job);
    const { client, wrapper } = clientWrapper();
    client.setQueryData(["files", "list", ""], []);
    client.setQueryData(["files", "search", "quantum"], []);
    const { result } = renderHook(() => useFileJob(job.id), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.data?.status).toBe("succeeded");
    expect(client.getQueryState(["files", "list", ""])?.isInvalidated).toBe(true);
    expect(client.getQueryState(["files", "search", "quantum"])?.isInvalidated).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("resumes polling a failed task after manual retry and stops again at success", async () => {
    vi.useFakeTimers();
    const failed = { ...job, status: "failed" as const, error_code: "worker_lost", can_retry: true };
    const get = vi.spyOn(api, "get").mockResolvedValueOnce(failed).mockResolvedValue(job);
    vi.spyOn(api, "post").mockResolvedValue({ ...job, status: "queued" });
    const { wrapper } = clientWrapper();
    const { result } = renderHook(() => ({ state: useFileJob(job.id), retry: useRetryFileJob() }), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.state.data?.status).toBe("failed");
    await act(async () => { await result.current.retry.mutateAsync(job.id); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.state.data?.status).toBe("queued");
    await act(async () => { await vi.advanceTimersByTimeAsync(2010); });
    expect(result.current.state.data?.status).toBe("succeeded");
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("refreshes search when a list first sees an already completed task, without needing an earlier active state", async () => {
    vi.spyOn(api, "get").mockResolvedValue([file]);
    const { client, wrapper } = clientWrapper();
    client.setQueryData(["files", "list", ""], []);
    await client.invalidateQueries({ queryKey: ["files", "list"] });
    client.setQueryData(["files", "search", "quantum"], []);
    const { result } = renderHook(() => useFilesList(""), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual([file]));
    expect(client.getQueryState(["files", "search", "quantum"])?.isInvalidated).toBe(true);
    client.setQueryData(["files", "search", "quantum"], ["fresh results"]);
    await act(async () => { await result.current.refetch(); });
    expect(client.getQueryState(["files", "search", "quantum"])?.isInvalidated).toBe(false);
  });
});
