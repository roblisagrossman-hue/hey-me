# Hey Me — your own Alexa

Say **"Hey Me"** anywhere in the house. The 24/7 server PC hears you, does the job,
and answers out loud.

```
  Phones (app)      Shields (app)     Raspberry Pi speakers     Echos (Alexa skill)
        \                 |                  |                       /
         \________________|__________________|______________________/
                                    |
                     HEY ME SERVER  (your 24/7 PC)
            speech-to-text · Claude brain · timers · your media
                                    |
         controls: the PC · both Shields · Echos & Alexa routines · media
```

## Combined with MEdiaCenter

Hey Me lives in `C:\MEdiaCenter\HeyMe` and works together with MEdiaCenter:

- **"Hey ME" in MEdiaCenter** (web pages, MEdiaDroid, the widget) still does everything it did
  before: play, open pages, weather, what's on. Anything ME doesn't know now goes to Hey Me's
  brain (Claude), so ME can also set timers, control the Shields, make announcements on the Echos and Pi speakers,
  and answer any question.
- **Hey Me** (phones, Pi speakers, Echos) uses **MEdiaCenter's library** and can play titles on
  any **screen showing MEdiaCenter**: "Hey Me, play Jaws on the living room Shield". It can also ask ME
  what's on live TV, what's recording and what's new.
- The connection is `C:\MEdiaCenter\app\heyme_bridge.py`, with settings in
  `C:\MEdiaCenter\data\heyme.json`. Its secret must match `SECRET` in `HeyMe\server\settings.py`.
- **After a MEdiaCenter update**, check that `server.py` still has these two lines right after
  `alexa.install(app, globals())`. If they're missing, add them back:
  ```python
  import heyme_bridge                                     # noqa: E402
  heyme_bridge.install(app, globals())
  ```

## What's in this folder

| Folder | What it is |
|---|---|
| `server/` | The brain. Runs on the 24/7 PC. **`settings.py` is the only file you need to edit.** |
| `satellite/` | The "listener" for any other PC, the Minix mini PC, or a Raspberry Pi speaker |
| `android/` | The app for your phones and Shields (GitHub builds it into `HeyMe.apk`) |
| `alexa/` | Echo setup (see `alexa/ALEXA_SETUP.md`) |

Do the steps in order. Each one works on its own, so you can stop after any of them.

---

## Step 1 — The server (on the 24/7 PC)  ⏱ 20 min

1. **Install Python 3.12** from python.org. On the first screen, tick
   **"Add Python to PATH"**.
2. Hey Me is already in `C:\MEdiaCenter\HeyMe`.
3. Open `C:\MEdiaCenter\HeyMe\server\settings.py` in Notepad and fill in at least:
   - `ANTHROPIC_API_KEY`: your key from console.anthropic.com
   - `SECRET`: make up a password. Every Hey Me device will use it.
   - `PC_MIC_NAME`: the room this PC is in, e.g. `"office"`
   - `MEDIA_FOLDERS`: where your music and movies are
   - `SHIELDS`: your Shields' names and IP addresses (Step 3 shows where to find them)
4. Double-click **`1 - Install Hey Me.bat`**. Say **Y** when asked about starting with Windows.
5. Double-click **`2 - Start Hey Me.bat`**. The first start downloads the speech models
   (a few minutes). When it says **Ready!** it shows a **status page** address. Open that
   page in your browser and bookmark it.
6. Try it: type *"what's the weather"* into the status page. Then say
   **"Hey Me"** to the PC's microphone, wait for the beep, and ask something.

> **Windows Firewall:** the first time, Windows asks whether Python may use the network.
> Tick **Private networks** and click **Allow**, otherwise your other devices can't reach the server.

---

## Step 2 — Control the Shields  ⏱ 10 min

1. Download **SDK Platform-Tools for Windows** from developer.android.com
   (search "android platform tools"). Unzip it to `C:\platform-tools`.
   In `settings.py`, set `ADB_PATH = r"C:\platform-tools\adb.exe"`.
2. On **each Shield**:
   - Settings → Device Preferences → About → write down the **IP address** (under Status)
   - Click **Build** 7 times ("You are now a developer")
   - Settings → Device Preferences → **Developer options** → turn on **Network debugging**
3. Put the IPs in `SHIELDS` in `settings.py` and restart Hey Me.
4. Say *"Hey Me, go home on the living room TV."* The first time, the TV asks
   **"Allow network debugging?"**. Tick **Always allow from this computer** and press OK.

> Tip: in your router, give each Shield a **reserved IP** so the address never changes.

---

## Step 3 — The phone & Shield app  ⏱ 15 min

### Get HeyMe.apk (built for you by GitHub)
1. Go to github.com and create a new **private** repository called `hey-me` (leave it empty).
2. Tell Claude: *"I made the hey-me repo, upload Hey Me to it"*. Claude uploads
   everything, and GitHub builds the app automatically (about 5 minutes).
   *Doing it yourself instead:* on the empty repo page, click "uploading an existing file"
   and drag in **everything inside** the HeyMe folder, including the `.github` folder.
3. On the repo page, click **Releases** (right side) → newest build → download **HeyMe.apk**.
4. Put `HeyMe.apk` in the server's `server\apps\` folder.

### Shields (one click)
On the status page, click **"Install / update on all Shields"**. Each Shield gets the
app, already set up. Shields have no microphone, but they will **speak timers and
announcements** on the TV and **play your media**. If you plug a USB microphone into a
Shield, you can turn on "Listen for Hey Me" in the app.

### Phones
1. On the phone, open the status page and tap **Download HeyMe.apk**. Open it and allow
   "install unknown apps" when asked.
2. Back on the status page, type a name (e.g. `rob phone`) and tap **Set up this phone**.
   The app opens already set up. Allow the microphone and notifications.
3. Tap **"Allow Hey Me to start music and videos"** and **"Let Hey Me run in the background"**.
4. Install **VLC** from the Play Store so Hey Me can play your music and movies.

**Away from home?** Install Tailscale on the phone, and in the app use the server's
Tailscale address (e.g. `http://100.x.x.x:8765`).

---

## Step 4 — Room speakers (Raspberry Pi)  ⏱ 30 min each

**What to buy for each room:**
- **Raspberry Pi Zero 2 W** (cheapest) or **Raspberry Pi 4** (faster), plus its power supply and a microSD card
- A **USB speakerphone** (a conference-call mic and speaker in one), or any USB mic + speaker
- For the Zero 2 W: a micro-USB-to-USB-A **OTG adapter**

**Setup:**
1. Use **Raspberry Pi Imager** to install **Raspberry Pi OS Lite (64-bit)**. In the Imager
   settings, set your Wi-Fi, a username/password, and turn on SSH.
2. Copy the `satellite` folder onto the Pi (e.g. with WinSCP), or plug in a USB stick.
3. Edit `hey_me_satellite.py`: set `SERVER`, `SECRET` and `NAME` (e.g. `"kitchen"`).
4. On the Pi, run:
   ```
   cd satellite
   bash install_on_raspberry_pi.sh
   ```
   That's it. It starts every time the Pi powers on.

If it can't hear you, run `venv/bin/python hey_me_satellite.py --list-devices` and put the
mic's number in `MIC =` (and the speaker's in `SPEAKER =`).

## Step 5 — The Minix mini PC or any other PC  ⏱ 10 min
1. Install Python (as in Step 1) and copy the `satellite` folder over.
2. Edit `hey_me_satellite.py` (`SERVER`, `SECRET`, `NAME`).
3. Double-click **`Start Hey Me Listener.bat`**. To start it with Windows, press
   **Win+R**, type `shell:startup`, and put a shortcut to the .bat file in that folder.
4. Install VLC to play media there.

On Linux (MEdiaLive), use `install_on_raspberry_pi.sh`. It works on any Debian or Ubuntu system.

## Step 6 — Echos
See **`alexa/ALEXA_SETUP.md`**.

---

## Things to say

| Say | What happens |
|---|---|
| "Hey Me, what's the weather this weekend?" | Forecast for Fargo |
| "Hey Me, set a pizza timer for 12 minutes" | Rings in the room you asked from |
| "Hey Me, remind me at 6 pm to take the trash out" | Reminder |
| "Hey Me, announce dinner's ready everywhere" | Every device and Echo speaks it |
| "Hey Me, play the Beatles in the kitchen" | Your music, on the kitchen Pi |
| "Hey Me, play The Matrix on the bedroom TV" | Your movie, on that Shield |
| "Hey Me, open YouTube on the living room TV" | Opens the app on the Shield |
| "Hey Me, search YouTube for ice fishing on the living room TV" | YouTube search |
| "Hey Me, pause the living room TV" / "volume up 3 times" | Shield remote control |
| "Hey Me, turn the PC volume down" | PC media keys |
| "Hey Me, run good night" | Starts that Alexa routine |
| "Hey Me, who won the Vikings game?" | Searches the web |
| "Hey Me, stop the music" | Stops media Hey Me started |

---

## Troubleshooting

- **Devices say "couldn't reach the server":** check the address (`http://PC-IP:8765`),
  the SECRET, and the Windows Firewall (Step 1).
- **It doesn't wake up:** speak clearly. "Hey Me" is short, so "**Hey… Me**" with a tiny pause
  works best. Make sure the right microphone is selected.
- **It wakes up by itself:** the TV probably said something close. Move the mic away from the TV.
- **Wrong words heard:** set `WHISPER_MODEL = "small.en"` (the default) or try `"medium.en"` on a fast PC.
- **"The Claude API key isn't working":** check `ANTHROPIC_API_KEY` in `settings.py`.
- **Anything else:** look at the Hey Me server window. It prints every request and every
  skill it runs.

## About your MEdiaCenter
Hey Me plays files straight from your media folders right now. To have it control
MEdiaCenter / MEdiaPlayer directly, share that project's code with Claude and add a
`mediacenter` skill in `server/skills.py`.
