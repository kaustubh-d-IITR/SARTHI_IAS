import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MediaFacts } from "../components/MediaFacts";
import { TitleField } from "../components/TitleField";
import { VideoField } from "../components/VideoField";
import { useBackend } from "../components/BackendStatus";
import { createSession, deleteUpload, uploadFile } from "../lib/api";
import { formatBytes } from "../lib/format";
import { canPrepareAuto } from "../lib/sessionGate";
import type { MediaRecord } from "../types";

export function AutoEditPage() {
  const { phase, health } = useBackend();
  const [record, setRecord] = useState<MediaRecord | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const limit = health ? formatBytes(health.configuration.max_file_bytes) : "the server limit";
  const formats = health?.configuration.allowed_extensions.join(", ") || "mp4, webm, mov, mkv, m4v";

  async function takeFile(file: File) {
    setError(null);
    setBusy(true);
    setProgress(1);
    const previous = record?.upload_id;
    let removed = !previous;
    try {
      if (previous) {
        await deleteUpload(previous);
        removed = true;
        setRecord(null);
      }
      const uploaded = await uploadFile("/api/uploads/raw-video", file, {}, setProgress);
      setRecord(uploaded);
      setProgress(null);
    } catch (caught) {
      if (removed) setRecord(null);
      setProgress(null);
      setError(caught instanceof Error ? caught.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function removeFile() {
    if (!record) return;
    setError(null);
    setBusy(true);
    try {
      await deleteUpload(record.upload_id);
      setRecord(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The file could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  async function prepare() {
    if (!record) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createSession({ mode: "auto_edit", raw_video_id: record.upload_id, broll: [], ...(title.trim() ? { title } : {}) });
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
        <h1 className="mt-3 font-serif text-4xl text-white">AI Auto-Edit</h1>
        <p className="mt-3 max-w-2xl text-mist">
          Upload one raw lesson. After you prepare the session, start AI processing. It transcribes the lesson and builds an editing plan from the existing footage pipeline. You review the plan, then choose Render Video.
        </p>
      </div>
      <section className="rounded-3xl bg-white p-6 text-ink shadow-[0_20px_50px_rgba(0,0,0,0.18)]">
        <h2 className="font-serif text-2xl">Raw lesson</h2>
        <p className="mt-2 text-sm text-slate-600">
          Accepted formats: {formats}. Maximum file size: {limit}. One lesson per session.
        </p>
        {phase === "offline" && (
          <p className="mt-3 text-sm text-red-700" role="alert">
            The studio API is offline, so uploads cannot be stored yet.
          </p>
        )}
        <div className="mt-4">
          <VideoField
            label={record ? "Replace the lesson video" : "Drop the raw lesson here, or browse"}
            hint="The original teacher recording. Do not upload B-roll on this page."
            disabled={busy || phase === "offline"}
            onFile={(file) => void takeFile(file)}
          />
        </div>
        <div className="mt-4">
          <MediaFacts record={record} progress={progress} />
        </div>
        <div className="mt-4">
          <TitleField value={title} onChange={setTitle} disabled={busy} />
        </div>
        {error && (
          <p className="mt-4 text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            className="rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!canPrepareAuto(record?.upload_id ?? null, busy)}
            onClick={() => void prepare()}
          >
            Prepare Video
          </button>
          <button
            type="button"
            className="rounded-full border border-line px-5 py-2.5 text-sm disabled:opacity-40"
            disabled={!record || busy}
            onClick={() => void removeFile()}
          >
            Remove file
          </button>
        </div>
        <p className="mt-4 text-sm text-slate-500">A successful upload means the lesson is ready for the next phase. It does not mean the video has been edited.</p>
      </section>
    </div>
  );
}
