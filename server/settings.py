# =====================================================================
#  HEY ME SETTINGS  -  this is the only file you need to edit.
#  Open it in Notepad, change the values, save, then restart Hey Me.
#  Text values go inside "quotes". Lines starting with # are notes.
# =====================================================================

# ---- The basics ------------------------------------------------------

# Your Claude API key (from console.anthropic.com). Starts with sk-ant-
ANTHROPIC_API_KEY = ""

# A password that every Hey Me device must know. Make up something long.
# Use the SAME secret on your phones, Shields, Raspberry Pis and listeners.
SECRET = "change-me-to-a-long-password"

# The port the server listens on. Leave this alone unless something else uses it.
PORT = 8765

# Where you live (used for the weather).
CITY = "Fargo"

# Which Claude model is the "brain".
#   "claude-haiku-5-5"   = fastest and cheapest (recommended for voice)
#   "claude-sonnet-5-5"  = smarter, a little slower
CLAUDE_MODEL = "claude-haiku-5-5"

# Let Hey Me search the web for news, scores, store hours, etc.
WEB_SEARCH = True

# Hey Me forgets the conversation after this many quiet minutes
# (so a new question starts fresh, like Alexa).
FORGET_AFTER_MINUTES = 5

# ---- Speech -----------------------------------------------------------

# Speech-to-text model. "base.en" = faster, "small.en" = more accurate.
WHISPER_MODEL = "small.en"

# The voice Hey Me speaks with on your PCs and Raspberry Pis.
# Leave blank for the Windows default. Other options on most PCs:
#   "Microsoft David Desktop"  (male)   "Microsoft Zira Desktop"  (female)
VOICE = ""
VOICE_SPEED = 1          # -10 (slow) to 10 (fast)

# ---- Microphone on this server PC -------------------------------------

# True = the server PC itself listens for "Hey Me" with its own microphone.
PC_MIC_ENABLED = True
PC_MIC_NAME = "office"   # what you call the room this PC is in

# ---- PC control --------------------------------------------------------

# Apps you can open by voice: "Hey Me, open notepad".
# Left side = what you say, right side = the program.
PC_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "file explorer": "explorer.exe",
    "chrome": "chrome.exe",
    "vlc": "vlc.exe",
}

# ---- NVIDIA Shields ----------------------------------------------------

# Your Shields: a name you'll say, and the Shield's IP address.
# Find the IP on the Shield: Settings > Device Preferences > About > Status
SHIELDS = {
    "living room tv": "192.168.1.50",
    "bedroom tv": "192.168.1.51",
}

# Apps Hey Me can open on a Shield (name you say : Android package name).
SHIELD_APPS = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "plex": "com.plexapp.android",
    "kodi": "org.xbmc.kodi",
    "vlc": "org.videolan.vlc",
    "hey me": "com.heyme.app",
}

# Where adb.exe is. Leave as "adb" if you added platform-tools to PATH,
# otherwise e.g. r"C:\platform-tools\adb.exe"
ADB_PATH = "adb"

# ---- Your media ---------------------------------------------------------

# Folders Hey Me searches when you say "play ...".
# Use r"..." around Windows paths. Add as many folders as you like.
MEDIA_FOLDERS = [
    r"D:\Music",
    r"D:\Movies",
    r"D:\TV Shows",
]

# How other devices reach this server. Leave blank and Hey Me works it out.
# If you use Tailscale everywhere, put this PC's Tailscale address, e.g.
#   "http://100.101.102.103:8765"
PUBLIC_URL = ""

# ---- Amazon Echo / Alexa -------------------------------------------------
# See alexa/ALEXA_SETUP.md. Leave these empty until you get to that step.

# Voice Monkey (lets Hey Me make your Echos talk and run Alexa routines)
VOICE_MONKEY_TOKEN = ""

# Your Echos: the name you'll say : the Voice Monkey "Speaker" device ID
ECHOS = {
    # "kitchen echo": "kitchen-echo-abc12",
}

# Alexa routines Hey Me can start: the name you'll say : Voice Monkey "Routine Trigger" ID
ECHO_ROUTINES = {
    # "good night": "good-night-xyz34",
}

# Your Alexa skill ID (starts with amzn1.ask.skill.) so Echos can talk TO Hey Me
ALEXA_SKILL_ID = ""

# Which Echo is which: Alexa device ID : name from ECHOS above.
# The Hey Me status page shows the IDs after you talk to each Echo once.
ALEXA_DEVICE_NAMES = {
    # "amzn1.ask.device.AEXXXXXXXX": "kitchen echo",
}

# Security check that requests really come from Amazon. Leave True.
ALEXA_VERIFY_SIGNATURE = True
