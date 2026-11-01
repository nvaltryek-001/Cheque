import { useState } from "react";
import { api, apiPage, del, Verification } from "../api";
import { useAuth } from "../auth";
import { Button, Confirm, Empty, ErrorState, fmtDate, Loading, Modal, PageHead, Pagination, Panel, Pill, useAsync, useToast } from "../components/ui";
import { useRoute } from "../router";
import { VerificationResult } from "./SignatureVerify";

function Detail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { data, error, loading, setData } = useAsync(() => api<Verification>(`/api/verifications/${id}`), [id]);
  return (
    <Modal title={`Signature check #${id}`} onClose={onClose}>
      {loading && !data ? <Loading /> : error ? <ErrorState message={error} /> : data && (
        <VerificationResult v={data} onChange={(v) => { setData({ ...data, ...v }); onChanged(); }} />
      )}
    </Modal>
  );
}

export default function History() {
  const { can } = useAuth();
  const toast = useToast();
  const route = useRoute();
  const [page, setPage] = useState(1);
  const [decision, setDecision] = useState("");
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState<number | null>(route.query.get("open") ? Number(route.query.get("open")) : null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const list = useAsync(() => apiPage<Verification[]>(`/api/verifications?page=${page}&per_page=20&decision=${decision}${pending ? "&reviewed=0" : ""}`), [page, decision, pending]);

  return (
    <>
      <PageHead title="Verification history" sub="Every signature comparison, its automated decision and any manual review." />
      <Panel
        title="Signature checks"
        actions={
          <div className="filters">
            <select value={decision} onChange={(e) => { setPage(1); setDecision(e.target.value); }} aria-label="Decision">
              <option value="">All decisions</option><option value="MATCH">Match</option><option value="REVIEW">Review</option><option value="MISMATCH">Mismatch</option>
            </select>
            <label className="check-label"><input type="checkbox" checked={pending} onChange={(e) => { setPage(1); setPending(e.target.checked); }} /> Not yet reviewed</label>
          </div>
        }
      >
        {list.loading && !list.data ? <Loading /> : list.error ? <ErrorState message={list.error} onRetry={list.reload} /> :
          list.data && list.data.data.length === 0 ? <Empty title="No signature checks match">Run a comparison from Signature verification.</Empty> : list.data && (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>#</th><th>When</th><th>Signer</th><th>Cheque</th><th>Decision</th><th className="r">Score</th><th>Manual review</th><th>Run by</th><th /></tr></thead>
                  <tbody>
                    {list.data.data.map((v) => (
                      <tr key={v.id} className="clickable" onClick={() => setOpen(v.id)}>
                        <td className="num">{v.id}</td>
                        <td className="nowrap">{fmtDate(v.created_at)}</td>
                        <td>{v.signer_code ?? <span className="muted">deleted</span>}</td>
                        <td>{v.cheque_id ? <a href={`#/cheques/${v.cheque_id}`} onClick={(e) => e.stopPropagation()}>#{v.cheque_id}</a> : <span className="muted">upload</span>}</td>
                        <td><Pill value={v.decision} /></td>
                        <td className="num r">{v.score.toFixed(3)}</td>
                        <td>{v.review_decision ? <Pill value={v.review_decision} /> : <span className="muted">—</span>}</td>
                        <td>{v.created_by_name ?? "—"}</td>
                        <td onClick={(e) => e.stopPropagation()}>{can("admin") && <Button small kind="ghost" icon="trash" title="Delete record" onClick={() => setDeleting(v.id)} />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} perPage={20} total={list.data.meta.total} onPage={setPage} />
            </>
          )}
      </Panel>
      {open && <Detail id={open} onClose={() => setOpen(null)} onChanged={list.reload} />}
      {deleting && (
        <Confirm text={`Delete signature check #${deleting}? The audit log keeps a record that it existed.`} confirmLabel="Delete record" onClose={() => setDeleting(null)}
          onConfirm={async () => { await del(`/api/verifications/${deleting}`); toast("success", "Record deleted"); list.reload(); }} />
      )}
    </>
  );
}
