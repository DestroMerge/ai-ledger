"""Stop hook: a session that works the ledger may not end its reply while it has ledger work it can do without the owner.

Claude Code runs this every time Claude is about to end a reply (registered under "Stop" in
.claude/settings.json). It reads the hook's JSON on stdin and prints its answer as JSON on stdout.

- It acts only in a session that has worked the ledger: one that ran `ledger.py start` or `ledger.py dispatch` on a
  task, and the ledger shows it took (the task's startedAt or dispatchedAt is no earlier than the command). A session
  that only answers a question, or only quotes those commands, is never pushed into ledger work.
- When there is work, it blocks the ending once and hands Claude a short list. `stop_hook_active` is true when
  Claude is already continuing because of this hook; the reply then ends normally, so the hook can never loop.
- Any failure lets the reply end and tells the owner why in one line. It never traps a session.

Why it exists: a rule written in CLAUDE.md or in memory ("run ledger.py next before ending") kept being skipped.
A hook runs every time, which a written rule does not. It also does three related checks:
- Messages the owner sends while Claude works come before ledger work: a message sent mid-reply blocks the ending
  once with the message quoted. A question is answered at the top and the reply ends; an instruction or a report
  ("do X", "this looks wrong") is acknowledged in one line and done in the same reply. The after-each-step half
  of this is owner_message_check.py.
- A reply the owner started is never pushed straight into ledger work, unless they said to keep working while away.
- A reply that ends by promising work ("I'll fix ...") is blocked once: do it, or name what blocks it.
A task id is matched with any prefix (the ledger then checks it), and the owner named in these messages is the one
in Docs/Ledger/ledger_config.json.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parents[2] / "Docs" / "Ledger"
# Python running ledger.py on a task. A grep or an echo that only names the command does not match; a quoted copy
# inside a script can, which is why each match is then checked against the ledger.
RE_WORKED = re.compile(r"(?:^|[\s;&|(])(?:python3?|py)(?:\.exe)?\s+(?:-\S+\s+)*[\"']?[^\s\"']*ledger\.py[\"']?\s+"
                       r"(start|dispatch)\s+[\"']?([A-Z][A-Z0-9]{0,7}-E\d{2}\.\d{2})\b")
STAMP_FIELD = {"start": "startedAt", "dispatch": "dispatchedAt"}
SLACK = timedelta(minutes=2)  # ledger stamps are whole minutes; transcript times are to the millisecond
MAX_LINES = 6


def owner_name() -> str:
    """The project's owner, read from ledger_config.json without importing ledger.py: the message checks in main()
    must still run when the ledger tool cannot load."""
    try:
        return json.loads((LEDGER_DIR / "ledger_config.json").read_text(encoding="utf-8")).get("owner") or "the owner"
    except (OSError, ValueError, AttributeError):
        return "the owner"


def ledger_commands(transcript: Path) -> list[tuple[str, str, datetime]]:
    """Every `ledger.py start|dispatch <task>` this session's own tool calls ran: (verb, task id, local time).

    Subagent lines (isSidechain) are skipped, so a department agent's own `start` never counts for the hub."""
    out = []
    with transcript.open("rb") as f:
        for raw in f:
            if b"ledger.py" not in raw:
                continue
            try:
                entry = json.loads(raw)
                when = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            for block in (entry.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                command = (block.get("input") or {}).get("command")
                out += [(verb, task, when) for verb, task in RE_WORKED.findall(command if isinstance(command, str) else "")]
    return out


def message_text(content) -> str:
    """The text of a message's content: a string, or the text blocks of a list (images and tool results skipped)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def this_reply(transcript: Path) -> tuple[str | None, str, list[str]]:
    """(who started the current reply, the text that started it, what the owner wrote while it ran).

    A reply starts at the last main-thread `user` entry that carries a turnOrigin ("human", "task_notification",
    "peer"...). Tool results and this hook's own block feedback carry none, so a block never starts a new reply. A
    message the owner sent mid-reply is an `attachment` of type queued_command whose origin kind is "human"."""
    origin, start, mid = None, "", []
    with transcript.open("rb") as f:
        for raw in f:
            if b"turnOrigin" not in raw and b"queued_command" not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if entry.get("isSidechain"):
                continue
            if entry.get("type") == "user" and entry.get("turnOrigin"):
                origin, start, mid = entry["turnOrigin"], message_text((entry.get("message") or {}).get("content")), []
            elif entry.get("type") == "attachment":
                att = entry.get("attachment") or {}
                if att.get("type") == "queued_command" and (att.get("origin") or {}).get("kind") == "human":
                    text = message_text(att.get("prompt")).strip()
                    if text:
                        mid.append(text)
    return origin, start, mid


PROMISE = re.compile(r"\b(?:I'll|I will|I'm going to|I am going to|next I|then I)\s+"
                     r"(?:start|do|fix|run|look|check|find|tone|let|add|make|build|go|work|get|move|pick|begin|redo|re-do|set)\b",
                     re.IGNORECASE)


def promised_work(transcript: Path, tail: int = 2 * 1024 * 1024) -> str:
    """The promise ("I'll fix ...") in the reply's last assistant text, or "" - read from the transcript's tail.

    A reply that ends with "I'll fix ..." and stops leaves the work undone. The block is once per reply; when the work really is blocked, the second try names
    the blocker and ends."""
    with transcript.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail))
        raw_lines = f.read().split(b"\n")
    for raw in reversed(raw_lines):
        if b'"assistant"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content") or []
        if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            return ""   # the reply's last act was a tool call, not a closing message
        text = message_text(content)
        if not text.strip():
            continue
        m = PROMISE.search(text)
        return text[max(0, m.start() - 20):m.end() + 60] if m else ""
    return ""


def quoted(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return '"' + (text if len(text) <= limit else text[:limit] + " ...") + '"'


def took_effect(task: dict | None, verb: str, when: datetime) -> bool:
    """True when the ledger's stamp for this verb is no earlier than the command - so this command really did it."""
    try:
        stamped = datetime.strptime(str((task or {}).get(STAMP_FIELD[verb]))[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return stamped >= when - SLACK


def hub_work(L, led: dict, started: set[str]) -> list[str]:
    """One line per piece of ledger work the hub can do without the owner, in the order `ledger.py next` puts it first."""
    lines = [f"APPLY     {d['id']} - {L.OWNER} answered it on the website" for d, _ in L.waiting_answers(led)]
    sent = [t for t in led["tasks"] if L.is_subtask(t) and t.get("dispatchedAt")]
    lines += [f"CONFIRM   {t['id']} - handed in by {t['owner']}: {t['title']}"
              for t in sent if t["status"] == "in-progress" and t.get("submittedAt")]
    own = [t for t in led["tasks"] if t["status"] == "in-progress" and t not in sent]
    lines += [f"CONTINUE  {t['id']} - you started it this session: {t['title']}" for t in own if t["id"] in started]
    lines += [f"CONTINUE  {t['id']} - in progress, untouched since {L.last_touched(t) or 'never'} "
              f"(nobody's unless a note names who holds it): {t['title']}"
              for t in own if t["id"] not in started and "STALE?" in L.age_text(L.last_touched(t))]
    wave = L.current_wave(led)
    ready = [t for t in L.ready_tasks(led) if t.get("owner") != L.OWNER and not t.get(L.NEEDS_OWNER)]
    now = [t for t in ready if t["wave"] == wave]
    for t in now or sorted((t for t in ready if wave is not None and t["wave"] > wave),
                           key=lambda t: (t["wave"], t["id"])):
        verb = "DISPATCH" if L.is_subtask(t) else "READY   "
        lines.append(f"{verb}  {t['id']} (wave {t['wave']}) - {t['title']}")
    return lines


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    if payload.get("stop_hook_active"):
        return 0
    transcript = Path(payload.get("transcript_path") or "")
    if not transcript.is_file():
        return 0
    owner = owner_name()
    origin, start, mid = this_reply(transcript)
    promise = promised_work(transcript)
    promise_line = (f"\nYour last message ends by promising work ({quoted(promise, 120)}) that is not running. Either "
                    f"do it now, in this reply, or replace the promise with exactly what blocks it ({owner}'s answer, "
                    "the editor, a build)." if promise else "")
    if mid:
        # Questions end the reply; instructions are done in it (see the docstring).
        reason = (f"{owner} wrote while you were working:\n"
                  + "\n".join(f"  {quoted(m)}" for m in mid)
                  + "\nA QUESTION: answer it at the top of your final message, in plain words, and end the reply there "
                    "- no more work in it. An INSTRUCTION or a report: one line at the top saying you have it, then do "
                    "it in this reply before ending. If your last message already answers every question and nothing "
                    f"{owner} told you to do is left undone or unblocked, end now." + promise_line)
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    owner_started = origin == "human" and not start.startswith("This session is being continued")
    commands = ledger_commands(transcript)
    if not commands:
        if promise:
            print(json.dumps({"decision": "block", "reason": promise_line.strip()}))
        return 0
    sys.path.insert(0, str(LEDGER_DIR))
    import ledger as L

    led = L.load(track=False)
    tasks = L.index(led)[1]
    done = [(verb, task) for verb, task, when in commands if took_effect(tasks.get(task), verb, when)]
    lines = hub_work(L, led, {task for verb, task in done if verb == "start"}) if done else []
    if not lines:
        if promise:
            print(json.dumps({"decision": "block", "reason": promise_line.strip()}))
        return 0
    shown = lines[:MAX_LINES]
    if len(lines) > MAX_LINES:
        shown.append(f"... and {len(lines) - MAX_LINES} more")
    if owner_started:
        reason = (f"{owner} started this reply: {quoted(start)}\n"
                  "If that ASKS something, your final message answers it at the top and the reply ends - do not start "
                  "ledger work. If it TELLS you to do something or reports a problem, doing that comes first: do it in "
                  "this reply and end only when it is done or running, or name exactly what blocks it. Only if "
                  f"{owner} told you to work on while away, then start the first of these (`python "
                  "Docs/Ledger/ledger.py next` shows the full picture):\n"
                  + "\n".join(f"  {s}" for s in shown)
                  + "\nThis check blocks once per reply." + promise_line)
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    reason = (f"Ledger check: this session works the ledger, and it has work you can do without {owner}:\n"
              + "\n".join(f"  {s}" for s in shown)
              + "\nStart the first one now; `python Docs/Ledger/ledger.py next` shows the full picture. End the reply "
                f"only if {owner} told you to stop, or each of these needs {owner}: then say which in one line. This "
                "check blocks once per reply." + promise_line)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never trap a session: let the reply end and say why
        print(json.dumps({"systemMessage": f"Ledger stop hook failed, so the ledger was not checked: "
                                           f"{type(e).__name__}: {e}"}))
        sys.exit(0)
