# sdl-lab-stt

Loopback speech-to-text for the lab assistant's push-to-talk input.
[Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf) held resident
on the local GPU — **no audio ever leaves the tailnet**, and the model's
trained-in context biasing is fed the device names from `equipment.yaml`, so
"PlateLoc" and "ot2 hte" transcribe as themselves.

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
| `GET /health` | `{status, model, loaded, load_failed}` — `loaded` gates the mic button |
| `POST /transcribe` | multipart `audio` (anything ffmpeg decodes) → `{text, audio_s, elapsed_ms, model}` |

Measured on the RTX 5080: ~550–700 ms for an 11 s clip warm (a startup warmup
pass absorbs CUDA's ~3 s first-request cost); ~4.4 GB VRAM.

Config (env): `STT_MODEL` (default `Qwen/Qwen3-ASR-1.7B-hf`; empty string =
serve without loading, for tests), `STT_DEVICE`, `STT_HOST`/`STT_PORT`,
`STT_EQUIPMENT_YAML`, `STT_VOCAB_FILE` (extra terms, one per line).

Privacy: audio is decoded in a TemporaryDirectory and never persisted; logs
carry durations and latency, never text. The dashboard-side caller
(`api/app/voice.py`) requires a verified `X-Auth-User` and logs who spoke,
not what was said.
