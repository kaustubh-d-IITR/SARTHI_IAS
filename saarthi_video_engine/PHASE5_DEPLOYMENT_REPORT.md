# Phase 5 — Production Deployment: Report

## Status at a glance

The Studio is ready to deploy, but it has not been deployed. Every piece of the production arrangement is in the repository and was tested on this machine, including two production-shaped rehearsals that used the real HyperFrames renderer. No Render, Vercel, or bucket account was available, so nothing runs on the internet and there are no production URLs.

| Status | What it covers |
| --- | --- |
| Implemented locally | Database and migrations, S3/local storage providers, direct uploads, durable job queue and worker, sign-in with CSRF and ownership checks, readiness checks, structured logs, frontend sign-in and session recovery, Dockerfiles, `render.yaml`, `vercel.json`, env templates, optional import tool, deployment guide |
| Tested locally | 75 API tests, 172 engine tests, 26 frontend tests, typecheck, production build; two rehearsals (Mode B and Mode A) with password sign-in, an S3 stand-in, the queue, a separate worker process, and real renders |
| Deployed to staging | Nothing |
| Deployed to production | Nothing |
| Not yet configured by the owner | Git remote, Render Blueprint (database, API, worker), Vercel project, R2/S3 bucket with CORS, ElevenLabs key on the worker, optional Gemini key, first studio account, staging smoke test, production approval |

Docker images were not built here (Docker is not used on this machine), PostgreSQL was not run (SQLite stood in for it), and the Linux HyperFrames path was not executed (the local worker used the Windows path that Phase 3 used). The first Render build is where those three get exercised; section 12 lists what to watch.

## 1. Production architecture

```
Browser ──► Vercel: web/dist, SPA fallback, /api/* rewritten to Render (cookie stays first-party)
                 │
                 ▼
            Render web service  sarthi-studio-api   (deploy/api.Dockerfile, uvicorn)
              │   • sign-in, ownership, CSRF, rate limits, validation
              │   • enqueues jobs; never runs transcription, analysis, or rendering
              │
              ├──► PostgreSQL (Render): users, sessions, uploads, jobs, plan versions, renders, audit
              │                 ▲
              │                 │ claims jobs (lease + heartbeat)
              │      Render background worker  sarthi-studio-worker  (deploy/worker.Dockerfile)
              │        • ElevenLabs transcription (hash-cached), CLIP / Snowflake analysis (offline),
              │          existing plan builder, existing HyperFrames 0.8.186 render, outro, audio check
              │                 │
              └──────► Private S3-compatible bucket (R2 or S3) ◄─┘
                         uploads/  work/  visual/  transcript_cache/  renders/
                         reached only through short-lived signed URLs
```

The browser uploads straight to the bucket with a signed PUT and plays or downloads videos through signed GET URLs. The API checks every upload after it lands. The worker copies the objects a job needs to a scratch folder, runs the unchanged engine there, copies the results back before announcing success, and empties the scratch folder.

## 2. Infrastructure choices and rationale

- **PostgreSQL as both database and queue.** A job is a row with a unique idempotency key and a per-session slot (`uq_jobs_one_active_per_slot`). The database itself refuses a second active process or render job, so a double click cannot create two. Claiming is one atomic `UPDATE`. That avoids running and paying for Redis for a single-team studio.
- **One worker process, one job at a time.** CLIP plus headless Chrome need about 2 GB. More throughput means more worker instances; the queue gives each job to one of them.
- **Object storage for every file.** Render's disk is ephemeral and the API and worker are separate machines. Keys mirror the old relative paths (`uploads/<uuid>/media.mp4`, `renders/<session>/<render>/final.mp4`), so the engine code and plan files did not change.
- **Direct uploads.** A 500 MB lesson never passes through the API, which avoids request-size and timeout limits on Render and Vercel. The API still checks size, type, and ffprobe output on the stored object.
- **Two images.** The API image has Python, ffmpeg, and the engine code (plan edits and render checks import it). The worker image adds Node 22, the pinned HyperFrames Chrome, CPU-only torch, and both models fetched at build time. The API image stays small and redeploys quickly.
- **Vercel rewrite instead of a cross-site API.** With `/api/*` proxied, the session cookie is first-party and can stay `SameSite=Lax`. Calling Render directly is supported (`STUDIO_COOKIE_SAMESITE=none`) but not recommended.
- **Server-side sessions with a hashed token.** Sign-out and disabling an account take effect immediately, which a stateless token would not give.

## 3. Files created and modified

Created:

| Area | Files |
| --- | --- |
| Design and report | `PHASE5_DEPLOYMENT_ARCHITECTURE.md`, `PHASE5_DEPLOYMENT_REPORT.md` |
| API core | `api/app/db.py`, `objects.py`, `workspace.py`, `jobqueue.py`, `worker.py`, `auth.py`, `cli.py`, `logs.py` |
| Import tool | `api/app/tools/__init__.py`, `api/app/tools/import_local.py` |
| Migrations | `api/alembic.ini`, `api/migrations/env.py`, `script.py.mako`, `versions/0001_initial.py` |
| Tests | `api/tests/test_phase5.py` (14 tests), `web/src/test/auth.test.tsx` (6 tests) |
| Frontend | `web/src/components/Auth.tsx`, `RecentSessions.tsx`, `web/src/pages/SessionPage.tsx` |
| Deployment | `render.yaml`, `.gitignore`, `.dockerignore` (project root); `deploy/api.Dockerfile`, `worker.Dockerfile`, `requirements-models.txt`, `fetch_models.py`, `smoke_local.py`, `DEPLOYMENT.md`; `web/vercel.json` |
| Env templates | `api/.env.production.example`, `web/.env.production.example`, `api/requirements-dev.txt` |

Modified:

| Area | Files |
| --- | --- |
| API | `api/app/config.py` (production settings and refusal, Postgres URL normalisation, cookie SameSite), `factory.py` (auth, readiness, middleware, direct-upload and file routes, session list, signed video links), `storage.py` (database records, object keys), `schemas.py`, `processing/jobs.py` (database recovery, enqueue before write, publish before `plan_ready`), `services/rendering/jobs.py` (enqueue, publish before `completed`), `services/rendering/adapter.py` (object checks without downloading), `services/rendering/bridge.py` (Linux HyperFrames command, music path) |
| API config | `api/requirements.txt`, `api/.env.example` |
| Existing tests | `api/tests/test_studio.py`, `api/tests/test_phase2.py` (three assertions that read the old JSON files now read the database) |
| Frontend | `web/src/App.tsx`, `types.ts`, `lib/api.ts`, `components/Shell.tsx`, `ProcessReview.tsx`, `RenderPanel.tsx`, `pages/HomePage.tsx`, `AutoEditPage.tsx`, `MyBrollPage.tsx`, `test/studioUi.test.tsx`, `web/.env.example` |
| Repository | `saarthi_video_engine/.gitignore` (keeps the licensed music file, ignores new local folders), `STUDIO.md` |

Not modified: the engine under `src/`, every `run_phase*.py`, `configs/`, HyperFrames, caption styling, music gains, the 58% B-roll cap, Mode B thresholds, Mode A licensing, raw videos, the reference dataset, the logo, the outro, and earlier outputs. A SHA-256 check of the 204 protected files matches the Phase 3 baseline.

## 4. Storage implementation

`api/app/objects.py` defines one interface with two providers.

- Operations: `put_file`, `put_bytes`, `get_bytes`, `download`, `delete`, `head` (size), `list`, `exists`, `sha256`, `presign_get` (inline or attachment), `presign_put`, `ping`.
- **Keys.** Keys are built by the server from UUIDs and SHA-256 values. `check_key` admits only the five prefixes and rejects `..`, `//`, absolute paths, backslashes, and trailing slashes. A user's filename is kept as display text only.
- **`LocalStorageProvider`.** Files live under the data directory. URLs are HMAC-signed `/api/files/<token>` links with an expiry and an operation (get or put). Used for development and tests.
- **`S3StorageProvider`.** Uses boto3 (SigV4, path-style) against R2 or S3. Objects are never written with a public ACL. URLs are presigned for `STUDIO_SIGNED_URL_SECONDS` (900 by default). `ping` also writes a probe object and tries to read it without credentials; if that works, `/api/ready` fails with "the bucket allows anonymous reads". I added that check after the rehearsal showed that the S3 stand-in serves multipart objects publicly, a quirk of moto that real buckets do not share.
- **`Workspace`.** `api/app/workspace.py` copies objects to and from a job's scratch folder and skips files whose size and modification time have not changed. With local storage it is a pass-through.
- **Upload checks.** `POST /api/uploads/{id}/complete` reads the stored object's size with HEAD. It rejects an empty file, an oversized file, or one whose size differs from what the browser announced, then runs ffprobe on the stored object, which must show a video stream, a positive duration, and dimensions. A rejected object is deleted. The SHA-256 is computed by the worker and stored on the upload.
- **Video delivery.** `/video-url` returns a signed `url` and `download_url` after an ownership check. `/video` and `/download` redirect to them (307). The player asks for fresh links when one expires.
- **Older local files.** These are never uploaded automatically. `python -m app.tools.import_local` does a dry run unless given `--apply`.

## 5. Database schema and migrations

SQLAlchemy Core tables, created by Alembic revision `0001_initial`. The downgrade drops them.

| Table | Purpose and key constraints |
| --- | --- |
| `users` | id, unique email, display name, scrypt hash, `is_active` |
| `auth_sessions` | SHA-256 of the session token (primary key), user, CSRF token, created, last seen, expiry, IP, user agent |
| `login_attempts` | email, IP, success, time (lockout window) |
| `uploads` | id, owner, kind (raw/broll), status (pending/valid/rejected), object key, display filename, announced size, stored size, SHA-256, probe metadata |
| `studio_sessions` | id, owner, mode, status, current plan version, full session record (JSON) |
| `jobs` | id, session, owner, kind, status, payload, unique idempotency key, `active_slot` with unique (session, slot), attempts / max attempts, lease owner and expiry, heartbeat, error, failure category |
| `plan_versions` | id, session, unique (session, number), parent, origin, validation result, title, object key of the immutable plan file |
| `renders` | render id, session, job, plan version, status, object prefix, details (metadata, audio check, outro, title), error |
| `audit_events` | time, event, user, session, detail (sign-in, failed sign-in, uploads, plan edits, renders) |
| `workers` | worker id, version, last heartbeat (readiness) |

Large files never go into the database; plan versions and renders store object keys.

Commands:

- `python -m app.cli migrate` runs `alembic upgrade head`. Render runs it as the pre-deploy command, and production does not migrate on start (`STUDIO_AUTO_MIGRATE=false`). Development migrates on start.
- Tested: upgrade, a repeat upgrade (no-op), downgrade to base, and upgrade again on SQLite.
- Local data: nothing is discarded. Older JSON sessions stay where they were; the import tool reads them and writes rows and objects only with `--apply`. A dry run against this machine's `data/studio` found 6 uploads, 5 sessions, and 81 files, all with valid keys.

## 6. Queue and worker architecture

- **Enqueue.** `POST /process`, `/rematch`, and `/render` accept an `Idempotency-Key`. The job row is inserted before the session record changes. If the same key comes back, the API returns the same job. A different key while a job holds the slot gets 409 `job_in_progress`. The frontend sends a fresh key per click, so a double click on Render gives one render and one 409.
- **Claim.** The worker (`python -m app.worker`) polls every 3 seconds and claims one queued job with an atomic `UPDATE ... WHERE status='queued'`. It sets a lease of `STUDIO_JOB_LEASE_SECONDS` (180) and renews it every third of that from a heartbeat thread. It records its own heartbeat in `workers`.
- **Run.** The worker pulls the session's objects, runs the same `run_processing` or `run_render` code that Phases 2–4 used, and copies the results to storage before writing `plan_ready` or `completed`. The rehearsal caught that ordering: Mode A had written `plan_ready` while about 50 MB of footage was still uploading, so the API briefly could not find the plan. A regression test now checks the ordering for both job types.
- **Worker crash.** The reaper (`recover_expired`) finds expired leases. A processing job is requeued once (2 attempts) and then fails. A render fails at once (1 attempt): its render record is marked failed, `failure.json` is written under its prefix, earlier renders and logs stay, and the user can render again under a new render ID.
- **Restarts.** Job and session state live in the database, so an API or worker restart loses nothing. In development (`STUDIO_JOB_RUNNER=inline`) jobs run inside the API through the same claim path, and a restart marks interrupted ones failed so they can be retried.
- **Rendering.** The worker uses the existing render adapter and bridge. On Linux the bridge runs `npx --yes hyperframes@0.8.186 lint`, then `render --quality draft --workers 1 --output ...` with the same log files as `run_phase4._hyperframes`. On Windows it calls `run_phase4._hyperframes` itself.
- **Visual analysis.** This is explicit about what ran. `STUDIO_VISUAL_PROVIDER=auto` uses the offline CLIP and Snowflake models baked into the worker image. If they cannot load, the plan says `none` and matching falls back to keywords; it never claims frames were analysed when they were not. Gemini runs only when `GEMINI_API_KEY` is set and the provider allows it. Mode A and Mode B stay separate: Mode B places only the session's own uploads (checked in the rehearsal), and Mode A keeps the existing Commons licence checks.

## 7. Authentication and authorization

- **Accounts.** Accounts are created by the owner: `python -m app.cli create-user --email ... --name ...`, with the password typed at a hidden prompt (at least 12 characters). Also `set-password` (which ends that user's sessions), `disable-user`, and `enable-user`. There is no self-registration. No email, password, or account was invented for production. The rehearsals used a throwaway account (`smoke@example.invalid`, random password, never printed) in a temporary database that was discarded.
- **Passwords.** Hashed with scrypt (n=2^15). Unknown emails take the same time to reject. The sign-in error is generic.
- **Sessions.** A random 32-byte token goes in the `sarthi_session` cookie: HttpOnly, Secure in production, SameSite=Lax, 7 days. The server stores only its SHA-256, so sign-out and disabling an account take effect at once.
- **CSRF.** Every POST, PUT, PATCH, or DELETE needs `X-CSRF-Token` matching the session; sign-in and signed file URLs are exempt. The rehearsal confirmed a missing token gets 403.
- **Rate limits.** Five failed sign-ins for one email in 15 minutes return 429 for the rest of the window. Sign-in is also limited to 20 requests per minute per IP, and all routes to 600 per minute per IP per instance. Request bodies are capped at 2 MB outside uploads.
- **Ownership.** Every upload, session, plan, version, thumbnail, render, and video route compares the owner and answers 404 for anyone else (tested with a second account). Thumbnails and videos are served only through owner-checked routes or signed URLs.
- **`X-Studio-Client`.** Only honoured when `STUDIO_AUTH_MODE=client_header`, and production refuses to start in that mode.
- **Manager access.** The owner creates one account per manager with the CLI, from the Render API service's shell. Access is removed with `disable-user`.

## 8. Environment variable reference

API and worker (Render). Templates: `api/.env.production.example` (production), `api/.env.example` (development).

| Variable | Production value | Notes |
| --- | --- | --- |
| `STUDIO_ENV` | `production` | Turns on the startup refusal of unsafe settings |
| `DATABASE_URL` | from Render database | `postgres://` is converted to the psycopg driver |
| `STUDIO_STORAGE_BACKEND` | `s3` | `local` in development |
| `OBJECT_STORAGE_ENDPOINT` | R2 endpoint, or empty for AWS | Owner |
| `OBJECT_STORAGE_BUCKET` | bucket name | Owner |
| `OBJECT_STORAGE_ACCESS_KEY`, `OBJECT_STORAGE_SECRET_KEY` | bucket-scoped key | Owner; secret |
| `OBJECT_STORAGE_REGION` | `auto` (R2) or the AWS region | |
| `STUDIO_SCRATCH_DIR` | `/var/studio/scratch` | Set in the images |
| `STUDIO_AUTH_MODE` | `password` | |
| `STUDIO_SESSION_SECRET` | generated by Render | 32+ characters; same on API and worker |
| `STUDIO_SESSION_TTL_HOURS` | `168` | |
| `STUDIO_COOKIE_SECURE` | `true` | |
| `STUDIO_COOKIE_SAMESITE` | `lax` | `none` only for a cross-site API |
| `STUDIO_JOB_RUNNER` | `queue` | `inline` in development |
| `STUDIO_JOB_LEASE_SECONDS` | `180` | |
| `STUDIO_SIGNED_URL_SECONDS` | `900` | |
| `STUDIO_AUTO_MIGRATE` | `false` | Migrations run pre-deploy |
| `STUDIO_RATE_LIMIT_PER_MINUTE`, `STUDIO_LOGIN_ATTEMPTS` | `600`, `5` | |
| `STUDIO_MAX_FILE_BYTES`, `STUDIO_MAX_SESSION_BYTES` | 512 MB, 2 GB | |
| `STUDIO_CORS_ORIGINS` | the Vercel origin | API |
| `ELEVENLABS_API_KEY` | owner's key | Worker only; secret |
| `GEMINI_API_KEY` | empty unless wanted | Worker only; secret; costs money |
| `STUDIO_VISUAL_PROVIDER` | `auto` | `clip`, `gemini`, `description`, `none` |
| `STUDIO_ENGINE_PYTHON` | `/usr/local/bin/python` | Set in the worker image |
| `HF_HOME` | `/opt/hf` | Model cache baked into the worker image |
| `STUDIO_WORKER_POLL_SECONDS`, `STUDIO_WORKER_ID` | optional | Worker tuning |

Frontend (Vercel). Template: `web/.env.production.example`.

| Variable | Value | Notes |
| --- | --- | --- |
| `VITE_API_BASE_URL` | empty | `vercel.json` proxies `/api`. Everything in `VITE_` is public; no keys go there. |

`.env` files are ignored by both `.gitignore` files and by `.dockerignore`.

## 9. Deployment instructions

The full runbook is `deploy/DEPLOYMENT.md`. In short:

1. Create a git repository at the project folder (the root that holds `LOGO/`, `OUTRO/`, `reference_dataset/`, and `saarthi_video_engine/`). The root `.gitignore` admits only deployable files; a check of 26 sample paths confirmed raw lectures, historical outputs, uploads, tools, and `.env` files stay out, while the logo, outro, dataset JSON, licensed music, and code go in.
2. Create a private R2 or S3 bucket, a key scoped to it, and the CORS rule for the Vercel origin (PUT, GET, HEAD; `content-type`).
3. In Render, create a Blueprint from `render.yaml`. Enter the storage values, `STUDIO_CORS_ORIGINS`, and the worker's `ELEVENLABS_API_KEY`. Confirm the plans. Render builds both images, runs `python -m app.cli migrate` before the API starts, and health-checks `/api/live`.
4. In the API service shell, run `python -m app.cli create-user --email <address> --name "<name>"`.
5. In Vercel, import the repository with Root Directory `saarthi_video_engine/web` and leave `VITE_API_BASE_URL` empty. If Render named the API differently, update the hostname in `vercel.json`.
6. Verify `/api/ready`, sign-in, refresh, and the staging smoke for both modes (runbook section 6) before calling anything deployed.

| Item | Value |
| --- | --- |
| API build | `docker build -f saarthi_video_engine/deploy/api.Dockerfile .` |
| API start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` |
| Worker build | `docker build -f saarthi_video_engine/deploy/worker.Dockerfile .` |
| Worker start | `python -m app.worker` |
| Health paths | `/api/live` (liveness), `/api/ready` (database, storage including privacy, queue, worker heartbeat, ffmpeg), `/api/health` (configuration) |
| Frontend build | `npm ci && npm run build`, output `dist`, SPA fallback in `vercel.json` |

## 10. Test results

All runs were on this machine on 31 October 2026.

| Suite | Result |
| --- | --- |
| API (`api/tests`, pytest) | 75 passed. Phase 5 adds 14: local provider; S3 provider against a moto server over HTTP (private, signed GET and PUT); bucket privacy readiness; migrations up, down, and up; production refusal, Postgres URL, and SameSite rules; queue duplicates, idempotency, and single claim; lost worker retry then failure; sign-in, cookie, CSRF, lockout, ownership, and audit; direct-upload rejections (size mismatch, junk file, forged token, oversize, stranger); readiness; a full S3 flow with separate API and worker scratch disks (crash, retry, signed video, versions, old render kept, restart); files published before results are announced; inline restart; import tool (dry run changes nothing, copies, recovers interrupted sessions, idempotent, source unchanged) |
| Engine (`tests/`, excluding `test_phase12.py`) | 172 passed |
| Frontend (vitest) | 26 passed, including sign-in gate, unreachable API, signed-out event, session recovery after refresh, missing session, and CSRF and idempotency headers |
| TypeScript and build | `tsc --noEmit` clean; `vite build` succeeded |
| Protected files | 204 of 204 unchanged against the Phase 3 SHA-256 baseline; rollback hold present; `src/broll_bot/` absent |
| Credential scan of new and changed config, deploy, and API files | No key-like values |

One observation: with the engine suite running at the same time, `test_title_edit_render_uses_the_exact_version_and_keeps_earlier_videos` failed once, when ffprobe could not read a freshly concatenated file. It passed alone three times and in two later full runs. I treat it as load-related flakiness on this machine and have not changed the test.

No ElevenLabs, Gemini, or paid call was made by any test or rehearsal, and no historical batch render was run.

## 11. Staging smoke-test results

No staging environment exists, so there is no staging result. In its place I ran a production-shaped rehearsal on this machine with `deploy/smoke_local.py`:

- password sign-in;
- moto as the S3-compatible bucket;
- direct uploads;
- the queue runner, with the API and a separate worker process sharing only the database and the bucket, each with its own scratch folder;
- the real engine, including HyperFrames.

Paid calls were impossible in this setup: both API keys were blanked in the child processes, and each lesson's existing ElevenLabs transcript was placed in the hash-keyed cache.

| Check | Mode B (Edit with My B-Roll) | Mode A (AI Auto-Edit) |
| --- | --- | --- |
| Input | 41.6 s lesson (79 MB, 1080×1920) and one 4.4 s PPE clip with description | 55.9 s lesson (59 MB, 1280×720) |
| Sign-in | Without session 401; wrong password 401; missing CSRF 403; cookie HttpOnly, SameSite=Lax | same |
| Readiness | 200: database, object storage, queue, worker, ffmpeg | same |
| Upload | Signed PUT straight to the bucket, then server-side size and ffprobe check | same |
| Processing | Repeated key returned the same job; queued → analysing_footage → selecting_footage → plan_ready in 69 s; transcript reused (no API call); CLIP analysed 1 clip; 1 cutaway; only the user's clip placed | Repeated key returned the same job; queued → selecting_footage → plan_ready in 39 s; transcript reused; 2 Commons cutaways through the existing licence checks |
| Review and edit | Title saved as `Smoke [[Test]]`: version 2 with parent v001 | Plan reviewed; no edit |
| Render | Second click 409; render_queued → preparing → rendering → assembling_outro → completed in 93 s | Second click 409; completed in 105 s |
| Output | 720×1280, 30 fps, 49.65 s with audio; narration correlation 0.9645 | 720×1280, 30 fps, 63.92 s with audio; correlation 0.9879 |
| Preview and download | Signed URL 200; `/download` 307 to a signed attachment; bytes identical | same |
| HyperFrames used | `composition/lint.txt` and `composition/render.txt` under the render prefix | same |

Frames from both videos show the unchanged identity: corner logo, the title with its yellow accent word, transliterated captions, the cutaway placed where the speech mentions a glove and an N95 mask (Mode B), and the original outro.

The first Mode A run failed. That run exposed the publish-ordering bug described in section 6, which I fixed and covered with a test; the second run passed. The unsigned GET of `final.mp4` returned 200 in both runs; that is moto not enforcing permissions on multipart uploads. A direct probe confirmed a single-PUT object gets 403 and a multipart object 200 on moto. Real R2 and S3 private buckets deny both, and `/api/ready` now fails if a bucket serves an unsigned read.

The runbook's staging smoke (section 6 of `deploy/DEPLOYMENT.md`) repeats these checks on Render and Vercel. It has not been run.

## 12. Known limitations

- **Not deployed.** Nothing has run on Render, Vercel, or a real bucket, so the Definition of Done items that need staging (upload through download working in staging) are open.
- **Docker images not built here.** The Dockerfiles have not been built. Risks to watch on the first build: the CPU torch wheel `torch==2.11.0` for Python 3.13, the Debian Bookworm package names, and `hyperframes browser ensure` downloading Chrome.
- **PostgreSQL not exercised.** Migrations and queries ran on SQLite only. The schema uses portable SQLAlchemy Core types, the URL conversion is tested, and the psycopg driver is pinned, but the first Render deploy is the first Postgres run.
- **Linux render path not executed.** It runs the same HyperFrames commands; the local rehearsal used the Windows path.
- **Fonts on Linux.** The title card names Arial. The worker image uses Liberation Sans, which has the same metrics. Inter is fetched by HyperFrames on the first render and cached. Compare one staging render with a local one before approving production.
- **Worker size and throughput.** The worker needs about 2 GB (Render `standard`) and runs one job at a time. Chrome inside a container may want more `/dev/shm`; `npx hyperframes doctor` reports it.
- **Rate limiter.** Per instance, in memory. The sign-in lockout is in the database and holds across instances.
- **Older local sessions.** Not visible in the new Studio until imported with `app.tools.import_local`. The local API on this machine now uses `data/studio/studio.db`, created at start, and the old JSON files are still there.
- **Vercel rewrite hostname.** Assumes the Render hostname `sarthi-studio-api.onrender.com`; edit `vercel.json` if Render assigns another.
- **One flaky test.** The load-related failure described in section 10.

## 13. Security considerations

- **Secrets.** Kept only in Render environment variables (`sync: false` or generated). None are in source, `render.yaml`, `vercel.json`, Docker images, or `VITE_` variables. `.env` files are excluded from git and Docker contexts. Logs pass through `mask()`, which redacts keys, secrets, passwords, tokens, cookies, authorization headers, and signed-URL signatures.
- **Startup refusal.** Production refuses to start without password sign-in, PostgreSQL, object storage with credentials, the queue runner, a 32-character secret, and secure cookies.
- **Storage.** The bucket is private and access is only through short-lived signed URLs, issued after an ownership check. Readiness fails if the bucket answers an unsigned read. Keys are server-built and validated, so a filename cannot traverse paths. The local file route verifies an HMAC token with an expiry and an operation.
- **Uploads.** The browser's declared size and type are not trusted: the stored object's size and ffprobe output decide. Files that fail are deleted. Per-file and per-session limits are enforced on the server.
- **Sign-in.** scrypt hashes; server-side sessions with a hashed token; HttpOnly, Secure, SameSite cookie; CSRF header on unsafe methods; lockout and rate limits; generic errors; audit events for sign-in, failure, upload, edit, and render.
- **Headers.** The API sends `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on API responses. Vercel adds the same frame, type, referrer, and permissions headers to the site. CORS lists explicit origins with credentials only in password mode.
- **Errors and logs.** Users see friendly messages. Engine details stay in diagnostics files under the session or render prefix and in logs, and paths are stripped from user-facing errors.
- **What the owner should still do.** Enable bucket versioning or lifecycle rules as needed, restrict who can open a shell on the Render API service (it can create accounts), and rotate the storage key if it is ever exposed.

## 14. Production URLs

None. Nothing has been deployed to staging or production, so there are no URLs to report. The services appear only after the owner applies the Render Blueprint and imports the Vercel project, and should be called deployed only after the checks in `deploy/DEPLOYMENT.md` section 6 pass.
