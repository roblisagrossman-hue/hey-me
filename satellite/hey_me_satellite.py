"""
HEY ME LISTENER - put this on any PC, mini PC or Raspberry Pi with a mic and speaker.
It listens for "Hey Me", records what you say, sends it to the Hey Me server,
and plays the answer. It also rings timers and plays announcements and music.

Edit the 4 settings below, then run:   python hey_me_satellite.py
Test without a mic:                    python hey_me_satellite.py --say "what time is it"
List microphones and speakers:         python hey_me_satellite.py --list-devices
"""

# ===================== SETTINGS =====================
SERVER = "http://192.168.1.10:8765"   # address of your Hey Me server PC
SECRET = "change-me-to-a-long-password"  # same SECRET as the server's settings.py
NAME = "living room"                   # the name of this room / device
WAKE_WORD = "hey me"

MIC = None        # None = default microphone, or a number from --list-devices
SPEAKER = None    # None = default speaker, or a number from --list-devices
# ====================================================

import io
import os
import sys
import json
import time
import queue
import wave
import shutil
import argparse
import threading
import subprocess

import numpy as np
import requests
import sounddevice as sd

RATE = 16000
CHUNK = 1600                     # 0.1 second
audio_q = queue.Queue(maxsize=300)
talk_lock = threading.Lock()
mic_muted = threading.Event()    # set while we're talking, so we don't hear ourselves
noise_level = 300.0
player = None                    # media player process


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------------
# Sound out
# ---------------------------------------------------------------------
def play_samples(samples, rate):
    sd.play(samples, rate, device=SPEAKER)
    sd.wait()


def tone(freq=880, ms=150, volume=0.3):
    t = np.arange(int(RATE * ms / 1000)) / RATE
    wave_ = (np.sin(2 * np.pi * freq * t) * volume).astype(np.float32)
    fade = min(len(wave_) // 4, 400)
    wave_[:fade] *= np.linspace(0, 1, fade)
    wave_[-fade:] *= np.linspace(1, 0, fade)
    play_samples(wave_, RATE)


def alarm_sound():
    for _ in range(3):
        tone(988, 180)
        tone(784, 180)
        time.sleep(0.15)


def play_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    samples = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        samples = samples.reshape(-1, ch)
    play_samples(samples, rate)


def say(text, audio_url=""):
    log("HEY ME:", text)
    if audio_url:
        try:
            r = requests.get(SERVER + audio_url, timeout=30)
            r.raise_for_status()
            play_wav(r.content)
            return
        except Exception as e:
            log("Couldn't play the server's voice:", e)
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception:
        pass


def find_player():
    for p in ("vlc", "mpv", "ffplay",
              r"C:\Program Files\VideoLAN\VLC\vlc.exe",
              r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"):
        path = shutil.which(p) or (p if os.path.exists(p) else None)
        if path:
            return path
    return None


def play_media(url):
    global player
    stop_media()
    full = SERVER + url
    exe = find_player()
    log("Playing:", full.split("?")[0])
    if exe:
        name = os.path.basename(exe).lower()
        if "vlc" in name:
            args = [exe, "--play-and-exit", "--fullscreen", full]
        elif "mpv" in name:
            args = [exe, "--fs", "--force-window=yes", full]
        else:
            args = [exe, "-autoexit", "-fs", full]
        player = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.name == "nt":
        os.startfile(full)
    else:
        log("Install VLC or mpv to play media here.")


def stop_media():
    global player
    if player and player.poll() is None:
        player.terminate()
    player = None


# ---------------------------------------------------------------------
# Talking to the server
# ---------------------------------------------------------------------
def headers():
    return {"X-HeyMe-Secret": SECRET}


def params():
    return {"device": NAME, "kind": "satellite"}


def handle_reply(r):
    say(r.get("reply", ""), r.get("audio_url", ""))
    if r.get("media_stop"):
        stop_media()
    if r.get("media_url"):
        play_media(r["media_url"])


def ask_text(text):
    r = requests.post(SERVER + "/api/ask_text", headers=headers(), timeout=90,
                      json={"text": text, "device": NAME, "kind": "satellite"})
    r.raise_for_status()
    handle_reply(r.json())


def ask_audio(samples):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.astype("<i2").tobytes())
    r = requests.post(SERVER + "/api/ask", params=params(), headers=headers(),
                      data=buf.getvalue(), timeout=90)
    if r.status_code == 403:
        raise RuntimeError("The server refused the SECRET. Make it match settings.py on the server.")
    r.raise_for_status()
    reply = r.json()
    log("YOU:", reply.get("heard", ""))
    handle_reply(reply)


def poll_events():
    """Every 2 seconds: anything for this room? (timers, announcements, music)"""
    warned = False
    while True:
        try:
            r = requests.get(SERVER + "/api/events", params=params(), headers=headers(), timeout=10)
            r.raise_for_status()
            if warned:
                log("Connected to the server again.")
            warned = False
            for ev in r.json().get("events", []):
                with talk_lock:
                    mic_muted.set()
                    try:
                        if ev.get("type") == "announce":
                            if ev.get("alarm"):
                                alarm_sound()
                            else:
                                tone(660, 120)
                            say(ev.get("text", ""), ev.get("audio_url", ""))
                        elif ev.get("type") == "media":
                            play_media(ev["url"])
                        elif ev.get("type") == "media_stop":
                            stop_media()
                    finally:
                        clear_mic()
                        mic_muted.clear()
        except Exception as e:
            if not warned:
                log("Can't reach the Hey Me server yet:", e)
                warned = True
        time.sleep(2)


# ---------------------------------------------------------------------
# Listening
# ---------------------------------------------------------------------
def mic_callback(indata, frames, t, status):
    if mic_muted.is_set():
        return
    try:
        audio_q.put_nowait(bytes(indata))
    except queue.Full:
        pass


def clear_mic():
    while not audio_q.empty():
        try:
            audio_q.get_nowait()
        except queue.Empty:
            break


def rms(samples):
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) else 0.0


def record_request():
    """Record until you stop talking (or 12 seconds)."""
    threshold = max(400.0, noise_level * 2.5)
    chunks, silent, total, heard_voice = [], 0.0, 0.0, False
    while total < 12:
        try:
            data = audio_q.get(timeout=2)
        except queue.Empty:
            break
        s = np.frombuffer(data, dtype=np.int16)
        chunks.append(s)
        secs = len(s) / RATE
        total += secs
        if rms(s) > threshold:
            heard_voice, silent = True, 0.0
        else:
            silent += secs
        if heard_voice and silent > 1.0:
            break
        if not heard_voice and total > 5:
            return None
    if not heard_voice:
        return None
    return np.concatenate(chunks)


def conversation():
    with talk_lock:
        mic_muted.set()
        tone(1000, 120)
        clear_mic()
        mic_muted.clear()
        log("Listening...")
        audio = record_request()
        mic_muted.set()
        try:
            if audio is None:
                log("Didn't hear anything.")
                tone(500, 120)
                return
            ask_audio(audio)
        except Exception as e:
            log("Error:", e)
            say("Sorry, I couldn't reach the Hey Me server.")
        finally:
            clear_mic()
            mic_muted.clear()


def listen_forever():
    global noise_level
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    log("Loading the wake word listener (the first time downloads a small model)...")
    model = Model(lang="en-us")
    grammar = json.dumps([WAKE_WORD, "[unk]"])
    rec = KaldiRecognizer(model, RATE, grammar)

    with sd.RawInputStream(samplerate=RATE, blocksize=CHUNK, dtype="int16",
                           channels=1, device=MIC, callback=mic_callback):
        log(f'Ready in "{NAME}". Say "{WAKE_WORD.title()}".')
        while True:
            data = audio_q.get()
            noise_level = 0.97 * noise_level + 0.03 * rms(np.frombuffer(data, dtype=np.int16))
            if rec.AcceptWaveform(data):
                heard = json.loads(rec.Result()).get("text", "")
            else:
                heard = json.loads(rec.PartialResult()).get("partial", "")
            if WAKE_WORD in heard:
                rec.Reset()
                conversation()
                rec.Reset()


def main():
    global SERVER, SECRET, NAME, MIC, SPEAKER
    ap = argparse.ArgumentParser(description="Hey Me listener")
    ap.add_argument("--server")
    ap.add_argument("--secret")
    ap.add_argument("--name")
    ap.add_argument("--mic", type=int)
    ap.add_argument("--speaker", type=int)
    ap.add_argument("--say", help="send typed text instead of listening (for testing)")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()
    SERVER = (a.server or SERVER).rstrip("/")
    SECRET = a.secret or SECRET
    NAME = a.name or NAME
    MIC = a.mic if a.mic is not None else MIC
    SPEAKER = a.speaker if a.speaker is not None else SPEAKER

    if a.list_devices:
        print(sd.query_devices())
        return
    if a.say:
        ask_text(a.say)
        return

    threading.Thread(target=poll_events, daemon=True).start()
    while True:
        try:
            listen_forever()
        except KeyboardInterrupt:
            return
        except Exception as e:
            log("Microphone problem:", e, "- trying again in 10 seconds.")
            time.sleep(10)


if __name__ == "__main__":
    main()
