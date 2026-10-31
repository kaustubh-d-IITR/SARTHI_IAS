# Phase 4 architecture audit

Written on 31 October 2026, before the Phase 4 matcher, versioning, and title code. Two small pieces were written first to answer audit questions: the frame sampler `api/app/services/visual/sampling.py` and the model worker `api/app/services/visual/worker.py`. The calibration they made possible is recorded below. No engine module, render code, or historical file was changed for this audit.

## 1. How Mode A selects footage

Phase 2 calls `src/assets/footage.py` `select_clips` once per processing run, inside `api/app/processing/jobs.py` `_default_select`. That function runs subject rules over the transcript, searches Wikimedia Commons, applies the licence and source checks in `assets/video_commons.py`, downloads candidates, and keeps clips that score at least `MIN_SCORE` (0.22).

The Studio passes `ranker=None`. In that mode `select_clips` gives every downloaded candidate a fixed score of 0.35 instead of a CLIP score. The historical phase runners pass a `ClipRanker`. CLIP weights are now known to be cached locally (section 3), but the API's virtual environment has no torch, so a ranker cannot be built inside the API process.

This is a known gap, not a defect that breaks Mode A. Mode A results still pass the licence checks, and Phase 3 rendered one successfully. Phase 4 does not change Mode A selection. Changing it would mean running `select_clips` in the engine interpreter, which would change how Commons is searched, and the brief only allows Mode A changes for a demonstrated issue.

## 2. How Mode B matches today

`api/app/processing/user_broll.py` `match_broll` splits each description and each transcript segment into content words of four or more letters, minus a stop list. The score is the share of description words that appear in the segment, raised to 0.5 when two or more words are shared. A score of 0.5 or more is accepted. Each clip is used once, segments do not overlap, the cutaway lasts `min(clip length, segment length)` and must be at least 2 s, and the source starts at 0. Frames are never looked at, `visual_analysis` is always `"unavailable"`, and evidence is always `"description"`.

This is keyword overlap. Two shared words such as “duty” and “covid” are enough, whatever the clip actually shows.

## 3. Is CLIP available locally?

Yes.

- `C:\Users\shubh\.cache\huggingface\hub\models--openai--clip-vit-base-patch32` holds 148 MB of weights.
- The engine interpreter, `C:\Users\shubh\AppData\Local\Programs\Python\Python313\python.exe`, has torch 2.11, transformers 5.17, Pillow, and NumPy.
- The engine wrapper is `src/assets/visual_rank.py` `ClipRanker`. It scores one image against one prompt.
- The Studio API virtual environment has NumPy and Pillow but no torch or transformers.

The same cache also holds `Snowflake/snowflake-arctic-embed-xs` (87 MB), a small text-embedding model, and `sentence-transformers` 6.0 is installed in the engine interpreter. No engine code uses it today. It is a better fit than CLIP's text encoder for comparing a description with a sentence of speech.

The machine has a Ryzen 7 6800H, 15.6 GB of RAM, and an RTX 3050 with 4 GB. The engine convention is CPU only (`CUDA_VISIBLE_DEVICES=""`).

Measured offline, with no download:

- Importing torch and loading CLIP took about 55 s cold.
- Loading Snowflake took about 1.5 s more.
- Peak resident memory was about 1 GB.
- A later run that encoded 7 frames and 120 texts took 6.5 s for CLIP and 1.5 s for Snowflake.

## 4. Is Gemini frame analysis implemented?

Partly. `src/broll_intel/gemini_api.py` `generate_structured(prompt, model, images=...)` sends up to three JPEG frames with a Pydantic response schema. It uses `gemini-3.8-flash` with fallback models, retries on 5xx errors, and redacts the key from errors.

`src/broll_intel/pipeline.py` and `director.py` use it for Phase 19 and 20 verification. They are experimental: they are tied to Commons candidates and to Phase 18–20 editorial rows, not to arbitrary uploaded clips. A key is configured; this audit checked only that it exists. The `google-genai` package is in the engine interpreter, not in the API virtual environment.

Gemini can be reused for frame descriptions through `generate_structured`. It costs money per call and needs network access, so it should be opt-in and never run in tests.

## 5. Frame-sampling and image utilities

`src/assets/visual_rank.py` provides:

- `sample_frames`: evenly spaced ffmpeg grabs.
- `black_fraction` and `framing_decision`: detect a subject isolated on black.
- `average_hash`: near-duplicate checks.

`sample_frames` does not reject black, blank, or flash frames, and it does not drop near-duplicates. `ClipRanker.score` loads CLIP inside the calling process.

The new `sampling.py` adds what the Studio needs:

- An adaptive frame count: 2 frames under 1 s, 4 up to 6 s, 6 up to 20 s, then one more per 10 s, capped at 12.
- Frames placed across the clip, never only the first frame.
- Black, flash, and flat frames rejected, with one retry a little later.
- A frame dropped when it is a near-duplicate of the one kept before it.
- Frames saved at 336 px wide, and a 320 px thumbnail taken from the most detailed kept frame.
- Every image closed after it is measured.

## 6. How editing plans represent footage

`build_phase2_plan` produces an engine plan that matches `schemas/editing_plan.schema.json`. A cutaway is a decision with `action: replace_with_broll`, an `asset_id`, an `asset_path`, an optional licence, and `start`/`end` on the lesson timeline. Decisions use `additionalProperties: false`, so source in/out points and evidence cannot be stored on the decision.

The builder:

- drops windows under 2 s;
- skips a window that overlaps an earlier one;
- trims the last windows to the 58% budget (`BROLL_CAP`).

The Studio keeps source in/out, scores, and explanations in `studio.placements`, inside `work/<session>/editing_plan.json`. That file is overwritten on every processing run, so there are no plan versions.

Phase 3 rendering reads that file. It requires a matching approved placement for every cutaway, and it plays the clip from `placement.source_in` for the decision's length.

## 7. How titles are stored and rendered

There is no title field in the Studio. `build_phase2_plan` writes one `title` decision. Its text comes from `_title(_title_source(...))`: the longest caption line, cut to its first clause and first five words, then uppercased. That rule produced “BLINDLY” for VIDEO_2 in Phase 3.

The composer renders the title decision as `compositions/title-card.html`. It uses `render.frame.fit_title`, and `_title_html` turns `[[word]]` into the yellow accent. Phase 19 hand-set titles such as `From [[NEET]] to [[UPSC]]` by replacing that decision's text in `run_phase5._render_one`, through `TITLE_OVERRIDES`. An empty title string is supported: `fit_title("")` returns empty text and the title line stays blank.

The outro is appended after the lesson, and the title does not appear in it. The smallest safe extension is to write the user's exact title into that existing decision's text, as Phase 19 did, and leave it empty when no title is confirmed. That needs no new screen and no styling change.

## 8. Calibration on real clips

These are real assets from earlier phases, read without modification:

- the Phase 19 PPE doffing clip;
- the Phase 19 fountain-pen clip;
- 47 speech windows from the VIDEO_1 and VIDEO_2 transcripts, single segments and adjacent pairs.

Raw CLIP and Snowflake scores sit in narrow bands (Snowflake: 0.80 for an unrelated sentence, 0.85 for a related one), so scores are read relative to the median over that lesson's windows.

| Clip, window | CLIP frames vs speech (above median) | Snowflake description vs speech (above median) |
| --- | --- | --- |
| PPE, “PPE kit and a N95 mask…” + next segment | +0.100 | +0.033 |
| PPE, “a COVID duty, I am coming back…” + next segment | +0.070 | +0.105 |
| PPE, “a COVID duty…” | +0.040 | +0.138 |
| Pen, best window in either lesson | +0.041 | +0.004 |

Agreement between the frames and the description itself, measured as CLIP frames vs description minus CLIP frames vs generic captions:

- PPE frames with the PPE description: +0.053.
- Pen frames with the pen description: +0.063.
- PPE frames with the description “Indian Parliament building exterior…”: −0.092.

The calibration shows:

- The PPE clip is a strong match for the PPE and COVID-duty speech.
- The pen clip has no strong match in either lesson.
- A false description of the frames comes out negative.

## 9. Gaps Phase 4 must close

- Mode B has no frame analysis, thumbnails, or cache.
- Mode B matching is keyword overlap.
- There is no record of why a clip was not used.
- There is no review or editing of placements, and no re-validation after a manual change.
- There is no title field, and the automatic title can be an arbitrary fragment.
- There are no plan versions. A render records a render id but not the plan it used.
- The model packages exist only in the engine interpreter.

## 10. Design decisions

- Models run in a short-lived worker process under the engine interpreter, configurable with `STUDIO_ENGINE_PYTHON`. The worker loads each model at most once per call, runs offline, and exits so its memory is released. The API never imports torch.
- `VisualAnalysisProvider` has three implementations:
  - `CLIPVisualAnalysisProvider` is the default when the worker is available.
  - `GeminiVisualAnalysisProvider` is opt-in through `STUDIO_VISUAL_PROVIDER=gemini` and costs money.
  - `DescriptionOnlyProvider` never claims analysis.
- Results are cached under `data/studio/visual/<sha256>/`. A changed file has a new hash and is analysed again. Failures are stored as diagnostics and are not treated as cache hits, so a later run retries them.
- The Mode B matcher combines three relative signals with fixed thresholds taken from section 8:
  - CLIP frames vs speech;
  - Snowflake description vs speech;
  - CLIP frames vs description, which catches a contradicting description.
  When no frames were analysed, a stricter description-only rule applies and the evidence is labelled description-only. When no model is available, the Phase 2 keyword rule applies and is labelled as such.
- Plans are stored as immutable versions under `work/<session>/plans/`. Each render records the version it used.
- The title is a session field written into the existing title decision. A transcript-based suggestion is offered only as a suggestion.
- Mode A selection and every render step stay as they are.
