"""
Speech for Hey Me:
  - hearing:  turns recorded audio into text with Whisper (runs offline on this PC)
  - talking:  turns text into a .wav file with the Windows voice
"""

import io
import os
import time
import uuid
import wave
import shutil
import threading
import subprocess
from pathlib import Path

import numpy as np

import settings

HERE = Path(__file__).parent
SPEECH_DIR = HERE / "speech_cache"
SPEECH_DIR.mkdir(exist_ok=True)

_whisper = None
_whisper_lock = threading.Lock()


# ---------------------------------------------------------------------
# Hearing
# ---------------------------------------------------------------------
def load_whisper():
    global _whisper
    from faster_whisper import WhisperModel
    _whisper = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")


def wav_to_samples(data: bytes) -> np.ndarray:
    """Read a 16-bit WAV file and return 16 kHz mono samples (-1.0 to 1.0)."""
    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("Audio must be a 16-bit WAV file")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(audio) > 1:
        new_len = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, new_len),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


def transcribe(wav_bytes: bytes) -> str:
    audio = wav_to_samples(wav_bytes)
    if len(audio) < 4000:               # less than a quarter second
        return ""
    with _whisper_lock:
        segments, _ = _whisper.transcribe(audio, language="en", beam_size=5,
                                          vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    # Whisper sometimes "hears" these in silence
    if text.lower().strip(" .!") in ("", "you", "thank you", "thanks for watching"):
        return ""
    return text


# ---------------------------------------------------------------------
# Talking
# ---------------------------------------------------------------------
_PS_SCRIPT = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "if ($env:HEYME_VOICE) { try { $s.SelectVoice($env:HEYME_VOICE) } catch {} };"
    "$s.Rate = [int]$env:HEYME_RATE;"
    "$s.SetOutputToWaveFile($env:HEYME_OUT);"
    "$s.Speak($env:HEYME_TEXT);"
    "$s.Dispose()"
)


def make_speech(text: str) -> str:
    """Create a .wav of `text`. Returns the file name, or "" if it couldn't."""
    if not text.strip():
        return ""
    _cleanup_old_files()
    name = f"{uuid.uuid4().hex}.wav"
    out = SPEECH_DIR / name
    try:
        if os.name == "nt":
            env = dict(os.environ, HEYME_TEXT=text, HEYME_OUT=str(out),
                       HEYME_VOICE=settings.VOICE, HEYME_RATE=str(int(settings.VOICE_SPEED)))
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                           env=env, timeout=60, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif shutil.which("espeak-ng"):
            subprocess.run(["espeak-ng", "-w", str(out), text], timeout=60, capture_output=True)
        else:
            return ""
    except Exception as e:
        print("Speech error:", e)
        return ""
    return name if out.exists() and out.stat().st_size > 100 else ""


_last_cleanup = 0.0


def _cleanup_old_files():
    global _last_cleanup
    if time.time() - _last_cleanup < 600:
        return
    _last_cleanup = time.time()
    for f in SPEECH_DIR.glob("*.wav"):
        try:
            if time.time() - f.stat().st_mtime > 3600:
                f.unlink()
        except OSError:
            pass
