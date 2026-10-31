# Phase 3 report

The Studio now renders an approved editing plan to MP4 through the existing HyperFrames renderer. A render starts only when the user clicks Render Video and confirms. Two real lessons were rendered, one per mode, and both passed output and narration checks. Everything else in this report used synthetic clips.

The pre-implementation audit, with findings added afterwards, is `PHASE3_RENDER_ARCHITECTURE_AUDIT.md`.

## Render architecture

The existing chain, unchanged: cut the teacher picture and narration (`run_phase4._cut_teacher`, `_cut_audio`), remove letterbox bars (`run_phase5._remove_letterbox`), place muted B-roll (`run_phase4._place_asset`), trim the logo (`run_phase5._trim_logo`) and the music (`run_phase4._trim_music`), compose (`hyperframes_adapter.compose.write_composition`), render (`run_phase4._hyperframes`, `hyperframes@0.8.186`, draft, one worker), append the outro (`render.outro.append_outro`), and compare audio (`validation.audio_compare.compare_wavs`). The Studio calls those functions. It does not call `run_phase5._render_one`, which selects footage again and writes into `outputs/`.

The adapter is in `api/app/services/rendering/`:

- `bridge.py` wraps each engine function and turns its `SystemExit` into an ordinary error with a saved diagnostic.
- `adapter.py` checks inputs, builds a render copy of the approved plan, runs the chain, and verifies the result.
- `jobs.py` runs the lifecycle, blocks duplicate starts, keeps the last completed render, and marks interrupted renders failed on restart.

The render copy changes only what the renderer needs:

- Real file paths for the teacher, narration, B-roll, logo, and music.
- Source width and height measured from the cut lesson.
- `object_fit` set by the phase 5 rule: `cover` for a portrait source, `contain` for landscape.
- The music object.

Every picture and caption decision keeps its approved id, time, asset, and text. The adapter checks this before composing and stops if anything else differs. The only change it allows is a trim at the end, if the narration cut is shorter than the plan. The stored action stays `replace_with_broll`.

Lifecycle: `render_queued` → `preparing` → `composing` → `rendering` → `verifying` → `assembling_outro` → `completed`, or `failed`. FastAPI `BackgroundTasks` runs the job, and the POST returns at `render_queued`.

Each attempt writes `data/studio/renders/<session-id>/<render-id>/`, a new UUID folder that is never reused. On success the folder holds:

- `final.mp4`
- `render_metadata.json`
- `input_manifest.json`, with SHA-256 of the raw lesson, each clip, the logo, the outro, and the music
- `editing_plan_snapshot.json`
- `render_plan.json`
- `audio_verification.json`
- `outro.json`
- `logs/steps.json`
- the composition HTML, plus `lint.txt` and `render.txt`

A failed attempt keeps its intermediate files and adds `failure.json` with the full diagnostic. Large intermediate media are deleted after a successful render.

## Mode A

The render uses the cutaways the existing selector approved in Phase 2, through the session's private `footage_index.json`. Each file must be inside the session work folder and match its recorded SHA-256. No search runs during a render, and the fixture test checks that the selector is called once, during processing only. Licence, licence URL, page URL, and creator stay on each decision as approved. The 58% cap is not touched. Where the plan shows the teacher, the teacher stays.

## Mode B

Every cutaway must be an upload in the same session. A placement with a page URL or `origin: commons`, or a clip owned by another session, stops the render. So do a missing clip, a clip that ffprobe cannot read, and a clip whose SHA-256 differs from the one catalogued in Phase 2. No other clip is ever substituted.

The B-roll is re-encoded with `-an`, and the adapter refuses a prepared cutaway that still has an audio track. The composer plays it full-card with `object-fit: cover`, which is the full-screen cutaway: the teacher's picture is replaced and there is no picture-in-picture. The cutaway plays from the approved source in-point for the length of its decision, and that interval is recorded in `logs/steps.json`.

## Audio and captions

The composition must contain exactly one narration track, starting at 0 and lasting the whole lesson, and every `<video>` tag must be `muted`. Captions are the plan's caption decisions, with the same ids, times, and text. Hinglish and Roman Hindi are kept as written, and the composer only uppercases them. The music bed is the licensed “Inspire Action” by Yuri Megis (CC BY 4.0) already used by the phase 5 renders, at 0.12 under speech and 0.28 in the open, ducked by the existing `duck.volume_lane`. If that file is missing, the render fails instead of going out silent.

Verification runs twice:

- On the lesson render: 720×1280, 30 fps, picture and audio present, length within 0.5 s of the plan, and narration correlation of at least 0.90.
- On the finished file: the same format checks, length within 0.75 s of lesson plus outro, and narration correlation of at least 0.90 on the lesson-length part.

The 0.90 floor comes from the Phase 19 measurements of 0.988 and 0.9645.

## Logo and outro

The logo is `LOGO/shivin sir logo.png`, trimmed by `_trim_logo` and placed by the existing frame layout. The outro is `OUTRO/WhatsApp Video 2026-10-23 at 12.53.20 PM.mp4`, joined by `append_outro`. That file has no audio stream, so `append_outro` pads silence, as it did before. Canvas, frame rate, caption style, and caption animation come from the existing composer and config.

## API

All routes require `X-Studio-Client`. Another browser's id gets 404, and a missing id gets 400.

| Method | Path | Result |
| --- | --- | --- |
| POST | `/api/sessions/{session_id}/render` | Returns at once with `render.status: render_queued` and a new `render_id`. 409 `plan_not_ready`, `render_in_progress`, or `render_inputs_missing`. |
| GET | `/api/sessions/{session_id}/render-status` | Stage and message, then `metadata`, `audio_verification`, `outro`, and `lesson_duration_seconds`. `last_completed` appears when a later attempt failed. No file paths. |
| GET | `/api/sessions/{session_id}/video` | The newest completed MP4, inline. |
| GET | `/api/sessions/{session_id}/download` | The same MP4 as an attachment named `saarthi-video-<render-id prefix>.mp4`. |

A failed render can be retried, and the retry gets a new render id and folder. Re-rendering after success is also allowed and never replaces the earlier file.

## Frontend

When a plan is ready, the plan summary shows Render Video. Clicking it opens a confirmation with:

- the workflow
- the raw lesson filename and duration
- the transcript segment count
- the approved B-roll placements
- the teacher-only intervals
- the plan validation result

During a render, the actual stages are listed with no percentage. When the render finishes, the page shows:

- a video player
- a download link
- the render id, duration, resolution, and frame rate
- the audio check with its correlation
- the outro status

A failure shows the error and a Retry render button. If an earlier render succeeded, that video stays visible. The browser fetches the video with its client header and plays it from a blob URL.

## Tests

| Suite | Result |
| --- | --- |
| Studio API (`api/.venv`, `pytest tests`) | 41 passed (18 Phase 1, 12 Phase 2, 11 Phase 3) |
| Engine (`pytest tests --ignore=tests/test_phase12.py`) | 172 passed. `test_phase12.py` was not run because it loads CLIP weights. |
| Frontend (`vitest`, single fork) | 13 passed |
| `npm run build` (`tsc --noEmit` and Vite) | Succeeded |

The Phase 3 API tests use generated clips: a 360×640 lesson with pink-noise audio, a B-roll clip with a 1 kHz tone, a 1 s outro, a sine music bed, and a PNG logo. Only the HyperFrames CLI is replaced. The stand-in writes a 720×1280 lesson from the composition's own narration and music files. The teacher and narration cuts, letterbox check, logo trim, B-roll placement, composer, outro join, and audio comparison are the real engine code. No ElevenLabs, Commons, or real lesson is used.

Covered:

- Mode B and Mode A end to end, including the exact composition: muted videos, one narration track from 0, music at 0.12, the corner logo, caption ids and times, and Roman Hindi captions.
- Mode A licence and creator kept, and no second selector call.
- Mode B with no external fields.
- B-roll audio removed by the engine helper, and a cutaway that keeps audio rejected.
- Missing raw lesson and missing B-roll blocked with 409.
- A changed clip and a corrupted clip rejected.
- A clip from another session, Mode B external footage, and Mode A paths outside the session rejected.
- Duplicate start blocked, and a render before the plan is ready blocked.
- Restart recovery.
- Corrupted HyperFrames output rejected, and a retry getting a new folder.
- A failed retry keeping the earlier video downloadable.
- A second render never changing the first `final.mp4`.
- Video and download ownership.
- Timeline-change detection.

Frontend tests cover the confirmation summary, stage display without a percentage, the finished-video panel and download link, and a failed retry that keeps the earlier video.

Running all three frontend test files in parallel once crashed a Vitest worker for lack of memory. The same tests pass in a single fork, which is how the results above were produced.

## Real renders

These are real lessons. Neither run called ElevenLabs or Commons. Both read sources and wrote only to new Studio folders. They were run one at a time with `api/tools/run_phase3_real_render.py`.

**Mode A, VIDEO_1.** Session `0d342832-2852-498f-8883-38141cabf93c`, render `ccf7cec8-ad51-459d-8e0f-91ee338060d2`. Inputs: raw `VIDEO_1.mp4` (SHA-256 `f0cfe846b69f6ecd…`), the saved Phase 19 transcript and editing plan, and the Phase 19 approved Commons cutaway, “Writing with fountain pen” (CC BY-SA 4.0). The cutaway is at 46.36–50.40 s and the teacher fills the rest.

The plan came from Phase 19, not from the Studio's Phase 2 selector. A live Mode A Phase 2 run would search Commons, so this run did not do one.

The render took about 2 minutes 20 seconds. Frames at 10 s, 48.4 s, and 60 s show the teacher card with logo, title, and a Roman Hindi caption; the pen cutaway filling the card with the caption still running; and the outro. The 48.4 s frame matches the Phase 19 render, and the length is identical (63.288 s).

**Mode B, VIDEO_2.** Session `e6f9d61a-de01-4351-abe1-000f1df01a24`. Inputs: raw `VIDEO_2.mp4` (SHA-256 `dd57f44b18227ee3…`) and one uploaded clip. The clip is a copy of the Phase 19 PPE doffing clip, described as “A health worker on COVID duty taking off a protective gown and mask.” The saved Phase 19 transcript was supplied as the transcription result.

Phase 2 processing ran through the Studio code and produced 1 candidate, 1 approved placement, and 2 teacher intervals. It matched the clip to “a COVID duty, I am coming back, I am tired, I am exhausted. I have been wearing a” at 19.86–22.88 s, using source 0.00–3.02 s.

The first render attempt, `7ffa8a55-c8a9-46db-bfc4-0b251bf71097`, failed inside HyperFrames at frame 412 of 1249 with “drawElement worker encode failed”. That happened while free memory was about 1 GB. Its folder and `failure.json` are kept. The retry, `06d30ef4-8c93-4676-8b20-913afcaadf72`, completed in about 2 minutes.

Frames at 21.1 s, 24.4 s, and 45 s show the user clip filling the card under the narration's caption, the teacher back afterwards, and the outro. The captions match Phase 19 exactly: 56 caption decisions, including the same three-line chip at 21 s. The title differs, as noted under limitations.

| | Mode A, VIDEO_1 | Mode B, VIDEO_2 |
| --- | --- | --- |
| Final duration | 63.288 s | 49.655 s |
| Lesson portion | 55.267 s | 41.633 s |
| Outro | 8.008 s, assembled | 8.008 s, assembled |
| Expected vs final | 63.275 s, +0.013 s | 49.641 s, +0.013 s |
| Resolution | 720×1280 | 720×1280 |
| Frame rate (ffprobe average) | 30.032 | 30.04 |
| Codecs | h264 / aac | h264 / aac |
| Size | 7,883,155 bytes | 9,553,488 bytes |
| Narration correlation, finished / lesson render | 0.988 / 0.9914 | 0.9645 / 0.9677 |
| Phase 19 reference | 0.988 | 0.9645 |
| Cutaways | 1 Commons | 1 user |

Over live HTTP, `/video` and `/download` returned the Mode B file to its owner: 9,553,488 bytes, matching the file on disk. Another client id got 404 and a request with no id got 400.

Earlier, before this adapter, three smoke attempts were made under `data/studio/renders/` at 19:37. One failed on an import, one on an x264 out-of-memory error, and one stopped without a result. None produced an MP4, and they are kept for reference.

## Protected files

The logo, the outro, both raw lessons, and every MP4 under `outputs/` (204 files) were hashed before Phase 3 work and again after both real renders. None changed and none is missing. No phase runner, engine module, or engine test was edited.

## Files

Created:

- `PHASE3_RENDER_ARCHITECTURE_AUDIT.md`
- `PHASE3_REPORT.md`
- `api/app/services/__init__.py`
- `api/app/services/rendering/__init__.py`
- `api/app/services/rendering/bridge.py`
- `api/app/services/rendering/adapter.py`
- `api/app/services/rendering/jobs.py`
- `api/tests/test_phase3.py`
- `api/tools/run_phase3_real_render.py`
- `web/src/components/RenderPanel.tsx`

Modified:

- `api/app/factory.py`: render routes, injectable renderer, restart recovery
- `api/app/storage.py`: `renders` folder
- `api/app/schemas.py`: `render` on the session response
- `api/app/processing/plan_adapt.py` and `api/app/processing/jobs.py`: Mode A `footage_index.json` and a shared session lock
- `web/src/components/ProcessReview.tsx`
- `web/src/lib/api.ts`
- `web/src/types.ts`
- `web/src/pages/HomePage.tsx`
- `web/src/pages/AutoEditPage.tsx`
- `web/src/pages/MyBrollPage.tsx`
- `web/src/test/processReview.test.tsx`
- `web/src/test/studioUi.test.tsx`
- `STUDIO.md`

## Known limitations

- HyperFrames needs free memory. On this machine one real render failed while capturing frames and succeeded on retry. OneDrive was holding about 8.9 GB of private memory at the time, and `data/studio/` sits inside the OneDrive folder, so every render is also synced.
- The title comes from the engine's automatic rule. Phase 19 set VIDEO_2's title by hand (“From NEET to UPSC”). The Studio has no title field, so the same lesson rendered as “BLINDLY”.
- The Mode A real render used the Phase 19 plan, not a fresh Studio Phase 2 selection, so a live Commons search through the Studio has still not been run.
- The Mode B user clip was a copy of an existing licensed clip, because no real user B-roll was available. The matching came from its description, and frames were not analysed.
- Long captions behave as before. A single long transcript segment becomes one three-line chip, as in Phase 19.
- Render jobs run inside the API process, one at a time per session. A restart marks a running render failed, and it has to be retried.
- The video is fetched into a browser blob, so very long renders use browser memory.
- The ffprobe average frame rate of the joined file is 30.03–30.04. The lesson and the outro are each encoded at 30 fps, and the check allows 0.1 fps of drift.
- No deployment work was done.

## Restart commands

Stop anything already listening on ports 8787 and 5173.

API, from `saarthi_video_engine\api`:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Website, from `saarthi_video_engine\web`:

```powershell
npm run dev
```

Open http://127.0.0.1:5173 . The API is at http://127.0.0.1:8787/api/health .
