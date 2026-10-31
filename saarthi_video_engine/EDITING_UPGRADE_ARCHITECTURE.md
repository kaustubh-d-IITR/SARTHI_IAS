# Intelligent Editing Upgrade — Architecture Map (Milestone 1)

Baseline: `main` at `aa9e925` (production), plus uncommitted owner account commands (`api/app/cli.py`, `api/tests/test_accounts.py`).
Tests before any change: API 81 passed (+10 PostgreSQL tests with a server), engine 172 passed (`test_phase12` excluded), web 26 passed.

## Code paths in production

| Concern | Where | Notes |
| --- | --- | --- |
| Transcription | `api/app/processing/transcribe.py` → engine `transcription.elevenlabs` | Hash-cached. Segments are fixed ~18-word chunks, not sentences. Every word has `start`/`end`. Text is mixed Devanagari and Latin; `transcription.romanize.transliterate` gives Latin. |
| Mode A footage | `processing/jobs.py` `select_auto` → engine `select_clips`; `plan_adapt.assets_from_engine` | Commons search, licence checks, ranking, verification. Unchanged by this upgrade. |
| Mode B analysis | `services/visual/*` (CLIP + Snowflake worker) | Frame sampling, thumbnails, `analysis.summary`, embedding batch. |
| Mode B matching | `processing/matching.py` | Scores whole segments and segment pairs; places a cutaway at the window's **start**; picks the globally best window. Root cause of the newspaper bug: phrase position inside an 18-word chunk is ignored, there is no negation/action model, and a later chunk that shares more words can win. |
| Plan build | `plan_adapt.build_engine_plan` → engine `editing_plan.phase2.build_phase2_plan` | Engine drops cutaways shorter than **2.0 s** (hard-coded in three places), merges overlaps, applies the 58 % cap. |
| Validation | engine `validation.plan.validate_plan` + `plan_adapt.validate_envelope` | Overlaps, narration coverage, Mode B ownership, source intervals. No minimum length. |
| Review edits | `processing/editing.py` `apply_edits` → `versions.save_version` | Each change is a new immutable version under `work/<sid>/plans/`. Enforces `MIN_CUTAWAY = 2.0`. |
| Render | `services/rendering/adapter.py` `render_attempt` → `bridge` → engine `hyperframes_adapter.compose.write_composition` → HyperFrames 0.8.186 → `render.outro.append_outro` | Cuts teacher/narration, trims logo and the one licensed music track (gains 0.12/0.28), places muted cutaways, composes, renders, checks size/fps/duration/narration correlation, appends outro. |
| Framing | `compose._family_css` with `render.frame.reference_frame` (card layout) | Teacher inside a rounded card (59 % of canvas height). Landscape lessons use `object-fit: contain` inside the card: a 720×354 strip. B-roll is confined to the card, not full screen. A 0.3 s opacity blend fades each cutaway in (double exposure). |
| Captions | engine caption decisions; `compose._captions` | Upper-case chip, fixed 26 px, fixed bottom offset. No user settings. |
| Outro | `render.outro.append_outro` | 848×478 silent outro scaled to fit and padded **black**: a horizontal strip. Music stops at the lesson end. |
| Music | `bridge.music()` → one licensed Commons track | No library, no upload, no user mix controls. |
| Frontend | `web/src/components/PlanReview.tsx`, `ProcessReview.tsx`, `RenderPanel.tsx` | Approve/reject, swap, move timeline and source window, title. |
| Deploy | `render.yaml` (API + worker Docker images, autoDeploy false), `web/vercel.json` | Vercel deploys on push; Render deploys are manual. |

## Design of the upgrade (backwards compatible)

1. **Director** (`processing/director.py`): splits the transcript into phrases from word timestamps (punctuation, pauses, Hindi/English clause starters such as "na hi", "aur", "jab", "lekin", "and", "because"), scores each clip against each phrase with description, keyword, action, negation and visual evidence, and places each clip at its phrase's first word. The earliest occurrence wins unless a later one is clearly stronger. Back-to-back phrase cuts may be shorter than 2 s (configurable floor, default 1.0 s) only when the next cutaway follows immediately. The engine builder gets an optional `min_cutaway` argument whose default stays 2.0, so engine runners and Mode A are unchanged.
2. **Studio settings** (`studio_settings` on each plan version): framing, captions, music, effects, transitions. Plans without it render exactly as before.
3. **Composer**: a new optional `render.studio` block in the engine plan switches the HyperFrames page to a full-canvas layout (teacher fill with a motion-centred crop and blurred backdrop, full-screen cutaways, hard cuts, caption settings, effects as CSS filters and overlays). Without the block the existing layouts are used.
4. **Music**: bundled library (manifest with licence per track) plus `music` uploads through the existing upload/object-storage path; the mix values flow into the existing music lane.
5. **Assistant**: deterministic parser for English/Hinglish instructions, optional Gemini when `GEMINI_API_KEY` is set; both produce validated operations that are applied through `apply_edits`, so every change is a new version and undo restores the parent.
6. **QA**: post-render report (blackdetect, loudness/clipping, cutaway visibility, outro framing) stored next to the render; hard failures fail the render.
