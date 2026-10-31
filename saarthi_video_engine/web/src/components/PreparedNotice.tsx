import type { SessionReceipt } from "../types";
import { formatBytes } from "../lib/format";

export function PreparedNotice({ receipt, onReset }: { receipt: SessionReceipt; onReset: () => void }) {
  return (
    <section className="rounded-3xl border border-line bg-white p-6 shadow-sm" aria-live="polite">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">Prepared</p>
      <h2 className="mt-2 font-serif text-3xl text-ink">Files received. Editing has not started.</h2>
      <p className="mt-3 max-w-2xl text-slate-700">{receipt.note}</p>
      <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Session</dt>
          <dd className="break-all font-medium">{receipt.session_id}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Mode</dt>
          <dd>{receipt.mode === "auto_edit" ? "AI Auto-Edit" : "Edit with My B-Roll"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Lesson</dt>
          <dd>{receipt.raw_video.filename}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Stored size</dt>
          <dd>
            {formatBytes(receipt.total_bytes)}
            {receipt.mode === "my_broll" ? ` · ${receipt.broll_count} B-roll clips` : ""}
          </dd>
        </div>
      </dl>
      <button type="button" className="mt-6 rounded-full border border-line px-4 py-2 text-sm" onClick={onReset}>
        Start another upload
      </button>
    </section>
  );
}
