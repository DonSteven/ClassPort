import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import i18n from "@/lib/i18n";
import type { DashboardSummary } from "@/lib/queries";
import { applyTheme, DEFAULT_THEME, normalizeTheme, readStoredTheme, THEMES } from "@/lib/themes";
import Dashboard from "./dashboard";

const clients: QueryClient[] = [];
beforeEach(async () => { await i18n.changeLanguage("en"); });
afterEach(() => {
  clients.splice(0).forEach(client => client.clear());
  localStorage.removeItem("classport:theme");
  delete document.documentElement.dataset.theme;
});

function fixture(): DashboardSummary {
  const topic = { id: "topic", course_code: "CS101", name: "Graph traversal", status: "not_started" as const };
  return {
    now: "2026-10-26T14:15:00Z",
    courses: [{ code: "CS101", full_name: "Discrete Mathematics", ects: 6, module_code: "D101", language: "EN" }, { code: "DATA2", full_name: "Data Systems", ects: 4 }],
    slots: [
      { id: "current", course_code: "CS101", kind: "lecture", weekday: 1, start_time: "11:00:00", end_time: "12:30:00", room: "B 204", ends_on: "2026-10-30" },
      { id: "following", course_code: "DATA2", kind: "lab", weekday: 1, start_time: "09:00", end_time: "10:30", room: "Lab", starts_on: "2026-11-02" },
    ],
    study_topics: [topic, { id: "studied", course_code: "CS101", name: "Sets", status: "studied" }, { id: "mastered", course_code: "DATA2", name: "SQL", status: "mastered" }],
    tasks: [
      { id: "open", course_code: "CS101", title: "Review graph traversal", status: "open", priority: "high" },
      { id: "done", title: "Read the slides", status: "done", priority: "low" },
      { id: "skipped", title: "Skipped exercise", status: "skipped", priority: "low" },
    ],
    deliverables: [{ id: "due", course_code: "CS101", kind: "submission", name: "Problem Set 4", due_at: "2026-10-28T20:00:00Z", status: "open" }],
    fall_behind: [{ course_code: "CS101", topics: [topic], severity: "critical", last_covered_on: "2026-10-23", next_lecture_at: "2026-10-26T15:00:00Z" }],
    lectures: [], exams: [],
  };
}

function setup({ failComplete = false, empty = false, failDashboard = false }: { failComplete?: boolean; empty?: boolean; failDashboard?: boolean } = {}) {
  const data = fixture();
  if (empty) { data.courses = []; data.slots = []; data.tasks = []; data.deliverables = []; data.study_topics = []; data.fall_behind = []; }
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/dashboard") return failDashboard ? json({ detail: "Unavailable" }, 500) : json(data);
    if (path === "/api/settings") return json({ display_name: "Taylor Student", theme: "mission-control", semester_start: empty ? null : "2026-09-28", semester_end: "2027-01-17" });
    if (path === "/api/tasks/open/complete") {
      if (failComplete) return json({ detail: "Could not complete task" }, 500);
      data.tasks[0].status = "done";
      return json(data.tasks[0]);
    }
    if (path === "/api/tasks/open/reopen") { data.tasks[0].status = "open"; return json(data.tasks[0]); }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  return { ...render(<QueryClientProvider client={client}><MemoryRouter><Dashboard /></MemoryRouter></QueryClientProvider>), fetch };
}

describe("Mission Control theme integration", () => {
  it("registers and restores the theme without changing the existing default", () => {
    expect(THEMES.find(theme => theme.id === "mission-control")?.label).toBe("Mission Control");
    applyTheme("mission-control");
    expect(document.documentElement.dataset.theme).toBe("mission-control");
    expect(readStoredTheme()).toBe("mission-control");
    expect(normalizeTheme("material")).toBe("material");
    expect(normalizeTheme(undefined)).toBe(DEFAULT_THEME);
  });

  it("keeps the selected theme when a superseded browser transition is skipped", async () => {
    Object.defineProperty(document, "startViewTransition", { configurable: true, value: (apply: () => void) => {
      apply();
      return { ready: Promise.reject(new DOMException("Transition was skipped", "AbortError")) };
    } });
    try {
      applyTheme("mission-control");
      await Promise.resolve();
      expect(document.documentElement.dataset.theme).toBe("mission-control");
      expect(readStoredTheme()).toBe("mission-control");
    } finally {
      Reflect.deleteProperty(document, "startViewTransition");
    }
  });

  it("dispatches the saved theme and renders live profile, courses, weighted progress, and study links", async () => {
    const { container } = setup();
    await screen.findByRole("heading", { name: /Good morning, Taylor/ });
    expect(container.querySelectorAll(".mc-course-card")).toHaveLength(2);
    expect(screen.getByRole("progressbar", { name: /CS101/ }).getAttribute("aria-valuenow")).toBe("45");
    expect(screen.getByRole("heading", { name: "Data Systems" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Make a study plan/ }).getAttribute("href")).toBe("/app/courses/CS101?tab=study");
    expect(container.querySelector(".mc-semester-readout strong")?.textContent).toBe("05");
    expect(screen.queryByText("Sihan")).toBeNull();
  });

  it("respects semester-bounded sessions and Eastern calendar weeks across daylight saving time", async () => {
    const { container } = setup();
    await screen.findByRole("heading", { name: /Good morning, Taylor/ });
    expect(container.querySelectorAll(".mc-slot")).toHaveLength(1);
    expect(container.querySelector(".mc-slot")?.getAttribute("href")).toBe("/app/courses/CS101?tab=schedule");
    expect(container.querySelector(".mc-now-line")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next week" }));
    expect(container.querySelectorAll(".mc-slot")).toHaveLength(1);
    expect(container.querySelector(".mc-slot")?.textContent).toContain("DATA2");
    expect(container.querySelector(".mc-week-range")?.textContent).toContain("2 Nov");
    expect(container.querySelector(".mc-now-line")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "This week" }));
    expect(container.querySelector(".mc-slot")?.textContent).toContain("CS101");
  });

  it("completes and reopens tasks through the API and updates the task queue", async () => {
    const { container, fetch } = setup();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Complete: Review graph traversal" }));
    await waitFor(() => expect(container.querySelectorAll(".mc-metric-value strong")[1].textContent).toBe("00"));
    fireEvent.click(screen.getByRole("button", { name: "Completed" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Reopen: Review graph traversal" }));
    await waitFor(() => expect(container.querySelectorAll(".mc-metric-value strong")[1].textContent).toBe("01"));
    expect(fetch.mock.calls.some(([path]) => path === "/api/tasks/open/complete")).toBe(true);
    expect(fetch.mock.calls.some(([path]) => path === "/api/tasks/open/reopen")).toBe(true);
    expect(screen.queryByText("Skipped exercise")).toBeNull();
  });

  it("keeps failed task updates visible and reports the error", async () => {
    const error = vi.spyOn(toast, "error");
    setup({ failComplete: true });
    const checkbox = await screen.findByRole("checkbox", { name: "Complete: Review graph traversal" });
    fireEvent.click(checkbox);
    await waitFor(() => expect(error).toHaveBeenCalledWith("Could not complete task"));
    expect(checkbox.getAttribute("aria-checked")).toBe("false");
  });

  it("offers course setup for an empty account without synthetic courses, warnings, or semester progress", async () => {
    const { container } = setup({ empty: true });
    await screen.findByRole("link", { name: /Add course/ });
    expect(container.querySelectorAll(".mc-course-card")).toHaveLength(0);
    expect(container.querySelector(".mc-warning")).toBeNull();
    expect(container.querySelector(".mc-semester-readout")).toBeNull();
  });

  it("shows a load error instead of prototype data when the dashboard API fails", async () => {
    const { container } = setup({ failDashboard: true });
    await screen.findByRole("alert");
    expect(container.querySelector(".mc-dashboard")).toBeNull();
  });

  it("localizes the dashboard and week controls in Chinese", async () => {
    await i18n.changeLanguage("zh");
    setup();
    await screen.findByRole("heading", { name: "本周学习航线" });
    expect(screen.getByRole("button", { name: "下一周" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "任务收件箱" })).toBeTruthy();
    expect(screen.getByText("今天")).toBeTruthy();
  });
});
