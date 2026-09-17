import {
  differenceInCalendarDays,
  differenceInHours,
  differenceInMinutes,
  isSameWeek,
  parseISO,
} from "date-fns";
import { formatInTimeZone, fromZonedTime, toZonedTime } from "date-fns-tz";

// Course calendars use US Eastern Time, including its daylight-saving rules.
export const TZ = "America/New_York";
export const now = () => new Date();

export function inEasternTime(d: Date | string): Date {
  return toZonedTime(d, TZ);
}

export function fromEasternInput(local: string): string {
  const instant = fromZonedTime(local, TZ);
  const requested = Date.parse(`${local}Z`);
  const actual = Date.parse(formatInTimeZone(instant, TZ, "yyyy-MM-dd'T'HH:mm:ss.SSS") + "Z");
  // Roll a missing spring-forward wall time forward, matching Python ZoneInfo.
  const adjusted = new Date(instant.getTime() + requested - actual);
  // Use the first occurrence of a repeated autumn time, as Python's fold=0 does.
  const earlier = new Date(adjusted.getTime() - 3600000);
  const earlierWall = Date.parse(formatInTimeZone(earlier, TZ, "yyyy-MM-dd'T'HH:mm:ss.SSS") + "Z");
  return (earlierWall === requested ? earlier : adjusted).toISOString();
}

export function fmtEastern(d: Date | string, pattern: string) {
  const date = typeof d === "string" && /^\d{4}-\d{2}-\d{2}$/.test(d)
    ? fromZonedTime(`${d}T00:00:00`, TZ)
    : typeof d === "string" ? new Date(d) : d;
  return formatInTimeZone(date, TZ, pattern);
}

export function fmtDate(d: Date | string) {
  return fmtEastern(d, "EEE, d MMM yyyy");
}

export function fmtDateShort(d: Date | string) {
  return fmtEastern(d, "d MMM");
}

export function fmtDateTime(d: Date | string) {
  return fmtEastern(d, "EEE d MMM, HH:mm");
}

export function fmtTime(d: Date | string) {
  return fmtEastern(d, "HH:mm");
}

export function fmtIsoDate(d: Date) {
  return fmtEastern(d, "yyyy-MM-dd");
}

export function easternCalendarDays(target: Date, reference: Date): number {
  return differenceInCalendarDays(inEasternTime(target), inEasternTime(reference));
}

export function startOfEasternWeek(d: Date): Date {
  const out = inEasternTime(d);
  const day = out.getDay() || 7;
  out.setDate(out.getDate() - (day - 1));
  out.setHours(0, 0, 0, 0);
  return fromZonedTime(out, TZ);
}

export function addEasternDays(d: Date, days: number): Date {
  const out = inEasternTime(d);
  out.setDate(out.getDate() + days);
  return fromZonedTime(out, TZ);
}

export type RelativeSeverity = "done" | "past" | "urgent" | "soon" | "later" | "far";

export function relative(
  target: Date | string,
  reference: Date = now()
): { label: string; severity: RelativeSeverity } {
  const t = typeof target === "string" ? new Date(target) : target;
  const mins = differenceInMinutes(t, reference);
  const hours = differenceInHours(t, reference);
  const days = easternCalendarDays(t, reference);

  if (mins < -60) {
    if (days <= -1) return { label: `${Math.abs(days)}d ago`, severity: "past" };
    return { label: `${Math.abs(hours)}h ago`, severity: "past" };
  }
  if (mins < 0) return { label: "just now", severity: "past" };
  if (mins < 60) return { label: `in ${mins}m`, severity: "urgent" };
  if (hours < 24) return { label: `in ${hours}h`, severity: hours < 6 ? "urgent" : "soon" };
  if (days === 0) return { label: "today", severity: "urgent" };
  if (days === 1) return { label: "tomorrow", severity: "soon" };
  if (days <= 3) return { label: `in ${days}d`, severity: "soon" };
  if (days <= 7) return { label: `in ${days}d`, severity: "later" };
  return { label: `in ${days}d`, severity: "far" };
}

export function humanWhen(d: Date | string): string {
  const date = typeof d === "string" ? new Date(d) : d;
  const reference = now();
  const days = easternCalendarDays(date, reference);
  if (days === 0) return `Today, ${fmtTime(date)}`;
  if (days === 1) return `Tomorrow, ${fmtTime(date)}`;
  if (isSameWeek(inEasternTime(date), inEasternTime(reference), { weekStartsOn: 1 })) {
    return fmtEastern(date, "EEEE, HH:mm");
  }
  return fmtDateTime(date);
}

export const weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const weekdayLabelsShort = ["M", "T", "W", "T", "F", "S", "S"];

// Calendar weeks remain seven local dates across daylight-saving changes.
export function semesterWeek(now: Date, startIso: string | null | undefined): number | null {
  if (!startIso) return null;
  const start = parseISO(startIso);
  if (Number.isNaN(start.getTime())) return null;
  const days = differenceInCalendarDays(inEasternTime(now), start);
  return days < 0 ? 0 : Math.floor(days / 7) + 1;
}

export function isoWeek(d: Date): number {
  const eastern = inEasternTime(d);
  const date = new Date(Date.UTC(eastern.getFullYear(), eastern.getMonth(), eastern.getDate()));
  const dayNum = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
}
