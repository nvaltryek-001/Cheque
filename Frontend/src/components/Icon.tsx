import { initials } from "../identity";

// Small stroke icon set (no icon dependency). 24x24 grid, 1.75px round stroke.
const PATHS: Record<string, string> = {
  dashboard: "M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-3H4zM14 7h6V4h-6z",
  cheque: "M3 7h18v10H3zM6 11h7M6 14h4M16 14h2",
  signature: "M3 17c3 0 4-9 6-9s1 7 3 7 2-4 4-4 2 3 5 3M3 21h18",
  enroll: "M15 19a6 6 0 0 0-12 0M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M16 11h6",
  batch: "M4 7h12v12H4zM8 3h12v12",
  dataset: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  model: "M12 3v4M12 17v4M3 12h4M17 12h4M7 7h10v10H7zM10 10h4v4h-4z",
  analytics: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 3",
  report: "M6 3h9l5 5v13H6zM14 3v6h6M9 13h7M9 17h5",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
  plus: "M12 5v14M5 12h14",
  check: "M5 12l5 5 9-10",
  x: "M6 6l12 12M18 6L6 18",
  alert: "M12 9v4M12 17h.01M10.3 4l-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7L20 8M20 3v5h-5",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  play: "M7 4l13 8-13 8z",
  crop: "M6 2v16h16M2 6h16v16",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  menu: "M4 6h16M4 12h16M4 18h16",
  chevron: "M9 6l6 6-6 6",
  user: "M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
  users: "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 20v-1a4 4 0 0 0-3-3.9M15 4.2a3.5 3.5 0 0 1 0 6.6",
  mail: "M4 6h16v12H4zM4 7.5l8 6 8-6",
  key: "M11 12.5a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0zM11 12.5h10M17 12.5v3M20.5 12.5v2",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3M12 15v2",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  shieldcheck: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5",
  arrowleft: "M19 12H5M11 6l-6 6 6 6",
  arrowright: "M5 12h14M13 6l6 6-6 6",
  scan: "M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M7 12h10",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS.alert} />
    </svg>
  );
}

/** The product mark: a viewfinder around a tick, i.e. "looked at and checked". Colours come from the stylesheet. */
export function BrandMark({ large }: { large?: boolean }) {
  return (
    <span className={`brand-mark${large ? " brand-mark-lg" : ""}`} aria-hidden="true">
      <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path className="bm-frame" d="M9 13V11.5A2.5 2.5 0 0 1 11.5 9H13M19 9h1.5a2.5 2.5 0 0 1 2.5 2.5V13M23 19v1.5a2.5 2.5 0 0 1-2.5 2.5H19M13 23h-1.5A2.5 2.5 0 0 1 9 20.5V19" />
        <path className="bm-tick" d="M12.8 16.4l2.5 2.5 4.6-5.3" />
      </svg>
    </span>
  );
}

export function Brand({ tone = "light" }: { tone?: "light" | "dark" }) {
  return (
    <div className={`brand brand-${tone}`}>
      <BrandMark />
      <span className="brand-name">ChequeVerify<b>AI</b></span>
    </div>
  );
}

/** Round initials badge for a person. */
export function Avatar({ name, large }: { name: string; large?: boolean }) {
  return <span className={`avatar${large ? " avatar-lg" : ""}`} aria-hidden="true">{initials(name)}</span>;
}
