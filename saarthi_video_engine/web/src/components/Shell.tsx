import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "./Auth";
import { useBackend } from "./BackendStatus";

const links = [
  { to: "/", label: "Studio", end: true },
  { to: "/auto-edit", label: "Auto-Edit", end: false },
  { to: "/my-broll", label: "My B-Roll", end: false },
];

export function Shell() {
  const { phase } = useBackend();
  const { state, signOut } = useAuth();
  const label = phase === "online" ? "API connected" : phase === "offline" ? "API offline" : "Checking API";
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-20 focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="border-b border-white/10">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal text-sm font-semibold tracking-wide text-white">
              SI
            </span>
            <div>
              <p className="font-serif text-lg leading-none text-white">Sarthi IAS Video Studio</p>
              <p className="mt-1 text-sm text-mist/80">AI-powered educational video production.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <nav className="flex rounded-full border border-white/10 bg-white/5 p-1" aria-label="Studio">
              {links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.end}
                  className={({ isActive }) =>
                    `rounded-full px-3 py-1.5 text-sm ${isActive ? "bg-white text-ink" : "text-mist hover:bg-white/10"}`
                  }
                >
                  {link.label}
                </NavLink>
              ))}
            </nav>
            <p className="inline-flex items-center gap-2 rounded-full border border-white/10 px-3 py-1.5 text-xs text-mist" role="status">
              <span className={`h-2 w-2 rounded-full ${phase === "online" ? "bg-teal" : phase === "offline" ? "bg-red-400" : "bg-amber-300"}`} />
              {label}
            </p>
            {state?.mode === "password" && state.user && (
              <div className="flex items-center gap-2 text-xs text-mist">
                <span title={state.user.email}>{state.user.display_name || state.user.email}</span>
                <button type="button" className="rounded-full border border-white/20 px-3 py-1.5 text-mist hover:bg-white/10" onClick={() => void signOut()}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      {phase === "offline" && (
        <div className="mx-auto max-w-6xl px-5 pt-4">
          <p className="rounded-2xl border border-red-300/40 bg-red-500/10 px-4 py-3 text-sm text-red-100" role="alert">
            The studio API is not responding. Uploads and processing will resume once it is back; your sessions are saved.
          </p>
        </div>
      )}
      <main id="main" className="mx-auto max-w-6xl px-5 py-8">
        <Outlet />
      </main>
    </div>
  );
}
