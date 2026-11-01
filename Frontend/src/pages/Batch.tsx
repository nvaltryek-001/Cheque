import { useRef, useState } from "react";
import { ApiError, post } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, errText, FileDrop, PageHead, Panel, Pill, useToast } from "../components/ui";

interface BatchResult {
  batch_id: string;
  processed: number;
  failed: number;
  results: { file: string; ok: boolean; error?: string; id?: number; status?: string; bank_name?: string; cheque_number?: string; payee_name?: string; amount_figures?: string; review_count?: number }[];
}
type Row = BatchResult["results"][number];

const MAX = 20;

/** 12 hex characters, the shape of the ids the server makes. The cheques are sent one per request under one id. */
function newBatchId(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export default function Batch() {
  const { can } = useAuth();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number; current: string } | null>(null);
  const stop = useRef(false);

  // One cheque per request: a small server needs up to a minute for each, and a single request for twenty would run for
  // twenty minutes. Sent one by one, every result shows as soon as it is ready and a long batch can be stopped.
  const run = async () => {
    const queue = files;
    const id = newBatchId();
    const rows: Row[] = [];
    stop.current = false;
    setBusy(true);
    setResult(null);
    try {
      for (let i = 0; i < queue.length && !stop.current; i++) {
        setProgress({ done: i, total: queue.length, current: queue[i].name });
        const fd = new FormData();
        fd.append("files", queue[i]);
        fd.append("batch_id", id);
        let signedOut = false;
        try {
          const r = await post<BatchResult>("/api/cheques/batch", fd);
          rows.push(...r.results);
        } catch (e) {
          rows.push({ file: queue[i].name, ok: false, error: errText(e) });
          signedOut = e instanceof ApiError && e.status === 401;
        }
        const failed = rows.filter((r) => !r.ok).length;
        setResult({ batch_id: id, processed: rows.length - failed, failed, results: [...rows] });
        if (signedOut) break; // the rest would fail the same way
      }
      const failed = rows.filter((r) => !r.ok).length;
      const left = queue.length - rows.length;
      toast(failed || left ? "info" : "success", `${rows.length - failed} processed, ${failed} failed${left ? `, ${left} not started` : ""}`);
    } finally {
      const done = new Set(queue.slice(0, rows.length));
      setFiles((current) => current.filter((f) => !done.has(f)));
      setProgress(null);
      setBusy(false);
    }
  };

  if (!can("admin", "operator")) return <Empty title="Batch processing needs operator access" />;

  return (
    <>
      <PageHead title="Batch processing" sub={`Process up to ${MAX} cheques in one go. Each one runs the full OCR, validation and signature-detection pipeline, one after the other.`} />
      <Panel>
        <FileDrop multiple label="Drop cheque images here, or click to choose several" onFiles={(f) => setFiles([...files, ...f].slice(0, MAX))} />
        {files.length > 0 && (
          <>
            <ul className="file-list">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  <span>{f.name}</span>
                  <span className="tiny muted">{(f.size / 1024).toFixed(0)} KB</span>
                  <button className="icon-btn" aria-label={`Remove ${f.name}`} disabled={busy} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button>
                </li>
              ))}
            </ul>
            <div className="row-actions">
              <Button disabled={busy} onClick={() => setFiles([])}>Clear</Button>
              <Button kind="primary" icon="play" busy={busy} onClick={run}>Process {files.length} cheque{files.length === 1 ? "" : "s"}</Button>
            </div>
          </>
        )}
        {busy && progress && (
          <div className="batch-progress" role="status" aria-live="polite">
            <progress max={progress.total} value={progress.done} aria-label="Batch progress" />
            <p className="tiny muted">
              Cheque {progress.done + 1} of {progress.total}: {progress.current}. Reading a cheque takes a few seconds on a normal
              server and up to a minute on a small free one. Results appear below as they finish.
            </p>
            <Button small onClick={() => { stop.current = true; }}>Stop after this cheque</Button>
          </div>
        )}
      </Panel>
      {result && (
        <Panel title={`Batch ${result.batch_id}`} actions={<span className="tiny muted">{result.processed} processed · {result.failed} failed</span>}>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>File</th><th>Result</th><th>Bank</th><th>Cheque no.</th><th>Payee</th><th className="r">Amount</th><th>Fields to review</th></tr></thead>
              <tbody>
                {result.results.map((r, i) => (
                  <tr key={i}>
                    <td>{r.ok ? <a href={`#/cheques/${r.id}`}>{r.file}</a> : r.file}</td>
                    <td>{r.ok ? <Pill value={r.status ?? "processed"} /> : <span className="bad-text">{r.error}</span>}</td>
                    <td>{r.bank_name || "—"}</td>
                    <td className="num">{r.cheque_number || "—"}</td>
                    <td>{r.payee_name || "—"}</td>
                    <td className="num r">{r.amount_figures ? `₹ ${r.amount_figures}` : "—"}</td>
                    <td className="num">{r.ok ? r.review_count : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
