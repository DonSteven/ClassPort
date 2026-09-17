/**
 * Keep <title> and <html lang> in sync with the current route and i18n locale.
 *
 * - useDocumentTitle(): set "Page — SiteName" from the current pathname + i18n.
 * - useHtmlLang():      mirror i18n.language onto <html lang="…">.
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

const SITE_NAME = import.meta.env.VITE_SITE_NAME ?? "ClassPort";

const TITLES: Record<string, { en: string; zh: string }> = {
  "/app": { en: "Today", zh: "今天" },
  "/app/courses": { en: "Courses", zh: "课程" },
  "/app/tasks": { en: "Tasks", zh: "任务" },
  "/app/deliverables": { en: "Deliverables", zh: "作业与项目" },
  "/app/exams": { en: "Exams", zh: "考试" },
  "/app/files": { en: "Files", zh: "文件" },
  "/app/activity": { en: "Activity", zh: "活动记录" },
  "/app/settings": { en: "Settings", zh: "设置" },
  "/login": { en: "Sign in", zh: "登录" },
};

function resolveTitle(pathname: string, lang: "en" | "zh"): string | null {
  const hit = TITLES[pathname];
  if (hit) return hit[lang];
  const courseMatch = pathname.match(/^\/app\/courses\/([^/]+)$/);
  if (courseMatch) return courseMatch[1].toUpperCase();
  return null;
}

// Routes that are part of the public surface Google should index. Everything
// else (the authed dashboard, the login page) gets `noindex, nofollow` so it
// doesn't end up in search results — Google would otherwise pick the auth
// redirect target as the canonical URL when crawling /, which is what
// produced "Sign in — ClassPort" in early indexing.
const PUBLIC_PATHS = new Set<string>(["/", "/zh"]);

export function useDocumentTitle(): void {
  const location = useLocation();
  const { i18n } = useTranslation();

  useEffect(() => {
    const lang = i18n.language === "zh" ? "zh" : "en";
    const page = resolveTitle(location.pathname, lang);
    document.title = page ? `${page} — ${SITE_NAME}` : SITE_NAME;

    const robots = document.querySelector('meta[name="robots"]') as HTMLMetaElement | null;
    if (robots) {
      robots.content = PUBLIC_PATHS.has(location.pathname)
        ? "index, follow, max-image-preview:large"
        : "noindex, nofollow";
    }
  }, [location.pathname, i18n.language]);
}

export function useHtmlLang(): void {
  const { i18n } = useTranslation();
  useEffect(() => {
    document.documentElement.lang = i18n.language === "zh" ? "zh" : "en";
  }, [i18n.language]);
}
