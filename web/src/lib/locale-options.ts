// Locale list used by Settings → Semester picker. The field is "Date format",
// not "Language" — the label is the canonical pattern so users pick by shape.
export const LOCALE_OPTIONS: { value: string; label: string }[] = [
  { value: "en-US", label: "MM/DD/YYYY" },
  { value: "en-GB", label: "DD/MM/YYYY" },
  { value: "zh-CN", label: "YYYY/MM/DD" },
];

// Full IANA timezone list via Intl.supportedValuesOf — supported in Chrome 99+,
// Safari 15.4+, Firefox 93+. Falls back to a short curated list if unavailable.
const FALLBACK_TIMEZONES = [
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "Europe/Madrid",
  "Europe/Rome",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Australia/Sydney",
];

function loadTimezones(): string[] {
  try {
    const intl = Intl as typeof Intl & {
      supportedValuesOf?: (key: string) => string[];
    };
    if (typeof intl.supportedValuesOf === "function") {
      return intl.supportedValuesOf("timeZone");
    }
  } catch {
    // fall through
  }
  return FALLBACK_TIMEZONES;
}

export const TIMEZONE_OPTIONS: string[] = loadTimezones();
