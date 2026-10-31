# Phase 4 report

The My B-Roll workflow now looks at the uploaded clips. Frames are sampled, analysed with local CLIP, cached by file hash, and used with the clip's description and the meaning of the speech. A clip is placed only when the frames, the description, and the speech support each other. Otherwise the teacher stays on screen and the reason is recorded.

Users can review and edit every placement and the title. Each change is rebuilt by the engine plan builder, validated again, and saved as a new immutable plan version. A render records the exact version and title it used.

Mode A footage selection, the renderer, captions, music, logo, outro, and the 58% cap are unchanged.

The audit written before implementation is `PHASE4_ARCHITECTURE_AUDIT.md`.

## 1. Audit summary

- **Mode A** runs the existing `select_clips` with `ranker=None`, so every candidate gets a fixed 0.35 score instead of a CLIP score. That is a known gap, not a defect, so it was left alone (section 7).
- **Mode B** in Phase 2 was keyword overlap between the description and a transcript segment, and no frame was ever looked at.
- **CLIP ViT-B/32** (148 MB) and **Snowflake arctic-embed-xs** (87 MB) are cached locally. torch, transformers, and sentence-transformers are in the engine interpreter, not in the API virtual environment. Both models load offline. A cold start takes about 35–55 s and peaks at about 1 GB.
- **Gemini** frame calls exist in `broll_intel/gemini_api.generate_structured` (up to three JPEGs per call). A key is configured.
- **Existing frame utilities** (`assets/visual_rank.py`) sample evenly but do not reject black, flat, or duplicate frames.
- **Plans** were written over `editing_plan.json` on every run, with no versions.
- **The title** came from `phase2._title(_title_source(...))`, which keeps the first five words of the longest caption line. That is how VIDEO_2 got “BLINDLY”. The composer renders one title decision through `fit_title` and `_title_html`, with `[[word]]` as the yellow accent. An empty title renders an empty line.

## 2. Matching logic

**Before (Phase 2):** content words of four or more letters, minus a stop list. The score was the share of description words found in a segment, raised to 0.5 when two words were shared. A score of 0.5 or more was accepted.

**Now** (`api/app/processing/matching.py`):

1. Speech windows are each transcript segment, plus each pair of adjacent segments up to 9 s long. A window must be at least 2 s.
2. For each clip and window, up to three signals are computed:
   - **Visual relevance:** CLIP cosine between the sampled frames and the speech (best frame).
   - **Description relevance:** Snowflake cosine between the user's description and the speech.
   - **Consistency:** CLIP frames against the description, minus CLIP frames against generic captions.
3. Raw similarities sit in narrow bands, so visual and description scores are measured against the median over the same lesson's windows. Lessons with fewer than five windows use fixed baselines instead. The offsets are scaled: 0.08 above the median gives a visual score of 1, and 0.12 gives a description score of 1.
4. A pair window is kept only if it scores higher than each of its single sentences, so a cutaway never spills over an unrelated sentence on a tie.
5. Selection is greedy:
   - strongest first;
   - one use per clip;
   - no overlaps;
   - the cutaway lasts `min(clip, window)` and at least 2 s;
   - the 58% cap is checked before the engine builder applies it again.
   The source in-point is centred on the best-matching frame.

Thresholds, from the real-clip calibration in the audit:

| Evidence available | Accepted when |
| --- | --- |
| Frames analysed (combined) | `0.55·visual + 0.45·description ≥ 0.60`, visual ≥ 0.45, description ≥ 0.20, and consistency ≥ 0 |
| Text model only (description) | description ≥ 0.85 and at least one shared word |
| No model (keywords) | at least two shared content words covering half the description |

## 3. Visual-analysis providers

`api/app/services/visual/analysis.py` defines `VisualAnalysisProvider` with three implementations:

- **`CLIPVisualAnalysisProvider`** is the default. It sends frames and texts to `worker.py`, which runs under the engine interpreter offline, loads each model once, and exits.
  - It stores frame embeddings and, per frame, the top three labels from a fixed list of 45, with probabilities.
  - It also stores a clip summary such as “a person taking off protective clothing (0.95)”.
  - The record says plainly that this is a label ranking, not a caption.
- **`GeminiVisualAnalysisProvider`** is opt-in through `STUDIO_VISUAL_PROVIDER=gemini` and costs money. It makes one structured call per uncached clip, over three frames: per-frame description, subjects, actions, and confidence, plus a summary.
- **`DescriptionOnlyProvider`** always reports `performed: false`. It uses the text model when it can, or keywords when no model is available.

`STUDIO_VISUAL_PROVIDER` takes `auto` (the default), `clip`, `gemini`, `description`, or `none`. If the engine interpreter is missing, the description-only provider is used.

Every analysis record holds:

- `performed`, provider, model, and analysis timestamp;
- the SHA-256 of the clip file and the sampler version;
- per-frame timestamp, frame file, description, subjects, actions, and confidence;
- the summary, and the reason when analysis was not performed.

The user's description is stored separately and never overwritten.

Records are stored in `data/studio/visual/<sha256>/<provider>.json`. A record is reused only if analysis was performed and the hash, model, and sampler version all match. A changed file has a new hash and is analysed again.

A failure is stored with its reason and is never reused, so the next run retries it. `POST /rematch` starts that retry from the Studio. A failed provider leaves frames and labels empty and makes no visual claim.

## 4. Frame sampling

`api/app/services/visual/sampling.py`:

- Frame count: 2 frames under 1 s, 4 up to 6 s, 6 up to 20 s, then one more per 10 s, capped at 12.
- Frames are spread across the clip with a small margin at each end, never only the first frame.
- A frame is rejected as black when mean luma is below 18. It is rejected as flash when luma is above 240 with low variation, and as flat when the standard deviation is below 6.
- A rejected frame is retried once a little later. A frame that is nearly identical to the one kept before it is dropped.
- Frames are saved at 336 px wide. A 320 px thumbnail is made from the most detailed kept frame.
- Images are closed after each measurement.

Sampling and analysis cover only the active session's clips, one session at a time, through a single worker call per processing run.

## 5. Evidence and confidence

Each placement records:

- the asset id and filename;
- the transcript segment ids and the matched speech;
- the timeline start and end, and the source in and out;
- the description, visual, and combined relevance;
- the evidence (`combined`, `description`, `description_lexical`, `manual`, or `existing_pipeline`) and `evidence_sources`;
- an explanation;
- a confidence;
- the approval and who approved it (`matcher` or `user`).

Confidence is the combined score with frames. Without frames it is reduced: 0.6 × the score with the text model, and 0.4 × with keywords only.

The explanations say “No frames were analysed” when that is true, and say which signals agreed when frames were used. Evaluations for every clip, including those not used, list the top three windows with their scores and reasons.

## 6. Abstention rules

The teacher stays on screen when:

- the frames do not show the speech's subject (visual score under 0.45);
- the description is unrelated to the speech (description score under 0.20);
- the combined score is under 0.60;
- the frames contradict the description (consistency below 0);
- a description-only match is under 0.85 or shares no word with the speech;
- a keyword match shares fewer than two words;
- the clip or window is shorter than 2 s;
- the clip's best window already has a stronger cutaway;
- the clip is already used;
- the 58% cap would be exceeded.

Every abstention is recorded with its reason and shown in the review panel.

## 7. Mode A

Mode A is unchanged. It runs no visual analysis, which a test checks: Mode A processing fails if it touches the Mode B provider. It still uses `select_clips` with its Commons licence and source checks, now writing an immutable plan version. Its placements can be approved, rejected, or retimed in review, but only clips its own selector approved for the session can be used. Mode A and Mode B never share a footage source.

Enabling `ClipRanker` in Mode A would mean running `select_clips` in the engine interpreter. That changes how Commons is searched, so it was left for a decision rather than done silently.

## 8. Title

- The title can be entered before processing, on either upload page, and changed in review.
- The text is kept exactly as typed, apart from trimming outer spaces, up to 120 characters. Devanagari, English, and Hinglish are all accepted, and nothing is translated.
- `[[word]]` uses the existing yellow accent, and a line break is allowed.
- The title is written into the existing title decision, as Phase 19's hand-set titles were. There is no new screen and no styling change.
- With no saved title, the title line stays empty instead of showing a transcript fragment.
- When there is no title, an AI suggestion is generated: the two or three most frequent recurring topic words, with their counts. It is labelled as a suggestion and used only if the user saves it. No suggestion is offered when fewer than two topic words recur.
- Render metadata and the manifest record the title text and status.

## 9. Plan versioning

`api/app/processing/versions.py` saves each plan as a write-once file, `work/<session>/plans/v###-<id>.json`. It holds:

- the version id and number;
- the creation time and parent version;
- the origin: `generated`, `rematch`, or `manual_edit`;
- the mode, source hashes (raw lesson, B-roll, Mode A footage), and matching configuration;
- the title, the validation result, the engine plan, and the studio envelope.

`editing_plan.json` remains a copy of the current version for older readers.

A version that fails validation is still saved, but it does not become current. An edit sent against a version that is no longer current is refused with 409.

A render loads the version that is current when it is queued. It records the `plan_version_id` in its state, manifest, and metadata, and the version list shows which renders came from each version. Earlier videos are never overwritten. Sessions created before versioning render from `editing_plan.json` and are marked `legacy`.

## 10. Frontend

- Both upload pages have a title field, with guidance on language and the `[[ ]]` accent.
- Processing shows a new stage, “Analysing your clips' frames”.
- The plan has a **Review cutaways and title** panel:
  - the thumbnail of the actual clip, the filename, and the user's description;
  - the generated visual summary, or why no analysis ran;
  - the matched speech, plus the description, visual, and combined scores, the evidence type, and the explanation;
  - approve or reject (reject keeps the teacher), or approve again;
  - swap the clip (Mode B);
  - edit timeline start and end, and source in; source out follows, so the source always lasts as long as its time on screen;
  - add a cutaway from an unused clip;
  - edit the title, with an AI suggestion clearly labelled and a “Use suggestion” button;
  - “Clips not used”, with reasons;
  - the B-roll total against the 58% limit;
  - “Save changes and validate”, which shows the server's validation errors.
- The plan summary has **Analyse clips and match again**.
- The render confirmation shows the title and plan version. A finished video shows its version and title. If the plan changed after a render, the panel says so and offers **Render the new version** while keeping the earlier video.
- Editing is locked while a render runs.

## 11. Performance safeguards

- Models run only in the worker subprocess. Each model loads once per processing run, and its memory is released when the process exits. The API never imports torch.
- Inference is CPU-only, with two threads.
- Clips are processed one after another in a single session. Frames are 336 px, and images are closed after measurement.
- Results are cached by hash. In the real check, a second run reused all three analyses and the worker only had to embed the texts again.
- Only the active session's clips are analysed. No historical asset is analysed.
- Tests never start the worker: `api/tests/conftest.py` sets `STUDIO_VISUAL_PROVIDER=none` and an invalid interpreter path.

## 12. Tests

| Suite | Result |
| --- | --- |
| Studio API (`api/.venv`, `pytest tests`) | 61 passed: 18 Phase 1, 12 Phase 2, 11 Phase 3, 20 Phase 4 |
| Engine (`pytest tests --ignore=tests/test_phase12.py`) | 172 passed. `test_phase12.py` was not run because it loads CLIP weights. |
| Frontend (`vitest`, single fork) | 20 passed |
| `npm run build` (`tsc --noEmit` and Vite) | Succeeded |

Phase 4 API tests use a fake model worker that returns small concept vectors. The real matcher, cache, versioning, edits, title, and render code are exercised; only HyperFrames is replaced, as in Phase 3. No Gemini, ElevenLabs, or Commons call is made.

They cover:

- **Sampling and caching:** frame planning, black and flat frame rejection, thumbnails, provider selection, hash caching and reuse, changed-file invalidation, CLIP failure with diagnostics and retry, and Gemini failure not cached.
- **Matching:** a strong match, a weak match that abstains, rejection of a single shared word, a contradicted description, the labelled description-only fallback, short clips, reuse, overlap, the cap, and the matcher cap being equal to the engine cap.
- **Titles:** exact Hindi and Hinglish text, length limits, no single-word suggestion, and the empty title.
- **Full Mode B pipeline:** frames, matching, thumbnails with ownership, and no vectors or paths in API output; also that Mode A never calls the visual provider.
- **Review edits:**
  - Rejected with 422: a source not matching its screen time, a source past the clip, a timeline past the lesson, a cutaway under 2 s, another session's clip, overlapping cutaways, and the 58% cap.
  - Accepted, each as a new version: manual approval, a source shift, a clip swap, rejecting a placement, and restoring teacher-only.
  - Also: a stale base gets 409, five versions are kept, and an old version can be read back.
- **Renders with titles:**
  - A title edit renders the exact Devanagari and Hinglish title into the title card, with the accent.
  - Each render records its plan version, and the first video's bytes are unchanged after a second render.
  - Rematch after a failure produces a new `rematch` version with frame evidence.

Frontend tests cover:

- thumbnail, both descriptions, evidence, and unused clips;
- the AI suggestion, used only on click;
- an exact Hindi title, and rejecting to restore the teacher;
- the source following the timeline, and server errors being shown;
- the lock during a render;
- the title and version in the render confirmation;
- an earlier video kept, with “Render the new version” offered.

All 204 protected files (logo, outro, both raw lessons, and every historical MP4) were hashed again after this work. None changed and none is missing.

### Real-model check

This is not a synthetic example. The real CLIP and Snowflake models ran through the production provider and worker on copies of two Phase 19 clips, against the real VIDEO_2 transcript. Files were written only to `%TEMP%`, and nothing was rendered.

| Clip and description | Result |
| --- | --- |
| PPE clip, “A health worker on COVID duty taking off a protective gown and mask.” | **Selected** at 19.86–24.26 s, source 0.0–4.4 s. Matched speech: “a COVID duty, I am coming back… I have been wearing a” + “PPE kit and a N95 mask…”. Visual 0.87, description 0.80, combined 0.84, consistency +0.049. CLIP top label: “a person taking off protective clothing” (0.95). |
| Pen clip, “A fountain pen writing on white paper.” | **Abstained.** “The frames do not show what this speech is about (visual 0.32 < 0.45).” CLIP top label: “a person writing with a pen” (0.93). |
| The same PPE frames, described as “Indian Parliament building exterior with people entering the premises.” | **Abstained.** Consistency −0.096: “The sampled frames do not resemble the clip's description.” This held even though its frames scored 1.00 against the PPE speech. |

The first run took 34–50 s, mostly loading the models. The cached second run reused all three analyses.

### Synthetic matching examples

These are **synthetic**. The vectors come from the test fixture's concept embedding, not from a model, and the five sentences are the test transcript.

1. **Strong match.**
   - Clip frames: “ppe”.
   - Description: “A health worker on COVID duty taking off a protective gown and mask.”
   - Selected for s2, “I was on COVID duty wearing a PPE gown and mask.”, at 5.2–8.0 s, source 3.2–6.0 s.
   - Description 1.00, visual 1.00, combined 1.00, consistency +0.785, evidence `combined`.
   - Before the pair-window rule (section 2), this case picked the pair s1+s2 at 2.8–8.0 s on a tie, which would have covered the unrelated sentence about exam papers.
2. **Weak match that abstains.**
   - Clip frames: “pen”.
   - Description: “A fountain pen writing on white paper.”
   - In a lesson that never mentions pens, the best window (s3+s4) scores description 0.04, visual 0.13, combined 0.09.
   - Reason: “The frames do not show what this speech is about (visual 0.13 < 0.45).” The teacher stays.
3. **Description contradicted by the frames.**
   - Clip frames: “pen”.
   - Description: “Indian Parliament building exterior…”.
   - The lesson includes “The Indian Parliament building is where the government meets.”
   - Consistency is −0.154, so the clip is refused: “The sampled frames do not resemble the clip's description.”
   - With frame analysis switched off, the same description would have been accepted on text alone. The frame check is what catches the mislabel.

## 13. Known limitations

- CLIP labels come from a fixed list of 45 phrases. They are a ranking, not a free description. A subject outside the list gets its nearest label or none. Gemini gives free descriptions but is opt-in, costs money, and its thresholds have not been calibrated on real clips.
- Thresholds were calibrated on two real clips and two lessons. More real sessions should be checked before the thresholds are treated as settled.
- CLIP and the text model are English-centred. Roman Hindi and Devanagari windows score close to the lesson median and rarely trigger a cutaway, so a clip about something said only in Hindi may be missed.
- A cold processing run waits about 35–55 s for torch to load.
- Mode A still ranks without CLIP (section 7).
- The timeline editor works in seconds and does not snap to sentence boundaries. The engine builder enforces the 2 s minimum, overlaps, and the cap; edits it would change are refused rather than adjusted.
- Long titles wrap to two lines through the existing `fit_title`.
- The suggestion is a keyword summary, not a written headline.
- Processing and renders still run inside the API process. A restart marks them failed for retry.
- During this phase, every terminal command, even a plain echo, hung for about 35 minutes after a test run. It recovered once memory was freed. Low memory on this machine remains the main operational risk.

## 14. Restart commands

Stop anything already on ports 8787 and 5173.

API, from `saarthi_video_engine\api`:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Website, from `saarthi_video_engine\web`:

```powershell
npm run dev
```

Open http://127.0.0.1:5173 . The API health check is http://127.0.0.1:8787/api/health . Both were restarted on the Phase 4 code.

## Files

Created:

- `PHASE4_ARCHITECTURE_AUDIT.md`
- `PHASE4_REPORT.md`
- `api/app/services/visual/__init__.py`
- `api/app/services/visual/sampling.py`
- `api/app/services/visual/worker.py`
- `api/app/services/visual/analysis.py`
- `api/app/processing/matching.py`
- `api/app/processing/titles.py`
- `api/app/processing/versions.py`
- `api/app/processing/editing.py`
- `api/tests/conftest.py`
- `api/tests/test_phase4.py`
- `web/src/components/PlanReview.tsx`
- `web/src/components/TitleField.tsx`
- `web/src/test/planReview.test.tsx`

Modified:

- `api/app/processing/jobs.py`: analysis stage, versions, title, rematch
- `api/app/processing/plan_adapt.py`: title, reconcile, placement fields, interval and cap checks
- `api/app/processing/user_broll.py`: keeps the raw user description
- `api/app/services/rendering/adapter.py` and `api/app/services/rendering/jobs.py`: render a pinned version, record version and title
- `api/app/factory.py`: new routes
- `api/app/schemas.py`
- `api/app/storage.py`: session title
- `api/.env.example`
- `api/tests/test_phase2.py`: evidence label
- `web/src/types.ts`
- `web/src/lib/api.ts`
- `web/src/components/ProcessReview.tsx`
- `web/src/components/RenderPanel.tsx`
- `web/src/pages/AutoEditPage.tsx`
- `web/src/pages/MyBrollPage.tsx`
- `STUDIO.md`

Not modified: the engine under `src/`, phase runners, engine tests, raw lessons, logo, outro, music, and historical outputs.
