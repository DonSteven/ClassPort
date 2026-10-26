import { useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Check, Clock3, Crosshair, ListChecks, Loader2, Plus, Radio, TriangleAlert, TrendingUp, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { useAppSettings, useCompleteTask, useDashboard, useReopenTask } from "@/lib/queries";
import { courseProgress } from "@/lib/fall-behind";
import { courseAccentVar } from "@/lib/theme";
import { easternCalendarDays, fmtEastern, fromEasternInput, inEasternTime, relative, semesterWeek, TZ } from "@/lib/time";
import { MissionSchedule } from "@/components/dashboard/mission-schedule";
import { TaskForm } from "@/components/forms/task-form";
import type { Course, Task } from "@/data/types";

function courseStyle(code: string): CSSProperties {
  return { "--accent": courseAccentVar(code) } as CSSProperties;
}

function SectionHeading({ number, title, aside }: { number: string; title: string; aside?: ReactNode }) {
  return <div className="mc-section-heading"><div className="flex items-center gap-3"><span className="mc-section-number">{number}</span><h2>{title}</h2></div>{aside}</div>;
}

function Metric({ icon: Icon, label, value, unit, hint, tone, detail }: { icon: LucideIcon; label: string; value: number | string; unit: string; hint: string; tone: "default" | "warn" | "critical" | "ok"; detail: string }) {
  return <article className="mc-metric" data-tone={tone}><div className="mc-metric-label"><Icon size={15} aria-hidden="true" /><h2>{label}</h2></div><div className="mc-metric-value"><strong>{typeof value === "number" ? String(value).padStart(2, "0") : value}</strong><span>{unit}</span></div><p>{hint}</p><div className="mc-metric-footer"><span className="mc-tiny-line" />{detail}</div></article>;
}

export function MissionControlDashboard() {
  const { t, i18n } = useTranslation();
  const dashboard = useDashboard();
  const settings = useAppSettings();
  if (dashboard.isPending) return <div className="mc-loading" role="status"><Loader2 size={22} className="animate-spin" /><span>{t("common.loading")}</span></div>;
  if (dashboard.error || !dashboard.data) return <div className="mc-loading" role="alert">{t("dashboard.loadFailed")}</div>;

  const data = dashboard.data;
  const profile = settings.data;
  const now = new Date(data.now);
  const locale = i18n.language === "zh" ? "zh-CN" : "en-GB";
  const firstName = profile?.display_name?.trim().split(/\s+/)[0] || t("dashboard.greetingFallbackName");
  const hour = inEasternTime(now).getHours();
  const period = hour < 5 ? "Night" : hour < 12 ? "Morning" : hour < 18 ? "Afternoon" : "Evening";
  const next = data.fall_behind.filter(item => item.next_lecture_at && Date.parse(item.next_lecture_at) > now.getTime()).sort((a, b) => Date.parse(a.next_lecture_at!) - Date.parse(b.next_lecture_at!))[0];
  const behind = data.fall_behind.filter(item => item.severity !== "ok" && item.topics.length > 0);
  const behindTopics = behind.reduce((sum, item) => sum + item.topics.length, 0);
  const deadlines = data.deliverables.filter(item => item.status === "open" || item.status === "in_progress").sort((a, b) => Date.parse(a.due_at) - Date.parse(b.due_at));
  const openTasks = data.tasks.filter(task => task.status !== "done" && task.status !== "skipped");
  const nextDue = deadlines[0];
  const dueDays = nextDue ? easternCalendarDays(new Date(nextDue.due_at), now) : null;
  const overdue = nextDue && Date.parse(nextDue.due_at) < now.getTime();
  const progress = data.courses.length ? Math.round(data.courses.reduce((sum, course) => sum + courseProgress(course.code, data.study_topics), 0) / data.courses.length) : 0;
  const highPriority = openTasks.filter(task => task.priority === "high" || task.priority === "urgent").length;
  const week = semesterWeek(now, profile?.semester_start);
  const totalWeeks = profile?.semester_start && profile.semester_end ? Math.ceil((easternCalendarDays(new Date(fromEasternInput(`${profile.semester_end}T00:00`)), new Date(fromEasternInput(`${profile.semester_start}T00:00`))) + 1) / 7) : null;
  const segments = week !== null && totalWeeks && totalWeeks > 0 ? Math.min(16, Math.max(0, Math.ceil(week / totalWeeks * 16))) : null;

  return <div className="mc-dashboard">
    <section className="mc-greeting" aria-label={t("mission.dailyPlan")}><div><div className="mc-eyebrow"><span className="mc-crosshair"><Crosshair size={13} /></span>{t("mission.dailyPlan")}<span className="mc-eyebrow-divider">/</span><span>{now.toLocaleDateString(locale, { timeZone: TZ, day: "2-digit", month: "short", year: "numeric" })}</span></div><h1>{t(`dashboard.greeting${period}`, { name: firstName })}</h1><div className="mc-greeting-subline"><p>{t("mission.greetingSubline")}</p>{next && <Link className="mc-next-chip" to={`/app/courses/${next.course_code}`}><span className="mc-next-label">{t("dashboard.nextUp")}</span><b style={courseStyle(next.course_code)}>{next.course_code}</b><span>{relative(next.next_lecture_at!, now).label}</span><ArrowUpRight size={13} /></Link>}</div></div>{week !== null && <div className="mc-semester-readout"><span>{t("mission.semesterTrajectory")}</span><div><strong>{String(week).padStart(2, "0")}</strong><span>{totalWeeks && totalWeeks > 0 ? `/ ${totalWeeks}` : ""}<small>{t("common.week")}</small></span><ArrowDownRight size={24} /></div>{segments !== null && <div className="mc-segments" aria-hidden="true">{Array.from({ length: 16 }, (_, i) => <i key={i} data-filled={i < segments} />)}</div>}</div>}</section>

    {behind.length > 0 && <section className="mc-warning" aria-labelledby="mission-warning-title"><div className="mc-warning-icon"><TriangleAlert size={21} /></div><div className="mc-warning-copy"><div className="mc-warning-title"><span className="mc-overline">{t("mission.attentionRequired")}</span><span className="mc-warning-label">{t("mission.catchUpWindow")}</span></div><h2 id="mission-warning-title">{t("mission.courseCorrection")}</h2><ul>{behind.slice(0, 2).map(item => <li key={item.course_code}>{t("dashboard.falling_behind", { code: item.course_code, count: item.topics.length })}{item.next_lecture_at && ` · ${t("courseDetail.nextLecture")} ${relative(item.next_lecture_at, now).label}`}</li>)}</ul></div><div className="mc-warning-actions"><div className="mc-severity-dots" aria-label={t("mission.courseReadiness")}>{data.courses.map(course => { const severity = data.fall_behind.find(item => item.course_code === course.code)?.severity ?? "ok"; return <span key={course.code}><i data-severity={severity} /><span>{course.code}</span><span className="sr-only">{t(`mission.readiness.${severity}`)}</span></span>; })}</div><Link className="mc-warning-button" to={`/app/courses/${behind[0].course_code}?tab=study`}>{t("mission.makeStudyPlan")}<ArrowRight size={15} /></Link></div></section>}

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label={t("mission.studyMetrics")}>
      <Metric icon={Clock3} label={t("dashboard.tile.nextDeadline")} value={dueDays === null ? "—" : Math.abs(dueDays)} unit={dueDays === null ? "" : t(overdue ? "mission.daysOverdue" : "mission.days")} hint={nextDue ? `${nextDue.course_code} · ${nextDue.name}` : t("dashboard.tile.noOpenDeliverables")} tone={dueDays === null ? "default" : overdue || dueDays <= 1 ? "critical" : dueDays <= 3 ? "warn" : "default"} detail={t(nextDue ? "mission.submissionApproaching" : "mission.allClear")} />
      <Metric icon={ListChecks} label={t("mission.taskQueue")} value={openTasks.length} unit={t("mission.open")} hint={t("mission.highPriority", { count: highPriority })} tone="default" detail={t("mission.readyWhenYouAre")} />
      <Metric icon={TriangleAlert} label={t("mission.catchUpNeeded")} value={behindTopics} unit={t("common.topics")} hint={t("mission.coursesNeedAttention", { count: behind.length })} tone={behind.some(item => item.severity === "critical") ? "critical" : behind.length ? "warn" : "ok"} detail={t(behind.length ? "mission.reviewBeforeLecture" : "mission.allOnCourse")} />
      <Metric icon={TrendingUp} label={t("mission.studyProgress")} value={progress} unit="%" hint={t("mission.acrossCourses", { count: data.courses.length })} tone="ok" detail={t("mission.forwardMotion")} />
    </section>

    <MissionSchedule slots={data.slots} courses={data.courses} now={now} />

    <section className="mc-section" aria-label={t("mission.yourCourses")}><SectionHeading number="02" title={t("mission.yourCourses")} aside={<span className="mc-heading-note">{data.courses.reduce((sum, course) => sum + (course.ects ?? 0), 0)} ECTS{profile?.semester_label && <><span>/</span>{profile.semester_label}</>}</span>} />{data.courses.length === 0 ? <div className="mc-empty"><p>{t("mission.noCourses")}</p><Link className="mc-primary-button" to="/app/courses"><Plus size={15} />{t("forms.course.titleAdd")}</Link></div> : <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">{data.courses.map(course => {
      const ownTopics = data.study_topics.filter(topic => topic.course_code === course.code);
      const studied = ownTopics.filter(topic => topic.status === "studied" || topic.status === "mastered").length;
      const percent = courseProgress(course.code, data.study_topics);
      const upcoming = data.fall_behind.find(item => item.course_code === course.code)?.next_lecture_at;
      const ready = !behind.some(item => item.course_code === course.code);
      return <Link key={course.code} className="mc-course-card" style={courseStyle(course.code)} to={`/app/courses/${course.code}`}><div className="mc-course-card-top"><span className="mc-course-code">{course.code}</span><ArrowUpRight size={17} /></div><h3>{course.full_name}</h3><p className="mc-course-meta">{[course.module_code, course.ects != null ? `${course.ects} ECTS` : null, course.language].filter(Boolean).join(" / ")}</p><div className="mc-course-next"><Radio size={13} /><span>{t("courseDetail.nextLecture")}</span><b>{upcoming ? relative(upcoming, now).label : t("mission.unscheduled")}</b></div><div className="mc-course-progress-copy"><span>{t("mission.studyProgress")}</span><strong>{percent}%</strong></div><div className="mc-progress" role="progressbar" aria-label={`${course.code} ${t("mission.studyProgress")}`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${percent}%` }} /></div><div className="mc-course-footer" data-ready={ready}>{ready ? <Check size={12} /> : <TriangleAlert size={12} />}<span>{t(ready ? "mission.onCourse" : "mission.catchUpNeeded")}</span><span>{t("mission.topicsStudied", { done: studied, total: ownTopics.length })}</span></div></Link>;
    })}</div>}</section>

    <div className="mc-bottom-panels grid gap-5 xl:grid-cols-2"><section className="mc-list-panel" aria-label={t("mission.upcomingDeadlines")}><SectionHeading number="03" title={t("mission.upcomingDeadlines")} aside={<Link className="mc-counter" to="/app/deliverables" aria-label={t("nav.deadlines")}>{String(deadlines.length).padStart(2, "0")}</Link>} /><div className="mc-panel-body">{deadlines.length === 0 ? <div className="mc-empty"><Check size={20} />{t("dashboard.nothingDue")}</div> : deadlines.slice(0, 5).map(item => <Link key={item.id} className="mc-deadline-row" to={`/app/courses/${item.course_code}?tab=deliverables`}><span className="mc-deadline-date"><b>{fmtEastern(item.due_at, "dd")}</b><small>{new Date(item.due_at).toLocaleDateString(locale, { timeZone: TZ, month: "short" })}</small></span><span className="mc-row-copy"><strong>{item.name}</strong><span><b style={courseStyle(item.course_code)}>{item.course_code}</b><span className="mc-slash">/</span>{t(`forms.deliverable.kind${item.kind.charAt(0).toUpperCase()}${item.kind.slice(1)}`)}</span></span><span className="mc-countdown" data-urgent={easternCalendarDays(new Date(item.due_at), now) <= 3}>{relative(item.due_at, now).label}</span><ArrowUpRight size={14} className="mc-row-arrow" /></Link>)}</div><div className="mc-panel-footnote"><Clock3 size={12} />{t("mission.easternTime")}<span>{t("mission.horizon")}</span></div></section><MissionTasks tasks={data.tasks} courses={data.courses} /></div>
    <footer className="mc-page-footer"><span><Crosshair size={12} />{t("mission.footer")}</span><span>CLASSPORT <i>/</i> MISSION CONTROL</span></footer>
  </div>;
}

function MissionTasks({ tasks, courses }: { tasks: Task[]; courses: Course[] }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<"open" | "done" | "all">("open");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const complete = useCompleteTask();
  const reopen = useReopenTask();
  const visible = tasks.filter(task => task.status !== "skipped" && (filter === "all" || (filter === "done" ? task.status === "done" : task.status !== "done")));
  const openCount = tasks.filter(task => task.status !== "done" && task.status !== "skipped").length;
  async function toggle(task: Task) {
    try {
      if (task.status === "done") await reopen.mutateAsync(task.id);
      else await complete.mutateAsync(task.id);
    } catch (error) {
      toast.error((error as Error).message || t("common.failed"));
    }
  }
  return <section className="mc-list-panel" aria-label={t("mission.taskInbox")}><SectionHeading number="04" title={t("mission.taskInbox")} aside={<button type="button" className="mc-icon-button" aria-label={t("tasks.addTask")} onClick={() => setCreating(true)}><Plus size={18} /></button>} /><div className="mc-task-filters" aria-label={t("mission.filterTasks")}>{(["open", "done", "all"] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(`mission.taskFilter.${value}`)}{value === "open" && <span>{openCount}</span>}</button>)}</div><div className="mc-task-list">{visible.length ? visible.map(task => {
    const pending = (complete.isPending && complete.variables === task.id) || (reopen.isPending && reopen.variables === task.id);
    return <div className="mc-task-row" key={task.id} data-done={task.status === "done"}><button type="button" className="mc-task-toggle" role="checkbox" aria-checked={task.status === "done"} aria-label={t(task.status === "done" ? "mission.reopenTask" : "dashboard.completeAria", { title: task.title })} disabled={pending} onClick={() => toggle(task)}><span>{pending ? <Loader2 size={12} className="animate-spin" /> : task.status === "done" ? <Check size={12} /> : null}</span></button><button type="button" className="mc-task-title" onClick={() => setEditing(task)}><strong>{task.title}</strong>{task.course_code && <span style={courseStyle(task.course_code)}><i />{task.course_code}</span>}</button><span className="mc-task-status" data-status={task.status}>{t(`kinds.status.${task.status}`)}</span></div>;
  }) : <div className="mc-empty"><Check size={20} />{t(filter === "done" ? "mission.noCompleted" : "dashboard.inboxZero")}</div>}</div><button type="button" className="mc-add-task" onClick={() => setCreating(true)}><Plus size={14} />{t("tasks.addTask")}<span>{t("mission.oneSmallStep")}</span></button><TaskForm open={creating || editing !== null} onOpenChange={open => { if (!open) { setCreating(false); setEditing(null); } }} task={editing} courses={courses} /></section>;
}
