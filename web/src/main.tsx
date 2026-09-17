import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./styles/terminal.css";
import "./styles/zine.css";
import "./styles/library.css";
import "./styles/swiss.css";
import App from "./App";
import { applyTheme } from "./lib/themes";
import "./lib/i18n";

// Apply the last-seen theme before React mounts so the first paint matches
// the user's choice. Without this you'd see Editorial for ~200ms while the
// settings query resolves, then a jarring swap.
applyTheme(null);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
