import type { ThemeId } from "@/data/types";

export type ThemeMeta = {
  id: ThemeId;
  label: string;
  tagline: string;
};

export const THEMES: ThemeMeta[] = [
  {
    id: "terminal",
    label: "Terminal",
    tagline: "Mono everywhere, teal-on-black, hacker cockpit.",
  },
  {
    id: "zine",
    label: "Zine",
    tagline: "Pastel cream + pink/cyan/yellow stickers, hand-drawn feel.",
  },
  {
    id: "library",
    label: "Library",
    tagline: "Cream paper, sepia type, card-catalog aesthetic.",
  },
  {
    id: "swiss",
    label: "Swiss",
    tagline: "12-column grid, red accent, Helvetica-era Swiss typography.",
  },
  {
    id: "editorial",
    label: "Generic",
    tagline: "Editorial style — serif, airy, muted.",
  },
];

export const DEFAULT_THEME: ThemeId = "zine";

export function normalizeTheme(raw: string | null | undefined): ThemeId {
  if (!raw) return DEFAULT_THEME;
  return (THEMES.find((t) => t.id === raw)?.id ?? DEFAULT_THEME) as ThemeId;
}

const STORAGE_KEY = "classport:theme";

export function readStoredTheme(): ThemeId | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return normalizeTheme(raw);
  } catch {
    return null;
  }
}

export function applyTheme(raw: string | null | undefined): void {
  if (typeof document === "undefined") return;
  // Prefer the explicit value; fall back to whatever the browser last saw so
  // reloads don't reset to Editorial while the API round-trip catches up.
  const id = normalizeTheme(raw ?? readStoredTheme() ?? undefined);
  const apply = () => {
    document.documentElement.dataset.theme = id;
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* ignore quota / privacy-mode failures */
    }
  };
  // View Transitions API (Chrome/Edge/Safari 18+) cross-fades between the
  // before/after states, hiding the layout reflow when sidebars + grids
  // re-shape themselves. Firefox falls through to the instant apply.
  const startTransition = (
    document as Document & { startViewTransition?: (cb: () => void) => unknown }
  ).startViewTransition;
  if (typeof startTransition === "function") {
    startTransition.call(document, apply);
  } else {
    apply();
  }
}
