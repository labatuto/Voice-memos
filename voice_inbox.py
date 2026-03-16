#!/usr/bin/env python3
"""
Voice Inbox — turns Voice Memos into sorted tasks, notes, and actions.

Records land in iCloud via Voice Memos → Apple transcribes them on-device →
this script reads the transcript from the .m4a file → classifies with Claude →
routes to the right place.
"""

import sys
import json
import struct
import time
import subprocess
import datetime
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG  (loaded from .env file next to this script)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()

def load_env():
    """Read key=value pairs from .env file."""
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        print(f"\n❌  Missing .env file at {env_path}")
        print("   Copy .env.example to .env and add your API keys.")
        sys.exit(1)
    config = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            config[key.strip()] = val.strip().strip('"').strip("'")
    return config

ENV = load_env()

ANTHROPIC_API_KEY = ENV.get("ANTHROPIC_API_KEY", "")

if not ANTHROPIC_API_KEY:
    print("❌  ANTHROPIC_API_KEY must be set in .env")
    print("   Get one at: https://console.anthropic.com/settings/keys")
    sys.exit(1)

# Where Voice Memos live on Mac (iCloud sync)
VOICE_MEMOS_DIR = Path.home() / "Library" / "Mobile Documents" / "iCloud~com~apple~Voicememos" / "Documents"

# Where we keep our outputs
INBOX_DIR = SCRIPT_DIR / "inbox"
INBOX_DIR.mkdir(exist_ok=True)

# Ledger of already-processed files
PROCESSED_LEDGER = SCRIPT_DIR / ".processed"

# How often to check for new memos (seconds)
POLL_INTERVAL = 30

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

LOG_PATH = SCRIPT_DIR / "voice_inbox.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("voice-inbox")

# ---------------------------------------------------------------------------
# PROCESSED-FILE TRACKING
# ---------------------------------------------------------------------------

def load_processed() -> set:
    if PROCESSED_LEDGER.exists():
        return set(PROCESSED_LEDGER.read_text().splitlines())
    return set()

def mark_processed(file_id: str):
    with open(PROCESSED_LEDGER, "a") as f:
        f.write(file_id + "\n")

def file_id(path: Path) -> str:
    """Stable identifier for a file: name + size."""
    stat = path.stat()
    return f"{path.name}::{stat.st_size}"

# ---------------------------------------------------------------------------
# STEP 1 — Find new voice memos
# ---------------------------------------------------------------------------

def find_new_memos() -> list[Path]:
    """Return list of .m4a files that haven't been processed yet."""
    if not VOICE_MEMOS_DIR.exists():
        log.warning(f"Voice Memos folder not found: {VOICE_MEMOS_DIR}")
        log.warning("Make sure iCloud Drive is enabled and Voice Memos are syncing.")
        return []

    processed = load_processed()
    new_files = []

    for m4a in sorted(VOICE_MEMOS_DIR.rglob("*.m4a")):
        fid = file_id(m4a)
        if fid not in processed:
            # Make sure the file is done syncing (size stable for 5 seconds)
            size1 = m4a.stat().st_size
            if size1 == 0:
                continue
            time.sleep(3)
            try:
                size2 = m4a.stat().st_size
            except FileNotFoundError:
                continue
            if size1 == size2:
                new_files.append(m4a)

    return new_files

# ---------------------------------------------------------------------------
# STEP 2 — Read Apple's on-device transcription from the .m4a file
# ---------------------------------------------------------------------------

def _find_atom(data: bytes, target: bytes) -> bytes | None:
    """Walk the MP4 atom tree and return the payload of the target atom."""
    offset = 0
    while offset < len(data) - 8:
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        atom_type = data[offset + 4:offset + 8]
        if size < 8:
            break
        if atom_type == target:
            # Return everything after the 8-byte header
            return data[offset + 8:offset + size]
        # Container atoms we need to descend into
        if atom_type in (b"moov", b"trak", b"udta"):
            result = _find_atom(data[offset + 8:offset + size], target)
            if result is not None:
                return result
        offset += size
    return None


def transcribe(audio_path: Path) -> str:
    """Extract Apple's on-device transcription from the .m4a file's tsrp atom."""
    log.info(f"  Reading transcript: {audio_path.name}")

    try:
        data = audio_path.read_bytes()
    except Exception as e:
        log.error(f"  Could not read file: {e}")
        return ""

    payload = _find_atom(data, b"tsrp")
    if payload is None:
        log.warning(f"  No transcription found in {audio_path.name}")
        log.warning("  (Apple may still be processing — will retry next poll)")
        return ""

    # The payload starts with "tsrp" prefix before the JSON in some cases,
    # or is raw JSON. Find the first '{' to be safe.
    try:
        json_start = payload.index(b"{")
        tsrp_json = json.loads(payload[json_start:])
    except (ValueError, json.JSONDecodeError) as e:
        log.error(f"  Could not parse transcript JSON: {e}")
        return ""

    # Extract text from attributedString.runs — runs alternate between
    # string segments and attribute dictionaries
    runs = tsrp_json.get("attributedString", {}).get("runs", [])
    text_parts = [r for r in runs if isinstance(r, str)]
    transcript = "".join(text_parts).strip()

    if not transcript:
        log.warning(f"  Transcript was empty in {audio_path.name}")
        return ""

    return transcript

# ---------------------------------------------------------------------------
# STEP 3 — Classify with Claude
# ---------------------------------------------------------------------------

CLASSIFICATION_PROMPT_TEMPLATE = """You are an assistant that classifies voice memo transcripts.
Given a transcript, determine what the speaker intends and return structured JSON.

Today is {today_full} ({today_weekday}). Use this to resolve relative dates like
"tomorrow", "next Thursday", "this weekend", etc. into actual calendar dates.

Categories:
- "task": Something the speaker wants to do or remember to do later.
- "calendar_event": Something with a specific date/time that should go on a calendar.
- "research_query": A question or request to look something up or investigate.
- "message_draft": Something the speaker wants to send to a specific person.
- "note": A thought, idea, or observation to capture — no action needed.

Return ONLY valid JSON with this structure:
{{
  "category": "task" | "calendar_event" | "research_query" | "message_draft" | "note",
  "title": "A short (5-10 word) title summarizing this",
  "summary": "A clean, 1-2 sentence version of what the speaker said, fixing any transcription artifacts",
  "urgency": "high" | "normal" | "low",
  "people_mentioned": ["list", "of", "names"],
  "datetime_mentioned": "any date or time referenced, or null",
  "raw_transcript": "the original transcript verbatim",

  "event_start": "ISO 8601 datetime (e.g. 2026-03-16T14:00:00) if category is calendar_event, otherwise null",
  "event_duration_minutes": 60,
  "event_location": "location if mentioned, otherwise null",

  "research_question": "A clear, well-formed question to research, if category is research_query, otherwise null"
}}

For calendar_event: resolve relative dates to actual dates. If no time is given, default to
9:00 AM. If no duration is mentioned, default to 60 minutes.

For research_query: rephrase the speaker's question clearly so it can be sent directly to an
AI assistant for research.

Do not include any text outside the JSON object."""

def classify(transcript: str) -> dict:
    """Send transcript to Claude API for classification."""
    import urllib.request

    log.info("  Classifying with Claude...")

    now = datetime.datetime.now()
    system_prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(
        today_full=now.strftime("%B %d, %Y"),
        today_weekday=now.strftime("%A"),
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": f"Classify this voice memo transcript:\n\n{transcript}"}
        ],
        "system": system_prompt,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            text = result["content"][0]["text"]
            # Parse the JSON from Claude's response
            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]  # remove first line
                text = text.rsplit("```", 1)[0]  # remove last fence
            return json.loads(text)
    except Exception as e:
        log.error(f"  Classification failed: {e}")
        return {
            "category": "note",
            "title": "Unclassified memo",
            "summary": transcript[:200],
            "urgency": "normal",
            "people_mentioned": [],
            "datetime_mentioned": None,
            "raw_transcript": transcript,
        }

# ---------------------------------------------------------------------------
# STEP 4 — Route: save to inbox + create Apple Reminders for tasks
# ---------------------------------------------------------------------------

def create_apple_reminder(title: str, notes: str = ""):
    """Create a reminder in Apple Reminders app via osascript."""
    # Escape for AppleScript
    title_escaped = title.replace('"', '\\"').replace("'", "'\\''")
    notes_escaped = notes.replace('"', '\\"').replace("'", "'\\''")
    
    script = f'''
    tell application "Reminders"
        tell list "Voice Inbox"
            try
                make new reminder with properties {{name:"{title_escaped}", body:"{notes_escaped}"}}
            on error
                -- If "Voice Inbox" list doesn't exist, use default list
                tell default list
                    make new reminder with properties {{name:"{title_escaped}", body:"{notes_escaped}"}}
                end tell
            end try
        end tell
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        log.info(f"  ✅ Created reminder: {title}")
    except Exception as e:
        log.error(f"  Failed to create reminder: {e}")

def create_calendar_event(title: str, start_iso: str, duration_minutes: int = 60,
                          location: str = "", notes: str = ""):
    """Create a calendar event in Apple Calendar via osascript."""
    try:
        dt = datetime.datetime.fromisoformat(start_iso)
    except (ValueError, TypeError):
        log.warning(f"  Could not parse event date '{start_iso}', skipping calendar event")
        return

    end_dt = dt + datetime.timedelta(minutes=duration_minutes)

    title_escaped = title.replace('"', '\\"')
    notes_escaped = notes.replace('"', '\\"')
    location_escaped = location.replace('"', '\\"') if location else ""

    # Build the AppleScript date by setting components individually (reliable)
    script = f'''
    tell application "Calendar"
        set startDate to current date
        set year of startDate to {dt.year}
        set month of startDate to {dt.month}
        set day of startDate to {dt.day}
        set hours of startDate to {dt.hour}
        set minutes of startDate to {dt.minute}
        set seconds of startDate to 0

        set endDate to current date
        set year of endDate to {end_dt.year}
        set month of endDate to {end_dt.month}
        set day of endDate to {end_dt.day}
        set hours of endDate to {end_dt.hour}
        set minutes of endDate to {end_dt.minute}
        set seconds of endDate to 0

        -- Try "Voice Inbox" calendar, fall back to default
        set targetCal to missing value
        try
            set targetCal to calendar "Voice Inbox"
        end try
        if targetCal is missing value then
            set targetCal to first calendar whose name is not ""
        end if

        tell targetCal
            set newEvent to make new event with properties {{summary:"{title_escaped}", start date:startDate, end date:endDate, description:"{notes_escaped}"}}
            {f'set location of newEvent to "{location_escaped}"' if location_escaped else ""}
        end tell
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True,
                                text=True, timeout=15)
        if result.returncode == 0:
            log.info(f"  📅 Created calendar event: {title} ({dt.strftime('%b %d, %I:%M %p')})")
        else:
            log.error(f"  Calendar event failed: {result.stderr.strip()}")
    except Exception as e:
        log.error(f"  Failed to create calendar event: {e}")


# ---------------------------------------------------------------------------
# STEP 4b — Research: ask Claude a question and return the answer
# ---------------------------------------------------------------------------

def research_with_claude(question: str) -> str:
    """Send a research question to Claude and return the answer."""
    import urllib.request

    log.info(f"  🔍 Researching: {question[:80]}...")

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": question}
        ],
        "system": (
            "You are a helpful research assistant. Give a clear, concise, and accurate "
            "answer to the user's question. If you're uncertain about something, say so. "
            "Keep your answer to 2-4 paragraphs unless the question requires more detail."
        ),
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
            return result["content"][0]["text"]
    except Exception as e:
        log.error(f"  Research query failed: {e}")
        return f"(Research failed: {e})"


def save_to_inbox(classification: dict, audio_filename: str):
    """Append the classified memo to the daily inbox markdown file."""
    today = datetime.date.today().isoformat()
    inbox_file = INBOX_DIR / f"{today}.md"

    timestamp = datetime.datetime.now().strftime("%I:%M %p")
    cat = classification.get("category", "note")
    cat_emoji = {
        "task": "☑️",
        "calendar_event": "📅",
        "research_query": "🔍",
        "message_draft": "✉️",
        "note": "📝",
    }.get(cat, "📝")

    entry = f"""
---

### {cat_emoji} {classification.get('title', 'Untitled')}

**Category:** {cat} · **Urgency:** {classification.get('urgency', 'normal')} · **Time:** {timestamp}  
**Source:** {audio_filename}

{classification.get('summary', '')}

{f"**People:** {', '.join(classification['people_mentioned'])}" if classification.get('people_mentioned') else ""}
{f"**Date/time referenced:** {classification['datetime_mentioned']}" if classification.get('datetime_mentioned') else ""}
{f"**Event start:** {classification['event_start']}" if classification.get('event_start') else ""}
{f"**Location:** {classification['event_location']}" if classification.get('event_location') else ""}

<details><summary>Raw transcript</summary>

{classification.get('raw_transcript', '')}

</details>
"""

    # If there's a research answer, append it
    if classification.get("_research_answer"):
        entry += f"""
> **Research Answer:**
>
> {classification['_research_answer'].replace(chr(10), chr(10) + '> ')}

"""

    # If file doesn't exist yet, add a header
    if not inbox_file.exists():
        header = f"# Voice Inbox — {today}\n\nProcessed memos from today.\n"
        inbox_file.write_text(header)

    with open(inbox_file, "a") as f:
        f.write(entry)

    log.info(f"  📝 Saved to inbox: {inbox_file.name}")

def route(classification: dict, audio_filename: str):
    """Route the classified memo to the right destination."""
    cat = classification.get("category")

    # --- Calendar events → Apple Calendar ---
    if cat == "calendar_event":
        create_calendar_event(
            title=classification.get("title", "Voice memo event"),
            start_iso=classification.get("event_start", ""),
            duration_minutes=classification.get("event_duration_minutes", 60),
            location=classification.get("event_location", "") or "",
            notes=classification.get("summary", ""),
        )

    # --- Research queries → Ask Claude, then save answer + create reminder ---
    elif cat == "research_query":
        question = classification.get("research_question") or classification.get("summary", "")
        answer = research_with_claude(question)
        classification["_research_answer"] = answer

        # Truncate answer for reminder notes (Reminders has limits)
        short_answer = answer[:500] + ("..." if len(answer) > 500 else "")
        create_apple_reminder(
            title=f"🔍 {classification.get('title', 'Research result')}",
            notes=f"Q: {question}\n\nA: {short_answer}",
        )

    # --- Tasks → Apple Reminders ---
    elif cat == "task":
        create_apple_reminder(
            title=classification.get("title", "Voice memo task"),
            notes=classification.get("summary", ""),
        )

    # --- Message drafts → just log (use Siri for sending) ---
    elif cat == "message_draft":
        log.info(f"  ✉️  Message draft logged for: {', '.join(classification.get('people_mentioned', ['unknown']))}")

    # Always save to the markdown inbox (after research so answer is included)
    save_to_inbox(classification, audio_filename)

# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def process_memo(memo_path: Path):
    """Full pipeline for one voice memo."""
    log.info(f"📎 Processing: {memo_path.name}")

    # Read Apple's on-device transcription
    transcript = transcribe(memo_path)
    if not transcript:
        # Don't mark as processed — Apple may still be transcribing.
        # We'll retry on the next poll cycle.
        log.warning(f"  No transcript yet, will retry: {memo_path.name}")
        return

    log.info(f"  Transcript: {transcript[:100]}...")

    # Classify
    classification = classify(transcript)
    classification["raw_transcript"] = transcript  # ensure we always have it

    log.info(f"  Category: {classification.get('category')} — {classification.get('title')}")

    # Route
    route(classification, memo_path.name)

    # Mark as done
    mark_processed(file_id(memo_path))
    log.info(f"  ✅ Done: {memo_path.name}")

def run():
    """Main polling loop."""
    log.info("=" * 60)
    log.info("Voice Inbox started")
    log.info(f"Watching: {VOICE_MEMOS_DIR}")
    log.info(f"Inbox:    {INBOX_DIR}")
    log.info(f"Polling every {POLL_INTERVAL}s")
    log.info("=" * 60)

    # Check the folder exists on first run
    if not VOICE_MEMOS_DIR.exists():
        log.error(f"\n❌  Voice Memos folder not found at:\n   {VOICE_MEMOS_DIR}\n")
        log.error("This usually means either:")
        log.error("  1. iCloud Drive isn't enabled on this Mac")
        log.error("  2. Voice Memos hasn't synced yet")
        log.error("  3. You haven't recorded any Voice Memos on your phone")
        log.error("\nTo fix: Open System Settings → Apple ID → iCloud → iCloud Drive")
        log.error("and make sure it's turned on. Then record a test memo on your phone.\n")
        sys.exit(1)

    while True:
        try:
            new_memos = find_new_memos()
            if new_memos:
                log.info(f"Found {len(new_memos)} new memo(s)")
                for memo in new_memos:
                    process_memo(memo)
            # Silently poll (no log spam when idle)
        except KeyboardInterrupt:
            log.info("\nStopping Voice Inbox.")
            break
        except Exception as e:
            log.error(f"Error in main loop: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
