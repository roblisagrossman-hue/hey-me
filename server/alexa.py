"""
Lets your Amazon Echos talk TO Hey Me:
  "Alexa, ask Hey Me to turn on the living room TV"
Amazon sends the words here; Hey Me answers and the Echo speaks the answer.
Setup steps: alexa/ALEXA_SETUP.md
"""

import base64
import datetime
import posixpath
import concurrent.futures
from urllib.parse import urlparse

import requests

import settings
import skills
import brain

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_cert_cache = {}


# ---------------------------------------------------------------------
# Make sure a request really comes from Amazon, for your skill
# ---------------------------------------------------------------------
def verify(headers, body: bytes, data: dict):
    if not settings.ALEXA_SKILL_ID:
        raise ValueError("ALEXA_SKILL_ID is empty in settings.py")
    app_id = (data.get("session", {}).get("application", {}).get("applicationId")
              or data.get("context", {}).get("System", {}).get("application", {}).get("applicationId"))
    if app_id != settings.ALEXA_SKILL_ID:
        raise ValueError(f"Request is for a different skill: {app_id}")

    ts = datetime.datetime.fromisoformat(data["request"]["timestamp"].replace("Z", "+00:00"))
    if abs((datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()) > 150:
        raise ValueError("Request is too old (check this PC's clock)")

    if not settings.ALEXA_VERIFY_SIGNATURE:
        return

    from cryptography import x509
    from cryptography.x509.verification import PolicyBuilder, Store
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    import certifi

    url = headers.get("SignatureCertChainUrl", "")
    p = urlparse(url)
    if (p.scheme.lower() != "https" or (p.hostname or "").lower() != "s3.amazonaws.com"
            or not posixpath.normpath(p.path).startswith("/echo.api/") or p.port not in (None, 443)):
        raise ValueError("Bad certificate URL")

    if url not in _cert_cache:
        pem = requests.get(url, timeout=10).content
        _cert_cache[url] = x509.load_pem_x509_certificates(pem)
    certs = _cert_cache[url]

    with open(certifi.where(), "rb") as f:
        store = Store(x509.load_pem_x509_certificates(f.read()))
    verifier = PolicyBuilder().store(store).build_server_verifier(x509.DNSName("echo-api.amazon.com"))
    verifier.verify(certs[0], certs[1:])          # valid, unexpired, issued to Amazon

    signature = base64.b64decode(headers.get("Signature-256", ""))
    certs[0].public_key().verify(signature, body, padding.PKCS1v15(), hashes.SHA256())


# ---------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------
def _say(text, end=True, reprompt=None, attrs=None):
    out = {"version": "1.0", "sessionAttributes": attrs or {},
           "response": {"outputSpeech": {"type": "PlainText", "text": text[:7000]},
                        "shouldEndSession": end}}
    if reprompt:
        out["response"]["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return out


def handle(data):
    req = data.get("request", {})
    kind = req.get("type")
    device_id = data.get("context", {}).get("System", {}).get("device", {}).get("deviceId", "")
    if device_id:
        skills.hub.alexa_ids[device_id] = datetime.datetime.now().strftime("%b %d %I:%M %p")
    echo_name = settings.ALEXA_DEVICE_NAMES.get(device_id, "echo")
    attrs = data.get("session", {}).get("attributes") or {}
    chatting = bool(attrs.get("chatting"))

    if kind == "LaunchRequest":
        return _say("Hey Me here. What can I do?", end=False,
                    reprompt="What would you like?", attrs={"chatting": True})
    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    if kind != "IntentRequest":
        return _say("Sorry, I can't do that.")

    intent = req.get("intent", {})
    name = intent.get("name", "")
    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent"):
        return _say("Okay.")
    if name == "AMAZON.HelpIntent":
        return _say("Just tell me what you need, like: ask Hey Me to play music on the living room TV.",
                    end=False, reprompt="What would you like?", attrs={"chatting": True})
    if name == "AMAZON.FallbackIntent":
        return _say("Sorry, I didn't catch that.", end=not chatting,
                    reprompt="Try again?" if chatting else None, attrs=attrs)

    query = (intent.get("slots", {}).get("query", {}) or {}).get("value", "")
    if not query:
        return _say("Sorry, I didn't catch that.")
    print(f"ALEXA ({echo_name}): {query}")

    ctx = {"device": echo_name, "kind": "alexa"}
    future = _pool.submit(brain.think, query, ctx)
    try:
        answer = future.result(timeout=6.5)         # Alexa only waits about 8 seconds
    except concurrent.futures.TimeoutError:
        if echo_name in settings.ECHOS and settings.VOICE_MONKEY_TOKEN:
            future.add_done_callback(lambda f: _late_answer(f, echo_name))
            return _say("Working on it. I'll tell you in a moment.")
        return _say("That's taking a while. Ask me again in a minute.")
    except Exception as e:
        print("Alexa error:", e)
        return _say("Sorry, something went wrong on the server.")

    print(f"HEY ME -> {echo_name}: {answer}")
    if chatting:
        return _say(answer, end=False, reprompt="Anything else?", attrs={"chatting": True})
    return _say(answer)


def _late_answer(future, echo_name):
    try:
        skills.echo_announce(future.result(), echo_name)
    except Exception as e:
        print("Late answer error:", e)
