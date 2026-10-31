# Phase 3 render architecture audit

Audit date: 30 October 2026. The first five sections were written before the render adapter. The last section records what implementation and the two real renders showed.

## Studio state before Phase 3

Phase 2 stores uploads under `data/studio/uploads/<upload-id>/` and the session under `data/studio/sessions/<session-id>.json`. The transcript, `editing_plan.json`, `catalogue.json`, and, for Mode A, `footage_index.json` sit under `data/studio/work/<session-id>/`. `editing_plan.json` holds `engine_plan`, which is validated against `schemas/editing_plan.schema.json`, and `studio`, the public preview with placements, source in/out, and explanations. A session with status `plan_ready` is the only one that may be rendered. Ownership is the `X-Studio-Client` header.

The engine plan uses `show_teacher` and `replace_with_broll`. The browser shows `replace_with_broll` as `show_broll`. The stored action is not renamed.

There is no `src/compose/` package and no `src/phase2/` package. Composition is `src/hyperframes_adapter/compose.py`. The plan builder is `src/editing_plan/phase2.py`.

## Existing render chain

`run_phase5._render_one` is the production example. Phases 8 and 16 through 20 reuse it after patching its output folder.

1. Probe the raw lesson. Cut `assets/teacher.mp4` with no audio and `assets/narration.wav` from the same source interval (`run_phase4._cut_teacher`, `_cut_audio`). Crop stable letterbox bars (`run_phase5._remove_letterbox`).
2. Re-encode each B-roll file with `-an`, seeking to `clip_in` when it is above 0 (`run_phase4._place_asset`).
3. Trim the logo's transparent border and cap it at 192 px (`run_phase5._trim_logo`).
4. Trim the Yuri Megis “Inspire Action” instrumental to the lesson length (`run_phase4._trim_music`). The plan gains are 0.12 under speech and 0.28 in the open. `configs/render.defaults.json` keeps 0.14 / 0.35 only as the composer's fallback.
5. Build the plan with `build_phase2_plan`, set `render.object_fit` (`cover` for a portrait source, `contain` for landscape) and `render.corner_logo`, then `clip_plan_to_duration` to the narration length.
6. `write_composition` writes `index.html`. Every `<video>` is `muted`. Narration is one `<audio id="narration">` from 0 for the lesson. Music is a second audio clip with ducking automation from `duck.volume_lane`, built from transcript speech intervals. Captions are separate elements whose text and times come from the plan's caption decisions; the composer uppercases them and writes no new text. The corner logo is an `<img>` positioned by `render.frame.reference_frame`. With a corner logo, B-roll fills the card with `object-fit: cover`, which is the full-screen cutaway. `_credit` returns an empty string, so licence text stays in the plan and is not drawn.
7. `run_phase4._hyperframes` runs `hyperframes@0.8.186 lint`, then `render --quality draft --workers 1`. Its output goes to `lint.txt` and `render.txt` in the composition folder.
8. `render.outro.append_outro` scales and pads the supplied outro onto the lesson canvas and joins it after the lesson. It reports the lesson, outro, expected, and final durations and its audio policy.
9. `run_phase5._verify` extracts the lesson-length part of the finished file at 16 kHz mono and compares it with the narration using `validation.audio_compare.compare_wavs` (Pearson correlation after a lag search). It records the result, takes snapshots, and writes `validation.json`. It does not stop on a low score.

Errors surface as `SystemExit` from `run_phase4._run` and `_hyperframes`, or as `RuntimeError` from `append_outro`.

`run_phase5._render_one` cannot be called from the Studio. It selects footage again, reads fixed files in `DR_SHIVIN_RAW`, deletes and recreates its composition folder, and writes into `outputs/`.

Caption wording is chosen inside `build_phase2_plan` by `transcription.captions.prepare_segment`. Latin Hinglish stays Latin. Devanagari is transliterated for the caption line. An English translation is never copied into a caption.

## Compatibility gaps between a Studio plan and the renderer

| Renderer expects | Validated Studio plan has |
| --- | --- |
| `assets/teacher.mp4` and `assets/narration.wav` beside `index.html` | Placeholder paths. No files. |
| Silent B-roll files already cut at the approved in-point; `data-media-start` for B-roll is 0 | `studio-assets/<id>/media`, which is not a file. Source in/out is on the studio placement because the schema forbids extra decision fields. |
| `assets/logo.png`, already trimmed | `corner_logo` set, no file |
| A music object with a file and gains | The silent music object `build_phase2_plan` writes when no music is passed |
| Source width and height measured from the cut lesson, and `object_fit` chosen from them | 720×1280 and `cover` for every lesson |
| A downloaded Mode A file | `select_clips` returns an absolute `local_path`, which the public plan drops |

Two more constraints apply. `_hyperframes` and `append_outro` overwrite their destination, so every attempt needs a fresh folder. A background thread that catches only `Exception` would let `SystemExit` escape and leave the session stuck in `rendering`.

## Adapter design

New code lives in `api/app/services/rendering/`.

- `bridge.py`: one method per existing engine function. It turns `SystemExit` and other failures into `EngineFailure`, and keeps the detail for diagnostics. It locates the logo and outro with `run_phase5._find_logo` / `_find_outro`. It locates music through the phase 5 candidates record whose page URL is `run_phase5.MUSIC_PAGE`. It does not search, download, or compose anything itself.
- `adapter.py`: loads the approved plan, maps each cutaway to a file the session owns, and copies the engine plan. The copy only gains file paths, the measured source size, the phase 5 `object_fit` rule, the music object, and the logo path. It then runs `validate_plan` again and checks that picture and caption decisions are unchanged except for a trim at the narration end. It calls the composer and HyperFrames, verifies the result, appends the outro, and verifies again.
- `jobs.py`: the render lifecycle, duplicate guard, history, last completed render, and restart recovery.

Mode A resolves each cutaway through the session's private `footage_index.json`, which Phase 2 writes when the selector returns files inside the session work folder. Mode B resolves each cutaway to an upload in the same session. It rejects a placement with a page URL or `origin: commons`, and it never substitutes another clip.

## Files

New code goes under `api/app/services/rendering/`, the render routes in `api/app/factory.py`, and the render panel in `web/src/`. Phase 2's Mode A step also writes `footage_index.json`. No phase runner, engine module, raw lesson, logo, outro, or historical MP4 is edited.

## Findings during implementation and the real renders

- Without `_trim_logo`, the logo would render larger than in earlier outputs. Without the `object_fit` rule, a landscape lesson such as VIDEO_1 (1280×720) would be laid out differently. The adapter applies both, as phase 5 does.
- Comparing the whole finished file with the narration would include the outro, which is padded silence because the outro file has no audio. The adapter compares only the lesson-length part, as `_verify` does.
- Phase 19 measured narration correlation of 0.988 (VIDEO_1) and 0.9645 (VIDEO_2). The adapter requires at least 0.90 in the lesson render and again in the finished file. The two Studio renders measured 0.988 and 0.9645.
- If the music file is missing, the render fails instead of going out silent.
- Phase 2 Mode B cutaways start at 0 in the source clip. `_place_asset` seeks only when `clip_in` is above 0, so a user clip plays from 0 for the length of its decision. That matches the approved source interval.
- The Studio plan's title comes from the engine's automatic title rule. Phase 19 used a hand-set override for VIDEO_2 (“From NEET to UPSC”). The Studio has no override field, so the same lesson rendered with the title “BLINDLY”.
- Low free memory caused two failures on this machine. One was an x264 `malloc` failure while cutting the teacher picture, in an earlier attempt. The other was HyperFrames stopping at frame 412 of 1249 with “drawElement worker encode failed”. The retry of the HyperFrames failure succeeded.
