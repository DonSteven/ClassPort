import { useEffect, useState, type CSSProperties } from "react";
import { cn } from "@/lib/cn";

type WordmarkProps = {
  className?: string;
  title?: string;
  style?: CSSProperties;
  /**
   * Override the auto-picked variant.
   *
   * - `"ink"` / `"cream"`: force a specific variant.
   * - undefined (default): follow the app's `data-theme` attribute — cream for
   *   dark-bg themes (editorial, terminal, mission-control), ink otherwise.
   * - `"auto-os"`: follow the OS's `prefers-color-scheme` instead of the app
   *   theme. Use on surfaces that live outside the app shell (landing page).
   */
  variant?: "ink" | "cream" | "auto-os";
};

const DARK_BG_THEMES = new Set(["editorial", "terminal", "mission-control"]);

function readTheme(): string {
  if (typeof document === "undefined") return "zine";
  return document.documentElement.dataset.theme ?? "zine";
}

export function Wordmark({ className, title = "ClassPort", style, variant }: WordmarkProps) {
  const [theme, setTheme] = useState(readTheme);

  useEffect(() => {
    if (variant) return;
    const observer = new MutationObserver(() => setTheme(readTheme()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, [variant]);

  // `auto-os` uses a <picture> + prefers-color-scheme so the switch happens in
  // native CSS, not React — matches the landing page's OS-theme design.
  if (variant === "auto-os") {
    return (
      <picture>
        <source media="(prefers-color-scheme: dark)" srcSet="/brand/wordmark/on-dark.svg" />
        <img
          src="/brand/wordmark/on-light.svg"
          alt={title}
          className={cn("h-auto w-auto select-none", className)}
          style={style}
          draggable={false}
        />
      </picture>
    );
  }

  const pick = variant ?? (DARK_BG_THEMES.has(theme) ? "cream" : "ink");
  const src = pick === "cream" ? "/brand/wordmark/on-dark.svg" : "/brand/wordmark/on-light.svg";

  return (
    <img
      src={src}
      alt={title}
      className={cn("h-auto w-auto select-none", className)}
      style={style}
      draggable={false}
    />
  );
}
