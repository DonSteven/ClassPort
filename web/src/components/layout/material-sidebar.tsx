import { Activity, ArrowUpRight, BookOpen, FolderOpen, GraduationCap, LayoutDashboard, ListChecks, Settings } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useActiveCourses, useAppSettings, useDashboard } from "@/lib/queries";
import { courseAccentVar } from "@/lib/theme";
import { fmtEastern, semesterWeek } from "@/lib/time";
import { prefetchRoute } from "@/lib/prefetch";
import type { CSSProperties } from "react";

const pages = [
  { path: "/app", key: "dashboard", icon: LayoutDashboard },
  { path: "/app/courses", key: "courses", icon: BookOpen },
  { path: "/app/tasks", key: "tasks", icon: ListChecks },
  { path: "/app/deliverables", key: "deadlines", icon: ListChecks },
  { path: "/app/files", key: "files", icon: FolderOpen },
  { path: "/app/exams", key: "exams", icon: GraduationCap },
  { path: "/app/activity", key: "activity", icon: Activity },
  { path: "/app/settings", key: "settings", icon: Settings },
];

export function MaterialSidebar() {
  const { t } = useTranslation();
  const courses = useActiveCourses();
  const settings = useAppSettings();
  const dashboard = useDashboard();
  const profile = settings.data;
  const now = dashboard.data ? new Date(dashboard.data.now) : new Date();
  const week = semesterWeek(now, profile?.semester_start);
  const name = profile?.display_name?.trim() || t("dashboard.greetingFallbackName");
  const monogram = profile?.monogram?.slice(0, 2) || name.charAt(0).toUpperCase();
  const openTasks = dashboard.data?.tasks.filter(task => task.status !== "done" && task.status !== "skipped").length ?? 0;
  const start = profile?.semester_start;
  const end = profile?.semester_end;
  const total = start && end ? Date.parse(end) - Date.parse(start) : 0;
  const progress = start && total > 0
    ? Math.min(100, Math.max(0, (Date.parse(fmtEastern(now, "yyyy-MM-dd")) - Date.parse(start)) / total * 100))
    : null;

  return (
    <aside className="me-side" aria-label={t("material.workspace")}>
      <Link to="/app" className="me-brand"><span><img src="/brand/mark/on-dark.svg" alt="" width={32} height={32} /></span>ClassPort<span className="me-brand-slash">/</span></Link>
      <Link to="/app/settings" className="me-semester">
        <BookOpen size={20} />
        <span><strong>{profile?.semester_label || t("material.workspace")}</strong><small>{profile?.institution || t("material.learningSpace")}</small></span>
        <ArrowUpRight size={15} />
      </Link>
      <p className="me-nav-caption">{t("nav.pages")}</p>
      <nav className="me-nav">
        {pages.map(({ path, key, icon: Icon }) => (
          <NavLink key={path} to={path} end={path === "/app" || path === "/app/tasks"}
            onMouseEnter={() => prefetchRoute(path)} onFocus={() => prefetchRoute(path)}
            className={({ isActive }) => `me-nav-item${isActive ? " me-nav-active" : ""}`}>
            <Icon size={18} /><span>{t(`nav.${key}`)}</span>
            {key === "tasks" && openTasks > 0 && <small>{openTasks}</small>}
          </NavLink>
        ))}
      </nav>
      {(courses.data?.length ?? 0) > 0 && <>
        <p className="me-nav-caption">{t("nav.courses")}</p>
        <nav className="me-course-nav">
          {courses.data?.map(course => (
            <NavLink key={course.code} to={`/app/courses/${course.code}`}
              className={({ isActive }) => `me-course-nav-item${isActive ? " me-nav-active" : ""}`}
              style={{ "--accent": courseAccentVar(course.code) } as CSSProperties}
              onMouseEnter={() => prefetchRoute(`/app/courses/${course.code}`)}>
              <span>{course.code}</span><strong>{course.short_name || course.full_name}</strong>
            </NavLink>
          ))}
        </nav>
      </>}
      <div className="me-side-message">
        <p>{t("material.focusLine")}<br /><strong>{t("material.progressLine")}</strong></p>
        {week !== null && <small>{week > 0 ? t("dashboard.topStrip.week", { n: week }) : t("common.preStart")}</small>}
        {progress !== null && <div className="me-semester-track" role="progressbar" aria-label={t("material.semesterProgress")} aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${progress}%` }} /></div>}
      </div>
      <Link to="/app/settings" className="me-profile"><span className="me-avatar">{monogram}</span><span><strong>{name}</strong><small>{t("nav.settings")}</small></span><Settings size={16} /></Link>
    </aside>
  );
}

export function MaterialTopbar() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const settings = useAppSettings();
  const page = pages.find(item => item.path !== "/app" && (pathname === item.path || pathname.startsWith(`${item.path}/`))) ?? pages[0];
  const name = settings.data?.display_name?.trim() || "ClassPort";
  return (
    <header className="me-topbar">
      <span>{t("material.workspace")}<span>/</span><strong>{t(`nav.${page.key}`)}</strong></span>
      <div>{settings.data?.semester_label && <small>{settings.data.semester_label}</small>}<Link to="/app/settings" className="me-avatar" aria-label={t("nav.settings")}>{settings.data?.monogram?.slice(0, 2) || name.charAt(0).toUpperCase()}</Link></div>
    </header>
  );
}
