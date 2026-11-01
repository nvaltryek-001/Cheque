// Thin client for the Flask API. Every call sends the bearer token and
// unwraps the {ok, data, meta, error} envelope.

const TOKEN_KEY = "cv_token";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface Page<T> {
  data: T;
  meta: { total: number; page: number; per_page: number };
}

let token: string | null = null;
try {
  token = sessionStorage.getItem(TOKEN_KEY);
} catch {
  token = null;
}
let onUnauthorized: (() => void) | null = null;

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: keep token in memory only */
  }
}
export const getToken = () => token;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the server. Check that the backend is running.");
  }
  if (!res.ok) {
    let code = "error";
    let message = `Request failed (${res.status})`;
    let details: unknown;
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
      details = body?.error?.details;
    } catch {
      /* non-JSON error */
    }
    // a wrong password or code is an answer to the sign-in form, not an expired session
    if (res.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/email/") && onUnauthorized) onUnauthorized();
    throw new ApiError(res.status, code, message, details);
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await request(path, init);
  const body = await res.json();
  return body.data as T;
}

export async function apiPage<T>(path: string): Promise<Page<T>> {
  const res = await request(path);
  const body = await res.json();
  return { data: body.data as T, meta: body.meta };
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: body instanceof FormData ? body : JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

/** Fetch a protected image as an object URL (images require the bearer token). */
export async function blobUrl(path: string): Promise<string> {
  const res = await request(path);
  return URL.createObjectURL(await res.blob());
}

/** Download a protected file (PDF/CSV/report). */
export async function download(path: string, fallbackName: string) {
  const res = await request(path);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// ------------------------------------------------------------------ types
/** "pending" = signed in with an institutional email, waiting for an administrator to give the account a role. */
export type Role = "admin" | "operator" | "auditor" | "pending";
export interface User {
  id: number;
  username: string;
  email: string | null;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}
/** What the server offers on the sign-in screen (GET /api/auth/config, no sign-in needed). */
export interface AuthConfig {
  email_signin: boolean;
  email_domain: string | null;
  email_domains: string[];
  password_signin: boolean;
  code_length: number;
  code_ttl_seconds: number;
  resend_seconds: number;
}
export interface CodeRequested {
  sent: boolean;
  email: string;
  expires_in: number;
  resend_after: number;
  code_length: number;
}
export type FieldStatus = "ok" | "review" | "missing";
export interface Field {
  value: string;
  confidence: number;
  source: string;
  raw: string;
  status: FieldStatus;
  issues: string[];
}
export interface Check {
  check: string;
  status: "pass" | "warn" | "fail";
  message: string;
}
export interface Cheque {
  id: number;
  original_filename: string;
  status: "processed" | "needs_review" | "reviewed" | "failed";
  ocr_engine: string;
  width: number;
  height: number;
  quality: { score: number; acceptable: boolean; issues: { code: string; message: string }[]; sharpness: number; brightness: number; contrast: number } | null;
  fields: Record<string, Field>;
  corrected: Record<string, string> | null;
  validation: { checks: Check[]; passed: number; failed: number; warnings: number; micr?: { raw: string; confidence: number }; signature_detection?: { found: boolean; box_fraction?: number[] } } | null;
  final: Record<string, string>;
  field_labels: Record<string, string>;
  field_order: string[];
  has_signature_crop: boolean;
  notes: string;
  processing_ms: number;
  created_at: string;
  updated_at: string;
  verifications?: { id: number; decision: Decision; score: number; signer_code: string; created_at: string; review_decision: string | null }[];
  suggested_signer?: { id: number; signer_code: string; full_name: string } | null;
  duplicate_of?: number | null;
}
export interface ChequeSummary {
  id: number;
  original_filename: string;
  status: Cheque["status"];
  created_at: string;
  bank_name: string;
  cheque_number: string;
  payee_name: string;
  amount_figures: string;
  account_number_masked: string;
  quality_score: number | null;
  review_count: number;
  batch_id: string | null;
}
export interface Signer {
  id: number;
  signer_code: string;
  full_name: string;
  bank: string;
  account_number: string;
  status: "active" | "inactive";
  source: string;
  notes: string;
  created_at: string;
  specimens_active: number;
  specimens_inactive: number;
  enrollment_status: "enrolled" | "no_specimens";
  /** none | incomplete | ready – whether the reference set is independent and large enough to trust a MATCH */
  reference_status: ReferenceStatus;
  independent_samples: number;
  independent_days: number;
}
export type ReferenceStatus = "none" | "incomplete" | "ready";
export interface Specimen {
  id: number;
  signer_id: number;
  original_filename: string;
  status: "active" | "inactive";
  quality: { score: number; acceptable: boolean; issues: { message: string }[]; ink_fraction: number } | null;
  replaced_by: number | null;
  created_at: string;
  session_id: number | null;
  source_ref: string;
  origin: "upload" | "dataset_import";
}
export type SessionMethod = "paper_scan" | "paper_photo" | "tablet" | "other";
export interface EnrollmentSession {
  id: number;
  signer_id: number;
  session_date: string;
  method: SessionMethod;
  instrument: string;
  consent_ref: string;
  notes: string;
  created_at: string;
  specimens_active: number;
  specimens_total: number;
}
export interface EnrollmentPolicy {
  min_specimens: number;
  min_sessions: number;
  cap_match_when_not_ready: boolean;
}
export interface Readiness {
  signer_id: number;
  ready: boolean;
  status: ReferenceStatus;
  active_specimens: number;
  independent_samples: number;
  independent_days: number;
  session_dates: string[];
  unsessioned_specimens: number;
  merged_specimens: number;
  missing: string[];
  notes: string[];
  policy: EnrollmentPolicy;
  sessions: EnrollmentSession[];
  consent_recorded: boolean;
}
export interface EnrollmentOverview {
  policy: EnrollmentPolicy;
  signers: number;
  signers_ready: number;
  signers_incomplete: number;
  signers_none: number;
}
export interface BatchEnrollResult {
  session_id: number;
  results: { file: string; ok: boolean; specimen?: Specimen; error?: { code: string; message: string } }[];
  enrolled: number;
  rejected: number;
  enrollment: Readiness;
}
export interface AuditEntry {
  id: number;
  username: string | null;
  action: string;
  entity: string | null;
  entity_id: string | null;
  detail: string;
  created_at: string;
}
export type Decision = "MATCH" | "REVIEW" | "MISMATCH";
export interface Verification {
  id: number;
  cheque_id: number | null;
  signer_id: number | null;
  signer_code: string | null;
  signer_name?: string;
  decision: Decision;
  score: number;
  model_version: string;
  review_decision: "APPROVED" | "REJECTED" | null;
  review_note?: string;
  created_at: string;
  created_by_name?: string;
  reviewed_by_name?: string;
  details?: {
    reasons: string[];
    capped_by_policy?: boolean;
    uncapped_decision?: Decision;
    reference_set?: { ready: boolean; status: ReferenceStatus; active_specimens: number; independent_samples: number; independent_days: number; missing: string[]; policy: EnrollmentPolicy };
    thresholds: { match: number; mismatch: number };
    per_specimen: { specimen_id: number; score: number; features: Record<string, number | null> }[];
    explanation: { strongest_agreement: [string, number][]; weakest_agreement: [string, number][] };
    query_quality: { score: number; issues: { message: string }[] };
    disclaimer: string;
  };
}
export interface ModelStatus {
  ocr_engine: { name: string; available: boolean; version: string };
  micr_model: { available: boolean; error?: string | null; trained_cheques: number | null; glyphs: number | null };
  signature_model: { available: boolean; error?: string | null; version: string; thresholds: { match: number; mismatch: number } | null; trained_at: string | null };
}
export interface Run {
  id: number;
  kind: string;
  status: "running" | "completed" | "failed";
  model_version: string | null;
  message: string;
  started_at: string;
  finished_at: string | null;
  started_by_name?: string;
  metrics?: Record<string, unknown>;
  config?: Record<string, unknown>;
}
