import { FormEvent, useEffect, useState } from "react";
import { api, apiPage, AuditEntry, del, patch, post, put, Role, setToken, User } from "../api";
import { useAuth } from "../auth";
import { Button, Confirm, ErrorState, errText, fmtDate, Loading, Modal, PageHead, Pagination, Panel, Pill, useAsync, useToast } from "../components/ui";

type S = Record<string, number | boolean | null>;

function ThresholdSettings() {
  const { can } = useAuth();
  const toast = useToast();
  const s = useAsync(() => api<S>("/api/settings"));
  const [v, setV] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (s.data) setV(Object.fromEntries(Object.entries(s.data).map(([k, x]) => [k, x == null ? "" : String(x)])));
  }, [s.data]);
  if (s.loading && !s.data) return <Loading />;
  if (s.error) return <ErrorState message={s.error} />;
  const admin = can("admin");
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body: S = {
        "signature.match_threshold": v["signature.match_threshold"] === "" ? null : Number(v["signature.match_threshold"]),
        "signature.mismatch_threshold": v["signature.mismatch_threshold"] === "" ? null : Number(v["signature.mismatch_threshold"]),
        "signature.max_specimens_per_signer": Number(v["signature.max_specimens_per_signer"]),
        "signature.duplicate_hamming": Number(v["signature.duplicate_hamming"]),
        "enrollment.min_specimens": Number(v["enrollment.min_specimens"]),
        "enrollment.min_sessions": Number(v["enrollment.min_sessions"]),
        "enrollment.cap_match_when_not_ready": v["enrollment.cap_match_when_not_ready"] === "true",
        "ocr.auto_signature_crop": v["ocr.auto_signature_crop"] === "true",
      };
      s.setData(await put<S>("/api/settings", body));
      toast("success", "Settings saved");
    } catch (x) {
      toast("error", errText(x));
    } finally {
      setBusy(false);
    }
  };
  const set = (k: string) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  return (
    <form className="form" onSubmit={save}>
      <label>Match threshold override
        <input type="number" step="0.001" min="0" max="1" placeholder="use validated value" value={v["signature.match_threshold"] ?? ""} onChange={set("signature.match_threshold")} disabled={!admin} />
        <span className="tiny muted">Leave empty to use the threshold chosen on the validation split.</span>
      </label>
      <label>Mismatch threshold override
        <input type="number" step="0.001" min="0" max="1" placeholder="use validated value" value={v["signature.mismatch_threshold"] ?? ""} onChange={set("signature.mismatch_threshold")} disabled={!admin} />
      </label>
      <label>Independent specimens needed for a MATCH
        <input type="number" min="1" max="50" value={v["enrollment.min_specimens"] ?? ""} onChange={set("enrollment.min_specimens")} disabled={!admin} />
        <span className="tiny muted">Specimens from recorded signing sessions; several from one document count once.</span>
      </label>
      <label>Different signing days needed for a MATCH
        <input type="number" min="1" max="50" value={v["enrollment.min_sessions"] ?? ""} onChange={set("enrollment.min_sessions")} disabled={!admin} />
      </label>
      <label>Cap MATCH at REVIEW when the reference set is not ready
        <select value={v["enrollment.cap_match_when_not_ready"] ?? "true"} onChange={set("enrollment.cap_match_when_not_ready")} disabled={!admin}><option value="true">Yes (recommended)</option><option value="false">No</option></select>
        <span className="tiny muted">Switching this off lets a single specimen produce a MATCH.</span>
      </label>
      <label>Maximum active specimens per signer
        <input type="number" min="1" max="50" value={v["signature.max_specimens_per_signer"] ?? ""} onChange={set("signature.max_specimens_per_signer")} disabled={!admin} />
      </label>
      <label>Duplicate specimen sensitivity (hash distance)
        <input type="number" min="0" max="64" value={v["signature.duplicate_hamming"] ?? ""} onChange={set("signature.duplicate_hamming")} disabled={!admin} />
      </label>
      <label>Detect signature automatically on upload
        <select value={v["ocr.auto_signature_crop"] ?? "true"} onChange={set("ocr.auto_signature_crop")} disabled={!admin}><option value="true">Yes</option><option value="false">No</option></select>
      </label>
      {admin && <div className="row-actions span-2"><Button kind="primary" type="submit" busy={busy}>Save settings</Button></div>}
    </form>
  );
}

function Users() {
  const { user } = useAuth();
  const toast = useToast();
  const list = useAsync(() => api<User[]>("/api/users"));
  const [adding, setAdding] = useState(false);
  const [resetting, setResetting] = useState<User | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);
  const [form, setForm] = useState({ username: "", full_name: "", password: "", role: "operator" as Role });
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  if (list.loading && !list.data) return <Loading />;
  if (list.error) return <ErrorState message={list.error} />;
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {list.data?.map((u) => (
              <tr key={u.id}>
                <td><b>{u.username}</b>{u.id === user?.id && <span className="tiny muted"> (you)</span>}</td>
                <td>{u.full_name || "—"}</td>
                <td>
                  <select value={u.role} disabled={u.id === user?.id} aria-label={`Role of ${u.username}`} onChange={async (e) => {
                    try { await patch(`/api/users/${u.id}`, { role: e.target.value }); list.reload(); toast("success", "Role updated"); } catch (x) { toast("error", errText(x)); }
                  }}>
                    <option value="admin">admin</option><option value="operator">operator</option><option value="auditor">auditor</option>
                  </select>
                </td>
                <td><Pill value={u.is_active ? "active" : "inactive"} /></td>
                <td className="nowrap">{fmtDate(u.last_login_at)}</td>
                <td className="row-actions">
                  <Button small onClick={() => { setPw(""); setResetting(u); }}>Reset password</Button>
                  {u.id !== user?.id && <Button small onClick={async () => {
                    try { await patch(`/api/users/${u.id}`, { is_active: !u.is_active }); list.reload(); } catch (x) { toast("error", errText(x)); }
                  }}>{u.is_active ? "Disable" : "Enable"}</Button>}
                  {u.id !== user?.id && <Button small kind="danger" icon="trash" title="Delete user" onClick={() => setDeleting(u)} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row-actions"><Button kind="primary" icon="plus" onClick={() => setAdding(true)}>Add user</Button></div>
      {adding && (
        <Modal title="Add user" onClose={() => setAdding(false)}>
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try { await post("/api/users", form); toast("success", `User ${form.username} created`); setAdding(false); setForm({ username: "", full_name: "", password: "", role: "operator" }); list.reload(); }
            catch (x) { toast("error", errText(x)); } finally { setBusy(false); }
          }}>
            <label>Username<input required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
            <label>Full name<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
            <label>Role<select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}><option value="operator">operator – process cheques and verify</option><option value="auditor">auditor – read only</option><option value="admin">admin – everything incl. enrollment</option></select></label>
            <label>Initial password<input type="password" required minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /><span className="tiny muted">At least 8 characters, mixing letters with numbers or symbols.</span></label>
            <div className="row-actions span-2"><Button kind="primary" type="submit" busy={busy}>Create user</Button></div>
          </form>
        </Modal>
      )}
      {resetting && (
        <Modal title={`Reset password for ${resetting.username}`} onClose={() => setResetting(null)} footer={<>
          <Button onClick={() => setResetting(null)}>Cancel</Button>
          <Button kind="primary" busy={busy} disabled={pw.length < 8} onClick={async () => {
            setBusy(true);
            try { await patch(`/api/users/${resetting.id}`, { password: pw }); toast("success", "Password reset – existing sessions were signed out"); setResetting(null); }
            catch (x) { toast("error", errText(x)); } finally { setBusy(false); }
          }}>Reset password</Button>
        </>}>
          <label className="block">New password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus /></label>
        </Modal>
      )}
      {deleting && <Confirm text={`Delete user ${deleting.username}? Their past actions stay in the audit log.`} confirmLabel="Delete user" onClose={() => setDeleting(null)}
        onConfirm={async () => { await del(`/api/users/${deleting.id}`); toast("success", "User deleted"); list.reload(); }} />}
    </>
  );
}

function ChangePassword() {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form className="form" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try {
        const r = await post<{ token: string }>("/api/auth/change-password", { current_password: cur, new_password: next });
        setToken(r.token);
        setCur(""); setNext("");
        toast("success", "Password changed – other sessions were signed out");
      } catch (x) { toast("error", errText(x)); } finally { setBusy(false); }
    }}>
      <label>Current password<input type="password" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></label>
      <label>New password<input type="password" autoComplete="new-password" required minLength={8} value={next} onChange={(e) => setNext(e.target.value)} /></label>
      <div className="row-actions span-2"><Button kind="primary" type="submit" busy={busy}>Change password</Button></div>
    </form>
  );
}

function AuditLog() {
  const [page, setPage] = useState(1);
  const list = useAsync(() => apiPage<AuditEntry[]>(`/api/audit-log?page=${page}&per_page=25`), [page]);
  if (list.loading && !list.data) return <Loading />;
  if (list.error) return <ErrorState message={list.error} />;
  return (
    <>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Record</th><th>Detail</th></tr></thead>
          <tbody>
            {list.data?.data.map((a) => (
              <tr key={a.id}><td className="nowrap">{fmtDate(a.created_at)}</td><td>{a.username ?? "system"}</td><td>{a.action}</td><td>{a.entity ? `${a.entity} ${a.entity_id ?? ""}` : "—"}</td><td className="tiny muted clip">{a.detail}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.data && <Pagination page={page} perPage={25} total={list.data.meta.total} onPage={setPage} />}
    </>
  );
}

export default function Settings() {
  const { can } = useAuth();
  const status = useAsync(() => api<{ version: string; datasets: { ocr: boolean; signature: boolean } }>("/api/system/status"));
  return (
    <>
      <PageHead title="Settings" sub={status.data ? `ChequeVerify AI ${status.data.version} · OCR dataset ${status.data.datasets.ocr ? "found" : "missing"} · signature dataset ${status.data.datasets.signature ? "found" : "missing"}` : undefined} />
      <Panel title="Verification settings"><ThresholdSettings /></Panel>
      <Panel title="Your password"><ChangePassword /></Panel>
      {can("admin") && <Panel title="Users and roles"><Users /></Panel>}
      {can("admin", "auditor") && <Panel title="Audit log"><AuditLog /></Panel>}
    </>
  );
}
