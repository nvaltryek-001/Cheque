import { FormEvent, useState } from "react";
import { useAuth } from "./auth";
import { Icon } from "./components/Icon";
import { Button, errText } from "./components/ui";
import { navigate, useRoute } from "./router";
import Dashboard from "./pages/Dashboard";
import ChequeWorkspace from "./pages/ChequeWorkspace";
import ChequeDetail from "./pages/ChequeDetail";
import SignatureVerify from "./pages/SignatureVerify";
import Enrollment from "./pages/Enrollment";
import SignerDetail from "./pages/SignerDetail";
import Batch from "./pages/Batch";
import { OcrDataset, SignatureDataset } from "./pages/Datasets";
import Training from "./pages/Training";
import Analytics from "./pages/Analytics";
import History from "./pages/History";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";

const NAV: { group: string; items: { to: string; label: string; icon: string }[] }[] = [
  { group: "Work", items: [
    { to: "/", label: "Dashboard", icon: "dashboard" },
    { to: "/cheques", label: "Cheque verification", icon: "cheque" },
    { to: "/verify", label: "Signature verification", icon: "signature" },
    { to: "/batch", label: "Batch processing", icon: "batch" },
    { to: "/history", label: "Verification history", icon: "history" },
  ] },
  { group: "Signers", items: [{ to: "/enrollment", label: "Signature enrollment", icon: "enroll" }] },
  { group: "Data & models", items: [
    { to: "/datasets/ocr", label: "OCR dataset", icon: "dataset" },
    { to: "/datasets/signature", label: "Signature dataset", icon: "dataset" },
    { to: "/training", label: "Model training", icon: "model" },
    { to: "/analytics", label: "Analytics", icon: "analytics" },
    { to: "/reports", label: "Reports", icon: "report" },
  ] },
  { group: "System", items: [{ to: "/settings", label: "Settings", icon: "settings" }] },
];

function Login() {
  const { login } = useAuth();
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="brand brand-dark">
          <span className="brand-mark"><Icon name="cheque" size={20} /></span>
          <span>ChequeVerify<b>AI</b></span>
        </div>
        <h1>Sign in</h1>
        <p className="muted">Cheque and signature records are restricted to authorised staff.</p>
        <label>
          Username
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} required />
        </label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <Button kind="primary" type="submit" busy={busy}>Sign in</Button>
        <p className="tiny muted">First run: the admin password is printed in the backend terminal.</p>
      </form>
    </div>
  );
}

export default function App() {
  const { user, ready, logout } = useAuth();
  const route = useRoute();
  const [menuOpen, setMenuOpen] = useState(false);
  if (!ready) return <div className="boot"><span className="spinner spinner-lg" /></div>;
  if (!user) return <Login />;

  const p = route.parts;
  let page;
  let active = "/" + (p[0] ?? "");
  if (p.length === 0) page = <Dashboard />;
  else if (p[0] === "cheques" && p[1]) page = <ChequeDetail id={Number(p[1])} />;
  else if (p[0] === "cheques") page = <ChequeWorkspace />;
  else if (p[0] === "verify") page = <SignatureVerify chequeId={route.query.get("cheque")} signerId={route.query.get("signer")} />;
  else if (p[0] === "enrollment" && p[1]) page = <SignerDetail id={Number(p[1])} />;
  else if (p[0] === "enrollment") page = <Enrollment />;
  else if (p[0] === "batch") page = <Batch />;
  else if (p[0] === "datasets" && p[1] === "signature") { page = <SignatureDataset />; active = "/datasets/signature"; }
  else if (p[0] === "datasets") { page = <OcrDataset />; active = "/datasets/ocr"; }
  else if (p[0] === "training") page = <Training />;
  else if (p[0] === "analytics") page = <Analytics />;
  else if (p[0] === "history") page = <History />;
  else if (p[0] === "reports") page = <Reports />;
  else if (p[0] === "settings") page = <Settings />;
  else page = <div className="state state-empty"><strong>Page not found</strong><Button onClick={() => navigate("/")}>Go to dashboard</Button></div>;

  return (
    <div className={`shell${menuOpen ? " menu-open" : ""}`}>
      <aside className="side">
        <div className="brand">
          <span className="brand-mark"><Icon name="cheque" size={20} /></span>
          <span>ChequeVerify<b>AI</b></span>
        </div>
        <nav aria-label="Main">
          {NAV.map((g) => (
            <div className="nav-group" key={g.group}>
              <div className="nav-label">{g.group}</div>
              {g.items.map((it) => (
                <a
                  key={it.to}
                  href={`#${it.to}`}
                  className={`nav-item${active === it.to ? " active" : ""}`}
                  aria-current={active === it.to ? "page" : undefined}
                  onClick={() => setMenuOpen(false)}
                >
                  <Icon name={it.icon} />
                  <span>{it.label}</span>
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <div className="who">
            <Icon name="user" />
            <div>
              <div className="who-name">{user.full_name || user.username}</div>
              <div className="who-role">{user.role}</div>
            </div>
          </div>
          <button className="icon-btn light" onClick={logout} title="Sign out" aria-label="Sign out">
            <Icon name="logout" />
          </button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setMenuOpen(false)} />
      <main className="main">
        <button className="menu-btn icon-btn" onClick={() => setMenuOpen(true)} aria-label="Open menu">
          <Icon name="menu" />
        </button>
        {page}
      </main>
    </div>
  );
}
