import { useEffect, useState } from "react";
import { api, apiPage, ModelStatus, post, Run } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, ErrorState, errText, fmtDate, Loading, Modal, PageHead, Panel, Pill, useAsync, useToast } from "../components/ui";

type Task = { id: string; name: string; status: string; reason: string; requirements?: string; action?: string };
type Rates = { threshold: number; far: number | null; frr: number | null; precision: number | null; recall: number | null; f1: number | null; tp: number; fp: number; tn: number; fn: number };
type SplitSummary = { pairs: Record<string, number>; roc_auc: number | null; eer: number; at_match_threshold: Rates; by_negative_type: Record<string, { auc: number | null; far_at_match_threshold: number | null }>; three_way_decisions: Record<string, Record<string, number>> };

const ACTION_PATH: Record<string, string> = { signature_train: "signature", micr_train: "micr", ocr_eval: "ocr-eval" };
const KIND_LABEL: Record<string, string> = { signature_train: "Signature fusion training", micr_train: "MICR template training", ocr_eval: "OCR evaluation" };
const f3 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(3));

function SignatureMetrics({ m }: { m: Record<string, unknown> }) {
  const splits = ["val", "test"].filter((s) => m[s]) as ("val" | "test")[];
  const th = m.thresholds as { match: number; mismatch: number; val_eer: number };
  return (
    <div className="stack">
      <p>Thresholds chosen on validation: match ≥ <b>{f3(th.match)}</b>, mismatch ≤ <b>{f3(th.mismatch)}</b>. Test pairs were scored once with the frozen model.</p>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>Split</th><th className="r">Pairs</th><th className="r">ROC-AUC</th><th className="r">EER</th><th className="r">FAR</th><th className="r">FRR</th><th className="r">Precision</th><th className="r">Recall</th><th className="r">F1</th><th className="r">AUC vs altered</th><th className="r">AUC vs other signer</th></tr></thead>
          <tbody>
            {splits.map((s) => {
              const r = m[s] as SplitSummary;
              const a = r.at_match_threshold;
              return (
                <tr key={s}>
                  <td>{s}</td>
                  <td className="num r">{Object.values(r.pairs).reduce((x, y) => x + y, 0)}</td>
                  <td className="num r">{f3(r.roc_auc)}</td><td className="num r">{f3(r.eer)}</td>
                  <td className="num r">{f3(a.far)}</td><td className="num r">{f3(a.frr)}</td>
                  <td className="num r">{f3(a.precision)}</td><td className="num r">{f3(a.recall)}</td><td className="num r">{f3(a.f1)}</td>
                  <td className="num r">{f3(r.by_negative_type.positive_vs_altered?.auc)}</td>
                  <td className="num r">{f3(r.by_negative_type.positive_vs_impostor?.auc)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {splits.map((s) => {
        const r = m[s] as SplitSummary;
        const a = r.at_match_threshold;
        return (
          <div key={s} className="grid-2">
            <div>
              <h4>{s} confusion matrix (MATCH vs not, at match threshold)</h4>
              <table className="table compact confusion">
                <thead><tr><th /><th className="r">predicted match</th><th className="r">predicted not</th></tr></thead>
                <tbody>
                  <tr><td>same signature</td><td className="num r">{a.tp}</td><td className="num r">{a.fn}</td></tr>
                  <tr><td>different / altered</td><td className="num r">{a.fp}</td><td className="num r">{a.tn}</td></tr>
                </tbody>
              </table>
            </div>
            <div>
              <h4>{s} three-way decisions</h4>
              <table className="table compact">
                <thead><tr><th>Pair type</th><th className="r">MATCH</th><th className="r">REVIEW</th><th className="r">MISMATCH</th></tr></thead>
                <tbody>
                  {Object.entries(r.three_way_decisions).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td className="num r">{v.MATCH ?? 0}</td><td className="num r">{v.REVIEW ?? 0}</td><td className="num r">{v.MISMATCH ?? 0}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      <ul className="reasons">{(m.limitations as string[]).map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}

function RunDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, error, loading } = useAsync(() => api<Run>(`/api/models/runs/${id}`), [id]);
  return (
    <Modal title={data ? `${KIND_LABEL[data.kind] ?? data.kind} · run #${id}` : `Run #${id}`} onClose={onClose}>
      {loading ? <Loading /> : error ? <ErrorState message={error} /> : data && (
        data.status !== "completed" ? <p>Status: <Pill value={data.status} /> {data.message}</p> :
        data.kind === "signature_train" ? <SignatureMetrics m={data.metrics ?? {}} /> :
        <pre className="json">{JSON.stringify({ ...data.metrics, predictions: undefined }, null, 2)}</pre>
      )}
    </Modal>
  );
}

export default function Training() {
  const { can } = useAuth();
  const toast = useToast();
  const readiness = useAsync(() => api<{ tasks: Task[] }>("/api/models/readiness"));
  const status = useAsync(() => api<ModelStatus>("/api/models/status"));
  const runs = useAsync(() => apiPage<Run[]>("/api/models/runs?per_page=20"));
  const [open, setOpen] = useState<number | null>(null);
  const [split, setSplit] = useState("test");
  const reloadRuns = runs.reload;
  const reloadStatus = status.reload;
  const running = runs.data?.data.some((r) => r.status === "running");

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => { reloadRuns(); reloadStatus(); }, 3000);
    return () => clearInterval(t);
  }, [running, reloadRuns, reloadStatus]);

  const start = async (action: string) => {
    try {
      await post(`/api/models/${ACTION_PATH[action]}/run`, action === "ocr_eval" ? { split } : {});
      toast("info", `${KIND_LABEL[action]} started`);
      runs.reload();
    } catch (e) {
      toast("error", errText(e));
    }
  };

  return (
    <>
      <PageHead title="Model training" sub="What the available data supports, what has been trained, and how it performed on held-out data." />
      <Panel title="Training readiness">
        {readiness.loading && !readiness.data ? <Loading /> : readiness.error ? <ErrorState message={readiness.error} /> : (
          <ul className="tasks">
            {readiness.data?.tasks.map((t) => (
              <li key={t.id} className="task">
                <div className="task-head"><b>{t.name}</b><Pill value={t.status} /></div>
                <p>{t.reason}</p>
                {t.requirements && <p className="tiny muted">Data needed: {t.requirements}</p>}
                {t.action && can("admin") && (
                  <div className="row-actions">
                    {t.action === "ocr_eval" && (
                      <select value={split} onChange={(e) => setSplit(e.target.value)} aria-label="Evaluation split">
                        <option value="test">test split</option><option value="val">validation split</option><option value="train">train split</option>
                      </select>
                    )}
                    <Button small kind="primary" icon="play" disabled={running} onClick={() => start(t.action!)}>
                      {t.action === "ocr_eval" ? "Run evaluation" : "Train and evaluate"}
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {status.data && (
        <Panel title="Deployed models">
          <div className="kv-grid">
            <div><span>OCR engine</span><b>{status.data.ocr_engine.available ? `${status.data.ocr_engine.name} ${status.data.ocr_engine.version}` : "unavailable"}</b></div>
            <div><span>MICR templates</span><b>{status.data.micr_model.available ? `${status.data.micr_model.glyphs} glyphs from ${status.data.micr_model.trained_cheques} cheques` : "not trained"}</b></div>
            <div><span>Signature model</span><b>{status.data.signature_model.version}</b></div>
            <div><span>Trained</span><b>{fmtDate(status.data.signature_model.trained_at)}</b></div>
          </div>
        </Panel>
      )}
      <Panel title="Training and evaluation history" actions={<Button small icon="refresh" onClick={runs.reload}>Refresh</Button>}>
        {runs.loading && !runs.data ? <Loading /> : runs.error ? <ErrorState message={runs.error} /> : runs.data && runs.data.data.length === 0 ? (
          <Empty title="No runs recorded in this installation">The bundled models were produced by the command-line scripts; their reports are under Reports.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>#</th><th>Job</th><th>Status</th><th>Model version</th><th>Started</th><th>Finished</th><th>By</th><th /></tr></thead>
              <tbody>
                {runs.data?.data.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{r.id}</td><td>{KIND_LABEL[r.kind] ?? r.kind}</td>
                    <td><Pill value={r.status} /> {r.status !== "completed" && <span className="tiny muted">{r.message}</span>}</td>
                    <td className="tiny">{r.model_version ?? "—"}</td><td className="nowrap">{fmtDate(r.started_at)}</td><td className="nowrap">{fmtDate(r.finished_at)}</td>
                    <td>{r.started_by_name ?? "—"}</td>
                    <td>{r.status === "completed" && <Button small onClick={() => setOpen(r.id)}>View results</Button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {open && <RunDetail id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
