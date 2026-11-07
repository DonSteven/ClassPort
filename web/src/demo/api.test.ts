import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Course, StudyTopic, Task } from "@/data/types";
import type { DashboardSummary, FileEntry } from "@/lib/queries";
import { DEMO_STORAGE_KEY, resetDemoData } from "./store";

beforeEach(() => {
  localStorage.removeItem(DEMO_STORAGE_KEY);
  vi.stubEnv("VITE_DEMO_MODE", "true");
  vi.resetModules();
});

afterEach(() => {
  localStorage.removeItem(DEMO_STORAGE_KEY);
  vi.unstubAllEnvs();
});

describe("browser-local demo", () => {
  it("opens an authenticated sample semester without making a network request", async () => {
    const fetch = vi.fn(() => { throw new Error("The demo must not contact a backend"); });
    vi.stubGlobal("fetch", fetch);
    vi.stubEnv("VITE_API_BASE_URL", "https://production.invalid");
    const { api, API_BASE } = await import("@/lib/api");
    expect(API_BASE).toBe("");
    expect(await api.get("/api/auth/session")).toEqual({ authed: true, totp_enabled: false });
    const dashboard = await api.get<DashboardSummary>("/api/dashboard");
    expect(dashboard.courses.length).toBeGreaterThanOrEqual(4);
    expect(dashboard.tasks.some((task) => task.due_at && new Date(task.due_at) > new Date())).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("persists created and completed tasks across a page reload, and resets them", async () => {
    const { api } = await import("@/lib/api");
    const task = await api.post<Task>("/api/tasks", { title: "Visitor-created task", course_code: "STAT201" });
    await api.post(`/api/tasks/${task.id}/complete`);
    vi.resetModules();
    const { api: reloaded } = await import("@/lib/api");
    const tasks = await reloaded.get<Task[]>("/api/tasks", { status: "done" });
    expect(tasks.find((item) => item.id === task.id)?.completed_at).toBeTruthy();
    await reloaded.post(`/api/tasks/${task.id}/reopen`);
    expect((await reloaded.get<Task[]>("/api/tasks", { status: "open" })).some((item) => item.id === task.id)).toBe(true);
    resetDemoData();
    expect((await reloaded.get<Task[]>("/api/tasks")).some((item) => item.id === task.id)).toBe(false);
  });

  it("updates progress and hides archived courses and their records from the dashboard", async () => {
    const { api } = await import("@/lib/api");
    const topic = (await api.get<StudyTopic[]>("/api/study-topics", { course_code: "STAT201" })).find((item) => item.status === "not_started")!;
    await api.post(`/api/study-topics/${topic.id}/studied`);
    expect((await api.get<DashboardSummary>("/api/dashboard")).study_topics.find((item) => item.id === topic.id)?.status).toBe("studied");
    await api.patch("/api/courses/STAT201", { archived: true });
    const dashboard = await api.get<DashboardSummary>("/api/dashboard");
    expect(dashboard.courses.some((course) => course.code === "STAT201")).toBe(false);
    expect(dashboard.tasks.some((task) => task.course_code === "STAT201")).toBe(false);
    expect((await api.get<DashboardSummary>("/api/dashboard?include_archived=true")).courses.some((course) => course.code === "STAT201")).toBe(true);
  });

  it("keeps theme preferences and course deletion consistent across pages", async () => {
    const { api } = await import("@/lib/api");
    await api.patch("/api/settings", { theme: "mission-control" });
    expect(await api.get("/api/settings")).toMatchObject({ theme: "mission-control" });
    await api.del("/api/courses/CS204");
    expect((await api.get<Course[]>("/api/courses")).some((course) => course.code === "CS204")).toBe(false);
    const dashboard = await api.get<DashboardSummary>("/api/dashboard");
    for (const rows of [dashboard.slots, dashboard.exams, dashboard.lectures, dashboard.study_topics, dashboard.deliverables, dashboard.tasks]) {
      expect(rows.some((row) => row.course_code === "CS204")).toBe(false);
    }
  });

  it("browses, searches, previews and renames prepared files without cloud URLs", async () => {
    const { api } = await import("@/lib/api");
    const root = await api.get<FileEntry[]>("/api/files/list", { prefix: "" });
    expect(root.some((item) => item.path === "STAT201" && item.type === "folder")).toBe(true);
    expect(await api.get("/api/files/search", { q: "Gamma" })).toEqual(expect.arrayContaining([expect.objectContaining({ path: "STAT201/course-notes.md" })]));
    expect(await api.get("/api/files/search", { q: "Atomicity" })).toEqual(expect.arrayContaining([expect.objectContaining({ path: "CS204/transactions.md" })]));
    const original = await api.get<{ url: string }>("/api/files/signed-url", { path: "STAT201/course-notes.md" });
    await api.post("/api/files/move", { from: "STAT201/course-notes.md", to: "STAT201/review.md", kind: "file" });
    expect(await api.get("/api/files/signed-url", { path: "STAT201/review.md" })).toMatchObject({ url: original.url });
    await api.post("/api/files/folder", { path: "My notes" });
    expect((await api.get<FileEntry[]>("/api/files/list")).some((item) => item.name === "My notes")).toBe(true);
  });

  it("blocks cloud-only operations and unknown endpoints without falling through to a server", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("@/lib/api");
    for (const path of ["/api/files/upload-url", "/api/settings/telegram/test", "/api/auth/totp/setup", "/api/not-in-demo"]) {
      await expect(api.post(path, {})).rejects.toMatchObject({ status: 501 });
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("uses the original backend when the demo build flag is off", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    const fetch = vi.fn(async () => Response.json({ authed: false, totp_enabled: false }));
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("@/lib/api");
    expect(await api.get("/api/auth/session")).toMatchObject({ authed: false });
    expect(fetch).toHaveBeenCalledOnce();
    expect(localStorage.getItem(DEMO_STORAGE_KEY)).toBeNull();
  });
});
