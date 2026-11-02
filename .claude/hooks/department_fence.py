"""PreToolUse hook for the department agents: refuses the commands that belong to the hub.

Each agent file in .claude/agents/ registers this in its frontmatter `hooks:`, so it runs before every shell command
and file write that agent makes, and only while that agent runs. It reads the hook's JSON on stdin; to refuse, it
prints a PreToolUse "deny" with the reason, which the agent sees.

What it refuses:
- `ledger.py` with any subcommand but the agent's seven: show, next, findings, start, finding, submit, block.
- Python that imports the ledger module, which would change the ledger without going through those commands.
- Running `claude` itself, and committing (`dv commit` or `git commit`): an agent starts no agents and commits nothing.
- A Write or Edit to anything under Docs/Ledger/ (the epic files, meta.json, LEDGER.md).

It matches real invocations, at the start of a command or after `;`, `&&`, `|` and the like - a grep or an echo
that only names one passes. On an internal error it lets the call through and says why in one line: a broken fence
must not stall every department run.

Why a hook: instructions and a permission allowlist alone did not hold. Auto mode never prompts on a command missing
from the allowlist, so nothing actually stopped an agent running `done`, `confirm` or `commit`.
"""
from __future__ import annotations

import json
import re
import sys

AGENT_COMMANDS = {"show", "next", "findings", "start", "finding", "submit", "block"}
SHELL_TOOLS = {"Bash", "PowerShell"}
FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}

# Where a command can begin: the start of a line, or after ; & | or ( - so `cd X && python ...` matches at the
# second & - with an optional `timeout N` in front. PATH_PREFIX lets the program be named by a full path.
START = r"(?:^|[;&|(\n])\s*(?:timeout\s+\d+\s+)?"
PATH_PREFIX = r"[\"']?(?:[^\s\"';&|]*[\\/])?"
PYTHON = PATH_PREFIX + r"(?:python3?|py)(?:\.exe)?[\"']?\s+"
RE_LEDGER = re.compile(START + PYTHON + r"(?:-\S+\s+)*[\"']?[^\s\"']*ledger\.py[\"']?\s+([a-z][\w-]*)", re.MULTILINE)
RE_PYTHON = re.compile(START + PYTHON, re.MULTILINE)
RE_IMPORT = re.compile(r"\bimport\s+ledger\b|\bfrom\s+ledger\s+import\b")
RE_CLAUDE = re.compile(START + r"(?:npx\s+)?" + PATH_PREFIX + r"claude(?:\.exe|\.cmd)?[\"']?(?=\s|$)",
                       re.MULTILINE | re.IGNORECASE)
RE_COMMIT = re.compile(START + PATH_PREFIX + r"(?:dv|git)(?:\.exe)?[\"']?\s+commit\b", re.MULTILINE | re.IGNORECASE)
RE_LEDGER_PATH = re.compile(r"(?:^|[\\/])Docs[\\/]Ledger[\\/]", re.IGNORECASE)


def refusal(tool: str, tool_input: dict) -> str | None:
    """Why this call is the hub's and not the agent's, or None when the agent may make it."""
    if tool in FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if RE_LEDGER_PATH.search(path):
            return f"{path} is the ledger. Change it only through your ledger.py commands: {', '.join(sorted(AGENT_COMMANDS))}."
        return None
    if tool not in SHELL_TOOLS:
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    hub_only = [sub for sub in RE_LEDGER.findall(command) if sub not in AGENT_COMMANDS]
    if hub_only:
        return (f"`ledger.py {hub_only[0]}` is the hub's command. Yours are {', '.join(sorted(AGENT_COMMANDS))}. "
                f"Hand your work in with `submit`; if you are stuck, `block <id> --on \"...\" --until none`.")
    if RE_PYTHON.search(command) and RE_IMPORT.search(command):
        return "Importing the ledger module changes it outside your commands. Use show, findings and next to read it."
    if RE_CLAUDE.search(command):
        return "A department agent never runs `claude` or starts an agent. Say in your reply what the hub should start."
    if RE_COMMIT.search(command):
        return "A department agent commits nothing. The hub commits your work after it confirms the subtask."
    return None


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    why = refusal(str(payload.get("tool_name") or ""), payload.get("tool_input") or {})
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "Department fence: " + why}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never stall a department run on a broken fence: let the call through and say why
        print(json.dumps({"systemMessage": f"Department fence hook failed, so this call was not checked: "
                                           f"{type(e).__name__}: {e}"}))
        sys.exit(0)
