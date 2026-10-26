import { useState, type CSSProperties } from "react";
import { ArrowUpRight, CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { Slot } from "@/data/types";
import { courseAccentVar } from "@/lib/theme";
import { addEasternDays, fmtEastern, inEasternTime, isoWeek, semesterWeek, startOfEasternWeek } from "@/lib/time";

const START = 8 * 60;
const END = 18 * 60;
const HEIGHT = 440;
const DAYS = [1, 2, 3, 4, 5];
const toMinutes = (value: string) => {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
};

export function MaterialSchedule({ slots, now, semesterStart }: {
  slots: Slot[];
  now: Date;
  semesterStart?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const [offset, setOffset] = useState(0);
  const locale = i18n.language === "zh" ? "zh-CN" : "en-GB";
  const monday = addEasternDays(startOfEasternWeek(now), offset * 7);
  const friday = addEasternDays(monday, 4);
  const dates = DAYS.map((_, index) => addEasternDays(monday, index));
  const today = fmtEastern(now, "yyyy-MM-dd");
  const eastern = inEasternTime(now);
  const nowMinutes = eastern.getHours() * 60 + eastern.getMinutes();
  const showNow = offset === 0 && eastern.getDay() >= 1 && eastern.getDay() <= 5 && nowMinutes >= START && nowMinutes < END;
  const week = semesterWeek(monday, semesterStart) ?? isoWeek(monday);
  const dateLabel = (date: Date, options: Intl.DateTimeFormatOptions) => date.toLocaleDateString(locale, { ...options, timeZone: "America/New_York" });
  const visible = slots.filter(slot => {
    if (slot.weekday < 1 || slot.weekday > 5) return false;
    const date = fmtEastern(dates[slot.weekday - 1], "yyyy-MM-dd");
    return (!slot.starts_on || date >= slot.starts_on) && (!slot.ends_on || date <= slot.ends_on)
      && toMinutes(slot.end_time) > START && toMinutes(slot.start_time) < END;
  });

  return (
    <section className="me-section" aria-labelledby="material-schedule-title">
      <div className="me-section-heading">
        <div><span className="me-kicker">{t("material.structure")}</span><h2 id="material-schedule-title">{t("material.weekTitle")}<small>{t("material.classCount", { count: visible.length })}</small></h2></div>
        <Link className="me-button me-button-outline" to="/app/courses">{t("material.manageCourses")}<ArrowUpRight size={16} /></Link>
      </div>
      <div className="me-schedule-surface">
        <div className="me-schedule-toolbar">
          <div className="me-week-label"><CalendarDays size={17} /><strong>{dateLabel(monday, { month: "short", day: "numeric" })} – {dateLabel(friday, { month: "short", day: "numeric", year: "numeric" })}</strong><span>{week > 0 ? t("dashboard.topStrip.week", { n: week }) : t("common.preStart")}</span></div>
          <div className="me-week-controls"><button type="button" className="me-icon-button" aria-label={t("material.previousWeek")} onClick={() => setOffset(value => value - 1)}><ChevronLeft size={18} /></button><button type="button" className="me-today-button" onClick={() => setOffset(0)}>{t("dashboard.section.thisWeek")}</button><button type="button" className="me-icon-button" aria-label={t("material.nextWeek")} onClick={() => setOffset(value => value + 1)}><ChevronRight size={18} /></button></div>
        </div>
        <div className="me-schedule-scroll" role="region" aria-label={t("material.timetable")} tabIndex={0}>
          <div className="me-schedule-grid">
            <div className="me-time-corner">ET</div>
            {dates.map((date, index) => <div key={index} className={`me-day-heading${fmtEastern(date, "yyyy-MM-dd") === today ? " me-day-today" : ""}`}><span>{dateLabel(date, { weekday: "short" })}</span><strong>{fmtEastern(date, "d")}</strong></div>)}
            <div className="me-time-rail" style={{ height: HEIGHT }}>{Array.from({ length: 11 }, (_, index) => <span key={index} style={{ top: index * HEIGHT / 10 }}>{String(index + 8).padStart(2, "0")}:00</span>)}</div>
            {DAYS.map((day, index) => <div key={day} className={`me-day-column${fmtEastern(dates[index], "yyyy-MM-dd") === today ? " me-day-column-today" : ""}`} style={{ height: HEIGHT }}>
              {visible.filter(slot => slot.weekday === day).map(slot => {
                const start = Math.max(START, toMinutes(slot.start_time));
                const end = Math.min(END, toMinutes(slot.end_time));
                return <Link key={slot.id} to={`/app/courses/${slot.course_code}?tab=schedule`} className="me-class-block"
                  style={{ "--accent": courseAccentVar(slot.course_code), top: (start - START) / (END - START) * HEIGHT, height: (end - start) / (END - START) * HEIGHT - 2 } as CSSProperties}
                  aria-label={`${slot.course_code} · ${t(`kinds.slot.${slot.kind}`)} · ${slot.start_time.slice(0, 5)}–${slot.end_time.slice(0, 5)} · ${slot.room || ""}`}>
                  <span className="me-class-top"><strong>{slot.course_code}</strong><span>{t(`kinds.slot.${slot.kind}`)}</span></span><span className="me-class-meta"><span>{slot.start_time.slice(0, 5)}</span><span>{slot.room}</span></span>
                </Link>;
              })}
            </div>)}
            {showNow && <div className="me-now-line" style={{ top: 68 + (nowMinutes - START) / (END - START) * HEIGHT }}><span>{fmtEastern(now, "HH:mm")}</span></div>}
          </div>
        </div>
        <div className="me-schedule-footer"><span aria-hidden="true">08:00 – 18:00 ·</span>{t("material.easternTime")}</div>
      </div>
    </section>
  );
}
