import { useRef } from "react";
import type { Placement, StudioSettings } from "../types";

type Row = Placement & { key: string };

type Props = {
  duration: number;
  rows: Row[];
  captions: { start: number; end: number }[];
  settings?: StudioSettings;
  outroSeconds?: number;
  selected: string | null;
  disabled: boolean;
  onSelect: (key: string) => void;
  onMove: (row: Row, start: number, end: number) => void;
};

const TRACK = "relative h-8 rounded-md bg-slate-100";

function pct(value: number, total: number): string {
  return `${Math.max(0, Math.min(100, (value / total) * 100))}%`;
}

/** The plan drawn to scale. B-roll blocks can be dragged (move) or resized from the right edge. */
export function Timeline({ duration, rows, captions, settings, outroSeconds = 8, selected, disabled, onSelect, onMove }: Props) {
  const area = useRef<HTMLDivElement | null>(null);
  const total = duration + outroSeconds;
  const approved = rows.filter((row) => row.approval !== "rejected").sort((a, b) => a.speech_start - b.speech_start);

  function drag(event: React.PointerEvent, row: Row, mode: "move" | "resize") {
    if (disabled || !area.current) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(row.key);
    const width = area.current.getBoundingClientRect().width;
    const startX = event.clientX;
    const original = { start: row.speech_start, end: row.speech_end };
    const clip = row.clip_duration || original.end - original.start;
    const move = (next: PointerEvent) => {
      const delta = Math.round((((next.clientX - startX) / width) * total) * 10) / 10;
      if (mode === "move") {
        const length = original.end - original.start;
        const start = Math.max(0, Math.min(duration - length, original.start + delta));
        onMove(row, start, start + length);
      } else {
        const end = Math.max(original.start + 1, Math.min(duration, original.start + clip, original.end + delta));
        onMove(row, original.start, end);
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const teacher: { start: number; end: number }[] = [];
  let cursor = 0;
  for (const row of approved) {
    if (row.speech_start > cursor + 0.05) teacher.push({ start: cursor, end: row.speech_start });
    cursor = Math.max(cursor, row.speech_end);
  }
  if (duration > cursor + 0.05) teacher.push({ start: cursor, end: duration });

  const effects = settings ? Object.entries(settings.effects).filter(([, value]) => value.enabled) : [];
  const intro = settings?.intro_seconds || 3;
  const ticks = Array.from({ length: Math.floor(total / 5) + 1 }, (_, k) => k * 5);

  return (
    <div className="mt-4 rounded-2xl border border-line p-4" aria-label="Timeline">
      <div className="grid grid-cols-[5.5rem_1fr] items-center gap-x-3 gap-y-2 text-xs">
        <span />
        <div className="relative h-4 text-[10px] text-slate-500">
          {ticks.map((tick) => (
            <span key={tick} className="absolute -translate-x-1/2" style={{ left: pct(tick, total) }}>
              {tick}s
            </span>
          ))}
        </div>

        <span className="font-medium text-slate-700">Picture</span>
        <div ref={area} className={TRACK}>
          {teacher.map((part, k) => (
            <div key={k} className="absolute inset-y-1 rounded bg-slate-300" style={{ left: pct(part.start, total), width: pct(part.end - part.start, total) }} title={`Teacher ${part.start.toFixed(1)}–${part.end.toFixed(1)} s`} />
          ))}
          {approved.map((row) => (
            <div
              key={row.key}
              role="button"
              tabIndex={0}
              aria-label={`${row.filename} from ${row.speech_start.toFixed(2)} to ${row.speech_end.toFixed(2)} seconds`}
              onPointerDown={(event) => drag(event, row, "move")}
              onKeyDown={(event) => {
                if (disabled) return;
                const length = row.speech_end - row.speech_start;
                if (event.key === "ArrowLeft") onMove(row, Math.max(0, row.speech_start - 0.1), Math.max(0, row.speech_start - 0.1) + length);
                if (event.key === "ArrowRight") onMove(row, Math.min(duration - length, row.speech_start + 0.1), Math.min(duration - length, row.speech_start + 0.1) + length);
              }}
              className={`absolute inset-y-0.5 cursor-grab overflow-hidden rounded border text-[10px] leading-7 text-white ${selected === row.key ? "border-amber-300 bg-teal ring-2 ring-amber-300" : "border-teal bg-teal/85"}`}
              style={{ left: pct(row.speech_start, total), width: pct(row.speech_end - row.speech_start, total) }}
              title={`${row.filename}: ${row.matched_phrase || row.speech_text || ""}`}
            >
              <span className="pointer-events-none truncate px-1">{row.filename}</span>
              <span onPointerDown={(event) => drag(event, row, "resize")} className="absolute inset-y-0 right-0 w-2 cursor-ew-resize bg-white/40" aria-hidden />
            </div>
          ))}
          <div className="absolute inset-y-1 rounded bg-slate-500/70" style={{ left: pct(duration, total), width: pct(outroSeconds, total) }} title="Outro" />
        </div>

        <span className="font-medium text-slate-700">Captions</span>
        <div className={TRACK}>
          {captions.map((caption, k) => (
            <div key={k} className="absolute inset-y-2 rounded-sm bg-amber-300/80" style={{ left: pct(caption.start, total), width: pct(Math.max(0.05, caption.end - caption.start), total) }} />
          ))}
        </div>

        <span className="font-medium text-slate-700">Music</span>
        <div className={TRACK}>
          {settings?.music.source !== "none" && (
            <div className="absolute inset-y-2 rounded-sm bg-indigo-300" style={{ left: 0, width: pct(settings?.music.into_outro === false ? duration : total, total) }} title="Music bed" />
          )}
          <span className="absolute left-2 top-1.5 text-[10px] text-indigo-900">
            {settings ? (settings.music.source === "none" ? "No music" : `${settings.music.source === "library" ? "Library track" : settings.music.source === "upload" ? "Your music" : "Default track"} · ${Math.round(settings.music.volume * 100)}%`) : "Default track"}
          </span>
        </div>

        <span className="font-medium text-slate-700">Effects</span>
        <div className={TRACK}>
          {effects.map(([name, value], k) => {
            const ranges = value.scope === "intro" ? [{ start: 0, end: intro }] : value.scope === "broll" ? approved.map((row) => ({ start: row.speech_start, end: row.speech_end })) : [{ start: 0, end: duration }];
            return ranges.map((range, r) => (
              <div key={`${name}-${r}`} className="absolute h-1.5 rounded-full bg-fuchsia-400" style={{ left: pct(range.start, total), width: pct(range.end - range.start, total), top: 4 + (k % 4) * 6 }} title={`${name.replaceAll("_", " ")} (${value.scope})`} />
            ));
          })}
          {effects.length === 0 && <span className="absolute left-2 top-1.5 text-[10px] text-slate-500">No effects</span>}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">Drag a B-roll block to move it, drag its right edge to change its length, or select it and use the arrow keys. Changes are saved with “Save changes and validate”.</p>
    </div>
  );
}
