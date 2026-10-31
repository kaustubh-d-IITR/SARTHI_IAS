import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { SessionFollowup } from "../components/ProcessReview";
import { getSession } from "../lib/api";
import type { SessionReceipt } from "../types";

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [receipt, setReceipt] = useState<SessionReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setReceipt(null);
    setError(null);
    getSession(sessionId)
      .then((next) => active && setReceipt(next))
      .catch((caught: unknown) => active && setError(caught instanceof Error ? caught.message : "The session could not be loaded."));
    return () => {
      active = false;
    };
  }, [sessionId]);

  if (error) {
    return (
      <section className="rounded-3xl bg-white p-6 text-ink" role="alert">
        <h1 className="font-serif text-2xl">This session could not be opened</h1>
        <p className="mt-2 text-sm text-slate-600">{error}</p>
        <Link to="/" className="mt-4 inline-flex rounded-full bg-ink px-5 py-2.5 text-sm text-white">
          Back to the studio
        </Link>
      </section>
    );
  }
  if (!receipt) {
    return (
      <p className="text-mist" role="status">
        Loading the session…
      </p>
    );
  }
  return (
    <div className="grid gap-4">
      <Link to="/" className="text-sm text-mist hover:text-white">
        Back to the studio
      </Link>
      <SessionFollowup key={receipt.session_id} receipt={receipt} onReset={() => navigate("/")} />
    </div>
  );
}
