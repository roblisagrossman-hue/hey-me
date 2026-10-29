# Hey Me + your Amazon Echos

There are two separate parts. You can set up either one, or both.

| Part | What it lets you do | Time |
|---|---|---|
| **A. Voice Monkey** | Hey Me makes your Echos **talk** (timers, announcements, late answers) and **starts Alexa routines** (lights, plugs, anything Alexa controls) | 15 min |
| **B. The Hey Me Alexa skill** | Talk **to** Hey Me through any Echo: *"Alexa, ask Hey Me to play the Matrix on the bedroom TV"* | 30 min |

---

## A. Voice Monkey (Hey Me → Echo)

Voice Monkey is a free service that lets other programs make an Echo speak.

1. Go to **voicemonkey.io** and sign in with the **same Amazon account** your Echos use.
2. Follow its steps to enable the Voice Monkey skill in your Alexa app.
3. In the Voice Monkey dashboard, add a **Speaker** device for each Echo
   (for example "kitchen echo"). Copy each Speaker's **device ID**.
4. Find your **API token** in the dashboard and copy it.
5. Open `server/settings.py` and fill in:
   ```python
   VOICE_MONKEY_TOKEN = "your-token"
   ECHOS = {
       "kitchen echo": "the-kitchen-speaker-id",
       "bedroom echo": "the-bedroom-speaker-id",
   }
   ```
6. **Routines (optional):** in Voice Monkey, add a **Routine Trigger** device, for example
   "good night". In the **Alexa app**, create a routine: *When this happens → Smart Home →
   (your trigger)*, then add whatever actions you like (lights off, lock door…). Then add it:
   ```python
   ECHO_ROUTINES = {"good night": "the-trigger-id"}
   ```
7. Restart Hey Me. Try: *"Hey Me, announce dinner is ready on the kitchen echo."*

---

## B. The Hey Me Alexa skill (Echo → Hey Me)

Amazon needs a secure (https) web address to reach your server. **Tailscale Funnel**
gives you one for free, and you already use Tailscale.

### 1. Make the server reachable with Tailscale Funnel
On the server PC, open Command Prompt and run:
```
tailscale funnel --bg 8765
```
The first time, Tailscale shows a link to turn Funnel on for your tailnet. Open it and approve.
It then prints your address, something like `https://your-pc.tail1234.ts.net`.
Only Amazon's requests (which are checked) and requests with your SECRET are answered.

### 2. Create the skill
1. Go to **developer.amazon.com/alexa/console/ask** and sign in with the **same Amazon
   account as your Echos**.
2. **Create Skill** → name: `Hey Me` → type: **Other** → **Custom** →
   hosting: **Provision your own** → template: **Start from scratch**.
3. Left menu: **Interaction Model → JSON Editor**. Delete what's there, paste everything
   from `alexa/interaction_model.json`, click **Save**, then **Build skill**.
4. Left menu: **Endpoint** → choose **HTTPS** → Default Region:
   `https://your-pc.tail1234.ts.net/alexa` (your address from step 1, plus `/alexa`).
   Certificate: **"My development endpoint has a certificate from a trusted certificate authority"**.
   Click **Save**.
5. On the same Endpoint page, copy the **Skill ID** (starts with `amzn1.ask.skill.`).
6. Put it in `server/settings.py`:
   ```python
   ALEXA_SKILL_ID = "amzn1.ask.skill.xxxxxxxx"
   ```
   and restart Hey Me.
7. Top menu: **Test** → change "Off" to **Development**. Type *"ask hey me what time it is"*.

You don't need to publish the skill. Once it's in Development, it works on every Echo
on your Amazon account.

### 3. Tell Hey Me which Echo is which (so timers ring on the right one)
1. Talk to each Echo once: *"Alexa, ask Hey Me what time it is."*
2. Open the Hey Me status page. Under **Echo device IDs** you'll see one ID per Echo.
3. Copy them into `settings.py`, using the same names as in `ECHOS`:
   ```python
   ALEXA_DEVICE_NAMES = {
       "amzn1.ask.device.AEXXXX...": "kitchen echo",
   }
   ```

### How to talk to it
- *"Alexa, ask Hey Me to turn on the living room TV."*
- *"Alexa, tell Hey Me to play the Beatles on the PC."*
- *"Alexa, open Hey Me"* → it stays listening, so you can keep talking. Say "stop" when done.

Alexa only waits about 8 seconds. If an answer takes longer, Hey Me says "Working on it"
and the Echo speaks the answer a moment later (needs part A).

### If it doesn't work
- **"There was a problem with the requested skill's response":** look at the Hey Me window.
  - `Request is for a different skill`: check `ALEXA_SKILL_ID`.
  - `Request is too old`: fix the PC's clock (Windows Settings → Time → Sync now).
  - Any other "refused" message: as a test, set `ALEXA_VERIFY_SIGNATURE = False`, restart,
    and try again. If that fixes it, tell Claude what the error said. Turn it back to `True` afterwards.
- **Nothing shows in the Hey Me window:** run `tailscale funnel status` and check that the
  Endpoint address ends with `/alexa`.
