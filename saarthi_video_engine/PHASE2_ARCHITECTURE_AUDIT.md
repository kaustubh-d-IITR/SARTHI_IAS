# Phase 2 architecture audit

Inspection only. No processing code had been added when this file was written. The tree is not a verified Phase 16 snapshot. Phase 20 is present and is not treated as the production default.

## Studio session lifecycle

Phase 1 stores uploads under `data/studio/uploads/<uuid>/` and sessions as `data/studio/sessions/<uuid>.json`.

`StudioStore.create_session` (`api/app/storage.py`) writes `status: prepared`. Mode `auto_edit` rejects a B-roll list. Mode `my_broll` requires at least one described clip. Ownership is the `X-Studio-Client` UUID. Public responses omit filesystem paths.

There is no processing state, transcript, or editing plan. `POST /api/sessions` only records files.

## Transcription

`src/transcription/elevenlabs.py` `transcribe_file` posts to `https://api.elevenlabs.io/v1/speech-to-text` with model `scribe_v2` and word timestamps. `transcript_from_response` keeps provider text on `text` and `original_text`, stores `logprob`, sets segment `confidence` to null, and sets `translated_to_english` to false. Its cache key is the SHA-256 of the file bytes plus the request settings.

`src/transcription/captions.py` `prepare_segment` transliterates Devanagari for caption display. That is caption text, not a rewrite of the stored transcript. The studio must persist the provider transcript before the plan builder runs.

The key is read by `src/config/env.py` `require_elevenlabs_key` from the engine `.env`. The studio must call that only when a job starts.

## B-roll selection

Mode A footage selection is `src/assets/footage.py` `select_clips`. It uses `analysis.subject_rules.records`, Commons `search_videos`, `accept_video_candidate`, download, and optional `ClipRanker`. `MIN_SCORE` is 0.22. With `ranker=None` the file uses a fallback score of 0.35 and does not load CLIP. Returned rows include `source_window`, `clip_in`, `license`, `page_url`, and `local_path`.

`src/broll_intel/` (director, Gemini, `recovery.fill_selected`) is a later path used by Phases 19 and 20. It is not the Phase 1 studio path and is not the default for this phase. Replacing `select_clips` with Gemini would drop licence checks.

Mode B has no user-library matcher. Commons search must not be called for that mode.

## Editing plan and renderer

`src/editing_plan/phase2.py` `build_phase2_plan` builds the schema in `schemas/editing_plan.schema.json`. Picture actions are `show_teacher` and `replace_with_broll`. `additionalProperties` is false, so a decision cannot gain a `show_broll` field. `BROLL_CAP` is 0.58. Windows shorter than 2 seconds are dropped. `src/validation/plan.py` `validate_plan` checks the schema and exclusive picture overlaps.

`replace_with_broll` is the full-screen cutaway. The studio preview will label that action `show_broll` without writing that word into the schema-validated decision.

`run_phase5.py` `_render_one` is the render entry. It cuts media, calls `build_phase2_plan`, writes HTML, and calls HyperFrames. This phase does not call it.

There is no `src/phase2/` package. The plan builder is `src/editing_plan/phase2.py`.

## Adapters

| Studio need | Engine piece | Adapter |
|---|---|---|
| Transcribe an upload | `transcribe_file` | Hash cache, injectable HTTP opener, no call at startup |
| Mode A footage | `select_clips` | Called only for `auto_edit`, ranker omitted so CLIP weights are not downloaded |
| Mode B footage | none | New description matcher. No Commons import |
| Plan | `build_phase2_plan` + `validate_plan` | Relative asset ids, sidecar for source in/out and explanations |
| Render | `run_phase5._render_one` | Not called |

## Incompatibilities

- Schema action is `replace_with_broll`, not `show_broll`.
- Decision objects cannot store source in/out or a user description. Those live beside the plan.
- `select_clips` returns absolute `local_path`. The public plan must not.
- User clips have no Commons licence. The adapter must not invent `page_url` or a CC licence.
- `build_phase2_plan` reads the local reference dataset for style. That is a read, not a render.
- A window under 2 seconds will not become a cutaway. That is existing behavior.
- The studio API venv does not yet install `jsonschema`, which `validate_plan` imports.

## Files to change

New code stays under `api/app/processing/` and `web/src/`. `api/app/factory.py`, `api/app/schemas.py`, and `api/app/storage.py` gain status and the new routes. `api/requirements.txt` gains `jsonschema`. The React prepare screen gains processing, transcript, and plan views.

No phase runner, engine module, raw video, logo, outro, or historical output is scheduled for edits.

## Implementation note

The adapters described above were added under `api/app/processing/` after this audit. `jsonschema==4.23.0` is now installed in the API virtual environment. The stored engine plan still uses `replace_with_broll`. The public envelope maps that action to `picture_role: show_broll`. Mode B does not run visual ranking; `visual_analysis` is `unavailable` and evidence is `description`.
