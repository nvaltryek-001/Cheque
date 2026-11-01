import { useState } from "react";
import { api, apiPage } from "../api";
import { useAuth } from "../auth";
import { AuthImage, Bars, Button, ErrorState, fmtDate, Loading, Modal, PageHead, Pagination, Panel, Pill, Stat, useAsync, useToast } from "../components/ui";

type Issue = { severity: "error" | "warning" | "info"; message: string };
type Audit = Record<string, unknown> & { issues: Issue[]; generated_at: string };

function Issues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <p className="good-text">No issues found.</p>;
  return (
    <ul className="checks">
      {issues.map((i) => (
        <li key={i.message} className="check"><Pill value={i.severity === "error" ? "fail" : i.severity === "warning" ? "warn" : "reviewed"} label={i.severity} /><span>{i.message}</span></li>
      ))}
    </ul>
  );
}

function SplitTable({ data }: { data: Record<string, Record<string, number>> }) {
  const splits = ["train", "val", "test"];
  return (
    <table className="table compact">
      <thead><tr><th>Bank</th>{splits.map((s) => <th key={s} className="r">{s}</th>)}<th className="r">total</th></tr></thead>
      <tbody>
        {Object.entries(data).map(([bank, c]) => (
          <tr key={bank}><td>{bank.toUpperCase()}</td>{splits.map((s) => <td key={s} className="num r">{c[s] ?? 0}</td>)}<td className="num r">{splits.reduce((a, s) => a + (c[s] ?? 0), 0)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function useAudit(which: "ocr" | "signature") {
  const [refresh, setRefresh] = useState(0);
  const a = useAsync(() => api<Audit>(`/api/datasets/${which}/audit${refresh ? "?refresh=1" : ""}`), [refresh]);
  return { ...a, rerun: () => setRefresh((r) => r + 1) };
}

function Browser({ which }: { which: "ocr" | "signature" }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [split, setSplit] = useState("");
  const [bank, setBank] = useState("");
  const [kind, setKind] = useState("genuine");
  const [open, setOpen] = useState<{ filename: string; split: string; bank: string } | null>(null);
  const url = which === "ocr" ? `/api/datasets/ocr/items?page=${page}&per_page=12&split=${split}&bank=${bank}`
    : `/api/datasets/signature/items?page=${page}&per_page=12&split=${split}&bank=${bank}&kind=${kind}`;
  const items = useAsync(() => apiPage<{ filename: string; split: string; bank: string; signer_id?: string }[]>(url), [url]);
  const imgUrl = (i: { filename: string; split: string; bank: string }) =>
    `/api/datasets/${which}/image?filename=${encodeURIComponent(i.filename)}&split=${i.split}&bank=${i.bank}`;
  return (
    <Panel title="Browse images" actions={
      <div className="filters">
        {which === "signature" && <select value={kind} onChange={(e) => { setPage(1); setKind(e.target.value); }} aria-label="Kind"><option value="genuine">Genuine</option><option value="altered">Synthetic altered</option></select>}
        <select value={split} onChange={(e) => { setPage(1); setSplit(e.target.value); }} aria-label="Split"><option value="">All splits</option><option value="train">train</option><option value="val">val</option><option value="test">test</option></select>
        <select value={bank} onChange={(e) => { setPage(1); setBank(e.target.value); }} aria-label="Bank"><option value="">All banks</option>{["axis", "canara", "icici", "syndicate"].map((b) => <option key={b} value={b}>{b.toUpperCase()}</option>)}</select>
      </div>
    }>
      {items.loading && !items.data ? <Loading /> : items.error ? <ErrorState message={items.error} /> : items.data && (
        <>
          <div className="thumbs">
            {items.data.data.map((i) => (
              <button key={`${i.split}/${i.bank}/${i.filename}`} className="thumb" onClick={() => setOpen(i)} disabled={!can("admin", "operator")}>
                <span className="thumb-name">{i.filename}</span>
                <span className="tiny muted">{i.split} · {i.bank}{i.signer_id ? ` · ${i.signer_id}` : ""}</span>
              </button>
            ))}
          </div>
          <Pagination page={page} perPage={12} total={items.data.meta.total} onPage={setPage} />
          {!can("admin", "operator") && <p className="tiny muted">Image viewing is restricted to administrators and operators.</p>}
        </>
      )}
      {open && (
        <Modal title={open.filename} onClose={() => setOpen(null)}>
          <AuthImage src={imgUrl(open)} alt={open.filename} className={which === "ocr" ? "cheque-img" : "sig-img"} />
        </Modal>
      )}
    </Panel>
  );
}

export function OcrDataset() {
  const { can } = useAuth();
  const toast = useToast();
  const a = useAudit("ocr");
  const d = a.data as (Audit & { total_images: number; images_per_split: Record<string, number>; images_per_bank_and_split: Record<string, Record<string, number>>; dimensions: Record<string, number>; actual_formats: Record<string, number>; corrupted_images: string[]; same_template_pairs: string[][]; same_template_note: string; annotation_files: string[]; filename_is_cheque_number: number; handwriting_assessment: string; paddleocr_finetune_reason: string; labels: { derivable_weak_labels: string[] } }) | null;
  return (
    <>
      <PageHead title="OCR dataset" sub={d ? `Audited ${fmtDate(d.generated_at)} · originals are read-only` : "Cheque images used for OCR development"}
        actions={can("admin") && <Button icon="refresh" busy={a.loading} onClick={() => { a.rerun(); toast("info", "Re-running audit…"); }}>Re-run audit</Button>} />
      {a.loading && !d ? <Loading label="Auditing dataset…" /> : a.error ? <ErrorState message={a.error} onRetry={a.reload} /> : d && (
        d.total_images === undefined ? <ErrorState message={(d.issues[0]?.message) ?? "Dataset not found"} /> : (
        <>
          <div className="stats">
            <Stat icon="layers" label="Images" value={d.total_images} hint={Object.entries(d.images_per_split).map(([k, v]) => `${k} ${v}`).join(" · ")} />
            <Stat icon="alert" label="Corrupted" value={d.corrupted_images.length} tone={d.corrupted_images.length ? "bad" : "good"} />
            <Stat icon="report" label="Transcription label files" value={d.annotation_files.length} tone={d.annotation_files.length ? "good" : "warn"} hint="needed for OCR training" />
            <Stat icon="info" label="Filenames = cheque no." value={d.filename_is_cheque_number} hint="weak label used for MICR" />
          </div>
          <div className="grid-2">
            <Panel title="Distribution by bank and split"><SplitTable data={d.images_per_bank_and_split} /></Panel>
            <Panel title="Issues"><Issues issues={d.issues} /></Panel>
          </div>
          <div className="grid-2">
            <Panel title="Handwriting and labels">
              <p>{d.handwriting_assessment}</p>
              <p className="muted">{d.paddleocr_finetune_reason}</p>
              <p className="tiny">Usable weak labels: {d.labels.derivable_weak_labels.join("; ")}.</p>
            </Panel>
            <Panel title="Image sizes and formats">
              <Bars rows={Object.entries(d.dimensions).map(([label, value]) => ({ label, value }))} />
              <p className="tiny muted">Formats by content: {Object.entries(d.actual_formats).map(([k, v]) => `${k} ${v}`).join(", ")}</p>
              <p className="tiny muted">{d.same_template_pairs.length} near-identical template pairs. {d.same_template_note}</p>
            </Panel>
          </div>
          <Browser which="ocr" />
        </>)
      )}
    </>
  );
}

export function SignatureDataset() {
  const { can } = useAuth();
  const a = useAudit("signature");
  const d = a.data as (Audit & { genuine_rows: number; altered_rows: number; unique_signers: number; specimens_per_signer: Record<string, number>; signers_per_split: Record<string, number>; genuine_per_bank_and_split: Record<string, Record<string, number>>; missing_images: string[]; corrupted_images: string[]; signers_in_multiple_splits: string[]; feasibility: Record<string, string>; image_size: { genuine: Record<string, number> } }) | null;
  return (
    <>
      <PageHead title="Signature dataset" sub={d ? `Audited ${fmtDate(d.generated_at)} · originals and manifests are never modified` : "Genuine specimens and synthetic alterations"}
        actions={can("admin") && <Button icon="refresh" busy={a.loading} onClick={a.rerun}>Re-run audit</Button>} />
      {a.loading && !d ? <Loading label="Auditing dataset…" /> : a.error ? <ErrorState message={a.error} onRetry={a.reload} /> : d && (
        d.genuine_rows === undefined ? <ErrorState message={(d.issues[0]?.message) ?? "Dataset not found"} /> : (
        <>
          <div className="stats">
            <Stat icon="signature" label="Genuine specimens" value={d.genuine_rows} hint={`${d.unique_signers} signers`} />
            <Stat icon="edit" label="Synthetic alterations" value={d.altered_rows} hint="stroke edits, not real forgeries" />
            <Stat icon="users" label="Specimens per signer" value={Object.keys(d.specimens_per_signer).join(", ")} tone="warn" hint="signer-specific training needs several" />
            <Stat icon="shield" label="Signer leakage across splits" value={d.signers_in_multiple_splits.length} tone={d.signers_in_multiple_splits.length ? "bad" : "good"} />
          </div>
          <div className="grid-2">
            <Panel title="Genuine specimens by bank and split"><SplitTable data={d.genuine_per_bank_and_split} /></Panel>
            <Panel title="Issues"><Issues issues={d.issues} /></Panel>
          </div>
          <Panel title="What this data can support">
            <ul className="checks">
              {Object.entries(d.feasibility).map(([k, v]) => (
                <li key={k} className="check"><Pill value={v.startsWith("BLOCKED") ? "BLOCKED" : v.startsWith("feasible") ? "READY" : "review"} label={v.split(" ")[0].replace("–", "")} /><span><b>{k.replace(/_/g, " ")}</b> – {v}</span></li>
              ))}
            </ul>
            <p className="tiny muted">Genuine image size: {d.image_size.genuine.min_w}–{d.image_size.genuine.max_w} × {d.image_size.genuine.min_h}–{d.image_size.genuine.max_h} px (median {d.image_size.genuine.median_w} × {d.image_size.genuine.median_h}).</p>
          </Panel>
          <Browser which="signature" />
        </>)
      )}
    </>
  );
}
