import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { MediaFacts } from "../components/MediaFacts";
import { TitleField } from "../components/TitleField";
import { VideoField } from "../components/VideoField";
import { useBackend } from "../components/BackendStatus";
import { createSession, deleteUpload, uploadFile } from "../lib/api";
import { formatBytes, formatDuration } from "../lib/format";
import { canPrepareBroll } from "../lib/sessionGate";
import type { BrollDraft, MediaRecord } from "../types";

function blankEntry(): BrollDraft {
  return {
    key: window.crypto.randomUUID(),
    description: "",
    file: null,
    fileName: null,
    fileSize: null,
    upload: null,
    progress: null,
    error: null,
  };
}

export function MyBrollPage() {
  const { phase, health } = useBackend();
  const [raw, setRaw] = useState<MediaRecord | null>(null);
  const [rawProgress, setRawProgress] = useState<number | null>(null);
  const [entries, setEntries] = useState<BrollDraft[]>([blankEntry()]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const limit = health ? formatBytes(health.configuration.max_file_bytes) : "the server limit";
  const sessionLimit = health ? formatBytes(health.configuration.max_session_bytes) : "the server limit";
  const ready = canPrepareBroll(raw?.upload_id ?? null, entries, busy);
  const uploadedBytes = (raw?.size_bytes ?? 0) + entries.reduce((sum, entry) => sum + (entry.upload?.size_bytes ?? 0), 0);

  function patch(key: string, next: Partial<BrollDraft>) {
    setEntries((current) => current.map((entry) => (entry.key === key ? { ...entry, ...next } : entry)));
  }

  async function takeRaw(file: File) {
    setError(null);
    setBusy(true);
    setRawProgress(1);
    try {
      if (raw) await deleteUpload(raw.upload_id);
      const uploaded = await uploadFile("/api/uploads/raw-video", file, {}, setRawProgress);
      setRaw(uploaded);
      setRawProgress(null);
    } catch (caught) {
      setRaw(null);
      setRawProgress(null);
      setError(caught instanceof Error ? caught.message : "The lesson could not be uploaded.");
    } finally {
      setBusy(false);
    }
  }

  async function uploadClip(entry: BrollDraft, file: File) {
    const description = entry.description.trim();
    if (!description) {
      patch(entry.key, {
        file,
        fileName: file.name,
        fileSize: file.size,
        error: "Add a description before uploading this clip.",
      });
      return;
    }
    setBusy(true);
    setError(null);
    patch(entry.key, { file, fileName: file.name, fileSize: file.size, error: null, progress: 1 });
    try {
      if (entry.upload) await deleteUpload(entry.upload.upload_id);
      const uploaded = await uploadFile("/api/uploads/broll", file, { description }, (percent) => patch(entry.key, { progress: percent }));
      patch(entry.key, { upload: uploaded, progress: null, error: null, fileName: uploaded.filename, fileSize: uploaded.size_bytes });
    } catch (caught) {
      patch(entry.key, {
        upload: null,
        progress: null,
        error: caught instanceof Error ? caught.message : "The clip could not be uploaded.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function removeEntry(entry: BrollDraft) {
    setBusy(true);
    setError(null);
    try {
      if (entry.upload) await deleteUpload(entry.upload.upload_id);
      setEntries((current) => {
        const next = current.filter((item) => item.key !== entry.key);
        return next.length > 0 ? next : [blankEntry()];
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The clip could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  async function prepare() {
    if (!raw) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createSession({
        mode: "my_broll",
        raw_video_id: raw.upload_id,
        broll: entries.map((entry) => ({
          asset_id: entry.upload?.asset_id || entry.upload?.upload_id || "",
          description: entry.description.trim(),
        })),
        ...(title.trim() ? { title } : {}),
      });
      navigate(`/sessions/${created.session_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The session could not be prepared.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6">
      <div>
        <Link to="/" className="text-sm text-mist hover:text-white">
          Back to workflows
        </Link>
        <h1 className="mt-3 font-serif text-4xl text-white">Edit with My B-Roll</h1>
        <p className="mt-3 max-w-2xl text-mist">
          Upload the lesson and the clips you want used as full-screen cutaways. Describe what each clip shows. Online footage is not part of this workflow.
        </p>
      </div>

      <section className="rounded-3xl bg-white p-6 text-ink shadow-[0_20px_50px_rgba(0,0,0,0.18)]">
        <h2 className="font-serif text-2xl">Raw lesson</h2>
        <p className="mt-2 text-sm text-slate-600">Required. Per file up to {limit}. A session can hold up to {sessionLimit} in total.</p>
        {phase === "offline" && (
          <p className="mt-3 text-sm text-red-700" role="alert">
            The studio API is offline, so uploads cannot be stored yet.
          </p>
        )}
        <div className="mt-4">
          <VideoField
            label={raw ? "Replace the lesson video" : "Drop the raw lesson here, or browse"}
            hint="This is the teacher recording. Its audio is the narration the finished video keeps."
            disabled={busy || phase === "offline"}
            onFile={(file) => void takeRaw(file)}
          />
        </div>
        <div className="mt-4">
          <MediaFacts record={raw} progress={rawProgress} />
        </div>
      </section>

      <section className="rounded-3xl bg-white p-6 text-ink shadow-[0_20px_50px_rgba(0,0,0,0.18)]" aria-labelledby="broll-library">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="broll-library" className="font-serif text-2xl">
              Your B-roll library
            </h2>
            <p className="mt-2 text-sm text-slate-600">Each clip needs its own file and a one-line description.</p>
          </div>
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-full bg-teal px-4 py-2 text-sm text-white"
            onClick={() => setEntries((current) => [...current, blankEntry()])}
          >
            <Plus size={16} aria-hidden="true" />
            Add another B-roll
          </button>
        </div>
        <div className="mt-5 grid gap-4">
          {entries.map((entry, index) => {
            const number = index + 1;
            return (
              <article key={entry.key} className="rounded-2xl border border-line p-4">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="font-medium">B-roll {number}</h3>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-sm text-red-700"
                    aria-label={`Remove B-roll ${number}`}
                    onClick={() => void removeEntry(entry)}
                    disabled={busy}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                    Remove
                  </button>
                </div>
                <label className="mt-3 block text-sm" htmlFor={`${entry.key}-description`}>
                  What this clip shows
                  <textarea
                    id={`${entry.key}-description`}
                    className="mt-1 min-h-20 w-full rounded-xl border border-line px-3 py-2"
                    placeholder="Describe exactly what this clip shows, e.g. A student watching a lecture on a smartphone."
                    value={entry.description}
                    onChange={(event) => patch(entry.key, { description: event.target.value })}
                  />
                </label>
                <div className="mt-3">
                  <VideoField
                    label={entry.fileName ? "Replace this clip" : "Drop a B-roll clip here, or browse"}
                    hint={entry.fileName ? `${entry.fileName}${entry.fileSize ? ` · ${formatBytes(entry.fileSize)}` : ""}` : `Video up to ${limit}`}
                    disabled={busy || phase === "offline"}
                    onFile={(file) => void uploadClip(entry, file)}
                  />
                </div>
                {!entry.upload && (
                  <button
                    type="button"
                    className="mt-3 rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40"
                    disabled={!entry.file || !entry.description.trim() || busy || phase === "offline"}
                    onClick={() => {
                      if (entry.file) void uploadClip(entry, entry.file);
                    }}
                  >
                    Upload clip
                  </button>
                )}
                <div className="mt-3">
                  <MediaFacts record={entry.upload} progress={entry.progress} />
                </div>
                <p className="mt-2 text-sm text-slate-600">
                  {entry.upload ? "Uploaded" : entry.fileName ? "Selected, not uploaded yet" : "No file yet"}
                  {entry.description.trim() ? "" : " · Description missing"}
                </p>
                {entry.error && (
                  <p className="mt-2 text-sm text-red-700" role="alert">
                    {entry.error}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </section>

      <section className="rounded-3xl border border-white/10 bg-panel p-6 text-mist" aria-label="Asset summary">
        <h2 className="font-serif text-2xl text-white">Asset summary</h2>
        <div className="mt-4 rounded-2xl bg-white p-4">
          <TitleField value={title} onChange={setTitle} disabled={busy} />
        </div>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-mist/70">Lesson</dt>
            <dd>{raw ? `${raw.filename} · ${formatDuration(raw.duration_seconds)}` : "Not uploaded"}</dd>
          </div>
          <div>
            <dt className="text-mist/70">B-roll clips</dt>
            <dd>
              {entries.length} in the form · {entries.filter((entry) => entry.upload).length} uploaded
            </dd>
          </div>
          <div>
            <dt className="text-mist/70">Total uploaded size</dt>
            <dd>{formatBytes(uploadedBytes)}</dd>
          </div>
          <div>
            <dt className="text-mist/70">Ready to prepare</dt>
            <dd>{ready ? "Yes" : "No — finish the lesson, every clip, and every description"}</dd>
          </div>
        </dl>
        {error && (
          <p className="mt-4 text-sm text-red-300" role="alert">
            {error}
          </p>
        )}
        <button
          type="button"
          className="mt-5 rounded-full bg-white px-5 py-2.5 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!ready || phase === "offline"}
          onClick={() => void prepare()}
        >
          Prepare Editing Session
        </button>
      </section>
    </div>
  );
}
