import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, CodeRequested } from "../api";
import { useAuth } from "../auth";
import { Brand, Icon } from "../components/Icon";
import { Button, errText, useToast } from "../components/ui";
import { displayName } from "../identity";

type View = "email" | "code" | "admin";

/** The cheque in the hero is a drawing, not a real document: bars stand in for text, and the boxes only name the regions the product reads. */
function ChequeArt() {
  return (
    <div className="cheque-art" aria-hidden="true">
      <div className="ca-sheet">
        <i className="ca-seal" />
        <i className="ca-bar ca-bank" />
        <i className="ca-bar ca-date" />
        <i className="ca-bar ca-payee" />
        <i className="ca-bar ca-words" />
        <i className="ca-bar ca-words2" />
        <i className="ca-bar ca-amount" />
        <span className="ca-micr">
          <i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i />
        </span>
        <svg className="ca-sign" viewBox="0 0 120 40" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path pathLength="1" d="M4 28c10-2 14-20 22-20s3 18 10 18 6-10 14-10 6 8 14 8 8-6 16-8 6 4 12 2" />
        </svg>
        <span className="ca-det ca-det-payee"><em>Payee</em></span>
        <span className="ca-det ca-det-amount"><em>Amount</em></span>
        <span className="ca-det ca-det-micr"><em>MICR</em></span>
        <span className="ca-det ca-det-sign"><em>Signature</em></span>
        <span className="ca-scan" />
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section className="auth-hero">
      <Brand tone="dark" />
      <div className="hero-copy">
        <h2 className="hero-title">
          <span>Read the cheque.</span>
          <span>Compare the signature.</span>
          <span>Keep the evidence.</span>
        </h2>
        <p className="hero-sub">
          ChequeVerify AI extracts the ten cheque fields, compares each signature with the signer's enrolled specimens
          and records every decision for audit.
        </p>
      </div>
      <ChequeArt />
      <ul className="hero-facts">
        <li><Icon name="scan" size={18} /><span>Fields read by OCR and a MICR reader</span></li>
        <li><Icon name="signature" size={18} /><span>Signatures compared with enrolled specimens</span></li>
        <li><Icon name="shieldcheck" size={18} /><span>Every action recorded in an audit log</span></li>
      </ul>
    </section>
  );
}

/** Seconds left until `resend` may be used again. */
function useCountdown(): [number, (seconds: number) => void] {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (left <= 0) return;
    const t = window.setTimeout(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => window.clearTimeout(t);
  }, [left]);
  return [left, setLeft];
}

function codeError(err: unknown): string {
  if (err instanceof ApiError && err.code === "invalid_code") {
    const left = (err.details as { attempts_left?: number } | undefined)?.attempts_left;
    if (left === 0) return `${err.message} This code can no longer be used, so request a new one.`;
    if (typeof left === "number") return `${err.message} ${left} ${left === 1 ? "try" : "tries"} left.`;
  }
  return errText(err);
}

export default function Login() {
  const { config, login, requestCode, verifyCode } = useAuth();
  const emailOn = config.email_signin;
  const passwordOn = config.password_signin;
  const domain = config.email_domain ?? "";
  const [view, setView] = useState<View>(emailOn ? "email" : "admin");
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<CodeRequested | null>(null);
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useCountdown();
  const codeInput = useRef<HTMLInputElement>(null);

  const go = (next: View) => {
    setError(null);
    setNotice(null);
    setView(next);
  };

  const sendCode = async (address: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await requestCode(address);
      setSent(r);
      setCode("");
      setWait(r.resend_after);
      setView("code");
    } catch (err) {
      if (err instanceof ApiError && err.code === "resend_too_soon") {
        // a code is already on its way: let the person enter it
        const retry = (err.details as { retry_after?: number } | undefined)?.retry_after ?? config.resend_seconds;
        setSent({ sent: true, email: address, expires_in: config.code_ttl_seconds, resend_after: retry, code_length: config.code_length });
        setWait(retry);
        setNotice("A code was sent a moment ago. Enter it below.");
        setView("code");
      } else {
        setError(errText(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const submitEmail = (e: FormEvent) => {
    e.preventDefault();
    const typed = email.trim();
    if (!typed) {
      setError("Enter your institutional email address.");
      return;
    }
    // typing only the part before the @ is fine; the server still checks the whole address
    void sendCode(typed.includes("@") || !domain ? typed : `${typed}@${domain}`);
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    if (!sent) return;
    if (code.length !== config.code_length) {
      setError(`Enter the ${config.code_length}-digit code from the email.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await verifyCode(sent.email, code);
    } catch (err) {
      setError(codeError(err));
      setCode("");
      codeInput.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  const submitPassword = async (e: FormEvent) => {
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

  const shown: View = view === "email" && !emailOn ? "admin" : view === "admin" && !passwordOn && emailOn ? "email" : view;
  const minutes = Math.max(1, Math.round((sent?.expires_in ?? config.code_ttl_seconds) / 60));
  const formatted = code.length > 4 ? `${code.slice(0, 4)} ${code.slice(4)}` : code;

  return (
    <div className="auth">
      <Hero />
      <section className="auth-main">
        <div className="auth-mobile-brand"><Brand tone="light" /></div>
        <div className="auth-card">
          {config.storage_temporary && (
            <div className="notice notice-warn" role="note">
              <Icon name="info" size={16} />
              <span>Temporary storage: this server has no persistent disk, so accounts, cheques and signatures are erased whenever it restarts or goes to sleep. After a quiet period the first visit can take about a minute.</span>
            </div>
          )}
          {shown === "email" && (
            <form onSubmit={submitEmail} noValidate className="auth-form">
              <span className="chip-icon"><Icon name="mail" size={22} /></span>
              <h1>Sign in</h1>
              <p className="auth-lead">
                Use your institutional email address. We send a one-time code to it, so there is no password to remember.
              </p>
              <label>
                Institutional email
                <span className="input-icon">
                  <Icon name="mail" size={18} />
                  <input
                    type="email" inputMode="email" autoComplete="email" autoCapitalize="none" spellCheck={false} autoFocus
                    placeholder={domain ? `name@${domain}` : "name@institution.edu"}
                    value={email} onChange={(e) => setEmail(e.target.value)} aria-describedby="email-hint"
                  />
                </span>
                <span id="email-hint" className="hint">
                  {domain ? <>Any address ending in <b>@{domain}</b> works. You can type just the part before the @.</> : "Use the email address your institution gave you."}
                </span>
              </label>
              {error && <div className="form-error" role="alert">{error}</div>}
              <Button kind="primary" type="submit" busy={busy} wide icon="arrowright">Send sign-in code</Button>
              {passwordOn && (
                <>
                  <div className="or"><span>or</span></div>
                  <Button wide icon="key" onClick={() => go("admin")}>Administrator sign-in</Button>
                </>
              )}
            </form>
          )}

          {shown === "code" && sent && (
            <form onSubmit={submitCode} noValidate className="auth-form">
              <button type="button" className="link-btn" onClick={() => go("email")}>
                <Icon name="arrowleft" size={16} /> Use a different email
              </button>
              <span className="chip-icon"><Icon name="shieldcheck" size={22} /></span>
              <h1>Check your email</h1>
              <p className="auth-lead">
                We sent a {sent.code_length}-digit code to <b className="break">{sent.email}</b>. It works once and expires in {minutes} minutes.
              </p>
              {notice && <div className="notice" role="status"><Icon name="info" size={16} /><span>{notice}</span></div>}
              <label>
                Sign-in code
                <input
                  ref={codeInput} className="code-input" inputMode="numeric" autoComplete="one-time-code" autoFocus spellCheck={false}
                  placeholder="0000 0000" maxLength={sent.code_length + 1} value={formatted}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, sent.code_length))}
                />
              </label>
              {error && <div className="form-error" role="alert">{error}</div>}
              <Button kind="primary" type="submit" busy={busy} wide icon="check">Verify and sign in</Button>
              <p className="resend">
                Nothing in your inbox? Check the spam folder, or{" "}
                <button type="button" className="link-btn" disabled={busy || wait > 0} onClick={() => void sendCode(sent.email)}>
                  {wait > 0 ? `resend the code in ${wait} s` : "resend the code"}
                </button>
              </p>
            </form>
          )}

          {shown === "admin" && (
            <form onSubmit={submitPassword} className="auth-form">
              {emailOn && (
                <button type="button" className="link-btn" onClick={() => go("email")}>
                  <Icon name="arrowleft" size={16} /> Sign in with email instead
                </button>
              )}
              <span className="chip-icon"><Icon name="key" size={22} /></span>
              <h1>{emailOn ? "Administrator sign-in" : "Sign in"}</h1>
              {passwordOn ? (
                <>
                  <p className="auth-lead">Use the username and password for your account.</p>
                  {!emailOn && (
                    <div className="notice" role="note">
                      <Icon name="info" size={16} />
                      <span>Email sign-in is not set up on this server yet. An administrator can turn it on in the server settings.</span>
                    </div>
                  )}
                  <label>
                    Username
                    <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
                  </label>
                  <label>
                    Password
                    <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                  </label>
                  {error && <div className="form-error" role="alert">{error}</div>}
                  <Button kind="primary" type="submit" busy={busy} wide>Sign in</Button>
                  <p className="tiny muted">The administrator password is the one set when this server was installed (CV_ADMIN_PASSWORD; if none was set, it was printed in the server log at the first start).</p>
                </>
              ) : (
                <div className="notice" role="note">
                  <Icon name="info" size={16} />
                  <span>Sign-in is not available on this server right now. Ask an administrator for help.</span>
                </div>
              )}
            </form>
          )}
        </div>
        <p className="auth-foot"><Icon name="lock" size={14} /> Cheque and signature records are restricted to authorised staff.</p>
      </section>
    </div>
  );
}

/** Shown to a person who has signed in by email but has no role yet. Nothing else in the application is reachable. */
export function Pending() {
  const { user, refresh, logout } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const name = user ? displayName(user) : "";

  const check = async (quiet: boolean) => {
    setBusy(true);
    try {
      const me = await refresh();
      if (me.role === "pending" && !quiet) toast("info", "Still waiting for an administrator to approve your account.");
    } catch (err) {
      if (!quiet) toast("error", errText(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    // an administrator may approve while this page is open
    const t = window.setInterval(() => void check(true), 20000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="gate">
      <header className="gate-top"><Brand tone="light" /></header>
      <div className="gate-card">
        <span className="chip-icon chip-warn"><Icon name="clock" size={22} /></span>
        <h1>Waiting for approval</h1>
        <p className="auth-lead">
          You are signed in as <b className="break">{user?.email ?? user?.username}</b>{name ? <> ({name})</> : null}. An administrator needs to
          give your account a role before you can open cheque and signature records.
        </p>
        <p className="muted tiny">This page checks again every 20 seconds. You can also close it and sign in again later.</p>
        <div className="row-actions start">
          <Button icon="refresh" busy={busy} onClick={() => void check(false)}>Check again</Button>
          <Button kind="ghost" icon="logout" onClick={() => void logout()}>Sign out</Button>
        </div>
      </div>
    </div>
  );
}
