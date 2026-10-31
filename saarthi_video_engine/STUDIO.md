# Sarthi IAS Video Studio — Phase 5

This folder is the website and API for Sarthi IAS Video Studio. Phase 1 stores a raw lesson and, in the second mode, B-roll clips the user describes. Phase 2 transcribes that lesson and writes an editing plan. Phase 3 renders an approved plan into an MP4 when the user clicks Render Video. Phase 4 adds clip analysis, plan review, versions, and titles. Phase 5 makes it deployable: a database, object storage, a durable job queue with a separate worker, and sign-in. Deployment steps are in `deploy/DEPLOYMENT.md`.

The video engine under `src/` and the `run_phase*.py` runners are unchanged. The studio calls them through `api/app/processing/` after a user starts processing, and through `api/app/services/rendering/` after a user confirms a render.

## Layout

```
saarthi_video_engine/
  api/                 FastAPI application, worker (app/worker.py), migrations (migrations/)
  web/                 React, TypeScript, and Vite
  deploy/              Dockerfiles, model fetch, local rehearsal script, deployment guide
  data/studio/         local database, objects, and work files; gitignored
  src/                 existing engine
```

Records (users, sign-in sessions, uploads, studio sessions, jobs, plan versions, renders, audit events) are in a database: SQLite at `data/studio/studio.db` locally, PostgreSQL in production. Files are objects under server-built keys: `uploads/<uuid>/media.<ext>`, `work/<session-id>/...` (transcript, plans, footage), `visual/<sha256>/...`, `transcript_cache/<sha256>.json`, and `renders/<session-id>/<render-id>/...`. Locally those keys are files under `data/studio/`; in production they are in a private S3-compatible bucket and jobs copy what they need into a scratch folder. A transcript is reused from the cache only when the raw file's SHA-256 still matches. Each render attempt gets its own prefix.

Sessions made before Phase 5 (`data/studio/sessions/*.json`) are not read automatically. `python -m app.tools.import_local --source ../data/studio` lists what would be imported; add `--apply` to import, and `--owner-email` to assign them to an account. The source folder is only read. Nothing is written into `DR_SHIVIN_RAW`, `LOGO`, `OUTRO`, `reference_dataset`, or `outputs/`. The renderer reads the logo, the outro, and the licensed music file already in `outputs/dr_shivin_phase5/asset_cache/`.

## Install

From `saarthi_video_engine`:

```powershell
& "C:\Users\shubh\AppData\Local\Programs\Python\Python313\python.exe" -m venv api\.venv
& "api\.venv\Scripts\python.exe" -m pip install -r api\requirements-dev.txt
cd web
npm install
```

## Run locally

API, from `saarthi_video_engine\api`:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

From the engine root, the same process is:

```powershell
& "api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --app-dir api
```

Website, from `saarthi_video_engine\web`:

```powershell
npm run dev
```

Open http://127.0.0.1:5173 . Vite proxies `/api` to http://127.0.0.1:8787. Leave `VITE_API_BASE_URL` empty for that proxy.

Local defaults: SQLite, files under `data/studio/`, jobs run inside the API process (`STUDIO_JOB_RUNNER=inline`), and no sign-in (`STUDIO_AUTH_MODE=client_header`). Migrations run on start. To try the production arrangement locally, set `STUDIO_AUTH_MODE=password`, `STUDIO_SESSION_SECRET`, and `STUDIO_JOB_RUNNER=queue`, create an account with `python -m app.cli create-user --email <address> --name <name>` (the password is typed at a prompt), and start `python -m app.worker` next to the API. `deploy/smoke_local.py` runs the whole arrangement, including an S3 stand-in, for one lesson.

Health checks: `/api/live` (the process answers), `/api/ready` (database, object storage, queue, a live worker heartbeat, and ffmpeg; 503 if any fails), `/api/health` (configuration summary).

## Tests

```powershell
& "api\.venv\Scripts\python.exe" -m pytest
```

Run that with the working directory `saarthi_video_engine\api`.

```powershell
npm run test
npm run build
```

Run those with the working directory `saarthi_video_engine\web`.

Engine tests stay in `saarthi_video_engine\tests`. `pyproject.toml` points pytest at that folder so `pytest` from the engine root does not collect the studio API. That line was added so the new tests cannot change the engine suite.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Status, version, whether the upload directory is writable, size limits, ffprobe availability. No credentials. |
| POST | `/api/uploads/raw-video` | One lesson video. Returns `upload_id` and probe metadata. |
| POST | `/api/uploads/broll` | One clip plus a form field `description`. Returns `asset_id`. |
| DELETE | `/api/uploads/{upload_id}` | Remove a file that is not already in a prepared session. |
| POST | `/api/sessions` | Body: `mode` (`auto_edit` or `my_broll`), `raw_video_id`, and `broll` entries of `asset_id` plus `description`. Status is `prepared`. |
| GET | `/api/sessions/{session_id}` | The same summary for the browser that uploaded the files. |
| POST | `/api/sessions/{session_id}/process` | Queue transcription and an editing plan. Returns immediately with status `queued`. |
| GET | `/api/sessions/{session_id}/status` | Current stage. No filesystem paths. |
| GET | `/api/sessions/{session_id}/transcript` | Provider transcript, including the original text and word timings. |
| GET | `/api/sessions/{session_id}/editing-plan` | The plan after validation has passed. The plan document itself always says `rendered: false`; render results are on `render-status`. |
| POST | `/api/sessions/{session_id}/render` | Queue a render of the approved plan. Returns immediately with `render.status` `render_queued` and a new `render_id`. |
| GET | `/api/sessions/{session_id}/render-status` | Render stage and render ID, then duration, size, frame rate, audio check, and outro result. Includes `last_completed` when a later attempt failed. |
| GET | `/api/sessions/{session_id}/video` | The finished MP4 inline, for the preview player. |
| GET | `/api/sessions/{session_id}/download` | The same MP4 as an attachment. |

`POST /process` is refused with 409 while a job is active or a plan is already ready. A failed job, or a job interrupted by a restart, can be started again. Processing does not run at API startup.

`POST /render` is refused with 409 when the plan is not `plan_ready`, when a render is already running, or when the raw lesson or a planned B-roll file is missing. Render stages are `render_queued`, `preparing`, `composing`, `rendering`, `verifying`, `assembling_outro`, then `completed` or `failed`. A failed render can be retried; every attempt writes a new folder and never replaces an earlier MP4. A render interrupted by an API restart is marked failed. If a retry fails, `/video` and `/download` still return the last completed render. A render does not start on its own after processing. The browser fetches the video with its `X-Studio-Client` header and plays it from a local blob URL.

Send header `X-Studio-Client` with a UUID on every route except health. The website stores that id in `localStorage`. Another browser id cannot read these uploads. This is local separation, not a login system.

`auto_edit` rejects a B-roll list. `my_broll` requires at least one described clip.

Allowed suffixes: mp4, webm, mov, mkv, m4v. The suffix is only a first check. `ffprobe` must find a video stream, a positive duration, and dimensions. If ffprobe is missing, the upload is rejected. Files are written in chunks. A failed upload deletes its temporary directory. The stored file is always `media` plus the suffix, never the user's filename.

Default limits, overridable in `api/.env` with `STUDIO_` variables only: 512 MB per file, 2 GB per session. Copy `api/.env.example`. Do not put engine API keys in that file.

## Production arrangement (Phase 5)

Vercel serves the website and rewrites `/api/*` to the Render API, so the sign-in cookie is first-party. The API (Render web service) answers requests and never runs the engine's long steps. A Render background worker claims jobs from the database queue and runs transcription, clip analysis, and the existing HyperFrames render. Both read and write the same PostgreSQL database and the same private bucket; neither relies on its local disk between jobs.

Sign-in: accounts are created by the owner with `python -m app.cli create-user`; there is no self-registration. Passwords are hashed with scrypt. The browser holds an HttpOnly, Secure, SameSite=Lax cookie; every state-changing request also needs the `X-CSRF-Token` returned at sign-in. Five failed attempts for one email in 15 minutes lock that email for the rest of the window. Every session, upload, plan, and video route checks the owner. `X-Studio-Client` is only used when `STUDIO_AUTH_MODE=client_header`, which production refuses.

Uploads go straight from the browser to the bucket: `POST /api/uploads/initiate` returns a 15-minute signed PUT for a server-built key, then `POST /api/uploads/{id}/complete` checks the stored size against the announced size and the limit, and runs ffprobe on the stored object. Anything that fails is deleted. The file's SHA-256 is computed when it is processed.

Jobs: one row per job with an idempotency key and a per-session slot, so a second click on Render or Process gets 409 (or the same job, when it repeats the same `Idempotency-Key`). The worker holds a lease (`STUDIO_JOB_LEASE_SECONDS`, 180 by default) and renews it every third of that. If a worker dies, processing is retried once; a render is marked failed with a `failure.json`, earlier videos stay, and the user can render again under a new render ID.

`STUDIO_ENV=production` refuses to start unless sign-in, object storage, PostgreSQL, the queue runner, a 32-character session secret, and secure cookies are configured.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/live`, `/api/ready` | Liveness and readiness. |
| GET | `/api/auth/me` | The signed-in user and CSRF token, or 401. |
| POST | `/api/auth/login`, `/api/auth/logout` | Sign in and out. |
| POST | `/api/uploads/initiate`, `/api/uploads/{id}/complete` | Direct upload. The multipart routes above are for local storage only. |
| GET | `/api/sessions` | The signed-in user's sessions, newest first. |
| GET | `/api/sessions/{id}/video-url` | Short-lived signed `url` (inline) and `download_url` (attachment). `/video` and `/download` redirect to them in production. |

## Rendering

A render runs only after the user clicks Render Video and confirms. Processing never starts one on its own. The render uses the approved plan and does not search for footage. Mode B uses only the session's uploaded clips. The existing composer keeps one narration track from 0 to the end, muted B-roll, captions taken from the plan, the corner logo, and the music bed at 0.12 under speech and 0.28 in the open. The outro is appended by `render.outro.append_outro`. A render is marked completed only when the finished file is 720×1280 at 30 fps, has picture and audio, its length matches the lesson plus the outro, and the narration correlation is at least 0.90.

HyperFrames needs free memory. On this machine a render failed once while capturing frames, and the retry succeeded.

A render uses the plan version that is current when it is queued, and records that version id and the title. Editing the plan afterwards creates a new version; earlier videos stay available.

## B-roll analysis, review, versions, and titles (Phase 4)

In My B-Roll, processing has an extra stage, `analysing_footage`. Each uploaded clip is sampled at 2 to 12 frames, depending on its length. Black, flash, and flat frames are rejected, and so are near-duplicates. Each clip gets a 320 px thumbnail.

The frames are analysed by a short-lived worker that runs under the engine interpreter, offline, with the cached CLIP ViT-B/32 weights and the Snowflake arctic-embed-xs text model. The API process never loads torch.

Results are cached under `data/studio/visual/<sha256>/`. A changed file has a new hash and is analysed again. A failed analysis is recorded and retried on the next run.

`STUDIO_VISUAL_PROVIDER` chooses the backend:

- `auto` (the default) uses CLIP when the worker is available.
- `clip` uses CLIP.
- `gemini` sends up to three frames per clip to Gemini. This is opt-in and costs money.
- `description` uses the text model only.
- `none` uses no models; matching falls back to keywords.

`STUDIO_ENGINE_PYTHON` points at the engine interpreter. It defaults to the project's Python 3.13.

Matches show their evidence: frames and description, description only, or keywords only. The matcher prefers no cutaway over a weak one, and records why each clip was or was not used. The plan can be reviewed in the Studio:

- approve or reject a cutaway;
- swap the clip;
- move it on the timeline;
- move its source window;
- set or change the title.

Each saved change is rebuilt by the engine plan builder and validated again, then stored as a new immutable version under `work/<session>/plans/`. `POST /api/sessions/{id}/rematch` analyses and matches again as a new version.

The title is used exactly as typed, in any language. `[[word]]` shows that word in yellow. When no title is saved, the title line stays empty; a transcript-based suggestion is shown and is used only if the user saves it.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sessions/{id}/editing-plan/edits` | Body: `base_version_id`, the full `placements` list (asset, timeline, source, approval), and optionally `title` with `change_title: true`. Returns the new version. 409 if the base is stale, 422 if the change is invalid. |
| GET | `/api/sessions/{id}/editing-plan/versions` | Every version with its parent, origin, validation result, and the renders made from it. |
| GET | `/api/sessions/{id}/editing-plan/versions/{version_id}` | One stored version. |
| POST | `/api/sessions/{id}/rematch` | Analyse the clips and match again (My B-Roll). The previous version stays. |
| GET | `/api/sessions/{id}/assets/{asset_id}/thumbnail` | The clip's thumbnail, for the owner only. |

`POST /api/sessions` accepts an optional `title`.

## Intelligent editing (upgrade after Phase 5)

Design notes: `EDITING_UPGRADE_ARCHITECTURE.md`. Everything below is stored on the plan version, can be inspected and changed before rendering, and is written by the existing HyperFrames renderer.

**Editing director (Mode B).** `api/app/processing/director.py` rebuilds phrases from ElevenLabs word timestamps (punctuation, pauses, clause starters such as "na hi", "aur", "jab", "and", "because") and scores every clip against every phrase: description concepts spoken in the phrase (with a small Hindi/English lexicon), the described action, negation (a crossed-out clip only belongs to speech that says "no"/"na hi"), text-model and CLIP similarity when available, timing and clip length. The earliest phrase wins unless a later one is clearly stronger, and the cutaway starts on the first word of the clause that names the subject. Consecutive phrases may get back-to-back cutaways of at least 1 s; a cutaway on its own still lasts at least 2 s. Each placement records `matched_phrase`, `phrase_start`, the per-signal `scores`, which mention it used (`occurrence`), and why. On the test lesson the newspaper clip starts at 0.12 s ("naa hi mainne newspaper padha"), NCERT at 1.26 s, YouTube at 6.08 s, and nothing lands on the later "newspaper padh lo" advice. A user instruction can pin a clip to a phrase (`target_phrase` in an edit); it is recorded as `user_override`.

**Look and sound settings** (`settings` on each plan version, `api/app/processing/settings.py`): framing (fill / balanced / whole frame, teacher height, automatic or manual horizontal focus), B-roll fit, clean cuts or a short fade, captions (size, position, colours, box/outline/shadow, words per caption, capitals), music (default track, bundled library, your upload, or none; volume, level during speech and pauses, fades, ducking, continue under the outro), effects (glitter, sparkle, cinematic, warm, cool, black and white, contrast, saturation, brightness, vignette, background blur, slow zoom; each with intensity and where it applies), and the outro fill. Plans made before settings existed keep the original card layout.

**Renderer.** For plans with settings, `hyperframes_adapter/studio_layout.py` writes a full-canvas composition: the teacher cropped around where the teacher is (measured from motion in the lesson), a blurred frame of the lesson behind it, full-screen B-roll, hard cuts, captions with the spoken word highlighted, and effects as CSS filters, overlays and GSAP tweens. The outro is framed over a blurred copy of itself and the music continues under it. HyperFrames is retried once if its capture browser dies.

**Music library.** `assets/music_library/` holds tracks taken from Wikimedia Commons through the engine's licence and instrumental gate (`deploy/build_music_library.py`); `library.json` records title, author, licence and source page. Categories with no track had no candidate that passed the gate. Custom music: `kind: "music"` uploads (MP3, WAV, M4A, AAC; 5 s to 15 min; up to 50 MB), checked with ffprobe like videos.

**Editing assistant** (optional, `api/app/processing/assistant.py`). English or Hinglish instructions become structured operations (move, remove, shift, change duration, music volume, effects, captions, framing) that are validated and applied through the normal edit path, so each change is a new plan version and "Undo last change" restores the previous one as another version. A clip moved onto the next one pushes that one later as a visible extra step. Unclear requests get a question. Gemini is used only if `GEMINI_API_KEY` is set on the API service; otherwise the built-in rules answer.

**Quality report.** After each render `quality_report.json` records canvas, duration, decoding, black frames, clipping, audio continuity, B-roll visibility, caption visibility and position, hard cuts, outro framing and outro music. Hard failures stop the render from being marked completed.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sessions/{id}/editing-plan/edits` | Now also accepts `settings` (partial) and per-placement `target_phrase` / `duration_seconds`. |
| POST | `/api/sessions/{id}/assistant/propose` | Instruction → understood text and operations, or a question. Changes nothing. |
| POST | `/api/sessions/{id}/assistant/apply` | Apply proposed operations as a new version. |
| POST | `/api/sessions/{id}/assistant/undo` | Restore the parent version as a new version. |
| GET | `/api/music/library`, `/api/music/library/{id}/preview` | Bundled tracks and previews. |
| GET | `/api/uploads/{id}/preview-url` | Signed preview of one of your uploads. |

## What the studio does not do

It does not batch-render, and the repository does not deploy itself: the owner connects the accounts in `deploy/DEPLOYMENT.md`. Model weights are downloaded only while building the worker image. Automated tests replace the transcription provider, the footage selector, the model worker, and the HyperFrames CLI. Every other step runs the real code on small generated clips. Mode A footage selection is unchanged.
