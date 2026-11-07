import { addEasternDays, fmtIsoDate, fromEasternInput, startOfEasternWeek, TZ } from "@/lib/time";
import type { AppSettings, Course, Deliverable, Exam, Lecture, Slot, StudyTopic, Task } from "@/data/types";
import type { ActivityEvent, FileEntry } from "@/lib/queries";
import statisticsUrl from "./materials/statistics-notes.md?url&no-inline";
import statisticsText from "./materials/statistics-notes.md?raw";
import transactionsUrl from "./materials/transactions.md?url&no-inline";
import transactionsText from "./materials/transactions.md?raw";
import welcomeUrl from "./materials/welcome.md?url&no-inline";
import welcomeText from "./materials/welcome.md?raw";

export type DemoFile = FileEntry & { asset: string; text: string };
export type DemoData = {
  settings: AppSettings;
  courses: Course[];
  slots: Slot[];
  exams: Exam[];
  deliverables: Deliverable[];
  tasks: Task[];
  study_topics: StudyTopic[];
  lectures: Lecture[];
  files: DemoFile[];
  events: ActivityEvent[];
};

export function createDemoData(reference = new Date()): DemoData {
  const day = (offset: number) => fmtIsoDate(addEasternDays(reference, offset));
  const at = (offset: number, time = "17:00") => fromEasternInput(`${day(offset)}T${time}`);
  const courses: Course[] = [
    { code: "STAT201", full_name: "Probability & Statistics", short_name: "Statistics", module_code: "STAT-02", ects: 6, language: "EN", color_hex: "#3185e8", folder_name: "STAT201", exam_weight: 60 },
    { code: "CS204", full_name: "Database Systems", short_name: "Databases", module_code: "CS-04", ects: 6, language: "EN", color_hex: "#14a878", folder_name: "CS204", exam_weight: 50 },
    { code: "SE301", full_name: "Software Engineering", short_name: "Software Eng.", module_code: "SE-03", ects: 5, language: "EN", color_hex: "#d28b21", folder_name: "SE301", exam_weight: 40 },
    { code: "ML302", full_name: "Applied Machine Learning", short_name: "Machine Learning", module_code: "ML-02", ects: 6, language: "EN", color_hex: "#c35d86", folder_name: "ML302", exam_weight: 50 },
  ];
  const topicNames = [
    ["Random variables", "Gamma distribution", "Central limit theorem", "Confidence intervals", "Maximum likelihood"],
    ["Relational algebra", "SQL joins", "Normalization", "Transactions & isolation", "Query optimization"],
    ["Requirements & user stories", "API design", "Integration testing", "Continuous delivery", "Observability"],
    ["Train / validation splits", "Linear regression", "Regularization", "Cross-validation", "Model interpretation"],
  ];
  const topicStatuses: StudyTopic["status"][][] = [
    ["mastered", "studied", "in_progress", "not_started", "not_started"],
    ["mastered", "mastered", "studied", "studied", "not_started"],
    ["studied", "in_progress", "not_started", "not_started", "struggling"],
    ["mastered", "studied", "studied", "in_progress", "not_started"],
  ];
  const study_topics: StudyTopic[] = courses.flatMap((course, i) => topicNames[i].map((name, j) => ({
    id: `topic-${course.code}-${j}`, course_code: course.code, chapter: `Chapter ${j + 1}`, name,
    kind: "lecture", covered_on: day(j - 6), lecture_id: `lecture-${course.code}-${Math.min(j, 2)}`,
    status: topicStatuses[i][j],
    confidence: j < 2 ? 4 : 2, sort_order: j,
  })));
  const lectures: Lecture[] = courses.flatMap((course, i) => [0, 1, 2].map((j) => ({
    id: `lecture-${course.code}-${j}`, course_code: course.code, number: j + 1,
    held_on: day(j * 2 - 6), title: topicNames[i][j], kind: "lecture", attended: j !== 2,
  })));
  const slots: Slot[] = courses.flatMap((course, i) => [0, 1].map((j) => ({
    id: `slot-${course.code}-${j}`, course_code: course.code, kind: j ? "exercise" : "lecture",
    weekday: ((i + j * 2) % 5 + 1) as Slot["weekday"],
    start_time: j ? "14:00" : `${String(9 + i).padStart(2, "0")}:00`,
    end_time: j ? "15:30" : `${String(10 + i).padStart(2, "0")}:30`, room: j ? "Workshop 2" : `Room ${201 + i}`,
  })));
  const files: DemoFile[] = [
    { file_id: "file-stats", name: "course-notes.md", path: "STAT201/course-notes.md", asset: statisticsUrl, text: statisticsText },
    { file_id: "file-db", name: "transactions.md", path: "CS204/transactions.md", asset: transactionsUrl, text: transactionsText },
    { file_id: "file-readme", name: "welcome.md", path: "welcome.md", asset: welcomeUrl, text: welcomeText },
  ].map((file) => ({ ...file, type: "file", version_id: "demo", updated_at: at(-1), size: new TextEncoder().encode(file.text).length, content_type: "text/markdown", job_id: null, job_status: "succeeded", job_error_code: null, job_error: null, job_can_retry: false, searchable: true }));

  return {
    settings: { display_name: "Alex Student", monogram: "A", institution: "ClassPort Demo", semester_label: "Sample semester", semester_start: fmtIsoDate(addEasternDays(startOfEasternWeek(reference), -28)), semester_end: fmtIsoDate(addEasternDays(startOfEasternWeek(reference), 84)), timezone: TZ, locale: "en-US", theme: "zine" },
    courses, slots, study_topics, lectures, files,
    exams: courses.map((course, i) => ({ course_code: course.code, scheduled_at: at(14 + i * 3, "10:00"), duration_min: 90, location: "Main Hall", structure: "Written exam: concepts and applied problems", aids_allowed: "One handwritten reference sheet", status: "confirmed", weight_pct: course.exam_weight ?? 50 })),
    deliverables: [
      { id: "deliverable-stats", course_code: "STAT201", kind: "submission", name: "Problem set: distributions & estimation", due_at: at(2), status: "in_progress", weight_info: "10% of course grade" },
      { id: "deliverable-db", course_code: "CS204", kind: "lab", name: "Build a transactional booking system", due_at: at(4), status: "open" },
      { id: "deliverable-se", course_code: "SE301", kind: "project", name: "Release the study dashboard", due_at: at(6), status: "in_progress" },
      { id: "deliverable-ml", course_code: "ML302", kind: "submission", name: "Compare regularized regression models", due_at: at(-1), status: "submitted" },
    ],
    tasks: [
      { id: "task-stats", course_code: "STAT201", title: "Review the Gamma distribution", description: "Read the sample notes, then mark the topic as studied.", due_at: at(1), status: "open", priority: "high", tags: ["review"] },
      { id: "task-db", course_code: "CS204", title: "Practice SQL joins and explain the query plan", due_at: at(2), status: "in_progress", priority: "med" },
      { id: "task-se", course_code: "SE301", title: "Add integration tests for task completion", due_at: at(3), status: "open", priority: "high" },
      { id: "task-ml", course_code: "ML302", title: "Prepare a cross-validation experiment", due_at: at(4), status: "open", priority: "med" },
      { id: "task-week", title: "Plan the next study session", due_at: at(1, "18:00"), status: "open", priority: "low" },
      { id: "task-done", course_code: "STAT201", title: "Summarize the central limit theorem", due_at: at(-1), completed_at: at(-1), status: "done", priority: "med" },
    ],
    events: [{ id: "event-welcome", kind: "demo:ready", created_at: reference.toISOString(), payload: { action: "Sample semester loaded. Changes stay in this browser." } }],
  };
}
