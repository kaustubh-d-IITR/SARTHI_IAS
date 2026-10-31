import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { getMe, login, logout, type AuthState } from "../lib/api";

type Phase = "checking" | "signed_in" | "signed_out" | "unreachable";

type AuthValue = {
  phase: Phase;
  state: AuthState | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  retry: () => void;
};

const AuthContext = createContext<AuthValue>({
  phase: "signed_in",
  state: { mode: "client_header", user: null, csrf_token: null },
  signIn: async () => undefined,
  signOut: async () => undefined,
  retry: () => undefined,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [state, setState] = useState<AuthState | null>(null);

  const check = useCallback(() => {
    setPhase("checking");
    getMe()
      .then((next) => {
        setState(next);
        setPhase("signed_in");
      })
      .catch((caught: { status?: number }) => {
        setState(null);
        setPhase(caught?.status === 401 ? "signed_out" : "unreachable");
      });
  }, []);

  useEffect(() => {
    check();
    const onSignedOut = () => {
      setState(null);
      setPhase("signed_out");
    };
    window.addEventListener("studio:signed-out", onSignedOut);
    return () => window.removeEventListener("studio:signed-out", onSignedOut);
  }, [check]);

  const signIn = useCallback(async (email: string, password: string) => {
    const next = await login(email, password);
    setState(next);
    setPhase("signed_in");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } finally {
      setState(null);
      setPhase("signed_out");
    }
  }, []);

  return <AuthContext.Provider value={{ phase, state, signIn, signOut, retry: check }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  return useContext(AuthContext);
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { phase, retry } = useAuth();
  if (phase === "checking") {
    return (
      <div className="grid min-h-screen place-items-center text-mist" role="status">
        Loading Sarthi IAS Video Studio…
      </div>
    );
  }
  if (phase === "unreachable") {
    return (
      <div className="grid min-h-screen place-items-center px-5">
        <section className="max-w-md rounded-3xl bg-white p-6 text-ink" role="alert">
          <h1 className="font-serif text-2xl">The studio is not reachable</h1>
          <p className="mt-2 text-sm text-slate-600">The API did not answer. It may be starting up. Wait a moment and try again.</p>
          <button type="button" className="mt-4 rounded-full bg-ink px-5 py-2.5 text-sm text-white" onClick={retry}>
            Try again
          </button>
        </section>
      </div>
    );
  }
  if (phase === "signed_out") return <LoginPage />;
  return <>{children}</>;
}

export function LoginPage() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
      setPassword("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-5">
      <form className="w-full max-w-sm rounded-3xl bg-white p-6 text-ink shadow-[0_20px_50px_rgba(0,0,0,0.18)]" onSubmit={(event) => void submit(event)} aria-label="Sign in">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal text-sm font-semibold tracking-wide text-white">SI</span>
          <div>
            <h1 className="font-serif text-xl leading-none">Sarthi IAS Video Studio</h1>
            <p className="mt-1 text-sm text-slate-500">Sign in to continue.</p>
          </div>
        </div>
        <label className="mt-5 block text-sm" htmlFor="login-email">
          Email
          <input id="login-email" type="email" autoComplete="username" required className="mt-1 w-full rounded-xl border border-line px-3 py-2" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="mt-3 block text-sm" htmlFor="login-password">
          Password
          <input id="login-password" type="password" autoComplete="current-password" required className="mt-1 w-full rounded-xl border border-line px-3 py-2" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && (
          <p className="mt-3 text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="mt-5 w-full rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:opacity-40" disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="mt-3 text-xs text-slate-500">Accounts are created by the studio owner. Ask them if you need access.</p>
      </form>
    </div>
  );
}
