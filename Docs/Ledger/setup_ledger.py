#!/usr/bin/env python3
"""Tie the ledger to your project: write ledger_config.json, retag the example ledger to your id prefix and owner, and
start the commit audit at your repository's current commit.

    python Docs/Ledger/setup_ledger.py --prefix APP --owner Alex
    python Docs/Ledger/setup_ledger.py --prefix APP --owner Alex --drop-example
    python Docs/Ledger/setup_ledger.py --prefix APP --owner Alex --vcs diversion --unreal

Run it once, after the first commit of your project that holds these files (the audit judges every commit after the
one it starts from). Run it again to change the prefix or the owner; it renames what the ledger already holds.

--drop-example removes the example epic (PR-E01). The ledger then has no epics until you add one with add_epic.py.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = HERE / "ledger_config.json"
META = HERE / "meta.json"
EPICS = HERE / "epics"
ABOUT = ("The four settings that tie the ledger tool to one project. Docs/Ledger/README.md, \"Configuration\", says "
         "what each one does. setup_ledger.py writes this file.")


def needs_field(owner: str) -> str:
    return "needs" + re.sub(r"\s+", "", owner)


def retag_text(text: str, old_prefix: str, new_prefix: str) -> str:
    return re.sub(rf"\b{re.escape(old_prefix)}-(?=[ED]\d)", f"{new_prefix}-", text)


def retag_obj(obj, old: dict, new: dict):
    """Ids to the new prefix, the owner's name and needs field to the new owner, everywhere in one ledger file."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = needs_field(new["owner"]) if k == needs_field(old["owner"]) else k
            out[key] = retag_obj(v, old, new)
        return out
    if isinstance(obj, list):
        return [retag_obj(v, old, new) for v in obj]
    if isinstance(obj, str):
        s = retag_text(obj, old["idPrefix"], new["idPrefix"])
        return new["owner"] if s == old["owner"] else s
    return obj


def head_commit(vcs: str) -> str | None:
    try:
        if vcs == "git":
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
            return f"git:{r.stdout.strip()[:12]}" if r.returncode == 0 and r.stdout.strip() else None
        if vcs == "diversion":
            r = subprocess.run(["dv", "log", "-n", "1"], cwd=REPO, capture_output=True, text=True)
            m = re.search(r"dv\.commit\.\d+", r.stdout)
            return m.group(0) if m else None
    except FileNotFoundError:
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", required=True, help="1 to 8 capital letters or digits, starting with a letter: APP")
    ap.add_argument("--owner", required=True, help="the one person who rules on decisions")
    ap.add_argument("--vcs", default="git", choices=["git", "diversion"])
    ap.add_argument("--unreal", action="store_true", help="an Unreal Engine project (Blueprint graph dumps, /Game/ paths)")
    ap.add_argument("--drop-example", action="store_true", help="remove the example epic PR-E01")
    a = ap.parse_args()

    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", a.prefix):
        print("--prefix is 1 to 8 capital letters or digits, starting with a letter (APP, GAME2)")
        return 2
    if not re.search(r"\w", a.owner):
        print("--owner is a name")
        return 2

    old = json.loads(CONFIG.read_text(encoding="utf-8"))
    new = {"idPrefix": a.prefix, "owner": a.owner.strip(), "vcs": a.vcs, "unreal": a.unreal}

    # the ledger files, retagged
    meta = retag_obj(json.loads(META.read_text(encoding="utf-8")), old, new)
    meta["owner"] = {"name": new["owner"]}
    for path in sorted(EPICS.glob(f"{old['idPrefix']}-E*.json")):
        if a.drop_example and path.stem == f"{old['idPrefix']}-E01":
            path.unlink()
            print(f"removed the example epic {path.name}")
            continue
        data = retag_obj(json.loads(path.read_text(encoding="utf-8")), old, new)
        target = EPICS / (retag_text(path.stem, old["idPrefix"], new["idPrefix"]) + ".json")
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if target != path:
            path.unlink()
            print(f"renamed {path.name} -> {target.name}")

    head = head_commit(a.vcs)
    if head:
        meta["auditFrom"] = head
        print(f"the audit starts after {head} (auditFrom)")
    else:
        print(f"no {a.vcs} commit found: commit these files, then run this again so the audit has a starting point")
    META.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    CONFIG.write_text(json.dumps({"_about": ABOUT, **new}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {CONFIG.relative_to(REPO).as_posix()}: prefix {new['idPrefix']}, owner {new['owner']}, "
          f"{new['vcs']}, unreal {str(new['unreal']).lower()}")

    sys.path.insert(0, str(HERE))
    import ledger  # reads the config just written

    errs = ledger.validate(ledger.load(track=False))
    ledger.write_render()
    print("rendered LEDGER.md")
    if errs:
        print("validate found problems:\n  " + "\n  ".join(errs))
        return 1
    print("validate: ok. Next: python Docs/Ledger/ledger.py next")
    return 0


if __name__ == "__main__":
    sys.exit(main())
