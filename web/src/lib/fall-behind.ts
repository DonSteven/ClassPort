import type { Course, CourseCode, Slot, StudyTopic } from "@/data/types";
import { format } from "date-fns";
import { fromZonedTime, toZonedTime } from "date-fns-tz";
import { TZ, fromEasternInput } from "./time";

export type FallBehindSeverity = "ok" | "warn" | "critical";

export type FallBehindItem = {
  course_code: CourseCode;
  topics: StudyTopic[];
  last_covered_on: Date | null;
  next_lecture_at: Date | null;
  severity: FallBehindSeverity;
};

const BEHIND_GRACE_HOURS = 48;
const CRITICAL_WINDOW_HOURS = 48;
const IMMINENT_LECTURE_HOURS = 72;

export function computeFallBehind(
  courses: Course[],
  topics: StudyTopic[],
  slots: Slot[],
  now: Date = new Date()
): FallBehindItem[] {
  return courses.map((c) => {
    const courseTopics = topics.filter((t) => t.course_code === c.code);
    const next = nextLectureAt(c.code, slots, now);
    const imminent = next !== null && (next.getTime() - now.getTime()) / 3600000 <= IMMINENT_LECTURE_HOURS;
    const behind = courseTopics.filter((t) => {
      if (!t.covered_on) return false;
      if (t.status !== "not_started" && t.status !== "in_progress") return false;
      const covered = fromZonedTime(`${t.covered_on}T00:00:00`, TZ);
      if (covered > now) return false;
      const hoursSince = (now.getTime() - covered.getTime()) / 3600000;
      return hoursSince >= BEHIND_GRACE_HOURS || imminent;
    });

    let severity: FallBehindSeverity = "ok";
    if (behind.length > 0) {
      severity = next && (next.getTime() - now.getTime()) / 3600000 <= CRITICAL_WINDOW_HOURS ? "critical" : "warn";
    }

    const lastCovered = behind
      .map((t) => (t.covered_on ? fromZonedTime(`${t.covered_on}T00:00:00`, TZ) : null))
      .filter((d): d is Date => d !== null)
      .sort((a, b) => b.getTime() - a.getTime())[0] ?? null;

    return {
      course_code: c.code,
      topics: behind,
      last_covered_on: lastCovered,
      next_lecture_at: next,
      severity,
    };
  });
}

export function nextLectureAt(code: CourseCode, slots: Slot[], now: Date): Date | null {
  const eastern = toZonedTime(now, TZ);
  const courseSlots = slots.filter(
    (s) => s.course_code === code && (s.kind === "lecture" || s.kind === "exercise")
  );
  if (courseSlots.length === 0) return null;

  // Search up to 7 days ahead
  let candidate: Date | null = null;
  for (let delta = 0; delta < 8; delta++) {
    const target = new Date(eastern);
    target.setDate(eastern.getDate() + delta);
    const weekday = ((target.getDay() + 6) % 7) + 1; // Mon=1..Sun=7
    for (const s of courseSlots) {
      if (s.weekday !== weekday) continue;
      const scheduled = new Date(fromEasternInput(`${format(target, "yyyy-MM-dd")}T${s.start_time}`));
      // Respect starts_on if set
      if (s.starts_on && scheduled < fromZonedTime(`${s.starts_on}T00:00:00`, TZ)) continue;
      if (scheduled <= now) continue;
      if (!candidate || scheduled < candidate) candidate = scheduled;
    }
    if (candidate) break;
  }
  return candidate;
}

export function courseProgress(courseCode: CourseCode, topics: StudyTopic[]): number {
  const list = topics.filter((t) => t.course_code === courseCode);
  if (list.length === 0) return 0;
  const weightMap: Record<StudyTopic["status"], number> = {
    not_started: 0,
    struggling: 0.2,
    in_progress: 0.5,
    studied: 0.9,
    mastered: 1,
  };
  const total = list.reduce((s, t) => s + weightMap[t.status], 0);
  return Math.round((total / list.length) * 100);
}
