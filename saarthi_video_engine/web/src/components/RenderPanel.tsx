import { useState } from "react";
import type { EditingPlanDocument, ProcessingCounts, RenderStatusBody, SessionReceipt } from "../types";

export const RENDER_STAGES = [
  { id: "render_queued", label: "Queued" },
  { id: "preparing", label: "Preparing approved media" },
  { id: "composing", label: "Writing the composition" },
  { id: "rendering", label: "Rendering with HyperFrames" },
  { id: "verifying", label: "Checking narration and format" },
  { id: "assembling_outro", label: "Appending the outro" },
] as const;

const ACTIVE = new Set<string>(RENDER_STAGES.map((item) => item.id));

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}s`;
}

export function isRendering(render: RenderStatusBody | null): boolean {
  return Boolean(render && ACTIVE.has(render.status));
}

function Finished({
  render,
  videoUrl,
  title,
  downloadUrl = null,
  onVideoError,
}: {
  render: RenderStatusBody;
  videoUrl: string | null;
  title: string;
  downloadUrl?: string | null;
  onVideoError?: () => void;
}) {
  const audio = render.audio_verification;
  return (
    <section className="mt-5 rounded-2xl border border-teal/30 bg-teal/5 p-4" aria-label="Rendered video">
      <h3 className="font-serif text-2xl text-ink">{title}</h3>
      <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Render ID</dt>
          <dd className="break-all">{render.render_id}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Duration</dt>
          <dd>{seconds(render.metadata?.duration_seconds)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Resolution</dt>
          <dd>
            {render.metadata?.width}×{render.metadata?.height}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Frame rate</dt>
          <dd>{render.metadata?.frame_rate} fps</dd>
        </div>
        <div>
          <dt className="text-slate-500">Audio verification</dt>
          <dd>
            {audio?.status === "passed" ? "Passed" : audio?.status || "—"}
            {typeof audio?.correlation === "number" ? ` · narration correlation ${audio.correlation.toFixed(3)}` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Outro</dt>
          <dd>{render.outro?.status === "assembled" ? `Appended · ${seconds(render.outro.duration_seconds)}` : render.outro?.status || "—"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Plan version</dt>
          <dd className="break-all">{render.plan_version_id || "—"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Title</dt>
          <dd>{render.title?.text || "No title"}</dd>
        </div>
      </dl>
      {render.quality && (
        <details className="mt-3 rounded-xl bg-slate-50 px-3 py-2 text-sm" open={render.quality.status !== "passed"}>
          <summary className="cursor-pointer">
            Quality check: {render.quality.status === "passed" ? "all checks passed" : `${render.quality.warnings} warning${render.quality.warnings === 1 ? "" : "s"}`}
          </summary>
          <ul className="mt-2 grid gap-1">
            {render.quality.checks.map((check, index) => (
              <li key={`${check.name}-${index}`} className={check.status === "passed" ? "text-slate-600" : check.status === "warning" ? "text-amber-800" : "text-red-700"}>
                {check.status === "passed" ? "✓" : check.status === "warning" ? "!" : "✗"} {check.detail}
              </li>
            ))}
          </ul>
        </details>
      )}
      {videoUrl ? (
        <>
          <video className="mt-4 max-h-[70vh] w-full rounded-xl bg-black" controls playsInline preload="metadata" src={videoUrl} aria-label="Rendered video preview" onError={onVideoError} />
          <a
            className="mt-4 inline-flex rounded-full bg-ink px-5 py-2.5 text-sm text-white"
            href={downloadUrl || videoUrl}
            download={`saarthi-video-${(render.render_id || "render").slice(0, 8)}.mp4`}
          >
            Download video
          </a>
        </>
      ) : (
        <p className="mt-4 text-sm text-slate-600">Loading the video preview…</p>
      )}
    </section>
  );
}

export function RenderPanel({
  receipt,
  counts,
  planOk,
  render,
  starting,
  error,
  videoUrl,
  onRender,
  plan = null,
  downloadUrl = null,
  onVideoError,
}: {
  receipt: SessionReceipt;
  counts: ProcessingCounts;
  planOk: boolean;
  render: RenderStatusBody | null;
  starting: boolean;
  error: string | null;
  videoUrl: string | null;
  onRender: () => void;
  plan?: EditingPlanDocument | null;
  downloadUrl?: string | null;
  onVideoError?: () => void;
}) {
  const currentVersion = plan?.plan_version?.version_id;
  const renderedOlder = Boolean(render?.status === "completed" && render.plan_version_id && currentVersion && render.plan_version_id !== currentVersion);
  const [confirming, setConfirming] = useState(false);
  const status = render?.status || "not_started";
  const rendering = isRendering(render);
  const current = RENDER_STAGES.findIndex((item) => item.id === status);
  const canStart = planOk && !rendering && !starting;

  return (
    <div className="mt-5">
      {status === "completed" && render && <Finished render={render} videoUrl={videoUrl} downloadUrl={downloadUrl} onVideoError={onVideoError} title="Your video is ready" />}

      {rendering && render && (
        <div className="rounded-2xl bg-slate-50 p-4" aria-live="polite">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">Rendering</p>
          <p className="mt-1 font-medium text-ink">{render.message || render.stage}</p>
          <ol className="mt-4 grid gap-2" aria-label="Rendering stages">
            {RENDER_STAGES.map((item, index) => {
              const state = index < current ? "Done" : index === current ? "Current" : "Waiting";
              return (
                <li
                  key={item.id}
                  className="flex justify-between rounded-xl bg-white px-3 py-2 text-sm"
                  aria-current={index === current ? "step" : undefined}
                >
                  <span>{item.label}</span>
                  <span className="text-slate-500">{state}</span>
                </li>
              );
            })}
          </ol>
          <p className="mt-3 text-sm text-slate-500">Render ID {render.render_id}</p>
        </div>
      )}

      {status === "failed" && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-4">
          <p className="font-medium text-red-800">Rendering failed</p>
          <p className="mt-1 text-sm text-red-700" role="alert">
            {render?.error || "Rendering failed."}
          </p>
          <p className="mt-1 text-sm text-red-700">The approved editing plan is unchanged. Retrying starts a new render.</p>
        </div>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {renderedOlder && (
        <p className="mt-3 text-sm text-amber-800">
          The plan changed after this video was rendered. The video above stays available; rendering again uses the new plan version.
        </p>
      )}

      {!rendering && (status !== "completed" || renderedOlder) && planOk && (
        <div className="mt-4 rounded-2xl bg-slate-50 p-4">
          {!confirming ? (
            <button
              type="button"
              className="rounded-full bg-ink px-6 py-3 text-sm font-medium text-white disabled:opacity-40"
              disabled={!canStart}
              onClick={() => setConfirming(true)}
            >
              {status === "failed" ? "Retry render" : renderedOlder ? "Render the new version" : "Render Video"}
            </button>
          ) : (
            <section aria-label="Confirm render">
              <h3 className="font-medium text-ink">Confirm this render</h3>
              <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">Workflow</dt>
                  <dd>{receipt.mode === "auto_edit" ? "AI Auto-Edit" : "Edit with My B-Roll"}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Raw lesson</dt>
                  <dd>{receipt.raw_video.filename}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Lesson duration</dt>
                  <dd>{seconds(receipt.raw_video.duration_seconds)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Transcript segments</dt>
                  <dd>{counts.segments}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Approved B-roll placements</dt>
                  <dd>{counts.approved_placements}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Teacher-only intervals</dt>
                  <dd>{counts.teacher_intervals}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Plan validation</dt>
                  <dd>{counts.validation === "passed" ? "Passed" : counts.validation}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Title</dt>
                  <dd>{plan?.title?.status === "user_confirmed" ? plan.title.text : "No title — the title line stays empty"}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Plan version</dt>
                  <dd>{plan?.plan_version ? `Version ${plan.plan_version.number}` : "—"}</dd>
                </div>
              </dl>
              <p className="mt-3 text-sm text-slate-600">
                The existing renderer keeps the teacher narration, captions, music, logo, and outro. Rendering can take several minutes.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  className="rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:opacity-40"
                  disabled={!canStart}
                  onClick={() => {
                    setConfirming(false);
                    onRender();
                  }}
                >
                  Confirm and render
                </button>
                <button type="button" className="rounded-full border border-line px-4 py-2 text-sm" onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </div>
            </section>
          )}
        </div>
      )}

      {status !== "completed" && render?.last_completed && (
        <Finished render={render.last_completed} videoUrl={videoUrl} downloadUrl={downloadUrl} onVideoError={onVideoError} title="Previous video" />
      )}
    </div>
  );
}
