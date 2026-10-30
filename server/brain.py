"""
The brain: sends what you said to Claude, lets Claude use skills, returns the answer.
Each device (kitchen Pi, your phone, ...) has its own short conversation memory.
"""

import re
import time
import datetime
import threading

import anthropic

import settings
import skills
import mediacenter

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)

_conversations = {}            # device name -> {"messages": [...], "last": time}
_lock = threading.Lock()
_web_search_works = settings.WEB_SEARCH
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


def system_prompt(ctx):
    now = datetime.datetime.now().strftime("%A %B %d %Y, %I:%M %p")
    online = [d["name"] for d in skills.hub.online()]
    return f"""You are Hey Me, a voice assistant for the user's home, like Alexa.
Your replies are spoken out loud, so: 1 to 3 short sentences, natural spoken English,
no markdown, no lists, no links, no emojis. Say numbers the way a person would.

It is now {now}. Home: {settings.CITY}.
You're talking through the device "{ctx['device']}" (type: {ctx['kind']}).
"Here" or "this room" means that device.
Hey Me devices online: {', '.join(online) or 'none'}.
TVs (NVIDIA Shields, controlled with the shield tool): {', '.join(settings.SHIELDS) or 'none'}.
Screens showing MEdiaCenter right now (play titles there with play_media): {', '.join(mediacenter.screens()) or 'none'}.
The media library is MEdiaCenter's. For live TV, recordings or what's new, use the mediacenter tool.
Echo speakers: {', '.join(settings.ECHOS) or 'none set up'}.
The server PC: "pc".

Use your tools to do things. After a tool works, confirm in a few words
("Done", "Timer set for ten minutes"). If something fails, say so simply.
If it's unclear which device the user means, use the device they're talking through.
For questions about news, sports, hours, prices or anything current, use web search."""


def _trim(messages):
    """Keep the last few exchanges; always start at something the user said."""
    while len(messages) > 16:
        messages.pop(0)
        while messages and not (messages[0]["role"] == "user"
                                and isinstance(messages[0]["content"], str)):
            messages.pop(0)


def _for_speech(text):
    text = re.sub(r"[*#_`>|]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def think(user_text, ctx):
    """ctx = {"device": name, "kind": "satellite"/"android"/"alexa"/"web"}"""
    global _web_search_works
    key = ctx["device"].lower()
    with _lock:
        convo = _conversations.get(key)
        if not convo or time.time() - convo["last"] > settings.FORGET_AFTER_MINUTES * 60:
            convo = {"messages": [], "last": 0}
            _conversations[key] = convo
        convo["last"] = time.time()
    messages = convo["messages"]
    _trim(messages)
    messages.append({"role": "user", "content": user_text})

    try:
        for _ in range(10):
            tools = skills.build_tools()
            if _web_search_works:
                tools = tools + [WEB_SEARCH_TOOL]
            try:
                reply = client.messages.create(
                    model=settings.CLAUDE_MODEL, max_tokens=700,
                    system=system_prompt(ctx), tools=tools, messages=messages)
            except anthropic.BadRequestError as e:
                if _web_search_works and "web_search" in str(e).lower():
                    print("Web search isn't available on this API key; turning it off.")
                    _web_search_works = False
                    continue
                raise

            messages.append({"role": "assistant", "content": reply.content})

            if reply.stop_reason == "pause_turn":       # long web search, keep going
                continue
            if reply.stop_reason != "tool_use":
                text = " ".join(b.text for b in reply.content if b.type == "text")
                return _for_speech(text) or "Done."

            results = []
            for block in reply.content:
                if block.type == "tool_use":
                    print(f"  skill: {block.name} {block.input}")
                    try:
                        out = skills.run_tool(block.name, block.input, ctx)
                    except Exception as e:
                        out = f"Error: {e}"
                    print(f"     -> {str(out)[:200]}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(out)})
            messages.append({"role": "user", "content": results})
        return "Sorry, that took too many steps."
    except Exception:
        messages.clear()          # start fresh next time
        raise
