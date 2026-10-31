import { useEffect, useMemo, useRef, useState } from "react";
import { getMusicLibrary, musicPreviewUrl, savePlanEdits, uploadMusic, uploadPreviewUrl } from "../lib/api";
import type { EditingPlanDocument, EffectName, MusicTrack, PlacementEdit, SettingsChange, StudioSettings } from "../types";

type Props = {
  sessionId: string;
  plan: EditingPlanDocument;
  disabled: boolean;
  onSaved: (plan: EditingPlanDocument) => void;
};

const EFFECTS: { name: EffectName; label: string; scopes: string[]; min: number; max: number; hint: string }[] = [
  { name: "cinematic", label: "Cinematic grade", scopes: ["all", "teacher", "broll"], min: 0, max: 1, hint: "Slightly richer contrast, softer colour." },
  { name: "warm", label: "Warm filter", scopes: ["all", "teacher", "broll"], min: 0, max: 1, hint: "Golden tone." },
  { name: "cool", label: "Cool filter", scopes: ["all", "teacher", "broll"], min: 0, max: 1, hint: "Bluish tone." },
  { name: "black_and_white", label: "Black and white", scopes: ["all", "teacher", "broll"], min: 0, max: 1, hint: "Removes colour." },
  { name: "contrast", label: "Contrast", scopes: ["all", "teacher", "broll"], min: -1, max: 1, hint: "Below 0 is flatter, above 0 is punchier." },
  { name: "saturation", label: "Saturation", scopes: ["all", "teacher", "broll"], min: -1, max: 1, hint: "Below 0 is muted, above 0 is vivid." },
  { name: "brightness", label: "Brightness", scopes: ["all", "teacher", "broll"], min: -1, max: 1, hint: "Below 0 is darker, above 0 is brighter." },
  { name: "vignette", label: "Vignette", scopes: ["all", "intro", "broll"], min: 0, max: 1, hint: "Darkens the corners." },
  { name: "glitter", label: "Glitter", scopes: ["intro", "all", "broll"], min: 0, max: 1, hint: "Small gold points around the edges, away from the face and captions." },
  { name: "sparkle", label: "Subtle sparkle", scopes: ["intro", "all", "broll"], min: 0, max: 1, hint: "A few white stars twinkling at the edges." },
  { name: "background_blur", label: "Background blur", scopes: ["all"], min: 0, max: 1, hint: "How soft the blurred fill behind the teacher is." },
  { name: "zoom", label: "Slow zoom on teacher", scopes: ["teacher"], min: 0, max: 1, hint: "A gentle push-in during each teacher shot." },
];

const SCOPE_LABEL: Record<string, string> = { all: "whole video", teacher: "teacher only", broll: "B-roll only", intro: "intro only" };

/** The same CSS the renderer writes into the composition (hyperframes_adapter/studio_layout.py). */
export function filterCss(settings: StudioSettings, scope: "all" | "teacher" | "broll"): string {
  const parts: string[] = [];
  for (const [name, effect] of Object.entries(settings.effects) as [EffectName, StudioSettings["effects"][EffectName]][]) {
    if (!effect.enabled || effect.scope !== scope) continue;
    const i = effect.intensity;
    if (name === "cinematic") parts.push(`contrast(${(1 + 0.15 * i).toFixed(3)}) saturate(${(1 - 0.15 * i).toFixed(3)}) sepia(${(0.12 * i).toFixed(3)}) brightness(${(1 - 0.03 * i).toFixed(3)})`);
    if (name === "warm") parts.push(`sepia(${(0.3 * i).toFixed(3)}) saturate(${(1 + 0.15 * i).toFixed(3)}) hue-rotate(${(-8 * i).toFixed(1)}deg)`);
    if (name === "cool") parts.push(`saturate(${(1 - 0.1 * i).toFixed(3)}) hue-rotate(${(14 * i).toFixed(1)}deg) brightness(${(1 + 0.02 * i).toFixed(3)})`);
    if (name === "black_and_white") parts.push(`grayscale(${i.toFixed(3)})`);
    if (name === "contrast") parts.push(`contrast(${(1 + 0.5 * i).toFixed(3)})`);
    if (name === "saturation") parts.push(`saturate(${Math.max(0, 1 + 0.8 * i).toFixed(3)})`);
    if (name === "brightness") parts.push(`brightness(${(1 + 0.35 * i).toFixed(3)})`);
  }
  return parts.join(" ");
}

function currentPlacements(plan: EditingPlanDocument): PlacementEdit[] {
  return [...(plan.placements || []), ...(plan.rejected_placements || [])].map((row) => ({
    asset_id: row.asset_id,
    speech_start: row.speech_start,
    speech_end: row.speech_end,
    source_in: row.source_in,
    source_out: row.source_out,
    approval: row.approval === "rejected" ? "rejected" : "approved",
  }));
}

function diff(saved: StudioSettings, draft: StudioSettings): SettingsChange {
  const change: Record<string, Record<string, unknown>> = {};
  for (const section of ["framing", "broll", "transitions", "captions", "music", "outro"] as const) {
    for (const [key, value] of Object.entries(draft[section])) {
      if (JSON.stringify((saved[section] as Record<string, unknown>)[key]) !== JSON.stringify(value)) {
        change[section] = { ...(change[section] || {}), [key]: value };
      }
    }
  }
  for (const [name, value] of Object.entries(draft.effects)) {
    if (JSON.stringify(saved.effects[name as EffectName]) !== JSON.stringify(value)) {
      change.effects = { ...(change.effects || {}), [name]: value };
    }
  }
  return change as SettingsChange;
}

function Slider({ label, value, min, max, step, onChange, disabled, format }: { label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void; disabled: boolean; format?: (value: number) => string }) {
  return (
    <label className="block text-sm">
      <span className="flex justify-between text-slate-700">
        <span>{label}</span>
        <span className="tabular-nums text-slate-500">{format ? format(value) : value}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} className="mt-1 w-full accent-teal-700" aria-label={label} />
    </label>
  );
}

function Preview({ plan, settings }: { plan: EditingPlanDocument; settings: StudioSettings }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    uploadPreviewUrl(plan.raw_video.upload_id)
      .then((link) => active && setUrl(link))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [plan.raw_video.upload_id]);
  const all = filterCss(settings, "all");
  const teacher = filterCss(settings, "teacher");
  const vignette = settings.effects.vignette;
  const glitter = settings.effects.glitter.enabled || settings.effects.sparkle.enabled;
  const caption = settings.captions;
  const height = settings.framing.mode === "fill" ? 100 : settings.framing.mode === "fit" ? 32 : settings.framing.teacher_height * 100;
  const focus = settings.framing.focus_x === null ? 50 : settings.framing.focus_x * 100;
  const captionStyle: React.CSSProperties =
    caption.style === "box"
      ? { background: "rgba(10,10,10,0.68)", borderRadius: 6, padding: "2px 8px" }
      : caption.style === "outline"
        ? { WebkitTextStroke: "1px #000", paintOrder: "stroke fill" }
        : { textShadow: "0 2px 6px rgba(0,0,0,0.85)" };
  return (
    <figure className="mx-auto w-48 shrink-0">
      <div className="relative aspect-[9/16] overflow-hidden rounded-xl bg-[#0b0b0d]" style={{ filter: all || undefined }} aria-label="Live preview of the look">
        {url && !failed ? (
          <>
            {height < 100 && <video src={url} muted loop autoPlay playsInline className="absolute inset-0 h-full w-full scale-105 object-cover blur-md brightness-75" aria-hidden />}
            <video
              src={url}
              muted
              loop
              autoPlay
              playsInline
              onError={() => setFailed(true)}
              className="absolute left-0 w-full object-cover"
              style={{ height: `${height}%`, top: `${(100 - height) * 0.42}%`, objectPosition: `${focus}% ${settings.framing.focus_y * 100}%`, filter: teacher || undefined }}
            />
          </>
        ) : (
          <div className="absolute inset-0 grid place-items-center px-3 text-center text-xs text-slate-400">{failed ? "Preview unavailable" : "Loading preview…"}</div>
        )}
        {vignette.enabled && <div className="pointer-events-none absolute inset-0" style={{ background: `radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 52%, rgba(0,0,0,${(0.25 + 0.45 * vignette.intensity).toFixed(2)}) 100%)` }} />}
        {glitter && (
          <div className="pointer-events-none absolute inset-0" aria-hidden>
            {[8, 18, 82, 90, 12, 86].map((x, k) => (
              <span key={k} className="absolute h-1.5 w-1.5 animate-pulse rounded-full bg-amber-200 shadow-[0_0_6px_#fde68a]" style={{ left: `${x}%`, top: `${[10, 85, 14, 78, 50, 40][k]}%` }} />
            ))}
          </div>
        )}
        <p
          className="absolute inset-x-2 text-center font-extrabold leading-tight"
          style={{ top: `${caption.position * 100}%`, transform: "translateY(-50%)", fontSize: caption.size / 3.6, color: caption.color, textTransform: caption.uppercase ? "uppercase" : "none" }}
        >
          <span style={captionStyle}>
            na hi <span style={{ color: caption.highlight }}>maine</span> newspaper
          </span>
        </p>
      </div>
      <figcaption className="mt-2 text-center text-xs text-slate-500">Live preview of colour, framing, vignette and captions. The render uses the same values.</figcaption>
    </figure>
  );
}

export function StudioControls({ sessionId, plan, disabled, onSaved }: Props) {
  const saved = plan.settings;
  const [draft, setDraft] = useState<StudioSettings | null>(saved ? structuredClone(saved) : null);
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [musicName, setMusicName] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setDraft(plan.settings ? structuredClone(plan.settings) : null);
    setError(null);
  }, [plan]);

  useEffect(() => {
    getMusicLibrary()
      .then((body) => setTracks(body.tracks))
      .catch(() => setTracks([]));
    return () => audio.current?.pause();
  }, []);

  const changes = useMemo(() => (saved && draft ? diff(saved, draft) : {}), [saved, draft]);
  const dirty = Object.keys(changes).length > 0;

  if (!saved || !draft) {
    return (
      <section className="rounded-3xl border border-line bg-white p-6" aria-label="Look and sound">
        <h2 className="font-serif text-2xl text-ink">Look and sound</h2>
        <p className="mt-2 text-sm text-slate-600">This plan was made before framing, music and effect controls existed, so it renders with the original layout. Save any cutaway change or ask the assistant for a change to switch it to the new controls.</p>
        <button
          type="button"
          className="mt-3 rounded-full bg-ink px-4 py-2 text-sm text-white disabled:opacity-40"
          disabled={disabled || saving || !plan.plan_version}
          onClick={async () => {
            if (!plan.plan_version) return;
            setSaving(true);
            try {
              onSaved(await savePlanEdits(sessionId, { base_version_id: plan.plan_version.version_id, placements: currentPlacements(plan), settings: { framing: { mode: "balanced" } } }));
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "Could not switch the plan.");
            } finally {
              setSaving(false);
            }
          }}
        >
          Use the new look and sound controls
        </button>
        {error && <p className="mt-2 text-sm text-red-700" role="alert">{error}</p>}
      </section>
    );
  }

  const set = <S extends keyof StudioSettings>(section: S, patch: Partial<StudioSettings[S]>) => setDraft((current) => (current ? { ...current, [section]: { ...(current[section] as object), ...patch } } : current));
  const setEffect = (name: EffectName, patch: Partial<StudioSettings["effects"][EffectName]>) =>
    setDraft((current) => (current ? { ...current, effects: { ...current.effects, [name]: { ...current.effects[name], ...patch } } } : current));

  async function preview(track: MusicTrack) {
    audio.current?.pause();
    if (playing === track.track_id) {
      setPlaying(null);
      return;
    }
    try {
      const url = await musicPreviewUrl(track.track_id);
      audio.current = new Audio(url);
      audio.current.volume = Math.min(1, draft!.music.in_pauses * draft!.music.volume * 2.5);
      await audio.current.play();
      setPlaying(track.track_id);
      audio.current.onended = () => setPlaying(null);
    } catch {
      setError("That track could not be played here.");
    }
  }

  async function onMusicFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setUploadPercent(0);
    try {
      const record = await uploadMusic(file, setUploadPercent);
      setMusicName(record.filename);
      set("music", { source: "upload", upload_id: record.upload_id });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The music file could not be uploaded.");
    } finally {
      setUploadPercent(null);
    }
  }

  async function save() {
    if (!plan.plan_version) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(await savePlanEdits(sessionId, { base_version_id: plan.plan_version.version_id, placements: currentPlacements(plan), settings: changes }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The settings could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  const off = disabled || saving;
  const music = draft.music;
  const categories = Array.from(new Set(tracks.map((track) => track.category)));

  return (
    <section className="rounded-3xl border border-line bg-white p-6" aria-label="Look and sound">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-serif text-2xl text-ink">Look and sound</h2>
        <p className="text-sm text-slate-500">Saved with plan version {plan.plan_version?.number}</p>
      </div>
      <div className="mt-4 flex flex-col gap-6 lg:flex-row">
        <Preview plan={plan} settings={draft} />
        <div className="grid flex-1 gap-6 md:grid-cols-2">
          <fieldset className="grid gap-3" disabled={off}>
            <legend className="font-medium text-ink">Framing</legend>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Framing">
              {(["fill", "balanced", "fit"] as const).map((mode) => (
                <button key={mode} type="button" role="radio" aria-checked={draft.framing.mode === mode} onClick={() => set("framing", { mode })} className={`rounded-full border px-3 py-1 text-sm ${draft.framing.mode === mode ? "border-teal bg-teal text-white" : "border-line"}`}>
                  {mode === "fill" ? "Fill the screen" : mode === "balanced" ? "Balanced" : "Whole frame"}
                </button>
              ))}
            </div>
            {draft.framing.mode === "balanced" && (
              <Slider label="Teacher height" value={draft.framing.teacher_height} min={0.3} max={1} step={0.02} onChange={(value) => set("framing", { teacher_height: value })} disabled={off} format={(value) => `${Math.round(value * 100)}% of the screen`} />
            )}
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.framing.focus_x === null} onChange={(event) => set("framing", { focus_x: event.target.checked ? null : 0.5 })} />
              Find the teacher automatically
            </label>
            {draft.framing.focus_x !== null && <Slider label="Horizontal focus" value={draft.framing.focus_x} min={0} max={1} step={0.01} onChange={(value) => set("framing", { focus_x: value })} disabled={off} format={(value) => `${Math.round(value * 100)}% from left`} />}
            <label className="text-sm">
              B-roll
              <select className="ml-2 rounded-lg border border-line px-2 py-1" value={draft.broll.fit} onChange={(event) => set("broll", { fit: event.target.value as StudioSettings["broll"]["fit"] })}>
                <option value="cover">Full screen</option>
                <option value="contain_blur">Whole clip over a blurred copy</option>
              </select>
            </label>
            <label className="text-sm">
              Transitions
              <select className="ml-2 rounded-lg border border-line px-2 py-1" value={draft.transitions.style} onChange={(event) => set("transitions", { style: event.target.value as "cut" | "fade" })}>
                <option value="cut">Clean cuts</option>
                <option value="fade">Short fade</option>
              </select>
            </label>
            {draft.transitions.style === "fade" && <Slider label="Fade length" value={draft.transitions.seconds} min={0.1} max={0.6} step={0.05} onChange={(value) => set("transitions", { seconds: value })} disabled={off} format={(value) => `${value.toFixed(2)} s`} />}
            <label className="text-sm">
              Outro
              <select className="ml-2 rounded-lg border border-line px-2 py-1" value={draft.outro.fill} onChange={(event) => set("outro", { fill: event.target.value as "blur" | "black" })}>
                <option value="blur">Fill 9:16 with a blurred copy</option>
                <option value="black">Black bars</option>
              </select>
            </label>
          </fieldset>

          <fieldset className="grid gap-3" disabled={off}>
            <legend className="font-medium text-ink">Captions</legend>
            <Slider label="Size" value={draft.captions.size} min={20} max={56} step={1} onChange={(value) => set("captions", { size: value })} disabled={off} format={(value) => `${value} px`} />
            <Slider label="Vertical position" value={draft.captions.position} min={0.55} max={0.9} step={0.01} onChange={(value) => set("captions", { position: value })} disabled={off} format={(value) => `${Math.round(value * 100)}% down`} />
            <Slider label="Words per caption" value={draft.captions.max_words} min={1} max={8} step={1} onChange={(value) => set("captions", { max_words: value })} disabled={off} />
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <label className="flex items-center gap-2">
                Highlight
                <input type="color" value={draft.captions.highlight} onChange={(event) => set("captions", { highlight: event.target.value })} aria-label="Highlight colour" />
              </label>
              <label className="flex items-center gap-2">
                Text
                <input type="color" value={draft.captions.color} onChange={(event) => set("captions", { color: event.target.value })} aria-label="Caption colour" />
              </label>
              <label>
                Style
                <select className="ml-2 rounded-lg border border-line px-2 py-1" value={draft.captions.style} onChange={(event) => set("captions", { style: event.target.value as StudioSettings["captions"]["style"] })}>
                  <option value="box">Box</option>
                  <option value="outline">Outline</option>
                  <option value="shadow">Shadow</option>
                </select>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={draft.captions.uppercase} onChange={(event) => set("captions", { uppercase: event.target.checked })} />
                Capitals
              </label>
            </div>
            <p className="text-xs text-slate-500">Captions keep the words exactly as spoken (Hindi in Latin letters, English unchanged).</p>
          </fieldset>

          <fieldset className="grid gap-3 md:col-span-2" disabled={off}>
            <legend className="font-medium text-ink">Background music</legend>
            <div className="flex flex-wrap gap-2 text-sm" role="radiogroup" aria-label="Music source">
              {(["default", "library", "upload", "none"] as const).map((source) => (
                <button key={source} type="button" role="radio" aria-checked={music.source === source} onClick={() => set("music", { source, ...(source === "library" && !music.track_id && tracks[0] ? { track_id: tracks[0].track_id } : {}) })} className={`rounded-full border px-3 py-1 ${music.source === source ? "border-teal bg-teal text-white" : "border-line"}`}>
                  {source === "default" ? "Default track" : source === "library" ? "Music library" : source === "upload" ? "My own music" : "No music"}
                </button>
              ))}
            </div>
            {music.source === "library" && (
              <div className="grid gap-3">
                {categories.map((category) => (
                  <div key={category}>
                    <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{category.replaceAll("_", " ")}</p>
                    <ul className="mt-1 grid gap-1">
                      {tracks
                        .filter((track) => track.category === category)
                        .map((track) => (
                          <li key={track.track_id} className={`flex flex-wrap items-center gap-3 rounded-xl border px-3 py-2 text-sm ${music.track_id === track.track_id ? "border-teal bg-teal/5" : "border-line"}`}>
                            <input type="radio" name="track" checked={music.track_id === track.track_id} onChange={() => set("music", { track_id: track.track_id })} aria-label={`Use ${track.title}`} />
                            <span className="font-medium text-ink">{track.title}</span>
                            <span className="text-slate-500">{track.artist}</span>
                            <span className="tabular-nums text-slate-500">{track.duration_seconds ? `${Math.floor(track.duration_seconds / 60)}:${String(Math.round(track.duration_seconds % 60)).padStart(2, "0")}` : ""}</span>
                            <a href={track.page_url} target="_blank" rel="noreferrer" className="text-xs text-teal underline">{track.license}</a>
                            <button type="button" className="ml-auto rounded-full border border-line px-3 py-0.5" onClick={() => void preview(track)}>
                              {playing === track.track_id ? "Stop" : "Play"}
                            </button>
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
                {tracks.length === 0 && <p className="text-sm text-slate-500">The music library could not be loaded.</p>}
              </div>
            )}
            {music.source === "upload" && (
              <div className="text-sm">
                <label className="inline-flex items-center gap-2">
                  Music file (MP3, WAV, M4A, AAC; up to 50 MB)
                  <input type="file" accept=".mp3,.wav,.m4a,.aac,audio/*" onChange={(event) => void onMusicFile(event.target.files?.[0])} />
                </label>
                {uploadPercent !== null && <p className="mt-1 text-slate-600">Uploading… {uploadPercent}%</p>}
                <p className="mt-1 text-slate-600">{music.upload_id ? `Selected: ${musicName || "your uploaded track"}` : "No file chosen yet."} You are responsible for the rights to music you upload.</p>
              </div>
            )}
            {music.source !== "none" && (
              <div className="grid gap-3 sm:grid-cols-3">
                <Slider label="Overall volume" value={music.volume} min={0} max={2} step={0.05} onChange={(value) => set("music", { volume: value })} disabled={off} format={(value) => `${Math.round(value * 100)}%`} />
                <Slider label="During speech" value={music.under_speech} min={0} max={0.6} step={0.01} onChange={(value) => set("music", { under_speech: value })} disabled={off || !music.ducking} />
                <Slider label="During pauses" value={music.in_pauses} min={0} max={1} step={0.01} onChange={(value) => set("music", { in_pauses: value })} disabled={off || !music.ducking} />
                <Slider label="Fade-in" value={music.fade_in} min={0} max={5} step={0.1} onChange={(value) => set("music", { fade_in: value })} disabled={off} format={(value) => `${value.toFixed(1)} s`} />
                <Slider label="Fade-out" value={music.fade_out} min={0} max={8} step={0.1} onChange={(value) => set("music", { fade_out: value })} disabled={off} format={(value) => `${value.toFixed(1)} s`} />
                <div className="grid gap-1 text-sm">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={music.ducking} onChange={(event) => set("music", { ducking: event.target.checked })} />
                    Lower the music while the teacher speaks
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={music.into_outro} onChange={(event) => set("music", { into_outro: event.target.checked })} />
                    Continue under the outro
                  </label>
                </div>
              </div>
            )}
            <p className="text-xs text-slate-500">The teacher's voice is never changed. Without ducking, the music stays at the “during speech” level throughout.</p>
          </fieldset>

          <fieldset className="grid gap-2 md:col-span-2" disabled={off}>
            <legend className="font-medium text-ink">Visual effects</legend>
            <p className="text-xs text-slate-500">All effects are off by default. Each one is drawn by the renderer into the final video.</p>
            <ul className="grid gap-2 md:grid-cols-2">
              {EFFECTS.map((effect) => {
                const value = draft.effects[effect.name];
                return (
                  <li key={effect.name} className={`rounded-xl border px-3 py-2 text-sm ${value.enabled ? "border-teal bg-teal/5" : "border-line"}`}>
                    <label className="flex items-center gap-2 font-medium text-ink">
                      <input type="checkbox" checked={value.enabled} onChange={(event) => setEffect(effect.name, { enabled: event.target.checked })} />
                      {effect.label}
                    </label>
                    <p className="text-xs text-slate-500">{effect.hint}</p>
                    {value.enabled && (
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <Slider label="Intensity" value={value.intensity} min={effect.min} max={effect.max} step={0.05} onChange={(next) => setEffect(effect.name, { intensity: next })} disabled={off} format={(next) => next.toFixed(2)} />
                        {effect.scopes.length > 1 && (
                          <label className="text-sm">
                            Apply to
                            <select className="ml-2 rounded-lg border border-line px-2 py-1" value={value.scope} onChange={(event) => setEffect(effect.name, { scope: event.target.value })}>
                              {effect.scopes.map((scope) => (
                                <option key={scope} value={scope}>
                                  {SCOPE_LABEL[scope]}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <button
              type="button"
              className="justify-self-start rounded-full border border-line px-3 py-1 text-sm"
              onClick={() => setDraft((current) => (current ? { ...current, effects: Object.fromEntries(Object.entries(current.effects).map(([name, value]) => [name, { ...value, enabled: false }])) as StudioSettings["effects"] } : current))}
            >
              Reset all effects
            </button>
          </fieldset>
        </div>
      </div>
      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-3">
        <button type="button" className="rounded-full bg-ink px-5 py-2.5 text-sm text-white disabled:opacity-40" disabled={!dirty || off} onClick={() => void save()}>
          {saving ? "Saving…" : "Save look and sound"}
        </button>
        <button type="button" className="rounded-full border border-line px-4 py-2 text-sm disabled:opacity-40" disabled={!dirty || off} onClick={() => setDraft(structuredClone(saved))}>
          Discard
        </button>
      </div>
    </section>
  );
}
