import { FormEvent, useState } from "react";
import { BatchEnrollResult, EnrollmentSession, patch, post, Readiness, ReferenceStatus, SessionMethod, Specimen } from "../api";
import { Button, Empty, errText, FileDrop, Meter, Modal, Panel, Pill, useToast } from "../components/ui";

export const METHOD_LABEL: Record<SessionMethod, string> = {
  paper_scan: "Paper, scanned", paper_photo: "Paper, photographed", tablet: "Signature tablet", other: "Other",
};
export const REFERENCE_LABEL: Record<ReferenceStatus, string> = {
  ready: "reference ready", incomplete: "reference not ready", none: "no specimens",
};
const MAX_BATCH = 20;
const today = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

// ------------------------------------------------------------------ readiness
export function ReadinessPanel({ r }: { r: Readiness }) {
  const pol = r.policy;
  return (
    <Panel title="Reference set" actions={<Pill value={r.status} label={REFERENCE_LABEL[r.status]} />}>
      <div className="readiness">
        <div className="readiness-meters">
          <Meter label="Independent specimens" value={r.independent_samples} target={pol.min_specimens} />
          <Meter label="Signing days" value={r.independent_days} target={pol.min_sessions} />
        </div>
      </div>
      {r.ready ? (
        <p>This signer's reference set meets the policy ({pol.min_specimens}+ independent specimens from {pol.min_sessions}+ signing days), so a MATCH can be issued.</p>
      ) : pol.cap_match_when_not_ready ? (
        <p><b>No MATCH will be issued for this signer yet</b> – comparisons stop at REVIEW. Still needed: {r.missing.join("; ") || "more independent specimens"}.</p>
      ) : (
        <p className="warn-text"><b>Reference set too thin to be reliable.</b> Still needed: {r.missing.join("; ")}. The MATCH cap is switched off in Settings, so a MATCH can still be issued.</p>
      )}
      {r.notes.length > 0 && <ul className="readiness-notes warn-text">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      {r.sessions.length > 0 && !r.consent_recorded && <p className="tiny warn-text">At least one signing session has no consent reference recorded.</p>}
      <p className="tiny muted">
        Independence is judged from the signing sessions recorded below, not from the images: the system refuses a second capture of the same
        signature, but it cannot prove when someone signed. Record a session only for signatures written on that day.
      </p>
    </Panel>
  );
}

// ------------------------------------------------------------------ sessions
export function SessionForm({ initial, onSubmit }: { initial?: Partial<EnrollmentSession>; onSubmit: (v: Record<string, string>) => Promise<void> }) {
  const [v, setV] = useState<Record<string, string>>({
    session_date: initial?.session_date ?? today(), method: initial?.method ?? "paper_scan", instrument: initial?.instrument ?? "",
    consent_ref: initial?.consent_ref ?? "", notes: initial?.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try { await onSubmit(v); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  return (
    <form className="form" onSubmit={submit}>
      <label>Day the signatures were written
        <input type="date" required max={today()} value={v.session_date} onChange={set("session_date")} />
      </label>
      <label>How they were captured
        <select value={v.method} onChange={set("method")}>{(Object.keys(METHOD_LABEL) as SessionMethod[]).map((m) => <option key={m} value={m}>{METHOD_LABEL[m]}</option>)}</select>
      </label>
      <label>Pen / tablet / scanner
        <input maxLength={80} value={v.instrument} onChange={set("instrument")} placeholder="e.g. blue ball pen, flatbed scanner 300 dpi" />
      </label>
      <label>Consent form reference
        <input maxLength={80} value={v.consent_ref} onChange={set("consent_ref")} placeholder="Reference of the signed consent – not the form itself" />
      </label>
      <label className="span-2">Notes<textarea rows={2} maxLength={500} value={v.notes} onChange={set("notes")} /></label>
      {err && <div className="form-error span-2" role="alert">{err}</div>}
      <div className="row-actions span-2"><Button kind="primary" type="submit" busy={busy}>{initial?.id ? "Save session" : "Record session"}</Button></div>
    </form>
  );
}

export function SessionsPanel({ sessions, admin, active, onNew, onEdit, onAdd, onDelete }: {
  sessions: EnrollmentSession[]; admin: boolean; active: boolean;
  onNew: () => void; onEdit: (s: EnrollmentSession) => void; onAdd: (s: EnrollmentSession) => void; onDelete: (s: EnrollmentSession) => void;
}) {
  return (
    <Panel title="Signing sessions" actions={admin && active && <Button kind="primary" icon="plus" onClick={onNew}>Record signing session</Button>}>
      {sessions.length === 0 ? (
        <Empty title="No signing sessions recorded">
          {admin ? "Record the day the signer wrote their signatures, then add those signatures to the session. Specimens that belong to no session do not count toward the reference set." : "An administrator records signing sessions."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table compact">
            <thead><tr><th>Day</th><th>Captured</th><th>Instrument</th><th>Consent ref.</th><th className="r">Signatures</th>{admin && <th />}</tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td className="num nowrap"><b>{s.session_date}</b></td>
                  <td>{METHOD_LABEL[s.method]}</td>
                  <td>{s.instrument || <span className="muted">—</span>}</td>
                  <td>{s.consent_ref || <span className="warn-text tiny">missing</span>}</td>
                  <td className="num r">{s.specimens_active}{s.specimens_total > s.specimens_active ? <span className="muted"> +{s.specimens_total - s.specimens_active}</span> : null}</td>
                  {admin && (
                    <td className="row-actions">
                      {active && <Button small onClick={() => onAdd(s)}>Add signatures</Button>}
                      <Button small onClick={() => onEdit(s)}>Edit</Button>
                      {s.specimens_total === 0 && <Button small kind="danger" icon="trash" title="Delete empty session" onClick={() => onDelete(s)} />}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ batch upload into one session
export function SessionUpload({ signerId, signerCode, sessions, sessionId, onClose, onDone }: {
  signerId: number; signerCode: string; sessions: EnrollmentSession[]; sessionId: number | null; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [session, setSession] = useState<string>(String(sessionId ?? sessions[0]?.id ?? ""));
  const [files, setFiles] = useState<File[]>([]);
  const [allowPoor, setAllowPoor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchEnrollResult | null>(null);
  const add = (picked: File[]) => {
    const merged = [...files, ...picked.filter((f) => !files.some((x) => x.name === f.name && x.size === f.size))];
    if (merged.length > MAX_BATCH) toast("error", `At most ${MAX_BATCH} files at a time – the rest were left out.`);
    setFiles(merged.slice(0, MAX_BATCH));
  };
  const run = async () => {
    const fd = new FormData();
    fd.append("session_id", session);
    if (allowPoor) fd.append("allow_poor_quality", "true");
    files.forEach((f) => fd.append("files", f));
    setBusy(true);
    try {
      const r = await post<BatchEnrollResult>(`/api/signers/${signerId}/specimens/batch`, fd);
      setResult(r);
      toast(r.enrolled ? "success" : "error", `${r.enrolled} enrolled, ${r.rejected} rejected`);
      onDone();
    } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Add signatures for ${signerCode}`} onClose={onClose} footer={result ? <Button kind="primary" onClick={onClose}>Close</Button> : <>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" icon="upload" busy={busy} disabled={!files.length || !session} onClick={run}>Enroll {files.length || ""} signature{files.length === 1 ? "" : "s"}</Button>
    </>}>
      {!result ? (
        <>
          <label className="block">Signing session
            <select value={session} onChange={(e) => setSession(e.target.value)}>
              {sessions.map((s) => <option key={s.id} value={s.id}>{s.session_date} · {METHOD_LABEL[s.method]} · {s.specimens_active} enrolled</option>)}
            </select>
          </label>
          <p className="tiny muted">Every file must be a separate signature written in this session. Re-scans and re-crops of one signature are refused as duplicates.</p>
          <FileDrop multiple label={files.length ? `${files.length} selected – drop or click to add more` : "Drop signature images, or click to choose"} onFiles={add} />
          {files.length > 0 && (
            <ul className="upload-list">
              {files.map((f) => (
                <li key={f.name + f.size}>
                  <span className="file-name">{f.name}</span>
                  <Button small kind="ghost" icon="x" title={`Remove ${f.name}`} onClick={() => setFiles(files.filter((x) => x !== f))} />
                </li>
              ))}
            </ul>
          )}
          <label className="check-label"><input type="checkbox" checked={allowPoor} onChange={(e) => setAllowPoor(e.target.checked)} /> Accept images below the quality standard (recorded in the audit log)</label>
        </>
      ) : (
        <>
          <p><b>{result.enrolled}</b> enrolled · <b>{result.rejected}</b> rejected · reference set is now <Pill value={result.enrollment.status} label={REFERENCE_LABEL[result.enrollment.status]} /></p>
          <ul className="upload-list">
            {result.results.map((x, i) => (
              <li key={`${x.file}-${i}`}>
                <span className="file-name">{x.file}</span>
                {x.ok ? <Pill value="ok" label="enrolled" /> : <span className="tiny warn-text">{x.error?.message}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ move a specimen to another session
export function LinkSession({ specimen, sessions, onClose, onDone }: { specimen: Specimen; sessions: EnrollmentSession[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [value, setValue] = useState<string>(specimen.session_id == null ? "" : String(specimen.session_id));
  const [source, setSource] = useState(specimen.source_ref);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={`Session of specimen #${specimen.id}`} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" busy={busy} onClick={async () => {
        setBusy(true);
        try {
          await patch(`/api/specimens/${specimen.id}`, { session_id: value === "" ? null : Number(value), source_ref: source });
          toast("success", "Specimen updated");
          onDone();
          onClose();
        } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
      }}>Save</Button>
    </>}>
      <p className="tiny muted">Link a specimen to the session in which it was really written. The change is recorded in the audit log.</p>
      <label className="block">Signing session
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">No session (does not count as independent)</option>
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.session_date} · {METHOD_LABEL[s.method]}</option>)}
        </select>
      </label>
      <label className="block">Source document (optional)
        <input maxLength={80} value={source} onChange={(e) => setSource(e.target.value)} placeholder="e.g. cheque or form reference" />
        <span className="tiny muted">Specimens cut from the same document count as one independent sample.</span>
      </label>
    </Modal>
  );
}
