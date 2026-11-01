import { useEffect, useMemo, useState } from "react";
import { getThumbnail, savePlanEdits, undoPlanChange } from "../lib/api";
import type { CatalogueItem, EditingPlanDocument, Placement, PlacementEdit, TranscriptDocument, VisualAnalysis } from "../types";
import { Timeline } from "./Timeline";
import { TitleField } from "./TitleField";

type Draft = Placement & { key: string };

const MIN_CUTAWAY = 2;

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}s`;
}

function score(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function evidenceLabel(evidence: string | undefined): string {
  switch (evidence) {
    case "combined":
      return "Frames and description";
    case "description":
      return "Description only — frames not analysed";
    case "description_lexical":
      return "Description keywords only — frames not analysed";
    case "manual":
      return "Chosen by you";
    case "existing_pipeline":
      return "Existing Auto-Edit pipeline";
    default:
      return evidence || "—";
  }
}

function analysisOf(item: CatalogueItem | undefined): VisualAnalysis | null {
  return item && typeof item.visual_analysis === "object" && item.visual_analysis ? item.visual_analysis : null;
}

function Thumbnail({ sessionId, assetId, available, label }: { sessionId: string; assetId: string; available: boolean; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!available) return;
    let active = true;
    let created: string | null = null;
    getThumbnail(sessionId, assetId)
      .then((blob) => {
        if (!active) return;
        created = URL.createObjectURL(blob);
        setUrl(created);
      })
      .catch(() => setUrl(null));
    return () => {
      active = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sessionId, assetId, available]);
  if (!url) {
    return <div className="grid h-24 w-40 shrink-0 place-items-center rounded-lg bg-slate-100 text-xs text-slate-500">No thumbnail</div>;
  }
  return <img src={url} alt={`Frame from ${label}`} className="h-24 w-40 shrink-0 rounded-lg object-cover" />;
}

function toDrafts(plan: EditingPlanDocument): Draft[] {
  return [...(plan.placements || []), ...(plan.rejected_placements || [])].map((item, index) => ({ ...item, key: `${item.asset_id}-${index}` }));
}

function captionSpans(transcript: TranscriptDocument | null | undefined, maxWords: number): { start: number; end: number }[] {
  const spans: { start: number; end: number }[] = [];
  for (const segment of transcript?.segments || []) {
    const words = (segment.words || []).filter((word) => typeof word.start === "number" && typeof word.end === "number");
    if (!words.length) {
      if (typeof segment.start === "number" && typeof segment.end === "number") spans.push({ start: segment.start, end: segment.end });
      continue;
    }
    for (let k = 0; k < words.length; k += maxWords) {
      const group = words.slice(k, k + maxWords);
      spans.push({ start: Number(group[0].start), end: Number(group[group.length - 1].end) });
    }
  }
  return spans;
}

export function PlanReview({
  sessionId,
  plan,
  disabled,
  onSaved,
  transcript,
}: {
  sessionId: string;
  plan: EditingPlanDocument;
  disabled: boolean;
  onSaved: (plan: EditingPlanDocument) => void;
  transcript?: TranscriptDocument | null;
}) {
  const [drafts, setDrafts] = useState<Draft[]>(() => toDrafts(plan));
  const [selected, setSelected] = useState<string | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [title, setTitle] = useState(plan.title?.text || "");
  const [errors, setErrors] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState("");
  const catalogue = useMemo(() => new Map((plan.catalogue || []).map((item) => [item.asset_id, item])), [plan.catalogue]);
  const lessonSeconds = plan.raw_video.duration_seconds || 0;

  useEffect(() => {
    setDrafts(toDrafts(plan));
    setTitle(plan.title?.text || "");
    setErrors(null);
  }, [plan]);

  const original = JSON.stringify(toDrafts(plan).map(({ key, ...rest }) => rest));
  const dirty = JSON.stringify(drafts.map(({ key, ...rest }) => rest)) !== original || title.trim() !== (plan.title?.text || "");
  const used = new Set(drafts.map((item) => item.asset_id));
  const addable = (plan.catalogue || []).filter((item) => !used.has(item.asset_id) && (item.duration_seconds || 0) >= MIN_CUTAWAY);
  const covered = drafts.filter((item) => item.approval !== "rejected").reduce((sum, item) => sum + (item.speech_end - item.speech_start), 0);
  const cap = (plan.broll_cap || 0.58) * lessonSeconds;

  function patch(key: string, next: Partial<Draft>) {
    setDrafts((rows) => rows.map((row) => (row.key === key ? { ...row, ...next } : row)));
  }

  /** A cutaway plays its clip in real time and may never ask for footage past the clip's end. */
  function fitSource(row: Draft, length: number, sourceIn: number): { length: number; source_in: number } {
    const clip = row.clip_duration || catalogue.get(row.asset_id)?.duration_seconds || 0;
    if (clip <= 0) return { length, source_in: Math.max(0, sourceIn) };
    const fitted = Math.min(length, clip);
    return { length: fitted, source_in: Math.max(0, Math.min(sourceIn, clip - fitted)) };
  }

  function setTimeline(row: Draft, start: number, end: number) {
    const fit = fitSource(row, Math.max(0, end - start), row.source_in);
    patch(row.key, { speech_start: round(start), speech_end: round(start + fit.length), source_in: round(fit.source_in), source_out: round(fit.source_in + fit.length) });
  }

  function setSource(row: Draft, sourceIn: number) {
    const fit = fitSource(row, row.speech_end - row.speech_start, sourceIn);
    patch(row.key, { source_in: round(fit.source_in), source_out: round(fit.source_in + fit.length) });
  }

  function changeAsset(row: Draft, assetId: string) {
    const item = catalogue.get(assetId);
    if (!item) return;
    const clip = item.duration_seconds || 0;
    const length = Math.min(row.speech_end - row.speech_start, clip);
    patch(row.key, {
      asset_id: assetId,
      filename: item.filename,
      description: item.description,
      visual_summary: analysisOf(item)?.summary || "",
      clip_duration: clip,
      speech_end: round(row.speech_start + length),
      source_in: 0,
      source_out: round(length),
      evidence: "manual",
      approval: "approved",
      approved_by: "user",
    });
  }

  function addPlacement(assetId: string) {
    const item = catalogue.get(assetId);
    if (!item) return;
    const evaluation = (plan.evaluations || []).find((row) => row.asset_id === assetId);
    const window = evaluation?.top_windows[0];
    const clip = item.duration_seconds || 0;
    const start = window ? window.start : 0;
    const length = Math.min(clip, window ? window.end - window.start : MIN_CUTAWAY, Math.max(MIN_CUTAWAY, clip));
    setDrafts((rows) => [
      ...rows,
      {
        key: `${assetId}-new-${rows.length}`,
        asset_id: assetId,
        filename: item.filename,
        description: item.description,
        visual_summary: analysisOf(item)?.summary || "",
        speech_text: window?.speech_text || "",
        speech_start: round(start),
        speech_end: round(start + length),
        source_in: 0,
        source_out: round(length),
        clip_duration: clip,
        evidence: "manual",
        approval: "approved",
        approved_by: "user",
      },
    ]);
    setAdding("");
  }

  async function save() {
    if (!plan.plan_version) return;
    setSaving(true);
    setErrors(null);
    const placements: PlacementEdit[] = drafts.map((row) => ({
      asset_id: row.asset_id,
      speech_start: row.speech_start,
      speech_end: row.speech_end,
      source_in: row.source_in,
      source_out: row.source_out,
      approval: row.approval === "rejected" ? "rejected" : "approved",
    }));
    const titleChanged = title.trim() !== (plan.title?.text || "");
    try {
      const next = await savePlanEdits(sessionId, {
        base_version_id: plan.plan_version.version_id,
        placements,
        ...(titleChanged ? { title, change_title: true } : {}),
      });
      onSaved(next);
    } catch (caught) {
      setErrors(caught instanceof Error ? caught.message : "The changes could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function undo() {
    if (!plan.plan_version) return;
    setUndoing(true);
    setErrors(null);
    try {
      onSaved(await undoPlanChange(sessionId, plan.plan_version.version_id));
    } catch (caught) {
      setErrors(caught instanceof Error ? caught.message : "Nothing could be undone.");
    } finally {
      setUndoing(false);
    }
  }

  const captions = useMemo(() => captionSpans(transcript, plan.settings?.captions.max_words || 3), [transcript, plan.settings]);
  const suggestion = plan.title?.suggestion;
  const abstained = (plan.evaluations || []).filter((row) => row.status === "abstained");

  return (
    <section className="rounded-3xl border border-line bg-white p-6" aria-label="Review B-roll and title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-serif text-2xl text-ink">Review cutaways and title</h2>
        {plan.plan_version && (
          <p className="text-sm text-slate-500">
            Plan version {plan.plan_version.number} · {plan.plan_version.origin.replace("_", " ")}
          </p>
        )}
      </div>
      {plan.visual_analysis && (
        <p className="mt-2 text-sm text-slate-600">
          {plan.visual_analysis.performed > 0
            ? `Frames were analysed for ${plan.visual_analysis.performed} of ${plan.visual_analysis.assets} clips with ${plan.visual_analysis.provider.toUpperCase()}.`
            : `No frames were analysed${plan.visual_analysis.unavailable_reason ? `: ${plan.visual_analysis.unavailable_reason}` : ""}. Matches below rely on descriptions only.`}
        </p>
      )}

      <div className="mt-4 rounded-2xl bg-slate-50 p-4">
        <TitleField id="plan-title" value={title} onChange={setTitle} disabled={disabled || saving} />
        {suggestion && !title.trim() && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-900">AI suggestion</span>
            <span className="text-slate-700">{suggestion.text}</span>
            <button type="button" className="rounded-full border border-line px-3 py-1" onClick={() => setTitle(suggestion.text)} disabled={disabled}>
              Use suggestion
            </button>
            <span className="w-full text-slate-500">{suggestion.basis}. It is not used unless you save it.</span>
          </div>
        )}
        <p className="mt-2 text-sm text-slate-600">
          {plan.title?.status === "user_confirmed" ? `Saved title: ${plan.title.text}` : "No title is saved. The title line in the video stays empty."}
        </p>
      </div>

      {lessonSeconds > 0 && (
        <Timeline
          duration={lessonSeconds}
          rows={drafts}
          captions={captions}
          settings={plan.settings}
          selected={selected}
          disabled={disabled || saving}
          onSelect={setSelected}
          onMove={(row, start, end) => setTimeline(row as Draft, start, end)}
        />
      )}

      <ol className="mt-4 grid gap-4">
        {drafts.map((row) => {
          const item = catalogue.get(row.asset_id);
          const analysis = analysisOf(item);
          const rejected = row.approval === "rejected";
          const length = row.speech_end - row.speech_start;
          return (
            <li key={row.key} onClick={() => setSelected(row.key)} className={`rounded-2xl border p-4 text-sm ${rejected ? "border-line bg-slate-50 opacity-80" : "border-teal/40 bg-teal/5"} ${selected === row.key ? "ring-2 ring-amber-300" : ""}`}>
              <div className="flex flex-wrap gap-4">
                <Thumbnail sessionId={sessionId} assetId={row.asset_id} available={Boolean(item?.thumbnail)} label={row.filename} />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
                    {plan.mode === "my_broll" ? "Your footage" : "Licensed internet footage"} · {rejected ? "Rejected — teacher stays" : row.approved_by === "user" ? "Approved by you" : "Proposed by the matcher"}
                  </p>
                  <p className="mt-1 font-medium text-ink">{row.filename}</p>
                  <p className="mt-1 text-slate-700">Your description: {row.description || "—"}</p>
                  <p className="mt-1 text-slate-700">
                    Visual analysis: {analysis?.performed ? analysis.summary : analysis?.reason ? `not performed (${analysis.reason})` : "not performed"}
                  </p>
                  {row.matched_phrase ? (
                    <p className="mt-1 text-slate-700">
                      Placed on: <span className="font-medium">“{row.matched_phrase}”</span> · {seconds(row.speech_start)}–{seconds(row.speech_end)}
                      {row.occurrence && row.occurrence.count > 1 ? ` · mention ${row.occurrence.index} of ${row.occurrence.count}` : ""}
                      {row.placement_rule === "back_to_back" ? " · runs straight into the next cutaway" : ""}
                    </p>
                  ) : (
                    row.speech_text && <p className="mt-1 text-slate-700">Matched speech: {row.speech_text}</p>
                  )}
                  {row.user_override && <p className="mt-1 text-slate-700">Your instruction: “{row.user_override.target_phrase}”</p>}
                  <p className="mt-1 text-slate-600">AI confidence: {score(row.confidence)} · {row.approved_by === "user" ? "approved by you" : "not yet approved by you"}</p>
                  <p className="mt-1 text-slate-600">
                    Evidence: {evidenceLabel(row.evidence)} · description {score(row.description_relevance)} · visual {score(row.visual_relevance)} · combined {score(row.score)}
                  </p>
                  {row.explanation && <p className="mt-1 text-slate-600">Why: {row.explanation}</p>}
                  {row.adjusted && <p className="mt-1 text-amber-800">Fitted to the clip: {row.adjusted}</p>}
                  {row.trimmed && <p className="mt-1 text-amber-800">{row.trimmed}</p>}
                </div>
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-4">
                <label className="block">
                  Timeline start (s)
                  <input type="number" step="0.1" min={0} className="mt-1 w-full rounded-lg border border-line px-2 py-1" value={row.speech_start} disabled={disabled || rejected} onChange={(event) => setTimeline(row, Number(event.target.value), Number(event.target.value) + length)} />
                </label>
                <label className="block">
                  Timeline end (s)
                  <input type="number" step="0.1" min={0} className="mt-1 w-full rounded-lg border border-line px-2 py-1" value={row.speech_end} disabled={disabled || rejected} onChange={(event) => setTimeline(row, row.speech_start, Number(event.target.value))} />
                </label>
                <label className="block">
                  Source in (s)
                  <input type="number" step="0.1" min={0} className="mt-1 w-full rounded-lg border border-line px-2 py-1" value={row.source_in} disabled={disabled || rejected} onChange={(event) => setSource(row, Number(event.target.value))} />
                </label>
                <div>
                  Source out (s)
                  <p className="mt-2 text-slate-700">
                    {seconds(row.source_out)} of {seconds(row.clip_duration)}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {rejected ? (
                  <button type="button" className="rounded-full bg-ink px-4 py-1.5 text-white" disabled={disabled} onClick={() => patch(row.key, { approval: "approved", approved_by: "user" })}>
                    Approve again
                  </button>
                ) : (
                  <>
                    <button type="button" className="rounded-full bg-ink px-4 py-1.5 text-white" disabled={disabled || row.approved_by === "user"} onClick={() => patch(row.key, { approval: "approved", approved_by: "user" })}>
                      Approve
                    </button>
                    <button type="button" className="rounded-full border border-line px-4 py-1.5" disabled={disabled} onClick={() => patch(row.key, { approval: "rejected" })}>
                      Reject — keep teacher
                    </button>
                  </>
                )}
                {plan.mode === "my_broll" && !rejected && (
                  <label className="flex items-center gap-2">
                    Clip
                    <select className="rounded-lg border border-line px-2 py-1" value={row.asset_id} disabled={disabled} onChange={(event) => changeAsset(row, event.target.value)} aria-label={`Clip for ${row.filename}`}>
                      {(plan.catalogue || [])
                        .filter((option) => option.asset_id === row.asset_id || !used.has(option.asset_id))
                        .map((option) => (
                          <option key={option.asset_id} value={option.asset_id}>
                            {option.filename}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {drafts.length === 0 && <p className="mt-4 text-sm text-slate-600">No cutaways. The teacher stays on screen for the whole lesson.</p>}

      {plan.mode === "my_broll" && addable.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-2">
            Add a cutaway from
            <select className="rounded-lg border border-line px-2 py-1" value={adding} disabled={disabled} onChange={(event) => setAdding(event.target.value)}>
              <option value="">choose a clip</option>
              {addable.map((item) => (
                <option key={item.asset_id} value={item.asset_id}>
                  {item.filename}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="rounded-full border border-line px-4 py-1.5" disabled={!adding || disabled} onClick={() => addPlacement(adding)}>
            Add
          </button>
        </div>
      )}

      {abstained.length > 0 && (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-slate-700">Clips not used ({abstained.length})</summary>
          <ul className="mt-2 grid gap-2">
            {abstained.map((row) => (
              <li key={row.asset_id} className="rounded-xl bg-slate-50 px-3 py-2">
                <span className="font-medium">{row.filename}</span>: {row.reason}
                {row.contradicted && <span className="ml-1 text-amber-800">The sampled frames do not match the description.</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="mt-4 text-sm text-slate-600">
        B-roll would cover {covered.toFixed(1)}s of the {cap.toFixed(1)}s allowed ({Math.round((plan.broll_cap || 0.58) * 100)}% of the lesson). Each cutaway lasts at least {MIN_CUTAWAY}s (or 1 s when it runs straight into the next cutaway) and plays its source in real time.
      </p>
      {errors && (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {errors}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-3">
        <button type="button" className="rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:opacity-40" disabled={!dirty || saving || disabled} onClick={() => void save()}>
          {saving ? "Validating…" : "Save changes and validate"}
        </button>
        <button type="button" className="rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40" disabled={!dirty || saving} onClick={() => { setDrafts(toDrafts(plan)); setTitle(plan.title?.text || ""); setErrors(null); }}>
          Discard changes
        </button>
        <button type="button" className="rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40" disabled={dirty || saving || undoing || disabled || !plan.plan_version?.parent_version_id} onClick={() => void undo()}>
          {undoing ? "Undoing…" : "Undo last change"}
        </button>
      </div>
      {disabled && <p className="mt-2 text-sm text-slate-500">Changes are locked while a render is running.</p>}
    </section>
  );
}
