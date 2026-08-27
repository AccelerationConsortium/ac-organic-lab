"""The ASR engine: Qwen3-ASR via transformers, loaded once, resident in VRAM.

Held resident because latency is the requirement: a cold load is seconds,
a resident 1.7B model transcribes a push-to-talk clip in a few hundred ms
on the RTX 5080. faster-whisper was considered and benched second — its
CTranslate2 INT8 path is broken on Blackwell (sm_120), and Whisper is the
weaker model for exactly this workload (silence hallucination, weaker
vocabulary biasing). Engine choice stays behind STT_MODEL so a bake-off is
an env change, not a rewrite.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen3-ASR-1.7B-hf"
# Hard cap on clip length. Push-to-talk utterances are seconds; anything
# longer is a stuck button, not a question.
MAX_CLIP_S = 32


@dataclass
class Transcript:
    text: str
    audio_s: float
    elapsed_ms: int


def _ffmpeg_to_wav16k(src: Path, dst: Path) -> float:
    """Decode whatever the browser sent (webm/opus, ogg, mp4...) to 16 kHz
    mono WAV, capped at MAX_CLIP_S. Returns the decoded duration in seconds.

    ffmpeg rather than python codecs: MediaRecorder's container/codec choice
    varies by browser, and ffmpeg swallows all of them.
    """
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-t", str(MAX_CLIP_S),
            "-ac", "1", "-ar", "16000", "-f", "wav",
            "-y", str(dst),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(dst)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return float(out.stdout.strip() or 0.0)


class QwenAsrEngine:
    def __init__(self, model_id: str = DEFAULT_MODEL, device: str = "cuda") -> None:
        # Imported here, not at module top: tests exercise the service without
        # the multi-GB stack, and the import itself takes seconds.
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        t0 = time.monotonic()
        self.model_id = model_id
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_id, dtype=torch.bfloat16, device_map=device
        )
        self.model.eval()
        self.device = device
        logger.info("loaded %s on %s in %.1fs", model_id, device, time.monotonic() - t0)
        self._warmup()

    def _warmup(self) -> None:
        """One throwaway transcription of synthetic silence at load time.
        The first generate() pays CUDA kernel/graph warmup (~3s measured);
        paying it here means no operator's first utterance ever does."""
        import struct

        t0 = time.monotonic()
        rate, seconds = 16000, 1
        pcm = b"\x00\x00" * rate * seconds
        wav = (
            b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm
        )
        try:
            self.transcribe(wav, "warmup")
            logger.info("warmup transcription in %.1fs", time.monotonic() - t0)
        except Exception:  # warmup is an optimization, never a boot failure
            logger.exception("warmup failed (continuing)")

    def transcribe(self, audio_bytes: bytes, context: str) -> Transcript:
        import torch

        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="lab-stt-") as td:
            src = Path(td) / "in.bin"
            wav = Path(td) / "in.wav"
            src.write_bytes(audio_bytes)
            audio_s = _ffmpeg_to_wav16k(src, wav)

            inputs = self.processor.apply_transcription_request(
                audio=str(wav), prompt=context, language="English"
            # dtype= casts only the floating tensors (the audio features) to
            # the model's bfloat16; input_ids stay integer. Without it the
            # float32 features hit bf16 conv weights and generate() raises.
            ).to(self.model.device, dtype=self.model.dtype)
            with torch.inference_mode():
                output_ids = self.model.generate(**inputs, max_new_tokens=256)
            text = self.processor.decode(
                output_ids[:, inputs["input_ids"].shape[1]:][0],
                return_format="transcription_only",
            )

        return Transcript(
            text=(text or "").strip(),
            audio_s=round(audio_s, 2),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
