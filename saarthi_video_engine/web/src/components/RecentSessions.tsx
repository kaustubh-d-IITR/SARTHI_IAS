import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSessions } from "../lib/api";
import type { SessionSummary } from "../types";

const STATUS: Record<string, string> = {
  prepared: "Ready to process",
  queued: "Queued",
  transcribing: "Transcribing",
  analysing: "Analysing",
  analysing_footage: "Analysing clips",
  selecting_footage: "Selecting footage",
  building_plan: "Building plan",
  validating_plan: "Validating plan",
  plan_ready: "Plan ready",
  failed: "Needs attention",
};

export function RecentSessions() {
  const [rows, setRows] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listSessions()
      .then((body) => active && setRows(body.sessions))
      .catch((caught: unknown) => active && setError(caught instanceof Error ? caught.message : "Sessions could not be loaded."));
    return () => {
      active = false;
    };
  }, []);

  if (error) {
    return (
      <p className="text-sm text-red-300" role="alert">
        {error}
      </p>
    );
  }
  if (!rows || rows.length === 0) return null;
  return (
    <section aria-label="Your sessions" className="rounded-3xl border border-white/10 bg-panel p-6">
      <h2 className="font-serif text-2xl text-white">Your sessions</h2>
      <ul className="mt-4 grid gap-2">
        {rows.slice(0, 12).map((row) => (
          <li key={row.session_id}>
            <Link to={`/sessions/${row.session_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-white/5 px-4 py-3 text-sm text-mist hover:bg-white/10">
              <span className="text-white">{row.title || row.filename}</span>
              <span>
                {row.mode === "auto_edit" ? "AI Auto-Edit" : "My B-Roll"} · {STATUS[row.status] || row.status}
                {row.render_status === "completed" || row.has_video ? " · video ready" : row.render_status !== "not_started" ? ` · render ${row.render_status.replace("_", " ")}` : ""}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
