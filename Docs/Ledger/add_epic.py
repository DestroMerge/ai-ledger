#!/usr/bin/env python3
"""Add an epic with its first task. ledger.py has no command for this (an epic is rare and needs a task to be valid),
so this script does it the way the README says scripts may: ledger.load(), change it, ledger.save(), write_render().

    python Docs/Ledger/add_epic.py --title "Save and load" --goal "Progress survives a restart" \
        --plan Docs/Design/saves.md --task first_task.json

The task file holds the first task's fields, as for `ledger.py add-task`: title, owner, size, wave, files,
acceptance ({"check": ..., "expect": ...}), and dependsOn or notes if it has them. The new epic gets the next free
id; its first task is <epic>.01. Commit both straight away, with --role Ledger, so another session cannot take the id:

    python Docs/Ledger/ledger.py commit --role Ledger --reason "add epic <id>" --message msg.txt Docs/Ledger/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--goal", required=True, help="what is true when the epic is done")
    ap.add_argument("--plan", required=True, help="the design or plan document the epic comes from (a repo path)")
    ap.add_argument("--task", required=True, help="a JSON file with the first task's fields")
    a = ap.parse_args()

    spec = json.loads(Path(a.task).read_text(encoding="utf-8"))
    led = ledger.load()
    nums = [int(e["id"].rsplit("-E", 1)[1]) for e in led["epics"]]
    epic_id = f"{ledger.PREFIX}-E{(max(nums) + 1 if nums else 1):02d}"
    led["epics"].append({"id": epic_id, "title": a.title, "status": "planned", "plan": a.plan, "goal": a.goal})
    task = {"id": f"{epic_id}.01", "epic": epic_id, **{k: v for k, v in spec.items() if k not in ("id", "epic")}}
    task.setdefault("status", "planned")
    led["tasks"].append(task)

    errs = [e for e in ledger.validate(led) if e.startswith(epic_id)]
    if errs:
        print("refused, nothing written:\n  " + "\n  ".join(errs))
        return 1
    ledger.save(led)
    ledger.write_render()
    print(f"added {epic_id} '{a.title}' with {task['id']} '{task.get('title', '')}'")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ledger.LedgerError as e:
        print(f"REFUSED: {e}")
        sys.exit(1)
