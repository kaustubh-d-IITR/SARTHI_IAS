import type { MediaRecord } from "../types";
import { formatBytes, formatDuration } from "../lib/format";

export function MediaFacts({ record, progress }: { record: MediaRecord | null; progress: number | null }) {
  if (!record && progress === null) return null;
  return (
    <div className="rounded-2xl border border-line bg-paper p-4 text-sm">
      {progress !== null && progress < 100 && (
        <div>
          <p role="status">Uploading {progress}%</p>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-mist" aria-hidden="true">
            <div className="h-full bg-teal" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}
      {record && (
        <dl className="grid gap-2 sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">File</dt>
            <dd className="font-medium text-ink">{record.filename}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Size</dt>
            <dd>{formatBytes(record.size_bytes)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Duration</dt>
            <dd>{formatDuration(record.duration_seconds)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Picture</dt>
            <dd>{record.width && record.height ? `${record.width}×${record.height}` : "Not available"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Check</dt>
            <dd>Valid video. Not edited yet.</dd>
          </div>
        </dl>
      )}
    </div>
  );
}
