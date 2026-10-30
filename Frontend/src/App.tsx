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
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return "";

  type JsonObject = Record<string, unknown>;

  const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  const aliasSet = new Set(aliases.map(normalize));

  // Never treat confidence metadata as cheque field values.
  const ignoredKeys = new Set([
    "confidence", "confidences", "fieldconfidence",
    "confidencescores", "scores", "score", "probabilities",
    "extractionlog", "metadata", "fieldconfidence",
    "confidencebyfield", "confidencebykey"
  ]);

  const valueKeys: string[] = [
    "value", "text", "extracted_value", "extractedValue",
    "detected_value", "detectedValue", "normalized_value",
    "normalizedValue", "raw_value", "rawValue"
  ];

  function visit(node: unknown, depth: number): string {
    if (
      !node ||
      typeof node !== "object" ||
      Array.isArray(node) ||
      depth > 8
    ) return "";

    const record = node as JsonObject;
    const entries = Object.entries(record);

    for (const [key, value] of entries) {
      if (ignoredKeys.has(normalize(key))) continue;
      if (!aliasSet.has(normalize(key))) continue;

      if (typeof value === "string" && value.trim()) return value.trim();

      if (typeof value === "number" && Number.isFinite(value)) {
        return String(value);
      }

      if (value && typeof value === "object" && !Array.isArray(value)) {
        const nested = value as JsonObject;

        for (const candidate of valueKeys) {
          const candidateValue: unknown = nested[candidate];

          if (typeof candidateValue === "string" && candidateValue.trim()) {
            return candidateValue.trim();
          }

          if (
            typeof candidateValue === "number" &&
            Number.isFinite(candidateValue)
          ) return String(candidateValue);
        }

        const found = visit(value, depth + 1);
        if (found) return found;
      }
    }

    for (const [key, value] of entries) {
      if (ignoredKeys.has(normalize(key))) continue;

      if (value && typeof value === "object") {
        const found = visit(value, depth + 1);
        if (found) return found;
      }
    }

    return "";
  }

  return visit(obj, 0);
}

function normalizeFields(raw: any): Field[] {
  const aliases: Record<string, string[]> = {
    bank_name: ["bank_name", "bank", "bankName", "bank_name_detected"],
    account_holder_name: [
      "account_holder_name", "account_holder", "payee_name", "payee",
      "accountName", "beneficiary_name", "name"
    ],
    amount_numeric: [
      "amount_numeric", "amount_figures", "amount", "numeric_amount",
      "amount_value", "cheque_amount"
    ],
    amount_words: [
      "amount_words", "amount_in_words", "amount_text", "amount_in_word"
    ],
    account_number: ["account_number", "account_no", "accountNumber"],
    ifsc_code: ["ifsc_code", "ifsc", "ifscCode"],
    cheque_number: [
      "cheque_number", "check_number", "cheque_no", "chequeNumber", "check_no"
    ],
    cheque_date: ["cheque_date", "check_date", "date", "chequeDate"],
    branch_name: ["branch_name", "branch", "branchName"],
    micr_code: ["micr_code", "micr", "micrCode"]
  };

  // Flask /api/process returns the extracted values under `fields`.
  // Prefer that object directly; never recursively search confidence metadata.
  const payload =
    raw && typeof raw === "object" &&
    raw.fields && typeof raw.fields === "object" &&
    !Array.isArray(raw.fields)
      ? raw.fields
      : raw;

  return FIELD_DEFS.map(([key, label]) => ({
    key,
    label,
    value: findValue(payload, aliases[key])
  }));
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
      "ChequeVerify AI - Cheque Extraction Report",
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
            <div><strong>AI cheque workspace</strong><p>10-field extraction Â· multi-bank dataset Â· reviewable output</p></div>
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
          <div className="metric-card violet"><div className="metric-icon"><Zap /></div><div><small>Processing</small><strong>{busy ? "Running..." : "On demand"}</strong><em>Triggered by Analyze</em></div><Clock3 className="metric-spark" /></div>
        </section>

        <section className="work-grid">
          <article className="panel upload-panel">
            <div className="panel-heading"><div className="step-number">1</div><div><h2>Upload cheque image</h2><p>PNG, JPG, JPEG, WEBP, BMP, TIFF Â· Max 15 MB</p></div></div>
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
                <div className="fields-card terminal-fields-card">
  <div className="fields-title">
    <strong>Cheque Extraction Terminal</strong>
    <b>10 FIELD REPORT</b>
  </div>
  <div style={{
    background: "#020b16",
    color: "#b8f7d4",
    padding: "16px",
    fontFamily: "Consolas, 'Courier New', monospace",
    fontSize: "12px",
    lineHeight: 1.8,
    minHeight: "300px",
    overflowX: "auto",
    borderRadius: "0 0 10px 10px"
  }}>
    <div style={{ color: "#60a5fa" }}>CHEQUEVERIFY AI / EXTRACTION REPORT</div>
    <div style={{ color: "#64748b" }}>---------------------------------------------</div>
    {(result?.fields ?? FIELD_DEFS.map(([key, label]) => ({ key, label, value: "" }))).map((field, i) => {
      const value = typeof field.value === "string" ? field.value.trim() : "";
      const missing = !value || /^(?:null|undefined|not extracted)$/i.test(value);
      return (
        <div key={field.key} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          <span style={{ color: "#60a5fa" }}>{String(i + 1).padStart(2, "0")} </span>
          <span style={{ color: "#e2e8f0" }}>{field.label}: </span>
          <span style={{ color: missing ? "#fbbf24" : "#34d399" }}>
            {missing ? "Not extracted" : value}
          </span>
        </div>
      );
    })}
    <div style={{ color: "#64748b" }}>---------------------------------------------</div>
    <div style={{ color: "#fbbf24" }}>Review extracted values against the cheque image.</div>
    <div style={{ color: "#94a3b8" }}>Confidence scores are not cheque field values.</div>
  </div>
</div>
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
        <footer className="footer"><span><LockKeyhole size={13} /> Local workspace Â· Review extracted values before relying on them</span><span>ChequeVerify AI Â· Multi-bank workflow</span></footer>
      </main>
    </div>
  );
}