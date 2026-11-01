import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, AuthConfig, CodeRequested, getToken, post, Role, setToken, setUnauthorizedHandler, User } from "./api";

// Used when the server cannot be asked (older backend, network error): the password form always works.
const FALLBACK_CONFIG: AuthConfig = {
  email_signin: false, email_domain: null, email_domains: [], password_signin: true,
  code_length: 8, code_ttl_seconds: 600, resend_seconds: 30,
};

interface AuthState {
  user: User | null;
  ready: boolean;
  config: AuthConfig;
  /** Administrator sign-in: username + password. */
  login: (u: string, p: string) => Promise<void>;
  /** Institutional email sign-in, step 1: mail a one-time code. */
  requestCode: (email: string) => Promise<CodeRequested>;
  /** Step 2: the code proves the mailbox. */
  verifyCode: (email: string, code: string) => Promise<void>;
  /** Ask the server who we are now (an administrator may have approved the account). */
  refresh: () => Promise<User>;
  logout: () => Promise<void>;
  can: (...roles: Role[]) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [config, setConfig] = useState<AuthConfig>(FALLBACK_CONFIG);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null);
      setUser(null);
    });
    const who = getToken()
      ? api<User>("/api/auth/me").then(setUser).catch(() => setToken(null))
      : Promise.resolve();
    const options = api<AuthConfig>("/api/auth/config").then(setConfig).catch(() => setConfig(FALLBACK_CONFIG));
    Promise.all([who, options]).finally(() => setReady(true));
  }, []);

  const finish = useCallback((res: { token: string; user: User }) => {
    setToken(res.token);
    setUser(res.user);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    finish(await post<{ token: string; user: User }>("/api/auth/login", { username, password }));
  }, [finish]);

  const requestCode = useCallback((email: string) => post<CodeRequested>("/api/auth/email/request", { email }), []);

  const verifyCode = useCallback(async (email: string, code: string) => {
    finish(await post<{ token: string; user: User }>("/api/auth/email/verify", { email, code }));
  }, [finish]);

  const refresh = useCallback(async () => {
    const me = await api<User>("/api/auth/me");
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout");
    } finally {
      setToken(null);
      setUser(null);
    }
  }, []);

  const can = useCallback((...roles: Role[]) => !!user && roles.includes(user.role), [user]);

  return <Ctx.Provider value={{ user, ready, config, login, requestCode, verifyCode, refresh, logout, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
