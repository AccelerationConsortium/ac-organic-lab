"use client";

/**
 * Push-to-talk voice input for the assistant bubble.
 *
 * One click to activate; the recording ends itself. An AnalyserNode watches
 * signal level, and once speech has been heard, ~0.9s of trailing quiet stops
 * the recorder — so the whole gesture is click, speak, done. A second click
 * stops it manually; a 30s cap backstops a noisy room that never reads as
 * quiet.
 *
 * The clip goes to /api/assistant/voice/transcribe (identity-gated by the
 * same middleware as chat, forwarded to the loopback GPU service — audio
 * never leaves the tailnet) and the transcript is handed to the caller. What
 * to DO with it is deliberately not decided here: Ask mode auto-sends for
 * speed, Control mode only fills the input box, and that policy lives in the
 * component where the modes do.
 *
 * `supported` is false without MediaRecorder + getUserMedia — which includes
 * every plain-http origin, where the mic API simply does not exist.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceState = "idle" | "recording" | "transcribing";

// Trailing quiet that ends an utterance. Long enough for a mid-sentence
// breath, short enough that the stop feels immediate.
const SILENCE_MS = 900;
// RMS (0..1) above which a frame counts as speech. Lab-noise floors sit well
// below this on a headset mic; a lapel mic next to a shaker may need tuning.
const SPEECH_RMS = 0.04;
const MAX_CLIP_MS = 30_000;
const LEVEL_POLL_MS = 100;

export function voiceSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

export function useVoiceInput(onTranscript: (text: string) => void) {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;

  // The button renders only when the browser can record AND the server can
  // transcribe — same configured-gate pattern as the bubble itself.
  useEffect(() => {
    if (!voiceSupported()) return;
    let alive = true;
    fetch("/api/assistant/voice/health")
      .then((r) => (r.ok ? r.json() : { configured: false }))
      .then((j) => alive && setAvailable(j.configured === true))
      .catch(() => alive && setAvailable(false));
    return () => {
      alive = false;
    };
  }, []);

  const stop = useCallback(() => {
    recorderRef.current?.state === "recording" && recorderRef.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (state !== "idle") {
      stop();
      return;
    }
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      setError("Microphone unavailable — check the browser permission.");
      return;
    }

    const recorder = new MediaRecorder(stream);
    recorderRef.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);

    // Silence endpointing. AudioContext is not in jsdom; guard so tests and
    // odd browsers degrade to manual stop instead of crashing.
    let audioCtx: AudioContext | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let heardSpeech = false;
    let quietSince = 0;
    try {
      audioCtx = new AudioContext();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      pollTimer = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const b of buf) {
          const v = (b - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const now = Date.now();
        if (rms >= SPEECH_RMS) {
          heardSpeech = true;
          quietSince = 0;
        } else if (heardSpeech) {
          quietSince ||= now;
          if (now - quietSince >= SILENCE_MS) stop();
        }
      }, LEVEL_POLL_MS);
    } catch {
      /* manual stop only */
    }
    const capTimer = setTimeout(stop, MAX_CLIP_MS);

    const cleanup = () => {
      clearTimeout(capTimer);
      if (pollTimer) clearInterval(pollTimer);
      void audioCtx?.close().catch(() => undefined);
      stream.getTracks().forEach((t) => t.stop());
      recorderRef.current = null;
    };
    cleanupRef.current = cleanup;

    recorder.onstop = async () => {
      cleanup();
      cleanupRef.current = null;
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (blob.size === 0) {
        setState("idle");
        return;
      }
      setState("transcribing");
      try {
        const form = new FormData();
        form.append("audio", blob, "clip");
        const res = await fetch("/api/assistant/voice/transcribe", {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          const detail = await res.text();
          throw new Error(`HTTP ${res.status}: ${detail.slice(0, 200)}`);
        }
        const { text } = (await res.json()) as { text: string };
        if (text?.trim()) onTranscriptRef.current(text.trim());
      } catch (e) {
        setError(e instanceof Error ? e.message : "transcription failed");
      } finally {
        setState("idle");
      }
    };

    recorder.start();
    setState("recording");
  }, [state, stop]);

  // Never leave a mic open on unmount.
  useEffect(
    () => () => {
      recorderRef.current?.state === "recording" && recorderRef.current.stop();
      cleanupRef.current?.();
    },
    []
  );

  return { state, error, available: available && voiceSupported(), start, stop };
}
