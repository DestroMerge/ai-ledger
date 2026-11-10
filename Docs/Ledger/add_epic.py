#!/usr/bin/env python3
"""Add an epic with its first task. It files a one-epic plan through `ledger.py add-plan`'s own code, so the same
rules hold: the next free id, every field checked, and a title an open epic already carries refused (two sessions
once filed the same epic four minutes apart).

    python Docs/Ledger/add_epic.py --title "Save and load" --goal "Progress survives a restart" \
        --plan Docs/Design/saves.md --task first_task.json [--same-title-ok]

The task file holds the first task's fields, as for `ledger.py add-task`: title, owner, size, wave, files,
acceptance ({"check": ..., "expect": ...}), and dependsOn or notes if it has them. The new epic gets the next free
id; its first task is <epic>.01. For several epics, tasks and decisions at once, write a plan file and run
`ledger.py add-plan --from plan.json` (README, "Filing a plan"). Commit straight away, with --role Ledger, so another
session cannot take the id:

    python Docs/Ledger/ledger.py commit --role Ledger --reason "add epic <id>" --message msg.txt Docs/Ledger/
"""
from __future__ import annotations

import argparse
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
    ap.add_argument("--same-title-ok", action="store_true", help="an open epic has this title, and this is another")
    a = ap.parse_args()

    spec = ledger.read_json_file(a.task, "--task")
    with ledger.ledger_lock():
        led = ledger.load()
        errs, made = ledger.cmd_add_plan(led, {"epics": [{"title": a.title, "goal": a.goal, "plan": a.plan,
                                                          "tasks": [spec]}]}, a.same_title_ok)
        if errs:
            print("REFUSED, nothing written:\n  " + "\n  ".join(errs))
            return 1
        said = ledger.persist(led)
    epic_id, task_id = made[0][1], made[1][1]
    print(f"added {epic_id} '{a.title}' with {task_id} '{spec.get('title', '')}'; {said}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ledger.LedgerError as e:
        print(f"REFUSED: {e}")
        sys.exit(1)
