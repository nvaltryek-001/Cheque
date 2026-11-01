import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError, blobUrl, Decision } from "../api";
import { Icon } from "./Icon";

// ------------------------------------------------------------------ async hook
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

// ------------------------------------------------------------------- toasts
type Toast = { id: number; kind: "success" | "error" | "info"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = ++seq.current;
    setItems((s) => [...s, { id, kind, text }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <Icon name={t.kind === "error" ? "alert" : "check"} size={16} />
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

// --------------------------------------------------------------- primitives
type BtnProps = {
  children?: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "ghost" | "danger";
  icon?: string;
  busy?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
  small?: boolean;
  title?: string;
};
export function Button({ children, onClick, kind = "secondary", icon, busy, disabled, type = "button", small, title }: BtnProps) {
  return (
    <button
      type={type}
      className={`btn btn-${kind}${small ? " btn-sm" : ""}`}
      onClick={onClick}
      disabled={disabled || busy}
      title={title}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spinner" aria-hidden="true" /> : icon ? <Icon name={icon} size={16} /> : null}
      {children}
    </button>
  );
}

export function Panel({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className ?? ""}`}>
      {(title || actions) && (
        <header className="panel-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state">
      <span className="spinner spinner-lg" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <Icon name="alert" size={22} />
      <span>{message}</span>
      {onRetry && (
        <Button small onClick={onRetry} icon="refresh">
          Try again
        </Button>
      )}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state-empty">
      <strong>{title}</strong>
      {children && <span>{children}</span>}
      {action}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  ok: "good", pass: "good", processed: "good", reviewed: "info", active: "good", enrolled: "good", completed: "good",
  APPROVED: "good", MATCH: "good", READY: "good",
  review: "warn", warn: "warn", needs_review: "warn", REVIEW: "warn", running: "info", "READY (limited)": "warn",
  missing: "bad", fail: "bad", failed: "bad", inactive: "muted", MISMATCH: "bad", REJECTED: "bad", BLOCKED: "bad",
  no_specimens: "muted", ready: "good", incomplete: "warn", none: "muted",
};
export function Pill({ value, label }: { value: string; label?: string }) {
  return <span className={`pill pill-${STATUS_TONE[value] ?? "muted"}`}>{label ?? value.replace(/_/g, " ")}</span>;
}

/** The one bold element: the decision is rendered like a teller's rubber stamp. */
export function Stamp({ decision, score }: { decision: Decision; score?: number | null }) {
  return (
    <div className={`stamp stamp-${decision.toLowerCase()}`} role="img" aria-label={`Decision ${decision}`}>
      <span className="stamp-word">{decision}</span>
      {score != null && <span className="stamp-score">similarity {score.toFixed(3)}</span>}
    </div>
  );
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

export function Confirm({ text, confirmLabel, onConfirm, onClose }: { text: string; confirmLabel: string; onConfirm: () => Promise<void>; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <Modal
      title="Please confirm"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            kind="danger"
            busy={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onClose();
              } catch (e) {
                toast("error", errText(e));
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p>{text}</p>
    </Modal>
  );
}

export function Pagination({ page, perPage, total, onPage }: { page: number; perPage: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  if (total <= perPage) return <div className="pager-info">{total} record{total === 1 ? "" : "s"}</div>;
  return (
    <div className="pager">
      <span className="pager-info">
        {(page - 1) * perPage + 1}–{Math.min(total, page * perPage)} of {total}
      </span>
      <Button small disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </Button>
      <Button small disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </div>
  );
}

/** <img> for endpoints that need the bearer token. */
export function AuthImage({ src, alt, className, onLoad }: { src: string | null; alt: string; className?: string; onLoad?: (el: HTMLImageElement) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    setUrl(null);
    setFailed(false);
    if (!src) return;
    blobUrl(src)
      .then((u) => {
        made = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [src]);
  if (!src || failed) return <div className={`img-missing ${className ?? ""}`}>Image unavailable</div>;
  if (!url) return <div className={`img-loading ${className ?? ""}`} />;
  return <img src={url} alt={alt} className={className} onLoad={(e) => onLoad?.(e.currentTarget)} />;
}

export function FileDrop({ onFiles, multiple, accept = "image/*", label }: { onFiles: (f: File[]) => void; multiple?: boolean; accept?: string; label: string }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      className={`drop${over ? " drop-over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length) onFiles(multiple ? files : files.slice(0, 1));
      }}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      role="button"
      tabIndex={0}
    >
      <Icon name="upload" size={22} />
      <span>{label}</span>
      <small>PNG, JPEG, BMP, TIFF or WEBP · up to 10 MB{multiple ? " each" : ""}</small>
      <input
        ref={input}
        type="file"
        hidden
        accept={accept}
        multiple={multiple}
        onChange={(e) => {
          const files = Array.from((e.target.files ?? []) as ArrayLike<File>);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function pct(v: number | null | undefined, digits = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="muted">{sub}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: "good" | "warn" | "bad" | "info"; hint?: ReactNode }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

/** Horizontal bar list – used for small distributions (no chart library needed). */
export function Bars({ rows, tone }: { rows: { label: string; value: number }[]; tone?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <Empty title="No data yet" />;
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="bar-row" key={r.label}>
          <span className="bar-label">{r.label}</span>
          <span className="bar-track">
            <span className={`bar-fill ${tone ?? ""}`} style={{ width: `${(r.value / max) * 100}%` }} />
          </span>
          <span className="bar-value">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Progress toward a target (e.g. independent specimens 3 of 5). The width is set through the CSSOM, which the
 *  page's Content-Security-Policy allows (unlike an inline style attribute in the markup). */
export function Meter({ label, value, target }: { label: string; value: number; target: number }) {
  const done = value >= target;
  return (
    <div className="meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={target} aria-valuenow={Math.min(value, target)}>
      <div className="meter-head"><span>{label}</span><b className={`num${done ? " meter-done" : ""}`}>{value} / {target}</b></div>
      <span className="bar-track"><span className={`bar-fill${done ? " good" : ""}`} style={{ width: `${Math.min(100, (value / Math.max(1, target)) * 100)}%` }} /></span>
    </div>
  );
}
