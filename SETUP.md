# Voice Inbox — Setup Guide

Press the Action Button on your iPhone, say something, put your phone away. Your Mac
picks it up and automatically:

- **Tasks** → appear in Apple Reminders (in a list called "Voice Inbox")
- **Calendar events** → appear in Apple Calendar (in a calendar called "Voice Inbox")
- **Research questions** → get answered by Claude, with the answer delivered as a Reminder notification and saved in your daily log
- **Notes & ideas** → saved in your daily markdown log
- **Message drafts** → logged (use Siri to actually send messages)

Everything also gets logged in a daily markdown file at `~/voice-inbox/inbox/`.

---

## Step 0: Set up your iPhone

1. Open **Settings** on your iPhone
2. Tap **Action Button** (iPhone 15 Pro / 16 and later)
3. Swipe through options until you see **Voice Memos**
4. Select it — done

Now pressing the Action Button starts/stops a voice memo recording.

---

## Step 1: Make sure iCloud Drive syncs Voice Memos to your Mac

1. On your Mac, open **System Settings → [your name] → iCloud → iCloud Drive**
2. Make sure iCloud Drive is on
3. Click **Options...** next to iCloud Drive and confirm **Voice Memos** is checked
4. Record a test Voice Memo on your phone
5. Wait a minute, then open Finder and press **Cmd+Shift+G**, paste this path:

```
~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings
```

You should see your test memo there as an `.m4a` file. If so, syncing works.

> **Note:** On older macOS (pre-Sequoia), memos may be at
> `~/Library/Mobile Documents/iCloud~com~apple~Voicememos/Documents` instead.
> The script checks both locations automatically.

---

## Step 2: Get your API key

You need one key. It's free to set up (you pay tiny amounts per use — expect
well under $1/month for normal use). Transcription is handled by Apple on your
device, for free.

### Anthropic key (for Claude)

1. Go to https://console.anthropic.com/settings/keys
2. Sign in or create an account
3. Click **Create Key**
4. Copy it (starts with `sk-ant-`)

---

## Step 3: Install the script

Open **Terminal** (press Cmd+Space, type "Terminal", hit Enter). Then paste these
commands one at a time:

```bash
# 1. Create a folder for the script
mkdir -p ~/voice-inbox

# 2. Go into the folder
cd ~/voice-inbox

# 3. Download the files (or copy them manually from this repo)
#    You need: voice_inbox.py and .env.example

# 4. Create your config file from the template
cp .env.example .env

# 5. Open the config file to add your API keys
open -e .env
```

This opens the `.env` file in TextEdit. Replace the placeholder value with your
actual API key from Step 2. Save and close.

---

## Step 4: Create the "Voice Inbox" reminder list and calendar

### Reminders
1. Open the **Reminders** app on your Mac
2. In the bottom-left, click **Add List**
3. Name it **Voice Inbox**

### Calendar (for calendar events)
1. Open the **Calendar** app on your Mac
2. Right-click on the left sidebar and choose **New Calendar**
3. Name it **Voice Inbox**

If you use Google Calendar: make sure your Google account is added in
**System Settings → Internet Accounts → Google**. Events created in Apple Calendar
will sync to Google Calendar automatically.

---

## Step 5: Run it

In Terminal:

```bash
cd ~/voice-inbox
python3 voice_inbox.py
```

You should see:

```
Voice Inbox started
Watching: /Users/yourname/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings
Inbox: /Users/yourname/voice-inbox/inbox
Polling every 30s
```

Now record a voice memo on your phone. Within about a minute, you should see it get
picked up, transcript extracted, classified, and routed.

**Note:** Apple's on-device transcription may take a few seconds after recording.
If the script says "No transcript yet, will retry" — that's normal. It will pick
it up on the next poll cycle (30 seconds).

To stop it: press **Ctrl+C** in Terminal.

---

## Step 6 (optional): Make it run automatically when you log in

If you want this to start silently every time you open your Mac:

```bash
cat > ~/Library/LaunchAgents/com.santi.voice-inbox.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.santi.voice-inbox</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$HOME/voice-inbox/voice_inbox.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$HOME/voice-inbox</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/voice-inbox/voice_inbox.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/voice-inbox/voice_inbox.log</string>
</dict>
</plist>
EOF

# Load it
launchctl load ~/Library/LaunchAgents/com.santi.voice-inbox.plist
```

To stop the auto-run later:

```bash
launchctl unload ~/Library/LaunchAgents/com.santi.voice-inbox.plist
```

---

## How it works day to day

1. **Capture:** Press the Action Button, say your thought, press again to stop
2. **Process:** Whenever your Mac is on and online, the script picks up new memos
   (checks every 30 seconds). Each one gets transcribed and classified
3. **Check your phone:**
   - Tasks and research answers show up as **Reminders** notifications
   - Calendar events appear in your **Calendar**
4. **Review (optional):** Open `~/voice-inbox/inbox/` to see daily logs with
   everything neatly organized

---

## What to say — examples

| You say... | What happens |
|---|---|
| "Pick up dry cleaning tomorrow" | Creates a **task** in Reminders |
| "Dentist appointment Thursday at 2pm" | Creates a **calendar event** for Thursday 2-3pm |
| "Is melatonin safe to take with Tylenol?" | Sends question to **Claude**, answer appears in Reminders |
| "Note to self: the restaurant on 5th Ave was called Marea" | Saved as a **note** in your daily log |
| "Text Mom happy birthday" | Logged as a **message draft** (use Siri to send) |

---

## Costs

- **Transcription:** Free (done by Apple on your device)
- **Claude classification:** ~$0.003 per memo
- **Claude research:** ~$0.01 per research query (uses more tokens for the answer)
- **Total:** roughly half a cent per voice memo. Even heavy use (10 memos/day) is
  about $1-2/month

---

## Troubleshooting

**"Voice Memos folder not found"** → iCloud Drive isn't syncing. Open System
Settings → Apple ID → iCloud → iCloud Drive and make sure it's on.

**Memos aren't appearing on Mac** → It can take 1-2 minutes for iCloud to sync.
Make sure your Mac is online. Try opening the Voice Memos app on Mac to kick-start
the sync.

**"No transcript yet, will retry"** → Apple's on-device transcription may take a
few seconds. The script will automatically retry on the next poll cycle. If it
persists, make sure you're running macOS Sequoia (15+) on an Apple Silicon Mac
(M1 or later). Open the Voice Memos app on your Mac and check if the transcript
appears there.

**"Classification failed"** → Check your Anthropic API key in `.env`. Make sure you
have credits at https://console.anthropic.com

**Reminders not appearing** → Make sure you created a list called "Voice Inbox" in
the Reminders app. The script falls back to your default list if it can't find it.

**Calendar events not appearing** → Make sure you created a calendar called "Voice
Inbox" in the Calendar app. The script falls back to your first available calendar.
