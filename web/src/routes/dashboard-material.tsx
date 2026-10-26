import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlarmClock, ArrowRight, ArrowUpRight, BookOpen, Check, CircleCheck, Clock3, Flag, ListChecks, Loader2, Plus, Sparkles, Target, TriangleAlert, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { useAppSettings, useCompleteTask, useDashboard, useReopenTask } from "@/lib/queries";
import { courseProgress } from "@/lib/fall-behind";
import { courseAccentVar } from "@/lib/theme";
import { easternCalendarDays, fmtEastern, inEasternTime, relative } from "@/lib/time";
import { TaskForm } from "@/components/forms/task-form";
import { EmptyState } from "@/components/common/empty-state";
import { MaterialSchedule } from "@/components/dashboard/material-schedule";
import type { Course, Task } from "@/data/types";

function courseStyle(code: string): CSSProperties {
  return { "--accent": courseAccentVar(code) } as CSSProperties;
}

function Metric({ icon: Icon, label, value, unit, hint, tone }: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  unit: string;
  hint: string;
  tone: "default" | "warn" | "critical" | "ok";
}) {
  return (
    <article className={`me-metric me-metric-${tone}`}>
      <div className="me-metric-top"><span><Icon size={20} /></span><h2>{label}</h2></div>
      <div className="me-metric-reading"><strong>{value}</strong><span>{unit}</span></div>
      <p>{hint}</p>
    </article>
  );
}

export function MaterialDashboard() {
  const { t, i18n } = useTranslation();
  const dashboard = useDashboard();
  const settings = useAppSettings();
  if (dashboard.isPending) return <div className="me-loading" role="status"><Loader2 className="animate-spin" size={22} /><span>{t("common.loading")}</span></div>;
  if (dashboard.error || !dashboard.data) return <div className="me-loading" role="alert">{t("dashboard.loadFailed")}</div>;

  const data = dashboard.data;
  const now = new Date(data.now);
  const locale = i18n.language === "zh" ? "zh-CN" : "en-GB";
  const firstName = settings.data?.display_name?.trim().split(/\s+/)[0] || t("dashboard.greetingFallbackName");
  const hour = inEasternTime(now).getHours();
  const period = hour < 5 ? "Night" : hour < 12 ? "Morning" : hour < 18 ? "Afternoon" : "Evening";
  const behind = data.fall_behind.filter(item => item.severity !== "ok" && item.topics.length > 0);
  const behindTopics = behind.reduce((sum, item) => sum + item.topics.length, 0);
  const next = data.fall_behind.filter(item => item.next_lecture_at && new Date(item.next_lecture_at) > now)
    .sort((a, b) => Date.parse(a.next_lecture_at!) - Date.parse(b.next_lecture_at!))[0];
  const deadlines = data.deliverables.filter(item => item.status === "open" || item.status === "in_progress")
    .sort((a, b) => Date.parse(a.due_at) - Date.parse(b.due_at));
  const nextDue = deadlines[0];
  const days = nextDue ? easternCalendarDays(new Date(nextDue.due_at), now) : null;
  const tasks = data.tasks.filter(task => task.status !== "skipped");
  const done = tasks.filter(task => task.status === "done").length;
  const progress = data.courses.length
    ? Math.round(data.courses.reduce((sum, course) => sum + courseProgress(course.code, data.study_topics), 0) / data.courses.length)
    : 0;
  const ects = data.courses.reduce((sum, course) => sum + (course.ects ?? 0), 0);

  return (
    <div className="me-page">
      <section className="me-greeting">
        <div>
          <div className="me-kicker">{now.toLocaleDateString(locale, { timeZone: "America/New_York", weekday: "long", day: "numeric", month: "long" })}</div>
          <h1>{t(`dashboard.greeting${period}`, { name: firstName })}<Sparkles size={28} aria-hidden="true" /></h1>
          <p>{t("material.greetingSubline")}</p>
        </div>
        {next && <Link to={`/app/courses/${next.course_code}`} className="me-next-up" style={courseStyle(next.course_code)}>
          <span className="me-next-icon"><Clock3 size={20} /></span>
          <span><small>{t("dashboard.nextUp")}</small><strong>{next.course_code}<span>{relative(next.next_lecture_at!, now).label}</span></strong></span>
          <ArrowUpRight size={18} />
        </Link>}
      </section>

      {behind.length > 0 && <section className="me-catchup" aria-labelledby="material-catchup-title">
        <span className="me-catchup-icon"><TriangleAlert size={22} /></span>
        <div className="me-catchup-copy">
          <h2 id="material-catchup-title">{t("material.catchupTitle")}</h2>
          <ul>{behind.slice(0, 2).map(item => <li key={item.course_code}>
            <Link to={`/app/courses/${item.course_code}?tab=study`}>{t("dashboard.falling_behind", { count: item.topics.length, code: item.course_code })}{item.next_lecture_at && ` · ${t("courseDetail.nextLecture")} ${relative(item.next_lecture_at, now).label}`}</Link>
          </li>)}</ul>
          <div className="me-signals">{data.courses.map(course => {
            const state = data.fall_behind.find(item => item.course_code === course.code)?.severity ?? "ok";
            return <span key={course.code}><i data-severity={state} />{course.code}<span className="sr-only">{t(`material.readiness.${state}`)}</span></span>;
          })}</div>
        </div>
        <Link to={`/app/courses/${behind[0].course_code}?tab=study`} className="me-button me-button-catchup">{t("material.reviewTopics")}<ArrowRight size={16} /></Link>
      </section>}

      <section className="me-metrics" aria-label={t("material.atAGlance")}>
        <Metric icon={AlarmClock} label={t("dashboard.tile.nextDeadline")} value={days === null ? "—" : Math.abs(days)} unit={days === null ? "" : t(days < 0 ? "material.daysOverdue" : "material.daysLeft")}
          hint={nextDue ? `${nextDue.course_code} · ${nextDue.name}` : t("dashboard.tile.noOpenDeliverables")}
          tone={days === null ? "default" : days <= 1 ? "critical" : days <= 3 ? "warn" : "default"} />
        <Metric icon={BookOpen} label={t("material.needsLove")} value={behindTopics} unit={t("common.topics")}
          hint={behind.length ? t("material.acrossCourses", { count: behind.length }) : t("material.allCaughtUp")}
          tone={behind.some(item => item.severity === "critical") ? "critical" : behind.length ? "warn" : "ok"} />
        <Metric icon={Target} label={t("material.learningProgress")} value={`${progress}%`} unit=""
          hint={t("material.acrossCourses", { count: data.courses.length })} tone="default" />
        <Metric icon={CircleCheck} label={t("material.smallWins")} value={`${done}/${tasks.length}`} unit={t("material.tasksDone")}
          hint={t("material.winsHint")} tone="ok" />
      </section>

      <MaterialSchedule slots={data.slots} now={now} semesterStart={settings.data?.semester_start} />

      <section className="me-section" aria-labelledby="material-courses-title">
        <div className="me-section-heading"><div><span className="me-kicker">{t("material.curiosity")}</span><h2 id="material-courses-title">{t("material.learningTitle")}<small>{t("material.courseCount", { count: data.courses.length })}</small></h2></div><span className="me-quiet">{ects} ECTS</span></div>
        {data.courses.length === 0 ? <div className="me-empty"><EmptyState title={t("material.noCourses")} description={t("material.noCoursesHint")} /><Link className="me-button" to="/app/courses"><Plus size={16} />{t("forms.course.titleAdd")}</Link></div> :
          <div className="me-courses">{data.courses.map(course => {
            const ownTopics = data.study_topics.filter(topic => topic.course_code === course.code);
            const studied = ownTopics.filter(topic => topic.status === "studied" || topic.status === "mastered").length;
            const percent = courseProgress(course.code, data.study_topics);
            const upcoming = data.fall_behind.find(item => item.course_code === course.code)?.next_lecture_at;
            return <Link key={course.code} to={`/app/courses/${course.code}`} className="me-course-card" style={courseStyle(course.code)}>
              <div className="me-course-top"><span><BookOpen size={21} /></span><ArrowUpRight size={19} /></div>
              <span className="me-course-code">{course.code}</span><h3>{course.full_name}</h3>
              <p className="me-course-meta">{[course.module_code, course.ects != null ? `${course.ects} ECTS` : null, course.language].filter(Boolean).join(" · ")}</p>
              <div className="me-course-progress-info"><span>{t("material.topicsStudied", { done: studied, total: ownTopics.length })}</span><strong>{percent}%</strong></div>
              <div className="me-course-track" role="progressbar" aria-label={`${course.code} ${t("material.learningProgress")}`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
              {upcoming && <span className="me-course-next"><Clock3 size={13} />{t("courseDetail.nextLecture")} {relative(upcoming, now).label}</span>}
            </Link>;
          })}</div>}
      </section>

      <div className="me-bottom-grid">
        <section className="me-list-surface" aria-labelledby="material-deadlines-title">
          <div className="me-list-heading"><span><Flag size={18} /></span><h2 id="material-deadlines-title">{t("material.horizon")}</h2><Link to="/app/deliverables" className="me-count" aria-label={t("nav.deadlines")}>{deadlines.length}<ArrowUpRight size={13} /></Link></div>
          <p className="me-list-description">{t("material.deadlineHint")}</p>
          {deadlines.length === 0 ? <p className="me-list-empty">{t("dashboard.nothingDue")}</p> : deadlines.slice(0, 5).map(item => <Link key={item.id} to={`/app/courses/${item.course_code}?tab=deliverables`} className="me-deadline-row">
            <span className="me-deadline-date"><strong>{fmtEastern(item.due_at, "dd")}</strong><small>{new Date(item.due_at).toLocaleDateString(locale, { timeZone: "America/New_York", month: "short" })}</small></span>
            <span className="me-deadline-copy"><strong>{item.name}</strong><span><b style={courseStyle(item.course_code)}>{item.course_code}</b>{t(`forms.deliverable.kind${item.kind.charAt(0).toUpperCase()}${item.kind.slice(1)}`)}</span></span>
            <span className="me-countdown" data-severity={relative(item.due_at, now).severity}>{relative(item.due_at, now).label}</span>
          </Link>)}
          <div className="me-list-footer"><Clock3 size={14} />{t("material.easternTime")}</div>
        </section>
        <MaterialTasks tasks={tasks} courses={data.courses} />
      </div>
      <footer className="me-page-footer"><span>{t("material.footer")}</span><span>ClassPort / Material Expressive</span></footer>
    </div>
  );
}

function MaterialTasks({ tasks, courses }: { tasks: Task[]; courses: Course[] }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<"todo" | "all" | "done">("todo");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const complete = useCompleteTask();
  const reopen = useReopenTask();
  const done = tasks.filter(task => task.status === "done");
  const visible = tasks.filter(task => filter === "all" || (filter === "done" ? task.status === "done" : task.status !== "done"));

  async function toggle(task: Task) {
    try {
      if (task.status === "done") await reopen.mutateAsync(task.id);
      else await complete.mutateAsync(task.id);
    } catch (error) {
      toast.error((error as Error).message || t("common.failed"));
    }
  }

  return (
    <section className="me-list-surface" aria-labelledby="material-tasks-title">
      <div className="me-list-heading"><span className="me-list-icon-green"><ListChecks size={18} /></span><h2 id="material-tasks-title">{t("material.oneThing")}</h2><button className="me-icon-button me-add-task" type="button" aria-label={t("tasks.addTask")} onClick={() => setCreating(true)}><Plus size={20} /></button></div>
      <div className="me-task-tabs" aria-label={t("material.filterTasks")}>{(["todo", "all", "done"] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(`material.taskFilter.${value}`)}<span>{value === "all" ? tasks.length : value === "done" ? done.length : tasks.length - done.length}</span></button>)}</div>
      <div className="me-task-rows">
        {visible.length === 0 ? <div className="me-list-empty"><CircleCheck size={26} /><p>{t(filter === "done" ? "material.noCompleted" : "dashboard.inboxZero")}</p></div> : visible.map(task => {
          const pending = (complete.isPending && complete.variables === task.id) || (reopen.isPending && reopen.variables === task.id);
          return <div key={task.id} className={`me-task-row${task.status === "done" ? " me-task-done" : ""}`}>
            <button type="button" className="me-task-toggle" role="checkbox" aria-checked={task.status === "done"} aria-label={task.status === "done" ? t("material.reopenTask", { title: task.title }) : t("dashboard.completeAria", { title: task.title })} disabled={pending} onClick={() => toggle(task)}><span>{pending ? <Loader2 size={12} className="animate-spin" /> : task.status === "done" ? <Check size={13} /> : null}</span></button>
            <button type="button" className="me-task-copy" onClick={() => setEditing(task)}><strong>{task.title}</strong>{task.course_code && <span style={courseStyle(task.course_code)}><i />{task.course_code}</span>}</button>
            <span className="me-task-status" data-status={task.status}>{t(`kinds.status.${task.status}`, task.status.replaceAll("_", " "))}</span>
          </div>;
        })}
      </div>
      <button type="button" className="me-add-task-inline" onClick={() => setCreating(true)}><Plus size={16} />{t("material.newTask")}</button>
      <TaskForm open={creating || editing !== null} onOpenChange={open => { if (!open) { setCreating(false); setEditing(null); } }} task={editing} courses={courses} />
    </section>
  );
}
