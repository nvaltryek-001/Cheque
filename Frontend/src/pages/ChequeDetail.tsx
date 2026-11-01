import { MouseEvent as RMouseEvent, useEffect, useRef, useState } from "react";
import { api, Cheque, del, download, patch, post } from "../api";
import { useAuth } from "../auth";
import { AuthImage, Button, Confirm, ErrorState, errText, fmtDate, Loading, PageHead, Panel, Pill, Stamp, useAsync, useToast } from "../components/ui";
import { navigate } from "../router";

type Box = [number, number, number, number];

function CropSelector({ chequeId, initial, onSaved, onCancel }: { chequeId: number; initial?: number[]; onSaved: () => void; onCancel: () => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<Box | null>(initial && initial.length === 4 ? (initial as Box) : null);
  const start = useRef<[number, number] | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const pos = (e: RMouseEvent): [number, number] => {
    const r = wrap.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  return (
    <div>
      <p className="muted tiny">Drag a rectangle around the signature, then save.</p>
      <div
        ref={wrap}
        className="crop-wrap"
        onMouseDown={(e) => { e.preventDefault(); start.current = pos(e); setBox([...pos(e), 0, 0] as Box); }}
        onMouseMove={(e) => {
          if (!start.current) return;
          const [x0, y0] = start.current;
          const [x1, y1] = pos(e);
          setBox([Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)]);
        }}
        onMouseUp={() => (start.current = null)}
        onMouseLeave={() => (start.current = null)}
      >
        <AuthImage src={`/api/cheques/${chequeId}/image`} alt="Cheque" className="cheque-img" />
        {box && <div className="crop-box" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` }} />}
      </div>
      <div className="row-actions">
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          kind="primary"
          icon="crop"
          busy={busy}
          disabled={!box || box[2] < 0.02 || box[3] < 0.02}
          onClick={async () => {
            setBusy(true);
            try {
              await post(`/api/cheques/${chequeId}/signature-crop`, { box });
              toast("success", "Signature region saved");
              onSaved();
            } catch (e) {
              toast("error", errText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Save signature region
        </Button>
      </div>
    </div>
  );
}

export default function ChequeDetail({ id }: { id: number }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data: c, error, loading, reload, setData } = useAsync(() => api<Cheque>(`/api/cheques/${id}`), [id]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [cropping, setCropping] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [sigVersion, setSigVersion] = useState(0);
  const editable = can("admin", "operator");

  useEffect(() => {
    if (c) {
      setEdits({ ...c.final });
      setNotes(c.notes || "");
    }
  }, [c]);

  if (loading && !c) return <Loading />;
  if (error || !c) return <ErrorState message={error ?? "Not found"} onRetry={reload} />;

  const machine = (k: string) => c.fields[k]?.value ?? "";
  const dirty = c.field_order.some((k) => (edits[k] ?? "") !== (c.final[k] ?? "")) || notes !== (c.notes || "");

  const save = async (status?: Cheque["status"]) => {
    const corrections: Record<string, string | null> = {};
    for (const k of c.field_order) {
      const v = (edits[k] ?? "").trim();
      if (v === machine(k)) { if (c.corrected && k in c.corrected) corrections[k] = null; }
      else if (v !== (c.final[k] ?? "")) corrections[k] = v;
    }
    setBusy(status ?? "save");
    try {
      const out = await patch<Cheque>(`/api/cheques/${id}`, { corrections, notes, ...(status ? { status } : {}) });
      setData({ ...c, ...out });
      toast("success", status === "reviewed" ? "Marked as reviewed" : "Changes saved");
    } catch (e) {
      toast("error", errText(e));
    } finally {
      setBusy(null);
    }
  };

  const verifyHref = `/verify?cheque=${id}${c.suggested_signer ? `&signer=${c.suggested_signer.id}` : ""}`;

  return (
    <>
      <PageHead
        title={`Cheque #${c.id}`}
        sub={<>{c.original_filename} · uploaded {fmtDate(c.created_at)} · {c.ocr_engine} · {(c.processing_ms / 1000).toFixed(1)} s · <Pill value={c.status} /></>}
        actions={
          <>
            <Button icon="download" onClick={() => download(`/api/cheques/${id}/report.pdf`, `cheque-${id}.pdf`).catch((e) => toast("error", errText(e)))}>PDF report</Button>
            {editable && <Button icon="refresh" busy={busy === "reprocess"} onClick={async () => {
              setBusy("reprocess");
              try { setData(await post<Cheque>(`/api/cheques/${id}/reprocess`)); setSigVersion((v) => v + 1); toast("success", "Cheque re-processed"); }
              catch (e) { toast("error", errText(e)); } finally { setBusy(null); }
            }}>Re-run OCR</Button>}
            {editable && <Button kind="danger" icon="trash" onClick={() => setConfirmDelete(true)}>Delete</Button>}
          </>
        }
      />
      <div className="detail-grid">
        <div className="stack">
          <Panel title="Cheque image">
            {cropping ? (
              <CropSelector chequeId={id} initial={c.validation?.signature_detection?.box_fraction} onCancel={() => setCropping(false)}
                onSaved={() => { setCropping(false); setSigVersion((v) => v + 1); reload(); }} />
            ) : (
              <div className="crop-wrap static">
                <AuthImage src={`/api/cheques/${id}/image`} alt="Uploaded cheque" className="cheque-img" />
              </div>
            )}
          </Panel>
          <Panel
            title="Signature"
            actions={editable && !cropping && <Button small icon="crop" onClick={() => setCropping(true)}>Adjust region</Button>}
          >
            <div className="sig-row">
              {c.has_signature_crop ? (
                <AuthImage key={sigVersion} src={`/api/cheques/${id}/signature?v=${sigVersion}`} alt="Detected signature" className="sig-img" />
              ) : (
                <div className="img-missing sig-img">No signature region found – adjust the region manually.</div>
              )}
              <div className="sig-side">
                {c.suggested_signer ? (
                  <p>Account number matches enrolled signer <b>{c.suggested_signer.signer_code}</b>{c.suggested_signer.full_name ? ` (${c.suggested_signer.full_name})` : ""}.</p>
                ) : (
                  <p className="muted">No enrolled signer matches this account number. You can still pick a signer manually.</p>
                )}
                {editable && <Button kind="primary" icon="signature" disabled={!c.has_signature_crop} onClick={() => navigate(verifyHref)}>Verify signature</Button>}
              </div>
            </div>
            {c.verifications && c.verifications.length > 0 && (
              <div className="mini-history">
                {c.verifications.map((v) => (
                  <a key={v.id} href={`#/history?open=${v.id}`} className="mini-stamp">
                    <Stamp decision={v.decision} />
                    <span className="tiny">{v.signer_code} · {v.score.toFixed(3)}{v.review_decision ? ` · ${v.review_decision.toLowerCase()}` : ""}</span>
                  </a>
                ))}
              </div>
            )}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Extracted fields" actions={<span className="tiny muted">Machine values are kept; edits are stored as corrections.</span>}>
            <div className="fields">
              {c.field_order.map((k) => {
                const f = c.fields[k];
                const corrected = c.corrected && k in c.corrected;
                return (
                  <div className={`field field-${corrected ? "corrected" : f?.status ?? "missing"}`} key={k}>
                    <label htmlFor={`f-${k}`}>
                      {c.field_labels[k]}
                      <span className="field-meta">
                        {corrected ? <Pill value="reviewed" label="corrected" /> : <Pill value={f?.status ?? "missing"} />}
                        {!corrected && f?.value && <span className="tiny muted">{f.source} · {(f.confidence * 100).toFixed(0)}%</span>}
                      </span>
                    </label>
                    <input id={`f-${k}`} value={edits[k] ?? ""} disabled={!editable} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })}
                      placeholder={f?.status === "missing" ? "Enter manually" : ""} />
                    {corrected && <div className="tiny muted">Machine read: {machine(k) || "nothing"}</div>}
                    {!corrected && f?.issues?.map((i) => <div className="tiny warn-text" key={i}>{i}</div>)}
                  </div>
                );
              })}
            </div>
            <label className="notes">
              Reviewer notes
              <textarea rows={2} value={notes} disabled={!editable} onChange={(e) => setNotes(e.target.value)} />
            </label>
            {editable && (
              <div className="row-actions">
                <Button disabled={!dirty} busy={busy === "save"} onClick={() => save()}>Save changes</Button>
                <Button kind="primary" icon="check" busy={busy === "reviewed"} disabled={c.status === "reviewed" && !dirty} onClick={() => save("reviewed")}>
                  {c.status === "reviewed" ? "Save as reviewed" : "Mark as reviewed"}
                </Button>
              </div>
            )}
          </Panel>
          <Panel title="Validation">
            <ul className="checks">
              {(c.validation?.checks ?? []).map((ch) => (
                <li key={ch.check} className={`check check-${ch.status}`}>
                  <Pill value={ch.status} />
                  <span>{ch.message}</span>
                </li>
              ))}
            </ul>
            {c.quality && (
              <div className="quality">
                <span>Image quality <b className="num">{c.quality.score}/100</b></span>
                <span className="tiny muted">sharpness {c.quality.sharpness} · brightness {c.quality.brightness} · contrast {c.quality.contrast}</span>
                {c.quality.issues.map((i) => <div className="tiny warn-text" key={i.code}>{i.message}</div>)}
              </div>
            )}
          </Panel>
        </div>
      </div>
      {confirmDelete && (
        <Confirm text={`Delete cheque #${id} and its stored images? Signature verification records are kept for audit.`} confirmLabel="Delete cheque"
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => { await del(`/api/cheques/${id}`); toast("success", `Cheque #${id} deleted`); navigate("/cheques"); }} />
      )}
    </>
  );
}
