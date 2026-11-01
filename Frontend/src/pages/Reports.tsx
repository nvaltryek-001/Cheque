import { api, download } from "../api";
import { useAuth } from "../auth";
import { Button, Empty, ErrorState, errText, Loading, PageHead, Panel, useAsync, useToast } from "../components/ui";

const EXPORTS = [
  { kind: "cheques", title: "Cheque register", text: "All processed cheques with final field values. Account numbers are masked." },
  { kind: "verifications", title: "Signature verification log", text: "Every comparison with decision, score, model version and manual review." },
  { kind: "signers", title: "Enrolled signers", text: "Signer IDs, banks, masked account numbers and active specimen counts." },
  { kind: "audit-log", title: "Audit trail", text: "Sensitive actions with user, time and IP. Admin and auditor only.", roles: ["admin", "auditor"] as const },
];

export default function Reports() {
  const { can } = useAuth();
  const toast = useToast();
  const files = useAsync(() => api<{ name: string; size: number; modified: number }[]>("/api/reports/files"));
  const get = (path: string, name: string) => download(path, name).catch((e) => toast("error", errText(e)));
  return (
    <>
      <PageHead title="Reports" sub="Exports are generated from live records. Individual cheque PDFs are on each cheque's page." />
      <Panel title="Data exports (CSV)">
        <ul className="exports">
          {EXPORTS.filter((e) => !e.roles || can(...e.roles)).map((e) => (
            <li key={e.kind}>
              <div><b>{e.title}</b><p className="muted tiny">{e.text}</p></div>
              <Button icon="download" onClick={() => get(`/api/reports/${e.kind}.csv`, `${e.kind}.csv`)}>Download</Button>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Audit and evaluation reports" actions={<Button small icon="refresh" onClick={files.reload}>Refresh</Button>}>
        {files.loading && !files.data ? <Loading /> : files.error ? <ErrorState message={files.error} /> : files.data && files.data.length === 0 ? (
          <Empty title="No report files yet">Run a dataset audit or a training job to generate reports.</Empty>
        ) : (
          <ul className="exports">
            {files.data?.map((f) => (
              <li key={f.name}>
                <div><b>{f.name}</b><p className="muted tiny">{(f.size / 1024).toFixed(1)} KB · updated {new Date(f.modified * 1000).toLocaleString()}</p></div>
                <Button icon="download" onClick={() => get(`/api/reports/files/${encodeURIComponent(f.name)}`, f.name)}>Download</Button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
