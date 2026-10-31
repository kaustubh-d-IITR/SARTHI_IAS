# B-roll Preparation Bot — codebase investigation

Read-only inspection of the tree as it exists on 29 October 2026. No pipeline was run, no test suite was executed for this report, and no external API was called. Metrics below are copied from files already on disk.

The only file written by this investigation is this report.

## 1. Executive summary

The engine already splits “what to show” from “how to render,” but the split is not a stable public contract. Rendering is one path: a transcript, a candidate list, and a reference dataset go through `run_phase5._render_one` into HyperFrames, then FFmpeg appends the outro. B-roll selection is a stack of later phase scripts (`run_phase16.py` through `run_phase20.py` and `run_phase20_recovery.py`) that all end by calling that same renderer.

The latest code that both selects and renders is `run_phase20.py`. It can drop a previously accepted cutaway when its reference-token selector says the teacher should stay. `run_phase20_recovery.py` puts those acceptances back without searching again. The newest inspected MP4s that contain both verified cutaways are under `outputs/dr_shivin_phase20_recovery/rendered/`.

A new bot should not replace the renderer. The smallest real integration point is the candidate row that `run_phase5._chosen_media` already accepts: a licensed local file, a `page_url`, `match_terms` that occur in the caption text, a `source_window` on the raw transcript clock, and a `clip_in` inside the source file. `src/assets/manifest.py` is an older placeholder (`retrieval_implemented: False`). It is not what the renderer reads.

The reference dataset does contain structured teacher-versus-cutaway intervals. It does not contain the source files of those cutaways (`broll_segments.source` is often `unknown`). Token overlap against those intervals is implemented. It is not a learned editor.

## 2. Verified repository structure

Working directory during inspection: `C:\Users\shubh\OneDrive\Desktop\SAARTHI IAS\saarthi_video_engine`.

| Path | Verified |
| --- | --- |
| `C:\Users\shubh\OneDrive\Desktop\SAARTHI IAS` | Project root. Contains `REPORT.md`, `DR_SHIVIN_RAW`, `reference_dataset`, `hyperframes`, `LOGO`, `OUTRO`, `saarthi_video_engine`. |
| `saarthi_video_engine` | Engine root. Python package under `src/`. Phase runners at the engine root. |
| `DR_SHIVIN_RAW\VIDEO_1.mp4`, `VIDEO_2.mp4` | Present. |
| `LOGO\shivin sir logo.png` | Present. |
| `OUTRO\WhatsApp Video 2026-10-23 at 12.53.20 PM.mp4` | Present. |
| `reference_dataset\shorts_dataset.json` | Present. 50 records, 47 `completed`, 3 `partial`. |
| `hyperframes\` | Directory exists. The render command does not point at it. It calls `npx hyperframes@0.8.186`. Whether this checkout is that version was not compared. |
| Engine `.git` | Absent. Project-root `.git` is also absent. |

There is no engine Git branch to report. A Git repository was previously observed at the user home directory, which is outside this project. It was not inspected or changed.

`REPORT.md` at the project root is a prior audit. This report follows the code and the JSON files, not that document.

## 3. Current pipeline call graph

### Historical versus current

| Script | Role | Network | Renders | Status |
| --- | --- | --- | --- | --- |
| `build_phase2.py`, `build_proof.py`, `validate_phase2.py` | Early plan and proof | No, unless a caller adds it | Proof path can render | Historical. `src/assets/manifest.py` still says retrieval is not implemented. |
| `run_dr_shivin.py` | Early Dr. Shivin pass using a still image for PPE | Can be local-only | Yes, into `outputs/dr_shivin_evaluation` | Superseded. Hard-codes one Commons still. |
| `run_phase4.py` | Asset prep, HyperFrames lint/render, audio check, outro | Commons music search when that path runs | Yes | Still the render implementation. Later phases import it. |
| `run_phase5.py` | Lesson render from candidates plus transcript | No, if candidates already exist | Yes | Current render entry used by Phase 8, 16, 18, 19, 20, and recovery. |
| `run_phase6.py` | Evaluation / baseline wiring | Depends on callees | Yes when invoked | Historical relative to Phase 20. |
| `run_phase7.py` | ElevenLabs `scribe_v2` transcription | Yes, ElevenLabs | No | Source of the transcript the later phases load. Re-running it would call the API. |
| `run_phase8.py` | Loads Phase 7 transcripts, calls `phase5.render`, plan-based coverage | No, if candidates exist | Yes | `render_lessons()` is the function Phase 20 calls. Its `_measure` trusts the plan. |
| `run_phase9.py`–`run_phase13.py`, `run_phase15.py`, `run_phase16.py` | Retrieval, ranking, and lesson renders that built the Commons pool | Commons, and Pexels only if a key is set | Yes when those scripts render | Historical producers of `outputs/dr_shivin_phase16/asset_cache/candidates.json`. No `run_phase14.py`. |
| `run_phase17.py` | Semantic planner plus Gemini verification of an existing pool | Gemini when `verify=True` | Writes plans, not the lesson MP4 path of Phase 20 | Still the module home of `broll_intel.pipeline.build`. |
| `run_phase18.py` | Retries verifier failures and renders accepted spans | Gemini | Yes | Verification source for the pen and the gown. |
| `run_phase19.py` | Director wired onto the Phase 18 accepts | Gemini for two segments in the recorded run | Yes | Last render before Phase 20 in which both lessons had a verified cutaway. |
| `run_phase20.py` | Reference selector, fresh Commons search, CLIP, Gemini, then `render_lessons` | Commons, Gemini, and `models.list` unless `--no-live` | Yes, into `outputs/dr_shivin_phase20` | Latest combined selector and renderer. |
| `run_phase20_recovery.py` | Reapplies Phase 18 acceptances onto the Phase 20 plan | No | Yes, into `outputs/dr_shivin_phase20_recovery` | Latest inspected MP4s that contain both cutaways. |

There is no single config object for phase paths. `configs/render.defaults.json` holds canvas and ducking defaults. Phase numbers, model names, titles, and output directories are hard-coded in each runner.

### Render call chain that actually produces an MP4

```
run_phase20.main / run_phase20_recovery.main
  -> broll_intel.director.assets_for_render
  -> run_phase8.render_lessons
       -> writes work/transcript.json from outputs/dr_shivin_phase7
       -> run_phase5.render
            -> run_phase5._render_one
                 -> _chosen_media
                 -> editing_plan.phase2.build_phase2_plan
                 -> hyperframes_adapter.compose.write_composition
                 -> run_phase4._hyperframes   # node + npx hyperframes@0.8.186
                 -> render.outro.append_outro
                 -> run_phase4._verify        # PCM correlation
```

`run_phase4._hyperframes` (`run_phase4.py` around lines 472–489) lints, then runs `render --quality draft --workers 1`. It does not use Docker and does not call the sibling `hyperframes` directory.

Phase 5 overrides music gains inside `_render_one` to 0.12 under speech and 0.28 in the open. `configs/render.defaults.json` still says 0.14 and 0.35. The code path that renders lessons uses the Phase 5 numbers.

## 4. Transcript schema and sample

Loader for the current lessons: `run_phase8._load_transcript` reads `outputs/dr_shivin_phase7/<VIDEO>/transcript.json`. `run_phase7.py` is what created those files. It posts to `https://api.elevenlabs.io/v1/speech-to-text` with model `scribe_v2` unless `ELEVENLABS_STT_MODEL` is set.

A new bot can read these files and skip re-transcription when the source video is still `DR_SHIVIN_RAW\VIDEO_1.mp4` or `VIDEO_2.mp4`. Nothing in the JSON stores a source-file hash in the fields inspected at the top of the VIDEO_2 transcript. A later bot should compare the file hash itself before trusting the cache. That comparison is not implemented as a gate in `run_phase20.py`.

### Top-level fields (VIDEO_2, real file)

`language`, `language_source`, `language_probability`, `translated_to_english`, `method`, `model`, `endpoint`, `provenance`, `confidence_note`, `audio_events`, `segments`.

`translated_to_english` is false. `method` is `elevenlabs`. Segment `confidence` is null. The provider stores `logprob` per word.

### Segment

`id` (`a0`, `a1`, …), `text`, `original_text`, `start`, `end` in seconds, `confidence`, `timing_precision` (`word`), `words`.

### Word

`text`, `start`, `end`, `logprob`.

Real opening of VIDEO_2 segment `a0` (word list shortened):

```json
{
  "id": "a0",
  "text": "ना ही मैंने newspaper पढ़ा और ना ही मैंने NCERT पढ़ी and I would have just started preparing",
  "original_text": "ना ही मैंने newspaper पढ़ा और ना ही मैंने NCERT पढ़ी and I would have just started preparing",
  "start": 0.12,
  "end": 3.84,
  "confidence": null,
  "timing_precision": "word",
  "words": [
    {"text": "ना", "start": 0.12, "end": 0.24, "logprob": -0.0088}
  ]
}
```

`text` and `original_text` are the same provider string in this file: Devanagari mixed with English tokens. There is no separate Hinglish field and no speaker id.

On-screen captions are a later transform. `transcription.captions.prepare_segment` and `caption_pieces` romanize Devanagari. `transcription.romanize.transliterate` is what the reference selector and role grounding also call. The bot must keep `original_text` unchanged and let the existing caption functions produce the chips.

Segmentation is the ElevenLabs segment list. It is not re-split into clauses by the renderer. `broll_intel.planner._merge` can join adjacent Phase 17 decisions when the subject, action, and overlay match and the gap is at most 0.6 seconds. Phase 20’s `choose_cutaway` does not merge. It scores one transcript segment at a time.

Missing times: `plan_lesson` uses `start or 0` and `end or start`. An empty `segments` list yields no decisions. What `run_phase5._render_one` does with an empty segment list was not executed. Treat that as unverified.

`transcription.align.attach_english` can attach a separate English translation. The Phase 7 files used by the current renderer have `translated_to_english: false`. `run_dr_shivin.py` is the older path that expected a whisper.cpp translation.

## 5. Reference dataset findings

Root: `reference_dataset\`. README states this folder is evidence, not an editor. Video files of the Shorts are not stored here.

| Path | What it is |
| --- | --- |
| `shorts_dataset.json` | 50 records. This is the structured source. |
| `manifest.json` | ID, URL, extract status. |
| `raw\SHORT_XX.raw.json` | Metadata, caption track, scene times, audio meters. |
| `visual_notes\SHORT_XX.json` | Contact-sheet descriptions. |
| `frame_diffs\SHORT_XX.json` | Motion scores. |
| `reports\SHORT_XX.md` | Human reports. |
| `translations.json` | English translations of caption tracks. |
| `assets\contact_sheets\` | Sheet images. |

Dataset header, real:

- `dataset_version`: `0.1.0`
- `analysis_summary`: completed 47, partial 3, failed 0
- Method: captions are automatic json3. Audio was metered, not listened to. Visual timeline is contact-sheet inspection, not 30 fps.

`dataset_adapter.loader.is_trusted` is `quality.status == "completed"`. Partials are `SHORT_30`, `SHORT_32`, `SHORT_37`.

A timeline row has: `start`, `end`, `teacher_visibility`, `visual_content`, `broll_or_asset`, `transition_or_effect`, `captions_and_emphasis`, `audio_note`, `spoken_wording`, `evidence`, `confidence`.

A `broll_segments` row has: `id`, `start`, `end`, `visible`, `asset_type`, `spoken_phrase`, `relation_to_speech`, `source`, `placement`, `enter_exit`, `confidence`.

SHORT_01’s first cutaway is a photograph placed as `overlay`, `relation_to_speech` `illustrative`, `source` `unknown`. The dataset records that the teacher was or was not hidden. It does not record a reusable licensed master clip.

`broll_intel.reference_edits.examples_from_dataset` turns trusted timeline rows into examples. A cutaway example requires `teacher_visibility == hidden_by_broll` and a `broll_or_asset` id. A teacher example requires `visible_primary` and no asset id. `safe_to_learn_from` is true only when wording exists, the interval is positive, and `confidence == "observed"`. `retrieve_examples` is Jaccard overlap on romanized tokens of length 4 or more. `holdout_counts` holds out the last 8 trusted ids.

Recorded holdout from `outputs/dr_shivin_phase20/reference_holdout.json`: precision 1.0, recall 0.0217. That file’s own note says many reference cutaways do not share content words with other Shorts.

`dataset_adapter.retrieve.retrieve` is a different function. It scores category and layout text, not timeline phrases.

What can be measured from the dataset: interval bounds, teacher visibility, a written description, caption behavior, and a relation label when the analyst filled it in.

What is already encoded as rules: `analysis.subject_rules`, `broll_intel.taxonomy`, `broll_intel.queries`, and the token-overlap selector. Those rules were written in this repository. They are not a statistical model trained on the 50 Shorts.

What would be a new analysis step: clause-level boundaries inside a transcript segment, embeddings, and any claim that a reference cutaway’s unseen source footage should be copied.

Minimum the bot should read: trusted rows from `shorts_dataset.json` via `examples_from_dataset`, excluding the three partials. Use `spoken_phrase`, `replaced_teacher`, `visible`, `relation_to_speech`, `duration_seconds`, and `confidence`. Do not treat `source: unknown` as a downloadable asset.

## 6. Semantic Director and decision flow

Two decision stacks exist. Phase 20 uses the second.

### Stack A — Phase 17 planner

```
plan_lesson(transcript, lesson_id)
  -> taxonomy.classify_sentence
  -> queries.build_queries
  -> EditorialDecision
pipeline.build
  -> selection.attach_candidate
  -> gemini_api.generate_structured(..., VerificationResult)
  -> selection.apply_verification
  -> schema.accept_verification
```

`classify_sentence` is rule-based. `visual_type == "none"` schedules the teacher. Anything else is tentatively `broll` until verification. Decisions are one per transcript segment, then optionally merged.

`roles.interpret` forces pronouns (`maine`, `main`, `mein`, `mai`, `i`, `me`) to the speaker. The taxonomy object wins over a model subject when both exist. Negation from the rules is kept if the model also says the line is negated (`base["negation"] or model_negation`).

`director.analyze` calls `interpret` and then `build_queries`. It does not, by itself, call Gemini. `DirectorRead` is the schema a live director call is allowed to return. `run_phase19.py` is the runner that called the live model for specific segments. Phase 20’s `choose_cutaway` calls `analyze` with no model object, so that call is rules plus reference overlap, not a live Gemini read.

### Stack B — Phase 20 selector

```
choose_cutaway(text, start, end, examples)
  -> director.analyze          # rules
  -> retrieve_examples         # token overlap
  -> select_cutaway only if visual_type != none
     AND a cutaway example scores strictly above a teacher example
fill_selected                    # only if select_cutaway
  -> Commons search, download, CLIP, Gemini verify
retain_verified(current, phase18_row)
director.refine
director.assets_for_render
```

`select_cutaway` false skips search. Before the retain change, that also erased a Phase 18 acceptance. `retain_verified` now puts a still-valid acceptance back. A `rejected` prior stays out. `API_UNAVAILABLE` is not an acceptance. A missing file is not reused. A later rejection of the same page is not overwritten by the old acceptance.

`schema.accept_verification` rejects when `contradiction_detected` or subject or action does not match, even if confidence is 1. An `ACCEPT` below 0.55 confidence, or without `context_match`, becomes `REVIEW`.

Gemini failures: `failure_class` maps 503, 504, 429, unavailable, and deadline to `retry`. Quota is separate. 400, 401, 403, 404, and an API-key error are fatal. `generate_structured` validates with Pydantic and allows one repair attempt. `GeminiError` in `fill_selected` becomes `API_UNAVAILABLE`, which `refine` schedules as `review`, not `broll`.

The model is not asked to invent the search string from scratch in the Phase 20 path. `queries.build_queries` maps a subject to scene queries (`newspaper`, `book`, `YouTube`, `hospital`, `study process`, and so on). A bare noun is not a query. That is still a hand-written map, not a sentence parser. A line the taxonomy does not recognize gets no queries.

Phase 20 does not run the director on every sentence with a live model. It runs rules on every segment and the network path only on segments the selector marked as cutaways.

## 7. Retrieval-provider architecture

| Provider | Code | Called when | Verified how |
| --- | --- | --- | --- |
| Wikimedia Commons video | `assets.video_commons.search_videos`, `accept_video_candidate`, `download_video` | `assets.footage.select_clips` and `broll_intel.recovery.fill_selected` | Implemented. Phase 16–20 outputs contain Commons files. This investigation did not call the API. |
| Commons audio | `assets.music_commons` | Phase 4 music path | The lesson music row is the already downloaded “Inspire Action” file, reused by `run_phase5._music_record`. |
| Pexels | `assets.providers.search_pexels` | Only if `PEXELS_API_KEY` is set | Implemented. Phase 20 `retrieval_report.json` records `pexels_configured: false`. Not called in that run. |
| Openverse video | `assets.providers.openverse_status` | Status only | Code says the video route returns HTTP 404 and stills are not used. Not re-requested here. |
| Local catalogue | `assets.library.Catalogue` | `footage._fill` searches tags before downloading | Implemented. Phase 20 recovery uses the Phase 16 `candidates.json` catalogue, not a fresh download. |

`accept_video_candidate` refuses NC and ND licenses, a missing license URL, a missing download URL, a non-video MIME, files under 50 KB, and files over 40 MB. `video/mpeg` is in `VIDEO_MIMES` (the fountain-pen file). The description must contain the query terms of length 4 or more, with one soft exception inside `recovery._absorb` when the only failure is “description does not contain the speech query.” `assets.concepts.denies` blocks a clip that depicts a denied reading action.

`footage.select_clips` downloads at most 4 files per subject and ranks with CLIP when a ranker is passed. `fill_selected` downloads at most 2 and verifies at most 2. Both can be called without the renderer if the caller passes the dependencies. `fill_selected`’s defaults call the network only when the caller passes the live `search_videos` and `download_video`.

Cache: `download_video` writes a destination path. `footage._download` returns an existing file larger than 50 KB instead of downloading again. There is no byte-range resume. `library.Catalogue` is a sqlite catalogue used by the footage filler.

A candidate row that survives (real keys from `outputs/dr_shivin_phase16/asset_cache/candidates.json`) includes `page_url`, `download_url`, `license`, `license_url`, `artist`, `local_path`, `sha256`, `clip_in`, `play_limit`, `source_window`, `match_terms`, `visual_evidence`, `provider`, `subject`, `rejected`.

## 8. Ranking and verification flow

`assets.visual_rank.ClipRanker` loads `openai/clip-vit-base-patch32` on CPU when `score` is called without an injected scorer. `footage._score_candidate` samples three frames inside each candidate in-point (`sample_frames(..., start=, end=)`), rejects a near-black frame (`black_fraction >= 0.72`), and keeps the window with the highest cosine. `MIN_SCORE` is 0.22. The sampled frame paths are stored on the candidate as `visual_evidence`.

`clip_in` is the in-point inside the downloaded file. Values at or below 0.05 are raised to 0.05 because a zero in-point makes the placer transcode the whole file. `source_window` is the lesson speech interval, not the source-file interval.

Verification is `pipeline.VERIFY_PROMPT` plus `generate_structured` into `VerificationResult`, then `accept_verification`. Outcomes that can be scheduled as B-roll are only `retrieval_status == verified` after `refine`. `REJECT` clears the asset. `REVIEW` and `API_UNAVAILABLE` stay off the timeline. `assets_for_render` skips a page that is missing from the catalogue or has no license or local path.

Phase 20’s pen cutaway reused the Phase 18 Gemini `ACCEPT`. It was not a new image call. The recovery gown cutaway is the same Phase 18 `ACCEPT` (confidence 0.95), also not re-checked. Phase 20 did make a fresh Gemini `REJECT` of a book-fair stage for the book line, and one exam-paper attempt ended `API_UNAVAILABLE`. Those are in `outputs/dr_shivin_phase20/editorial_summary.md` and the editorial plans.

CLIP and Gemini can be invoked as `ClipRanker.score_frames` and `generate_structured` without rendering. Doing so downloads model weights or spends API quota. That was not done for this report.

## 9. Existing candidate, decision, and edit-plan schemas

These are the schemas the code uses. They are not a proposal.

### Editorial decision

`broll_intel.schema.EditorialDecision`. Fields include `lesson_id`, `transcript_segment_id`, `speech_start`, `speech_end`, `exact_transcript`, `primary_subject`, `action`, `visual_type`, `visual_brief`, `required_objects`, `required_actions`, `forbidden_visuals`, `negation`, `negation_kind`, `overlay_type`, `search_queries`, `retrieval_status`, `selected_asset_id`, `selected_source_in`, `selected_source_out`, `verification_evidence`, `schedule` (`broll`, `teacher`, or `review`).

`schedule == "broll"` is what `assets_for_render` reads. The renderer never sees `EditorialDecision` directly.

### Verification

`VerificationResult`: `decision` is `ACCEPT`, `REJECT`, or `REVIEW`, plus `subject_match`, `action_match`, `context_match`, `contradiction_detected`, `confidence`, `reason`, `visual_summary`, `evidence`.

### Catalogue candidate

Keys listed in section 7. `assets_for_render` copies the catalogue row and overwrites `clip_in` from `selected_source_in`.

### Edit plan consumed by HyperFrames

`editing_plan.phase2.build_phase2_plan` writes the plan. Top-level keys on the recovery VIDEO_2 plan: `contract_version`, `plan_id`, `decisions`, `render`, `audio`, `style`, `form`, `input_media`, `content_analysis`, `attribution`, `limitations`.

A `replace_with_broll` decision’s keys: `id`, `action`, `start`, `end`, `reason`, `reference_ids`, `reference_timestamps`, `confidence`, `source`, `asset_id`, `asset_path`, `asset_kind`, `transition`, `license`, `license_url`, `page_url`, `creator`.

`start` and `end` are on the lesson clock after `run_phase5` subtracts the transcript selection in-point from `source_window`.

`_visual_decisions` fills gaps with `show_teacher`. `_cap_windows` stops B-roll at `BROLL_CAP` (0.58 of the timeline). `_merge_windows` collapses overlaps. A candidate is dropped by `_chosen_media` when `rejected` is set, the page is not in the accepted URL list, `match_terms` are missing from the caption string, a term is on the board block list (`visual_observations.json`), the file is missing, or the same page was already used.

`src/assets/manifest.py` `build_manifest` is a different, older object. `retrieval_implemented` is false and the sample assets are synthetic. Do not target it.

### Smallest adapter

A future bot manifest should be translated into catalogue rows plus the page-url allow list that `_chosen_media` filters on:

- `video_id`
- `kind`: `video`
- `page_url` (also the dedupe key via `analysis.uniqueness.asset_identity`)
- `local_path` that exists
- `license` and `license_url`
- `match_terms`: at least one token that appears in the romanized full caption text and is not in that lesson’s blocked board terms
- `source_window`: `[speech_start, speech_end]` on the Phase 7 clock
- `clip_in` and `play_limit`
- `rejected`: false

`run_phase20._render` already writes those rows to `asset_cache/candidates.json` and the allow list to `accepted.json`, then calls `render_lessons`. An adapter can do that write and call `run_phase8.render_lessons` without changing `compose.py`.

## 10. Renderer integration point

`hyperframes_adapter.compose.write_composition` turns the Phase 2 plan into HTML. Captions come from `caption_pieces` for every decision interval, including `replace_with_broll`. `_credit` returns an empty string, so license text is not burned into the frame. The logo and dotted card are part of the composition, not the B-roll file.

`render.outro.append_outro` concatenates the outro after the lesson. The outro file has no audio. Silence is padded. Lesson title, captions, and B-roll decisions are not extended into the outro because their times are inside the lesson plan, which ends before the concat.

`validation.audio_compare.compare_wavs` correlates 16 kHz PCM of the lesson narration with the rendered file. The recorded method string is Pearson correlation after a lag search.

Canvas is 720×1280 at 30 fps in `configs/render.defaults.json`. Titles for these two lessons are hard-coded in the phase runners (`You Will Make / Mistakes`, `From NEET to UPSC`).

The renderer cannot be pointed at an arbitrary folder unless that folder’s files are copied into the composition `assets` directory by `_place_asset`, which only sees candidates that survived `_chosen_media`. A path in a side manifest is ignored until the adapter builds those rows.

Prerequisites for a new lecture, from the code rather than a dry run:

1. A Phase 7-shaped transcript, or a new `run_phase7` call.
2. The raw video reachable the way `run_phase5` resolves `DR_SHIVIN_RAW`.
3. Logo and outro files.
4. Candidate rows for any cutaway, plus the music row from `_music_record`.
5. `visual_observations.json` if board terms must be blocked. Phase 20 copies the Phase 16 file.
6. Node and `hyperframes@0.8.186`, and FFmpeg on the path `render.outro.tool` resolves.
7. The reference dataset, because `build_phase2_plan` and `choose_exemplar` load it even when B-roll is empty.

`run_phase5.VIDEOS` is the two Dr. Shivin files. A third lecture needs that table, or a caller that sets the same inputs `_render_one` takes. That generalization is not implemented.

## 11. Latest verified output metrics

Evidence is existing JSON. Nothing was re-measured for this report.

### `outputs/dr_shivin_phase20_recovery/rendered_coverage.json`

Pixel method: midpoint frame versus a teacher frame, outro excluded.

| Video | Planned inserts | Seconds that differed from the teacher | Lesson seconds | Fraction |
| --- | --- | --- | --- | --- |
| VIDEO_1 | 1 | 4.04 | 55.26 | 0.0731 |
| VIDEO_2 | 1 | 4.22 | 41.44 | 0.1018 |

`validation.json` in that directory: VIDEO_1 narration correlation 0.988, lag 0.0213 s, final duration 63.288 s against an expected 63.275 s. VIDEO_2 correlation 0.9645, lag 0.0213 s, final duration 49.488 s against 49.475 s. Outro path is the WhatsApp file named in section 2.

`render_report.json`: `kind` `verified_broll`, `accepted_segments` 2, renderer status 0.

Eligible-speech percentages are not in that coverage file. Earlier phase reports used about 17.541 s and 31.1 s as eligible speech. Those figures are prior-report inputs, not recomputed here.

### Phase 20 before recovery

`outputs/dr_shivin_phase20/rendered_coverage.json`: VIDEO_1 4.04 s, VIDEO_2 0 s. That VIDEO_2 zero is a rendered result, not only a plan. The plan has no `replace_with_broll` row.

### Phase 16 versus Phase 18

Phase 16 editing plans contain more cutaways (shelves plus pen; five VIDEO_2 inserts). Phase 18 editorial plans mark the shelves, both exam clips, and the Ebola training clip `rejected`, and the Rambam hospital clip `API_UNAVAILABLE`. Only the pen and the gown remain `verified`. Phase 19 rendered those two. Comparing coverage without that distinction treats rejected footage as if it were still approved.

The gown file visibly includes a “/love” burn-in. That is in the Phase 19 frame and in the recovery frames. It is a property of the accepted source, recorded in `outputs/dr_shivin_phase20_recovery/RECOVERY_REPORT.md`.

## 12. Tests and reliability gaps

Tests live in `saarthi_video_engine\tests\`. They were not executed for this investigation. `outputs/dr_shivin_phase20_recovery/RECOVERY_REPORT.md` records a later `pytest tests -q` result of 185 passed. That number is a previous run’s record, not a result of this read-only pass.

| Area | Test module | What it locks | Live services |
| --- | --- | --- | --- |
| Transcript and captions | `test_phase7.py` | ElevenLabs payload shape, romanization, missing key | Mocks the key. A careless run of `run_phase7.py` would call the API. The test file itself is not `run_phase7.py`. |
| Reference examples | `test_phase20.py` | Partials excluded, wording unchanged, relative lines not selected | Reads the local dataset. |
| Retain | `test_phase20_recovery.py` | Selector miss does not erase an acceptance; API failure is neither accept nor erase; rejected and missing files stay out | Local temp files only. |
| Verification gate | `test_phase17.py`, `test_phase18.py`, `test_phase19.py` | `accept_verification`, pronouns, refine | Gemini tests stub the client. One test sets a fake `GEMINI_API_KEY` and restores it. |
| Retrieval and license | `test_phase12.py`, `test_phase15.py`, `test_phase16.py` | Commons filters, Pexels absent, segment ranking | `test_phase12.py` can spawn a real CLIP process if the shelf fixture exists. It writes a temp frame directory. |
| Plan and render pieces | `test_phase2.py`, `test_phase4.py`, `test_phase5.py`, `test_phase8.py` | Plan schema, outro, composition | Some build files under test output directories. Not run here. |
| End to end | None that renders both lessons without a network or a GPU/CPU model | The MP4 checks are phase scripts, not pytest | Do not treat pytest as a render test. |

Offline fixtures the bot can use without a network: the Phase 7 transcripts, `shorts_dataset.json`, the Phase 16 candidate files that are still on disk (pen and gown), and `tests/test_phase20_recovery.py`’s pattern of injecting `search`, `download`, `rank`, and `verify`.

Smallest future integration test: build one candidate row from the existing pen file, run only the pure functions `retain_verified` and `assets_for_render`, and assert the row’s `clip_in`, `source_window`, and `page_url`. Do not call HyperFrames in that test.

## 13. Environment and dependencies

`pyproject.toml`: Python `>=3.11`, dependency `jsonschema>=4.20` only.

`requirements-broll.txt`: `google-genai==2.33.0`, `pydantic==2.14.1`. CLIP, PyTorch, and Transformers are imported by `visual_rank.py` but are not pinned in that file.

Node is resolved by `shutil.which("node")` or `C:\Program Files\nodejs\node.exe`. HyperFrames is `hyperframes@0.8.186` through `npx-cli.js`. FFmpeg and ffprobe are resolved by `render.outro.tool`.

Environment names read by code, values not inspected:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_LANGUAGE_CODE`
- `ELEVENLABS_STT_MODEL`
- `GEMINI_API_KEY`
- `PEXELS_API_KEY`
- `SAARTHI_LLM_URL`
- `SAARTHI_WHISPER_CLI`
- `SAARTHI_WHISPER_MODEL`
- `CUDA_VISIBLE_DEVICES` (forced empty inside `ClipRanker._load`)

`.env.example` lists the ElevenLabs and Gemini names and leaves them blank. `.env` exists and was not opened.

Gemini text model constants: `gemini-3.8-flash`, fallback `gemini-3.5-flash`. `video_generation_status` reports a video model only when `models.list` returns a name containing `veo` or `video-generation`. The Phase 20 generation report says none was returned. Generation is not an implemented render step.

The bot can use this environment. CLIP is heavy enough that the Phase 20 notes say not to run it while HyperFrames is encoding.

## 14. Confirmed facts versus unresolved questions

Confirmed from code or existing JSON:

- Phase 7 transcripts have word times and mixed Devanagari/English text.
- Trusted reference records expose teacher-versus-cutaway intervals. Three shorts are partial.
- The selector is token overlap plus rules, not a trained model.
- Commons video search, optional Pexels, CLIP ranking, and Gemini verification exist as functions.
- The renderer’s input is a candidate row plus the Phase 2 plan, not `manifest.py`.
- Phase 20 recovery MP4s contain 4.04 s and 4.22 s of B-roll that differed from the teacher frame.
- Rejected Phase 16 clips are still rejected in the Phase 18 editorial plans.

Unresolved without a later, separate test:

- Whether the sibling `hyperframes` checkout matches package 0.8.186.
- The exact failure of `run_phase5._render_one` on an empty transcript.
- A fresh Gemini re-check of the pen and gown frames. The rendered recovery uses stored acceptances.
- Pexels behavior with a key present. The key is unset in the Phase 20 report.
- A source-hash guard that would invalidate the Phase 7 transcript after the raw file changes.
- Home-directory Git status. Not part of this project, and not queried.

## 15. Proposed architecture

This section is a design. It is not an existing module.

```
Phase 7 transcript JSON
  -> bot.decide            # per segment: no_broll | wanted
       uses examples_from_dataset and the existing rules
  -> bot.search            # fill_selected-style, Commons first
  -> bot.rank              # ClipRanker on the span that would play
  -> bot.verify            # generate_structured + accept_verification
  -> bot_output/<lesson>/clips/ and manifest.json
  -> adapter.to_candidates # the catalogue row in section 9
  -> run_phase8.render_lessons
```

The bot does not own captions, the title, the logo, music, the teacher cut, narration, or the outro. Those stay in `run_phase5._render_one` and `compose.py`.

Reuse without a wrapper:

- `reference_edits.examples_from_dataset` and `retrieve_examples`
- `director.analyze` and `roles.interpret`
- `queries.build_queries`
- `video_commons.search_videos`, `accept_video_candidate`, `download_video`
- `footage._score_candidate` and `ClipRanker`
- `schema.accept_verification` and `selection.apply_verification`
- `retain_verified`
- `assets_for_render` if the bot emits editorial rows instead of raw candidates

Thin wrapper:

- A driver that loops segments, writes only under a new directory, and never assigns `run_phase20.OUT` to an old phase directory.
- The candidate adapter in section 9, including `match_terms` and blocked board terms.

New code:

- An explicit state enum, below.
- A manifest writer.
- A clause splitter, if segment-level ElevenLabs chunks are too coarse. That splitter does not exist.
- Any embedding index over reference phrases. Token overlap is what exists today.

The bot must be allowed to leave the teacher on screen. Coverage is not a success metric by itself.

### Proposed state contract

Not an existing schema.

| State | Meaning | Renderer |
| --- | --- | --- |
| `teacher` | No cutaway wanted, or the reference comparison prefers the teacher | Teacher |
| `ready` | File, license, source in/out, and `ACCEPT` are all present | B-roll |
| `unfilled` | A cutaway was wanted and no licensed span passed rank and verification | Teacher |
| `retrieval_failed` | Search or download raised, or the provider was unconfigured | Teacher |
| `verify_failed` | HTTP 429, 503, 504, timeout, or schema failure | Teacher, and the row stays resumable |
| `rejected` | The verifier said the frames do not match | Teacher |
| `review` | Ambiguous frames, or confidence below the existing gate | Teacher until a person accepts |

`ready` is the only state the adapter may emit as a candidate. `verify_failed` must not be stored as `rejected` or as `ready`.

Video generation stays out of this contract until a provider listed by `video_generation_status` can return a real MP4 that passes the same gate. The current generation report says no such model was listed.

## 16. Prioritized implementation plan

1. Add a bot package that only reads the Phase 7 transcript and writes a manifest of the states above, using `choose_cutaway` and `retain_verified`. No network. Compare it to the Phase 18 acceptances so a selector miss cannot delete the pen or the gown.
2. Add the candidate adapter and a unit test that `assets_for_render` returns those two existing files with the stored in-points.
3. Only then call Commons for segments whose state is still `unfilled`, with the existing download caps, and run CLIP and Gemini on the played span.
4. Render once, into a new output directory, through `render_lessons`. Do not point `OUT` at Phase 16–20 or the recovery folder.
5. Inspect frames and the existing audio-correlation function. Do not add a generation provider in that milestone.

## 17. Files the next implementation would touch

- A new package, for example `src/broll_bot/`, which does not exist today.
- A new runner, for example `run_broll_bot.py`.
- Tests under `tests/` that import the new package.
- Possibly a small adapter next to `broll_intel.director.assets_for_render` if the catalogue row is built there instead of in the bot.

Call, do not rewrite:

- `broll_intel.reference_edits`
- `broll_intel.director.analyze`, `refine`, `assets_for_render`
- `broll_intel.retain.retain_verified`
- `broll_intel.recovery.fill_selected`
- `broll_intel.schema.accept_verification`
- `assets.video_commons`
- `assets.footage._score_candidate`
- `assets.visual_rank`
- `run_phase8.render_lessons` and `run_phase5._render_one`

## 18. Files to leave untouched in the first implementation

- `src/hyperframes_adapter/compose.py`
- `src/render/outro.py`
- `src/editing_plan/phase2.py` (`BROLL_CAP`, caption assembly)
- `run_phase4.py` `_hyperframes`
- `configs/render.defaults.json`
- `reference_dataset\`
- `DR_SHIVIN_RAW\`
- `LOGO\` and `OUTRO\`
- `outputs/dr_shivin_phase7` through `outputs/dr_shivin_phase20_recovery`
- `.env` and `.env.example`
- `src/assets/manifest.py` until a later cleanup. The adapter should not pretend that placeholder is the contract.

## 19. Minimal integration-test plan

Offline, no pytest network:

1. Load `outputs/dr_shivin_phase7/VIDEO_2/transcript.json` segment `a6`. Assert `original_text` is unchanged after a decision.
2. Feed a selector result of `teacher` plus the Phase 18 gown row into `retain_verified`. Assert `schedule` becomes `broll`, `selected_source_in` stays 40.0, and speech bounds stay 22.94–27.16.
3. Feed a `rejected` shelves row. Assert it is not `broll`.
4. Feed `API_UNAVAILABLE` with no prior acceptance. Assert it is not `broll`.
5. Build one catalogue row from the existing gown path and run `assets_for_render`. Assert one row, the same page, and `clip_in` 40.0.
6. Do not start HyperFrames in this test.

A later manual test, separate from CI, can call `render_lessons` on a copy of those two rows and compare the midpoint frames with the recovery inspection images.

## 20. Assumptions that were not verified by execution

- Pytest was not run for this report. The 185-passed figure is only what `RECOVERY_REPORT.md` already records.
- Commons, Pexels, ElevenLabs, and Gemini were not contacted.
- `.env` was not read. Presence of keys is taken from `.env.example` and from the Phase 20 retrieval report’s `pexels_configured` flag.
- The sibling `hyperframes` directory was not diffed against package 0.8.186.
- Word-level caption alignment inside a cutaway was inferred from `caption_pieces` and from frames already saved by the recovery render, not from a new render.
- Eligible-speech denominators from older phase write-ups were not recomputed.
