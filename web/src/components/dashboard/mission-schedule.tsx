import { useState, type CSSProperties } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { Course, Slot } from "@/data/types";
import { courseAccentVar } from "@/lib/theme";
import { addEasternDays, fmtEastern, inEasternTime, startOfEasternWeek, TZ } from "@/lib/time";

const minutes = (value: string) => {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
};

export function MissionSchedule({ slots, courses, now }: { slots: Slot[]; courses: Course[]; now: Date }) {
  const { t, i18n } = useTranslation();
  const [offset, setOffset] = useState(0);
  const locale = i18n.language === "zh" ? "zh-CN" : "en-GB";
  const monday = addEasternDays(startOfEasternWeek(now), offset * 7);
  const dates = Array.from({ length: 5 }, (_, day) => addEasternDays(monday, day));
  const today = fmtEastern(now, "yyyy-MM-dd");
  const eastern = inEasternTime(now);
  const nowMinutes = eastern.getHours() * 60 + eastern.getMinutes();
  const showNow = offset === 0 && eastern.getDay() >= 1 && eastern.getDay() <= 5 && nowMinutes >= 480 && nowMinutes < 1080;
  const dateLabel = (date: Date, options: Intl.DateTimeFormatOptions) => date.toLocaleDateString(locale, { ...options, timeZone: TZ });
  const visible = slots.filter(slot => {
    if (slot.weekday < 1 || slot.weekday > 5) return false;
    const date = fmtEastern(dates[slot.weekday - 1], "yyyy-MM-dd");
    return (!slot.starts_on || slot.starts_on <= date) && (!slot.ends_on || slot.ends_on >= date)
      && minutes(slot.end_time) > 480 && minutes(slot.start_time) < 1080;
  });

  return <section className="mc-section mc-schedule" aria-label={t("mission.weekTitle")}>
    <div className="mc-section-heading"><div className="flex items-center gap-3"><span className="mc-section-number">01</span><h2>{t("mission.weekTitle")}</h2></div><div className="mc-week-controls"><span className="mc-week-range" aria-live="polite">{dateLabel(dates[0], { day: "numeric", month: "short" })} — {dateLabel(dates[4], { day: "numeric", month: "short" })}</span><button type="button" className="mc-today-button" onClick={() => setOffset(0)}>{t("dashboard.section.thisWeek")}</button><div className="mc-week-arrows"><button type="button" aria-label={t("mission.previousWeek")} onClick={() => setOffset(value => value - 1)}><ChevronLeft size={16} /></button><button type="button" aria-label={t("mission.nextWeek")} onClick={() => setOffset(value => value + 1)}><ChevronRight size={16} /></button></div></div></div>
    <div className="mc-calendar-surface"><div className="mc-calendar-scroll" tabIndex={0} role="region" aria-label={t("mission.timetable")}><div className="mc-calendar">
      <div className="mc-day-headings"><div className="mc-timezone">ET<br /><span>{t("mission.newYork")}</span></div>{dates.map(date => <div key={date.toISOString()} className="mc-day-heading" data-today={fmtEastern(date, "yyyy-MM-dd") === today}><span>{dateLabel(date, { weekday: "short" })}</span><b>{fmtEastern(date, "dd")}</b>{fmtEastern(date, "yyyy-MM-dd") === today && <span className="mc-today-tag">{t("mission.today")}</span>}</div>)}</div>
      <div className="mc-time-body"><div className="mc-time-labels">{Array.from({ length: 11 }, (_, hour) => <span key={hour} style={{ top: `${hour * 10}%` }}>{String(hour + 8).padStart(2, "0")}:00</span>)}</div><div className="mc-days-body">{dates.map((date, day) => <div key={date.toISOString()} className="mc-day-column" data-today={fmtEastern(date, "yyyy-MM-dd") === today}>
        {visible.filter(slot => slot.weekday === day + 1).map(slot => {
          const start = Math.max(480, minutes(slot.start_time));
          const end = Math.min(1080, minutes(slot.end_time));
          return <Link key={slot.id} to={`/app/courses/${slot.course_code}?tab=schedule`} className="mc-slot" style={{ "--accent": courseAccentVar(slot.course_code), top: `${(start - 480) / 6}%`, height: `${(end - start) / 6}%` } as CSSProperties} aria-label={`${slot.course_code} · ${t(`kinds.slot.${slot.kind}`)} · ${slot.start_time.slice(0, 5)}–${slot.end_time.slice(0, 5)} · ${slot.room}`}><span className="mc-slot-top"><b>{slot.course_code}</b><span>{t(`kinds.slot.${slot.kind}`)}</span></span><span className="mc-slot-bottom"><span>{slot.start_time.slice(0, 5)}</span><span>{slot.room}</span></span></Link>;
        })}
      </div>)}</div>{showNow && <div className="mc-now-line" style={{ top: `${(nowMinutes - 480) / 6}%` }}><span>{fmtEastern(now, "HH:mm")}</span><i /></div>}</div>
    </div></div><div className="mc-calendar-footer"><div>{courses.map(course => <span key={course.code} style={{ "--accent": courseAccentVar(course.code) } as CSSProperties}><i />{course.code}</span>)}</div><span className="mc-schedule-count"><CalendarDays size={12} />{t("mission.sessions", { count: visible.length })}</span><span className="mc-scroll-hint">{t("mission.scrollSchedule")}</span></div></div>
  </section>;
}
