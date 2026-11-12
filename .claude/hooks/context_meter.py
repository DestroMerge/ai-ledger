"""The context meter, and the three hooks that make a summary of the conversation safe.

A session costs by its length: each step re-reads the conversation so far (the manual, "Session length"). A project
caps its conversation in .claude/settings.json (`autoCompactWindow`, tokens) and Claude Code summarises it there. A
summary throws detail away, and without these what it keeps is whatever the session happened to write down. With
them the ledger carries it:

    python .claude/hooks/context_meter.py           on call: how large this project's newest session is, against the cap
    python .claude/hooks/context_meter.py warn      PostToolUse hook: the early warning
    python .claude/hooks/context_meter.py brief     PreCompact hook: the summary's brief
    python .claude/hooks/context_meter.py reentry   SessionStart hook: the re-entry
    python .claude/hooks/context_meter.py log       Stop hook: one line a reply in the context log

- **The meter** (`last_step`) reads the last step's token counts from the transcript's tail: what it read plain, from
  cache and wrote to cache, summed. That is the conversation as that step saw it.
- **The early warning** says once, at 80% of the cap, to write each task's note now, while the session still knows
  what it measured. It is said again only after a summary has taken the conversation back under 60%.
- **The summary's brief** prints, from the ledger, the tasks this session holds, the newest note of the most lately
  touched, and the files with uncommitted changes. Claude Code uses a PreCompact hook's output as instructions for
  the summary.
- **The re-entry** prints the same after a summary, a clear, a start or a resume, and Claude Code adds a
  SessionStart hook's output to the conversation: the new context begins from the ledger, not only from the summary.
- **The log** appends {"at", "session", "tokens", "cap"} to the file `contextLog` names in ledger_config.json, a path
  from the repository's root. With no such key it does nothing.

Each is registered in the project's own .claude/settings.json, which update_ledger.py never touches; the manual,
"Session length", has the block. The cap is read from the project's .claude/settings.local.json, else its
.claude/settings.json, else the user's own; with none set, the warning says nothing.

The token counts (`message.usage`) are not documented by Claude Code. When a session's steps carry none, the warning
hook says so once per session; `ledger.py selftest` runs these readers on transcript lines of the shape they expect.
Any failure says nothing and lets the tool result, the summary or the session through: a PreCompact hook that exits
2 would block the summary, so none of these ever does.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
REPO = HOOKS_DIR.parents[1]
LEDGER_DIR = REPO / "Docs" / "Ledger"
TAIL_BYTES = 2 * 1024 * 1024
WARN_AT = 0.8     # of the cap: the warning is given
REARM_AT = 0.6    # of the cap: under this the conversation has been summarised, and the warning may be given again
COUNT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
FULL_NOTES = 4    # the held tasks whose newest note is given
NOTE_CHARS = 1200
MAX_FILES = 30
MAX_OTHERS = 6
VCS_SECONDS = 8   # how long the version control is waited for
# A note's stamp, as ledger.py writes one: "2026-11-12 12:38. " or "2026-11-12 12:38 PT. ".
RE_STAMP = re.compile(r"(?:^| )(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?: [A-Za-z][A-Za-z0-9+:\-]{0,9})?\. ")
SOURCES = {"startup": "begins", "resume": "is resumed", "clear": "begins again after a clear",
           "compact": "goes on after a summary", "fork": "begins as a fork"}


def last_step(transcript: Path, tail: int = TAIL_BYTES) -> tuple[int | None, bool]:
    """(the tokens the session's last step read, whether its steps carry no token counts).

    A step is a main-thread assistant entry. A subagent's (isSidechain) is skipped, and so is one with every count at
    0, which Claude Code writes for a message of its own making. The first is None when the tail holds no step with
    counts; the second is True when it holds three steps or more and none has any."""
    with transcript.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail))
        lines = f.read().split(b"\n")
    if size > tail:
        lines = lines[1:]
    without = 0
    for raw in reversed(lines):
        if b'"assistant"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage")
        if not isinstance(usage, dict) or not any(k in usage for k in COUNT_KEYS):
            without += 1
            continue
        total = sum(int(usage.get(k) or 0) for k in COUNT_KEYS)
        if total > 0:
            return total, False
    return None, without >= 3


def cap(repo: Path = REPO, home: Path | None = None) -> int | None:
    """The context cap, tokens: the first `autoCompactWindow` among the project's local settings, its settings and
    the user's. None when none sets one."""
    home = home if home is not None else Path.home()
    for path in (repo / ".claude" / "settings.local.json", repo / ".claude" / "settings.json",
                 home / ".claude" / "settings.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("autoCompactWindow")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def due(tokens: int, limit: int, warned: bool) -> tuple[bool, bool]:
    """(whether the early warning is given now, whether it then counts as given). Once each time the conversation
    fills: at WARN_AT of the cap, and not again until a summary has taken it back under REARM_AT."""
    if tokens < limit * REARM_AT:
        return False, False
    if tokens >= limit * WARN_AT and not warned:
        return True, True
    return False, warned


def touched(t: dict) -> str:
    """When a task was last touched, to the minute: the newest stamp among its notes and its own dates."""
    stamps = [m.group(1) for m in RE_STAMP.finditer(t.get("notes") or "")]
    stamps += [str(t[k])[:16] for k in ("startedAt", "blockedAt", "dispatchedAt", "submittedAt") if t.get(k)]
    return max(stamps) if stamps else ""


def last_note(notes: str, limit: int = NOTE_CHARS) -> str:
    """A task's newest note, from its last stamp on; over the limit, its end is kept, where what is left is said."""
    notes = notes or ""
    found = list(RE_STAMP.finditer(notes))
    text = " ".join((notes[found[-1].start():] if found else notes).split())
    return text if len(text) <= limit else "... " + text[-limit:]


def held(led: dict, session: str) -> list[dict]:
    """The tasks in progress that this session started or dispatched, the most lately touched first."""
    if not session:
        return []
    mine = [t for t in led.get("tasks", []) if t.get("status") == "in-progress"
            and session in (t.get("session"), t.get("dispatchedSession"))]
    return sorted(mine, key=touched, reverse=True)


def state(led: dict, session: str, changed: list[str] | None) -> list[str]:
    """The ledger's state for one session, as lines: the tasks it holds, with the newest note of the most lately
    touched; when it holds none, the tasks in progress; and the files with uncommitted changes (None: the version
    control could not be asked)."""
    mine = held(led, session)
    out = []
    if mine:
        out.append(f"This session holds {len(mine)} ledger task{'' if len(mine) == 1 else 's'} in progress, the most "
                   "lately touched first:")
        for t in mine[:FULL_NOTES]:
            note = last_note(t.get("notes", ""))
            out.append(f"  {t['id']} - {t.get('title', '')}")
            out.append(f"    its newest note: {note}" if note else "    it has no note yet")
        if len(mine) > FULL_NOTES:
            out.append("  and, touched before those: " + ", ".join(t["id"] for t in mine[FULL_NOTES:]))
    else:
        others = sorted((t for t in led.get("tasks", []) if t.get("status") == "in-progress"), key=touched, reverse=True)
        if others:
            out.append(f"This session holds no ledger task. In progress, the most lately touched first "
                       f"({len(others)} in all):")
            out += [f"  {t['id']} - {t.get('title', '')} ({t.get('heldBy') or 'no holder recorded'})"
                    for t in others[:MAX_OTHERS]]
        else:
            out.append("This session holds no ledger task, and none is in progress.")
    if changed is None:
        out.append("Uncommitted changes: the version control could not be asked.")
    elif changed:
        more = f", and {len(changed) - MAX_FILES} more" if len(changed) > MAX_FILES else ""
        out.append(f"Files with uncommitted changes ({len(changed)}): " + ", ".join(changed[:MAX_FILES]) + more)
    else:
        out.append("No file has uncommitted changes.")
    return out


def warning(tokens: int, limit: int, mine: list[dict]) -> str:
    """What the early warning says."""
    tasks = (" This session holds: " + ", ".join(t["id"] for t in mine[:12])
             + (f" and {len(mine) - 12} more" if len(mine) > 12 else "") + ".") if mine else ""
    return (f"THE CONVERSATION IS AT {tokens:,} OF ITS CAP OF {limit:,} TOKENS ({tokens / limit:.0%}). Claude Code "
            "summarises it at the cap, and a summary keeps only part of it.\n"
            "Before the next long step, write on each task you are working, with `python Docs/Ledger/ledger.py note "
            "<task> --text \"...\"`, what is measured, decided or half-done and is not yet in its notes: the numbers, "
            "the cause found, what is left to do, and any data changed for a test and not put back." + tasks + "\n"
            "Then go on with the work. This is said once each time the conversation fills.")


def brief(led: dict, session: str, changed: list[str] | None) -> str:
    """What the summary is told to keep."""
    return "\n".join(
        ["From the task ledger (Docs/Ledger), which outlives this conversation. Keep in the summary:",
         "- for each task below that this session was working: what was being done to it, and the next step;",
         "- whatever was measured, decided or half-changed since that task's newest note: it is nowhere else;",
         "- each file with uncommitted changes, and whether it is this session's;",
         "- the owner's last instruction, and any question still open to them, in their own words."]
        + state(led, session, changed)
        + ["After the summary the ledger's state is handed back as it is then. What only this conversation knows is "
           "not: say it in the summary."])


def reentry(led: dict, session: str, changed: list[str] | None, source: str) -> str:
    """What a session is handed as it begins, or goes on after a summary."""
    lines = [f"The task ledger's state as this conversation {SOURCES.get(source, 'begins')} (Docs/Ledger; `python "
             "Docs/Ledger/ledger.py next` shows the whole picture):"] + state(led, session, changed)
    if held(led, session):
        lines.append("Before going on with a task, read it: `python Docs/Ledger/ledger.py show <task>`. Its notes are "
                     "the record; a summary of this conversation is not.")
    else:
        lines.append("A task is taken with `python Docs/Ledger/ledger.py start <task>`; one another session holds, "
                     "only when that session is gone, with `--takeover \"<why>\"`.")
    return "\n".join(lines)


def read_ledger() -> tuple[dict, list[str] | None]:
    """(the ledger, the files with uncommitted changes or None when the version control did not answer in time)."""
    sys.path.insert(0, str(LEDGER_DIR))
    import ledger as L

    led = L.load(track=False)
    got: dict = {}

    def ask() -> None:
        try:
            got["changed"] = sorted(L.changed_paths("."))
        except Exception:  # noqa: BLE001 - no version control here, or it refused
            pass

    asking = threading.Thread(target=ask, daemon=True)
    asking.start()
    asking.join(VCS_SECONDS)
    return led, got.get("changed")


def state_file(session: str) -> Path:
    return Path(tempfile.gettempdir()) / f"claude_context_meter_{session or 'unknown'}.json"


def hook_warn(payload: dict, limit: int | None = None, ledger=None) -> dict | None:
    """PostToolUse: the early warning, and the notice that the token counts are gone. Each call leaves what it read
    in the session's state file, which is also how to see that the hook runs. The selftest hands it a cap and a
    ledger of its own."""
    if payload.get("agent_id"):
        return None
    transcript = Path(payload.get("transcript_path") or "")
    if not transcript.is_file():
        return None
    session = str(payload.get("session_id") or "")
    tokens, missing = last_step(transcript)
    mark = state_file(session)
    try:
        st = json.loads(mark.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    out: dict = {}
    if missing and not st.get("toldMissing"):
        st["toldMissing"] = True
        out["systemMessage"] = ("Context meter: this session's steps carry no token counts ('usage'), so Claude Code "
                                "has changed the field it reads; the warning before a summary is not being given. "
                                "Update .claude/hooks/context_meter.py.")
    limit = limit if limit is not None else cap()
    if tokens is not None and limit:
        say, st["warned"] = due(tokens, limit, bool(st.get("warned")))
        if say:
            try:
                mine = held((ledger or read_ledger)()[0], session)
            except Exception:  # noqa: BLE001 - the warning is still given when the ledger cannot load
                mine = []
            out["hookSpecificOutput"] = {"hookEventName": "PostToolUse",
                                         "additionalContext": warning(tokens, limit, mine)}
    st.update(tokens=tokens, cap=limit, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        mark.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass
    return out or None


def log_file() -> Path | None:
    """The context log: the path ledger_config.json names as `contextLog`, from the repository's root; None with no
    such key."""
    try:
        rel = json.loads((LEDGER_DIR / "ledger_config.json").read_text(encoding="utf-8")).get("contextLog")
    except (OSError, ValueError, AttributeError):
        return None
    return REPO / rel if rel and isinstance(rel, str) else None


def hook_log(payload: dict, path: Path | None = None) -> None:
    """Stop: one line a reply in the context log. The selftest hands it a file of its own."""
    transcript = Path(payload.get("transcript_path") or "")
    path = path or log_file()
    if path is None or not transcript.is_file() or payload.get("agent_id"):
        return
    tokens, _ = last_step(transcript)
    if tokens is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": payload.get("session_id"),
            "tokens": tokens, "cap": cap()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def say(text: str) -> None:
    """Plain text on stdout as UTF-8, whatever the console's own code page is."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def on_call() -> int:
    """How large the newest session of the project this is run in is, against the cap."""
    import os
    folder = Path.home() / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in os.getcwd())
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        say(f"context_meter.py: no transcript in {folder}")
        return 1
    tokens, missing = last_step(files[-1])
    limit = cap()
    say(str(files[-1]))
    if tokens is None:
        say("its steps carry no token counts: the transcript may no longer write them" if missing
            else "no step with token counts in its last part")
        return 1
    say(f"its last step read {tokens:,} tokens"
        + (f", {tokens / limit:.0%} of the cap of {limit:,}" if limit else "; no cap is set (autoCompactWindow)"))
    return 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if not mode:
        return on_call()
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    if mode == "warn":
        out = hook_warn(payload)
        if out:
            print(json.dumps(out))
    elif mode == "log":
        hook_log(payload)
    elif mode in ("brief", "reentry"):
        if payload.get("agent_id"):
            return 0
        led, changed = read_ledger()
        session = str(payload.get("session_id") or "")
        say(brief(led, session, changed) if mode == "brief"
            else reentry(led, session, changed, str(payload.get("source") or "")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:  # never get in the way of a tool result, a summary or a session
        sys.exit(0)
