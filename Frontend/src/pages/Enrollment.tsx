import { FormEvent, useState } from "react";
import { apiPage, post, Signer } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, ErrorState, errText, Loading, Modal, PageHead, Pagination, Panel, Pill, useAsync, useToast } from "../components/ui";
import { navigate } from "../router";

export const BANKS = ["axis", "canara", "icici", "syndicate", "sbi", "hdfc", "pnb", "bob", "kotak", "union", "yes", "other"];

export function SignerForm({ initial, onSubmit, submitLabel }: { initial?: Partial<Signer>; onSubmit: (v: Record<string, string>) => Promise<void>; submitLabel: string }) {
  const [v, setV] = useState<Record<string, string>>({
    signer_code: initial?.signer_code ?? "", full_name: initial?.full_name ?? "", bank: initial?.bank ?? "axis",
    account_number: initial?.account_number ?? "", notes: initial?.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try { await onSubmit(v); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  const set = (k: string) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  return (
    <form className="form" onSubmit={submit}>
      <label>Signer ID<input required value={v.signer_code} onChange={set("signer_code")} placeholder="e.g. person_121" /></label>
      <label>Full name<input value={v.full_name} onChange={set("full_name")} placeholder="As on bank records" /></label>
      <label>Bank
        <select value={v.bank} onChange={set("bank")}>{BANKS.map((b) => <option key={b} value={b}>{b.toUpperCase()}</option>)}</select>
      </label>
      <label>Account number<input inputMode="numeric" value={v.account_number} onChange={set("account_number")} placeholder="Used to suggest the signer for a cheque" /></label>
      <label className="span-2">Notes<textarea rows={2} value={v.notes} onChange={set("notes")} /></label>
      {err && <div className="form-error span-2" role="alert">{err}</div>}
      <div className="row-actions span-2"><Button kind="primary" type="submit" busy={busy}>{submitLabel}</Button></div>
    </form>
  );
}

export default function Enrollment() {
  const { can } = useAuth();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [bank, setBank] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [splits, setSplits] = useState<string[]>(["train", "val", "test"]);
  const [busy, setBusy] = useState(false);
  const list = useAsync(() => apiPage<Signer[]>(`/api/signers?page=${page}&per_page=20&q=${encodeURIComponent(search)}&bank=${bank}`), [page, search, bank]);
  const admin = can("admin");

  return (
    <>
      <PageHead
        title="Signature enrollment"
        sub="Authorised signers and their reference specimens. Only administrators can enroll or change specimens."
        actions={admin && (
          <>
            <Button icon="dataset" onClick={() => setImporting(true)}>Import from dataset</Button>
            <Button kind="primary" icon="plus" onClick={() => setCreating(true)}>New signer</Button>
          </>
        )}
      />
      <Panel
        title="Signers"
        actions={
          <form className="filters" onSubmit={(e) => { e.preventDefault(); setPage(1); setSearch(q); }}>
            <input type="search" placeholder="Search ID, name, account…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search signers" />
            <select value={bank} onChange={(e) => { setPage(1); setBank(e.target.value); }} aria-label="Filter by bank">
              <option value="">All banks</option>
              {BANKS.map((b) => <option key={b} value={b}>{b.toUpperCase()}</option>)}
            </select>
          </form>
        }
      >
        {list.loading && !list.data ? <Loading /> : list.error ? <ErrorState message={list.error} onRetry={list.reload} /> :
          list.data && list.data.data.length === 0 ? (
            <Empty title="No signers enrolled" action={admin ? <Button kind="primary" icon="plus" onClick={() => setCreating(true)}>Enroll the first signer</Button> : undefined}>
              {admin ? "Create a signer, then upload one or more specimen signatures." : "Ask an administrator to enroll signers."}
            </Empty>
          ) : list.data && (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Signer ID</th><th>Name</th><th>Bank</th><th>Account</th><th className="r">Specimens</th><th>Enrollment</th><th>Status</th><th>Source</th></tr></thead>
                  <tbody>
                    {list.data.data.map((s) => (
                      <tr key={s.id} className="clickable" onClick={() => navigate(`/enrollment/${s.id}`)}>
                        <td><a href={`#/enrollment/${s.id}`} onClick={(e) => e.stopPropagation()}>{s.signer_code}</a></td>
                        <td>{s.full_name || <span className="muted">not recorded</span>}</td>
                        <td>{s.bank.toUpperCase()}</td>
                        <td className="num">{s.account_number || "—"}</td>
                        <td className="num r">{s.specimens_active}{s.specimens_inactive ? <span className="muted"> +{s.specimens_inactive}</span> : null}</td>
                        <td><Pill value={s.enrollment_status} /></td>
                        <td><Pill value={s.status} /></td>
                        <td className="tiny muted">{s.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} perPage={20} total={list.data.meta.total} onPage={setPage} />
            </>
          )}
      </Panel>
      {creating && (
        <Modal title="New signer" onClose={() => setCreating(false)}>
          <SignerForm submitLabel="Create signer" onSubmit={async (v) => {
            const s = await post<Signer>("/api/signers", v);
            toast("success", `Signer ${s.signer_code} created – now upload a specimen`);
            navigate(`/enrollment/${s.id}`);
          }} />
        </Modal>
      )}
      {importing && (
        <Modal title="Import genuine specimens from the signature dataset" onClose={() => setImporting(false)}
          footer={<>
            <Button onClick={() => setImporting(false)}>Cancel</Button>
            <Button kind="primary" busy={busy} disabled={!splits.length} onClick={async () => {
              setBusy(true);
              try {
                const r = await post<{ signers_created: number; specimens_enrolled: number; skipped_existing: number; errors: unknown[] }>("/api/signers/import-dataset", { splits });
                toast("success", `${r.signers_created} signers created, ${r.specimens_enrolled} specimens enrolled, ${r.skipped_existing} already present${r.errors.length ? `, ${r.errors.length} errors` : ""}`);
                setImporting(false);
                list.reload();
              } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
            }}>Import</Button>
          </>}>
          <p>Creates one signer per row of <code>genuine_signatures.csv</code> using its signer ID and bank. The account number is taken from the specimen filename. Names are left empty – they are not in the dataset.</p>
          <p className="muted tiny">Original files are only read; copies go to private storage. Note: these specimens were cropped from the same cheque scans, so verifying those cheques against them is not an independent test.</p>
          <div className="checks-inline">
            {["train", "val", "test"].map((s) => (
              <label key={s} className="check-label">
                <input type="checkbox" checked={splits.includes(s)} onChange={(e) => setSplits(e.target.checked ? [...splits, s] : splits.filter((x) => x !== s))} />
                {s}
              </label>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
