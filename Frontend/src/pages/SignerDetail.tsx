import { useState } from "react";
import { api, AuditEntry, del, patch, post, put, Signer, Specimen } from "../api";
import { useAuth } from "../auth";
import { AuthImage, Button, Confirm, Empty, ErrorState, errText, FileDrop, fmtDate, Loading, Modal, PageHead, Panel, Pill, useAsync, useToast } from "../components/ui";
import { navigate } from "../router";
import { SignerForm } from "./Enrollment";

type Detail = Signer & { specimens: Specimen[]; history: AuditEntry[] };
type Preview = { image: string; quality: { score: number; acceptable: boolean; issues: { message: string }[] } };

function SpecimenUpload({ title, onUpload, onClose }: { title: string; onUpload: (f: File, allowPoor: boolean) => Promise<void>; onClose: () => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [allowPoor, setAllowPoor] = useState(false);
  const [busy, setBusy] = useState(false);
  const choose = async (f: File) => {
    setFile(f);
    setPreview(null);
    const fd = new FormData();
    fd.append("file", f);
    try { setPreview(await post<Preview>("/api/signature/preprocess", fd)); } catch (e) { toast("error", errText(e)); }
  };
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" icon="upload" busy={busy} disabled={!file || !preview} onClick={async () => {
        if (!file) return;
        setBusy(true);
        try { await onUpload(file, allowPoor); onClose(); } catch (e) { toast("error", errText(e)); } finally { setBusy(false); }
      }}>Enroll specimen</Button>
    </>}>
      <FileDrop label={file ? `${file.name} – click to change` : "Drop a specimen signature, or click to choose"} onFiles={(f) => choose(f[0])} />
      {preview && (
        <div className="preview">
          <img src={preview.image} alt="Preprocessed specimen" className="sig-img" />
          <div>
            Quality <b className="num">{preview.quality.score}/100</b> <Pill value={preview.quality.acceptable ? "ok" : "review"} label={preview.quality.acceptable ? "acceptable" : "below standard"} />
          </div>
          {preview.quality.issues.map((i) => <span key={i.message} className="tiny warn-text">{i.message}</span>)}
          {!preview.quality.acceptable && (
            <label className="check-label"><input type="checkbox" checked={allowPoor} onChange={(e) => setAllowPoor(e.target.checked)} /> Enroll anyway (recorded in the audit log)</label>
          )}
        </div>
      )}
    </Modal>
  );
}

export default function SignerDetail({ id }: { id: number }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data: s, error, loading, reload } = useAsync(() => api<Detail>(`/api/signers/${id}`), [id]);
  const [editing, setEditing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [replacing, setReplacing] = useState<Specimen | null>(null);
  const [confirm, setConfirm] = useState<{ text: string; label: string; run: () => Promise<void> } | null>(null);
  const admin = can("admin");
  const viewer = can("admin", "operator");

  if (loading && !s) return <Loading />;
  if (error || !s) return <ErrorState message={error ?? "Not found"} onRetry={reload} />;

  const upload = (path: string, method: "POST" | "PUT") => async (f: File, allowPoor: boolean) => {
    const fd = new FormData();
    fd.append("file", f);
    if (allowPoor) fd.append("allow_poor_quality", "true");
    await (method === "POST" ? post(path, fd) : put(path, fd));
    toast("success", method === "POST" ? "Specimen enrolled" : "Specimen replaced – the old one is kept as inactive");
    reload();
  };

  return (
    <>
      <PageHead
        title={s.signer_code}
        sub={<>{s.full_name || "Name not recorded"} · {s.bank.toUpperCase()} · account {s.account_number || "—"} · <Pill value={s.status} /> <Pill value={s.enrollment_status} /></>}
        actions={<>
          <Button onClick={() => navigate("/enrollment")}>All signers</Button>
          {viewer && s.specimens_active > 0 && <Button icon="signature" onClick={() => navigate(`/verify?signer=${s.id}`)}>Verify against this signer</Button>}
          {admin && <Button icon="edit" onClick={() => setEditing(true)}>Edit</Button>}
          {admin && <Button onClick={async () => {
            try { await patch(`/api/signers/${id}`, { status: s.status === "active" ? "inactive" : "active" }); reload(); toast("success", s.status === "active" ? "Signer deactivated" : "Signer activated"); }
            catch (e) { toast("error", errText(e)); }
          }}>{s.status === "active" ? "Deactivate" : "Activate"}</Button>}
          {admin && <Button kind="danger" icon="trash" onClick={() => setConfirm({ text: `Delete signer ${s.signer_code} and all of their specimens? This cannot be undone.`, label: "Delete signer",
            run: async () => { await del(`/api/signers/${id}`); toast("success", "Signer deleted"); navigate("/enrollment"); } })}>Delete</Button>}
        </>}
      />
      <Panel title="Reference specimens" actions={admin && s.status === "active" && <Button kind="primary" icon="plus" onClick={() => setUploading(true)}>Add specimen</Button>}>
        {s.specimens.length === 0 ? (
          <Empty title="No specimens yet">Verification is disabled for this signer until at least one specimen is enrolled. Several specimens collected on different days make comparisons more reliable.</Empty>
        ) : (
          <div className="specimens">
            {s.specimens.map((sp) => (
              <div key={sp.id} className={`specimen${sp.status === "inactive" ? " inactive" : ""}`}>
                {viewer ? <AuthImage src={`/api/specimens/${sp.id}/image`} alt={`Specimen ${sp.id}`} className="sig-img" /> : <div className="img-missing sig-img">Restricted</div>}
                <div className="specimen-meta">
                  <div><b>#{sp.id}</b> <Pill value={sp.status} /> {sp.replaced_by && <span className="tiny muted">replaced by #{sp.replaced_by}</span>}</div>
                  <div className="tiny muted">{sp.original_filename} · {fmtDate(sp.created_at)}</div>
                  {sp.quality && <div className="tiny">Quality {sp.quality.score}/100 · ink {(sp.quality.ink_fraction * 100).toFixed(1)}%</div>}
                </div>
                {admin && (
                  <div className="row-actions">
                    {sp.status === "active" && <Button small onClick={() => setReplacing(sp)}>Replace</Button>}
                    <Button small onClick={async () => {
                      try { await patch(`/api/specimens/${sp.id}`, { status: sp.status === "active" ? "inactive" : "active" }); reload(); }
                      catch (e) { toast("error", errText(e)); }
                    }}>{sp.status === "active" ? "Deactivate" : "Activate"}</Button>
                    <Button small kind="danger" icon="trash" onClick={() => setConfirm({ text: `Permanently delete specimen #${sp.id}?`, label: "Delete specimen",
                      run: async () => { await del(`/api/specimens/${sp.id}`); toast("success", "Specimen deleted"); reload(); } })} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Enrollment history">
        {s.history.length === 0 ? <Empty title="No recorded activity" /> : (
          <ul className="timeline">
            {s.history.map((h) => (
              <li key={h.id}><span className="num tiny muted">{fmtDate(h.created_at)}</span> <b>{h.action.replace(".", " ")}</b> {h.entity === "specimen" ? `#${h.entity_id}` : ""} <span className="muted">by {h.username ?? "system"}</span></li>
            ))}
          </ul>
        )}
      </Panel>
      {editing && (
        <Modal title={`Edit ${s.signer_code}`} onClose={() => setEditing(false)}>
          <SignerForm initial={s} submitLabel="Save changes" onSubmit={async (v) => { await patch(`/api/signers/${id}`, v); toast("success", "Signer updated"); setEditing(false); reload(); }} />
        </Modal>
      )}
      {uploading && <SpecimenUpload title={`New specimen for ${s.signer_code}`} onClose={() => setUploading(false)} onUpload={upload(`/api/signers/${id}/specimens`, "POST")} />}
      {replacing && <SpecimenUpload title={`Replace specimen #${replacing.id}`} onClose={() => setReplacing(null)} onUpload={upload(`/api/specimens/${replacing.id}`, "PUT")} />}
      {confirm && <Confirm text={confirm.text} confirmLabel={confirm.label} onConfirm={confirm.run} onClose={() => setConfirm(null)} />}
    </>
  );
}
