$ErrorActionPreference = "Stop"

# ChequeVerify AI - rebuild frontend with React + TypeScript + Tailwind CSS
# Run from any PowerShell location. Recreates only the Frontend app source/config.
$Root = "N:\Cheque\ChequeA"
$Frontend = Join-Path $Root "Frontend"

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "Project root not found: $Root"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "backend\api\routes.py"))) {
    Write-Warning "Could not find backend\api\routes.py. Frontend will still be created, but API integration must be verified."
}

Write-Host "`n=== ChequeVerify AI React + TypeScript + Tailwind rebuild ===" -ForegroundColor Cyan
Write-Host "Project: $Root"
Write-Host "This script changes only Frontend. Backend and dataset are not modified." -ForegroundColor Yellow

# Preserve package install/cache if present; replace source/config only.
New-Item -ItemType Directory -Force -Path `
    $Frontend, `
    (Join-Path $Frontend "src"), `
    (Join-Path $Frontend "src\components"), `
    (Join-Path $Frontend "tests"), `
    (Join-Path $Frontend "public") | Out-Null

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

Write-Utf8NoBom (Join-Path $Frontend "package.json") @'
{
  "name": "chequeverify-ai-frontend",
  "private": true,
  "version": "2.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 127.0.0.1",
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  },
  "dependencies": {
    "html2canvas": "^1.4.1",
    "jspdf": "^2.5.2",
    "lucide-react": "^0.468.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.49.1",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.16",
    "typescript": "^5.7.2",
    "vite": "^5.4.21"
  }
}
'@

Write-Utf8NoBom (Join-Path $Frontend "index.html") @'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#06132c" />
    <meta name="description" content="ChequeVerify AI - multi-bank cheque extraction and review workspace" />
    <title>ChequeVerify AI | Document Intelligence</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
'@

Write-Utf8NoBom (Join-Path $Frontend "vite.config.ts") @'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true
      }
    }
  },
  test: undefined
});
'@

Write-Utf8NoBom (Join-Path $Frontend "tsconfig.json") @'
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false
  },
  "include": ["src"]
}
'@

Write-Utf8NoBom (Join-Path $Frontend "tsconfig.node.json") @'
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true
  },
  "include": ["vite.config.ts", "playwright.config.ts"]
}
'@

Write-Utf8NoBom (Join-Path $Frontend "tailwind.config.js") @'
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07152e",
        panel: "#0c1d3a",
        electric: "#1478ff",
        cyan: "#00d7f5"
      },
      boxShadow: {
        glow: "0 0 32px rgba(20,120,255,.18)"
      }
    }
  },
  plugins: []
};
'@

Write-Utf8NoBom (Join-Path $Frontend "postcss.config.js") @'
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {}
  }
};
'@

Write-Utf8NoBom (Join-Path $Frontend "playwright.config.ts") @'
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120000
  }
});
'@

Write-Utf8NoBom (Join-Path $Frontend "src\main.tsx") @'
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
'@

Write-Utf8NoBom (Join-Path $Frontend "src\App.tsx") @'
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownToLine, ArrowRight, BadgeCheck, Banknote,
  Bell, Check, CheckCircle2, ChevronDown, CircleHelp, Clock3, Copy, Database,
  FileCheck2, FileImage, FileText, Fingerprint, Gauge, History, Home, ImagePlus,
  Landmark, Link2, LoaderCircle, LockKeyhole, Menu, MessageCircle, Printer,
  RefreshCw, ScanLine, Search, Settings, Shield, ShieldCheck, Sparkles,
  UploadCloud, UserRound, X, Zap
} from "lucide-react";
import { jsPDF } from "jspdf";

type Field = { key: string; label: string; value: string };
type ApiResult = { uploadId?: string; raw: unknown; fields: Field[]; status?: string; message?: string };

const FIELD_DEFS = [
  ["bank_name", "Bank Name"],
  ["account_holder_name", "Account Holder / Payee Name"],
  ["amount_numeric", "Amount (Numeric)"],
  ["amount_words", "Amount (In Words)"],
  ["account_number", "Account Number"],
  ["ifsc_code", "IFSC Code"],
  ["cheque_number", "Cheque Number"],
  ["cheque_date", "Cheque Date"],
  ["branch_name", "Branch Name"],
  ["micr_code", "MICR Code"]
] as const;

const NAV = [
  { label: "Dashboard", icon: Home },
  { label: "Verify Cheque", icon: ScanLine },
  { label: "Batch Processing", icon: Database },
  { label: "Analytics", icon: Activity },
  { label: "Dataset & Training", icon: Fingerprint },
  { label: "Recent Activity", icon: History },
  { label: "Reports", icon: FileText },
  { label: "Settings", icon: Settings }
];

function findValue(obj: any, aliases: string[]): string {
  if (!obj || typeof obj !== "object") return "";
  const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  for (const [key, value] of Object.entries(obj)) {
    if (aliases.some(a => normalize(a) === normalize(key)) && value !== null && value !== undefined && typeof value !== "object") {
      return String(value);
    }
  }
  for (const value of Object.values(obj)) {
    if (value && typeof value === "object") {
      const nested = findValue(value, aliases);
      if (nested) return nested;
    }
  }
  return "";
}

function normalizeFields(raw: any): Field[] {
  const aliases: Record<string, string[]> = {
    bank_name: ["bank_name", "bank", "bankName", "bank_name_detected"],
    account_holder_name: ["account_holder_name", "account_holder", "payee_name", "payee", "accountName", "beneficiary_name", "name"],
    amount_numeric: ["amount_numeric", "amount", "numeric_amount", "amount_value", "cheque_amount"],
    amount_words: ["amount_words", "amount_in_words", "amount_text", "amount_in_word"],
    account_number: ["account_number", "account_no", "accountNumber", "account"],
    ifsc_code: ["ifsc_code", "ifsc", "ifscCode"],
    cheque_number: ["cheque_number", "check_number", "cheque_no", "chequeNumber", "check_no"],
    cheque_date: ["cheque_date", "check_date", "date", "chequeDate"],
    branch_name: ["branch_name", "branch", "branchName"],
    micr_code: ["micr_code", "micr", "micrCode"]
  };
  return FIELD_DEFS.map(([key, label]) => ({ key, label, value: findValue(raw, aliases[key]) }));
}

function getId(raw: any): string {
  const candidates = ["upload_id", "uploadId", "id", "request_id"];
  for (const key of candidates) {
    const found = findValue(raw, [key]);
    if (found) return found;
  }
  return "";
}

async function readResponse(response: Response): Promise<any> {
  const text = await response.text();
  let data: any = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch { data = { message: text }; }
  if (!response.ok) {
    throw new Error(data.error || data.message || `Request failed (${response.status})`);
  }
  return data;
}

export default function App() {
  const [page, setPage] = useState("Dashboard");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [result, setResult] = useState<ApiResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiState, setApiState] = useState<"checking" | "connected" | "offline">("checking");
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [history, setHistory] = useState<ApiResult[]>([]);
  const [searchText, setSearchText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/health")
      .then(r => readResponse(r))
      .then(() => active && setApiState("connected"))
      .catch(() => active && setApiState("offline"));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const populated = result?.fields.filter(f => f.value.trim()).length ?? 0;
  const filteredHistory = useMemo(() => history.filter(item => {
    const text = `${item.fields.map(f => f.value).join(" ")} ${item.status ?? ""}`.toLowerCase();
    return text.includes(searchText.toLowerCase());
  }), [history, searchText]);

  function chooseFile(next: File | null) {
    if (!next) return;
    const allowed = ["image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"];
    if (!allowed.includes(next.type) && !/\.(png|jpe?g|webp|bmp|tiff?)$/i.test(next.name)) {
      setNotice("Unsupported file type. Choose PNG, JPG, JPEG, WEBP, BMP or TIFF.");
      return;
    }
    if (next.size > 15 * 1024 * 1024) {
      setNotice("Image exceeds the 15 MB limit. Choose a smaller image.");
      return;
    }
    setFile(next);
    setResult(null);
    setNotice("");
  }

  function onInput(e: ChangeEvent<HTMLInputElement>) { chooseFile(e.target.files?.[0] ?? null); }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault(); setDragging(false); chooseFile(e.dataTransfer.files?.[0] ?? null);
  }

  async function analyze() {
    if (!file) { setNotice("Choose a cheque image before analysis."); return; }
    setBusy(true); setNotice(""); setResult(null);
    try {
      const form = new FormData();
      // Backend contract from the existing Flask route: upload field is named "image".
      form.append("image", file);
      const upload = await readResponse(await fetch("/api/upload", { method: "POST", body: form }));
      const uploadId = getId(upload);
      let processed: any = upload;

      if (uploadId) {
        const processResponse = await fetch("/api/process", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ upload_id: uploadId, id: uploadId })
        });
        processed = await readResponse(processResponse);
      } else {
        // Some backend versions return extraction results directly from /api/upload.
        const hasFields = normalizeFields(upload).some(f => f.value);
        if (!hasFields) {
          throw new Error("Upload succeeded, but the API did not return an upload ID or extracted fields. Check the Flask /api/upload response contract.");
        }
      }

      const combined = { ...upload, ...processed };
      const fields = normalizeFields(combined);
      const item: ApiResult = {
        uploadId: uploadId || undefined,
        raw: combined,
        fields,
        status: findValue(combined, ["status", "verification_status", "result"]),
        message: findValue(combined, ["message", "detail"])
      };
      setResult(item);
      setHistory(prev => [item, ...prev].slice(0, 8));
      const count = fields.filter(f => f.value.trim()).length;
      setNotice(count === 10 ? "Analysis completed. All 10 fields returned by the backend." : `Analysis completed; ${count}/10 fields were returned. Missing values are left blank and must not be guessed.`);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Analysis failed. Check the backend terminal.");
    } finally {
      setBusy(false);
    }
  }

  function clearAll() {
    setFile(null); setResult(null); setNotice("");
    if (inputRef.current) inputRef.current.value = "";
  }

  function reportText(item: ApiResult) {
    return [
      "ChequeVerify AI — Cheque Extraction Report",
      ...item.fields.map((f, i) => `${i + 1}. ${f.label}: ${f.value || "Not extracted"}`),
      `Fields populated: ${item.fields.filter(f => f.value.trim()).length}/10`,
      `Review status: ${item.status || "Not assessed"}`,
      "Note: Extraction output is not a guarantee of cheque authenticity."
    ].join("\n");
  }

  async function exportPdf() {
    if (!result) { setNotice("Analyze a cheque before exporting a report."); return; }
    const pdf = new jsPDF({ unit: "mm", format: "a4" });
    const margin = 14;
    pdf.setFillColor(7, 21, 46); pdf.rect(0, 0, 210, 32, "F");
    pdf.setTextColor(255, 255, 255); pdf.setFontSize(19); pdf.text("ChequeVerify AI", margin, 14);
    pdf.setFontSize(10); pdf.text("Cheque extraction and review report", margin, 22);
    pdf.setTextColor(20, 35, 60); pdf.setFontSize(12); pdf.text("Extracted cheque fields", margin, 43);
    let y = 52;
    result.fields.forEach((field, index) => {
      pdf.setDrawColor(220, 228, 240); pdf.line(margin, y + 3, 196, y + 3);
      pdf.setFontSize(9); pdf.setTextColor(100, 116, 139);
      pdf.text(`${String(index + 1).padStart(2, "0")}. ${field.label}`, margin, y);
      pdf.setFontSize(10); pdf.setTextColor(20, 35, 60);
      const value = pdf.splitTextToSize(field.value || "Not extracted", 95);
      pdf.text(value, 100, y);
      y += Math.max(8, value.length * 5 + 2);
    });
    pdf.setFontSize(9); pdf.setTextColor(80, 90, 105);
    pdf.text(`Fields populated: ${populated}/10`, margin, Math.min(y + 4, 268));
    pdf.text(`Review status: ${result.status || "Not assessed"}`, margin, Math.min(y + 10, 274));
    if (preview && file) {
      try {
        const dataUrl = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(new Error("Could not read selected image"));
          reader.readAsDataURL(file);
        });
        pdf.addPage();
        pdf.setFontSize(14); pdf.setTextColor(20, 35, 60); pdf.text("Original cheque image", margin, 17);
        const format = file.type === "image/png" ? "PNG" : "JPEG";
        const props = pdf.getImageProperties(dataUrl);
        const maxW = 182, maxH = 240;
        const scale = Math.min(maxW / props.width, maxH / props.height);
        pdf.addImage(dataUrl, format, margin, 25, props.width * scale, props.height * scale);
      } catch {
        pdf.addPage(); pdf.setFontSize(10); pdf.text("Original image could not be embedded by the browser.", margin, 20);
      }
    }
    pdf.save("chequeverify-report.pdf");
    setNotice("PDF report downloaded with extracted fields and the selected cheque image.");
  }

  async function shareWhatsApp() {
    if (!result) { setNotice("Analyze a cheque before sharing its results."); return; }
    const url = `https://wa.me/?text=${encodeURIComponent(reportText(result))}`;
    window.open(url, "_blank", "noopener,noreferrer");
    setNotice("WhatsApp opened with the text summary. Attach the downloaded PDF manually; browsers cannot silently attach local files to WhatsApp.");
  }

  async function copyReport() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(reportText(result));
      setNotice("10-field report text copied.");
    } catch {
      setNotice("Clipboard access was blocked. Use the WhatsApp share action instead.");
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={26} /></div>
          <div><div className="brand-name">ChequeVerify <span>AI</span></div><div className="brand-sub">DOCUMENT INTELLIGENCE</div></div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {NAV.map(({ label, icon: Icon }) => (
            <button key={label} className={`nav-item ${page === label ? "active" : ""}`} onClick={() => setPage(label)}>
              <Icon size={19} strokeWidth={1.8} /><span>{label}</span>{page === label && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="ai-card">
            <div className="ai-orb"><Sparkles size={22} /></div>
            <div><strong>AI cheque workspace</strong><p>10-field extraction · multi-bank dataset · reviewable output</p></div>
          </div>
          <div className="connection-box">
            <span className={`status-dot ${apiState}`} />
            <div><strong>{apiState === "connected" ? "Backend connected" : apiState === "offline" ? "Backend offline" : "Checking backend"}</strong><small>127.0.0.1:5000</small></div>
            <button title="Check backend connection" className="icon-button" onClick={() => {
              setApiState("checking");
              fetch("/api/health").then(r => { if (!r.ok) throw new Error(); setApiState("connected"); }).catch(() => setApiState("offline"));
            }}><RefreshCw size={15} /></button>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="search-box"><Search size={17} /><input aria-label="Search recent results" placeholder="Search cheques, results, or sessions..." value={searchText} onChange={e => setSearchText(e.target.value)} /></div>
          <div className="top-actions">
            <span className={`connection-label ${apiState}`}><span className="status-dot" />{apiState === "connected" ? "Backend Connected" : apiState === "offline" ? "Backend Offline" : "Checking API..."}</span>
            <button className="icon-button notification" aria-label="Notifications"><Bell size={19} /><i>3</i></button>
            <div className="profile-avatar">CV</div>
            <div className="profile-text"><strong>ChequeVerify AI</strong><small>Administrator</small></div><ChevronDown size={15} />
          </div>
        </header>

        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">DOCUMENT WORKSPACE / VERIFICATION</div>
            <h1>AI Cheque <span>Verification</span></h1>
            <p>Extract 10 key fields, review validation checks, and export a cheque report.</p>
            <div className="hero-pills"><span><ScanLine size={14} />10-Field Extraction</span><span><Sparkles size={14} />OCR Technology</span><span><Landmark size={14} />Multi-Bank Support</span><span><Shield size={14} />Fraud Indicators</span><span><LockKeyhole size={14} />Secure Workflow</span></div>
          </div>
          <div className="hero-art" aria-hidden="true"><div className="art-ring ring-one" /><div className="art-ring ring-two" /><div className="art-shield"><ShieldCheck size={82} /></div><div className="art-chip"><Fingerprint size={24} /> BANK-AGNOSTIC<br/>DOCUMENT ANALYSIS</div></div>
          <div className="hero-assurance"><span><CheckCircle2 size={16} />Secure processing</span><span><CheckCircle2 size={16} />Real API results</span><span><CheckCircle2 size={16} />Review before sharing</span></div>
        </section>

        <section className="metric-grid">
          <div className="metric-card blue"><div className="metric-icon"><FileText /></div><div><small>Total processed</small><strong>{history.length}</strong><em>Current browser session</em></div><Activity className="metric-spark" /></div>
          <div className="metric-card green"><div className="metric-icon"><CheckCircle2 /></div><div><small>Fields extracted</small><strong>{result ? `${populated}/10` : "0/10"}</strong><em>Current cheque</em></div><Gauge className="metric-spark" /></div>
          <div className="metric-card amber"><div className="metric-icon"><AlertTriangle /></div><div><small>Review required</small><strong>{result && populated < 10 ? "Yes" : result ? "Check" : "—"}</strong><em>Confirm values manually</em></div><Shield className="metric-spark" /></div>
          <div className="metric-card violet"><div className="metric-icon"><Zap /></div><div><small>Processing</small><strong>{busy ? "Running…" : "On demand"}</strong><em>Triggered by Analyze</em></div><Clock3 className="metric-spark" /></div>
        </section>

        <section className="work-grid">
          <article className="panel upload-panel">
            <div className="panel-heading"><div className="step-number">1</div><div><h2>Upload cheque image</h2><p>PNG, JPG, JPEG, WEBP, BMP, TIFF · Max 15 MB</p></div></div>
            <input ref={inputRef} className="hidden-input" type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff" onChange={onInput} />
            <div className={`dropzone ${dragging ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop} onClick={() => inputRef.current?.click()} role="button" tabIndex={0} aria-label="Choose cheque image" onKeyDown={e => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}>
              <div className="upload-cloud"><UploadCloud size={39} /></div><strong>Drag & drop your cheque here</strong><span>or browse files from your device</span><button className="primary-button choose-button" type="button" onClick={e => { e.stopPropagation(); inputRef.current?.click(); }}><ImagePlus size={17} />Choose Image</button><small>Image uploads only when you select Analyze Cheque.</small>
            </div>
            {file && preview ? <div className="preview-wrap"><div className="preview-header"><span><CheckCircle2 size={15} /> Selected: {file.name} ({Math.max(1, Math.round(file.size / 1024))} KB)</span><button className="icon-button" aria-label="Remove selected image" onClick={clearAll}><X size={17} /></button></div><img className="cheque-preview" src={preview} alt="Selected cheque preview" /></div> : <div className="empty-preview"><FileImage size={35} /><strong>Your cheque preview appears here</strong><span>Select an image to review it before analysis.</span></div>}
            <div className="button-row"><button className="secondary-button danger" onClick={clearAll}><X size={16} />Clear</button><button className="secondary-button" onClick={() => inputRef.current?.click()}><RefreshCw size={16} />Change image</button><button className="primary-button analyze-button" onClick={analyze} disabled={busy || !file}>{busy ? <LoaderCircle className="spin" size={17} /> : <ScanLine size={17} />}{busy ? "Analyzing..." : "Analyze Cheque"}</button></div>
            {notice && <div className={`notice ${notice.toLowerCase().includes("failed") || notice.toLowerCase().includes("error") || notice.toLowerCase().includes("could not") ? "notice-error" : "notice-info"}`} role="status">{notice}</div>}
          </article>

          <article className="panel results-panel" ref={resultRef}>
            <div className="panel-heading"><div className="step-number"><FileCheck2 size={22} /></div><div><h2>Extracted results</h2><p>10 key fields returned by the connected backend</p></div><span className={`result-badge ${result ? "ready" : ""}`}>{busy ? "Analyzing" : result ? "Analysis returned" : "Awaiting analysis"}</span></div>
            <div className="result-note"><CircleHelp size={15} />Values shown after analysis are returned by the backend. Missing values are never guessed.</div>
            <div className="results-layout">
              <div className="fields-card">
                <div className="fields-title"><strong>Extracted fields</strong><b>{populated}/10 populated</b></div>
                <div className="field-list">
                  {(result?.fields ?? FIELD_DEFS.map(([key, label]) => ({ key, label, value: "" }))).map((field, i) => (
                    <div className="field-row" key={field.key}><span className="field-index">{String(i + 1).padStart(2, "0")}</span><span className="field-label">{field.label}</span><strong className={field.value ? "field-value" : "field-missing"}>{field.value || "Not extracted"}</strong>{field.value && <CheckCircle2 size={15} className="field-check" />}</div>
                  ))}
                </div>
                {result && <div className="raw-status"><span>Backend status</span><strong>{result.status || "Not assessed"}</strong></div>}
              </div>
              <div className="verification-card">
                <div className="verification-title"><ShieldCheck size={17} />Verification summary</div>
                <div className={`review-status ${result && populated === 10 ? "review-ready" : ""}`}><strong>{result ? (populated === 10 ? "Extraction complete" : "Review required") : "Not assessed"}</strong><span>{result ? `${populated}/10 fields populated. Authenticity is not inferred from extraction alone.` : "Run analysis to receive actual backend results."}</span></div>
                <div className="checklist-title">Review checklist</div>
                {["Bank name detected", "IFSC code format", "Account number format", "Cheque number", "Cheque date validity", "Amount consistency", "Fraud indicators"].map(label => <div className="check-row" key={label}><ShieldCheck size={15} /><span>{label}</span><em>{result ? "Review" : "Pending"}</em></div>)}
                <small className="disclaimer">Checks remain pending unless the backend explicitly returns their results.</small>
              </div>
            </div>
            <div className="export-row">
              <button className="secondary-button" onClick={() => window.print()} disabled={!result}><Printer size={16} />Print report</button>
              <button className="secondary-button" onClick={exportPdf} disabled={!result}><ArrowDownToLine size={16} />Download PDF</button>
              <button className="whatsapp-button" onClick={shareWhatsApp} disabled={!result}><MessageCircle size={17} />Share on WhatsApp</button>
              <button className="icon-button copy-button" title="Copy report text" aria-label="Copy report text" onClick={copyReport} disabled={!result}><Copy size={16} /></button>
            </div>
            <p className="share-note">Sharing is user-triggered. WhatsApp opens with the text summary; attach the PDF manually.</p>
          </article>
        </section>

        <section className="panel history-panel">
          <div className="history-heading"><div className="history-title"><div className="step-number"><History size={20} /></div><div><h2>Recent activity</h2><p>Results from this browser session only</p></div></div><button className="secondary-button compact" onClick={() => setPage("Recent Activity")}>View all <ArrowRight size={15} /></button></div>
          {filteredHistory.length ? <div className="history-list">{filteredHistory.map((item, index) => <div className="history-item" key={`${item.uploadId ?? "upload"}-${index}`}>{preview && index === 0 ? <img src={preview} alt="Recent cheque" /> : <div className="history-thumb"><FileImage size={22} /></div>}<div className="history-info"><strong>{item.fields.find(f => f.key === "account_holder_name")?.value || "Payee not extracted"}</strong><span>{item.fields.find(f => f.key === "bank_name")?.value || "Bank not extracted"}</span><small>{item.fields.find(f => f.key === "amount_numeric")?.value || "Amount not extracted"}</small></div><span className={`history-status ${item.fields.filter(f => f.value).length === 10 ? "complete" : "review"}`}>{item.fields.filter(f => f.value).length}/10 fields</span><button className="icon-button" title="Export this result" onClick={() => { setResult(item); exportPdf(); }}><ArrowDownToLine size={16} /></button></div>)}</div> : <div className="history-empty"><History size={25} /><strong>No analyzed cheques yet</strong><span>After a real analysis, the result will appear here for this session.</span></div>}
        </section>
        <footer className="footer"><span><LockKeyhole size={13} /> Local workspace · Review extracted values before relying on them</span><span>ChequeVerify AI · Multi-bank workflow</span></footer>
      </main>
    </div>
  );
}
'@

Write-Utf8NoBom (Join-Path $Frontend "src\index.css") @'
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap");
@tailwind base;
@tailwind components;
@tailwind utilities;

:root { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #eaf1ff; background: #061126; font-synthesis: none; text-rendering: optimizeLegibility; }
* { box-sizing: border-box; }
html { min-width: 320px; background: #061126; }
body { margin: 0; min-width: 320px; min-height: 100vh; }
button, input { font: inherit; }
button { cursor: pointer; }
button:disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible, input:focus-visible, [role="button"]:focus-visible { outline: 3px solid #65b7ff; outline-offset: 2px; }
.app-shell { min-height: 100vh; display: flex; background: radial-gradient(ellipse at 72% 0%, #122957 0, #07152e 45%, #061126 100%); }
.sidebar { width: 252px; flex: 0 0 252px; min-height: 100vh; padding: 19px 14px 15px; display: flex; flex-direction: column; border-right: 1px solid #1c3459; background: rgba(4, 15, 34, .97); position: sticky; top: 0; height: 100vh; }
.brand { display: flex; gap: 11px; align-items: center; padding: 0 7px 30px; }
.brand-mark { width: 43px; height: 43px; border-radius: 14px; display: grid; place-items: center; color: white; background: linear-gradient(140deg, #56a5ff, #1552f4 75%); box-shadow: 0 6px 24px #1264ff42; }
.brand-name { font-size: 16px; font-weight: 800; letter-spacing: -.45px; white-space: nowrap; }
.brand-name span { font-size: 10px; background: #682cff; border: 1px solid #a58cff; padding: 2px 5px; border-radius: 5px; margin-left: 3px; }
.brand-sub { color: #8ca8d6; font-size: 9px; letter-spacing: 1.3px; margin-top: 5px; }
.nav-label, .eyebrow { color: #8198bc; font-size: 10px; font-weight: 800; letter-spacing: 1.8px; }
.nav-label { padding: 0 12px 12px; }
.sidebar nav { display: grid; gap: 5px; }
.nav-item { min-height: 46px; display: flex; align-items: center; gap: 12px; border: 1px solid transparent; border-radius: 10px; color: #b9c9e4; padding: 0 12px; text-align: left; background: transparent; font-size: 12px; font-weight: 600; }
.nav-item:hover { background: #10264a; color: white; }
.nav-item.active { color: white; background: linear-gradient(100deg, #154cbd, #087cf8); box-shadow: 0 7px 25px #006eff30; }
.nav-dot { margin-left: auto; width: 6px; height: 6px; background: #a7d6ff; border-radius: 50%; }
.sidebar-bottom { margin-top: auto; }
.ai-card { display: flex; gap: 11px; align-items: flex-start; padding: 14px 11px; border: 1px solid #173c6b; border-radius: 13px; background: linear-gradient(140deg, #0d2852, #07172e); margin-bottom: 10px; }
.ai-orb { width: 35px; height: 35px; flex: 0 0 35px; display: grid; place-items: center; color: #45efff; background: #153e9c; border-radius: 11px; }
.ai-card strong { font-size: 11px; color: #d8e8ff; }
.ai-card p { color: #8da9d2; font-size: 10px; line-height: 1.6; margin: 6px 0 0; }
.connection-box { border: 1px solid #1c3c62; border-radius: 11px; padding: 11px 9px; display: flex; align-items: center; gap: 8px; background: #091b35; }
.status-dot { display: inline-block; width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: #f5ad43; }
.status-dot.connected, .connection-label.connected .status-dot { background: #00d69a; box-shadow: 0 0 10px #00d69a66; }
.status-dot.offline, .connection-label.offline .status-dot { background: #ff526e; }
.connection-box strong { display: block; font-size: 10px; color: #d9e8ff; }
.connection-box small { display: block; margin-top: 4px; font-size: 9px; color: #8da8cc; }
.icon-button { position: relative; border: 1px solid #243d61; color: #a9c0e5; background: #0a1c37; display: inline-grid; place-items: center; border-radius: 9px; width: 34px; height: 34px; flex: 0 0 auto; }
.icon-button:hover:not(:disabled) { color: white; border-color: #398bff; }
.connection-box .icon-button { margin-left: auto; width: 29px; height: 29px; }
.main { min-width: 0; flex: 1; padding: 0 23px 20px; max-width: 1800px; margin: 0 auto; }
.topbar { height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.search-box { width: min(440px, 48%); height: 39px; display: flex; align-items: center; gap: 10px; padding: 0 12px; border: 1px solid #20385c; border-radius: 10px; color: #83a0cb; background: #081833; }
.search-box input { min-width: 0; flex: 1; outline: none; border: 0; color: #eaf1ff; background: transparent; font-size: 12px; }
.search-box input::placeholder { color: #7f98bd; }
.top-actions { display: flex; align-items: center; gap: 13px; }
.connection-label { display: flex; align-items: center; gap: 7px; color: #91a9cc; font-size: 11px; white-space: nowrap; }
.connection-label.connected { color: #00d69a; }
.connection-label.offline { color: #ff7185; }
.notification { background: transparent; border: 0; }
.notification i { position: absolute; top: -5px; right: -3px; display: grid; place-items: center; width: 17px; height: 17px; border-radius: 50%; color: white; background: #f43f5e; font-style: normal; font-size: 9px; }
.profile-avatar { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 50%; color: white; font-weight: 800; font-size: 12px; background: linear-gradient(145deg, #1689ff, #2842df); }
.profile-text strong, .profile-text small { display: block; white-space: nowrap; }
.profile-text strong { font-size: 11px; }
.profile-text small { margin-top: 4px; color: #91a7c9; font-size: 10px; }
.hero { min-height: 145px; position: relative; overflow: hidden; display: flex; align-items: center; padding: 22px 24px; border: 1px solid #1e4e8a; border-radius: 15px; background: linear-gradient(105deg, #102e60 0%, #123e82 49%, #082044 100%); box-shadow: inset 0 1px #ffffff0c; }
.hero::before { content: ""; position: absolute; inset: 0; opacity: .42; background-image: linear-gradient(115deg, transparent 30%, #318aff1c 30.2%, transparent 30.5%), radial-gradient(circle at 76% 48%, #167aff55, transparent 30%); pointer-events: none; }
.hero-copy { position: relative; z-index: 2; max-width: 720px; }
.hero h1 { margin: 9px 0 6px; color: #f4f8ff; font-size: clamp(25px, 2.25vw, 36px); letter-spacing: -1.4px; line-height: 1.12; }
.hero h1 span { color: #45a8ff; }
.hero p { color: #b1c8ea; font-size: 12px; margin: 0; }
.hero-pills { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
.hero-pills span { display: flex; align-items: center; gap: 6px; padding: 7px 9px; border: 1px solid #4779b5; border-radius: 7px; color: #d9eaff; background: #0a2858a8; font-size: 10px; font-weight: 600; }
.hero-art { position: absolute; right: 210px; top: -30px; width: 220px; height: 210px; opacity: .8; }
.art-ring { position: absolute; border: 1px solid #27a7ff9c; border-radius: 50%; transform: rotate(-25deg); }
.ring-one { width: 190px; height: 95px; left: 8px; top: 53px; }
.ring-two { width: 150px; height: 145px; left: 30px; top: 20px; border-color: #3580ff70; }
.art-shield { position: absolute; left: 75px; top: 49px; width: 90px; height: 104px; display: grid; place-items: center; color: #8bdfff; filter: drop-shadow(0 0 15px #078bff); background: linear-gradient(150deg, #2f94ffbb, #132c86dd); clip-path: polygon(50% 0, 95% 17%, 88% 66%, 50% 100%, 12% 66%, 5% 17%); }
.art-chip { position: absolute; bottom: 14px; right: 0; color: #c3dcff; font-size: 8px; letter-spacing: 1.4px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
.hero-assurance { position: absolute; z-index: 2; right: 17px; top: 22px; display: grid; gap: 10px; padding: 11px 12px; border: 1px solid #2d5d9b; border-radius: 10px; background: #071b3cbb; }
.hero-assurance span { display: flex; align-items: center; gap: 7px; font-size: 10px; color: #d6e8ff; white-space: nowrap; }
.hero-assurance svg { color: #00e5b0; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 13px 0; }
.metric-card { min-height: 82px; padding: 13px 14px; display: flex; align-items: center; gap: 11px; border: 1px solid #203b61; border-radius: 12px; background: linear-gradient(130deg, #0d2140, #0a1a33); }
.metric-icon { width: 39px; height: 39px; display: grid; place-items: center; border-radius: 11px; color: #e9f4ff; background: #0d68e8; box-shadow: 0 5px 17px #006eff24; }
.metric-icon svg { width: 20px; height: 20px; }
.metric-card.green .metric-icon { background: #079b68; }
.metric-card.amber .metric-icon { background: #ca7a13; }
.metric-card.violet .metric-icon { background: #6e39e7; }
.metric-card small, .metric-card strong, .metric-card em { display: block; }
.metric-card small { color: #9db3d4; font-size: 10px; }
.metric-card strong { margin-top: 4px; color: #f0f6ff; font-size: 21px; letter-spacing: -.5px; }
.metric-card em { margin-top: 3px; color: #829bc0; font-size: 9px; font-style: normal; }
.metric-spark { margin-left: auto; color: #3d9cff; width: 24px; }
.metric-card.green .metric-spark { color: #00d69a; }
.metric-card.amber .metric-spark { color: #f9b24d; }
.metric-card.violet .metric-spark { color: #a17bff; }
.work-grid { display: grid; grid-template-columns: minmax(350px, .83fr) minmax(510px, 1.35fr); gap: 13px; align-items: stretch; }
.panel { min-width: 0; border: 1px solid #203c62; border-radius: 14px; background: linear-gradient(145deg, #0b1d39, #08172e); box-shadow: 0 8px 25px #00000012; }
.upload-panel, .results-panel { padding: 15px; }
.panel-heading { display: flex; align-items: center; gap: 10px; margin-bottom: 13px; }
.step-number { width: 36px; height: 36px; flex: 0 0 36px; display: grid; place-items: center; color: white; background: linear-gradient(145deg, #2996ff, #0753e9); border-radius: 10px; box-shadow: 0 5px 15px #076eff2c; font-size: 19px; font-weight: 800; }
.panel-heading h2, .history-heading h2 { margin: 0; color: #f1f6ff; font-size: 14px; letter-spacing: -.3px; }
.panel-heading p, .history-heading p { margin: 4px 0 0; color: #8da7cc; font-size: 10px; }
.hidden-input { display: none; }
.dropzone { min-height: 183px; border: 1.5px dashed #2488ff; border-radius: 11px; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 15px; color: #dceaff; background: radial-gradient(ellipse at top, #0f2a50, #07162d 75%); cursor: pointer; transition: border-color .15s, background .15s; }
.dropzone:hover, .dropzone.dragging { border-color: #7bc8ff; background: #102a50; }
.upload-cloud { color: #328fff; margin-bottom: 7px; filter: drop-shadow(0 0 12px #087aff3a); }
.dropzone > strong { font-size: 12px; }
.dropzone > span { margin-top: 5px; color: #8eaad1; font-size: 10px; }
.dropzone > small { margin-top: 9px; color: #7795bd; font-size: 9px; text-align: center; }
.primary-button, .secondary-button, .whatsapp-button { min-height: 36px; display: inline-flex; justify-content: center; align-items: center; gap: 7px; border-radius: 8px; padding: 0 12px; font-size: 10px; font-weight: 700; transition: transform .12s, filter .12s, border-color .12s; }
.primary-button:hover:not(:disabled), .whatsapp-button:hover:not(:disabled) { filter: brightness(1.12); transform: translateY(-1px); }
.primary-button { color: white; border: 1px solid #3489ff; background: linear-gradient(100deg, #1379ff, #2455ed); box-shadow: 0 5px 14px #076eff24; }
.choose-button { min-height: 35px; margin-top: 12px; padding: 0 18px; }
.secondary-button { color: #c5d7f2; border: 1px solid #2b466d; background: #0a1c36; }
.secondary-button:hover:not(:disabled) { border-color: #4b9cff; color: white; }
.secondary-button.danger { color: #ff9aab; }
.analyze-button { flex: 1.2; }
.button-row { display: flex; gap: 7px; margin-top: 10px; }
.button-row > button { flex: 1; padding: 0 8px; white-space: nowrap; }
.preview-wrap { margin-top: 9px; overflow: hidden; border: 1px solid #26466d; border-radius: 10px; background: #06152a; }
.preview-header { min-height: 34px; padding: 6px 9px; display: flex; align-items: center; justify-content: space-between; gap: 7px; color: #7ee7bc; background: #092c31; font-size: 10px; }
.preview-header span { display: flex; align-items: center; gap: 5px; overflow-wrap: anywhere; }
.preview-header .icon-button { width: 26px; height: 26px; }
.cheque-preview { display: block; max-width: 100%; max-height: 270px; width: 100%; object-fit: contain; background: #fff; }
.empty-preview { min-height: 145px; margin-top: 9px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 7px; border: 1px dashed #1c3558; border-radius: 10px; color: #6082b0; background: #08182f; }
.empty-preview strong { font-size: 11px; color: #a8c0e2; }
.empty-preview span { font-size: 10px; color: #7793b8; }
.result-badge { margin-left: auto; padding: 7px 9px; border-radius: 7px; color: #a9bddb; background: #122744; font-size: 9px; white-space: nowrap; }
.result-badge.ready { color: #7ee7bc; background: #0b382f; }
.result-note { display: flex; align-items: center; gap: 7px; padding: 9px 10px; border: 1px solid #755322; border-radius: 8px; color: #ffd58d; background: #2b2114; font-size: 10px; margin-bottom: 10px; }
.results-layout { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(230px, .9fr); gap: 9px; }
.fields-card, .verification-card { min-width: 0; border: 1px solid #203a5e; border-radius: 9px; overflow: hidden; background: #091a32; }
.fields-title, .verification-title { min-height: 37px; display: flex; align-items: center; justify-content: space-between; gap: 7px; padding: 0 10px; color: #dceaff; background: #102544; border-bottom: 1px solid #203a5e; font-size: 10px; }
.fields-title b { color: #00d9a0; font-size: 10px; white-space: nowrap; }
.field-row { min-height: 36px; display: grid; grid-template-columns: 22px minmax(90px, .85fr) minmax(90px, 1fr) 14px; align-items: center; gap: 6px; padding: 6px 8px; border-bottom: 1px solid #1b304f; }
.field-row:last-child { border-bottom: 0; }
.field-index { color: #6685b0; font-size: 9px; }
.field-label { color: #a6bbdb; font-size: 9px; line-height: 1.35; }
.field-value { color: #eef5ff; font-size: 10px; font-weight: 600; overflow-wrap: anywhere; }
.field-missing { color: #7d91af; font-size: 9px; font-weight: 500; }
.field-check { color: #00d69a; }
.verification-title { justify-content: flex-start; }
.verification-title svg { color: #58a8ff; }
.review-status { display: grid; gap: 5px; margin: 9px; padding: 11px; border: 1px solid #79572b; border-radius: 8px; color: #ffda9b; background: #302515; }
.review-status strong { font-size: 11px; }
.review-status span { font-size: 9px; line-height: 1.5; color: #d7bd91; }
.review-status.review-ready { border-color: #17694e; color: #78ebc1; background: #0b2f2c; }
.review-status.review-ready span { color: #9dd9c6; }
.checklist-title { padding: 3px 10px 6px; color: #d7e6ff; font-size: 10px; font-weight: 700; }
.check-row { display: flex; align-items: center; gap: 6px; min-height: 29px; padding: 5px 9px; border-top: 1px solid #192e4c; color: #9db3d3; font-size: 9px; }
.check-row svg { color: #6d9cdd; flex: 0 0 auto; }
.check-row span { flex: 1; }
.check-row em { padding: 4px 6px; border-radius: 5px; background: #352714; color: #ffcf82; font-size: 8px; font-style: normal; }
.disclaimer { display: block; padding: 8px 9px; color: #728bad; font-size: 8px; line-height: 1.5; }
.raw-status { display: flex; justify-content: space-between; gap: 8px; padding: 9px; border-top: 1px solid #1b304f; color: #8fa8cc; font-size: 9px; }
.raw-status strong { color: #c8dcfa; overflow-wrap: anywhere; }
.export-row { display: flex; gap: 7px; margin-top: 10px; }
.export-row > button { flex: 1; }
.whatsapp-button { color: #effff9; border: 1px solid #00aa76; background: linear-gradient(100deg, #07975f, #00b779); }
.copy-button { flex: 0 0 34px !important; }
.share-note { margin: 7px 0 0; text-align: center; color: #718bad; font-size: 8px; }
.notice { margin-top: 9px; padding: 9px 10px; border-radius: 8px; font-size: 10px; line-height: 1.5; overflow-wrap: anywhere; }
.notice-info { border: 1px solid #235a90; color: #a8d7ff; background: #0c2a4c; }
.notice-error { border: 1px solid #883b51; color: #ffb4c0; background: #351b2a; }
.history-panel { margin-top: 13px; padding: 13px 15px; }
.history-heading, .history-title { display: flex; align-items: center; gap: 10px; }
.history-heading { justify-content: space-between; }
.history-title .step-number { width: 33px; height: 33px; flex-basis: 33px; }
.secondary-button.compact { min-height: 30px; }
.history-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 11px; }
.history-item { min-width: 0; display: flex; align-items: center; gap: 9px; padding: 9px; border: 1px solid #203a5d; border-radius: 9px; background: #091a32; }
.history-item > img, .history-thumb { width: 66px; height: 45px; flex: 0 0 66px; object-fit: contain; border-radius: 5px; background: #eaf2ff; }
.history-thumb { display: grid; place-items: center; color: #468aff; background: #102b50; }
.history-info { min-width: 0; flex: 1; }
.history-info strong, .history-info span, .history-info small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-info strong { color: #dceaff; font-size: 10px; }
.history-info span { margin-top: 4px; color: #91a9cc; font-size: 9px; }
.history-info small { margin-top: 4px; color: #8aa7ce; font-size: 9px; }
.history-status { padding: 5px 6px; border-radius: 6px; font-size: 8px; white-space: nowrap; color: #ffd58d; background: #352714; }
.history-status.complete { color: #8df0c7; background: #0b382f; }
.history-item .icon-button { width: 28px; height: 28px; }
.history-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 24px 8px 15px; color: #6383af; }
.history-empty strong { color: #c0d4f1; font-size: 11px; }
.history-empty span { color: #7e98bc; font-size: 10px; text-align: center; }
.footer { display: flex; justify-content: space-between; gap: 12px; padding: 13px 2px 0; color: #6984a9; font-size: 9px; }
.footer span { display: flex; align-items: center; gap: 6px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (min-width: 1500px) { .main { padding-left: 28px; padding-right: 28px; } .work-grid { grid-template-columns: minmax(400px, .85fr) minmax(600px, 1.35fr); } }
@media (max-width: 1250px) { .sidebar { width: 220px; flex-basis: 220px; } .hero-art { right: 175px; opacity: .5; } .hero-assurance { right: 12px; } .work-grid { grid-template-columns: minmax(310px, .8fr) minmax(450px, 1.2fr); } .results-layout { grid-template-columns: 1fr; } .verification-card { display: block; } .check-row { min-height: 27px; } }
@media (max-width: 980px) { .sidebar { width: 70px; flex-basis: 70px; padding: 15px 8px; } .brand { justify-content: center; padding: 0 0 25px; } .brand > div:last-child, .nav-label, .nav-item span, .sidebar-bottom { display: none; } .nav-item { justify-content: center; padding: 0; } .main { padding: 0 13px 18px; } .hero-art, .hero-assurance { display: none; } .work-grid { grid-template-columns: 1fr; } .results-layout { grid-template-columns: minmax(0, 1.1fr) minmax(230px, .9fr); } .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .topbar { height: auto; min-height: 59px; flex-wrap: wrap; padding: 10px 0; } .search-box { width: 100%; order: 2; } .top-actions { margin-left: auto; gap: 8px; } .profile-text, .top-actions > svg:last-child { display: none; } .connection-label { font-size: 9px; } .hero { padding: 17px; } .hero h1 { font-size: 25px; } .hero p { line-height: 1.6; } .hero-pills span { font-size: 9px; } .metric-grid { gap: 7px; } .metric-card { padding: 10px; gap: 8px; min-height: 75px; } .metric-icon { width: 32px; height: 32px; } .metric-card strong { font-size: 18px; } .metric-spark { display: none; } .results-layout { grid-template-columns: 1fr; } .field-row { grid-template-columns: 20px minmax(85px, .8fr) minmax(80px, 1fr) 13px; gap: 4px; padding: 7px 6px; } .field-label, .field-value { font-size: 9px; } .button-row { flex-wrap: wrap; } .button-row > button { min-width: 30%; } .analyze-button { flex: 1 0 100% !important; } .export-row { flex-wrap: wrap; } .export-row > button { min-width: 40%; } .copy-button { flex: 1 !important; } .history-list { grid-template-columns: 1fr; } .footer { flex-direction: column; } }
@media print { :root, body, .app-shell { background: white !important; color: #111827 !important; } .sidebar, .topbar, .hero, .metric-grid, .upload-panel, .history-panel, .footer, .export-row, .share-note { display: none !important; } .main { padding: 0; max-width: none; } .work-grid { display: block; } .results-panel, .fields-card, .verification-card { background: white !important; color: #111827 !important; border: 1px solid #cbd5e1; box-shadow: none; break-inside: avoid; } .panel-heading h2, .field-label, .field-value, .fields-title, .verification-title { color: #111827 !important; } .field-row { border-color: #e5e7eb; } .field-missing, .disclaimer, .check-row, .review-status span { color: #475569 !important; } }
'@

Write-Utf8NoBom (Join-Path $Frontend "tests\dashboard.spec.ts") @'
import { test, expect } from "@playwright/test";

const fakeFields = {
  bank_name: "Axis Bank",
  account_holder_name: "Demo Payee",
  amount_numeric: "12500.00",
  amount_words: "Twelve Thousand Five Hundred Only",
  account_number: "1234567890",
  ifsc_code: "UTIB0000123",
  cheque_number: "123456",
  cheque_date: "2026-10-30",
  branch_name: "Demo Branch",
  micr_code: "560211001"
};

test("dashboard renders all 10 field labels and clear empty state", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /AI Cheque Verification/i })).toBeVisible();
  for (const label of ["Bank Name", "Account Holder / Payee Name", "Amount (Numeric)", "Amount (In Words)", "Account Number", "IFSC Code", "Cheque Number", "Cheque Date", "Branch Name", "MICR Code"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText("0/10 populated")).toBeVisible();
});

test("uploads a cheque and displays 10 backend-returned fields", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.route("**/api/upload", async route => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ upload_id: "e2e-upload-1", message: "uploaded" }) });
  });
  await page.route("**/api/process", async route => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "processed", extracted_fields: fakeFields }) });
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sample-cheque.png",
    mimeType: "image/png",
    buffer: Buffer.from("fake image content")
  });
  await page.getByRole("button", { name: "Analyze Cheque" }).click();
  await expect(page.getByText("Axis Bank", { exact: true })).toBeVisible();
  await expect(page.getByText("Demo Payee", { exact: true })).toBeVisible();
  await expect(page.getByText("12,500.00", { exact: true })).toHaveCount(0); // backend string is rendered as returned
  await expect(page.getByText("12500.00", { exact: true })).toBeVisible();
  await expect(page.getByText("10/10 populated")).toBeVisible();
});

test("PDF and WhatsApp actions are disabled until analysis result exists", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /Download PDF/i })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Share on WhatsApp/i })).toBeDisabled();
});

test("rejects unsupported file extensions", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "not-a-cheque.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not an image")
  });
  await expect(page.getByRole("status")).toContainText("Unsupported file type");
});
'@

Write-Utf8NoBom (Join-Path $Frontend "README.md") @'
# ChequeVerify AI Frontend

React + TypeScript + Vite + Tailwind CSS dashboard.

## Development
```powershell
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:5000`.

## Production build
```powershell
npm run build
```

## E2E tests
```powershell
npx playwright install chromium
npm run test:e2e
```

Playwright tests mock the API to test UI and request handling. They do not prove the real OCR model or real-bank extraction accuracy. Test the live backend separately with representative images from each dataset bank.

## Ten displayed extraction fields
1. Bank Name
2. Account Holder / Payee Name
3. Amount (Numeric)
4. Amount (In Words)
5. Account Number
6. IFSC Code
7. Cheque Number
8. Cheque Date
9. Branch Name
10. MICR Code

The PDF export includes the selected cheque image when browser image decoding succeeds, plus the ten returned field values. WhatsApp sharing opens a prefilled text summary; the PDF must be attached manually in WhatsApp because browsers cannot silently attach a local file.
'@

Write-Host "`n=== Install dependencies ===" -ForegroundColor Cyan
Push-Location $Frontend
try {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }

    Write-Host "`n=== Production build (TypeScript + Vite) ===" -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Production build failed. Review TypeScript errors above." }

    Write-Host "`n=== Install Playwright Chromium ===" -ForegroundColor Cyan
    npx playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright browser installation failed." }

    Write-Host "`n=== Run frontend E2E tests (mock API) ===" -ForegroundColor Cyan
    npm run test:e2e
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "E2E tests failed. Read the failing test and output; do not treat integration as complete."
    }
}
finally {
    Pop-Location
}

Write-Host "`n=== Backend endpoint contract for integration review ===" -ForegroundColor Cyan
$routes = Join-Path $Root "backend\api\routes.py"
if (Test-Path $routes) {
    Select-String -LiteralPath $routes -Pattern '@.*route|def upload|def process|request\.files|request\.json|upload_id|jsonify' |
        Select-Object -First 100 LineNumber, Line |
        Format-Table -Wrap -AutoSize
}

Write-Host "`nFrontend rebuild script finished. Review build and E2E results above." -ForegroundColor Green
Write-Host "Start backend in another terminal: Set-Location '$Root\backend'; & '..\.venv312\Scripts\python.exe' app.py"
Write-Host "Start frontend: Set-Location '$Frontend'; npm run dev"
