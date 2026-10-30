"""
Talks to your MEdiaCenter server on this PC (through C:\\MEdiaCenter\\app\\heyme_bridge.py).
Lets Hey Me search the MEdiaCenter library, play titles on screens that show MEdiaCenter,
ask ME (what's on TV, recordings, what's new) and post family announcements.
"""

import time

import requests

import settings

_screens_cache = {"t": 0, "list": []}


def enabled():
    return bool(getattr(settings, "MEDIACENTER_URL", ""))


def call(action, **kw):
    """-> the bridge's answer (a dict), or None if MEdiaCenter isn't reachable."""
    if not enabled():
        return None
    try:
        r = requests.post(settings.MEDIACENTER_URL.rstrip("/") + "/api/heyme/bridge",
                          json={"action": action, **kw}, timeout=20,
                          headers={"X-HeyMe-Secret": settings.SECRET})
        if r.status_code == 403:
            print("MEdiaCenter refused Hey Me: check that the secret in "
                  "C:\\MEdiaCenter\\data\\heyme.json matches SECRET in settings.py")
            return None
        return r.json() if r.ok else None
    except Exception:
        return None


def screens():
    """Names of the screens currently showing MEdiaCenter (cached for 20 seconds)."""
    if time.time() - _screens_cache["t"] > 20:
        r = call("screens")
        _screens_cache["list"] = [s["name"] for s in (r or {}).get("screens", [])]
        _screens_cache["t"] = time.time()
    return _screens_cache["list"]


def search(query, kind="any"):
    r = call("search", query=query, kind=kind)
    return (r or {}).get("items", [])


def screen_cmd(screen, cmd, **kw):
    r = call("screen", screen=screen, cmd=cmd, **kw)
    if r is None:
        return {"ok": False, "error": "MEdiaCenter isn't answering."}
    _screens_cache["t"] = 0
    return r


def ask(question):
    r = call("ask", q=question)
    if r is None:
        return "MEdiaCenter isn't answering."
    return r.get("say") or "ME didn't have an answer for that."


def announce(text):
    r = call("announce", text=text)
    return "Posted the announcement in MEdiaCenter." if r and r.get("ok") else "MEdiaCenter isn't answering."
