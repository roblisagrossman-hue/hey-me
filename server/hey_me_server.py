"""
HEY ME SERVER - your own Alexa, running on your home PC 24/7.

Start it with "Start Hey Me.bat" (or: python hey_me_server.py)
Then open the status page it prints, e.g. http://192.168.1.10:8765/?t=YOUR_SECRET

Every Hey Me device (PC listener, Raspberry Pi speakers, phones, Shields, Echos)
sends what you said here. This server:
   1. turns your voice into text (Whisper)
   2. asks Claude what to do (brain.py), which can use skills (skills.py)
   3. sends back the answer as text and speech
"""

import os
import sys
import hmac
import json
import time
import subprocess
from pathlib import Path
from urllib.parse import quote

from flask import Flask, request, jsonify, send_file, abort, Response

import settings
import speech
import skills
import brain
import alexa

HERE = Path(__file__).parent
APPS_DIR = HERE / "apps"
APPS_DIR.mkdir(exist_ok=True)
APK = APPS_DIR / "HeyMe.apk"

app = Flask(__name__)


# ---------------------------------------------------------------------
# Security: every request needs the secret (except Amazon's, checked in alexa.py)
# ---------------------------------------------------------------------
def authorized():
    given = request.headers.get("X-HeyMe-Secret") or request.args.get("t") or ""
    return hmac.compare_digest(given.encode(), settings.SECRET.encode())


def require_secret():
    if not authorized():
        abort(403)


def device_info():
    name = (request.args.get("device") or "unknown")[:40].strip() or "unknown"
    kind = (request.args.get("kind") or "satellite")[:20]
    return name, kind


# ---------------------------------------------------------------------
# Talking to Hey Me
# ---------------------------------------------------------------------
def answer(text, ctx):
    print(f"\n[{ctx['device']}] YOU: {text}")
    reply = brain.think(text, ctx)
    print(f"[{ctx['device']}] HEY ME: {reply}")
    out = {"heard": text, "reply": reply, "audio_url": "",
           "media_url": "", "media_type": "", "media_stop": bool(ctx.get("media_stop"))}
    if ctx["kind"] == "satellite":
        out["audio_url"] = skills.speech_url(reply)
    if ctx.get("media"):
        out["media_url"] = ctx["media"]["url"]
        out["media_type"] = ctx["media"]["type"]
    return out


@app.post("/api/ask")
def api_ask():
    """A device sends a WAV recording of what you said."""
    require_secret()
    name, kind = device_info()
    skills.hub.seen(name, kind)
    try:
        text = speech.transcribe(request.get_data())
    except Exception as e:
        return jsonify({"error": f"Couldn't read the audio: {e}"}), 400
    if not text:
        reply = "Sorry, I didn't hear anything."
        return jsonify({"heard": "", "reply": reply, "media_url": "", "media_type": "",
                        "media_stop": False,
                        "audio_url": skills.speech_url(reply) if kind == "satellite" else ""})
    try:
        return jsonify(answer(text, {"device": name, "kind": kind}))
    except Exception as e:
        print("Error:", e)
        reply = "Sorry, something went wrong. " + _friendly_error(e)
        return jsonify({"heard": text, "reply": reply, "media_url": "", "media_type": "",
                        "media_stop": False,
                        "audio_url": skills.speech_url(reply) if kind == "satellite" else ""})


@app.post("/api/ask_text")
def api_ask_text():
    """Same as /api/ask but with typed text (used by the status page)."""
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("device") or "status page")[:40]
    kind = (data.get("kind") or "web")[:20]
    skills.hub.seen(name, kind) if kind not in ("web", "mediacenter") else None
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "no text"}), 400
    try:
        return jsonify(answer(text, {"device": name, "kind": kind}))
    except Exception as e:
        print("Error:", e)
        return jsonify({"heard": text, "reply": "Sorry, something went wrong. " + _friendly_error(e),
                        "audio_url": "", "media_url": "", "media_type": "", "media_stop": False})


def _friendly_error(e):
    s = str(e).lower()
    if "api_key" in s or "authentication" in s or "x-api-key" in s:
        return "The Claude API key in settings dot p y isn't working."
    if "connection" in s:
        return "The server couldn't reach the internet."
    return ""


@app.get("/api/events")
def api_events():
    """Devices check here every few seconds for timers, announcements and media."""
    require_secret()
    name, kind = device_info()
    skills.hub.seen(name, kind)
    return jsonify({"events": skills.hub.pop(name)})


# ---------------------------------------------------------------------
# Files: speech, media, playlists, the Android app
# ---------------------------------------------------------------------
@app.get("/speech/<name>")
def get_speech(name):
    require_secret()
    path = speech.SPEECH_DIR / os.path.basename(name)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="audio/wav")


@app.get("/media/<fid>/<path:_name>")
def get_media(fid, _name):
    require_secret()
    path = skills.media.by_id.get(fid)
    if not path or not os.path.exists(path):
        abort(404)
    mime = skills.MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return send_file(path, mimetype=mime, conditional=True)


@app.get("/playlist/<pid>.m3u")
def get_playlist(pid):
    require_secret()
    text = skills.media.playlist_text(pid)
    if text is None:
        abort(404)
    return Response(text, mimetype="audio/x-mpegurl")


@app.get("/apps/<name>")
def get_app(name):
    require_secret()
    path = APPS_DIR / os.path.basename(name)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/vnd.android.package-archive",
                     as_attachment=True, download_name=path.name)


# ---------------------------------------------------------------------
# Amazon Echo -> Hey Me
# ---------------------------------------------------------------------
@app.post("/alexa")
def alexa_endpoint():
    body = request.get_data()
    try:
        data = json.loads(body)
        alexa.verify(request.headers, body, data)
    except Exception as e:
        print("Alexa request refused:", e)
        abort(400)
    return jsonify(alexa.handle(data))


# ---------------------------------------------------------------------
# Status page + admin buttons
# ---------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    require_secret()
    return jsonify({
        "devices": [{"name": d["name"], "kind": d["kind"],
                     "seconds_ago": int(time.time() - d["last_seen"])} for d in skills.hub.online()],
        "timers": skills.timers.describe(),
        "media_files": len(skills.media.files),
        "alexa_devices": skills.hub.alexa_ids,
        "shields": list(settings.SHIELDS),
        "echos": list(settings.ECHOS),
        "apk": APK.exists(),
        "server": skills.public_base(),
    })


@app.post("/api/rescan")
def api_rescan():
    require_secret()
    return jsonify({"files": skills.media.scan()})


@app.post("/api/install_shields")
def api_install_shields():
    """Install (or update) the Hey Me app on every Shield and set it up automatically."""
    require_secret()
    if not APK.exists():
        return jsonify({"result": "Put HeyMe.apk in the server's apps folder first."})
    results = []
    for name, ip in settings.SHIELDS.items():
        try:
            out = skills.adb(ip, "install", "-r", str(APK))
            if "Success" not in out:
                results.append(f"{name}: {out[-150:]}")
                continue
            for cmd in (["pm", "grant", "com.heyme.app", "android.permission.RECORD_AUDIO"],
                        ["pm", "grant", "com.heyme.app", "android.permission.POST_NOTIFICATIONS"],
                        ["appops", "set", "com.heyme.app", "SYSTEM_ALERT_WINDOW", "allow"]):
                skills.adb(ip, "shell", *cmd)
            skills.adb(ip, "shell", "am", "start", "-n", "com.heyme.app/.MainActivity",
                       "--es", "server", skills.public_base(), "--es", "secret", settings.SECRET,
                       "--es", "name", name)
            results.append(f"{name}: installed and set up")
        except FileNotFoundError:
            return jsonify({"result": "adb isn't installed yet (see the setup guide)."})
        except Exception as e:
            results.append(f"{name}: {e}")
    return jsonify({"result": "\n".join(results) or "No Shields in settings.py"})


@app.get("/")
def status_page():
    if not authorized():
        return Response(LOGIN_PAGE, mimetype="text/html")
    return Response(STATUS_PAGE.replace("__SECRET__", json.dumps(settings.SECRET)),
                    mimetype="text/html")


LOGIN_PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Hey Me</title><body style="font-family:system-ui;background:#111;color:#eee;padding:24px">
<h1>Hey Me</h1><form><p>Password (the SECRET in settings.py):</p>
<input name=t type=password style="font-size:18px;padding:8px"> <button style="font-size:18px">Open</button></form>"""

STATUS_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name=viewport content="width=device-width,initial-scale=1"><title>Hey Me</title>
<style>
 :root{--bg:#0f1115;--card:#1a1d24;--fg:#e8e8ea;--dim:#9aa0aa;--acc:#4fb3ff}
 body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif}
 main{max-width:760px;margin:0 auto;padding:16px}
 h1{margin:8px 0 16px} h2{font-size:15px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin:0 0 10px}
 .card{background:var(--card);border-radius:12px;padding:16px;margin-bottom:14px}
 input,button{font:inherit;border-radius:8px;border:1px solid #333;padding:10px 12px}
 input{background:#0b0d11;color:var(--fg);width:100%;box-sizing:border-box}
 button{background:var(--acc);color:#001;border:0;font-weight:600;cursor:pointer;margin:4px 6px 4px 0}
 .row{display:flex;gap:8px}.row input{flex:1}
 ul{margin:0;padding-left:18px} li{margin:4px 0} .dim{color:var(--dim)} pre{white-space:pre-wrap;margin:8px 0 0}
 a{color:var(--acc)}
</style></head><body><main>
<h1>Hey Me</h1>
<div class=card><h2>Ask Hey Me</h2>
 <div class=row><input id=q placeholder="Type a question or command…"><button onclick="ask()">Ask</button></div>
 <pre id=ans class=dim></pre></div>
<div class=card><h2>Devices online</h2><ul id=devices></ul></div>
<div class=card><h2>Timers &amp; reminders</h2><ul id=timers></ul></div>
<div class=card><h2>Phones &amp; Shields app</h2>
 <p id=apk class=dim></p>
 <p class=dim>On your phone, open this page, install the app, then type a name for the phone and tap "Set up this phone".</p>
 <div class=row><input id=pname placeholder="Name for this phone, e.g. rob phone"><button onclick="setupPhone()">Set up this phone</button></div>
 <p><button onclick="post('/api/install_shields')">Install / update on all Shields</button></p>
 <pre id=adm class=dim></pre></div>
<div class=card><h2>Media library</h2><p id=media class=dim></p><button onclick="post('/api/rescan')">Rescan folders</button></div>
<div class=card><h2>Echo device IDs</h2><p class=dim>Talk to each Echo through the Hey Me skill once, then copy its ID into ALEXA_DEVICE_NAMES in settings.py.</p><ul id=alexa></ul></div>
<script>
const T = __SECRET__; const H = {"X-HeyMe-Secret": T, "Content-Type": "application/json"};
let SERVER = location.origin;
function li(el, items){ el.innerHTML = items.length ? items.map(x=>"<li>"+x+"</li>").join("") : "<li class=dim>none</li>"; }
function esc(s){ return String(s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
async function refresh(){
  const s = await (await fetch("/api/status", {headers:H})).json();
  li(devices, s.devices.map(d=>esc(d.name)+" <span class=dim>("+d.kind+", "+d.seconds_ago+"s ago)</span>"));
  li(timers, s.timers.map(esc));
  li(alexa, Object.entries(s.alexa_devices).map(([k,v])=>"<code>"+esc(k)+"</code> <span class=dim>last used "+esc(v)+"</span>"));
  media.textContent = s.media_files + " files found in your media folders.";
  apk.innerHTML = s.apk ? '<a href="/apps/HeyMe.apk?t='+encodeURIComponent(T)+'">Download HeyMe.apk</a>' : "HeyMe.apk isn't in the server's apps folder yet.";
}
async function ask(){
  const text = q.value.trim(); if(!text) return;
  ans.textContent = "Thinking…";
  const r = await (await fetch("/api/ask_text", {method:"POST", headers:H, body: JSON.stringify({text})})).json();
  ans.textContent = "You: " + r.heard + "\nHey Me: " + r.reply;
  try { speechSynthesis.speak(new SpeechSynthesisUtterance(r.reply)); } catch(e) {}
  q.value = ""; refresh();
}
async function post(url){
  adm.textContent = "Working…";
  const r = await (await fetch(url, {method:"POST", headers:H})).json();
  adm.textContent = r.result || ("Found " + r.files + " files"); refresh();
}
function setupPhone(){
  const n = pname.value.trim() || "phone";
  location.href = "heyme://setup?server=" + encodeURIComponent(SERVER) + "&secret=" + encodeURIComponent(T) + "&name=" + encodeURIComponent(n);
}
q.addEventListener("keydown", e => { if(e.key === "Enter") ask(); });
refresh(); setInterval(refresh, 5000);
</script></main></body></html>"""


# ---------------------------------------------------------------------
# Start everything
# ---------------------------------------------------------------------
def start_pc_listener():
    listener = HERE.parent / "satellite" / "hey_me_satellite.py"
    if not settings.PC_MIC_ENABLED or not listener.exists():
        return
    subprocess.Popen([sys.executable, str(listener),
                      "--server", f"http://127.0.0.1:{settings.PORT}",
                      "--secret", settings.SECRET, "--name", settings.PC_MIC_NAME],
                     cwd=str(listener.parent))
    print(f"Started the PC microphone listener ('{settings.PC_MIC_NAME}').")


def main():
    print("=" * 60)
    print("  HEY ME SERVER")
    print("=" * 60)
    if not (settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")):
        print("\n!! Put your Claude API key in settings.py (ANTHROPIC_API_KEY) first.\n")
        input("Press Enter to close...")
        return
    if settings.SECRET.startswith("change-me"):
        print("\n!! Tip: change SECRET in settings.py to your own password.\n")

    print("Loading speech recognition (the first time downloads a model)...")
    speech.load_whisper()
    skills.start_background_jobs()

    from waitress import serve
    url = f"{skills.public_base()}/?t={quote(settings.SECRET)}"
    print(f"\nReady!  Status page:  {url}\n")
    start_pc_listener()
    serve(app, host="0.0.0.0", port=settings.PORT, threads=16)


if __name__ == "__main__":
    main()
