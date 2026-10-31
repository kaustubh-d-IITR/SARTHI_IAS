# Saarthi video engine

Phase 1 connects the existing reference dataset to [HyperFrames](https://github.com/heygen-com/hyperframes) and renders one synthetic proof. HyperFrames stays an upstream checkout. This folder holds the Sarthi adapter, the editing-plan contract, and the proof composition.

The reference reports in `../reference_dataset` are not modified. `../links.txt` is not modified.

## What this phase does

- Loads `reference_dataset/shorts_dataset.json`.
- Treats `quality.status == "completed"` as the trusted baseline. Partial records still load. They cannot seed a style profile. In the current file those partials are `SHORT_30`, `SHORT_32`, and `SHORT_37`.
- Retrieves examples by category, layout family, template family, and token overlap on the category fields. There is no embedding index.
- Writes an editing plan (`contract_version` `1.0.0`) and checks it against `schemas/editing_plan.schema.json` plus timeline rules.
- Turns that plan into a HyperFrames composition. The layout family comes from the exemplar. Four writers exist: `bounded_card_with_title`, `full_bleed_board_or_lecture`, `split_teacher_and_graphic`, and `screen_recording_or_product_ui`.
- Renders that composition with the HyperFrames CLI.

## What this phase does not do

- It does not transcribe a teacher video, pick a topic, or retrieve B-roll.
- It does not identify music. The dataset metered audio and did not listen to it. The plan keeps `music.identity` null.
- It does not name a font file, a stock URL, or a frame-accurate effect. Numbers that were not measured are in `configs/render.defaults.json` and marked as defaults.
- It does not edit a real teacher recording. The proof media is generated with FFmpeg. A real-media run needs an authorized local file passed in as `input_media.video_path` and `audio.narration_path`.

## Layout

```
saarthi_video_engine/
  build_proof.py
  configs/render.defaults.json
  schemas/editing_plan.schema.json
  examples/sample_editing_plan.json
  src/dataset_adapter/
  src/editing_plan/
  src/validation/
  src/assets/
  src/hyperframes_adapter/
  compositions/proof_of_concept/
  tests/test_engine.py
  outputs/                  generated MP4s, gitignored
```

Put a future authorized teacher file outside the reference dataset, and point the plan at it. Rendered MP4s belong in `outputs/`. Do not commit them.

## Environment

| Tool | Role |
| --- | --- |
| Python 3.11+ and `jsonschema` | Dataset adapter, plan validation, composition writer |
| Node.js 22+ | `npx hyperframes` |
| FFmpeg and FFprobe | Synthetic media, and the HyperFrames render |
| Chrome | Used by HyperFrames for frame capture |

HyperFrames itself is the checkout at `../hyperframes` (remote `https://github.com/heygen-com/hyperframes.git`, branch `main`). Rendering uses the published CLI (`npx hyperframes`), not a local build of that monorepo. The monorepo's package manager is Bun. This phase does not install Bun or build the packages.

No API key is required for the local proof. HeyGen sign-in is unused.

`npx hyperframes doctor --json` checks the machine. `ok: false` can still mean optional pieces are missing (local Whisper, Kokoro, Docker). FFmpeg, FFprobe, Node, and Chrome need to be `ok` before a render.

## Dataset

```powershell
cd saarthi_video_engine
python -c "import sys; sys.path.insert(0,'src'); from dataset_adapter import load_dataset, style_profile; d=load_dataset(); print(style_profile(d,'SHORT_03')['layout_family'])"
```

`load_dataset()` reads `../reference_dataset/shorts_dataset.json`. Retrieval is `retrieve()` and `similar()` in `src/dataset_adapter/retrieve.py`. A style profile for one completed exemplar is `style_profile()`. Passing a partial id raises.

`manual_review_required` is true on every record because the audio was not listened to. It is not the completeness gate. `quality.status` is.

## Editing plan

Timestamps are seconds. A decision covers `[start, end)`. `show_teacher` and `replace_with_broll` cannot overlap. Overlays may overlap the teacher. Each decision stores the action, timing, reason, reference ids, confidence, and `observed`, `inferred`, or `default`.

```powershell
python build_proof.py
python tests\test_engine.py
```

`build_proof.py` selects completed exemplar `SHORT_03`, writes `examples/sample_editing_plan.json`, and writes the composition under `compositions/proof_of_concept/`. It also generates the synthetic media when `ffmpeg` is on PATH.

## Composition and audio

`src/hyperframes_adapter/compose.py` writes `index.html` and `compositions/title-card.html`.

Picture clips are `muted`. The original narration is one `<audio id="narration">` across the whole duration, so a picture cut does not mute or duplicate the voice. Music is a second `<audio id="music-bed">`. Its gain follows a `data-automation` volume lane: `0.35` when no speech segment is active, `0.14` while a transcript segment is active. That is a written envelope, not a compressor listening to the waveform. The bed is a generated tone. It is not a song from the references.

Asset records live in `compositions/proof_of_concept/asset_manifest.json`. `retrieval_implemented` is false. Placeholder rights are explicit.

## Preview and render

From `compositions/proof_of_concept`:

```powershell
npx hyperframes lint
npx hyperframes check --json
npx hyperframes preview
npx hyperframes render --quality draft --output ..\..\outputs\proof_of_concept.mp4
ffprobe -hide_banner -show_entries format=duration -show_entries stream=codec_type,codec_name,width,height -of default=nw=1 ..\..\outputs\proof_of_concept.mp4
```

`lint` must report 0 errors before a render. `check` also samples layout and contrast in Chrome. `preview` opens the HyperFrames studio. The proof was rendered with `--quality draft` because this pass is a pipeline test, not a delivery master.

After a render, `npx hyperframes snapshot` can write stills. `npx hyperframes audio <file> --json` measures the file. It does not listen to it.

## Tests

`python tests\test_engine.py` covers the dataset counts, partial-record exclusion, music identity staying null, plan validation, overlap rejection, one narration track in the HTML, the four layout families, and the ducking envelope.

## Verified on this machine

`python tests\test_engine.py` — 10 tests, all passed.

`npx hyperframes lint` on `compositions/proof_of_concept` — 0 errors, 0 warnings.

`npx hyperframes check --json` — `ok: true` (one nested-title warning existed before the title was moved into `compositions/title-card.html`; the later lint is clean).

`npx hyperframes render --quality draft --output ..\..\outputs\proof_of_concept.mp4` wrote an 8.0 second, 720×1280, 30 fps H.264 file with one stereo AAC stream at 48 kHz. `silencedetect` at −40 dB / 0.3 s found no gap. Snapshots at 1.0 s, 3.2 s, and 6.0 s show the blue field, the orange placeholder, then the blue field again, with the matching caption on each and the title and test mark still up. The audio was measured, not listened to.

The first synthetic encodes had sparse keyframes. HyperFrames still extracted a full frame coverage for this render, and the stills match the plan. `src/assets/synthetic.py` now asks x264 for a keyframe every 30 frames. Re-run `python build_proof.py` before the next render so the media matches that setting.

## Phase 2

`python build_phase2.py` reads the lecture in `test_assets/educator_source/`, cuts one 30–60 second interval on ASR boundaries, and writes `compositions/phase2_real_video/`. The original Ogg is not modified. Render from that directory:

```powershell
npx hyperframes render --quality draft --output ..\..\outputs\phase2\phase2.mp4
python ..\..\validate_phase2.py
```

Transcription is local whisper.cpp (`tools/whisper/whisper-cli.exe` and `ggml-tiny.bin`). Set `SAARTHI_WHISPER_CLI` and `SAARTHI_WHISPER_MODEL` to point elsewhere. There is no cloud key in the source. A missing binary raises `WhisperCppError` and does not invent text.

English speech keeps the ASR string as both `original_text` and `english_text`. A non-English segment without a supplied translation leaves `english_text` empty. Word times are copied from token offsets only.

Commons image search is in `src/assets/commons.py`. A still is kept only when the license is on the allow list and the description matches the spoken words. This cut did not use a still.

## Current limits

- The Phase 1 proof input is synthetic color and a sine tone. Captions match that test transcript. They were not heard from a teacher.
- Phase 2 transcription is whisper.cpp tiny. The wording was not hand-corrected.
- Teacher-visibility figures copied into a style profile remain 1 fps estimates.
- Logo spelling read from reference frames is not reused. The proof mark says TEST.
- `other_or_mixed` has no renderer. A completed record in that group cannot seed a profile until a family is added.

## Phase 7 — ElevenLabs transcription and lesson analysis

Copy `.env.example` to `.env` in this folder. Paste the ElevenLabs key into `ELEVENLABS_API_KEY` in that local file. Do not paste the key into chat, source, tests, or a report. `.env` is gitignored. The checked-in example leaves the value empty.

No extra package is required. `src/config/env.py` reads `.env`. `jsonschema` is already the project dependency:

```powershell
cd saarthi_video_engine
python -m pip install -e .
```

If the key is missing, `python run_phase7.py` exits 2 and names `.env`. It does not call the API.

With a key in `.env`, the same command sends each raw lesson to ElevenLabs, writes `outputs/dr_shivin_phase7/VIDEO_*/transcript.json` and `lesson_analysis.json`, and searches Wikimedia Commons for planned slots. It does not render a new MP4. A slot stays `rendered: false` until an editing plan attaches the file.

The request is `POST https://api.elevenlabs.io/v1/speech-to-text` with model `scribe_v2`, header `xi-api-key`, and word timestamps. Language is detected unless `ELEVENLABS_LANGUAGE_CODE` is set. The spoken wording is kept. Hindi-English speech is not translated. Each word keeps the provider `logprob`. Segment `confidence` stays null because the API does not send a confidence score. A word with `logprob` below -1.0, or a word with no timestamp, is flagged `low_logprob_or_missing_time` for review. That cutoff is a local rule. Unchanged files are cached by the file bytes and the transcription settings. Commons rate limits are retried with a short backoff. One failed source does not invent a file.

To write an analysis JSON from the Phase 6 transcript without calling ElevenLabs:

```powershell
python run_phase7.py --use-existing-transcript
```

That writes `outputs/dr_shivin_phase7/VIDEO_1/lesson_analysis.json` and the same file for `VIDEO_2`. The provider field stays the existing transcript's method. A planned slot, a found candidate, a downloaded file, an approved file, and a rendered interval are separate fields. `rendered` is true only when the editing plan has a `replace_with_broll` decision over that interval. Coverage is rendered B-roll seconds divided by the main-lesson seconds. The outro is not in that duration.

A printed `warning:` means a slot was planned and nothing was attached. A printed `shortfall:` means rendered coverage is under the 30–40% planning target and says why. Do not fill that gap with an unrelated clip.

The picture render is still:

```powershell
python run_phase6.py
```

Check `outputs/dr_shivin_phase6/VIDEO_*/outro.json` for `broll_seconds` and `lesson_plan_duration`. Divide those two. That is the rendered coverage, not the planned coverage.

```powershell
python tests\test_phase7.py
python tests\test_engine.py
python tests\test_phase2.py
python tests\test_phase4.py
python tests\test_phase5.py
python tests\test_phase6.py
```
