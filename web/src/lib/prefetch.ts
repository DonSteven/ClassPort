// Hover/focus prefetch for the lazy-loaded /app/* routes. ES module imports
// are cached, so calling the same dynamic import here and in App.tsx's
// `lazy()` shares the module — by the time the user clicks, the chunk is
// already in cache and Suspense never has to fall back.

type RouteMatch = {
  match: (path: string) => boolean;
  load: () => Promise<unknown>;
};

const ROUTES: RouteMatch[] = [
  { match: (p) => p === "/app",                  load: () => import("@/routes/dashboard") },
  { match: (p) => p === "/app/courses",          load: () => import("@/routes/courses") },
  { match: (p) => p.startsWith("/app/courses/"), load: () => import("@/routes/course-detail") },
  { match: (p) => p === "/app/tasks",            load: () => import("@/routes/tasks") },
  { match: (p) => p === "/app/deliverables",     load: () => import("@/routes/deliverables") },
  { match: (p) => p === "/app/exams",            load: () => import("@/routes/exams") },
  { match: (p) => p === "/app/files",            load: () => import("@/routes/files") },
  { match: (p) => p === "/app/activity",         load: () => import("@/routes/activity") },
  { match: (p) => p === "/app/settings",         load: () => import("@/routes/settings") },
];

export function prefetchRoute(path: string): void {
  const hit = ROUTES.find((r) => r.match(path));
  if (hit) void hit.load();
}
