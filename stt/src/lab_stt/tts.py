"""Text-to-speech: Kokoro-82M, resident next to the ASR model.

The browser's speechSynthesis was the first implementation of read-aloud and
survives as the fallback — but on most lab machines its voices are the
espeak-era robots, which operators reasonably refuse to listen to. Kokoro is
the smallest model that sounds like a person: ~82M params (~300 MB on the
GPU beside the 4.3 GB ASR model), ~100-300 ms warm for a sentence, Apache.

Same privacy posture as ASR: text in, audio out, nothing persisted, nothing
logged but sizes and latency.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24_000
# The dashboard speaks single shaped sentences (~180 chars); anything huge is
# a caller bug, and synthesis time scales with length.
MAX_TEXT_CHARS = 600


@dataclass
class Synthesis:
    wav: bytes
    audio_s: float
    elapsed_ms: int


class KokoroTtsEngine:
    def __init__(self, voice: str = DEFAULT_VOICE, device: str = "cuda") -> None:
        from kokoro import KPipeline  # deferred: heavy, and tests fake this class

        t0 = time.monotonic()
        self.voice = voice
        self.pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=device)
        logger.info("kokoro ready on %s in %.1fs", device, time.monotonic() - t0)
        # First synthesis pays lazy voice-pack load + CUDA warmup; absorb it
        # here so no operator's first answer does.
        t0 = time.monotonic()
        self.synthesize("Ready.")
        logger.info("tts warmup in %.1fs", time.monotonic() - t0)

    def synthesize(self, text: str) -> Synthesis:
        import numpy as np
        import soundfile as sf

        t0 = time.monotonic()
        chunks = [audio for _, _, audio in self.pipeline(text, voice=self.voice)]
        audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV")
        return Synthesis(
            wav=buf.getvalue(),
            audio_s=round(len(audio) / SAMPLE_RATE, 2),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
