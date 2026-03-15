# Voice Inbox — Context Document for Claude Code (Terminal)

Read this entire document before doing anything. It contains everything you need
to set up Voice Inbox on this Mac.

---

## What is this?

Voice Inbox is a Python script that turns iPhone Voice Memos into automatically
sorted tasks, calendar events, research answers, and notes. The user presses the
Action Button on their iPhone, speaks a thought, and the script running on their
Mac handles everything else.

## The flow

1. User presses iPhone Action Button → records a Voice Memo
2. Voice Memo syncs to Mac via iCloud (lands in ~/Library/Mobile Documents/iCloud~com~apple~Voicememos/Documents/)
3. Script polls that folder every 30 seconds for new .m4a files
4. New memo → sent to OpenAI Whisper API for transcription
5. Transcript → sent to Claude API for classification into one of 5 categories
6. Based on category, the memo is routed:

| Category | What happens |
|---|---|
| task | Creates an Apple Reminder in "Voice Inbox" list |
| calendar_event | Creates an Apple Calendar event in "Voice Inbox" calendar |
| research_query | Sends question to Claude API, saves answer in daily log AND creates a Reminder with the answer (so user gets a phone notification) |
| message_draft | Logged only (user handles messages via Siri) |
| note | Saved in daily markdown log |

7. Everything is also logged in ~/voice-inbox/inbox/YYYY-MM-DD.md

## The code

The entire script is in the GitHub repo: https://github.com/labatuto/Voice-memos
Branch: claude/voice-memo-transcription-OM20a

Files:
- voice_inbox.py — the main script (single file, ~570 lines)
- .env.example — template for API keys
- SETUP.md — user-facing setup guide
- .gitignore — excludes .env, .processed, inbox/, logs

The script uses ONLY Python standard library (no pip install needed). It talks to
APIs via urllib and creates Reminders/Calendar events via osascript (AppleScript).

## What you need to do

### Step 1: Create the working directory and download files

```bash
mkdir -p ~/voice-inbox
cd ~/voice-inbox
curl -O https://raw.githubusercontent.com/labatuto/Voice-memos/claude/voice-memo-transcription-OM20a/voice_inbox.py
curl -O https://raw.githubusercontent.com/labatuto/Voice-memos/claude/voice-memo-transcription-OM20a/.env.example
```

### Step 2: Set up the .env file

Copy .env.example to .env:
```bash
cp .env.example .env
```

Then ask the user for their two API keys and write them into the .env file:
- OPENAI_API_KEY (from https://platform.openai.com/api-keys)
- ANTHROPIC_API_KEY (from https://console.anthropic.com/settings/keys)

The .env file format is:
```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

IMPORTANT: The user said they are NOT technical. Ask them clearly for each key,
one at a time. If they don't have keys yet, walk them through getting them with
simple instructions.

### Step 3: Create "Voice Inbox" list in Apple Reminders

Run this via osascript:
```bash
osascript -e 'tell application "Reminders" to make new list with properties {name:"Voice Inbox"}'
```

### Step 4: Create "Voice Inbox" calendar in Apple Calendar

Run this via osascript:
```bash
osascript -e 'tell application "Calendar" to make new calendar with name "Voice Inbox"'
```

### Step 5: Verify iCloud Voice Memos sync

Check that this folder exists:
```bash
ls ~/Library/Mobile\ Documents/iCloud~com~apple~Voicememos/Documents/
```

If it doesn't exist, tell the user to:
1. Open System Settings → [their name] → iCloud → iCloud Drive
2. Click Options next to iCloud Drive
3. Make sure Voice Memos is checked
4. Record a test Voice Memo on their phone and wait a minute

### Step 6: Verify the iPhone Action Button

Ask the user to confirm their Action Button is set to Voice Memos. If not:
1. Open Settings on iPhone
2. Tap Action Button
3. Swipe to Voice Memos
4. Select it

### Step 7: Test run

```bash
cd ~/voice-inbox
python3 voice_inbox.py
```

The script should start and show:
```
Voice Inbox started
Watching: /Users/.../Library/Mobile Documents/iCloud~com~apple~Voicememos/Documents
Inbox: /Users/.../voice-inbox/inbox
Polling every 30s
```

Tell the user to press their Action Button, say something like "remind me to buy
milk," and wait about a minute. They should see the memo get transcribed,
classified as a task, and a Reminder should appear on their phone.

### Step 8 (optional): Auto-start on login

If the user wants the script to run automatically when they log into their Mac,
create a LaunchAgent:

```bash
cat > ~/Library/LaunchAgents/com.voiceinbox.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voiceinbox</string>
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

launchctl load ~/Library/LaunchAgents/com.voiceinbox.plist
```

## Important notes for Claude in Terminal

- The user is NOT technical. Use plain language. Don't explain technical details
  unless asked. Just do things and confirm they worked.
- Don't ask the user to make technical decisions — make them yourself.
- The script has zero external dependencies. Do NOT run pip install anything.
- The .env file contains secrets. Never display the API keys back to the user
  or commit them to git.
- If something fails, troubleshoot it yourself before asking the user for help.
- After setup, offer to set up the auto-start LaunchAgent so they don't have to
  manually run the script every time.

## Architecture notes for future iterations

The user has mentioned wanting to expand this project. Future ideas include:
- More sophisticated routing (e.g., Slack messages, email drafts)
- Better research answers (web search integration)
- Multi-memo context (understanding "add to what I said earlier")
- A simple web dashboard to review the inbox

For now, the MVP is: tasks → Reminders, calendar → Calendar, research → Claude
answer via Reminder notification, everything → daily markdown log.
