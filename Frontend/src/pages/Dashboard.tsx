import { api, Decision, ModelStatus } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, ErrorState, fmtDate, Loading, PageHead, Panel, Pill, Stat, useAsync } from "../components/ui";
import { displayName } from "../identity";
import { navigate } from "../router";

interface Dash {
  cheques: { total: number; needs_review: number; reviewed: number; processed: number };
  avg_processing_ms: number | null;
  verifications: { total: number; match: number; review: number; mismatch: number; pending: number };
  enrollment: { signers: number; active_signers: number; specimens: number };
  models: ModelStatus;
  recent_cheques: { id: number; original_filename: string; status: string; created_at: string }[];
  recent_verifications: { id: number; decision: Decision; score: number; created_at: string; signer_code: string | null }[];
}

/** How the signature decisions split between match, review and mismatch: a bar plus the counts. */
function DecisionMix({ v }: { v: Dash["verifications"] }) {
  const total = v.match + v.review + v.mismatch;
  return (
    <>
      {total > 0 && (
        <span className="stackbar stackbar-thin" role="img" aria-label={`${v.match} match, ${v.review} review, ${v.mismatch} mismatch`}>
          <span className="good" style={{ width: `${(100 * v.match) / total}%` }} />
          <span className="warn" style={{ width: `${(100 * v.review) / total}%` }} />
          <span className="bad" style={{ width: `${(100 * v.mismatch) / total}%` }} />
        </span>
      )}
      <span>{v.match} match · {v.review} review · {v.mismatch} mismatch</span>
    </>
  );
}

export default function Dashboard() {
  const { user, can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api<Dash>("/api/dashboard"));
  return (
    <>
      <PageHead
        title={`Good to see you, ${user ? displayName(user) : ""}`}
        sub="Live figures from this installation. Nothing here is sample data."
        actions={can("admin", "operator") && <Button kind="primary" icon="upload" onClick={() => navigate("/cheques")}>Verify a cheque</Button>}
      />
      {loading && !data ? <Loading /> : error ? <ErrorState message={error} onRetry={reload} /> : data && (
        <>
          <div className="stats">
            <Stat icon="cheque" label="Cheques processed" value={data.cheques.total} hint={data.avg_processing_ms ? `${(data.avg_processing_ms / 1000).toFixed(1)} s average` : "No cheques yet"} />
            <Stat icon="clock" label="Waiting for review" value={data.cheques.needs_review} tone={data.cheques.needs_review ? "warn" : undefined} hint={`${data.cheques.reviewed} reviewed`} />
            <Stat icon="signature" label="Signature checks" value={data.verifications.total} hint={<DecisionMix v={data.verifications} />} />
            <Stat icon="users" label="Enrolled signers" value={data.enrollment.active_signers} hint={`${data.enrollment.specimens} active specimen${data.enrollment.specimens === 1 ? "" : "s"}`} tone={data.enrollment.specimens ? undefined : "warn"} />
          </div>
          <div className="grid-2">
            <Panel title="Recent cheques" actions={<Button small kind="ghost" onClick={() => navigate("/cheques")}>Open workspace</Button>}>
              {data.recent_cheques.length === 0 ? (
                <Empty title="No cheques yet">Upload a cheque image to extract its ten fields.</Empty>
              ) : (
                <ul className="list">
                  {data.recent_cheques.map((c) => (
                    <li key={c.id}>
                      <a href={`#/cheques/${c.id}`}>#{c.id} · {c.original_filename}</a>
                      <span className="list-meta"><Pill value={c.status} /> {fmtDate(c.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Recent signature decisions" actions={<Button small kind="ghost" onClick={() => navigate("/history")}>Full history</Button>}>
              {data.recent_verifications.length === 0 ? (
                <Empty title="No verifications yet">{data.enrollment.specimens ? "Run a comparison from a processed cheque." : "Enroll at least one signer before verifying."}</Empty>
              ) : (
                <ul className="list">
                  {data.recent_verifications.map((v) => (
                    <li key={v.id}>
                      <span>#{v.id} · {v.signer_code ?? "deleted signer"}</span>
                      <span className="list-meta"><Pill value={v.decision} /> <span className="num">{v.score.toFixed(3)}</span></span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
          <Panel title="Engines and models">
            <div className="kv-grid">
              <div><span>OCR engine</span><b>{data.models.ocr_engine.available ? `${data.models.ocr_engine.name} ${data.models.ocr_engine.version}` : "Not available – fields must be entered manually"}</b></div>
              <div><span>MICR reader</span><b>{data.models.micr_model.available ? `Trained on ${data.models.micr_model.trained_cheques} train-split cheques` : data.models.micr_model.error ? "File damaged: retrain or restore a backup" : "Not trained"}</b></div>
              <div><span>Signature model</span><b>{data.models.signature_model.error ? `${data.models.signature_model.version} (model file damaged)` : data.models.signature_model.version}</b></div>
              <div><span>Decision thresholds</span><b>{data.models.signature_model.thresholds ? `match ≥ ${data.models.signature_model.thresholds.match.toFixed(3)} · mismatch ≤ ${data.models.signature_model.thresholds.mismatch.toFixed(3)}` : "baseline defaults"}</b></div>
            </div>
          </Panel>
        </>
      )}
    </>
  );
}
