import { useState, type CSSProperties } from "react";
import { Activity, ArrowUpRight, BookOpen, ChevronDown, Crosshair, Flag, FolderOpen, GraduationCap, Layers3, ListTodo, Menu, Plus, Settings, ShieldCheck } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useActiveCourses, useAppSettings, useDashboard } from "@/lib/queries";
import { courseAccentVar } from "@/lib/theme";
import { prefetchRoute } from "@/lib/prefetch";
import { semesterWeek } from "@/lib/time";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { TaskForm } from "@/components/forms/task-form";

const pages = [
  { path: "/app", key: "dashboard", icon: Crosshair },
  { path: "/app/courses", key: "courses", icon: Layers3 },
  { path: "/app/tasks", key: "tasks", icon: ListTodo },
  { path: "/app/deliverables", key: "deadlines", icon: Flag },
  { path: "/app/files", key: "files", icon: FolderOpen },
  { path: "/app/exams", key: "exams", icon: GraduationCap },
  { path: "/app/activity", key: "activity", icon: Activity },
  { path: "/app/settings", key: "settings", icon: Settings },
];

export function MissionSidebar({ drawer = false, onNavigate }: { drawer?: boolean; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const courses = useActiveCourses();
  const settings = useAppSettings();
  const dashboard = useDashboard();
  const profile = settings.data;
  const name = profile?.display_name?.trim() || t("dashboard.greetingFallbackName");
  const monogram = profile?.monogram?.slice(0, 2) || name.charAt(0).toUpperCase();
  const now = dashboard.data ? new Date(dashboard.data.now) : new Date();
  const week = semesterWeek(now, profile?.semester_start);
  const openTasks = dashboard.data?.tasks.filter(task => task.status !== "done" && task.status !== "skipped").length ?? 0;

  return <aside className={`mc-sidebar${drawer ? " mc-sidebar-drawer" : ""}`} aria-label={t("mission.workspace")}>
    <Link className="mc-brand" to="/app" onClick={onNavigate}><span className="mc-brand-icon"><img src="/brand/mark/on-dark.svg" alt="" width={34} height={34} /></span><span>CLASS<span>PORT</span><small>{t("mission.studyOperations")}</small></span></Link>
    <div className="mc-sidebar-label">{t("mission.workspace")}<span>01</span></div>
    <nav aria-label={t("nav.pages")}>{pages.map(({ path, key, icon: Icon }, index) => <NavLink to={path} end={path === "/app" || path === "/app/tasks"} key={path} className="mc-nav-item" onClick={onNavigate} onMouseEnter={() => prefetchRoute(path)} onFocus={() => prefetchRoute(path)}><Icon size={17} /><span>{t(`nav.${key}`)}</span>{key === "tasks" && openTasks > 0 ? <span className="mc-nav-count">{openTasks}</span> : <small>{String(index + 1).padStart(2, "0")}</small>}</NavLink>)}</nav>
    {!!courses.data?.length && <><div className="mc-sidebar-label mc-sidebar-courses-label">{t("mission.activeCourses")}<span>{String(courses.data.length).padStart(2, "0")}</span></div><nav className="mc-sidebar-courses" aria-label={t("nav.courses")}>{courses.data.map(course => <NavLink key={course.code} to={`/app/courses/${course.code}`} style={{ "--accent": courseAccentVar(course.code) } as CSSProperties} onClick={onNavigate} onMouseEnter={() => prefetchRoute(`/app/courses/${course.code}`)}><i /><b>{course.code}</b><span>{course.short_name || course.full_name}</span></NavLink>)}</nav></>}
    <div className="mc-sidebar-bottom"><Link to="/app/settings" className="mc-semester-card" onClick={onNavigate}><div><BookOpen size={16} /><span>{profile?.semester_label || t("settings.sections.semester")}</span></div><strong>{t("mission.focusLine")}</strong><span>{week === null ? t("mission.setSemester") : week > 0 ? t("dashboard.topStrip.week", { n: week }) : t("common.preStart")}<ArrowUpRight size={14} /></span></Link><div className="mc-system-status"><ShieldCheck size={13} /><span>{t("mission.yourPace")}</span></div><Link to="/app/settings" className="mc-profile" onClick={onNavigate}><span className="mc-avatar">{monogram}</span><span><strong>{name}</strong><small>{profile?.institution || t("mission.personalWorkspace")}</small></span><ChevronDown size={14} /></Link></div>
  </aside>;
}

export function MissionTopbar() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const settings = useAppSettings();
  const courses = useActiveCourses();
  const [menuOpen, setMenuOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const page = pages.find(item => item.path !== "/app" && (pathname === item.path || pathname.startsWith(`${item.path}/`))) ?? pages[0];
  const name = settings.data?.display_name?.trim() || t("dashboard.greetingFallbackName");
  return <>
    <header className="mc-topbar"><button type="button" className="mc-icon-button mc-mobile-menu" aria-label={t("mission.openNavigation")} onClick={() => setMenuOpen(true)}><Menu size={20} /></button><div className="mc-breadcrumb"><span>{t("mission.workspace")}</span><span>/</span><b>{t(`nav.${page.key}`)}</b></div><div className="mc-topbar-right">{settings.data?.semester_label && <span className="mc-topbar-semester">{settings.data.semester_label}</span>}<Link className="mc-topbar-profile mc-avatar" to="/app/settings" aria-label={t("nav.settings")}>{settings.data?.monogram?.slice(0, 2) || name.charAt(0).toUpperCase()}</Link><button type="button" className="mc-primary-button" onClick={() => setCreating(true)}><Plus size={15} /><span>{t("tasks.addTask")}</span></button></div></header>
    <Sheet open={menuOpen} onOpenChange={setMenuOpen}><SheetContent title={t("mission.workspace")} description={t("mission.studyOperations")} className="mc-navigation-sheet"><MissionSidebar drawer onNavigate={() => setMenuOpen(false)} /></SheetContent></Sheet>
    <TaskForm open={creating} onOpenChange={setCreating} courses={courses.data ?? []} />
  </>;
}
