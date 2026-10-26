import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/lib/i18n";
import { MaterialDashboard } from "./dashboard-material";
import type { DashboardSummary } from "@/lib/queries";

const clients: QueryClient[] = [];
beforeEach(async () => { await i18n.changeLanguage("en"); });
afterEach(() => clients.splice(0).forEach(client => client.clear()));

function fixture(): DashboardSummary {
  const topic = { id: "topic-open", course_code: "CS101", name: "Graph traversal", status: "not_started" as const, covered_on: "2026-10-23" };
  return {
    now: "2026-10-26T14:15:00Z",
    courses: [
      { code: "CS101", full_name: "Discrete Mathematics", ects: 6 },
      { code: "DAT2", full_name: "Data Systems", ects: 4 },
    ],
    slots: [
      { id: "active", course_code: "CS101", kind: "lecture", weekday: 1, start_time: "11:00:00", end_time: "12:30:00", room: "B 204", ends_on: "2026-10-30" },
      { id: "next-week", course_code: "DAT2", kind: "lab", weekday: 1, start_time: "09:00", end_time: "10:30", room: "Lab", starts_on: "2026-11-02" },
    ],
    study_topics: [topic, { id: "studied", course_code: "CS101", name: "Sets", status: "studied" }, { id: "mastered", course_code: "DAT2", name: "SQL", status: "mastered" }],
    tasks: [
      { id: "task-open", title: "Review graph traversal", course_code: "CS101", status: "open", priority: "high" },
      { id: "task-done", title: "Read the slides", status: "done", priority: "low" },
      { id: "task-skipped", title: "Skipped exercise", status: "skipped", priority: "low" },
    ],
    deliverables: [{ id: "due", course_code: "CS101", kind: "submission", name: "Problem Set 4", due_at: "2026-10-28T20:00:00Z", status: "open" }],
    fall_behind: [{ course_code: "CS101", severity: "critical", topics: [topic], last_covered_on: "2026-10-23", next_lecture_at: "2026-10-26T15:00:00Z" }],
    lectures: [], exams: [],
  };
}

function setup(failComplete = false) {
  const data = fixture();
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/dashboard") return json(data);
    if (path === "/api/settings") return json({ display_name: "Taylor Student", theme: "material", semester_start: "2026-08-31" });
    if (path === "/api/tasks/task-open/complete") {
      if (failComplete) return json({ detail: "Could not complete task" }, 500);
      data.tasks[0].status = "done";
      return json(data.tasks[0]);
    }
    if (path === "/api/tasks/task-open/reopen") {
      data.tasks[0].status = "open";
      return json(data.tasks[0]);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  const result = render(<QueryClientProvider client={client}><MemoryRouter><MaterialDashboard /></MemoryRouter></QueryClientProvider>);
  return { ...result, fetch, data };
}

describe("Material dashboard integration", () => {
  it("uses server courses, weighted progress, catch-up links, and semester-bounded slots across DST", async () => {
    const { container } = setup();
    await screen.findByRole("heading", { name: /Good morning, Taylor/ });
    expect(screen.getByRole("heading", { name: "Data Systems" })).toBeTruthy();
    expect(container.querySelectorAll(".me-course-card")).toHaveLength(2);
    expect(screen.getByRole("progressbar", { name: /CS101/ }).getAttribute("aria-valuenow")).toBe("45");
    expect(screen.getByRole("link", { name: /Review topics/ }).getAttribute("href")).toBe("/app/courses/CS101?tab=study");
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(container.querySelectorAll(".me-class-block")).toHaveLength(1);
    expect(container.querySelector(".me-class-block")?.textContent).toContain("CS101");
    fireEvent.click(screen.getByRole("button", { name: "Next week" }));
    expect(container.querySelectorAll(".me-class-block")).toHaveLength(1);
    expect(container.querySelector(".me-class-block")?.textContent).toContain("DAT2");
    expect(container.querySelector(".me-now-line")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "This week" }));
    expect(container.querySelector(".me-class-block")?.textContent).toContain("CS101");
  });

  it("completes and reopens tasks through the application API and refreshes counts", async () => {
    const { fetch } = setup();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Complete: Review graph traversal" }));
    await screen.findByText("2/2");
    expect(screen.queryByRole("checkbox", { name: "Complete: Review graph traversal" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Completed/ }));
    const reopen = screen.getByRole("checkbox", { name: "Reopen: Review graph traversal" });
    expect(reopen.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(reopen);
    await screen.findByText("1/2");
    expect(fetch.mock.calls.some(([path]) => path === "/api/tasks/task-open/complete")).toBe(true);
    expect(fetch.mock.calls.some(([path]) => path === "/api/tasks/task-open/reopen")).toBe(true);
  });

  it("keeps a task open when its server update fails", async () => {
    setup(true);
    const checkbox = await screen.findByRole("checkbox", { name: "Complete: Review graph traversal" });
    fireEvent.click(checkbox);
    await waitFor(() => expect((checkbox as HTMLButtonElement).disabled).toBe(false));
    expect(checkbox.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText("1/2")).toBeTruthy();
  });
});
