import { useState } from "react";
import { apiPage, Cheque, ChequeSummary, post } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorState, errText, FileDrop, fmtDate, Loading, PageHead, Pagination, Panel, Pill, useAsync, useToast } from "../components/ui";
import { navigate } from "../router";

export default function ChequeWorkspace() {
  const { can } = useAuth();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const list = useAsync(
    () => apiPage<ChequeSummary[]>(`/api/cheques?page=${page}&per_page=15&q=${encodeURIComponent(search)}&status=${status}`),
    [page, search, status],
  );

  const upload = async (files: File[]) => {
    const fd = new FormData();
    fd.append("file", files[0]);
    setBusy(files[0].name);
    try {
      const c = await post<Cheque>("/api/cheques", fd);
      toast("success", `Cheque #${c.id} processed in ${(c.processing_ms / 1000).toFixed(1)} s`);
      if (c.duplicate_of) toast("info", `The same image was already uploaded as cheque #${c.duplicate_of}.`);
      navigate(`/cheques/${c.id}`);
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHead title="Cheque verification" sub="Upload a cheque to extract its ten fields, check them, and compare the signature." />
      {can("admin", "operator") && (
        <Panel>
          {busy ? (
            <div className="state"><span className="spinner spinner-lg" /><span>Reading {busy} – OCR, MICR and signature detection…</span></div>
          ) : (
            <FileDrop label="Drop a cheque image here, or click to choose a file" onFiles={upload} />
          )}
        </Panel>
      )}
      <Panel
        title="Processed cheques"
        actions={
          <form className="filters" onSubmit={(e) => { e.preventDefault(); setPage(1); setSearch(q); }}>
            <input type="search" placeholder="Search file, payee, number…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search cheques" />
            <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }} aria-label="Filter by status">
              <option value="">All statuses</option>
              <option value="needs_review">Needs review</option>
              <option value="processed">Processed</option>
              <option value="reviewed">Reviewed</option>
            </select>
          </form>
        }
      >
        {list.loading && !list.data ? <Loading /> : list.error ? <ErrorState message={list.error} onRetry={list.reload} /> :
          list.data && list.data.data.length === 0 ? (
            <Empty title={search || status ? "No cheques match these filters" : "No cheques yet"}>
              {search || status ? "Clear the search or status filter." : "Upload a cheque image above to get started."}
            </Empty>
          ) : list.data && (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>#</th><th>File</th><th>Bank</th><th>Cheque no.</th><th>Payee</th><th className="r">Amount</th><th>Account</th><th>Status</th><th>Uploaded</th></tr>
                  </thead>
                  <tbody>
                    {list.data.data.map((c) => (
                      <tr key={c.id} className="clickable" onClick={() => navigate(`/cheques/${c.id}`)}>
                        <td className="num">{c.id}</td>
                        <td><a href={`#/cheques/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.original_filename}</a></td>
                        <td>{c.bank_name || "—"}</td>
                        <td className="num">{c.cheque_number || "—"}</td>
                        <td>{c.payee_name || "—"}</td>
                        <td className="num r">{c.amount_figures ? `₹ ${c.amount_figures}` : "—"}</td>
                        <td className="num">{c.account_number_masked || "—"}</td>
                        <td><Pill value={c.status} />{c.review_count > 0 && c.status !== "reviewed" && <span className="tiny muted"> {c.review_count} fields</span>}</td>
                        <td className="nowrap">{fmtDate(c.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} perPage={15} total={list.data.meta.total} onPage={setPage} />
            </>
          )}
      </Panel>
    </>
  );
}
