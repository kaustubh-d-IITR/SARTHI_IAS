# Intelligent Editing Upgrade — Report

Baseline: `main` at `aa9e925`. Architecture map: `EDITING_UPGRADE_ARCHITECTURE.md`. Everything below was implemented and tested locally; deployment status is at the end.

## 1. What changed, by area

| Area | Status | Where |
| --- | --- | --- |
| Phrase-level B-roll director (Mode B) | Implemented, tested, rendered | `api/app/processing/director.py`; engine `build_phase2_plan(min_cutaway=…)` |
| User phrase overrides | Implemented, tested | `target_phrase` in edits, recorded as `user_override` |
| Look and sound settings on every plan version | Implemented, tested | `api/app/processing/settings.py`, `versions.py`, `editing.py` |
| Full-canvas framing, blurred backdrop, full-screen B-roll, hard cuts | Implemented, rendered | engine `hyperframes_adapter/studio_layout.py`, `api/app/services/rendering/studio.py` |
| Captions: size, position, colours, style, words per caption, word highlight | Implemented, rendered | `studio_layout.py`, engine `caption_max_words` |
| Outro framed in 9:16 with blurred fill, music continuing under it | Implemented, rendered | engine `render/outro.py` (`fill`, `audio`) |
| Music library (7 licensed tracks) | Implemented, rendered | `assets/music_library/`, `deploy/build_music_library.py`, `api/app/services/music.py` |
| Custom music upload | Implemented, rendered | `kind: "music"` uploads, `media_probe.probe_audio` |
| Mix controls (volume, speech/pause levels, fades, ducking, into outro) | Implemented, rendered | `adapter.studio_music_plan`, `studio.prepare_music`, `music_tail` |
| Visual effects (12) | Implemented, rendered | CSS filters, overlays and GSAP tweens in `studio_layout.py` |
| Editing assistant (English/Hinglish → validated operations, undo) | Implemented, tested through the API and in a render | `api/app/processing/assistant.py`, `/assistant/*` routes |
| Timeline, look-and-sound panel, assistant panel | Implemented, unit-tested | `web/src/components/Timeline.tsx`, `StudioControls.tsx`, `AssistantPanel.tsx` |
| B-roll library page: preview, reorder | Implemented | `web/src/pages/MyBrollPage.tsx` |
| Post-render quality report | Implemented, rendered | `api/app/services/rendering/quality.py` |
| HyperFrames retry once on a capture crash | Implemented | `bridge.hyperframes` |

Unchanged: authentication, migrations (head stays `0002_repair_missing_tables`), R2 storage design, `render.yaml`, environment variables, Mode A footage discovery/licensing/ranking/verification, ElevenLabs transcription, logo, captions' spoken text, music ducking logic.

## 2. The newspaper problem

Cause: the Mode B matcher scored whole 18-word transcript chunks and started a cutaway at the chunk's start, with no notion of negation, and the engine dropped any cutaway shorter than 2 s. The director now works on phrases rebuilt from word timestamps and checks keyword, action, negation, semantic and visual evidence per phrase; the earliest strong phrase wins; consecutive phrases may cut back to back (≥ 1 s).

Result on the real test lesson (`api/tests/test_director.py` and a real render):

| Clip | Placed at | Phrase |
| --- | --- | --- |
| Newspaper with red cross | 0.12–1.26 s | “naa hi mainne newspaper padha” (mention 1 of 2) |
| NCERT books with red cross | 1.26–3.44 s | “aur naa hi mainne NCERT padhi” |
| Phone opening YouTube | 6.08–8.08 s | “… mainne bhi YouTube khola thaa” |

Nothing is placed on the later advice “yaar newspaper padh lo” (16.1 s): a crossed-out clip only matches speech that negates.

## 3. Tests

| Suite | Result |
| --- | --- |
| API (`api/tests`) | 113 passed (new: director 10, assistant 14, studio render 8, accounts 7) |
| API on PostgreSQL 16 (`STUDIO_TEST_POSTGRES_URL`) | 10 passed |
| Engine (`tests/`, `test_phase12` excluded) | 172 passed |
| Web (vitest) | 32 passed (new: 6) |
| TypeScript and `vite build` | clean |
| API Docker image (`deploy/api.Dockerfile`) | built; on Linux: `/api/live` 200, `/api/ready` 200 at `0002`, 7 library tracks, preview served |
| Worker Docker image (`deploy/worker.Dockerfile`) | all 24 build steps completed (pip + CPU torch, model download with offline load check, HyperFrames Chrome); the final image export failed because the local Docker engine crashed on this memory-limited machine. Not a Dockerfile error, but the worker image was **not** produced locally. |

## 4. Real renders (local production-shaped rehearsal, real HyperFrames)

`deploy/smoke_local.py`: password sign-in, S3 stand-in, queue, separate worker, real HyperFrames 0.8.186. Transcripts came from the cache; no ElevenLabs or Gemini call was made. B-roll in Mode B used three labelled synthetic clips (newspaper ✕, NCERT ✕, phone/YouTube) because the real clips were not available locally.

| Render | What it proves | Result |
| --- | --- | --- |
| R0 Mode B, defaults | Placement, framing, captions, outro, QA | 720×1280, 30 fps, 49.66 s; QA passed (11 checks); narration correlation 0.9645 |
| R1 Mode B + assistant + library track + warm/glitter/vignette | TEST 1–3, 5, 6, 7 | Assistant moved newspaper to 0.12–2.12 s and pushed NCERT to 2.12 s (shown as an extra step); QA passed (12 checks incl. outro music −20.9 dB); outro audio matches the chosen library track (correlation 0.485 vs ≤ 0.054 for the other six) |
| R2 Mode B + custom MP3, effects off | TEST 4, 6 | Outro audio peaks at 523.2 Hz and 659.2 Hz = the uploaded track (84.6 % of energy); red–blue balance 7.92 vs 14.27 with warm on in R1, so the filter is removed when disabled |
| R3 Mode A landscape + glitter/sparkle intro | TEST 8, 10, 11 | Commons footage discovered and placed as before; teacher fills 62 % of the height around the measured focus, blurred backdrop; QA passed (13 checks); correlation 0.9879 |

Before/after: the 16:9 lesson changed from a 720×354 strip in a card to a teacher-centred crop filling most of the frame; the outro changed from a strip on black to a blurred 9:16 fill.

## 5. Limitations (honest)

- **Music library**: 7 tracks in 5 of the 8 requested categories (inspirational, cinematic, documentary, calm educational, minimal ambience). Motivational, serious/current affairs and energetic are empty: no Commons candidate passed the engine's licence and instrumental gate, and the gate was not relaxed.
- **Glitter** was made more visible after R1 (larger points with a glow); the stronger version is in R3 only.
- **Word highlight** timing inside a caption line is shared by word length; line boundaries use exact word timestamps.
- **Effect preview** in the browser uses the same CSS values as the renderer for colour, framing, vignette and captions; glitter/sparkle and zoom are only approximated in the preview.
- **Assistant**: the built-in rules cover the listed English/Hinglish instructions and similar phrasings; others get a clarifying question. Gemini is used only if `GEMINI_API_KEY` is added to the API service (not configured; not tested live).
- **Focus detection** uses motion, not face detection; a still teacher falls back to the centre (the user can set the focus).
- **Worker image** was not completed locally (see tests). Render builds it on its own infrastructure.
- **Production** verification depends on Render's manual deploy (`autoDeploy: false`) of both services after the push.

## 6. Production repair: "source interval is past the clip duration"

Production session `85865c1b-…` (lesson `VIDEO_2.mp4`, three 2 s clips) failed in plan validation. Reproduced locally with the same lesson transcript and three 2.0 s clips: the director gave `ncert_books_red_cross_2s.mp4` the timeline 1.26–3.44 s and the source 0–2.18 s. The clip has 2.0 s. `_snap_end` extended the end to the end of the spoken word without the clip's length as a limit. The production asset id `85dd3534-…` is most likely that NCERT clip; production records were not read.

Fix:

- `api/app/processing/intervals.py` holds the rule for every path: the source window must lie inside 0..usable footage, where usable footage is the shorter of the container and the video stream. Times are rounded towards the inside of the clip.
  - Too long: the cutaway is shortened to the footage.
  - Too short after fitting: rejected with a reason; the teacher stays on screen.
  - Footage is never taken from another clip.
- Director: snapping and the final window are limited by the clip's footage.
- Processing: `fit_placements` re-checks every placement (Mode A and B) before the plan is built.
- Review edits: a typed source start is kept. The end is clamped if enough footage remains; otherwise 422 with the reason. The row carries `adjusted`, shown in the review.
- Upload probe: records the video stream duration. Older uploads are probed again.
- Renderer: refuses an interval longer than the picture (one-frame tolerance), with the numbers, before HyperFrames runs.
- Captions: an English line with many short words ("I", "am", "a") was treated as Whisper letter tokens and shown whole; only non-Latin tokens count now.
- Web: the editing controls were only mounted after `plan_ready`, at the bottom of the page. A failed session showed none of them. They are now in an "Editing workspace" directly under the status card, with tabs:
  - Cutaways & timeline
  - Look & sound
  - AI editing assistant

  The workspace states why it is locked when processing has failed.

Local verification: `api/tests/test_intervals.py` (12 tests) and the full suites pass. A real HyperFrames rehearsal with three 2.0 s clips placed the newspaper at 0.12–1.26 s (source 0–1.14 s), NCERT at 1.26–3.26 s (0–2.0 s) and YouTube at 6.08–8.08 s (0–2.0 s). Output: 720×1280, 30 fps, 49.66 s, QA passed (12 checks), narration correlation 0.9645, outro music present. Production is fixed only after both Render services are redeployed and the session is processed again.
