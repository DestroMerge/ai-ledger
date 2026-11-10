"""PostToolUse hook: when the owner sends a message while Claude is working, tell Claude after its very next tool call to
stop and answer them.

Claude Code runs this after every tool call (registered under "PostToolUse" in .claude/settings.json). It reads
the hook's JSON on stdin and, only when there is something to say, prints JSON on stdout whose additionalContext is
shown to Claude beside the tool's result.

- The owner's message is the transcript's `queue-operation` "enqueue" line, written the moment it is sent. A message that
  started the current reply has a main-thread `user` entry with the same text after it and is skipped; one that is
  still only queued, or was absorbed mid-reply, is theirs to answer. Task notifications and agent reports are
  queued too, but their text starts with "<", and the owner's does not.
- Each message is flagged once (a small state file per session in the temp folder), and only within 15 minutes of
  being sent, so installing the hook never flags old messages.
- A subagent's tool calls (the payload carries agent_id) are skipped: the message is the main thread's to answer.
- Only the last 4 MB of the transcript are read, so a call costs a fraction of a second on an 80 MB transcript.
- Any failure says nothing and lets the tool result through. It never blocks a tool.

Why it exists: Claude can run many tool calls in one reply, and a question the owner typed meanwhile waited until
the whole reply was done. Paired with ledger_stop.py, which checks again when the reply is about to end. The
matching rule goes in CLAUDE.md (see CLAUDE.example.md, "Replies").
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAIL_BYTES = 4 * 1024 * 1024
CONFIG = Path(__file__).resolve().parents[2] / "Docs" / "Ledger" / "ledger_config.json"
FRESH = timedelta(minutes=15)


def owner_name() -> str:
    """The owner named in ledger_config.json, so the message says who wrote; "the user" when it cannot be read."""
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("owner") or "the user"
    except (OSError, ValueError, AttributeError):
        return "the user"


def when(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None


def tail_entries(transcript: Path) -> list[dict]:
    """The transcript's last TAIL_BYTES as parsed entries; the first, cut line is dropped."""
    with transcript.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read()
    lines = data.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]
    out = []
    for raw in lines:
        if b"queue-operation" not in raw and b"turnOrigin" not in raw:
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def unanswered(entries: list[dict], now: datetime) -> list[tuple[str, str]]:
    """(timestamp, text) of each message the owner sent during the current reply that did not start it, sent within FRESH.

    The current reply starts at the last main-thread `user` entry carrying a turnOrigin; a message sent in an earlier
    reply was that reply's to answer (the first live run flagged one from the reply before)."""
    starts = [i for i, e in enumerate(entries)
              if e.get("type") == "user" and e.get("turnOrigin") and not e.get("isSidechain")]
    sent = []
    for i, e in enumerate(entries):
        if starts and i < starts[-1]:
            continue
        if e.get("type") != "queue-operation" or e.get("operation") != "enqueue":
            continue
        text = text_of(e.get("content")).strip()
        at = when(e)
        if not text or text.startswith("<") or at is None or now - at > FRESH:
            continue
        started_reply = any(later.get("type") == "user" and later.get("turnOrigin") and not later.get("isSidechain")
                            and text_of((later.get("message") or {}).get("content")).strip() == text
                            for later in entries[i + 1:])
        if not started_reply and text not in [t for _, t in sent]:
            sent.append((str(e.get("timestamp")), text))
    return sent


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    if payload.get("agent_id"):
        return 0
    transcript = Path(payload.get("transcript_path") or "")
    if not transcript.is_file():
        return 0
    found = unanswered(tail_entries(transcript), datetime.now(timezone.utc))
    if not found:
        return 0
    state = Path(tempfile.gettempdir()) / f"claude_owner_flagged_{payload.get('session_id') or 'unknown'}.json"
    try:
        flagged = set(json.loads(state.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        flagged = set()
    new = [(ts, text) for ts, text in found if ts not in flagged]
    if not new:
        return 0
    state.write_text(json.dumps(sorted(flagged | {ts for ts, _ in new})), encoding="utf-8")
    lines = "\n".join("  \"" + (" ".join(t.split())[:400]) + "\"" for _, t in new)
    # Two kinds of message, not one: treating instructions like questions ("answer and end") made Claude reply
    # "I'll do it" and stop.
    context = (f"{owner_name().upper()} JUST WROTE TO YOU:\n" + lines + "\n"
               "If it ASKS something: stop the work you are doing; your next message answers it at the top, in plain "
               "words, and ends the reply - make no more tool calls than the answer itself needs. Do not write "
               "\"I'll answer at the end\". The work picks up after.\n"
               "If it TELLS you to do something or reports a problem: say in one line at the top of your next message "
               "that you have it, then do it now, in this reply. Never answer an instruction with \"I'll do it\" and stop: "
               "end only when it is done or running, or name exactly what blocks it (their answer, a build, a missing tool).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never get in the way of a tool result
        sys.exit(0)
