import type { Course, Deliverable, Lecture, Slot, StudyTopic, Task } from "@/data/types";
import type { FileEntry } from "@/lib/queries";
import { computeFallBehind } from "@/lib/fall-behind";
import { readDemoData, saveDemoData } from "./store";
import type { DemoData, DemoFile } from "./data";

type Row = Task | Deliverable | Lecture | Slot | StudyTopic;
const json = (value: unknown, status = 200) => Response.json(value, { status });

function filtered<T extends { course_code?: string; status?: string }>(rows: T[], params: URLSearchParams): T[] {
  return rows.filter((row) => (!params.get("course_code") || row.course_code === params.get("course_code"))
    && (!params.get("status") || row.status === params.get("status")));
}

function dashboard(data: DemoData, includeArchived: boolean) {
  const courses = data.courses.filter((course) => includeArchived || !course.archived);
  const codes = new Set(courses.map((course) => course.code));
  const active = <T extends { course_code?: string }>(rows: T[]) => rows.filter((row) => !row.course_code || codes.has(row.course_code));
  return {
    now: new Date().toISOString(), courses, slots: active(data.slots), exams: active(data.exams),
    tasks: active(data.tasks), deliverables: active(data.deliverables), study_topics: active(data.study_topics), lectures: active(data.lectures),
    fall_behind: computeFallBehind(courses, active(data.study_topics), active(data.slots)),
  };
}

function listFiles(data: DemoData, prefix: string): FileEntry[] {
  const parent = prefix ? prefix.replace(/\/$/, "") + "/" : "";
  const entries = new Map<string, FileEntry>();
  for (const file of data.files) {
    if (!file.path.startsWith(parent)) continue;
    const remainder = file.path.slice(parent.length);
    if (!remainder) continue;
    const name = remainder.split("/")[0];
    const path = parent + name;
    if (remainder.includes("/")) {
      entries.set(path, { ...file, file_id: `folder:${path}`, path, name, type: "folder", version_id: null, job_status: null, searchable: false });
    } else {
      entries.set(path, file);
    }
  }
  return [...entries.values()].sort((a, b) => a.type === b.type ? a.name.localeCompare(b.name) : a.type === "folder" ? -1 : 1);
}

function changed(data: DemoData, value: unknown, resource: string, action: string) {
  data.events.unshift({ id: crypto.randomUUID(), kind: `demo:${resource}`, created_at: new Date().toISOString(), payload: { action } });
  saveDemoData(data);
  return json(value);
}

export async function demoFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(path, "https://demo.invalid");
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const [, resource, id, action] = parts;
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(String(init.body)) : {};
  const data = readDemoData();
  const missing = () => json({ detail: "This demo item was removed. Reset the demo to restore the sample semester." }, 404);

  if (url.pathname === "/api/auth/session") return json({ authed: true, totp_enabled: false });
  if (url.pathname === "/api/auth/logout") return json({ ok: true });
  if (url.pathname === "/api/dashboard" && method === "GET") return json(dashboard(data, url.searchParams.get("include_archived") === "true"));
  if (url.pathname === "/api/settings") {
    if (method === "GET") return json(data.settings);
    if (method === "PATCH") {
      Object.assign(data.settings, body);
      return changed(data, data.settings, "settings", "Updated browser-local preferences");
    }
  }
  if (url.pathname === "/api/settings/secrets" && method === "GET") {
    return json({ telegram_bot_token_set: false, telegram_chat_id: null, telegram_webhook_secret_set: false });
  }
  if (resource === "events" && method === "GET") {
    return json(filtered(data.events, url.searchParams).filter((event) => !url.searchParams.get("kind") || event.kind === url.searchParams.get("kind")).slice(0, Number(url.searchParams.get("limit") ?? 100)));
  }
  if (resource === "courses") {
    if (method === "GET") return id ? (data.courses.find((course) => course.code === id) ? json(data.courses.find((course) => course.code === id)) : missing()) : json(data.courses);
    if (method === "POST" && !id) {
      if (data.courses.some((course) => course.code === body.code)) return json({ detail: "This course code already exists." }, 409);
      const course: Course = { exam_weight: 0, archived: false, ...body };
      data.courses.push(course);
      return changed(data, course, "courses", `Created ${course.code}`);
    }
    const course = data.courses.find((item) => item.code === id);
    if (!course) return missing();
    if (method === "PATCH") {
      Object.assign(course, body, { code: id });
      return changed(data, course, "courses", `Updated ${id}`);
    }
    if (method === "DELETE") {
      data.courses = data.courses.filter((item) => item.code !== id);
      data.slots = data.slots.filter((item) => item.course_code !== id);
      data.exams = data.exams.filter((item) => item.course_code !== id);
      data.lectures = data.lectures.filter((item) => item.course_code !== id);
      data.study_topics = data.study_topics.filter((item) => item.course_code !== id);
      data.deliverables = data.deliverables.filter((item) => item.course_code !== id);
      data.tasks = data.tasks.filter((item) => item.course_code !== id);
      return changed(data, {}, "courses", `Deleted ${id}`);
    }
  }
  if (resource === "exams") {
    if (method === "GET") return json(data.exams);
    if (method === "PATCH") {
      if (!data.courses.some((course) => course.code === id)) return missing();
      let exam = data.exams.find((item) => item.course_code === id);
      if (!exam) {
        exam = { course_code: id, scheduled_at: null, duration_min: null, location: null, structure: null, aids_allowed: null, status: "planned", weight_pct: 0 };
        data.exams.push(exam);
      }
      Object.assign(exam, body, { course_code: id });
      return changed(data, exam, "exams", `Updated exam for ${id}`);
    }
  }

  const collections: Record<string, Row[]> = { tasks: data.tasks, deliverables: data.deliverables, lectures: data.lectures, "study-topics": data.study_topics, "schedule-slots": data.slots };
  const rows = collections[resource];
  if (rows) {
    if (method === "GET") return id ? (rows.find((row) => row.id === id) ? json(rows.find((row) => row.id === id)) : missing()) : json(filtered(rows, url.searchParams));
    if (method === "POST" && !id) {
      if (body.course_code && !data.courses.some((course) => course.code === body.course_code)) return missing();
      const defaults: Record<string, object> = { tasks: { status: "open", priority: "med" }, deliverables: { kind: "submission", status: "open" }, lectures: { attended: false, kind: "lecture" }, "study-topics": { status: "not_started", sort_order: rows.length }, "schedule-slots": { room: "", kind: "lecture" } };
      const row = { ...defaults[resource], ...body, id: crypto.randomUUID() } as Row;
      rows.push(row);
      return changed(data, row, resource, `Created ${"title" in row ? row.title : "name" in row ? row.name : resource}`);
    }
    const row = rows.find((item) => item.id === id);
    if (!row) return missing();
    if (method === "DELETE") {
      rows.splice(rows.indexOf(row), 1);
      if (resource === "lectures") data.study_topics.forEach((topic) => { if (topic.lecture_id === id) delete topic.lecture_id; });
      return changed(data, {}, resource, `Deleted ${resource} item`);
    }
    if (method === "PATCH" && !action) {
      Object.assign(row, body, { id });
      return changed(data, row, resource, `Updated ${resource} item`);
    }
    if (method === "POST") {
      if (resource === "tasks" && (action === "complete" || action === "reopen")) {
        Object.assign(row, { status: action === "complete" ? "done" : "open", completed_at: action === "complete" ? new Date().toISOString() : undefined });
      } else if (resource === "deliverables" && (action === "submit" || action === "reopen")) {
        Object.assign(row, { status: action === "submit" ? "submitted" : "open" });
      } else if (resource === "study-topics" && action === "studied") {
        Object.assign(row, { status: "studied", last_reviewed_at: new Date().toISOString() });
      } else {
        return json({ detail: "This action is available in the full application." }, 501);
      }
      return changed(data, row, resource, `${action}: ${"title" in row ? row.title : "name" in row ? row.name : id}`);
    }
  }

  if (resource === "files") {
    if (id === "list" && method === "GET") return json(listFiles(data, url.searchParams.get("prefix") ?? ""));
    if (id === "search" && method === "GET") {
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      return json(data.files.filter((file) => file.type === "file" && `${file.path} ${file.text}`.toLowerCase().includes(q)).map((file) => ({ path: file.path, course_code: data.courses.find((course) => file.path.startsWith(`${course.folder_name || course.code}/`))?.code ?? null, size: file.size, rank: 1, snippet: file.text })));
    }
    if (id === "signed-url" && method === "GET") {
      const file = data.files.find((item) => item.path === url.searchParams.get("path") && item.type === "file");
      return file ? json({ url: file.asset, expires_in: 86400 }) : missing();
    }
    if (id === "lecture-materials" && method === "GET") return json({});
    if (id === "folder" && method === "POST") {
      if (data.files.some((file) => file.path === body.path)) return json({ detail: "A folder with this name already exists." }, 409);
      const folder: DemoFile = { file_id: crypto.randomUUID(), version_id: null, name: String(body.path).split("/").pop()!, path: body.path, type: "folder", asset: "", text: "", job_id: null, job_status: null, job_error_code: null, job_error: null, job_can_retry: false, searchable: false };
      data.files.push(folder);
      return changed(data, { folder: folder.path }, "files", `Created folder ${folder.path}`);
    }
    if (id === "move" && method === "POST") {
      const affected = data.files.filter((file) => file.path === body.from || (body.kind === "folder" && file.path.startsWith(`${body.from}/`)));
      if (!affected.length) return missing();
      if (data.files.some((file) => file.path === body.to)) return json({ detail: "This destination already exists." }, 409);
      const moved = affected.map((file) => {
        const from = file.path;
        file.path = String(body.to) + file.path.slice(String(body.from).length);
        file.name = file.path.split("/").pop()!;
        return { from, to: file.path };
      });
      return changed(data, { moved }, "files", `Renamed ${body.from}`);
    }
    if (!id && method === "DELETE") {
      const target = url.searchParams.get("path");
      const removed = data.files.filter((file) => file.path === target || (url.searchParams.get("kind") === "folder" && file.path.startsWith(`${target}/`)));
      data.files = data.files.filter((file) => !removed.includes(file));
      return changed(data, { deleted: removed.map((file) => file.path) }, "files", `Deleted ${target}`);
    }
  }
  return json({ detail: "This feature requires the full self-hosted application. This demo does not contact external services." }, 501);
}
