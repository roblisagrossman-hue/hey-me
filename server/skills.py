"""
Skills = the things Hey Me can DO (besides talking).

Each skill has:
  - a description Claude reads (in build_tools)
  - a Python function that does the work (in run_tool)

To add your own skill: add a description in build_tools(), a function below,
and a line in run_tool().
"""

import os
import re
import json
import time
import uuid
import random
import socket
import hashlib
import datetime
import tempfile
import threading
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests

import settings
import speech
import mediacenter

HERE = Path(__file__).parent


# =====================================================================
# Devices: every phone / Shield / Pi / PC listener that checks in
# =====================================================================
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}          # "kitchen" -> {"name", "kind", "last_seen"}
        self.queues = {}           # "kitchen" -> [events waiting for that device]
        self.alexa_ids = {}        # Alexa device id -> last seen time

    def seen(self, name, kind):
        with self.lock:
            self.devices[name.lower()] = {"name": name, "kind": kind, "last_seen": time.time()}

    def online(self):
        now = time.time()
        with self.lock:
            return [d for d in self.devices.values() if now - d["last_seen"] < 90]

    def push(self, name, event):
        with self.lock:
            q = self.queues.setdefault(name.lower(), [])
            q.append(event)
            del q[:-20]

    def pop(self, name):
        with self.lock:
            return self.queues.pop(name.lower(), [])


hub = Hub()


def _norm(name):
    name = (name or "").lower().strip()
    name = re.sub(r"^(the|my)\s+", "", name)
    return name.replace("'s", "").strip()


def _match(name, choices):
    """Find `name` in a list of names, allowing small differences."""
    n = _norm(name)
    if not n:
        return None
    by_norm = {_norm(c): c for c in choices}
    if n in by_norm:
        return by_norm[n]
    for k, c in by_norm.items():
        if n in k or k in n:
            return c
    return None


def public_base():
    if settings.PUBLIC_URL:
        return settings.PUBLIC_URL.rstrip("/")
    return f"http://{lan_ip()}:{settings.PORT}"


def lan_ip():
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        home = sorted(a for a in addrs if a.startswith("192.168."))
        if home:
            return home[0]
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def speech_url(text):
    name = speech.make_speech(text)
    return f"/speech/{name}?t={quote(settings.SECRET)}" if name else ""


# =====================================================================
# Sending things to devices (announcements, alarms, media)
# =====================================================================
def deliver(target, text, alarm=False, ctx=None):
    """Say `text` on a device, an Echo, or everywhere. Returns what happened."""
    target = target or ""
    if ctx and _norm(target) in ("", "here", "this room", "this device", "me"):
        target = ctx["device"]

    if _norm(target) in ("everywhere", "all", "all devices", "whole house", "house"):
        done = []
        audio = speech_url(text)
        for d in hub.online():
            hub.push(d["name"], {"type": "announce", "text": text, "alarm": alarm, "audio_url": audio})
            done.append(d["name"])
        for echo in settings.ECHOS:
            echo_announce(text, echo)
            done.append(echo)
        return f"Announced on: {', '.join(done) or 'no devices are online'}."

    echo = _match(target, settings.ECHOS)
    if echo:
        return echo_announce(text, echo)

    known = [d["name"] for d in hub.online()] + list(hub.devices)
    dev = _match(target, known)
    if dev:
        hub.push(dev, {"type": "announce", "text": text, "alarm": alarm, "audio_url": speech_url(text)})
        return f"Sent to {dev}."
    return f"I don't know a device called '{target}'. Online devices: {', '.join(known) or 'none'}."


# =====================================================================
# Timers and reminders
# =====================================================================
TIMERS_FILE = HERE / "timers.json"


class Timers:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        try:
            self.items = json.loads(TIMERS_FILE.read_text())
        except Exception:
            self.items = []

    def save(self):
        try:
            TIMERS_FILE.write_text(json.dumps(self.items, indent=1))
        except OSError:
            pass

    def add(self, due, label, target, kind):
        with self.lock:
            self.items.append({"id": uuid.uuid4().hex[:8], "due": due, "label": label,
                               "target": target, "kind": kind})
            self.save()

    def cancel(self, label):
        with self.lock:
            if _norm(label) in ("", "all", "everything"):
                gone, self.items = self.items, []
            else:
                gone = [t for t in self.items if _norm(label) in _norm(t["label"])]
                self.items = [t for t in self.items if t not in gone]
            self.save()
            return len(gone)

    def describe(self):
        with self.lock:
            items = sorted(self.items, key=lambda t: t["due"])
        out = []
        for t in items:
            left = int(t["due"] - time.time())
            when = datetime.datetime.fromtimestamp(t["due"]).strftime("%I:%M %p on %a %b %d")
            out.append(f"{t['kind']} '{t['label']}' for {t['target']}: {max(left, 0)} seconds left (at {when})")
        return out

    def loop(self):
        while True:
            time.sleep(1)
            now = time.time()
            with self.lock:
                due = [t for t in self.items if t["due"] <= now]
                self.items = [t for t in self.items if t["due"] > now]
                if due:
                    self.save()
            for t in due:
                if now - t["due"] > 3600:          # missed while the server was off
                    continue
                if t["kind"] == "timer":
                    text = "Your timer is done." if t["label"] in ("", "timer") \
                        else f"Your {t['label']} timer is done."
                else:
                    text = f"Reminder: {t['label']}"
                try:
                    print("  ⏰", deliver(t["target"], text, alarm=True))
                except Exception as e:
                    print("Timer error:", e)


timers = Timers()


# =====================================================================
# Media library (your music, movies and shows)
# =====================================================================
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wav", ".wma", ".opus"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".ts", ".mpg"}
SKIP_WORDS = {"play", "some", "song", "songs", "music", "movie", "film", "video", "a", "an",
              "by", "me", "please", "of", "track", "album", "episode", "show", "the"}
MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".wav": "audio/wav", ".wma": "audio/x-ms-wma", ".opus": "audio/ogg",
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".wmv": "video/x-ms-wmv", ".m4v": "video/mp4",
        ".webm": "video/webm", ".ts": "video/mp2t", ".mpg": "video/mpeg"}


class MediaLibrary:
    def __init__(self):
        self.lock = threading.Lock()
        self.files = []        # (id, path, kind, searchable text)
        self.by_id = {}
        self.playlists = {}    # id -> [file ids]
        self.last_scan = 0

    def scan(self):
        files, by_id = [], {}
        for folder in settings.MEDIA_FOLDERS:
            if not os.path.isdir(folder):
                continue
            for root, _dirs, names in os.walk(folder):
                for n in names:
                    ext = os.path.splitext(n)[1].lower()
                    kind = "audio" if ext in AUDIO_EXT else "video" if ext in VIDEO_EXT else None
                    if not kind:
                        continue
                    path = os.path.join(root, n)
                    rel = os.path.relpath(path, folder)
                    text = re.sub(r"[_.\-\[\]()]+", " ", rel.lower())
                    fid = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:16]
                    files.append((fid, path, kind, text))
                    by_id[fid] = path
        with self.lock:
            self.files, self.by_id = files, by_id
            self.last_scan = time.time()
        print(f"  Media library: {len(files)} files")
        return len(files)

    def loop(self):
        while True:
            try:
                self.scan()
            except Exception as e:
                print("Media scan error:", e)
            time.sleep(1800)

    def search(self, query, kind=None):
        words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in SKIP_WORDS]
        with self.lock:
            files = [f for f in self.files if not kind or f[2] == kind]
        if not words:
            return files
        exact = [f for f in files if all(w in f[3] for w in words)]
        if exact:
            return sorted(exact, key=lambda f: f[3])
        scored = [(sum(w in f[3] for w in words), f) for f in files]
        best = max((s for s, _ in scored), default=0)
        if best == 0 or best < len(words) / 2:
            return []
        return sorted((f for s, f in scored if s == best), key=lambda f: f[3])

    def add_path(self, path):
        fid = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:16]
        with self.lock:
            self.by_id[fid] = path
        return fid

    def file_link(self, fid):
        name = os.path.basename(self.by_id.get(fid, "file"))
        return f"/media/{fid}/{quote(name)}?t={quote(settings.SECRET)}"

    def make_playlist(self, fids):
        pid = uuid.uuid4().hex[:12]
        with self.lock:
            self.playlists[pid] = fids
            if len(self.playlists) > 50:
                self.playlists.pop(next(iter(self.playlists)))
        return f"/playlist/{pid}.m3u?t={quote(settings.SECRET)}"

    def playlist_text(self, pid):
        fids = self.playlists.get(pid)
        if fids is None:
            return None
        base = public_base()
        lines = ["#EXTM3U"]
        for fid in fids:
            name = os.path.splitext(os.path.basename(self.by_id.get(fid, "")))[0]
            lines += [f"#EXTINF:-1,{name}", base + self.file_link(fid)]
        return "\n".join(lines) + "\n"


media = MediaLibrary()


def _title(path):
    return os.path.splitext(os.path.basename(path))[0]


# =====================================================================
# Skills
# =====================================================================
def skill_weather(days=1):
    days = max(1, min(int(days or 1), 7))
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": settings.CITY, "count": 1}, timeout=10).json()
    place = geo["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "auto", "forecast_days": days}, timeout=10).json()
    return json.dumps({"place": place.get("name"), "current": w["current"], "daily": w["daily"],
                       "note": "weather_code uses WMO codes"})


def skill_set_timer(args, ctx):
    seconds = int(args["seconds"])
    target = args.get("ring_on") or ctx["device"]
    if ctx["kind"] == "mediacenter" and not args.get("ring_on"):
        options = [d["name"] for d in hub.online()] + list(settings.ECHOS)
        return ("Ask the user which room should ring (MEdiaCenter screens can't ring timers). "
                f"Options: {', '.join(options) or 'no Hey Me speakers are online yet'}, or everywhere.")
    if ctx["kind"] == "alexa" and not _match(target, settings.ECHOS) and not args.get("ring_on"):
        return ("This Echo isn't linked to Hey Me yet, so Hey Me can't ring on it. Tell the user "
                "to say 'Alexa, set a timer' directly, or pick another device to ring on.")
    label = args.get("label") or "timer"
    timers.add(time.time() + seconds, label, target, "timer")
    return f"Timer '{label}' set for {seconds} seconds, it will ring on {target}."


def skill_set_reminder(args, ctx):
    when = datetime.datetime.strptime(args["when"], "%Y-%m-%d %H:%M")
    target = args.get("ring_on") or ctx["device"]
    timers.add(when.timestamp(), args["text"], target, "reminder")
    return f"Reminder set for {when.strftime('%I:%M %p on %A %B %d')} on {target}."


def skill_open_pc_app(app):
    exe = PC_APP_MATCH(app)
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", exe or app], shell=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.Popen([exe or app])
        return f"Opened {app}."
    except Exception as e:
        return f"Couldn't open {app}: {e}. Known apps: {', '.join(settings.PC_APPS)}"


def PC_APP_MATCH(app):
    key = _match(app, settings.PC_APPS)
    return settings.PC_APPS[key] if key else None


def skill_pc_media(action, steps=5):
    import pyautogui
    keys = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
            "stop": "stop", "volume_up": "volumeup", "volume_down": "volumedown",
            "mute": "volumemute"}
    key = keys.get(action)
    if not key:
        return "Unknown action."
    presses = max(1, min(int(steps or 5), 50)) if action.startswith("volume") else 1
    pyautogui.press(key, presses=presses)
    return f"Done: {action} on the PC."


def adb(ip, *args):
    target = ip if ":" in ip else f"{ip}:5555"
    subprocess.run([settings.ADB_PATH, "connect", target], capture_output=True, timeout=10)
    r = subprocess.run([settings.ADB_PATH, "-s", target, *args],
                       capture_output=True, text=True, timeout=30)
    return (r.stdout + r.stderr).strip()


SHIELD_KEYS = {
    "wake": "KEYCODE_WAKEUP", "sleep": "KEYCODE_SLEEP", "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK", "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "stop": "KEYCODE_MEDIA_STOP", "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS", "rewind": "KEYCODE_MEDIA_REWIND",
    "fast_forward": "KEYCODE_MEDIA_FAST_FORWARD", "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_VOLUME_MUTE",
    "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN", "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT", "select": "KEYCODE_DPAD_CENTER",
}


def find_shield(name):
    key = _match(name, settings.SHIELDS)
    return (key, settings.SHIELDS[key]) if key else (None, None)


def skill_shield(args):
    name, ip = find_shield(args.get("shield", ""))
    if not ip:
        return f"Unknown Shield '{args.get('shield')}'. Shields: {', '.join(settings.SHIELDS)}"
    action = args.get("action")
    try:
        if action == "open_app":
            app_key = _match(args.get("app", ""), settings.SHIELD_APPS)
            if not app_key:
                return f"Unknown Shield app. Known apps: {', '.join(settings.SHIELD_APPS)}"
            adb(ip, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
            out = adb(ip, "shell", "monkey", "-p", settings.SHIELD_APPS[app_key],
                      "-c", "android.intent.category.LEANBACK_LAUNCHER", "1")
            if "No activities found" in out:
                out = adb(ip, "shell", "monkey", "-p", settings.SHIELD_APPS[app_key],
                          "-c", "android.intent.category.LAUNCHER", "1")
        elif action == "search_youtube":
            q = quote(args.get("text", ""))
            adb(ip, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
            out = adb(ip, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                      "-d", f"https://www.youtube.com/results?search_query={q}",
                      "com.google.android.youtube.tv")
        elif action == "type_text":
            text = (args.get("text", "") or "").replace(" ", "%s")
            out = adb(ip, "shell", "input", "text", text)
        elif action in SHIELD_KEYS:
            repeat = max(1, min(int(args.get("repeat") or 1), 20))
            out = ""
            for _ in range(repeat):
                out = adb(ip, "shell", "input", "keyevent", SHIELD_KEYS[action])
        else:
            return "Unknown Shield action."
    except FileNotFoundError:
        return "adb isn't installed on the server PC yet (see the setup guide)."
    except subprocess.TimeoutExpired:
        return f"The {name} Shield didn't answer. Is it turned on?"
    if "unauthorized" in out or "failed to connect" in out or "cannot connect" in out:
        return f"Couldn't control the {name} Shield: {out[:150]}"
    return f"Done: {action} on the {name} Shield."


def shield_play(ip, url, mime):
    adb(ip, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    return adb(ip, "shell", "am", "start", "-a", "android.intent.action.VIEW",
               "-d", url, "-t", mime)


def _find_media(query, kind):
    """-> [(file id, path, title, MEdiaCenter media id or None)]"""
    if mediacenter.enabled():
        items = mediacenter.search(query, {"audio": "music", "video": "video"}.get(kind, "any"))
        if items:
            return [(media.add_path(i["path"]), i["path"], i["label"], i["id"]) for i in items]
    return [(f[0], f[1], _title(f[1]), None) for f in media.search(query, kind)]


def skill_play_media(args, ctx):
    query = args.get("query", "")
    kind = {"music": "audio", "audio": "audio", "video": "video"}.get(args.get("kind", "any"))
    found = _find_media(query, kind)
    if not found:
        return f"Nothing in the library matches '{query}'."
    if args.get("shuffle") or kind == "audio" and len(found) > 3:
        random.shuffle(found)
    found = found[:300]
    titles = ", ".join(f[2] for f in found[:3])
    more = f" and {len(found) - 3} more" if len(found) > 3 else ""

    if len(found) == 1:
        link = media.file_link(found[0][0])
        mime = MIME.get(os.path.splitext(found[0][1])[1].lower(), "video/*")
    else:
        link = media.make_playlist([f[0] for f in found])
        mime = "audio/x-mpegurl"

    where = args.get("where") or "here"
    if _norm(where) in ("here", "this room", "this device", ""):
        where = ctx["device"]
    if ctx["kind"] == "mediacenter" and _norm(where) == _norm(ctx["device"]):
        return ("Ask the user which screen or room to play it on. MEdiaCenter screens: "
                f"{', '.join(mediacenter.screens()) or 'none online'}; Shields: {', '.join(settings.SHIELDS)}.")

    # 0) a screen that is showing MEdiaCenter: MEdiaCenter plays it itself
    screen = _match(where, mediacenter.screens()) if mediacenter.enabled() else None
    if screen and found[0][3]:
        r = mediacenter.screen_cmd(screen, "play", media_id=found[0][3])
        if r.get("ok"):
            return f"Playing {r.get('title') or found[0][2]} on {r.get('screen') or screen}."
        return f"Couldn't play on {screen}: {r.get('error', 'no answer')}"

    # 1) this server PC
    if _norm(where) in ("pc", "computer", "server", "this pc", "the pc") or \
            (settings.PC_MIC_ENABLED and _norm(where) == _norm(settings.PC_MIC_NAME)):
        if len(found) == 1:
            os.startfile(found[0][1]) if os.name == "nt" else None
        else:
            m3u = Path(tempfile.gettempdir()) / "heyme_playlist.m3u"
            m3u.write_text("#EXTM3U\n" + "\n".join(f[1] for f in found), encoding="utf-8")
            os.startfile(str(m3u)) if os.name == "nt" else None
        return f"Playing on the PC: {titles}{more}."

    # 2) a Shield
    name, ip = find_shield(where)
    if ip:
        try:
            shield_play(ip, public_base() + link, mime)
        except Exception as e:
            return f"Couldn't reach the {name} Shield: {e}"
        return f"Playing on the {name} Shield: {titles}{more}."

    # 3) an Echo
    if _match(where, settings.ECHOS) or (ctx["kind"] == "alexa" and where == ctx["device"]):
        return ("Echos can't play files from the library. Ask the user which TV, PC, "
                "phone or speaker to play it on instead.")

    # 4) the device that asked, or another Hey Me device
    if _norm(where) == _norm(ctx["device"]):
        ctx["media"] = {"url": link, "type": mime}
        return f"Playing here: {titles}{more}."
    known = [d["name"] for d in hub.online()]
    dev = _match(where, known)
    if dev:
        hub.push(dev, {"type": "media", "url": link, "media_type": mime})
        return f"Playing on {dev}: {titles}{more}."
    return f"I don't know where '{where}' is. Devices: PC, {', '.join(list(settings.SHIELDS) + known)}"


def skill_stop_media(args, ctx):
    where = args.get("where") or ctx["device"]
    if _norm(where) in ("pc", "computer", "server"):
        return skill_pc_media("stop")
    screen = _match(where, mediacenter.screens()) if mediacenter.enabled() else None
    if screen:
        r = mediacenter.screen_cmd(screen, args.get("action") or "stop")
        return f"Done on {r.get('screen') or screen}." if r.get("ok") else f"Couldn't: {r.get('error')}"
    name, ip = find_shield(where)
    if ip:
        return skill_shield({"shield": name, "action": "stop"})
    if _norm(where) in ("here", "this room"):
        where = ctx["device"]
    if _norm(where) == _norm(ctx["device"]) and ctx["kind"] != "alexa":
        ctx["media_stop"] = True
        return "Stopped."
    dev = _match(where, [d["name"] for d in hub.online()])
    if dev:
        hub.push(dev, {"type": "media_stop"})
        return f"Stopped on {dev}."
    return f"Couldn't find '{where}'."


def skill_search_media(query, kind):
    kind = {"music": "audio", "audio": "audio", "video": "video"}.get(kind)
    found = media.search(query, kind)
    if not found:
        return "No matches."
    return f"{len(found)} matches. First ones: " + "; ".join(_title(f[1]) for f in found[:15])


def echo_announce(text, echo):
    if not settings.VOICE_MONKEY_TOKEN:
        return "Echo announcements aren't set up yet (Voice Monkey token missing in settings.py)."
    key = _match(echo, settings.ECHOS)
    if not key:
        return f"Unknown Echo '{echo}'. Echos: {', '.join(settings.ECHOS) or 'none set up'}"
    r = requests.post("https://api-v3.voicemonkey.io/announce", timeout=15, json={
        "token": settings.VOICE_MONKEY_TOKEN, "device": settings.ECHOS[key], "speech": text})
    return f"Announced on the {key}." if r.ok else f"Voice Monkey error: {r.text[:150]}"


def echo_routine(routine):
    if not settings.VOICE_MONKEY_TOKEN:
        return "Alexa routines aren't set up yet (Voice Monkey token missing in settings.py)."
    key = _match(routine, settings.ECHO_ROUTINES)
    if not key:
        return f"Unknown routine '{routine}'. Routines: {', '.join(settings.ECHO_ROUTINES) or 'none set up'}"
    r = requests.post("https://api-v3.voicemonkey.io/trigger", timeout=15, json={
        "token": settings.VOICE_MONKEY_TOKEN, "device": settings.ECHO_ROUTINES[key]})
    return f"Started the Alexa routine '{key}'." if r.ok else f"Voice Monkey error: {r.text[:150]}"


# =====================================================================
# What Claude sees
# =====================================================================
def build_tools():
    devices = [d["name"] for d in hub.online()]
    shields = list(settings.SHIELDS)
    echos = list(settings.ECHOS)
    mc_screens = mediacenter.screens() if mediacenter.enabled() else []
    places = ["here", "pc"] + mc_screens + shields + devices
    return [
        {"name": "set_timer",
         "description": "Start a countdown timer. It rings and speaks when done.",
         "input_schema": {"type": "object", "properties": {
             "seconds": {"type": "integer"},
             "label": {"type": "string", "description": "e.g. 'pizza'. Optional."},
             "ring_on": {"type": "string", "description": f"Where it rings. Default: the device that asked. Options: {', '.join(devices + echos) or 'none'}, or 'everywhere'"}},
             "required": ["seconds"]}},
        {"name": "set_reminder",
         "description": "Set a reminder for a specific date and time.",
         "input_schema": {"type": "object", "properties": {
             "when": {"type": "string", "description": "Local time, format 'YYYY-MM-DD HH:MM' (24 hour)"},
             "text": {"type": "string", "description": "What to remind about"},
             "ring_on": {"type": "string"}},
             "required": ["when", "text"]}},
        {"name": "list_timers", "description": "List timers and reminders that are running.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "cancel_timer", "description": "Cancel timers/reminders by label, or 'all'.",
         "input_schema": {"type": "object", "properties": {"label": {"type": "string"}},
                          "required": ["label"]}},
        {"name": "weather", "description": f"Weather in {settings.CITY}: current conditions and forecast.",
         "input_schema": {"type": "object", "properties": {
             "days": {"type": "integer", "description": "Days of forecast, 1-7"}}}},
        {"name": "announce",
         "description": "Speak a message out loud on another device (intercom), or 'everywhere'.",
         "input_schema": {"type": "object", "properties": {
             "text": {"type": "string"},
             "where": {"type": "string", "description": f"Options: {', '.join(devices + echos) or 'none'}, everywhere"}},
             "required": ["text", "where"]}},
        {"name": "open_pc_app",
         "description": f"Open a program on the server PC. Known: {', '.join(settings.PC_APPS)}",
         "input_schema": {"type": "object", "properties": {"app": {"type": "string"}},
                          "required": ["app"]}},
        {"name": "pc_media",
         "description": "Control music/video playback and volume on the server PC.",
         "input_schema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop",
                                                   "volume_up", "volume_down", "mute"]},
             "steps": {"type": "integer", "description": "For volume: how many 2% steps (default 5)"}},
             "required": ["action"]}},
        {"name": "shield",
         "description": f"Control an NVIDIA Shield TV. Shields: {', '.join(shields)}. "
                        f"Apps: {', '.join(settings.SHIELD_APPS)}.",
         "input_schema": {"type": "object", "properties": {
             "shield": {"type": "string"},
             "action": {"type": "string", "enum": list(SHIELD_KEYS) + ["open_app", "search_youtube", "type_text"]},
             "app": {"type": "string", "description": "For open_app"},
             "text": {"type": "string", "description": "For search_youtube or type_text"},
             "repeat": {"type": "integer", "description": "Press the button this many times"}},
             "required": ["shield", "action"]}},
        {"name": "play_media",
         "description": "Play music, movies or shows from the user's own media library.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Artist, song, album, movie or show name. Empty = everything."},
             "kind": {"type": "string", "enum": ["music", "video", "any"]},
             "where": {"type": "string", "description": f"Options: {', '.join(places)}"},
             "shuffle": {"type": "boolean"}},
             "required": ["query"]}},
        {"name": "stop_media", "description": "Stop, pause or resume media on a device or MEdiaCenter screen.",
         "input_schema": {"type": "object", "properties": {
             "where": {"type": "string", "description": f"Options: {', '.join(places)}"},
             "action": {"type": "string", "enum": ["stop", "pause", "resume"]}}}},
        {"name": "search_media", "description": "Look up what's in the media library without playing.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"},
             "kind": {"type": "string", "enum": ["music", "video", "any"]}},
             "required": ["query"]}},
        {"name": "mediacenter",
         "description": "The user's MEdiaCenter server. 'ask' passes a question to its assistant ME, which knows "
                        "what's on live TV now, DVR recordings, what's newly added, how many movies/shows/songs, "
                        "who is online, and the morning briefing. 'announce' posts a family announcement in MEdiaCenter.",
         "input_schema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["ask", "announce"]},
             "text": {"type": "string", "description": "The question for ME, or the announcement"}},
             "required": ["action", "text"]}},
        {"name": "alexa_routine",
         "description": f"Start an Alexa routine (smart lights, plugs, etc). Routines: {', '.join(settings.ECHO_ROUTINES) or 'none set up yet'}",
         "input_schema": {"type": "object", "properties": {"routine": {"type": "string"}},
                          "required": ["routine"]}},
    ]


def run_tool(name, args, ctx):
    a = args or {}
    if name == "set_timer":
        return skill_set_timer(a, ctx)
    if name == "set_reminder":
        return skill_set_reminder(a, ctx)
    if name == "list_timers":
        return "; ".join(timers.describe()) or "No timers running."
    if name == "cancel_timer":
        return f"Cancelled {timers.cancel(a.get('label', 'all'))} timer(s)."
    if name == "weather":
        return skill_weather(a.get("days", 1))
    if name == "announce":
        return deliver(a.get("where", ""), a.get("text", ""), ctx=ctx)
    if name == "open_pc_app":
        return skill_open_pc_app(a.get("app", ""))
    if name == "pc_media":
        return skill_pc_media(a.get("action"), a.get("steps", 5))
    if name == "shield":
        return skill_shield(a)
    if name == "play_media":
        return skill_play_media(a, ctx)
    if name == "stop_media":
        return skill_stop_media(a, ctx)
    if name == "search_media":
        return skill_search_media(a.get("query", ""), a.get("kind", "any"))
    if name == "mediacenter":
        if not mediacenter.enabled():
            return "MEdiaCenter isn't connected (MEDIACENTER_URL is empty in settings.py)."
        if a.get("action") == "announce":
            return mediacenter.announce(a.get("text", ""))
        return mediacenter.ask(a.get("text", ""))
    if name == "alexa_routine":
        return echo_routine(a.get("routine", ""))
    return f"Unknown skill {name}"


def start_background_jobs():
    threading.Thread(target=timers.loop, daemon=True).start()
    threading.Thread(target=media.loop, daemon=True).start()
