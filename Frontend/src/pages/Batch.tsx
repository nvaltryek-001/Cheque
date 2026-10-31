import { useState } from "react";
import { post } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, errText, FileDrop, PageHead, Panel, Pill, useToast } from "../components/ui";

interface BatchResult {
  batch_id: string;
  processed: number;
  failed: number;
  results: { file: string; ok: boolean; error?: string; id?: number; status?: string; bank_name?: string; cheque_number?: string; payee_name?: string; amount_figures?: string; review_count?: number }[];
}

const MAX = 20;

export default function Batch() {
  const { can } = useAuth();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchResult | null>(null);

  const run = async () => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setBusy(true);
    try {
      const r = await post<BatchResult>("/api/cheques/batch", fd);
      setResult(r);
      setFiles([]);
      toast(r.failed ? "info" : "success", `${r.processed} processed, ${r.failed} failed`);
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!can("admin", "operator")) return <Empty title="Batch processing needs operator access" />;

  return (
    <>
      <PageHead title="Batch processing" sub={`Process up to ${MAX} cheques at once. Each one runs the full OCR, validation and signature-detection pipeline.`} />
      <Panel>
        <FileDrop multiple label="Drop cheque images here, or click to choose several" onFiles={(f) => setFiles([...files, ...f].slice(0, MAX))} />
        {files.length > 0 && (
          <>
            <ul className="file-list">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  <span>{f.name}</span>
                  <span className="tiny muted">{(f.size / 1024).toFixed(0)} KB</span>
                  <button className="icon-btn" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button>
                </li>
              ))}
            </ul>
            <div className="row-actions">
              <Button onClick={() => setFiles([])}>Clear</Button>
              <Button kind="primary" icon="play" busy={busy} onClick={run}>Process {files.length} cheque{files.length === 1 ? "" : "s"}</Button>
            </div>
            {busy && <p className="tiny muted">Processing runs on the server; about 1–3 seconds per cheque.</p>}
          </>
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
