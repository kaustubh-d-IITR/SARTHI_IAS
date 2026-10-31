import { useState } from "react";
import { assistantApply, assistantPropose, undoPlanChange } from "../lib/api";
import type { AssistantReply, EditingPlanDocument } from "../types";

type Turn = { role: "user" | "assistant"; text: string; reply?: AssistantReply; state?: "pending" | "applied" | "dismissed" | "failed" };

const EXAMPLES = [
  "Move the newspaper B-roll to where sir says na hi maine newspaper padha and keep it for 2 seconds.",
  "Phone wala B-roll hata do.",
  "Background music thoda subtle kar do.",
  "Captions ka size thoda bada kar do.",
  "Glitter effect sirf intro mein lagao.",
];

/** Optional. Everything here can also be done with the manual controls; nothing is applied without your click. */
export function AssistantPanel({ sessionId, plan, disabled, onSaved }: { sessionId: string; plan: EditingPlanDocument; disabled: boolean; onSaved: (plan: EditingPlanDocument) => void }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(text: string) {
    const clean = text.trim();
    if (!clean) return;
    setBusy(true);
    setError(null);
    setTurns((rows) => [...rows, { role: "user", text: clean }]);
    setMessage("");
    try {
      const reply = await assistantPropose(sessionId, clean);
      setTurns((rows) => [...rows, { role: "assistant", text: reply.status === "proposal" ? reply.understood : reply.question, reply, state: reply.status === "proposal" ? "pending" : undefined }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The assistant is not available. You can still edit with the controls.");
    } finally {
      setBusy(false);
    }
  }

  async function apply(index: number) {
    const turn = turns[index];
    if (!turn.reply || turn.reply.status !== "proposal") return;
    setBusy(true);
    setError(null);
    try {
      const next = await assistantApply(sessionId, turn.reply.base_version_id, turn.reply.operations);
      setTurns((rows) => rows.map((row, k) => (k === index ? { ...row, state: "applied" } : row)));
      onSaved(next);
    } catch (caught) {
      setTurns((rows) => rows.map((row, k) => (k === index ? { ...row, state: "failed" } : row)));
      setError(caught instanceof Error ? caught.message : "The change could not be applied.");
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!plan.plan_version) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await undoPlanChange(sessionId, plan.plan_version.version_id));
      setTurns((rows) => [...rows, { role: "assistant", text: "Undone. The previous version's clips, settings and title are back as a new version." }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Nothing could be undone.");
    } finally {
      setBusy(false);
    }
  }

  const canUndo = Boolean(plan.plan_version?.parent_version_id);

  return (
    <section className="rounded-3xl border border-line bg-white p-6" aria-label="Editing assistant">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-2xl text-ink">Editing assistant</h2>
        <button type="button" className="rounded-full border border-line px-4 py-1.5 text-sm" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Hide" : "Open"} assistant
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-600">Optional. Ask in English or Hinglish; you see what it understood and apply it yourself. Every change is a new plan version you can undo.</p>
      {open && (
        <div className="mt-4">
          <ol className="grid max-h-96 gap-3 overflow-y-auto" aria-live="polite">
            {turns.map((turn, index) => (
              <li key={index} className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${turn.role === "user" ? "ml-auto bg-ink text-white" : "bg-slate-50 text-slate-800"}`}>
                <p>{turn.text}</p>
                {turn.reply?.status === "proposal" && (
                  <>
                    <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
                      {turn.reply.operations.map((op, k) => (
                        <li key={k}>
                          <code>{op.operation}</code>
                          {op.matched_phrase ? ` → “${op.matched_phrase}” at ${Number(op.target_start).toFixed(2)} s` : ""}
                          {op.duration_seconds ? `, ${op.duration_seconds} s` : ""}
                        </li>
                      ))}
                    </ul>
                    {turn.state === "pending" && (
                      <div className="mt-2 flex gap-2">
                        <button type="button" className="rounded-full bg-teal px-3 py-1 text-white disabled:opacity-40" disabled={busy || disabled} onClick={() => void apply(index)}>
                          Apply
                        </button>
                        <button type="button" className="rounded-full border border-line px-3 py-1" disabled={busy} onClick={() => setTurns((rows) => rows.map((row, k) => (k === index ? { ...row, state: "dismissed" } : row)))}>
                          Don't apply
                        </button>
                      </div>
                    )}
                    {turn.state === "applied" && <p className="mt-2 text-xs font-medium text-teal">Applied as a new plan version.</p>}
                    {turn.state === "dismissed" && <p className="mt-2 text-xs text-slate-500">Not applied.</p>}
                    {turn.state === "failed" && <p className="mt-2 text-xs text-red-700">Not applied — see the message below.</p>}
                  </>
                )}
              </li>
            ))}
          </ol>
          {turns.length === 0 && (
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="rounded-full border border-line px-3 py-1 text-left text-xs text-slate-700" onClick={() => setMessage(example)}>
                  {example}
                </button>
              ))}
            </div>
          )}
          <form
            className="mt-3 flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void send(message);
            }}
          >
            <label className="sr-only" htmlFor="assistant-message">
              Editing instruction
            </label>
            <input id="assistant-message" className="min-w-0 flex-1 rounded-full border border-line px-4 py-2 text-sm" placeholder="e.g. Newspaper wala B-roll 2 seconds tak rakho" value={message} maxLength={500} onChange={(event) => setMessage(event.target.value)} disabled={busy || disabled} />
            <button type="submit" className="rounded-full bg-ink px-4 py-2 text-sm text-white disabled:opacity-40" disabled={busy || disabled || !message.trim()}>
              {busy ? "…" : "Send"}
            </button>
            <button type="button" className="rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40" disabled={busy || disabled || !canUndo} onClick={() => void undo()}>
              Undo last change
            </button>
          </form>
          {error && (
            <p className="mt-2 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
