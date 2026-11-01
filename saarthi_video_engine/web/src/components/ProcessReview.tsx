import { useEffect, useState } from "react";
import {
  absoluteUrl,
  getEditingPlan,
  getRenderStatus,
  getVideoLinks,
  getSessionStatus,
  getTranscript,
  rematchFootage,
  startProcessing,
  startRender,
} from "../lib/api";
import type { EditingPlanDocument, PictureDecision, RenderStatusBody, SessionReceipt, SessionStatusBody, TranscriptDocument } from "../types";
import { AssistantPanel } from "./AssistantPanel";
import { evidenceLabel, PlanReview } from "./PlanReview";
import { StudioControls } from "./StudioControls";
import { PreparedNotice } from "./PreparedNotice";
import { isRendering, RenderPanel } from "./RenderPanel";

const STAGES = [
  { id: "queued", label: "Queued" },
  { id: "transcribing", label: "Transcribing" },
  { id: "analysing", label: "Analysing the transcript" },
  { id: "analysing_footage", label: "Analysing your clips' frames" },
  { id: "selecting_footage", label: "Selecting footage" },
  { id: "building_plan", label: "Building the editing plan" },
  { id: "validating_plan", label: "Validating the plan" },
  { id: "plan_ready", label: "Plan ready" },
] as const;

function clock(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}s`;
}

function originLabel(origin: string | undefined, mode: SessionReceipt["mode"]): string {
  if (origin === "user" || mode === "my_broll") return "Your footage";
  if (origin === "commons") return "Licensed internet footage";
  return "Footage";
}

const WORKSPACE_TABS = [
  { id: "review", label: "Cutaways & timeline" },
  { id: "look", label: "Look & sound" },
  { id: "assistant", label: "AI editing assistant" },
] as const;

/** Where every editing control lives. Locked, with the reason, until the plan exists. */
function EditingWorkspace({
  ready,
  failed,
  review,
  look,
  assistant,
}: {
  ready: boolean;
  failed: boolean;
  review: React.ReactNode;
  look: React.ReactNode;
  assistant: React.ReactNode;
}) {
  const [tab, setTab] = useState<(typeof WORKSPACE_TABS)[number]["id"]>("review");
  return (
    <section id="editing-workspace" className="rounded-3xl border-2 border-teal/50 bg-white p-6" aria-label="Editing workspace">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-serif text-2xl text-ink">Editing workspace</h2>
        {ready && (
          <a href="#workspace-render" className="text-sm text-teal underline">
            Go to render
          </a>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-600">Timeline and cutaways, background music and your own music, filters and effects, captions and framing, and the AI editing assistant.</p>
      {!ready ? (
        <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-700" role="status">
          {failed ? (
            <p>Processing failed, so there is no editing plan to work on yet. Use “Retry processing” above; the workspace opens as soon as processing succeeds.</p>
          ) : (
            <p>The workspace opens when the editing plan is ready. Processing is still running.</p>
          )}
        </div>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="Editing workspace sections">
            {WORKSPACE_TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`workspace-tab-${item.id}`}
                aria-selected={tab === item.id}
                aria-controls={`workspace-${item.id}`}
                onClick={() => setTab(item.id)}
                className={`rounded-full border px-4 py-2 text-sm ${tab === item.id ? "border-teal bg-teal text-white" : "border-line text-ink"}`}
              >
                {item.label}
              </button>
            ))}
          </div>
          {/* Panels stay mounted so unsaved edits survive switching tabs. */}
          {WORKSPACE_TABS.map((item) => (
            <div key={item.id} className="mt-4" role="tabpanel" id={`workspace-${item.id}`} aria-labelledby={`workspace-tab-${item.id}`} hidden={tab !== item.id}>
              {item.id === "review" ? review : item.id === "look" ? look : assistant}
            </div>
          ))}
        </>
      )}
    </section>
  );
}

export function ProcessReview({
  receipt,
  status,
  transcript,
  plan,
  error,
  starting,
  onStart,
  onRetry,
  onReset,
  render,
  renderStarting,
  renderError,
  videoUrl,
  onRender,
  onPlanSaved,
  onRematch,
  downloadUrl = null,
  onVideoError,
}: {
  receipt: SessionReceipt;
  status: SessionStatusBody | null;
  transcript: TranscriptDocument | null;
  plan: EditingPlanDocument | null;
  error: string | null;
  starting: boolean;
  onStart: () => void;
  onRetry: () => void;
  onReset: () => void;
  render: RenderStatusBody | null;
  renderStarting: boolean;
  renderError: string | null;
  videoUrl: string | null;
  onRender: () => void;
  onPlanSaved?: (plan: EditingPlanDocument) => void;
  onRematch?: () => void;
  downloadUrl?: string | null;
  onVideoError?: () => void;
}) {
  const stage = status?.stage || status?.status || "prepared";
  const stageIndex = STAGES.findIndex((item) => item.id === stage);
  const pictures = [...(plan?.picture_decisions || [])].sort((left, right) => left.start - right.start);
  const cutaways = pictures.filter((row) => row.picture_role === "show_broll");
  const teachers = pictures.filter((row) => row.picture_role === "show_teacher");

  if (!status) {
    return (
      <div className="grid gap-4">
        <PreparedNotice receipt={receipt} onReset={onReset} />
        <section className="rounded-3xl border border-line bg-white p-6">
          <h2 className="font-serif text-2xl text-ink">Ready to analyse</h2>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Start AI processing to transcribe the lesson and build an editing plan. Rendering is a separate step after you review the plan.
          </p>
          {error && (
            <p className="mt-3 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
          <button
            type="button"
            className="mt-4 rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:opacity-40"
            disabled={starting}
            onClick={onStart}
          >
            Start AI Processing
          </button>
        </section>
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <section className="rounded-3xl border border-line bg-white p-6" aria-live="polite">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">{stage === "failed" ? "Failed" : "Processing"}</p>
        <h2 className="mt-2 font-serif text-3xl text-ink">{status.message || receipt.note}</h2>
        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">Session</dt>
            <dd className="break-all font-medium">{receipt.session_id}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Lesson</dt>
            <dd>{status.filename || receipt.raw_video.filename}</dd>
          </div>
          {receipt.mode === "my_broll" && (
            <div>
              <dt className="text-slate-500">Your B-roll library</dt>
              <dd>
                {receipt.broll_count} clip{receipt.broll_count === 1 ? "" : "s"} supplied
                {receipt.broll.length > 0 ? `: ${receipt.broll.map((item) => item.filename).join(", ")}` : ""}
              </dd>
            </div>
          )}
        </dl>
        {stage !== "failed" && stage !== "plan_ready" && (
          <ol className="mt-5 grid gap-2" aria-label="Processing stages">
            {STAGES.filter((item) => item.id !== "plan_ready").map((item, index) => {
              const state = index < stageIndex ? "Done" : index === stageIndex ? "Current" : "Waiting";
              return (
                <li key={item.id} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-sm" aria-current={index === stageIndex ? "step" : undefined}>
                  <span>{item.label}</span>
                  <span className="text-slate-500">{state}</span>
                </li>
              );
            })}
          </ol>
        )}
        {status.status === "failed" && (
          <p className="mt-4 text-sm text-red-700" role="alert">
            {status.error || error || "Processing failed."}
          </p>
        )}
        {error && status.status !== "failed" && (
          <p className="mt-4 text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        <div className="mt-5 flex flex-wrap gap-3">
          {status.status === "failed" && (
            <button type="button" className="rounded-full bg-ink px-5 py-2.5 text-sm text-white" onClick={onRetry} disabled={starting}>
              Retry processing
            </button>
          )}
          <button type="button" className="rounded-full border border-line px-4 py-2 text-sm" onClick={onReset}>
            Start another upload
          </button>
        </div>
      </section>

      <EditingWorkspace
        ready={status.status === "plan_ready" && Boolean(plan) && Boolean(onPlanSaved)}
        failed={status.status === "failed"}
        review={plan && onPlanSaved ? <PlanReview sessionId={receipt.session_id} plan={plan} disabled={isRendering(render)} onSaved={onPlanSaved} transcript={transcript} /> : null}
        look={plan && onPlanSaved ? <StudioControls sessionId={receipt.session_id} plan={plan} disabled={isRendering(render)} onSaved={onPlanSaved} /> : null}
        assistant={plan && onPlanSaved ? <AssistantPanel sessionId={receipt.session_id} plan={plan} disabled={isRendering(render)} onSaved={onPlanSaved} defaultOpen /> : null}
      />

      {status.status === "plan_ready" && status.counts && (
        <section id="workspace-render" className="rounded-3xl border border-line bg-white p-6" aria-label="Plan summary">
          <h2 className="font-serif text-2xl text-ink">Editing plan ready</h2>
          <p className="mt-2 text-sm text-slate-600">Transcription completed. Review the approved edit, then render when ready.</p>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-500">Transcript segments</dt>
              <dd>{status.counts.segments}</dd>
            </div>
            <div>
              <dt className="text-slate-500">B-roll clips supplied</dt>
              <dd>{status.counts.broll_supplied}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Candidate matches</dt>
              <dd>{status.counts.candidate_matches}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Approved placements</dt>
              <dd>{status.counts.approved_placements}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Teacher-only intervals</dt>
              <dd>{status.counts.teacher_intervals}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Validation</dt>
              <dd>{status.counts.validation}</dd>
            </div>
          </dl>
          {receipt.mode === "my_broll" && <p className="mt-4 text-sm text-teal">Every cutaway in this plan comes from your uploaded footage.</p>}
          {status.rematch_error && (
            <p className="mt-3 text-sm text-red-700" role="alert">
              Matching again failed: {status.rematch_error}
            </p>
          )}
          {receipt.mode === "my_broll" && onRematch && (
            <button type="button" className="mt-3 rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40" disabled={isRendering(render)} onClick={onRematch}>
              Analyse clips and match again
            </button>
          )}
          <RenderPanel
            receipt={receipt}
            counts={status.counts}
            planOk={Boolean(plan?.validation.ok)}
            render={render}
            starting={renderStarting}
            error={renderError}
            videoUrl={videoUrl}
            downloadUrl={downloadUrl}
            onVideoError={onVideoError}
            onRender={onRender}
            plan={plan}
          />
        </section>
      )}

      {transcript && (
        <section className="rounded-3xl border border-line bg-white p-6" aria-label="Transcript preview">
          <h2 className="font-serif text-2xl text-ink">Transcript</h2>
          <p className="mt-2 text-sm text-slate-600">
            {transcript.model || "Transcription"} · {transcript.segments.length} segments
            {transcript.reused ? " · reused for this file" : ""}
          </p>
          <ol className="mt-4 grid max-h-80 gap-3 overflow-auto">
            {transcript.segments.map((segment) => (
              <li key={segment.id} className="rounded-xl bg-slate-50 px-3 py-2 text-sm">
                <p className="text-slate-500">
                  {clock(segment.start)}–{clock(segment.end)}
                </p>
                <p className="mt-1 text-ink">{segment.original_text || segment.text}</p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {plan && (
        <section className="rounded-3xl border border-line bg-white p-6" aria-label="Editing plan preview">
          <h2 className="font-serif text-2xl text-ink">Timeline</h2>
          <p className="mt-2 text-sm text-slate-600">
            Picture decisions in time order. {teachers.length} teacher interval{teachers.length === 1 ? "" : "s"}, {cutaways.length} cutaway
            {cutaways.length === 1 ? "" : "s"}.
          </p>
          <ol className="mt-4 grid gap-3">
            {pictures.map((row) => (
              <DecisionCard key={row.decision_id} row={row} mode={receipt.mode} />
            ))}
          </ol>
          {plan.validation.ok ? (
            <p className="mt-4 text-sm text-teal">Validation passed.</p>
          ) : (
            <p className="mt-4 text-sm text-red-700" role="alert">
              {plan.validation.errors.join(" ")}
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function DecisionCard({ row, mode }: { row: PictureDecision; mode: SessionReceipt["mode"] }) {
  if (row.picture_role !== "show_broll") {
    return (
      <li className="rounded-xl border border-line px-3 py-3 text-sm">
        <p className="font-medium text-ink">Teacher on screen</p>
        <p className="mt-1 text-slate-600">
          {clock(row.start)}–{clock(row.end)}
        </p>
      </li>
    );
  }
  return (
    <li className="rounded-xl border border-teal/40 bg-teal/5 px-3 py-3 text-sm">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">{originLabel(row.origin, mode)}</p>
      <p className="mt-1 font-medium text-ink">{row.filename || "Clip"}</p>
      {row.description && <p className="mt-1 text-slate-700">{row.description}</p>}
      {row.speech_text && <p className="mt-2 text-slate-700">Matched speech: {row.speech_text}</p>}
      <dl className="mt-2 grid gap-1 text-slate-600 sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Timeline</dt>
          <dd>
            {clock(row.start)}–{clock(row.end)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Source interval</dt>
          <dd>
            {clock(row.source_in)}–{clock(row.source_out)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Relevance</dt>
          <dd>{typeof row.score === "number" ? row.score.toFixed(2) : "—"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Evidence</dt>
          <dd>{evidenceLabel(row.evidence)}</dd>
        </div>
      </dl>
      {row.explanation && <p className="mt-2 text-slate-700">Reason: {row.explanation}</p>}
    </li>
  );
}

export function SessionFollowup({ receipt, onReset }: { receipt: SessionReceipt; onReset: () => void }) {
  const [status, setStatus] = useState<SessionStatusBody | null>(null);
  const [transcript, setTranscript] = useState<TranscriptDocument | null>(null);
  const [plan, setPlan] = useState<EditingPlanDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [render, setRender] = useState<RenderStatusBody | null>(null);
  const [renderStarting, setRenderStarting] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [videoRenderId, setVideoRenderId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function fetchLinks(sessionId: string, renderId: string) {
    const links = await getVideoLinks(sessionId);
    setVideoUrl(absoluteUrl(links.url));
    setDownloadUrl(absoluteUrl(links.download_url));
    setVideoRenderId(renderId);
  }

  async function showRender(sessionId: string, next: RenderStatusBody) {
    setRender(next);
    const shown = next.status === "completed" ? next.render_id : next.last_completed?.render_id;
    if (shown && shown !== videoRenderId) await fetchLinks(sessionId, shown);
  }

  async function load(sessionId: string) {
    const next = await getSessionStatus(sessionId);
    setStatus(next.status === "prepared" ? null : next);
    if (next.status === "plan_ready") {
      const [words, ready, renderState] = await Promise.all([getTranscript(sessionId), getEditingPlan(sessionId), getRenderStatus(sessionId)]);
      setTranscript(words);
      setPlan(ready);
      await showRender(sessionId, renderState);
    }
  }

  useEffect(() => {
    if (receipt.status === "prepared") {
      setLoaded(true);
      return;
    }
    load(receipt.session_id)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "The session could not be loaded."))
      .finally(() => setLoaded(true));
  }, [receipt.session_id]);

  const stage = status?.status;
  useEffect(() => {
    if (!stage || stage === "plan_ready" || stage === "failed" || stage === "prepared") return;
    const timer = window.setInterval(() => {
      void load(receipt.session_id).catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Status could not be read.");
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [receipt.session_id, stage]);

  const renderStage = render?.status;
  useEffect(() => {
    if (!isRendering(render)) return;
    const timer = window.setInterval(() => {
      void getRenderStatus(receipt.session_id)
        .then((next) => showRender(receipt.session_id, next))
        .catch((caught: unknown) => setRenderError(caught instanceof Error ? caught.message : "Render status could not be read."));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [receipt.session_id, renderStage]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await startProcessing(receipt.session_id);
      await load(receipt.session_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Processing could not be started.");
    } finally {
      setStarting(false);
    }
  }

  async function planSaved(next: EditingPlanDocument) {
    setPlan(next);
    try {
      setStatus(await getSessionStatus(receipt.session_id));
    } catch {
      // The saved plan is already shown; the summary counts refresh on the next load.
    }
  }

  async function rematch() {
    setError(null);
    try {
      await rematchFootage(receipt.session_id);
      setStatus(await getSessionStatus(receipt.session_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Matching could not be started again.");
    }
  }

  async function renderVideo() {
    setRenderStarting(true);
    setRenderError(null);
    try {
      await startRender(receipt.session_id);
      await showRender(receipt.session_id, await getRenderStatus(receipt.session_id));
    } catch (caught) {
      setRenderError(caught instanceof Error ? caught.message : "Rendering could not be started.");
      void getRenderStatus(receipt.session_id).then((next) => showRender(receipt.session_id, next)).catch(() => undefined);
    } finally {
      setRenderStarting(false);
    }
  }

  if (!loaded) {
    return (
      <p className="text-mist" role="status">
        Loading the session…
      </p>
    );
  }

  return (
    <ProcessReview
      receipt={receipt}
      status={status}
      transcript={transcript}
      plan={plan}
      error={error}
      starting={starting}
      onStart={() => void start()}
      onRetry={() => void start()}
      onReset={onReset}
      render={render}
      renderStarting={renderStarting}
      renderError={renderError}
      videoUrl={videoUrl}
      downloadUrl={downloadUrl}
      onVideoError={() => {
        if (videoRenderId) void fetchLinks(receipt.session_id, videoRenderId).catch(() => undefined);
      }}
      onRender={() => void renderVideo()}
      onPlanSaved={(next) => void planSaved(next)}
      onRematch={() => void rematch()}
    />
  );
}
