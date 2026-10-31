# Phase 5 deployment architecture

Written on 31 October 2026, before any Phase 5 code. It records what the Studio depends on today, the production architecture chosen, and how the code moves to it.

## 1. Audit of the current Studio

### 1.1 Data stored locally

Everything lives under `data/studio/`, inside the OneDrive folder:

| Data | Location | Size |
| --- | --- | --- |
| Upload metadata | `uploads/<uuid>/meta.json` | small JSON |
| Raw lessons and B-roll | `uploads/<uuid>/media.<ext>` | up to 512 MB each |
| Session records, render state and history, title, plan pointer | `sessions/<uuid>.json` | small JSON |
| Transcript, catalogue, `footage_index.json`, `editing_plan.json`, immutable `plans/v###-*.json`, Mode A downloaded Commons clips (`footage/`), `library.sqlite` | `work/<session>/` | JSON, plus clips |
| Sampled frames, thumbnail, `sampling.json`, CLIP or Gemini analysis | `visual/<sha256>/` | about 1 MB per clip |
| Hash-keyed ElevenLabs transcripts | `transcript_cache/<sha256>.json` | small JSON |
| Final MP4, manifests, logs, plan snapshot, composition HTML | `renders/<session>/<render>/` | 5–15 MB per render |

### 1.2 Operations that depend on local paths

- `StudioStore.media_file()` returns a path that ffprobe, ffmpeg, the visual worker, and the renderer open directly.
- `work_dir()` is read and written by processing, edits, versioning, and rendering.
- The render adapter cuts media, writes the composition, runs HyperFrames, and joins the outro in `renders/<session>/<render>/`.
- The engine reads its code, `configs/`, `schemas/`, `reference_dataset/shorts_dataset.json`, `LOGO/`, `OUTRO/`, and the licensed music file `outputs/dr_shivin_phase5/asset_cache/inspire-action-yuri-megis.ogg` with its record in `candidates.json`. That record holds a Windows absolute path.
- `run_phase4._hyperframes` finds `npx-cli.js` next to `node.exe`, which is Windows-specific. On Linux, npm lives elsewhere.

### 1.3 Assumption of a shared filesystem

Processing and rendering run in the API process through FastAPI `BackgroundTasks`. Upload, processing, review edits, rendering, thumbnail serving, and video download all assume the same disk. A separate worker would not see the API's files.

### 1.4 Sessions and ownership

The browser sends `X-Studio-Client`, a random UUID kept in `localStorage`. Each upload and session stores it as `client_id`, and every route compares it. That separates browsers; it is not authentication.

### 1.5 Jobs across an API restart

Jobs are threads inside the API. On startup, `recover_interrupted` and `recover_render_jobs` mark any job left mid-stage as `failed`, so the user has to retry it. Nothing is queued durably.

### 1.6 Upload validation

Uploads are streamed through the API in 1 MB chunks, with per-file and per-session limits. The suffix must be on an allow-list. ffprobe must find a video stream with a positive duration and dimensions. The stored name is always `media<ext>`, and the client's filename is only displayed. The SHA-256 is computed when processing catalogues the clip.

### 1.7 Video delivery

`GET /video` and `GET /download` stream `final.mp4` from the API after an owner check. The browser fetches it into a blob.

### 1.8 Secrets and external services

- `ELEVENLABS_API_KEY`: transcription, read by `src/config/env.py`. A process environment variable takes priority over the engine's `.env`.
- `GEMINI_API_KEY`: only for the opt-in Gemini visual provider.
- Wikimedia Commons: Mode A search and download, public and keyless.
- Google Fonts and the jsDelivr GSAP CDN, fetched by HyperFrames at render time.
- `npx hyperframes@0.8.186`, which downloads a headless Chrome on first use.
- Hugging Face models (CLIP ViT-B/32 and Snowflake arctic-embed-xs), read offline from the cache.

### 1.9 Engine assets needed for rendering

- `saarthi_video_engine/src`, `configs`, `schemas`, `run_phase4.py`, `run_phase5.py` (and the modules they import), and `pyproject.toml`.
- `reference_dataset/shorts_dataset.json` (1.85 MB). The rest of `reference_dataset` (about 97 MB) is not read.
- `LOGO/shivin sir logo.png` and `OUTRO/WhatsApp Video 2026-10-23 at 12.53.20 PM.mp4`.
- `outputs/dr_shivin_phase5/asset_cache/inspire-action-yuri-megis.ogg` (2 MB) and `candidates.json`.
- ffmpeg and ffprobe, Node 22, `hyperframes@0.8.186`, and the libraries headless Chrome needs.
- For Mode B visual analysis: torch on CPU, transformers, sentence-transformers, and the two cached models (about 235 MB).

None of the raw lessons, historical outputs, or the rest of the reference dataset is needed.

## 2. Production architecture

```
Browser ──HTTPS──> Vercel (React build)
   │                 └─ rewrite /api/* ──> Render web service "sarthi-studio-api" (FastAPI)
   │                                          ├─ PostgreSQL (Render): users, auth sessions, uploads, studio sessions,
   │                                          │   jobs (durable queue), plan versions, renders, audit events, workers
   │                                          └─ Object storage (Cloudflare R2 or S3): private bucket
   └──signed PUT/GET URLs──────────────────────> Object storage (direct upload, preview, download)
Render background worker "sarthi-studio-worker" (same Docker image)
   ├─ claims jobs from PostgreSQL with a lease and heartbeat
   ├─ pulls inputs from object storage into a scratch disk, runs the existing pipeline, pushes results back
   └─ ffmpeg, Node, HyperFrames, the engine and its assets, optional CLIP and text models
```

### 2.1 Choices and reasons

- **PostgreSQL is both the database and the durable queue. There is no Redis.**
  - The load is a few managers and a handful of long jobs. A `jobs` table with leases, heartbeats, an idempotency key, and a unique "active slot" per session gives durable, deduplicated work.
  - The job and the session state change in the same transaction.
  - Redis would add a service, a second source of truth, and its own persistence settings for no gain at this scale.
  - SQLite is used locally and in tests. The schema is written in SQLAlchemy Core, so the same code and Alembic migrations run on both.
- **Cloudflare R2 for object storage** (AWS S3 also works).
  - It speaks the S3 API, charges no egress for video previews and downloads, and supports presigned PUT and GET.
  - The bucket stays private. Object keys are built from server UUIDs and SHA-256 values, never from user filenames.
- **One Docker image for the API and the worker.**
  - The API needs the engine to build and validate plans and to make thumbnails. The worker needs everything.
  - A single image keeps them on the same versions.
  - In the image there is one Python that holds both the Studio's and the engine's packages, so `STUDIO_ENGINE_PYTHON` is that interpreter. This also settles the Windows split between the API virtual environment and the engine interpreter.
- **Vercel serves the static React build and proxies `/api/*` to Render.**
  - The browser then talks to a single origin, so the login cookie is first-party with `SameSite=Lax`. That avoids third-party cookie blocking.
  - Large files never go through Vercel or the API: uploads go straight to R2 with presigned PUT, and playback and downloads come straight from R2 with presigned GET.
  - `VITE_API_BASE_URL` stays supported for a direct cross-origin setup, but the recommended value is empty.
- **Authentication is password login with server-side sessions.**
  - Accounts are created by the owner from a command-line tool. No self-registration.
  - Passwords are hashed with scrypt.
  - The session token goes in an HttpOnly, Secure, `SameSite=Lax` cookie, and only its SHA-256 is stored.
  - Every state-changing request needs a CSRF token header.
  - Repeated failed logins are throttled in the database, and a per-instance rate limiter covers other requests.
  - `X-Studio-Client` remains only in development mode and in tests. Production refuses it.

### 2.2 How the code moves

- **`StudioStore` keeps its interface.** Session and upload records move from JSON files to PostgreSQL, with the full record kept in a JSON column for compatibility. Files move behind an `ObjectStorage` interface:
  - `LocalStorageProvider` is the default in development. Its root is `data/studio`, so local behaviour is unchanged.
  - `S3StorageProvider` is for production.
- **Object keys mirror today's relative paths:** `uploads/<id>/media.mp4`, `work/<session>/...`, `visual/<sha>/...`, `transcript_cache/<sha>.json`, `renders/<session>/<render>/...`.
- **A `Workspace` copies a job's inputs from storage into a scratch directory, and copies new or changed results back afterwards.** With local storage the scratch directory is the storage root, so copying does nothing. Processing, matching, versioning, and rendering keep working on local paths, and none of their logic changes.
- **Jobs** come in two runners:
  - `queue` for production: the API inserts a job row and the worker claims it.
  - `inline` for development and tests: the same job record and claim path run inside the API.
  A worker that stops renewing its lease has its job reclaimed. Processing is retried once. Renders are marked failed with their logs kept, and wait for an explicit retry with a new render ID. Earlier renders are never touched.
- **Uploads use initiate, PUT, and complete.**
  1. Initiate creates a pending row and returns a signed PUT URL, either to R2 or to the API for local storage.
  2. The browser PUTs the file.
  3. Complete checks size and suffix, runs ffprobe on the stored object for duration, dimensions, and a video stream, and records metadata from the server side only. Anything invalid is deleted.
  The SHA-256 is computed when the worker first catalogues the file, and is checked before every render, as before.
- **Video, download, and thumbnail requests** return signed, short-lived URLs after the ownership check. Local storage serves those through an HMAC-signed API route.
- **The plan builder and `validate_plan` stay in the API**, so review edits stay synchronous. The edit pulls the session's small JSON files, applies the change, pushes the new immutable version, and records it in PostgreSQL.
- **Linux compatibility shims** live in the Studio bridge, not the engine:
  - HyperFrames runs with the same `npx --yes hyperframes@0.8.186 lint` and `render --quality draft --workers 1` commands, using the `npx` on PATH. Windows still calls `run_phase4._hyperframes`.
  - The music record's Windows path is resolved to the file beside `candidates.json`.
- **Model availability is explicit.** The image downloads CLIP and Snowflake into a fixed Hugging Face cache at build time, and the worker runs offline. If the models are missing, the Phase 4 description-only fallback applies and is labelled as such. Gemini is off unless `STUDIO_VISUAL_PROVIDER=gemini` and a key are set.

### 2.3 What does not change

The HyperFrames composer, captions, narration timing, music levels, logo, outro, the 58% cap, Mode B thresholds, Mode A selection and licensing, and Mode A/Mode B source separation all stay the same. Historical data stays where it is: no raw lesson or historical output is uploaded anywhere.

## 3. Migration strategy

1. Add configuration with safe development defaults: SQLite at `data/studio/studio.db`, local storage at `data/studio`, the inline runner, and `X-Studio-Client`. Production validation refuses unsafe combinations.
2. Add the SQLAlchemy schema and the first Alembic migration. Development runs it on startup; production runs `alembic upgrade head` before each deploy.
3. Back `StudioStore` with the database and the storage provider, then add the workspace sync.
4. Add the jobs table, the queue functions, the worker, and the inline runner. Remove the API-thread recovery.
5. Add authentication, CSRF, rate limiting, request IDs, structured logs, and readiness checks.
6. Switch the frontend to login, direct uploads, signed media URLs, and session recovery.
7. Add the Dockerfile, `render.yaml`, `vercel.json`, and the environment templates.
8. Make importing existing local Studio sessions an explicit, optional tool. The old JSON and media files are left untouched.

Live deployment needs owner accounts and credentials for Render, Vercel, R2, and the PostgreSQL plan. It also needs the code in a git repository. The engine directory currently has no repository, and the raw lessons must not go into one. Until those exist, staging is approximated locally with a separate worker process and an S3 emulator, and it is reported as local, not as a deployment.
