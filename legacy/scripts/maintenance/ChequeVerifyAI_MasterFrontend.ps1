$ErrorActionPreference = "Stop"

$Root = "N:\Cheque\ChequeA"
$Frontend = Join-Path $Root "frontend"
$Src = Join-Path $Frontend "src"
$AppFile = Join-Path $Src "App.jsx"
$CssFile = Join-Path $Src "styles.css"

if (!(Test-Path $Frontend)) { throw "Frontend folder not found: $Frontend" }
if (!(Test-Path $AppFile)) { throw "App.jsx not found: $AppFile" }
if (!(Test-Path (Join-Path $Root "backend\app.py"))) {
    throw "Backend app.py not found. Check project root before running."
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupDir = Join-Path $Root "backups\frontend-$Stamp"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
Copy-Item $AppFile (Join-Path $BackupDir "App.jsx")
if (Test-Path $CssFile) { Copy-Item $CssFile (Join-Path $BackupDir "styles.css") }

Write-Host "Backup saved to: $BackupDir" -ForegroundColor Green

# Install the icon package only if missing.
Push-Location $Frontend
try {
    npm ls lucide-react --depth=0 *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing lucide-react..." -ForegroundColor Cyan
        npm install lucide-react
        if ($LASTEXITCODE -ne 0) { throw "npm install lucide-react failed." }
    }
} finally {
    Pop-Location
}

$App = @'
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ShieldCheck, Sun, Moon, Upload, FileText, Activity, CheckCircle2,
  Clock3, AlertTriangle, Image as ImageIcon, ScanText, Download,
  RotateCcw, LoaderCircle, Server, CircleHelp, X, Eye, ChevronRight
} from "lucide-react";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:5000").replace(/\/$/, "");
const MAX_BYTES = 20 * 1024 * 1024;
const ACCEPTED = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"];

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = { message: raw }; }
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

const fieldLabels = {
  bank_name: "Bank Name", cheque_number: "Cheque Number", micr_code: "MICR Code",
  account_number: "Account Number", date: "Date", payee_name: "Payee Name",
  amount_figures: "Amount in Figures", amount_in_figures: "Amount in Figures",
  amount_words: "Amount in Words", amount_in_words: "Amount in Words",
  ifsc_code: "IFSC Code", drawer_name: "Drawer Name", branch_name: "Branch Name"
};
const metricLabels = {
  brightness: "Brightness", contrast: "Contrast", resolution: "Resolution",
  sharpness: "Sharpness", skew: "Skew", text_visibility: "Text Visibility"
};
const prettyKey = (key) => fieldLabels[key] || key.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
const prettyValue = (value) => {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "object") return value.value ?? value.text ?? JSON.stringify(value);
  return String(value);
};

export default function App() {
  const [dark, setDark] = useState(true);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [uploadId, setUploadId] = useState("");
  const [quality, setQuality] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [health, setHealth] = useState("checking");
  const [elapsed, setElapsed] = useState(null);
  const inputRef = useRef(null);
  const startedRef = useRef(0);

  useEffect(() => {
    let alive = true;
    apiRequest("/api/health")
      .then(() => alive && setHealth("ready"))
      .catch(() => alive && setHealth("offline"));
    return () => { alive = false; };
  }, []);

  useEffect(() => () => {
    if (preview && preview.startsWith("blob:")) URL.revokeObjectURL(preview);
  }, [preview]);

  const score = Number(quality?.overall_score ?? quality?.score ?? 0);
  const qualityBand = typeof quality?.quality_band === "object"
    ? (quality?.quality_band?.label || quality?.quality_band?.level || "")
    : (quality?.quality_band || "");
  const metrics = quality?.metrics && typeof quality.metrics === "object" ? quality.metrics : {};
  const fields = result?.fields || result?.extracted_fields || result?.cheque_fields || {};
  const visibleFields = Object.entries(fields).filter(([, v]) => {
    const val = prettyValue(v);
    return val.trim() !== "";
  });
  const ocr = result?.ocr || result?.ocr_results || {};
  const ocrText = ocr?.text || result?.extracted_text || "";
  const processingTime = result?.processing_time ?? elapsed;
  const ocrFailed = ocr?.status === "ERROR" || ocr?.status === "DISABLED";

  const reset = () => {
    if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
    setFile(null); setPreview(""); setUploadId(""); setQuality(null);
    setResult(null); setError(""); setStage(""); setElapsed(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const chooseFile = async (selected) => {
    if (!selected) return;
    setError(""); setResult(null); setQuality(null); setUploadId(""); setElapsed(null);
    if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
    const ext = "." + (selected.name.split(".").pop() || "").toLowerCase();
    if (!ACCEPTED.includes(ext)) { setError("Unsupported file type. Choose JPG, PNG, BMP, WEBP or TIFF."); return; }
    if (selected.size > MAX_BYTES) { setError("File exceeds the 20 MB upload limit."); return; }
    setFile(selected); setPreview(URL.createObjectURL(selected)); setBusy(true); setStage("Uploading cheque image…");
    try {
      const fd = new FormData();
      fd.append("image", selected);
      const data = await apiRequest("/api/upload", { method: "POST", body: fd });
      if (!data.upload_id) throw new Error("Upload succeeded but the server did not return an upload ID.");
      setUploadId(data.upload_id);
      setQuality(data.quality || null);
      setStage("Image uploaded. Ready to run OCR analysis.");
    } catch (e) {
      setError(e.message || "Upload failed.");
      setStage("");
    } finally { setBusy(false); }
  };

  const runAnalysis = async () => {
    if (!uploadId) { setError("Upload a cheque image before running analysis."); return; }
    setBusy(true); setError(""); setStage("Running local OCR…");
    startedRef.current = performance.now();
    try {
      const data = await apiRequest("/api/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          upload_id: uploadId,
          run_ocr: true,
          run_vlm: false,
          run_verification: false
        })
      });
      setResult(data);
      setElapsed(((performance.now() - startedRef.current) / 1000).toFixed(1));
      setStage(data.ocr?.status === "ERROR" ? "Analysis finished with an OCR error." : "Processing complete.");
      if (data.ocr?.status === "ERROR") setError(`OCR could not read this image: ${data.ocr.error || "unknown OCR error"}`);
    } catch (e) {
      setError(e.message || "Processing failed.");
      setStage("");
    } finally { setBusy(false); }
  };

  const exportReport = () => {
    const report = {
      project: "ChequeVerify AI",
      created_at: new Date().toISOString(),
      upload_id: uploadId,
      filename: file?.name || uploadId,
      quality_score: score,
      quality_band: qualityBand,
      processing_time_seconds: processingTime,
      ocr_status: ocr?.status || "NOT_RUN",
      ocr_text: ocrText,
      extracted_fields: fields,
      verification: "MANUAL_REVIEW_REQUIRED",
      note: "This prototype has no validated forgery-detection model. This report is not proof of cheque authenticity."
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `cheque-report-${Date.now()}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const metricEntries = useMemo(() => Object.entries(metrics), [metrics]);
  const metricValue = (value) => {
    if (typeof value === "number") return value;
    if (typeof value === "object" && value) return Number(value.score ?? value.value ?? 0);
    return 0;
  };
  const metricMessage = (value) => typeof value === "object" && value ? (value.message || value.label || `${metricValue(value).toFixed(0)} / 100`) : (value == null ? "No metric returned" : `${metricValue(value).toFixed(0)} / 100`);

  return (
    <div className={`app-shell ${dark ? "theme-dark" : "theme-light"}`}>
      <header className="topbar">
        <div className="brand">
          <div className="brand-shield"><ShieldCheck size={42} strokeWidth={1.8} /></div>
          <div><h1>Cheque Verification</h1><p>AI-Powered Image Quality Assessment &amp; Verification</p></div>
        </div>
        <div className="top-actions">
          <span className={`system-pill ${health}`}><span className="status-dot" />{health === "ready" ? "System ready" : health === "offline" ? "API offline" : "Checking API"}</span>
          <button className="icon-button" title="Toggle light/dark theme" onClick={() => setDark(v => !v)}>{dark ? <Sun size={18}/> : <Moon size={18}/>}</button>
        </div>
      </header>

      <main className="dashboard">
        <section className="top-grid">
          <article className="panel upload-panel">
            <div className="panel-heading">
              <span className="heading-icon blue"><ImageIcon size={22}/></span>
              <div className="heading-copy"><h2>Image Preview</h2><p>Uploaded cheque document</p></div>
              <span className="step-tag">STEP 01</span>
            </div>
            <button className={`dropzone ${file ? "has-file" : ""}`} onClick={() => inputRef.current?.click()} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); chooseFile(e.dataTransfer.files?.[0]); }}>
              {preview ? <img className="cheque-preview" src={preview} alt="Uploaded cheque preview" /> : <span className="drop-empty"><Upload size={32}/><strong>Drop your cheque image here</strong><span>or click to browse files</span><small>Clear, well-lit images improve results</small></span>}
            </button>
            <input ref={inputRef} className="hidden-input" type="file" accept=".jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff" onChange={e => chooseFile(e.target.files?.[0])}/>
            <div className="file-summary">
              <span className="file-icon"><FileText size={21}/></span>
              <div className="file-copy"><strong>{file?.name || "No cheque uploaded"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "JPG, PNG, BMP, WEBP or TIFF"}</small></div>
              {file && <button className="mini-button" title="Choose another image" onClick={() => inputRef.current?.click()}><RotateCcw size={16}/></button>}
            </div>
            <div className="upload-actions">
              <button className="btn btn-primary full" onClick={runAnalysis} disabled={!uploadId || busy}>{busy ? <><LoaderCircle className="spin" size={18}/> Working…</> : <><Activity size={18}/> Run analysis</>}</button>
              <button className="btn btn-quiet" onClick={reset} disabled={busy}>Reset</button>
            </div>
            <p className="micro-note"><ShieldCheck size={14}/> Quality analysis is provided by your local API. Authenticity is not guaranteed.</p>
            {stage && <p className="stage-message"><Activity size={14}/>{stage}</p>}
            {error && <div className="error-banner"><AlertTriangle size={16}/><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error"><X size={15}/></button></div>}
            <p className="formats">Supported: JPG, PNG, BMP, WEBP, TIFF <span>•</span> Max size: 20 MB</p>
          </article>

          <article className="panel quality-panel">
            <div className="panel-heading">
              <span className="heading-icon teal"><Activity size={22}/></span>
              <div className="heading-copy"><h2>Image Quality Analysis</h2><p>Pre-verification quality assessment</p></div>
              <span className="step-tag">STEP 02</span>
            </div>
            <div className="score-card">
              <div className={`score-ring ${score >= 80 ? "score-good" : score >= 60 ? "score-warn" : "score-bad"}`} style={{ "--score": `${Math.max(0, Math.min(100, score))}%` }}>
                <div><strong>{quality ? score.toFixed(1) : "—"}</strong><small>SCORE</small></div>
              </div>
              <div className="score-copy"><strong>{quality ? (qualityBand || (score >= 80 ? "Good" : score >= 60 ? "Acceptable with caution" : "Poor")) : "Awaiting image"}</strong><p>{quality ? "Image quality assessment returned by the backend." : "Upload an image to calculate quality metrics."}</p><small>{quality ? "Quality score does not establish cheque authenticity." : "Quality metrics will appear here after upload."}</small></div>
              <div className="score-side">
                <div><CheckCircle2 size={19}/><span><b>{result ? (ocrFailed ? "OCR needs attention" : "OCR response received") : "OCR not run"}</b><small>{result ? `${result.ocr_text_length ?? ocrText.length ?? 0} characters detected` : "Run analysis to extract text"}</small></span></div>
                <div><Clock3 size={19}/><span><b>Processing time</b><small>{processingTime != null ? `${processingTime} seconds` : "—"}</small></span></div>
                <div><FileText size={19}/><span><b>Text extraction</b><small>{ocrText ? "Text returned by OCR" : "No OCR text available yet"}</small></span></div>
              </div>
            </div>
            <div className="subsection-title"><h3>Quality Metrics</h3>{quality && <span>Live API data</span>}</div>
            <div className="metrics-grid">
              {(metricEntries.length ? metricEntries : Object.keys(metricLabels).map(k => [k, null])).map(([key, value]) => {
                const val = Math.max(0, Math.min(100, metricValue(value)));
                const known = value !== null && value !== undefined;
                const label = metricLabels[key] || prettyKey(key);
                const status = !known ? "WAIT" : val >= 60 ? "PASS" : "REVIEW";
                return <div className="metric-card" key={key}>
                  <div className="metric-top"><span>{label}</span><b className={`metric-badge ${status.toLowerCase()}`}>{status}</b></div>
                  <div className="progress-track"><i style={{ width: `${known ? val : 0}%` }}/></div>
                  <small>{known ? metricMessage(value) : "Awaiting image analysis"}</small>
                </div>;
              })}
            </div>
            {quality?.recommendations?.length > 0 && <div className="recommendation"><strong>Recommendations</strong><p>{quality.recommendations.map(r => typeof r === "string" ? r : r.message || JSON.stringify(r)).join(" • ")}</p></div>}
          </article>
        </section>

        <section className="bottom-grid">
          <article className="panel details-panel">
            <div className="panel-heading">
              <span className="heading-icon blue"><ScanText size={22}/></span>
              <div className="heading-copy"><h2>Extracted Cheque Details</h2><p>Only populated fields are displayed</p></div>
              <span className="step-tag">OCR</span>
            </div>
            {visibleFields.length ? <div className="details-table">
              {visibleFields.map(([key, value]) => <div className="detail-row" key={key}><span>{prettyKey(key)}</span><strong>{prettyValue(value)}</strong></div>)}
            </div> : <div className="empty-state"><FileText size={26}/><strong>{result ? "No cheque fields recognized" : "No extracted fields yet"}</strong><p>{result ? "OCR may have returned no readable text, or the field extractor could not identify the values. Try a clear, straight image." : "Upload a cheque and run analysis to extract text using the local OCR engine."}</p></div>}
            {ocrText && <details className="ocr-details"><summary><Eye size={15}/> View raw OCR text <ChevronRight size={15}/></summary><pre>{ocrText}</pre></details>}
          </article>

          <article className="panel verification-panel">
            <div className="panel-heading">
              <span className="heading-icon shield"><ShieldCheck size={22}/></span>
              <div className="heading-copy"><h2>Verification Decision</h2><p>Confidence-aware review workflow</p></div>
              <span className="step-tag">STEP 03</span>
            </div>
            <div className="review-alert"><AlertTriangle size={28}/><div><strong>Manual review recommended</strong><p>The forgery-detection model is not validated. Do not treat this output as proof of authenticity.</p></div></div>
            <div className="verification-info">
              <div><span className="info-label">Model status</span><strong>Not validated</strong><small>No validated model weights are configured in this prototype.</small></div>
              <div><span className="info-label">Recommended next steps</span><ul><li>Review the cheque manually</li><li>Verify key fields against bank records</li><li>Use a validated model for authenticity checks</li><li>Check for signs of tampering</li></ul></div>
            </div>
            <div className="decision-actions">
              <button className="btn btn-primary" onClick={runAnalysis} disabled={!uploadId || busy}>{busy ? <LoaderCircle className="spin" size={17}/> : <Activity size={17}/>} Run analysis</button>
              <button className="btn btn-secondary" onClick={exportReport} disabled={!result}><Download size={17}/> Export report</button>
              <button className="btn btn-quiet" onClick={reset}><RotateCcw size={17}/> Reset</button>
            </div>
          </article>
        </section>
        <footer className="footer"><span>ChequeVerify AI <b>•</b> Local research prototype</span><span><CircleHelp size={13}/> Image quality ≠ cheque authenticity</span></footer>
      </main>
    </div>
  );
}
'@

$Css = @'
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{font-family:'DM Sans',system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-synthesis:none;text-rendering:optimizeLegibility;-webkit-font-smoothing:antialiased}
*{box-sizing:border-box}
body{margin:0;min-width:320px}
button,input{font:inherit}
button{cursor:pointer}
button:disabled{opacity:.48;cursor:not-allowed}
.app-shell{min-height:100vh;--bg:#061525;--panel:#0d2235;--panel2:#102b40;--border:#23455f;--text:#f1f7ff;--muted:#a8c0d4;--muted2:#7e9ab2;--teal:#19d7ae;--blue:#328cff;--green:#51e6bf;--yellow:#ffc04d;background:radial-gradient(ellipse at 55% -20%,#075c63 0%,transparent 45%),var(--bg);color:var(--text)}
.theme-light{--bg:#f1f5fa;--panel:#fff;--panel2:#f7faff;--border:#d7e2ed;--text:#13243a;--muted:#526b84;--muted2:#6b8096;--teal:#079d83;--green:#059f83;background:var(--bg);color:var(--text)}
.topbar{min-height:94px;padding:17px 3.2%;display:flex;align-items:center;justify-content:space-between;gap:18px;background:linear-gradient(105deg,#087a79,#08b89e 56%,#00545f);border-bottom:1px solid #1dd5b8}
.brand{display:flex;align-items:center;gap:22px}.brand-shield{width:68px;height:68px;display:grid;place-items:center;color:#eaffff}.brand h1{font:800 clamp(23px,2.3vw,34px) Manrope,sans-serif;letter-spacing:-.7px;margin:0}.brand p{margin:2px 0 0;color:#d6fffa;font-size:14px}.top-actions{display:flex;align-items:center;gap:12px}.system-pill{display:flex;align-items:center;gap:9px;border:1px solid #1de0b8;background:#053e49a8;padding:9px 15px;border-radius:12px;font-size:12px;white-space:nowrap}.status-dot{width:9px;height:9px;border-radius:50%;background:#18e2ad}.system-pill.offline .status-dot{background:#ff6262}.system-pill.checking .status-dot{background:#ffc04d}.icon-button{border:1px solid #66c9c0;background:#003e4b9c;color:white;width:39px;height:39px;border-radius:50%;display:grid;place-items:center}
.dashboard{max-width:1800px;margin:auto;padding:18px 2.1% 12px}.top-grid{display:grid;grid-template-columns:minmax(300px,.82fr) minmax(500px,1.18fr);gap:16px}.bottom-grid{display:grid;grid-template-columns:minmax(420px,1.2fr) minmax(380px,.98fr);gap:16px;margin-top:16px}.panel{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--border);border-radius:13px;padding:17px;min-width:0;box-shadow:0 8px 24px #0000000b}.panel-heading{display:flex;align-items:center;gap:12px;margin-bottom:15px;min-width:0}.heading-icon{width:43px;height:43px;display:grid;place-items:center;border-radius:11px;flex-shrink:0}.heading-icon.blue{background:#1766ba55;color:#80bdff}.heading-icon.teal{background:#00a98c35;color:#45f1cb}.heading-icon.shield{background:#b5d5a52b;color:#d2e9d0}.heading-copy{min-width:0;flex:1}.heading-copy h2{font:700 16px Manrope,sans-serif;margin:0 0 3px}.heading-copy p{font-size:12px;color:var(--muted);margin:0}.step-tag{border:1px solid #2d90ad;color:#9ee7f4;border-radius:7px;padding:6px 9px;font-size:10px;font-weight:700;white-space:nowrap}.theme-light .step-tag{color:#087b91;border-color:#83c7d6}
.dropzone{width:100%;height:clamp(190px,19vw,290px);display:grid;place-items:center;background:#08192a;border:1px solid var(--border);border-radius:11px;overflow:hidden;padding:0;color:var(--muted);text-align:center}.theme-light .dropzone{background:#f3f7fb}.dropzone:focus-visible{outline:2px solid var(--teal)}.cheque-preview{width:100%;height:100%;object-fit:contain;background:#071523}.drop-empty{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:16px}.drop-empty svg{color:var(--teal)}.drop-empty strong{color:var(--text);font-size:14px}.drop-empty span,.drop-empty small{font-size:12px;color:var(--muted)}.hidden-input{display:none}.file-summary{margin-top:10px;display:flex;align-items:center;gap:11px;padding:10px 12px;border:1px solid var(--border);border-radius:10px}.file-icon{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;background:#1766ba3b;color:#8fc7ff;flex-shrink:0}.file-copy{flex:1;min-width:0}.file-copy strong,.file-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-copy strong{font-size:12px}.file-copy small{font-size:11px;color:var(--muted);margin-top:3px}.mini-button{display:grid;place-items:center;width:30px;height:30px;border:1px solid var(--border);border-radius:8px;background:transparent;color:var(--muted)}.upload-actions{display:flex;gap:9px;margin-top:11px}.btn{min-height:40px;border:1px solid var(--border);border-radius:9px;display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:9px 13px;font-size:12px;font-weight:700;color:var(--text);background:var(--panel2);transition:filter .15s,transform .15s}.btn:hover:not(:disabled){filter:brightness(1.12);transform:translateY(-1px)}.btn-primary{background:linear-gradient(110deg,#28dcb4,#00b994);color:#032a2c;border-color:#22d8b2}.btn-secondary{background:transparent}.btn-quiet{background:transparent;color:var(--muted)}.btn.full{flex:1}.micro-note{display:flex;align-items:flex-start;gap:6px;color:var(--muted);font-size:10px;line-height:1.5;margin:10px 0 0}.micro-note svg{color:var(--green);flex-shrink:0}.formats{font-size:10px;color:var(--muted2);text-align:center;margin:9px 0 0}.formats span{padding:0 5px}.stage-message{display:flex;align-items:center;gap:6px;color:var(--teal);font-size:11px;margin:8px 0 0}.error-banner{display:flex;align-items:flex-start;gap:8px;padding:10px;margin-top:10px;border:1px solid #c45d52;background:#6d211f42;border-radius:8px;color:#ffb6ad;font-size:12px}.error-banner span{flex:1}.error-banner button{border:0;background:transparent;color:inherit;padding:0}
.score-card{display:grid;grid-template-columns:128px minmax(130px,1fr) minmax(150px,.78fr);align-items:center;gap:17px;padding:16px;border:1px solid var(--border);border-radius:11px;background:#071a2b6e}.theme-light .score-card{background:#f7faff}.score-ring{width:112px;height:112px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--green) var(--score),#253e50 var(--score));position:relative}.score-ring:before{content:"";position:absolute;inset:8px;border-radius:50%;background:var(--panel)}.score-ring>div{z-index:1;display:flex;flex-direction:column;align-items:center}.score-ring strong{font:800 26px Manrope,sans-serif}.score-ring small{font-size:10px;color:var(--muted);margin-top:2px}.score-warn{background:conic-gradient(#ffbd4a var(--score),#253e50 var(--score))}.score-bad{background:conic-gradient(#ff6e72 var(--score),#253e50 var(--score))}.score-copy>strong{font:800 24px Manrope,sans-serif;color:var(--teal)}.score-copy p{font-size:12px;line-height:1.5;margin:6px 0;color:var(--text)}.score-copy small{font-size:11px;line-height:1.45;color:var(--muted)}.score-side{border-left:1px solid var(--border);padding-left:16px;display:flex;flex-direction:column;gap:12px}.score-side>div{display:flex;align-items:flex-start;gap:9px}.score-side svg{color:var(--green);flex-shrink:0}.score-side>div:nth-child(2) svg{color:var(--muted)}.score-side span{display:flex;flex-direction:column;gap:3px}.score-side b{font-size:11px;font-weight:600}.score-side small{font-size:10px;color:var(--muted);line-height:1.4}.subsection-title{display:flex;align-items:center;justify-content:space-between;margin:14px 0 8px}.subsection-title h3{font:700 14px Manrope,sans-serif;margin:0}.subsection-title span{font-size:10px;color:var(--teal)}.metrics-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.metric-card{border:1px solid var(--border);border-radius:10px;padding:11px 12px;min-width:0}.metric-top{display:flex;align-items:center;justify-content:space-between;gap:6px}.metric-top>span{font-size:11px;font-weight:700;overflow:hidden;text-overflow:ellipsis}.metric-badge{font-size:9px;border-radius:6px;padding:5px 7px}.metric-badge.pass{background:#1bb98b38;color:#7df0c6}.metric-badge.review{background:#b7771e40;color:#ffca69}.metric-badge.wait{background:#65768b40;color:var(--muted)}.progress-track{height:7px;background:#304459;border-radius:99px;margin:10px 0 7px;overflow:hidden}.progress-track i{display:block;height:100%;background:linear-gradient(90deg,#16d9aa,#22bd9d);border-radius:99px}.metric-badge.review~.progress-track i{background:#ffb942}.metric-card small{font-size:10px;color:var(--muted);line-height:1.4;display:block;overflow-wrap:anywhere}.recommendation{margin-top:10px;border:1px solid #9b6b2245;background:#a66d1a25;border-radius:8px;padding:10px}.recommendation strong{font-size:11px;color:#ffce76}.recommendation p{font-size:11px;color:var(--muted);margin:5px 0 0;line-height:1.5}
.details-table{border:1px solid var(--border);border-radius:9px;overflow:hidden}.detail-row{display:grid;grid-template-columns:minmax(130px,.7fr) minmax(0,1.5fr);border-bottom:1px solid var(--border);min-height:31px}.detail-row:last-child{border-bottom:0}.detail-row span,.detail-row strong{padding:7px 11px;font-size:12px;overflow-wrap:anywhere}.detail-row span{color:var(--muted);background:#08192a35}.detail-row strong{font-weight:600;border-left:1px solid var(--border)}.empty-state{min-height:190px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;color:var(--muted);gap:8px}.empty-state svg{color:var(--muted2)}.empty-state strong{font-size:13px;color:var(--text)}.empty-state p{max-width:420px;font-size:11px;line-height:1.6;margin:0}.ocr-details{margin-top:12px;border:1px solid var(--border);border-radius:9px;padding:10px 12px}.ocr-details summary{display:flex;align-items:center;gap:7px;cursor:pointer;font-size:11px;color:var(--muted);list-style:none}.ocr-details summary svg:last-child{margin-left:auto}.ocr-details pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;font-size:11px;line-height:1.5;color:var(--muted);padding:10px;background:#06152575;border-radius:7px}
.review-alert{display:flex;align-items:center;gap:14px;border:1px solid #b97a12;background:linear-gradient(110deg,#8a591f45,#5c3d193d);border-radius:10px;padding:15px}.review-alert>svg{color:#ffc047;flex-shrink:0}.review-alert strong{font:800 16px Manrope,sans-serif;color:#ffd65f}.review-alert p{font-size:12px;line-height:1.55;color:var(--text);margin:5px 0 0}.verification-info{display:grid;grid-template-columns:.9fr 1.1fr;gap:15px;margin-top:10px;padding:13px;border:1px solid var(--border);border-radius:9px}.verification-info>div+div{border-left:1px solid var(--border);padding-left:15px}.info-label{display:block;color:var(--muted);font-size:10px;margin-bottom:5px}.verification-info strong{display:block;font-size:13px}.verification-info small{display:block;color:var(--muted);font-size:10px;line-height:1.5;margin-top:5px}.verification-info ul{padding-left:16px;margin:4px 0 0;color:var(--muted);font-size:10px;line-height:1.65}.decision-actions{display:grid;grid-template-columns:1fr 1fr auto;gap:9px;margin-top:11px}.footer{display:flex;justify-content:space-between;align-items:center;gap:12px;color:var(--muted2);font-size:10px;padding:16px 3px 5px}.footer b{padding:0 4px;color:var(--teal)}.footer span:last-child{display:flex;align-items:center;gap:5px}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media(min-width:1500px){.panel{padding:19px}.top-grid{gap:18px}.bottom-grid{gap:18px;margin-top:18px}.dashboard{padding-top:20px}.dropzone{height:290px}}
@media(max-width:1100px){.top-grid{grid-template-columns:minmax(280px,.8fr) minmax(0,1.2fr)}.bottom-grid{grid-template-columns:1fr}.score-card{grid-template-columns:105px 1fr}.score-ring{width:96px;height:96px}.score-side{grid-column:1/-1;border-left:0;border-top:1px solid var(--border);padding:12px 0 0;display:grid;grid-template-columns:repeat(3,1fr)}.metrics-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.topbar{align-items:flex-start;padding:14px 4%;flex-direction:column}.brand{gap:10px}.brand-shield{width:46px;height:46px}.brand-shield svg{width:36px}.brand h1{font-size:22px}.brand p{font-size:11px;line-height:1.5}.top-actions{width:100%;justify-content:flex-end}.dashboard{padding:12px 3%}.top-grid,.bottom-grid{grid-template-columns:1fr;gap:12px}.panel{padding:13px}.dropzone{height:220px}.score-card{grid-template-columns:90px 1fr;gap:12px;padding:12px}.score-ring{width:84px;height:84px}.score-ring strong{font-size:21px}.score-copy>strong{font-size:19px}.score-side{grid-template-columns:1fr;gap:10px}.metrics-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric-card{padding:9px}.metric-top>span{font-size:10px}.verification-info{grid-template-columns:1fr}.verification-info>div+div{border-left:0;border-top:1px solid var(--border);padding:12px 0 0}.decision-actions{grid-template-columns:1fr 1fr}.decision-actions .btn-quiet{grid-column:1/-1}.detail-row{grid-template-columns:minmax(110px,.8fr) minmax(0,1.2fr)}.footer{align-items:flex-start;flex-direction:column}.step-tag{font-size:9px;padding:5px 6px}.heading-copy h2{font-size:14px}.heading-copy p{font-size:10px}}
@media(max-width:390px){.metrics-grid{grid-template-columns:1fr}.upload-actions{flex-direction:column}.upload-actions .btn-quiet{width:100%}.decision-actions{grid-template-columns:1fr}.decision-actions .btn-quiet{grid-column:auto}.detail-row span,.detail-row strong{padding:7px;font-size:11px}}
'@

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($AppFile, $App, $Utf8NoBom)
[System.IO.File]::WriteAllText($CssFile, $Css, $Utf8NoBom)

Write-Host "`nFrontend files replaced successfully." -ForegroundColor Green
Write-Host "App: $AppFile"
Write-Host "CSS: $CssFile"

Push-Location $Frontend
try {
    Write-Host "`nRunning production build..." -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed. Original files are backed up in $BackupDir"
    }
} finally {
    Pop-Location
}

Write-Host "`nSUCCESS: New dashboard built. Backend and dataset were not changed." -ForegroundColor Green
Write-Host "Start backend in one terminal:" -ForegroundColor Cyan
Write-Host 'Set-Location "N:\Cheque\ChequeA\backend"; & "..\.venv312\Scripts\python.exe" app.py'
Write-Host "Start frontend in another terminal:" -ForegroundColor Cyan
Write-Host 'Set-Location "N:\Cheque\ChequeA\frontend"; npm run dev -- --host 127.0.0.1'
Write-Host "Open http://127.0.0.1:5173/" -ForegroundColor Green
