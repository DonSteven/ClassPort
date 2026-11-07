import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { QueryProvider } from "@/components/layout/query-provider";
import { Toaster } from "@/components/ui/toaster";
import Landing from "@/routes/landing";
import Login from "@/routes/login";
import Signup from "@/routes/signup";
import ForgotPassword from "@/routes/forgot-password";
import ResetPassword from "@/routes/reset-password";
import VerifyEmail from "@/routes/verify-email";
import { DEMO_MODE } from "@/lib/demo-mode";

const DemoBar = lazy(() => import("@/demo/demo-bar"));

function AppLayout() {
  return (
    <div className={DEMO_MODE ? "demo-app" : undefined}>
      {DEMO_MODE && <DemoBar />}
      <AppShell />
    </div>
  );
}

// Eager: Landing (the public marketing page Google sees) + Login (small +
// always on the critical path). Everything under /app is lazy-loaded so
// the public landing bundle doesn't ship the dashboard, sidebars, charts,
// and per-route UI just to render a static marketing page.
const AppShell = lazy(() =>
  import("@/components/layout/app-shell").then((m) => ({ default: m.AppShell })),
);
const Dashboard = lazy(() => import("@/routes/dashboard"));
const Courses = lazy(() => import("@/routes/courses"));
const CourseDetail = lazy(() => import("@/routes/course-detail"));
const Tasks = lazy(() => import("@/routes/tasks"));
const Deliverables = lazy(() => import("@/routes/deliverables"));
const Exams = lazy(() => import("@/routes/exams"));
const Files = lazy(() => import("@/routes/files"));
const Activity = lazy(() => import("@/routes/activity"));
const Settings = lazy(() => import("@/routes/settings"));

// Enable the marketing landing with VITE_SHOW_LANDING=true. Self-hosters
// leave VITE_SHOW_LANDING unset/false so `/` jumps straight to the app, same
// pattern Cal.com / n8n / Plausible / Ghost / Sentry use.
const SHOW_LANDING = import.meta.env.VITE_SHOW_LANDING === "true";

const router = createBrowserRouter([
  {
    path: "/",
    element: SHOW_LANDING ? <Landing /> : <Navigate to="/app" replace />,
  },
  { path: "/login", element: DEMO_MODE ? <Navigate to="/app" replace /> : <Login /> },
  { path: "/signup", element: DEMO_MODE ? <Navigate to="/app" replace /> : <Signup /> },
  { path: "/forgot-password", element: DEMO_MODE ? <Navigate to="/app" replace /> : <ForgotPassword /> },
  { path: "/reset-password", element: DEMO_MODE ? <Navigate to="/app" replace /> : <ResetPassword /> },
  { path: "/verify-email", element: DEMO_MODE ? <Navigate to="/app" replace /> : <VerifyEmail /> },
  {
    path: "/app",
    element: <AppLayout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "courses", element: <Courses /> },
      { path: "courses/:code", element: <CourseDetail /> },
      { path: "tasks", element: <Tasks /> },
      { path: "deliverables", element: <Deliverables /> },
      { path: "exams", element: <Exams /> },
      { path: "files", element: <Files /> },
      { path: "activity", element: <Activity /> },
      { path: "settings", element: <Settings /> },
    ],
  },
]);

export default function App() {
  return (
    <QueryProvider>
      {/* Outer Suspense only catches the AppShell chunk on first /app visit.
          Sub-route chunks (Dashboard, Courses, …) are caught by an inner
          Suspense inside AppShell, so the sidebar + header stay mounted
          across navigation instead of the whole tree blanking. */}
      <Suspense fallback={null}>
        <RouterProvider router={router} />
      </Suspense>
      <Toaster />
    </QueryProvider>
  );
}
