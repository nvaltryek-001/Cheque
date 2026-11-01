import { useEffect, useState } from "react";
import { api, apiPage, ChequeSummary, patch, post, Signer, Verification } from "../api";
import { useAuth } from "../auth";
import { AuthImage, Button, Empty, errText, FileDrop, PageHead, Panel, Pill, Stamp, useAsync, useToast } from "../components/ui";

export function SignerPicker({ value, onChange }: { value: number | null; onChange: (s: Signer | null) => void }) {
  const [q, setQ] = useState("");
  const { data } = useAsync(() => apiPage<Signer[]>(`/api/signers?status=active&per_page=50&q=${encodeURIComponent(q)}`), [q]);
  const [selected, setSelected] = useState<Signer | null>(null);
  useEffect(() => {
    if (value && (!selected || selected.id !== value)) {
      api<Signer>(`/api/signers/${value}`).then((s) => { setSelected(s); onChange(s); }).catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <div className="picker">
      <input type="search" placeholder="Search signer ID, name or account…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search signers" />
      <div className="picker-list" role="listbox" aria-label="Enrolled signers">
        {(data?.data ?? []).length === 0 && !selected && <div className="tiny muted pad">No active signers match.</div>}
        {[...(selected && !(data?.data ?? []).some((x) => x.id === selected.id) ? [selected] : []), ...(data?.data ?? [])].map((s) => (
          <button key={s.id} type="button" role="option" aria-selected={selected?.id === s.id}
            className={`picker-item${selected?.id === s.id ? " selected" : ""}`}
            onClick={() => { setSelected(s); onChange(s); }}>
            <span><b>{s.signer_code}</b> {s.full_name && <span className="muted">· {s.full_name}</span>}</span>
            <span className="tiny muted">{s.bank.toUpperCase()} · {s.specimens_active} specimen{s.specimens_active === 1 ? "" : "s"}{s.reference_status !== "ready" ? " · reference not ready" : ""}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function VerificationResult({ v, onChange }: { v: Verification; onChange?: (v: Verification) => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [note, setNote] = useState(v.review_note ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const d = v.details;
  const th = d?.thresholds;
  const review = async (decision: "APPROVED" | "REJECTED" | null) => {
    setBusy(decision ?? "clear");
    try {
      const out = await patch<Verification>(`/api/verifications/${v.id}`, { review_decision: decision, review_note: note });
      onChange?.(out);
      toast("success", decision ? `Marked ${decision.toLowerCase()}` : "Review cleared");
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(null);
    }
  };
  const best = d?.per_specimen?.reduce((a, b) => (b.score > a.score ? b : a), d.per_specimen[0]);
  return (
    <div className="result">
      <div className="result-top">
        <Stamp decision={v.decision} score={v.score} />
        <div className="result-meta">
          <div>Signer <b>{v.signer_code ?? "—"}</b>{v.signer_name ? ` · ${v.signer_name}` : ""}</div>
          <div className="tiny muted">Model {v.model_version} · check #{v.id}{v.cheque_id ? <> · <a href={`#/cheques/${v.cheque_id}`}>cheque #{v.cheque_id}</a></> : null}</div>
          {th && (
            <div className="scale" aria-label="Score scale">
              <span className="scale-zone mismatch" style={{ width: `${th.mismatch * 100}%` }} />
              <span className="scale-zone review" style={{ width: `${(th.match - th.mismatch) * 100}%` }} />
              <span className="scale-zone match" style={{ width: `${(1 - th.match) * 100}%` }} />
              <span className="scale-mark" style={{ left: `${Math.min(100, Math.max(0, v.score * 100))}%` }} />
            </div>
          )}
          {th && <div className="tiny muted">mismatch ≤ {th.mismatch.toFixed(3)} · review between · match ≥ {th.match.toFixed(3)}</div>}
        </div>
      </div>
      <div className="compare">
        <figure>
          <AuthImage src={can("admin", "operator") ? `/api/verifications/${v.id}/query-image` : null} alt="Questioned signature" className="sig-img" />
          <figcaption>Questioned signature</figcaption>
        </figure>
        <figure>
          <AuthImage src={best && can("admin", "operator") ? `/api/specimens/${best.specimen_id}/image?variant=original` : null} alt="Closest reference specimen" className="sig-img" />
          <figcaption>Closest enrolled specimen{best ? ` (#${best.specimen_id})` : ""}</figcaption>
        </figure>
      </div>
      {d?.capped_by_policy && (
        <div className="banner banner-warn" role="note">
          <b>Not a MATCH by policy.</b> The similarity score alone reached MATCH, but this signer's reference set is not ready
          ({d.reference_set?.independent_samples ?? 0} of {d.reference_set?.policy.min_specimens ?? "?"} independent specimens, {d.reference_set?.independent_days ?? 0} of {d.reference_set?.policy.min_sessions ?? "?"} signing days),
          so the decision was lowered to REVIEW. {v.signer_id && <a href={`#/enrollment/${v.signer_id}`}>Open the signer's enrollment</a>}
        </div>
      )}
      {d && (
        <>
          <ul className="reasons">{d.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          {d.explanation && (
            <p className="tiny">
              Strongest agreement: {d.explanation.strongest_agreement.map(([k, s]) => `${k} ${s?.toFixed(2)}`).join(", ")}.
              {" "}Weakest: {d.explanation.weakest_agreement.map(([k, s]) => `${k} ${s?.toFixed(2)}`).join(", ")}.
            </p>
          )}
          <details>
            <summary>Per-specimen feature scores</summary>
            <div className="table-wrap">
              <table className="table compact">
                <thead><tr><th>Specimen</th><th className="r">Score</th>{Object.keys(d.per_specimen[0]?.features ?? {}).map((k) => <th key={k} className="r">{k}</th>)}</tr></thead>
                <tbody>
                  {d.per_specimen.map((p) => (
                    <tr key={p.specimen_id}>
                      <td>#{p.specimen_id}</td>
                      <td className="num r">{p.score.toFixed(3)}</td>
                      {Object.values(p.features).map((x, i) => <td key={i} className="num r">{x == null ? "n/a" : x.toFixed(2)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <p className="disclaimer">{d.disclaimer}</p>
        </>
      )}
      <div className="review-box">
        <div>
          Manual review: {v.review_decision ? <Pill value={v.review_decision} /> : <span className="muted">not reviewed</span>}
          {v.reviewed_by_name && <span className="tiny muted"> by {v.reviewed_by_name}</span>}
        </div>
        {can("admin", "operator") && (
          <>
            <textarea rows={2} placeholder="Reviewer note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Reviewer note" />
            <div className="row-actions">
              {v.review_decision && <Button small kind="ghost" busy={busy === "clear"} onClick={() => review(null)}>Clear review</Button>}
              <Button small kind="danger" icon="x" busy={busy === "REJECTED"} onClick={() => review("REJECTED")}>Reject signature</Button>
              <Button small kind="primary" icon="check" busy={busy === "APPROVED"} onClick={() => review("APPROVED")}>Approve signature</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function SignatureVerify({ chequeId, signerId }: { chequeId: string | null; signerId: string | null }) {
  const toast = useToast();
  const [source, setSource] = useState<"cheque" | "upload">(chequeId ? "cheque" : "upload");
  const [cheque, setCheque] = useState<string>(chequeId ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"signature" | "cheque">("signature");
  const [signer, setSigner] = useState<Signer | null>(null);
  const [preview, setPreview] = useState<{ image: string; quality: { score: number; issues: { message: string }[] } } | null>(null);
  const [result, setResult] = useState<Verification | null>(null);
  const [busy, setBusy] = useState(false);
  const cheques = useAsync(() => apiPage<ChequeSummary[]>("/api/cheques?per_page=50"));
  const health = useAsync(() => api<{ ready: boolean; message: string | null }>("/api/signature/health"));

  useEffect(() => {
    setPreview(null);
    if (source !== "upload" || !file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    post<{ image: string; quality: { score: number; issues: { message: string }[] } }>("/api/signature/preprocess", fd)
      .then(setPreview)
      .catch((e) => toast("error", errText(e)));
  }, [file, mode, source, toast]);

  const run = async () => {
    if (!signer) return;
    const fd = new FormData();
    fd.append("signer_id", String(signer.id));
    if (source === "cheque") fd.append("cheque_id", cheque);
    else if (file) { fd.append("file", file); fd.append("mode", mode); }
    setBusy(true);
    try {
      const v = await post<Verification>("/api/verifications", fd);
      setResult(v);
      toast("success", `Decision: ${v.decision}`);
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(false);
    }
  };

  const ready = !!signer && (source === "cheque" ? !!cheque : !!file);

  return (
    <>
      <PageHead title="Signature verification" sub="Compare a questioned signature with the enrolled reference specimens of one signer." />
      {health.data && !health.data.ready && (
        <div className="banner banner-warn">{health.data.message} <a href="#/enrollment">Go to enrollment</a></div>
      )}
      <div className="grid-2">
        <Panel title="1 · Questioned signature">
          <div className="seg" role="tablist">
            <button role="tab" aria-selected={source === "cheque"} className={source === "cheque" ? "on" : ""} onClick={() => setSource("cheque")}>From a processed cheque</button>
            <button role="tab" aria-selected={source === "upload"} className={source === "upload" ? "on" : ""} onClick={() => setSource("upload")}>Upload an image</button>
          </div>
          {source === "cheque" ? (
            <>
              <select value={cheque} onChange={(e) => setCheque(e.target.value)} aria-label="Cheque">
                <option value="">Choose a cheque…</option>
                {(cheques.data?.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>#{c.id} · {c.cheque_number || c.original_filename} · {c.payee_name || "unknown payee"}</option>
                ))}
              </select>
              {cheque && <AuthImage src={`/api/cheques/${cheque}/signature`} alt="Signature from cheque" className="sig-img" />}
              {cheque && <p className="tiny muted">Wrong region? <a href={`#/cheques/${cheque}`}>Adjust it on the cheque page.</a></p>}
            </>
          ) : (
            <>
              <div className="seg seg-small">
                <button className={mode === "signature" ? "on" : ""} onClick={() => setMode("signature")}>Image is a signature</button>
                <button className={mode === "cheque" ? "on" : ""} onClick={() => setMode("cheque")}>Image is a full cheque</button>
              </div>
              <FileDrop label={file ? `Selected: ${file.name} – click to change` : "Drop an image here, or click to choose"} onFiles={(f) => setFile(f[0])} />
              {preview && (
                <div className="preview">
                  <img src={preview.image} alt="Preprocessed signature" className="sig-img" />
                  <span className="tiny muted">After preprocessing · quality {preview.quality.score}/100</span>
                  {preview.quality.issues.map((i) => <span className="tiny warn-text" key={i.message}>{i.message}</span>)}
                </div>
              )}
            </>
          )}
        </Panel>
        <Panel title="2 · Enrolled signer">
          <SignerPicker value={signerId ? Number(signerId) : null} onChange={setSigner} />
          {signer && signer.specimens_active === 0 && <p className="warn-text tiny">This signer has no active specimens.</p>}
          {signer && signer.specimens_active > 0 && signer.reference_status !== "ready" && (
            <p className="warn-text tiny">Reference set not ready ({signer.independent_samples} independent specimens, {signer.independent_days} signing day{signer.independent_days === 1 ? "" : "s"}): this comparison cannot end in MATCH, only REVIEW or MISMATCH.</p>
          )}
          <div className="row-actions">
            <Button kind="primary" icon="signature" busy={busy} disabled={!ready} onClick={run}>Compare signatures</Button>
          </div>
        </Panel>
      </div>
      <Panel title="3 · Result">
        {result ? <VerificationResult v={result} onChange={setResult} /> : <Empty title="No comparison yet">Choose a signature and a signer, then compare.</Empty>}
      </Panel>
    </>
  );
}
