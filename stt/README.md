# sdl-lab-stt

Loopback speech service for the lab assistant: speech-to-text for push-to-talk
and text-to-speech for read-aloud.
[Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf) is held resident
on a GPU — **no audio ever leaves the tailnet**, and the model's trained-in
context biasing is fed the device names from `equipment.yaml`, so "PlateLoc"
and "ot2 hte" transcribe as themselves.
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is the read-aloud voice.

Not a workspace member: own `.venv` (Python 3.12 — the GPU stack does not
support the workspace's 3.14), own `uv sync`, and a GPU model must never load
inside the `api/` process.

```
uv sync                       # from stt/ — installs torch (CUDA) + transformers
uv run lab-stt-serve          # 127.0.0.1:8070; first start downloads ~4 GB
curl -F audio=@clip.wav http://127.0.0.1:8070/transcribe
```

| Route | Contract |
|---|---|
| `GET /health` | `{status, asr, model, loaded, load_failed, tts, tts_voice}` — `loaded` gates the mic button, `tts` the server voice |
| `POST /transcribe` | multipart `audio` (anything ffmpeg decodes) → `{text, audio_s, elapsed_ms, model}`; 503 on a TTS-only instance |
| `POST /speak` | `{text}` (≤600 chars) → `audio/wav` — Kokoro-82M, the read-aloud voice |

Config (env): `STT_MODEL` (default `Qwen/Qwen3-ASR-1.7B-hf`; empty string =
serve without loading, for tests), `STT_ASR` (`0` = TTS-only: no ASR model;
a failed Kokoro load then fails the instance instead of being tolerated),
`STT_DEVICE` (default `cuda`; `cpu` for a TTS-only instance),
`STT_HOST`/`STT_PORT`, `STT_EQUIPMENT_YAML`, `STT_VOCAB_FILE` (extra terms, one
per line), `STT_TTS_VOICE` (Kokoro voice for `/speak`; default `af_heart`, `""`
disables).

## Deployed as two instances

| Unit | Host | Role | Latency |
|---|---|---|---|
| `ac-organic-lab-stt` | gaia, RTX 5080 | ASR (`STT_TTS_VOICE=`) | 0.27 / 0.62 / 0.87 s for 2 / 7 / 12.5 s clips; ~4.4 GB VRAM |
| `ac-organic-lab-tts` | orchestration, CPU | TTS (`STT_ASR=0`, `STT_DEVICE=cpu`, 8 threads) | ~0.5–1 s per sentence |

The dashboard reaches gaia through `dashboard-voice-relay` and the TTS instance
on loopback; units, relay and install steps are in
[`deploy/README.md`](../deploy/README.md#optional-voice-push-to-talk--read-aloud).
The CUDA torch wheel from the lock is used on both hosts; it runs on CPU.

Measured on the orchestration host (Core Ultra 9 285H, no NVIDIA), 2026-10-06:

- **Kokoro on CPU** — ~0.5 s for a 3.4 s sentence, ~1.5 s for 9.7 s of
  speech, with no first-use penalty. 8 threads is fastest; torch's default
  of 16 lands on the E-cores and is 3–5× slower. ONNX Runtime and OpenVINO
  CPU are no faster; the int8 ONNX model is slower.
- **Kokoro on the Arc iGPU (PyTorch XPU)** — ~0.25 s only for sentence lengths
  already seen in this process; each new length JIT-compiles kernels for
  1–14 s, again after every restart (`SYCL_CACHE_PERSISTENT` only halves it).
  Unusable for varied text. OpenVINO's GPU plugin rejects Kokoro's 3D
  `linear` interpolate.
- **Kokoro on the NPU** — the compiler cannot handle Kokoro's data-dependent
  frame count, even with a static token length.
- **Qwen3-ASR on CPU** — 4.4–8.8 s (bf16) for the same clips; fp32 is slower.
  ASR needs a GPU.

## Traps

- **espeak data path ≤ 160 chars.** misaki points the bundled libespeak-ng
  (espeakng-loader 0.2.4) at `site-packages/espeakng_loader/espeak-ng-data`;
  the library's path buffer is 160 bytes, and a longer path silently falls
  back to the build machine's compiled-in path (`Error processing file
  '/home/runner/…/phontab'`). `stt/.venv` in the checkout is ~105 chars; a venv
  under a deep temp directory is not.
- **`en_core_web_sm` is pinned** in `pyproject.toml`. misaki otherwise
  pip-installs it at first use, which fails in a venv without pip or a unit
  without network.
- **Don't add `kokoro-onnx`** to this venv: it pulls upstream `phonemizer`,
  which overwrites the `phonemizer-fork` files misaki needs.

Privacy: audio is decoded in a TemporaryDirectory and never persisted; logs
carry durations and latency, never text. The dashboard-side caller
(`api/app/voice.py`) requires a verified `X-Auth-User` and logs who spoke,
not what was said.
