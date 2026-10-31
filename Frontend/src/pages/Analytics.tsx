import { api } from "../api";
import { Bars, Empty, ErrorState, Loading, PageHead, Panel, Stat, useAsync } from "../components/ui";

interface A {
  cheques: number;
  field_status: Record<string, { ok: number; review: number; missing: number; corrected: number }>;
  by_bank: Record<string, number>;
  per_day: Record<string, number>;
  manual_corrections: number;
  decisions: Record<string, number>;
  score_histogram: number[];
  reviewer_agreement: { agree: number; disagree: number };
}

const LABELS: Record<string, string> = {
  bank_name: "Bank name", branch_name: "Branch", ifsc_code: "IFSC", cheque_number: "Cheque number", micr_code: "MICR",
  account_number: "Account", date: "Date", payee_name: "Payee", amount_figures: "Amount (figures)", amount_words: "Amount (words)",
};

export default function Analytics() {
  const { data, error, loading, reload } = useAsync(() => api<A>("/api/analytics"));
  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorState message={error ?? "No data"} onRetry={reload} />;
  const agreeTotal = data.reviewer_agreement.agree + data.reviewer_agreement.disagree;
  return (
    <>
      <PageHead title="Analytics" sub="Computed from the records in this installation. Field status is the extractor's own confidence, not measured accuracy." />
      {data.cheques === 0 ? (
        <Empty title="Nothing to analyse yet">Process some cheques and run signature checks – charts appear here automatically.</Empty>
      ) : (
        <>
          <div className="stats">
            <Stat label="Cheques" value={data.cheques} />
            <Stat label="Manual field corrections" value={data.manual_corrections} hint="each one is a labelled example for future evaluation" />
            <Stat label="Signature decisions" value={Object.values(data.decisions).reduce((a, b) => a + b, 0)} />
            <Stat label="Reviewer agreed with system" value={agreeTotal ? `${Math.round((100 * data.reviewer_agreement.agree) / agreeTotal)}%` : "—"} hint={agreeTotal ? `${agreeTotal} reviewed MATCH/MISMATCH results` : "no reviewed decisions yet"} />
          </div>
          <Panel title="Field extraction status">
            <div className="table-wrap">
              <table className="table compact">
                <thead><tr><th>Field</th><th className="r">Confident</th><th className="r">Needs review</th><th className="r">Missing</th><th className="r">Corrected by staff</th><th>Confident share</th></tr></thead>
                <tbody>
                  {Object.entries(data.field_status).map(([k, s]) => {
                    const total = s.ok + s.review + s.missing || 1;
                    return (
                      <tr key={k}>
                        <td>{LABELS[k] ?? k}</td><td className="num r">{s.ok}</td><td className="num r">{s.review}</td><td className="num r">{s.missing}</td><td className="num r">{s.corrected}</td>
                        <td><span className="stackbar"><span className="good" style={{ width: `${(100 * s.ok) / total}%` }} /><span className="warn" style={{ width: `${(100 * s.review) / total}%` }} /><span className="bad" style={{ width: `${(100 * s.missing) / total}%` }} /></span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
          <div className="grid-2">
            <Panel title="Cheques by bank"><Bars rows={Object.entries(data.by_bank).map(([label, value]) => ({ label, value }))} /></Panel>
            <Panel title="Signature decisions"><Bars rows={["MATCH", "REVIEW", "MISMATCH"].map((k) => ({ label: k, value: data.decisions[k] ?? 0 }))} /></Panel>
          </div>
          <div className="grid-2">
            <Panel title="Similarity score distribution">
              <Bars rows={data.score_histogram.map((v, i) => ({ label: `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`, value: v }))} tone="info" />
            </Panel>
            <Panel title="Cheques per day (last 30 active days)">
              <Bars rows={Object.entries(data.per_day).map(([label, value]) => ({ label, value }))} tone="info" />
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
