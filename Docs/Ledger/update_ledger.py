#!/usr/bin/env python3
"""Bring this project's copy of the ledger tool up to date from an ai-ledger checkout. It copies the tool's files and
never the project's data, then runs the selftest.

    python Docs/Ledger/update_ledger.py --from ../ai-ledger            show what would change, then copy
    python Docs/Ledger/update_ledger.py --from ../ai-ledger --dry-run  only show

Copied (TOOL_FILES): ledger.py, ledger_web.py, review_graph_dump.py, add_epic.py, update_ledger.py, usage.py, README.md (the
manual), web/, and the three hooks in .claude/hooks/. Never copied: ledger_config.json, meta.json, epics/,
LEDGER.md, .claude/agents/ (each project writes its own departments), .claude/settings.json, the launchers.

Each copied file keeps the line endings the project's copy has (CRLF stays CRLF), so a project whose files are CRLF
does not see every line change. A new file takes the endings of the project's ledger.py.

Why: four projects carried three versions of the tool with nothing saying which was which, and fixes made in one
never reached the others. Fix the tool in ai-ledger first, raise ledger.py's VERSION, then run this in each project.
`ledger.py version` (and `check`) prints the version a copy is.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOL_FILES = ["Docs/Ledger/ledger.py", "Docs/Ledger/ledger_web.py", "Docs/Ledger/review_graph_dump.py",
              "Docs/Ledger/add_epic.py", "Docs/Ledger/update_ledger.py", "Docs/Ledger/usage.py", "Docs/Ledger/README.md",
              "Docs/Ledger/web/app.js", "Docs/Ledger/web/index.html", "Docs/Ledger/web/style.css",
              ".claude/hooks/ledger_stop.py", ".claude/hooks/owner_message_check.py",
              ".claude/hooks/department_fence.py"]
RE_VERSION = re.compile(rb'^VERSION = "([^"]+)"', re.M)


def version(path: Path) -> str:
    try:
        m = RE_VERSION.search(path.read_bytes())
    except OSError:
        return "none"
    return m.group(1).decode() if m else "unversioned"


def endings(data: bytes) -> bytes:
    """The line ending a file uses: CRLF when most of its lines end so."""
    return b"\r\n" if data.count(b"\r\n") * 2 > data.count(b"\n") else b"\n"


def as_endings(data: bytes, eol: bytes) -> bytes:
    lf = data.replace(b"\r\n", b"\n")
    return lf if eol == b"\n" else lf.replace(b"\n", b"\r\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", required=True, help="the ai-ledger checkout to copy from")
    ap.add_argument("--dry-run", action="store_true", help="list what would change; copy nothing")
    a = ap.parse_args()

    src = Path(a.source).resolve()
    if not (src / "Docs" / "Ledger" / "ledger.py").is_file():
        print(f"REFUSED: {src} holds no Docs/Ledger/ledger.py; --from names an ai-ledger checkout")
        return 1
    if src == REPO:
        print("REFUSED: --from is this repository itself")
        return 1
    here_eol = endings((REPO / "Docs/Ledger/ledger.py").read_bytes()) if (REPO / "Docs/Ledger/ledger.py").is_file() \
        else b"\n"
    print(f"ledger.py here: {version(REPO / 'Docs/Ledger/ledger.py')}; in {src}: {version(src / 'Docs/Ledger/ledger.py')}")
    plan = []
    for rel in TOOL_FILES:
        s, d = src / rel, REPO / rel
        if not s.is_file():
            print(f"  missing in the source, left alone: {rel}")
            continue
        old = d.read_bytes() if d.is_file() else None
        new = as_endings(s.read_bytes(), endings(old) if old is not None else here_eol)
        if new != old:
            plan.append((rel, d, new, "new" if old is None else "changed"))
    for rel, _, _, what in plan:
        print(f"  {what:<8} {rel}")
    if not plan:
        print("every tool file is already the source's")
        return 0
    if a.dry_run:
        print(f"dry run: {len(plan)} file(s) would change; nothing was copied")
        return 0
    for rel, d, new, _ in plan:
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes(new)
    print(f"copied {len(plan)} file(s). Running the selftest:")
    r = subprocess.run([sys.executable, str(REPO / "Docs/Ledger/ledger.py"), "selftest"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    print((r.stdout.strip().splitlines() or ["(no output)"])[-1])
    if r.returncode != 0:
        print("\n".join(x for x in r.stdout.splitlines() if "FAIL" in x) or r.stderr[-2000:])
        print("the selftest failed: read the failures above before using the tool")
        return 1
    print("Next: python Docs/Ledger/ledger.py check, then commit the tool files (--role Repair or Ledger, with a reason)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
