"""PreToolUse hook for the department agents: refuses the commands that belong to the hub.

Each agent file in .claude/agents/ registers this in its frontmatter `hooks:`, so it runs before every shell command
and file write that agent makes, and only while that agent runs. It reads the hook's JSON on stdin; to refuse, it
prints a PreToolUse "deny" with the reason, which the agent sees.

What it refuses:
- `ledger.py` with any subcommand but the agent's seven: show, next, findings, start, finding, submit, block - however
  the script is run (python, py, python3, a full path to python.exe, `uv run`, an env prefix, ./Docs/Ledger/ledger.py).
- Python that loads the ledger module (import, from ... import, importlib.import_module, __import__, runpy), which
  would change the ledger without going through those commands.
- Running `claude` itself, and committing (`dv commit`, `git commit`, with any options before the subcommand): an
  agent starts no agents and commits nothing.
- A Write or Edit to anything under Docs/Ledger/, a shell redirect into it, or a file command (tee, cp, mv, rm,
  Set-Content, Out-File and the like, sed -i) that names it.

A command is split where one command ends and the next begins (; & | && || newlines and parentheses), and each piece
is judged by its first word: a piece that starts with grep, rg, echo, cat, git (or another reader) only names a
command and passes. On an internal error it lets the call through and says why in one line: a broken fence must not
stall every department run.

Why a hook: instructions and a permission allowlist alone did not hold. Auto mode never prompts on a command missing
from the allowlist, so nothing actually stopped an agent running `done`, `confirm` or `commit`. The first version
matched one spelling (`python ... ledger.py <cmd>` at the start of a command); `uv run`, an env prefix, ./ledger.py,
`git -C . commit` and a redirect into Docs/Ledger/ all went past it.
"""
from __future__ import annotations

import json
import re
import sys

AGENT_COMMANDS = {"show", "next", "findings", "start", "finding", "submit", "block"}
SHELL_TOOLS = {"Bash", "PowerShell"}
FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}

# Where one command ends and the next begins. Quotes are not respected: a refusal of `echo "a; ledger.py done"` is a
# false alarm the agent can step around, while a split that trusted quotes could be walked past.
RE_SPLIT = re.compile(r"&&|\|\||[;&|\n()`]|\$\(")
# Words in front of the program that do not change what runs.
PREFIX_WORDS = {"env", "nice", "nohup", "sudo", "exec", "time", "call", "&", "npx", "command", "builtin", "start"}
# A piece that starts with one of these only reads, prints or searches: what it names is not run.
READERS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "select-string", "sls", "echo", "printf",
           "write-output", "write-host", "cat", "type", "get-content", "gc", "head", "tail", "less", "more", "wc",
           "git", "dv", "diff", "fc", "find", "ls", "dir", "get-childitem", "gci"}
# File commands that change what they name.
WRITERS = {"tee", "cp", "mv", "rm", "del", "erase", "copy", "move", "ren", "rename", "out-file", "set-content",
           "add-content", "clear-content", "copy-item", "move-item", "remove-item", "new-item", "rename-item",
           "truncate", "touch", "dd", "install", "ln", "mklink", "rmdir", "rd"}
RE_LEDGER_CALL = re.compile(r"ledger\.py[\"']?\s+(?:-\S+\s+)*([a-z][\w-]*)")
RE_LEDGER_LOAD = re.compile(r"\bimport\s+ledger\b|\bfrom\s+ledger\s+import\b|import_module\(\s*[\"']ledger[\"']"
                            r"|__import__\(\s*[\"']ledger[\"']|run_path\([^)]*ledger\.py"
                            r"|spec_from_file_location\([^)]*ledger\.py")
RE_LEDGER_PATH = re.compile(r"(?:^|[\\/\s\"'=])Docs[\\/]Ledger[\\/]", re.IGNORECASE)
RE_REDIRECT_INTO = re.compile(r">{1,2}\s*[\"']?[^\s\"';&|<>]*Docs[\\/]Ledger[\\/]", re.IGNORECASE)
RE_PY_WRITE = re.compile(r"write_text|write_bytes|json\.dump\b|os\.replace|os\.remove|unlink\(|shutil\."
                         r"|open\([^)]*[\"'][wax]\+?b?[\"']")
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def pieces(command: str) -> list[str]:
    return [p.strip() for p in RE_SPLIT.split(command) if p.strip()]


def words(piece: str) -> list[str]:
    """The piece's words, quotes kept together roughly ("C:/Program Files/x.exe" is one word)."""
    return re.findall(r"\"[^\"]*\"|'[^']*'|\S+", piece)


def program(word: str) -> str:
    """A word as a program name: no quotes, no folder, no .exe/.cmd/.bat/.ps1, lower case."""
    w = word.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", w)


def first_word(piece: str) -> tuple[str, list[str]]:
    """The program a piece runs, past env assignments (X=1), `env`, `timeout N`, `uv run` and the like, and the words
    after it."""
    ws = words(piece)
    i = 0
    while i < len(ws):
        w = program(ws[i])
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", ws[i]) or w in PREFIX_WORDS:
            i += 1
        elif w == "timeout" and i + 1 < len(ws) and re.fullmatch(r"\d+[smhd]?", ws[i + 1]):
            i += 2
        elif w in ("uv", "uvx", "poetry", "pipenv", "conda") and i + 1 < len(ws) and ws[i + 1] == "run":
            i += 2
        else:
            return w, ws[i + 1:]
    return "", []


def ledger_calls(command: str) -> list[tuple[str, str]]:
    """Every `ledger.py <subcommand> ...` a command runs: (subcommand, the text after it). A piece that only reads or
    prints (grep, echo, git ...) runs nothing. The Stop hook uses this too."""
    out = []
    for piece in pieces(command):
        if first_word(piece)[0] in READERS:
            continue
        for m in RE_LEDGER_CALL.finditer(piece):
            out.append((m.group(1), piece[m.end():]))
    return out


def commits(piece: str) -> bool:
    """True when the piece runs `git commit` or `dv commit`, options before the subcommand included (git -C . commit)."""
    prog, rest = first_word(piece)
    if prog not in ("git", "dv"):
        return False
    i = 0
    while i < len(rest):
        w = rest[i].strip("\"'")
        if w in GIT_VALUE_OPTIONS:
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w == "commit"
    return False


def refusal(tool: str, tool_input: dict) -> str | None:
    """Why this call is the hub's and not the agent's, or None when the agent may make it."""
    if tool in FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if RE_LEDGER_PATH.search(path.replace("\\", "/")) or RE_LEDGER_PATH.search(path):
            return f"{path} is the ledger. Change it only through your ledger.py commands: {', '.join(sorted(AGENT_COMMANDS))}."
        return None
    if tool not in SHELL_TOOLS:
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    hub_only = [sub for sub, _ in ledger_calls(command) if sub not in AGENT_COMMANDS]
    if hub_only:
        return (f"`ledger.py {hub_only[0]}` is the hub's command. Yours are {', '.join(sorted(AGENT_COMMANDS))}. "
                f"Hand your work in with `submit`; if you are stuck, `block <id> --on \"...\" --until none`.")
    # Python that loads the module is read from the whole command (a split at "(" or ";" would cut it apart), unless
    # every piece only reads or prints: `grep "import ledger" x.py` names it and runs nothing.
    if RE_LEDGER_LOAD.search(command) and any(first_word(p)[0] not in READERS for p in pieces(command)):
        return "Loading the ledger module changes it outside your commands. Use show, findings and next to read it."
    for piece in pieces(command):
        prog, rest = first_word(piece)
        if RE_REDIRECT_INTO.search(piece):  # whatever writes it - `echo {} > Docs/Ledger/meta.json` too
            return ("That writes into Docs/Ledger/, which is the ledger. Change it only through your ledger.py "
                    f"commands: {', '.join(sorted(AGENT_COMMANDS))}.")
        if prog in READERS - {"git", "dv"}:
            continue
        if prog == "claude" or (prog == "npx" and rest and program(rest[0]) == "claude"):
            return "A department agent never runs `claude` or starts an agent. Say in your reply what the hub should start."
        if commits(piece):
            return "A department agent commits nothing. The hub commits your work after it confirms the subtask."
        names_ledger = bool(RE_LEDGER_PATH.search(piece))
        if names_ledger and (
                prog in WRITERS or (prog in ("sed", "perl") and any(w.startswith("-i") for w in rest))
                or (prog.startswith("python") or prog == "py") and RE_PY_WRITE.search(piece)):
            return ("That writes into Docs/Ledger/, which is the ledger. Change it only through your ledger.py "
                    f"commands: {', '.join(sorted(AGENT_COMMANDS))}.")
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
