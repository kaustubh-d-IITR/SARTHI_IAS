# Phase 2 report

Phase 2 connects a prepared Studio session to transcription and an editing plan. It does not render an MP4. No lesson file from `DR_SHIVIN_RAW` was processed. The samples below are synthetic fixtures.

The inspection record is `PHASE2_ARCHITECTURE_AUDIT.md`. The engine under `src/` and the `run_phase*.py` runners were not modified.

## Processing architecture

`POST /api/sessions/{session_id}/process` checks ownership, sets the session to `queued`, and returns that status in the HTTP response. FastAPI `BackgroundTasks` then runs the job. The request does not wait for transcription.

States, in order: `prepared`, `queued`, `transcribing`, `analysing`, `selecting_footage`, `building_plan`, `validating_plan`, `plan_ready`. A failure sets `failed` and keeps the message in the session plus `data/studio/work/<session-id>/diagnostics.json`.

One in-process lock per session stops a second start while the status is active or `plan_ready`. A failed session can be started again. If the API process dies mid-job, the next startup marks that session `failed` with “Processing was interrupted before the plan was saved.” so it can be retried. The job is not resumed in the middle.

Work files, private to the server:

- `data/studio/work/<session-id>/transcript.json`
- `data/studio/work/<session-id>/editing_plan.json` (`engine_plan` plus the public `studio` envelope)
- `data/studio/work/<session-id>/catalogue.json`
- `data/studio/transcript_cache/<sha256>.json`

Public responses omit filesystem paths. The browser still sends `X-Studio-Client`.

Mode A calls `src/assets/footage.py` `select_clips` with `ranker=None`, so CLIP weights are not downloaded. The existing licence checks inside that function stay in place. Mode B never imports it. Mode B matches the user’s descriptions to transcript text and records `visual_analysis: unavailable`. A match is not a visual-verification claim.

The stored plan is built by `build_phase2_plan`. Its picture action remains `replace_with_broll`. The public envelope maps that to `picture_role: show_broll`. Source in/out points and the matching explanation sit beside the schema object because a decision cannot carry extra fields. `BROLL_CAP` is still 0.58. Windows shorter than 2 seconds are still dropped. No match stays on the teacher.

`validate_plan` runs first. The studio then checks explanations, source intervals, timeline bounds, session membership, and, in Mode B, the absence of external footage. `plan_ready` is set only when that combined result has no errors.

ElevenLabs is not called at import or startup. The key is read from the engine environment only inside a job, through the existing `require_elevenlabs_key`. A cached transcript is reused only when the raw file’s SHA-256 still matches. The stored text is the provider text. Roman Hindi is not translated.

## API

All routes except health require `X-Studio-Client`.

| Method | Path | Result |
| --- | --- | --- |
| POST | `/api/sessions/{session_id}/process` | `queued`, then the job runs. 409 if it is already running or the plan is ready. |
| GET | `/api/sessions/{session_id}/status` | `status`, `stage`, `error`, `message`, lesson filename, B-roll count, and counts once the plan is ready. |
| GET | `/api/sessions/{session_id}/transcript` | Segments, original text, word timings, model, provider, reuse flag. 404 before a transcript exists. |
| GET | `/api/sessions/{session_id}/editing-plan` | Public plan. 404 until status is `plan_ready`. `rendered` is false. |

## Transcript example

This object was written by the sample fixture. It was not returned by ElevenLabs.

```json
{
  "language": "en",
  "method": "elevenlabs",
  "model": "scribe_v2",
  "translated_to_english": false,
  "segments": [
    {
      "id": "s0",
      "text": "The Indian Parliament building is where laws are discussed.",
      "original_text": "The Indian Parliament building is where laws are discussed.",
      "start": 0.4,
      "end": 4.2,
      "words": [{ "text": "Parliament", "start": 1.1, "end": 1.7, "logprob": -0.2 }]
    },
    {
      "id": "s1",
      "text": "main hospital ja raha hoon for this example",
      "original_text": "main hospital ja raha hoon for this example",
      "start": 4.4,
      "end": 7.6,
      "words": [{ "text": "hospital", "start": 5.2, "end": 5.8, "logprob": -0.15 }]
    }
  ]
}
```

## Sample Mode A plan

Synthetic. The footage row was injected. No Commons request was made. No MP4 was rendered. Validation passed. The second sentence stays on the teacher because the fixture only supplied one window.

```json
{
  "session_id": "synthetic-mode-a",
  "mode": "auto_edit",
  "status": "plan_ready",
  "rendered": false,
  "validation": { "ok": true, "errors": [] },
  "picture_decisions": [
    { "picture_role": "show_teacher", "start": 0.0, "end": 0.4 },
    {
      "picture_role": "show_broll",
      "start": 0.4,
      "end": 4.2,
      "filename": "Parliament exterior",
      "origin": "commons",
      "source_in": 0.0,
      "source_out": 3.8,
      "score": 0.61,
      "explanation": "Returned by the injected selector for this fixture.",
      "license": "CC BY 4.0",
      "page_url": "https://commons.wikimedia.org/wiki/File:Synthetic.webm"
    },
    { "picture_role": "show_teacher", "start": 4.2, "end": 8.0 }
  ]
}
```

The stored engine decision for the cutaway uses `action: replace_with_broll`, not `show_broll`.

## Sample Mode B plan

Synthetic. The only clip is the described parliament file. The hospital sentence has no matching description, so the teacher stays. Evidence is the description. Frames were not analysed. No external URL is present. Validation passed. No MP4 was rendered.

Description analysis of “Indian Parliament building exterior with people entering the premises.” produced subject `indian`, action `entering`, and the content words in that sentence. Nothing was added from outside the sentence.

```json
{
  "session_id": "synthetic-mode-b",
  "mode": "my_broll",
  "status": "plan_ready",
  "rendered": false,
  "validation": { "ok": true, "errors": [] },
  "picture_decisions": [
    { "picture_role": "show_teacher", "start": 0.0, "end": 0.4 },
    {
      "picture_role": "show_broll",
      "start": 0.4,
      "end": 4.2,
      "filename": "parliament.mp4",
      "description": "Indian Parliament building exterior with people entering the premises.",
      "speech_text": "The Indian Parliament building is where laws are discussed.",
      "source_in": 0.0,
      "source_out": 3.8,
      "score": 0.5,
      "explanation": "The description shares words with this speech: Indian Parliament building exterior with people entering the premises. Visual frames were not analysed.",
      "evidence": "description",
      "origin": "user"
    },
    { "picture_role": "show_teacher", "start": 4.2, "end": 8.0 }
  ]
}
```

## Validation

Before `plan_ready`, the job checks:

1. `validate_plan` accepts the engine plan, including schema and exclusive picture overlaps.
2. Every Mode B asset id belongs to the session.
3. Source in/out lies inside the clip duration when that duration is known.
4. Decision times lie inside the lesson duration.
5. Every cutaway has an explanation.
6. An asset id from another session is rejected.
7. Mode B rejects `origin: commons`, a page URL, and an `http` asset path.
8. Negative or reversed timestamps are rejected.
9. Overlapping `show_teacher` / `replace_with_broll` decisions are rejected.
10. A failed check leaves the session at `failed` and does not publish the plan.

Both synthetic plans above returned `ok: true` with an empty error list. The failure cases are covered by `api/tests/test_phase2.py`.

## Tests

| Suite | Result |
| --- | --- |
| Studio API, `api/.venv` pytest | 30 passed |
| Engine, `python -m pytest tests --ignore=tests/test_phase12.py` | 172 passed |
| `tests/test_phase12.py` | Not run. It loads CLIP weights. |
| Frontend `npm run test` | 9 passed |
| `npm run build` (`tsc --noEmit` and Vite production build) | Succeeded |

API coverage includes ownership, duplicate starts, interrupted-job recovery, transcription failure and retry, hash reuse and hash change, unchanged Roman Hindi, the Mode A adapter, the Mode B catalogue and description match, abstention, bad source and timeline times, cross-session assets, Mode B external footage, schema validation, and status/plan retrieval. Providers and `select_clips` are fakes in these tests. No ElevenLabs credit was used. No Commons search was made.

The site was opened at http://127.0.0.1:5173. The home page, AI Auto-Edit, and My B-Roll pages loaded with “API connected”. Prepare stays disabled until the required files are present. “Add another B-roll” created a second clip row. There is no render button. Start AI Processing was not clicked on the live server, because that click would call ElevenLabs and, in Mode A, Wikimedia Commons.

## Files

Created:

- `PHASE2_ARCHITECTURE_AUDIT.md`
- `PHASE2_REPORT.md`
- `api/app/processing/__init__.py`
- `api/app/processing/transcribe.py`
- `api/app/processing/user_broll.py`
- `api/app/processing/plan_adapt.py`
- `api/app/processing/jobs.py`
- `api/tests/test_phase2.py`
- `web/src/components/ProcessReview.tsx`
- `web/src/test/processReview.test.tsx`

Modified:

- `api/requirements.txt` (`jsonschema==4.23.0`)
- `api/app/factory.py`
- `api/app/schemas.py`
- `api/app/storage.py`
- `web/src/types.ts`
- `web/src/lib/api.ts`
- `web/src/pages/HomePage.tsx`
- `web/src/pages/AutoEditPage.tsx`
- `web/src/pages/MyBrollPage.tsx`
- `web/src/test/studioUi.test.tsx`
- `STUDIO.md`

Not modified: phase runners, `src/`, existing engine tests, raw videos, logos, outro, historical outputs, and the rollback hold.

## Known limitations

- No final video is produced. The engine plan’s `assets/teacher.mp4` and `assets/narration.wav` paths satisfy the schema. Those files are not created.
- Mode B does not inspect frames. Matching is overlap of content words from the description and the spoken text. Two shared words are enough to clear the acceptance floor. A description that shares nothing is left on the teacher.
- A live Mode A job uses the existing selector’s fallback score of 0.35 when CLIP is not constructed. Licence checks inside `select_clips` still run. This phase did not execute a live search.
- A live transcription needs `ELEVENLABS_API_KEY` in the engine environment. If it is missing, the session fails and can be retried. The key is not sent to the browser.
- Jobs live in the API process. A restart does not continue a half-finished transcription; it marks the session failed.
- Thumbnails are not generated.
- Devanagari caption transliteration remains in the engine caption helper and is not applied to the stored transcript. This phase does not render captions.

## Restart both servers

Stop whatever is already listening on ports 8787 and 5173, then:

API, from `saarthi_video_engine\api`:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Website, from `saarthi_video_engine\web`:

```powershell
npm run dev
```

Open http://127.0.0.1:5173 . The API is http://127.0.0.1:8787 . Both were started after this phase. Processing still begins only when Start AI Processing is used.
