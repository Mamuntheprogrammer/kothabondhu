# কথাবন্ধু · Kotha Bondhu — `ai_version`

A rebuilt, modern interface for the original offline TTS engine. Instead of a desktop
window that owns the audio device, this version is a **local web studio**: a tiny
Python server streams freshly synthesized audio to a browser UI over Server-Sent
Events, and playback happens with the Web Audio API.

Nothing leaves the machine. No API keys. No CDN fonts or scripts. The Kokoro,
MMS and two-voice Bangla models are bundled in `ai_version/models/`, so a fresh
copy runs offline immediately — no download step, no user cache to find.
**Optional voice cloning is off by default**: it is installed only if you open
the Clone tab and ask for it, and it remains isolated from the lightweight
offline path.

```
python ai_version/run.py
```

Then open <http://127.0.0.1:8765> (the browser opens automatically). Use
`--port`, `--host`, `--no-browser`, `--models PATH` or `--debug` if needed.

> Self-contained: no separate TTS package is needed. Create a virtual
> environment and run `pip install -r requirements.txt` from this folder.
>
> Repository: <https://github.com/mamuntheprogrammer/Kotha_bondhu>

---

## What changed in the rebuild

| | Original (v2.0) | **Kotha Bondhu (ai_version)** |
|---|---|---|
| UI toolkit | CustomTkinter desktop window | Custom HTML/CSS/JS, dark + light, responsive |
| Rendering | Widget updates from a Tk `after` pump | DOM + canvas, no layout blocked by work |
| Audio playback | `sounddevice` on the UI process | Web Audio API, analyser-driven waveform |
| Streaming | Player thread in the same process | SSE events + per-chunk WAV fetch |
| Long work | Worker thread + event queue | Dedicated job worker, one job at a time, cancel/supersede |
| Model downloads | Blocking progress bar per row | Background downloads with live SSE progress + toasts |
| Voice picker | Dropdown of 30 labels | Searchable voice browser: 30 English, 2 fixed Bangla, plus clones |
| Bangla tone | One engine call per sentence | Optional steady mode reads longer blocks to avoid pitch drift |
| History | none | Last 15 generations, restore text or replay audio |
| Export | File dialog, one format | WAV / FLAC / MP3 / OGG from a download menu |
| Playback controls | Pause / stop | Play, pause, seek, stop, mute, replay, live progress |
| Bangla cache | Fails if `manifest.json` is missing | Self-heals the manifest on startup |

The default Kokoro/MMS path keeps the original quality: the studio now carries
its own small `core/` package with the engines, text planner, audio helpers and
model bookkeeping. The optional Bangla voices and cloning live beside it.

---

## Portable models (included)

`ai_version/models/` already contains all three engines:

```
ai_version/models/
├── kokoro/            310 MB model + 27 MB voices + checksums
├── mms-ben-onnx/      109 MB fp32 + 37 MB int8 + vocab + manifest
└── indic-tiny-bn/     91 MB ONNX graphs + runtime package + sample clips
```

Startup resolution order is:

1. `--models PATH` (or an existing `AWAZ_CACHE_DIR`),
2. `ai_version/models/`,
3. the per-user cache (`%LOCALAPPDATA%\Awaaz\Cache` on Windows — legacy cache name kept for compatibility).

So moving the whole project folder to another PC keeps all voices working
without downloading anything. The model panel shows the active folder. The
`--models` flag and the core's `AWAZ_CACHE_DIR` variable remain available for a
shared or external model drive. Model downloads, if ever needed, go into the
active folder.

> The bundled MMS model is CC-BY-NC 4.0 (non-commercial). Kokoro-82M is
> Apache 2.0. The two Bangla voices are CC-BY-4.0 (acoustic weights; Rasa data)
> and MIT (Vocos vocoder). See `ai_version/models/README.md`.

---

## Why it feels fast

- **Nothing blocking on the UI thread.** The server owns one synthesis worker
  thread. Requests return a job id immediately; the page never waits for a model.
- **First audio before the last sentence.** Each synthesized chunk is published as
  soon as it exists. The browser fetches and decodes it while later chunks are
  still being generated.
- **SSE, not polling.** One long-lived event stream carries plans, progress,
  warnings and completion. Reconnects resume from `Last-Event-ID`, so no duplicate
  audio.
- **Per-chunk WAV endpoints.** Audio bytes stay out of the event stream; the
  browser streams them with normal HTTP caching disabled.
- **Cached model checks.** Hashing hundreds of megabytes on every page load would
  be silly, so model status is cached and warmed in the background at startup.
- **AudioContext scheduling.** Chunks are scheduled back-to-back with a small lead
  time; pause/resume suspends the whole graph, which is both instant and accurate.

---

## Features

**Composer**
- Auto / বাংলা / English routing, with the original script splitter for mixed text
- Live character, word and estimated-duration stats
- Draft autosave, sample texts, clear, drag-and-drop `.txt`
- `Ctrl/⌘ + Enter` to speak, `/` to focus, `Esc` to stop

**Voice**
- 30 Kokoro English voices, grouped by accent and gender
- MMS single-speaker Bangla (the original default) plus fixed female/male Bangla voices
- Search by name, style or id; preview without leaving the picker
- **Steady Bangla tone** merges adjacent Bangla sentences into one ~480-character
  block, so prosody does not restart on every line (on by default)
- Speed 0.5×–2.0×, Bangla/English loudness balancing, auto-play toggle

**Player**
- Streaming playback with live waveform
- Play / pause / seek / stop / mute
- Segment chips show language and duration as they arrive
- Replay finished audio or load it again from history

**Models**
- Three status cards (English, Bangla MMS, Bangla male/female) with sizes and cache path
- Background downloads with percent, bytes and file label
- Re-check without restarting

**Clone (optional)**
- Self-service setup of IndicF5 in an isolated virtual environment
- CPU or CUDA/GPU install profile, auto-detected from `nvidia-smi`
- Gated model download with a token saved locally once (owner-readable file)
- Add Bengali reference voices (file or microphone), preview, select, delete
- One cloned voice reads every segment; English may sound accented
- Speed presets (Fast 8 / Balanced 16 / Best 32 diffusion steps) for CPU users
- Periodic status pings and SSE logs — setup never blocks the UI

**Export**
- WAV (16-bit PCM, 24 kHz), FLAC, MP3, OGG
- Served by the local server with a proper filename

---

## Bangla voices and steady tone

The original MMS Bengali model has one speaker. The studio still uses it when no
Bangla voice is chosen. It also bundles the small
[Indic Tiny TTS Bengali](https://huggingface.co/sulabhkatiyar/indic-tiny-tts-bengali)
model with two fixed speakers — **Bangla Female** and **Bangla Male**. Pick one
from the voice menu; it applies to every Bangla segment (including Bangla text in
Auto mode). No token, GPU or cloning setup is needed.

The other Bangla improvement is the **Steady Bangla tone** switch. Normally the
core planner splits at every `।`, `!`, `?`, newline and `.`; each fragment is
synthesized independently, so pitch and rhythm restart and can sound like the
tone changes. With the switch on (default), adjacent Bangla fragments are
merged into blocks of up to ~480 characters and read in one pass. Mixed text and
language order are preserved. Turn it off for the fastest first chunk.

The two-voice model uses a fixed random seed, so the same sentence sounds the
same on repeated runs. Its speed control changes duration without the
pitch-shifting resampling used by MMS.

---

## Optional voice cloning

Voice cloning is **not part of the default install**. Nothing is downloaded,
installed, or imported into the main process until you start it from
**Models → Clone**. The normal Kokoro + MMS path keeps working unchanged.

The engine is [`ai4bharat/IndicF5`](https://huggingface.co/ai4bharat/IndicF5)
(MIT, 350M parameters, 24 kHz Bengali output). Its official repository is gated,
so a Hugging Face token is needed once for the ~1.45 GB model download. Use the
Clone tab to:

1. **Save a token** — pasted into the browser and stored once in
   `<cache>/cloning/token` with owner-only permissions, or read from `HF_TOKEN` /
   the standard Hugging Face token file. It is never returned to the page. Save
   an empty token to clear it. It is only needed for the one-time gated download;
   cloning and speaking work offline once the model is present.
2. **Install the runtime** — creates `<cache>/cloning/venv` with a pinned,
   isolated dependency set (`numpy<=1.26.4`, `transformers<4.50`, …). Choose
   `CUDA / GPU` when installing on the GPU machine; the CPU profile is available
   but very slow. Pick a **Cloning speed** preset here (step 2): Balanced (16
   steps) is the default, Fast (8) is roughly 4× quicker on CPU, Best (32) sounds
   most natural. The choice persists and takes effect on the next run.
3. **Download IndicF5** — weights, vocabulary, the `f5_tts` modules and the
   Vocos vocoder, with resumable progress. Everything is stored under
   `<cache>/cloning/`.
4. **Add a reference voice** — a clear **3–60 s** clip (WAV/FLAC/OGG or browser
   recording), trimmed and peak-normalized to mono 24 kHz, plus the **exact
   transcript**. Confirm you have permission to use that voice.
5. **Pick it from the voice menu** — a cloned voice overrides the voice for all
   segments. It is Bengali-tuned, so English text in a mixed script will be
   understandable but accented.

Inference runs in a separate `clone_worker.py` subprocess using JSON lines over
stdin/stdout. A crash or out-of-memory failure there cannot take down the
studio; pressing **Stop** or **Speak** again terminates that worker. Speed
changes use `librosa.effects.time_stretch` when available (a simpler pitch-
shifting resample is the fallback).

> **CPU warning.** IndicF5 is a diffusion model. On a 2-core CPU a sentence can
> take several minutes. Install the CUDA profile on the GPU machine for normal
> use. The UI stays responsive either way: synthesis happens in the worker and
> audio is streamed as soon as a chunk exists.

For automated testing without the model or PyTorch, start the server with
`AWAZ_CLONE_MOCK=1`. The worker then emits short standard-library tones, so the
whole upload → job → stream → export flow can be exercised offline.

---

## Architecture

```
ai_version/
├── run.py            launcher
├── server.py         ThreadingHTTPServer, JSON + SSE routes, static files
├── studio.py         job queue, worker, events, downloads, export, cache repair
├── paths.py          bundled-model discovery / AWAZ_CACHE_DIR selection
├── core/             local synthesis core (no external TTS package)
│   ├── config.py     cache path and shared constants
│   ├── engines/      Kokoro (en) + MMS-TTS (bn), registry, engine interface
│   ├── text/         script detection → Bangla normalization → chunking
│   ├── audio/        resampling, loudness, WAV/FLAC/MP3/OGG export
│   ├── models/       download, verify, cache
│   └── pipeline.py   request → ordered (language, text) jobs
├── bn_voices.py      bundled two-speaker Bangla ONNX engine + downloader
├── cloning.py        optional manager: venv, HF download, voices, TTSEngine
├── clone_worker.py   isolated IndicF5 subprocess (JSON-lines protocol)
├── models/           bundled Kokoro + MMS + Indic Tiny models (portable, offline)
└── web/
    ├── index.html    layout + SVG icon sprite
    ├── styles.css    design system (dark/light, responsive, reduced-motion)
    └── app.js        state, SSE client, Web Audio player, canvas, UI
```

Flow for one press of **Speak**:

1. `POST /api/speak` validates the request and enqueues a `Job`; the response is
   immediate (`{ job, url }`).
2. The worker plans `(language, text)` chunks, optionally merges adjacent Bangla
   chunks for steadier tone, emits a `plan` event, then loads
   the needed engine if it is cold.
3. Every synthesized chunk is resampled to 24 kHz, loudness-matched when mixed,
   encoded as WAV and stored on the job; a `chunk` event with its URL is emitted.
4. The browser fetches each WAV, decodes it, and schedules it on one `AudioContext`.
5. `done` reports generation time and audio duration; export becomes available.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/status` | Models, voices, clone status, cache path, recent jobs |
| `POST` | `/api/speak` | `{text, language, voice, voice_bn, clone_voice, speed, loudness, steady_bn}` → job id |
| `GET` | `/api/jobs/{id}/events` | SSE stream (`plan`, `stage`, `chunk`, `done`, …) |
| `GET` | `/api/jobs/{id}` | Job snapshot with chunk metadata |
| `GET` | `/api/jobs/{id}/chunks/{n}` | One 24 kHz WAV chunk |
| `POST` | `/api/jobs/{id}/cancel` | Cancel a run |
| `GET` | `/api/jobs/{id}/export?format=wav` | Concatenated download |
| `GET` | `/api/models/download?key=kokoro` | SSE download progress |
| `POST` | `/api/preview` | `{voice}` → short WAV preview |
| `GET` | `/api/clone` | Clone environment, model, device and saved voices |
| `GET` | `/api/clone/setup?profile=cpu\|cuda` | SSE: install isolated runtime |
| `POST` | `/api/clone/token` | `{token}` → save/clear the local token |
| `POST` | `/api/clone/quality` | `{quality}` → fast / balanced / best |
| `GET` | `/api/clone/download` | SSE: download/resume IndicF5 with progress |
| `POST` | `/api/clone/voices` | `{name, text, audio, language}` → save a reference voice |
| `GET` | `/api/clone/voices/{id}/audio` | Play the saved reference clip |
| `POST` | `/api/clone/voices/{id}/delete` | Remove a cloned voice |
| `POST` | `/api/open-cache` | Reveal the model folder |

All endpoints are local-only (`127.0.0.1` by default).

---

## Notes

- **Bangla manifest repair.** Some existing caches contain `model.onnx` and
  `vocab.json` but no `manifest.json`, which makes the original app report the
  Bangla model as missing. The studio derives and writes that manifest on
  startup, so no 500 MB re-download is needed.
- **Bangla model setup.** If the ONNX export is genuinely absent, copy a set of
  `model.onnx`, `vocab.json` and `manifest.json` into `models/mms-ben-onnx/`.
  Producing that export needs PyTorch on a separate machine; the studio itself
  only reads the finished files.
- **MP3/OGG/FLAC.** Availability depends on the local `libsndfile`; the server
  returns a readable error instead of a broken file.
- **Licenses.** Kokoro-82M is Apache 2.0. MMS-TTS Bengali is CC-BY-NC 4.0
  (non-commercial). Indic Tiny TTS Bengali acoustic weights are CC-BY-4.0 with
  MIT runtime code and a MIT Vocos vocoder. IndicF5 is MIT; its Vocos vocoder
  ships with its own license.
- **Privacy.** Text, audio, history and preferences stay on this machine.
  History lives in browser `localStorage`; audio lives in server memory for up
  to two hours (last 24 jobs) and can be cleared by restarting the server. A
  cloning token, if saved, persists locally in `<cache>/cloning/token` (mode
  `0600`) so you only paste it once; clear it with an empty Save token.
