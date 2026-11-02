#!/usr/bin/env python3
"""Task ledger - the record of planned work and of every commit that delivers it.

Built for multi-session work with AI coding agents (Claude Code). The JSON files are the source of truth;
LEDGER.md is rendered from them and must never be hand-edited. An epic whose every task is done or
withdrawn is one line on that page; its tasks are read with `show <id>` or on the ledger website.
Nothing is ever removed from the JSON.

Files (one file per epic; it was one ledger.json that every session rewrote whole):
    Docs/Ledger/meta.json           waves, gates, conventions, auditFrom, outsideCommits
    Docs/Ledger/epics/PR-Enn.json   {"epic": {...}, "tasks": [...], "decisions": [...]} - one epic, its tasks, and
                                    the decisions filed under it (each decision carries "epic")
load() reads them all into ONE dict shaped as the single file was ({"epics", "tasks", "decisions", ...}), so
every query, rule and script works on the whole ledger. save() writes back only the files whose content
changed, and refuses (LedgerConflict) when another session changed one of those files after this one loaded
it - reload and redo rather than overwrite. Commands that change the ledger hold a lock file
(Saved/Ledger/ledger.lock) so two runs of this tool do not interleave.

Project config: Docs/Ledger/ledger_config.json names the four things that tie it to one project - the id prefix
(PR in these examples, so ids read PR-E02.01), the owner (the one person who rules on decisions), the version
control system (git or Diversion: every call goes through a backend class below) and whether the project is
Unreal. README.md, "Configuration".

    python Docs/Ledger/ledger.py next                  where we are, and what can start now
    python Docs/Ledger/ledger.py status                progress per epic
    python Docs/Ledger/ledger.py show <id>             one task or decision in full
    python Docs/Ledger/ledger.py start <task> [--by NAME]   planned -> in-progress (refuses unmet dependencies);
                                                       --by is required on a department subtask
    python Docs/Ledger/ledger.py done <task> --evidence "<ran-it: what ran and what it showed>"
  Department subtasks (a task with 'kind'; README "Department subtasks"):
    python Docs/Ledger/ledger.py dispatch <task> --by "<hub title>" [--dump <folder>] [--restart]
                                                       hub: freeze what it covers, print the agent and its prompt
    python Docs/Ledger/ledger.py submit <task> --evidence "..."   department agent: hand the work in (stays in progress)
    python Docs/Ledger/ledger.py confirm <task> --by "<hub title>" --evidence "..."   hub: one pointer re-checked; done
    python Docs/Ledger/ledger.py finding <task> --severity blocker|hardening --text "..." --evidence "<pointer>"
    python Docs/Ledger/ledger.py findings [--unrouted] [--severity S]   every finding, finished epics included
    python Docs/Ledger/ledger.py route <task>#<n> (--to <task> | --reject "<why>") --by "<hub title>"
    python Docs/Ledger/ledger.py block <task> --on "<the named question>"
    python Docs/Ledger/ledger.py note <id> --text "<text>"   append to a task's notes, stamped from the clock
    python Docs/Ledger/ledger.py add-task --epic PR-Enn --from <file.json>      next free id in that epic
    python Docs/Ledger/ledger.py add-decision --epic PR-Enn --from <file.json>  next free PR-Dnn
    python Docs/Ledger/ledger.py decide <decision> --ruling "<text>" --by <who ruled> --date YYYY-MM-DD
    python Docs/Ledger/ledger.py apply <decision>      make the answer the owner gave on the website the ruling (`next` lists them)
    python Docs/Ledger/ledger.py render [--check]      write LEDGER.md, or refuse if it is stale
    python Docs/Ledger/ledger.py validate              structural rules, every one refuses
    python Docs/Ledger/ledger.py commit --message <file> [--role R] [--reason T] [--agent NAME] [--dry-run] <paths...>
    python Docs/Ledger/ledger.py audit                 judge every commit after auditFrom
    python Docs/Ledger/ledger.py check                 validate + render --check + audit: the ledger gate
    python Docs/Ledger/ledger.py selftest              planted faults must each refuse; clean controls must pass

Commit rules (each refuses rather than warns):
  1. The subject starts with a task id (PR-Enn.mm) or an epic id (PR-Enn), or the caller names a role
     (--role, one of ROLES) and a reason. A commit with neither is refused.
  2. A named task exists and is not planned. Run `start` first.
  3. A task being closed (status done, no landed yet) commits its epic's ledger file with it. The tool adds
     the named task's (or epic's) ledger file and LEDGER.md to every task or epic commit itself.
  4. A named task that is already done and landed takes new work only as --role Repair.
  5. Every path lies inside the named task's files list (or its epic's, for an epic commit). The ledger
     files are always allowed. Role commits are not path-restricted; the reason says why.
     A task commit given --reason (15+ characters) adds the paths outside the list to the task's files,
     records them in the task's filesAdded with the reason and the date, and goes on; without --reason it
     refuses and says so. (add the file with a written reason, do not refuse.)
  6. When a ledger file is among the paths, validate and render --check must pass.
  7. Paths are explicit. There is no -a, and no shell sits between this tool and dv.
  The old name Docs/Ledger/ledger.json among the paths stands for the named task's epic file (or, in a role
  commit, every ledger file with changes), so callers written before the split still commit the right file.
  After the commit, a task that is done and unlanded gets landed stamped with the new commit id, and its
  epic's ledger file is committed alone as a second commit naming the same task.

Audit: walks `dv log` from the ledger's auditFrom (exclusive) to head, and refuses when:
  - a commit names an unknown id, or a task still planned;
  - a commit names nothing, carries no valid role, AND touched a file of a task that is in progress now,
    unless the commit is recorded in outsideCommits with a reason;
  - a done task has no landed, or no evidence;
  - dv log is empty or does not reach auditFrom.
  dv log answers at most 1000 commits a call, so the walk pages back with --until until it reaches
  auditFrom. A commit's paths are cached in Saved/Ledger/commit_paths.json (a commit never changes).
  Commits that name nothing and touch no in-progress file are out of the ledger's scope. They are counted
  and listed, never refused: other departments commit to this repository too.
  LIMIT, stated plainly: the scope test uses the in-progress set as it is when the audit runs, not as it
  was when the commit was made. The audit is a gate run at every ledger commit, so it catches the case
  while the task is open; it cannot judge history after a task closes.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
META_JSON = HERE / "meta.json"
EPICS_DIR = HERE / "epics"
LEDGER_MD = HERE / "LEDGER.md"
META_REL = "Docs/Ledger/meta.json"
EPICS_REL = "Docs/Ledger/epics/"
LEDGER_MD_REL = "Docs/Ledger/LEDGER.md"
# The single file the ledger used to be. It must not exist now: a script that still writes it would
# be writing changes nobody reads, so load() refuses while it is there. As a commit path it is an alias (above).
LEDGER_JSON = HERE / "ledger.json"
LEDGER_REL = "Docs/Ledger/ledger.json"
SPLIT_KEYS = ("epics", "tasks", "decisions")
LOCK = REPO / "Saved" / "Ledger" / "ledger.lock"
LOCK_WAIT_S = 30.0  # how long a run waits for a live holder before it refuses (a dead holder's lock is taken over)
PATHS_CACHE = REPO / "Saved" / "Ledger" / "commit_paths.json"
REASON_MIN = 15
# The answers the owner gives on the ledger website (ledger_web.py). The website writes only this
# file; `apply` turns an answer into the decision's ruling. It sits under Saved/, which version control ignores: an
# answer is versioned once it is in the ledger files.
ANSWERS_JSON = REPO / "Saved" / "Ledger" / "answers.json"
OPTIONS_MIN, OPTIONS_MAX = 2, 4
OTHER = "Other"

ROLES = ["Owner", "Repair", "Checkpoint", "Ledger"]
STATUSES = ["planned", "in-progress", "blocked", "done", "withdrawn"]
DECISION_STATUSES = ["open", "decided"]
SIZES = ["S", "M", "L"]
# The id patterns (RE_EPIC, RE_TASK, RE_DECISION, RE_SUBJECT_ID, RE_EPIC_FILE, RE_ANY_ID, RE_FINDING_REF), the commit id
# pattern (RE_COMMIT_ID), RE_POINTER and LOG_LIMIT are set from the project config: apply_config() below.
RE_ROLE_LINE = re.compile(r"^\s*Role:\s*(\S+)\s*$", re.M)
FINISHED = {"done", "withdrawn"}
# A blocker names what releases it ('waitsOn'), save() releases the task once all of those are
# decided or finished, and `next` says how old every blocker and every in-progress task is. Blocker text alone went
# stale: decisions were applied, the tasks they gated stayed "blocked on Decision PR-Dnn", and `next` kept telling the owner
# to answer questions they had answered days before. RE_ANY_ID, set in apply_config(), finds the ids a blocker names.
RE_NOTE_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\b")
STALE_DAYS = 3  # a blocker or an in-progress task untouched this long is re-checked before anyone reports on it
# Department subtasks. A department is
# an agent file the hub starts fresh for one task; a task carrying 'kind' is that department's subtask. Owner names
# come from meta.json's 'departments' list (plus the owner), so they can no longer drift into free text.
# 'produce': a department that MAKES something - an
# asset, an effect - and may change only the files its task lists. It still hands in with submit; the hub confirms it and
# commits the work.
KINDS = ["review", "measure", "produce"]
EDITOR_USES = ["owns", "foreground", "none"]
# A finding is recorded on its subtask, not filed. The hub routes every finding - to the task that fixes it, or rejected
# with a reason - before it confirms. 'blocker' is item loss or duplication on the priority path: its fixing task goes
# on the critical path now. 'hardening' is everything else: its fixing task goes in the wave the work belongs to.
# No hardcoded hardening wave. Hardening findings used to wait, unfiled, until a late hardening wave
# was current; most were routed early on the owner's word anyway, so the wait added paperwork and no filter.
SEVERITIES = ["blocker", "hardening"]
AGENTS_DIR = REPO / ".claude" / "agents"


# --------------------------------------------------------------------------- io

class LedgerError(RuntimeError):
    """The ledger files cannot be read or written as they stand."""


class LedgerConflict(LedgerError):
    """Another session changed a ledger file after this one loaded it."""


# --------------------------------------------------------------------------- project config
# The ledger serves any project. Everything that ties the tool to one project is one of four settings in ledger_config.json beside this file (README.md, "Configuration"):
#   idPrefix  the letters every id starts with. "PR" makes epics PR-E02, tasks PR-E02.01, decisions PR-D95, and the
#             epic files epics/PR-E02.json
#   owner     the one person who rules on decisions and who may own an open task without being a department. The task
#             field saying what they do on a task is "needs" plus their name without spaces: needsSam
#   vcs       the version control every log, diff and commit goes through: a key of VCS_BACKENDS
#   unreal    true for an Unreal project: a /Game/ asset path counts as evidence, and a review covering .uasset or
#             .umap files needs their Blueprint graphs dumped before it is dispatched
# Paths stay fixed: the tool lives in Docs/Ledger/ at the repository root, and its working files in Saved/Ledger/.

CONFIG_JSON = HERE / "ledger_config.json"
CONFIG_KEYS = ("idPrefix", "owner", "vcs", "unreal")
RE_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
# The settings the selftest's fixtures are written in. selftest() runs under these whatever the project's own are.
SELFTEST_PROFILE = {"idPrefix": "PR", "owner": "Sam", "vcs": "diversion", "unreal": True}


class Diversion:
    """Diversion, through its `dv` command line. Commit ids are dv.commit.N, numbered in the order they were made.

    Everything the ledger asks of version control is a method here; nothing else in the tool runs the command. A
    backend for another system offers the same attributes and methods and is added to VCS_BACKENDS."""
    name = "Diversion"
    cmd = "dv"
    RE_ID = re.compile(r"dv\.commit\.\d+")
    id_name = "dv.commit id"               # what a commit id is called in messages
    show_cmd = "dv show {id} --name-status"  # the command the web view offers for one commit
    LOG_LIMIT = 1000                       # dv's own ceiling for one `dv log` call (None: the log comes whole)

    def run(self, *args: str) -> str:
        r = subprocess.run([self.cmd, *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"{self.cmd} {' '.join(args)} failed ({r.returncode}): "
                               f"{r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def example_id(self, n: int) -> str:
        """A well-formed commit id, numbered n."""
        return f"dv.commit.{n}"

    def order(self, cid: str) -> int:
        """A number that grows with each commit, so two ids compare by when they were made; -1 for no id."""
        m = re.search(r"(\d+)$", cid)
        return int(m.group(1)) if m else -1

    def log_page(self, until: str | None) -> str:
        """One page of the log, newest first: from head, or at or before `until` (an ISO time)."""
        args = ["log", "-n", str(self.LOG_LIMIT), "--date", "iso"]
        if until:
            args += ["--until", until]
        return self.run(*args)

    def parse_log(self, text: str) -> list[dict]:
        """A log page as [{id, date, message, subject}], in the log's order."""
        commits = []
        cur = None
        for line in text.splitlines():
            m = re.match(r"^commit (dv\.commit\.\d+)", line)
            if m:
                if cur:
                    commits.append(cur)
                cur = {"id": m.group(1), "lines": []}
                continue
            if cur is None:
                continue
            if line.startswith("Date:"):
                cur["date"] = line[5:].strip()
                continue
            if line.startswith("Author:"):
                continue
            if line.startswith("\t") or line.startswith("    "):
                cur["lines"].append(line.strip())
        if cur:
            commits.append(cur)
        for c in commits:
            msg = "\n".join(c.pop("lines")).strip()
            c["message"] = msg
            c["subject"] = msg.splitlines()[0] if msg else ""
        return commits

    def show(self, cid: str) -> str:
        """One commit with the paths it changed, as printed after a commit."""
        return self.run("show", cid, "--name-status", "--color", "never")

    def commit_paths(self, cid: str) -> list[str]:
        """The repo paths one commit changed."""
        paths = []
        for line in self.show(cid).splitlines():
            m = re.match(r"^[AMDR]\d*\t(.+)$", line)
            if m:
                paths.append(norm(m.group(1).split("\t")[-1]))
        return paths

    def changed_paths(self, under: str) -> dict[str, str]:
        """Uncommitted changes under a repo path: {path: status letter}."""
        got = {}
        for line in self.run("diff", "--name-status", under).splitlines():
            m = re.match(r"^([AMDR])\d*\t(.+)$", line)
            if m:
                got[norm(m.group(2).split("\t")[-1])] = m.group(1)
        return got

    def new_commit_id(self, output: str) -> str | None:
        """The id a commit just created. dv's commit output can name other commits too (the parent), so take the
        'New commit ID:' line, and otherwise the highest-numbered id printed - never the first one found."""
        m = re.search(r"New commit ID:\s*(dv\.commit\.\d+)", output)
        if m:
            return m.group(1)
        ids = self.RE_ID.findall(output)
        return max(ids, key=self.order) if ids else None

    def commit(self, paths: list[str], message: str) -> tuple[str | None, str]:
        """Commit exactly these paths. Returns (the new commit's id, or None if it cannot be read; the output)."""
        out = self.run("commit", *paths, "-m", message)
        return self.new_commit_id(out), out


class Git:
    """git, through its command line. Commit ids are written git:<12 hex>, so a pointer in evidence cannot be mistaken
    for any other hex in the text. Commits are ordered by their place in HEAD's history (see order()).

    The audit reads the whole log in one call (LOG_LIMIT None). A commit is made from exactly the paths it names: they
    are staged (new, changed or deleted) and committed with `git commit --only`, so anything else staged stays staged
    and out of the commit."""
    name = "git"
    cmd = "git"
    RE_ID = re.compile(r"git:[0-9a-f]{7,40}")
    id_name = "git commit id (git:<hash>)"
    show_cmd = "git show --name-status {id}"
    LOG_LIMIT = None
    SEP_COMMIT, SEP_FIELD = "\x1e", "\x1f"

    def __init__(self):
        self._order: dict[str, int] = {}

    def run(self, *args: str) -> str:
        r = subprocess.run([self.cmd, *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"{self.cmd} {' '.join(args)} failed ({r.returncode}): "
                               f"{r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    @staticmethod
    def sha(cid: str) -> str:
        return cid[4:] if cid.startswith("git:") else cid

    @staticmethod
    def make_id(sha: str) -> str:
        return f"git:{sha[:12]}"

    def example_id(self, n: int) -> str:
        return self.make_id(f"{n:012x}")

    def order(self, cid: str) -> int:
        """The commit's place in HEAD's history, oldest 1, read once with `git rev-list --topo-order` (so a commit is
        always after its parents); -1 for no id or one not in HEAD's history."""
        if not cid or not self.RE_ID.fullmatch(cid):
            return -1
        if not self._order:
            try:
                shas = self.run("rev-list", "--topo-order", "HEAD").split()
            except RuntimeError:
                shas = []
            self._order = {self.make_id(s): len(shas) - i for i, s in enumerate(shas)}
        return self._order.get(self.make_id(self.sha(cid)), -1)

    def log_page(self, until: str | None) -> str:
        args = ["log", "--topo-order", f"--format={self.SEP_COMMIT}%H{self.SEP_FIELD}%cI{self.SEP_FIELD}%B"]
        if until:
            args.append(f"--until={until}")
        return self.run(*args)

    def parse_log(self, text: str) -> list[dict]:
        commits = []
        for rec in text.split(self.SEP_COMMIT):
            parts = rec.split(self.SEP_FIELD, 2)
            if len(parts) != 3 or not parts[0].strip():
                continue
            msg = parts[2].strip()
            commits.append({"id": self.make_id(parts[0].strip()), "date": parts[1].strip(), "message": msg,
                            "subject": msg.splitlines()[0] if msg else ""})
        return commits

    def show(self, cid: str) -> str:
        return self.run("show", "--name-status", "--no-color", self.sha(cid))

    def commit_paths(self, cid: str) -> list[str]:
        paths = []
        out = self.run("show", "--name-status", "--no-color", "--format=", "-m", "--first-parent", self.sha(cid))
        for line in out.splitlines():
            m = re.match(r"^[AMDRCT]\d*\t(.+)$", line)
            if m:
                paths.append(norm(m.group(1).split("\t")[-1]))
        return list(dict.fromkeys(paths))

    def changed_paths(self, under: str) -> dict[str, str]:
        got = {}
        for line in self.run("status", "--porcelain=v1", "-uall", "--", under).splitlines():
            if len(line) < 4:
                continue
            xy, path = line[:2], line[3:].split(" -> ")[-1].strip('"')
            letter = "A" if xy == "??" else (xy[1] if xy[1] != " " else xy[0])
            got[norm(path)] = letter if letter in "MADR" else "M"
        return got

    def new_commit_id(self, output: str) -> str | None:
        """git prints a short hash after a commit; HEAD is read instead, so the id is always the full form."""
        try:
            return self.make_id(self.run("rev-parse", "HEAD").strip())
        except RuntimeError:
            return None

    def commit(self, paths: list[str], message: str) -> tuple[str | None, str]:
        """Commit exactly these paths: stage them (a deleted file too), then commit only them."""
        self.run("add", "-A", "--", *paths)
        out = self.run("commit", "--only", "-m", message, "--", *paths)
        self._order = {}  # history changed
        return self.new_commit_id(out), out


VCS_BACKENDS = {"git": Git, "diversion": Diversion}


def config_errors(cfg) -> list[str]:
    """What is wrong with a config: every key present, none unknown, each value of its kind. A key starting with '_' is
    a note for the reader."""
    if not isinstance(cfg, dict):
        return ["the config is one JSON object"]
    errs = [f"unknown key '{k}' (the keys are {', '.join(CONFIG_KEYS)}; a key starting with '_' is a note)"
            for k in cfg if k not in CONFIG_KEYS and not str(k).startswith("_")]
    errs += [f"missing key '{k}'" for k in CONFIG_KEYS if k not in cfg]
    if "idPrefix" in cfg and not (isinstance(cfg["idPrefix"], str) and RE_PREFIX.match(cfg["idPrefix"])):
        errs.append(f"idPrefix '{cfg['idPrefix']}' is 1 to 8 capital letters or digits starting with a letter, "
                    f"such as PR")
    if "owner" in cfg and not (isinstance(cfg["owner"], str) and re.search(r"\w", cfg["owner"])):
        errs.append("owner is the name of the person who rules on decisions")
    if "vcs" in cfg and cfg["vcs"] not in VCS_BACKENDS:
        errs.append(f"vcs '{cfg['vcs']}' has no backend; there is one for: {', '.join(VCS_BACKENDS)}")
    if "unreal" in cfg and not isinstance(cfg["unreal"], bool):
        errs.append("unreal is true or false")
    return errs


def load_config(path: Path | None = None) -> dict:
    """ledger_config.json, checked. Refuses (LedgerError) rather than guess a missing or misspelled setting."""
    path = Path(path or CONFIG_JSON)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LedgerError(f"{path} is missing. It names this project's id prefix, owner, version control and whether "
                          f"it is an Unreal project; README.md, 'Configuration', shows one")
    except json.JSONDecodeError as e:
        raise LedgerError(f"{path} cannot be read: {e}")
    errs = config_errors(cfg)
    if errs:
        raise LedgerError(f"{path}: {'; '.join(errs)}")
    return {k: cfg[k] for k in CONFIG_KEYS}


def apply_config(cfg: dict) -> None:
    """Set every project-specific name from a checked config. Run once at import; the selftest runs it to switch to its
    fixtures' settings and to a made-up project's, and back."""
    global CONFIG, PREFIX, OWNER, NEEDS_OWNER, UNREAL, VCS, LOG_LIMIT, EPIC_GLOB, RE_EPIC, RE_TASK
    global RE_DECISION, RE_SUBJECT_ID, RE_EPIC_FILE, RE_ANY_ID, RE_FINDING_REF, RE_COMMIT_ID, RE_POINTER
    CONFIG = dict(cfg)
    PREFIX, OWNER, UNREAL = cfg["idPrefix"], cfg["owner"], cfg["unreal"]
    NEEDS_OWNER = "needs" + re.sub(r"\W", "", OWNER)
    VCS = VCS_BACKENDS[cfg["vcs"]]()
    LOG_LIMIT = VCS.LOG_LIMIT
    p = re.escape(PREFIX)
    EPIC_GLOB = f"{PREFIX}-E*.json"
    RE_EPIC = re.compile(rf"^{p}-E\d{{2}}$")
    RE_TASK = re.compile(rf"^{p}-E\d{{2}}\.\d{{2}}$")
    RE_DECISION = re.compile(rf"^{p}-D\d{{2,3}}$")
    RE_SUBJECT_ID = re.compile(rf"^({p}-E\d{{2}}(?:\.\d{{2}})?)(?![\d.])")
    RE_EPIC_FILE = re.compile(rf"^Docs/Ledger/epics/({p}-E\d{{2}})\.json$")
    RE_ANY_ID = re.compile(rf"\b{p}-(?:D\d{{2,3}}|E\d{{2}}\.\d{{2}})\b")
    RE_FINDING_REF = re.compile(rf"({p}-E\d{{2}}\.\d{{2}})#(\d+)")
    RE_COMMIT_ID = VCS.RE_ID
    # A pointer is something the hub can open and check: a commit id, (Unreal) a /Game/ asset path with its node named
    # beside it, an absolute path, or a repo path with a file extension (optionally :line or :line-line). It proves a
    # pointer is there, not that it is true; the hub's re-check before `confirm` is what catches a wrong one.
    RE_POINTER = re.compile("|".join([VCS.RE_ID.pattern] + ([r"/Game/[\w/.\-]+"] if UNREAL else [])
                                     + [r"\b[A-Za-z]:[\\/][^\s,;]+",
                                        r"(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,8}(?::\d+(?:-\d+)?)?"]))


def pointer_kinds() -> str:
    """What counts as a pointer, in words, for the messages that ask for one."""
    return ("a repo path (file:line), " + ("a /Game/ asset with its node, " if UNREAL else "")
            + f"a log or screenshot path, or a {VCS.id_name}")


try:
    apply_config(load_config())
except LedgerError as _e:
    if __name__ == "__main__":
        print(f"REFUSED: {_e}", file=sys.stderr)
        sys.exit(1)
    raise


# What each tracked load read, per file, so save() writes only what changed and can see another session's write.
# Keyed by id() of the loaded dict; the dict is held too so the id cannot be reused while the entry lives.
_SNAPSHOTS: dict[int, tuple[dict, Path, dict[str, str]]] = {}
_SNAPSHOTS_MAX = 16


def epic_rel(epic_id: str) -> str:
    return f"{EPICS_REL}{epic_id}.json"


def is_ledger_file(p: str) -> bool:
    """A file the ledger tool writes: always allowed in a commit."""
    p = norm(p)
    return p in (META_REL, LEDGER_MD_REL, LEDGER_REL) or bool(RE_EPIC_FILE.match(p))


def is_ledger_data(p: str) -> bool:
    """A file that holds ledger state (not the rendered page)."""
    p = norm(p)
    return p in (META_REL, LEDGER_REL) or bool(RE_EPIC_FILE.match(p))


def decision_num(i: str) -> int:
    m = re.search(r"(\d+)$", i)
    return int(m.group(1)) if m else 0


def load(root: Path | None = None, track: bool = True) -> dict:
    """The whole ledger as one dict: meta.json's keys, plus "epics", "tasks" and "decisions" gathered from every
    epics/PR-Enn.json in epic order (each file's own order kept). track=False for a reader that never saves."""
    root = Path(root) if root else HERE
    if (root / "ledger.json").exists():
        raise LedgerError(f"{root / 'ledger.json'} exists. The ledger is split into meta.json and epics/{PREFIX}-Enn.json; "
                          f"a script wrote the old single file, and nothing reads it. Move its "
                          f"changes into the epic files through ledger.py, then delete it.")
    meta_p = root / "meta.json"
    try:
        meta_text = meta_p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LedgerError(f"{meta_p} is missing")
    ledger = json.loads(meta_text)
    for k in SPLIT_KEYS:
        if k in ledger:
            raise LedgerError(f"{meta_p} carries '{k}', which lives in the epic files")
        ledger[k] = []
    snap = {meta_p.name: meta_text}
    for p in sorted((root / "epics").glob(EPIC_GLOB)):
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        e = data.get("epic") or {}
        if e.get("id") != p.stem:
            raise LedgerError(f"{p}: holds epic '{e.get('id')}', not {p.stem}")
        ledger["epics"].append(e)
        ledger["tasks"].extend(data.get("tasks") or [])
        ledger["decisions"].extend(data.get("decisions") or [])
        snap[f"epics/{p.name}"] = text
    ledger["decisions"].sort(key=lambda d: decision_num(d.get("id", "")))
    if track:
        _SNAPSHOTS[id(ledger)] = (ledger, root, snap)
        while len(_SNAPSHOTS) > _SNAPSHOTS_MAX:
            _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
    return ledger


def decision_epic(ledger: dict, d: dict) -> str | None:
    """The epic a decision is filed under: its own 'epic', else the epic most of the tasks it gates belong to."""
    if d.get("epic"):
        return d["epic"]
    gated = [t["epic"] for t in ledger.get("tasks", []) if d.get("id") in (t.get("dependsOn") or [])]
    return max(sorted(set(gated)), key=gated.count) if gated else None


def split(ledger: dict) -> dict[str, dict]:
    """The files the ledger is written as: {"meta.json": {...}, "epics/PR-Enn.json": {...}}. A decision with no
    'epic' gets the one decision_epic() names; one that names none cannot be placed and is refused."""
    meta = {k: v for k, v in ledger.items() if k not in SPLIT_KEYS}
    files: dict[str, dict] = {"meta.json": meta}
    epic_ids = [e["id"] for e in ledger.get("epics", [])]
    for d in ledger.get("decisions", []):
        if not d.get("epic"):
            e = decision_epic(ledger, d)
            if not e:
                raise LedgerError(f"{d.get('id')}: decision names no 'epic' and gates no task; set 'epic' to the epic "
                                  f"it belongs to")
            d["epic"] = e
    for t in ledger.get("tasks", []):
        if t.get("epic") not in epic_ids:
            raise LedgerError(f"{t.get('id')}: epic '{t.get('epic')}' does not exist")
    for d in ledger.get("decisions", []):
        if d["epic"] not in epic_ids:
            raise LedgerError(f"{d.get('id')}: epic '{d['epic']}' does not exist")
    for e in ledger.get("epics", []):
        files[f"epics/{e['id']}.json"] = {
            "epic": e,
            "tasks": [t for t in ledger["tasks"] if t["epic"] == e["id"]],
            "decisions": [d for d in ledger.get("decisions", []) if d["epic"] == e["id"]],
        }
    return files


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, p)


def epic_status(mine: list[dict]) -> str:
    """An epic's status, read off its tasks: planned until one starts, done when every one is done or withdrawn,
    in-progress between; withdrawn if every one was withdrawn.

    The field was set by hand and nothing kept it in step, so most epics came to disagree
    with their own tasks (some read "planned" with every task finished). save() now writes it and validate() refuses
    a ledger where the two differ.
    """
    states = [t.get("status") for t in mine]
    if states and all(x == "withdrawn" for x in states):
        return "withdrawn"
    if states and all(x in FINISHED for x in states):
        return "done"
    if any(x in ("in-progress", "blocked", "done") for x in states):
        return "in-progress"
    return "planned"


def sync_epic_status(ledger: dict) -> list[str]:
    """Set every epic's status from its tasks. Returns the ids it changed."""
    changed = []
    for e in ledger.get("epics", []):
        want = epic_status([t for t in ledger.get("tasks", []) if t.get("epic") == e.get("id")])
        if e.get("status") != want:
            e["status"] = want
            changed.append(e["id"])
    return changed


def save(ledger: dict, root: Path | None = None) -> list[str]:
    """Write the ledger back as its files, only those whose content changed. Returns the repo-relative paths
    written. A ledger that came from load() is compared with what that load read: a file this save would
    change that another session has changed since is a LedgerConflict, and nothing is written. A ledger made
    in memory (a migration, a test) writes every file that differs from the disk.

    The compare-then-write runs under the ledger lock, so two saves (a script, a
    background department agent, the hub) cannot both pass the compare and the second overwrite the first. A ledger
    under another root (a test) locks a file of its own there."""
    tracked = _SNAPSHOTS.get(id(ledger))
    root = Path(root) if root else (tracked[1] if tracked else HERE)
    with ledger_lock(LOCK if root == HERE else root / ".ledger.lock"):
        return _save_locked(ledger, root, tracked)


def _save_locked(ledger: dict, root: Path, tracked) -> list[str]:
    released = release_blocked(ledger)
    if released:
        print(f"released from blocked (everything they waited on is decided or finished): {', '.join(released)}")
    sync_epic_status(ledger)
    snap = tracked[2] if tracked and tracked[1] == root else None
    want = {rel: _dump(obj) for rel, obj in split(ledger).items()}
    changed, conflicts = [], []
    for rel, text in want.items():
        p = root / rel
        on_disk = p.read_text(encoding="utf-8") if p.exists() else None
        before = snap.get(rel) if snap is not None else on_disk
        if text == before:
            continue
        if snap is not None and on_disk != before:
            conflicts.append(rel)
        changed.append(rel)
    if conflicts:
        raise LedgerConflict(f"another session changed {', '.join(conflicts)} after this command read the ledger; "
                             f"nothing was written. Run the command again (it reads the files afresh).")
    for rel in changed:
        _write_atomic(root / rel, want[rel])
        if snap is not None:
            snap[rel] = want[rel]
    return [f"Docs/Ledger/{rel}" for rel in changed]


_LOCK_HELD: dict[tuple, int] = {}  # (lock path, thread) -> how deep that thread holds it (the lock is re-entrant)


def pid_alive(pid: int) -> bool:
    """True while a process with this id runs. On Windows os.kill(pid, 0) would terminate it, so ask the kernel."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: it exists, it is just not ours to open
        try:
            code = wintypes.DWORD()
            return not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def ledger_lock(path: Path | None = None, wait: float | None = None):
    """Serialise every change to the ledger files.

    The lock was taken over by age (a commit waiting on dv for five minutes lost
    it) and removed by whoever finished, whatever it held. Now a lock is taken over only when the process it names has
    exited, the takeover is a rename only one waiter can win, and a run removes only a lock that still holds its own
    token. Re-entrant within one process: main() holds it for a changing command and save() takes it again."""
    path = Path(path or LOCK)
    key = (os.path.normcase(str(path)), threading.get_ident())
    if _LOCK_HELD.get(key):
        _LOCK_HELD[key] += 1
        try:
            yield
        finally:
            _LOCK_HELD[key] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()} {uuid.uuid4().hex} {datetime.now().isoformat(timespec='seconds')}\n"
    deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, token.encode())
            os.close(fd)
            break
        except FileExistsError:
            pass
        try:
            held = path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError):
            continue
        try:
            pid = int(held.split()[0])
        except (ValueError, IndexError):
            pid = None  # still being written: wait for it
        if pid is not None and not pid_alive(pid):
            tomb = path.with_name(f"{path.name}.dead-{uuid.uuid4().hex}")
            try:
                os.rename(path, tomb)
            except (FileNotFoundError, FileExistsError, PermissionError):
                continue
            moved = tomb.read_text(encoding="utf-8")
            if moved != held and not path.exists():
                os.rename(tomb, path)  # it was a live run's new lock, not the dead one: put it back
            else:
                tomb.unlink()
            continue
        if time.monotonic() > deadline:
            raise LedgerError(f"another ledger.py run holds {path} ({held.strip() or 'being written'}) and its process "
                              f"is still running, so it is not taken over; try again when it finishes")
        time.sleep(0.25)
    _LOCK_HELD[key] = 1
    try:
        yield
    finally:
        _LOCK_HELD.pop(key, None)
        with contextlib.suppress(FileNotFoundError):
            if path.read_text(encoding="utf-8") == token:
                path.unlink()


def index(ledger: dict) -> tuple[dict, dict, dict]:
    epics = {e["id"]: e for e in ledger.get("epics", [])}
    tasks = {t["id"]: t for t in ledger.get("tasks", [])}
    decisions = {d["id"]: d for d in ledger.get("decisions", [])}
    return epics, tasks, decisions


def norm(p: str) -> str:
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def path_allowed(path: str, allowed: list[str]) -> bool:
    path = norm(path)
    for a in allowed:
        a = norm(a)
        if a.endswith("/"):
            if path.startswith(a):
                return True
        elif path == a:
            return True
    return False


# --------------------------------------------------------------------------- validate

def option_errors(options) -> list[str]:
    """What is wrong with a decision's options. Each is {"label", "detail"}; one may be {"recommended": true}."""
    if not isinstance(options, list) or not (OPTIONS_MIN <= len(options) <= OPTIONS_MAX):
        return [f"an open decision needs 'options': {OPTIONS_MIN} to {OPTIONS_MAX} choices, each a label and a detail, "
                f"so it can be answered on the website"]
    errs = []
    labels = [str(o.get("label", "")).strip() if isinstance(o, dict) else "" for o in options]
    for n, o in enumerate(options):
        if not isinstance(o, dict) or not labels[n] or not str(o.get("detail", "")).strip():
            errs.append(f"option {n + 1} needs a 'label' and a 'detail'")
    if len(set(labels)) != len(labels):
        errs.append("option labels must differ")
    if OTHER in labels:
        errs.append(f"'{OTHER}' is the website's own free-text choice and cannot be an option label")
    if len([o for o in options if isinstance(o, dict) and o.get("recommended")]) > 1:
        errs.append("at most one option is the recommended one")
    return errs


def load_answers() -> dict:
    """The website's inbox: decision id -> {"choice", "text", "note", "answeredAt"}. Empty when there is none.

    An inbox that cannot be read is refused, not read as empty - the next write from
    either side would otherwise replace every pending answer with one."""
    try:
        text = ANSWERS_JSON.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        raise LedgerError(f"{ANSWERS_JSON} cannot be read ({e}); nothing was read from it or written to it. Fix or "
                          f"move it, then try again")
    if not isinstance(data, dict):
        raise LedgerError(f"{ANSWERS_JSON} holds {type(data).__name__}, not the inbox's object; fix or move it")
    return data


def save_answers(answers: dict) -> None:
    """Write the inbox. Callers hold ledger_lock() around their read-modify-write; each writes its own temp file."""
    ANSWERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = ANSWERS_JSON.with_name(f"{ANSWERS_JSON.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(answers, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ANSWERS_JSON)


def take_answer(ident: str) -> None:
    """Remove one applied answer from the inbox, re-read under the lock, so an answer the website filed while `apply`
    was saving the ledger stays."""
    with ledger_lock():
        answers = load_answers()
        if answers.pop(ident, None) is not None:
            save_answers(answers)


def answer_errors(d: dict | None, choice: str, text: str) -> list[str]:
    """Why an answer cannot be taken. Used by the website before it writes and by `apply` before it rules."""
    if not d:
        return ["unknown decision"]
    if d.get("status") != "open":
        return [f"{d['id']} is already decided"]
    labels = [o["label"] for o in d.get("options") or []]
    if choice == OTHER:
        return [] if text.strip() else [f"'{OTHER}' needs the answer written out"]
    if choice not in labels:
        return [f"'{choice}' is not one of {d['id']}'s options"]
    return []


def answer_ruling(d: dict, ans: dict) -> str:
    """The ruling text an answer becomes."""
    note = str(ans.get("note", "")).strip()
    if ans.get("choice") == OTHER:
        return str(ans.get("text", "")).strip()
    opt = next(o for o in d["options"] if o["label"] == ans["choice"])
    return f"{opt['label']}. {opt['detail']}" + (f" {OWNER}'s note: {note}" if note else "")


def waiting_answers(ledger: dict) -> list[tuple[dict, dict]]:
    """Answers on file for decisions that are still open, oldest first."""
    _, _, decisions = index(ledger)
    rows = [(decisions[i], a) for i, a in load_answers().items()
            if i in decisions and decisions[i]["status"] == "open" and isinstance(a, dict)]
    return sorted(rows, key=lambda r: str(r[1].get("answeredAt", "")))


def department_index(ledger: dict) -> dict[str, dict] | None:
    """meta.json's departments by name, or None for a ledger with no list (a test fixture from before it)."""
    depts = ledger.get("departments")
    return None if depts is None else {d.get("name"): d for d in depts if isinstance(d, dict)}


def hub_name(ledger: dict) -> str | None:
    """The department that files and hands out work: the one entry marked 'hub' (Engineering)."""
    return next((d.get("name") for d in ledger.get("departments") or [] if isinstance(d, dict) and d.get("hub")), None)


def is_subtask(t: dict) -> bool:
    """A department subtask: a task that carries 'kind'. Every subtask rule keys on this field alone, so the finished
    department tasks from before it existed are never touched."""
    return bool(t.get("kind"))


def has_pointer(text: str | None) -> bool:
    """True when the text names something checkable (see RE_POINTER)."""
    return bool(RE_POINTER.search(text or ""))


def covered_ids(ledger: dict, t: dict) -> list[str]:
    """The tasks a subtask is about. Once dispatched, its frozen 'covers'. Before that: its parent task, or every
    unwithdrawn task in its parent epic that has no kind. Frozen at dispatch so a task the hub adds to the epic later
    (the task a blocker is routed to) cannot make a running or finished review invalid."""
    if t.get("covers") is not None:
        return list(t["covers"])
    p = t.get("parent")
    if not p:
        return []
    epics, tasks, _ = index(ledger)
    if p in tasks:
        return [p]
    if p in epics:
        return [x["id"] for x in ledger["tasks"]
                if x["epic"] == p and x["id"] != t["id"] and not is_subtask(x) and x["status"] != "withdrawn"]
    return []


def validate(ledger: dict) -> list[str]:
    errs: list[str] = []
    # 'departments' is required; a meta.json that lost it switched every owner
    # and kind rule off and still passed.
    for key in ["schema", "schemaVersion", "auditFrom", "epics", "tasks", "decisions", "gates", "conventions",
                "departments"]:
        if key not in ledger:
            errs.append(f"ledger: missing top-level key '{key}'")
    if errs:
        return errs

    seen: set[str] = set()
    for coll in ("epics", "tasks", "decisions"):
        for item in ledger[coll]:
            i = item.get("id", "")
            if i in seen:
                errs.append(f"{i}: duplicate id")
            seen.add(i)

    epics, tasks, decisions = index(ledger)

    for d in ledger["decisions"]:
        i = d["id"]
        if not RE_DECISION.match(i):
            errs.append(f"{i}: decision id must look like {PREFIX}-Dnn")
        for k in ("question", "recommendation", "status"):
            if not d.get(k):
                errs.append(f"{i}: decision missing '{k}'")
        if d.get("status") not in DECISION_STATUSES:
            errs.append(f"{i}: decision status '{d.get('status')}' not in {DECISION_STATUSES}")
        if d.get("status") == "decided" and not (d.get("ruling") and d.get("ruledOn")):
            errs.append(f"{i}: a decided decision needs 'ruling' and 'ruledOn'")
        # An open decision carries the choices the owner can pick on the website. Decided ones from
        # before this rule have none and need none.
        if d.get("status") == "open" or "options" in d:
            errs.extend(f"{i}: {e}" for e in option_errors(d.get("options")))
        # A decision is filed in one epic's file. save() fills 'epic' from the tasks a
        # decision gates; one that gates nothing must name its epic.
        if not decision_epic(ledger, d):
            errs.append(f"{i}: decision names no 'epic' and gates no task, so it has no file to live in")
        elif decision_epic(ledger, d) not in epics:
            errs.append(f"{i}: decision epic '{decision_epic(ledger, d)}' does not exist")

    depts = department_index(ledger)
    if depts is not None:
        names = [d.get("name") for d in ledger["departments"] if isinstance(d, dict)]
        if len(set(names)) != len(names) or len(names) != len(ledger["departments"]):
            errs.append("departments: every entry is an object with its own name")
        if len([d for d in ledger["departments"] if isinstance(d, dict) and d.get("hub")]) != 1:
            errs.append("departments: exactly one entry is the hub (the department that files and hands out work)")
        for d in ledger["departments"]:
            if not isinstance(d, dict):
                continue
            n = d.get("name") or "?"
            if not d.get("name") or not str(d.get("does", "")).strip():
                errs.append(f"departments: {n} needs a 'name' and a 'does' line")
            if d.get("editor") not in EDITOR_USES:
                errs.append(f"departments: {n}'s editor '{d.get('editor')}' is not one of {EDITOR_USES}")
            a = d.get("agent")
            if d.get("hub") and a:
                errs.append(f"departments: {n} is the hub, a session, so it has no agent file")
            elif a is not None and not (isinstance(a, str) and (AGENTS_DIR / f"{a}.md").is_file()):
                errs.append(f"departments: {n}'s agent file .claude/agents/{a}.md does not exist")

    for e in ledger["epics"]:
        i = e["id"]
        if not RE_EPIC.match(i):
            errs.append(f"{i}: epic id must look like {PREFIX}-Enn")
        for k in ("title", "status", "plan"):
            if not e.get(k):
                errs.append(f"{i}: epic missing '{k}'")
        if e.get("status") not in STATUSES:
            errs.append(f"{i}: epic status '{e.get('status')}' invalid")
        mine = [t for t in ledger["tasks"] if t.get("epic") == i]
        if not mine:
            errs.append(f"{i}: epic has no tasks")
        if e.get("status") == "done":
            open_ = [t["id"] for t in mine if t.get("status") not in FINISHED]
            if open_:
                errs.append(f"{i}: epic is done but carries unfinished tasks {open_}")
        elif mine and e.get("status") in STATUSES and e["status"] != epic_status(mine):
            errs.append(f"{i}: epic status '{e['status']}' does not match its tasks, which make it "
                        f"'{epic_status(mine)}'; any save through ledger.py corrects it")

    for t in ledger["tasks"]:
        i = t["id"]
        if not RE_TASK.match(i):
            errs.append(f"{i}: task id must look like {PREFIX}-Enn.mm")
            continue
        if t.get("epic") not in epics:
            errs.append(f"{i}: epic '{t.get('epic')}' does not exist")
        elif not i.startswith(t["epic"] + "."):
            errs.append(f"{i}: id does not sit under its epic {t['epic']}")
        for k in ("title", "owner", "size", "wave", "files", "acceptance", "status"):
            # A review or measure edits nothing, so its files list may be empty.
            # A produce edits exactly its files list, so it needs one.
            if k == "files" and is_subtask(t) and t.get("kind") != "produce" and isinstance(t.get("files"), list):
                continue
            if k not in t or t[k] in ("", None, []):
                errs.append(f"{i}: task missing '{k}'")
        if t.get("size") not in SIZES:
            errs.append(f"{i}: size '{t.get('size')}' not in {SIZES}")
        if t.get("status") not in STATUSES:
            errs.append(f"{i}: status '{t.get('status')}' not in {STATUSES}")
        # Owners come from the departments list, open tasks only: a finished task keeps the
        # free-text owner it was closed with.
        if depts is not None and t.get("status") not in FINISHED:
            if t.get("owner") != OWNER and t.get("owner") not in depts:
                errs.append(f"{i}: owner '{t.get('owner')}' is not on meta.json's departments list (or {OWNER})")
            elif t.get("owner") in depts and not depts[t["owner"]].get("hub") and not is_subtask(t):
                errs.append(f"{i}: owned by {t['owner']}, so it is a department subtask and needs 'kind' "
                            f"({' or '.join(KINDS)})")
        if "kind" in t:
            if t.get("kind") not in KINDS:
                errs.append(f"{i}: kind '{t.get('kind')}' is not one of {KINDS}")
            elif depts is not None and (t.get("owner") not in depts or depts[t["owner"]].get("hub")):
                errs.append(f"{i}: a {t['kind']} subtask is owned by a department other than the hub, not "
                            f"'{t.get('owner')}'")
            if t.get("kind") == "review" and not t.get("parent"):
                errs.append(f"{i}: a review names its 'parent' (the task or epic it reviews)")
        if t.get("parent"):
            p = t["parent"]
            if not is_subtask(t):
                errs.append(f"{i}: 'parent' belongs on a department subtask (a task with 'kind')")
            if p == i:
                errs.append(f"{i}: a subtask cannot be its own parent")
            elif p in tasks:
                if is_subtask(tasks[p]):
                    errs.append(f"{i}: parent {p} is itself a subtask; a subtask never has subtasks of its own")
            elif p not in epics:
                errs.append(f"{i}: parent '{p}' is no task or epic in the ledger")
        if NEEDS_OWNER in t and not str(t.get(NEEDS_OWNER) or "").strip():
            errs.append(f"{i}: {NEEDS_OWNER} says, in words, what {OWNER} does on the task")
        # A subtask's life: the hub dispatches it (covers frozen), the department agent starts it
        # with --by and hands it in (submit, status stays in progress), and only the hub's confirm marks it done.
        if is_subtask(t):
            if "covers" in t and not isinstance(t["covers"], list):
                errs.append(f"{i}: covers is a list of task ids")
            for c in t.get("covers") or []:
                if c not in tasks:
                    errs.append(f"{i}: covers '{c}', which is no task in the ledger")
            if t.get("status") in ("in-progress", "done"):
                if not t.get("dispatchedAt") or t.get("covers") is None:
                    errs.append(f"{i}: a subtask is dispatched (covers and dispatchedAt recorded) before it starts")
                if not t.get("heldBy"):
                    errs.append(f"{i}: a subtask in progress or done names who holds it (start --by)")
                if t.get("kind") == "review" and t.get("covers") == []:
                    errs.append(f"{i}: a review in progress or done covers at least one task")
                for c in t.get("covers") or []:
                    if c in tasks and tasks[c].get("status") not in FINISHED:
                        errs.append(f"{i}: is {t['status']} but {c}, which it covers, is {tasks[c].get('status')}")
            if t.get("submittedAt") and t.get("status") not in ("in-progress", "done"):
                errs.append(f"{i}: handed in (submittedAt) but status is {t.get('status')}")
            if t.get("status") == "done":
                c = t.get("confirmed")
                if not (isinstance(c, dict) and c.get("by") and c.get("at") and c.get("evidence")) \
                        or not t.get("submittedAt"):
                    errs.append(f"{i}: a done subtask was handed in (submit) and then confirmed by the hub (confirm)")
                elif str(c["by"]).strip().lower() == str(t.get("heldBy", "")).strip().lower():
                    errs.append(f"{i}: confirmed by its own holder ({c['by']}); a different context, the hub, confirms")
            elif t.get("confirmed"):
                errs.append(f"{i}: carries 'confirmed' but status is {t.get('status')}")
            fs = t.get("findings")
            if fs is not None and not isinstance(fs, list):
                errs.append(f"{i}: findings is a list")
            ns = [f.get("n") for f in fs or [] if isinstance(f, dict)]
            if len(set(ns)) != len(ns):
                errs.append(f"{i}: two findings share a number")
            for f in fs or []:
                if not isinstance(f, dict):
                    errs.append(f"{i}: a finding is an object")
                    continue
                n = f"{i}#{f.get('n')}"
                if not isinstance(f.get("n"), int):
                    errs.append(f"{n}: a finding is numbered")
                if f.get("severity") not in SEVERITIES:
                    errs.append(f"{n}: severity '{f.get('severity')}' is not one of {SEVERITIES}")
                if not str(f.get("text", "")).strip() or not f.get("by") or not f.get("at"):
                    errs.append(f"{n}: a finding has text, 'by' and 'at'")
                if not has_pointer(f.get("evidence")):
                    errs.append(f"{n}: a finding's evidence points at something checkable")
                r = f.get("routed")
                if r is not None:
                    if not (isinstance(r, dict) and r.get("by") and r.get("at")
                            and (r.get("task") in tasks or len(str(r.get("rejected", "")).strip()) >= REASON_MIN)):
                        errs.append(f"{n}: routed is null, {{task, by, at}} naming a task in the ledger, or "
                                    f"{{rejected, by, at}} with a reason of {REASON_MIN}+ characters")
        elif any(k in t for k in ("covers", "dispatchedAt", "submittedAt", "confirmed", "findings")):
            errs.append(f"{i}: covers, dispatch, findings, submit and confirm belong on a department subtask "
                        f"(a task with 'kind')")
        acc = t.get("acceptance") or {}
        if not (isinstance(acc, dict) and acc.get("check") and acc.get("expect")):
            errs.append(f"{i}: acceptance needs both 'check' and 'expect'")
        for f in t.get("files") or []:
            nf = norm(f)
            if nf.startswith("/") or re.match(r"^[A-Za-z]:", nf) or ".." in nf.split("/"):
                errs.append(f"{i}: file '{f}' must be repo-relative")
        for dep in t.get("dependsOn") or []:
            if dep not in tasks and dep not in decisions:
                errs.append(f"{i}: dependsOn '{dep}' does not exist")
        if t.get("status") in ("in-progress", "done"):
            for dep in t.get("dependsOn") or []:
                if dep in tasks and tasks[dep].get("status") not in FINISHED:
                    errs.append(f"{i}: is {t['status']} but dependency {dep} is {tasks[dep].get('status')}")
                if dep in decisions and decisions[dep].get("status") != "decided":
                    errs.append(f"{i}: is {t['status']} but decision {dep} is still open")
        if t.get("status") == "done" and not t.get("evidence"):
            errs.append(f"{i}: done without evidence (say what ran and what it showed)")
        if t.get("status") == "blocked" and not t.get("blockedOn"):
            errs.append(f"{i}: blocked without 'blockedOn' naming the question")
        for w in t.get("waitsOn") or []:
            if w not in tasks and w not in decisions:
                errs.append(f"{i}: waitsOn '{w}' does not exist")
        if t.get("waitsOn") and t.get("status") != "blocked":
            errs.append(f"{i}: has waitsOn but status is {t.get('status')} (only a blocked task waits)")
        if t.get("status") == "blocked" and t.get("waitsOn") and all(
                satisfied(ledger, w) for w in t["waitsOn"]):
            errs.append(f"{i}: stale blocker: everything in waitsOn ({', '.join(t['waitsOn'])}) is decided or "
                        f"finished, so it is not blocked any more (save() releases it)")
        if t.get("landed") and not RE_COMMIT_ID.fullmatch(t["landed"]):
            errs.append(f"{i}: landed '{t['landed']}' is not a {VCS.id_name}")
        if t.get("landed") and t.get("status") != "done":
            errs.append(f"{i}: has landed but status is {t.get('status')}")
        for n, fa in enumerate(t.get("filesAdded") or []):
            if not (isinstance(fa, dict) and fa.get("paths") and fa.get("on")
                    and len(str(fa.get("reason", "")).strip()) >= REASON_MIN):
                errs.append(f"{i}: filesAdded entry {n + 1} needs 'paths', 'on' and a 'reason' of {REASON_MIN}+ characters")
            elif any(not path_allowed(p, t.get("files") or []) for p in fa["paths"]):
                errs.append(f"{i}: filesAdded entry {n + 1} names a path that is not in the task's files")

    # dependency cycles
    graph = {t["id"]: [d for d in (t.get("dependsOn") or []) if d in tasks] for t in ledger["tasks"]}
    state: dict[str, int] = {}

    def visit(n: str, stack: list[str]) -> None:
        state[n] = 1
        for m in graph.get(n, []):
            if state.get(m) == 1:
                errs.append(f"{n}: dependency cycle {' -> '.join(stack + [n, m])}")
            elif state.get(m) is None:
                visit(m, stack + [n])
        state[n] = 2

    for n in graph:
        if state.get(n) is None:
            visit(n, [])

    # a task may not depend on a task in a LATER wave
    for t in ledger["tasks"]:
        for dep in t.get("dependsOn") or []:
            if dep in tasks and tasks[dep].get("wave", 0) > t.get("wave", 0):
                errs.append(f"{t['id']}: wave {t.get('wave')} depends on {dep} in later wave {tasks[dep].get('wave')}")

    for o in ledger.get("outsideCommits", []):
        for k in ("id", "subject", "reason", "recordedOn"):
            if not o.get(k):
                errs.append(f"outsideCommits: entry missing '{k}': {o}")
    return errs


# --------------------------------------------------------------------------- queries

def ready_tasks(ledger: dict) -> list[dict]:
    _, tasks, decisions = index(ledger)
    out = []
    for t in ledger["tasks"]:
        if t["status"] != "planned":
            continue
        ok = True
        for dep in t.get("dependsOn") or []:
            if dep in tasks and tasks[dep]["status"] not in FINISHED:
                ok = False
            if dep in decisions and decisions[dep]["status"] != "decided":
                ok = False
        # A subtask also waits for what it covers: a review cannot start before its parent lands.
        if ok and is_subtask(t):
            ok = all(tasks[c]["status"] in FINISHED for c in covered_ids(ledger, t) if c in tasks)
        if ok:
            out.append(t)
    return out


def current_wave(ledger: dict) -> int | None:
    waves = sorted({t["wave"] for t in ledger["tasks"] if t["status"] not in FINISHED})
    return waves[0] if waves else None


def satisfied(ledger: dict, ident: str) -> bool | None:
    """True once a decision is decided or a task is done or withdrawn; None for an id the ledger does not hold."""
    _, tasks, decisions = index(ledger)
    if ident in decisions:
        return decisions[ident]["status"] == "decided"
    if ident in tasks:
        return tasks[ident]["status"] in FINISHED
    return None


def id_state(ledger: dict, ident: str) -> str:
    """'decided 2026-10-14', 'open', 'in-progress', ... - what an id named in a blocker stands at now."""
    _, tasks, decisions = index(ledger)
    if ident in decisions:
        d = decisions[ident]
        return f"decided {d.get('ruledOn', '')}".rstrip() if d["status"] == "decided" else d["status"]
    if ident in tasks:
        return tasks[ident]["status"]
    return "unknown id"


def unmet_ids(ledger: dict, text: str) -> list[str]:
    """The decisions and tasks a piece of text names that are not yet decided or finished, in order, once each."""
    return [i for i in dict.fromkeys(RE_ANY_ID.findall(text or "")) if satisfied(ledger, i) is False]


def release_blocked(ledger: dict) -> list[str]:
    """Move every blocked task whose 'waitsOn' ids are all decided or finished back to planned, with a dated note.
    Returns the ids released. A blocker with no waitsOn waits on a person or an event and is never released here."""
    out = []
    for t in ledger.get("tasks", []):
        w = t.get("waitsOn") or []
        if t.get("status") != "blocked" or not w or not all(satisfied(ledger, x) for x in w):
            continue
        t["status"] = "planned"
        for k in ("blockedOn", "waitsOn", "blockedAt"):
            t.pop(k, None)
        cmd_note(ledger, t["id"], f"Released from blocked: {', '.join(f'{x} {id_state(ledger, x)}' for x in w)}.")
        out.append(t["id"])
    return out


def days_since(stamp: str | None) -> int | None:
    """Whole days from a 'YYYY-MM-DD...' stamp to today on this machine's clock; None when there is no stamp."""
    try:
        return (datetime.now().date() - datetime.strptime(str(stamp)[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


def last_touched(t: dict) -> str | None:
    """The newest of a task's dated notes, 'blockedAt' and 'startedAt' (YYYY-MM-DD), or None."""
    dates = RE_NOTE_STAMP.findall(t.get("notes", "")) + [str(t[k])[:10] for k in ("blockedAt", "startedAt") if t.get(k)]
    # A subtask is also touched when it is dispatched, a finding is recorded, or it is handed in.
    dates += [str(t[k])[:10] for k in ("dispatchedAt", "submittedAt") if t.get(k)]
    dates += [str(f.get("at"))[:10] for f in t.get("findings") or [] if isinstance(f, dict) and f.get("at")]
    return max(dates) if dates else None


def age_text(stamp: str | None) -> str:
    """'2026-10-16 (0 d)', with a STALE flag once it is STALE_DAYS old; a task never dated is flagged too."""
    days = days_since(stamp)
    if days is None:
        return "never dated  STALE? re-check before reporting it"
    flag = "  STALE? re-check before reporting it" if days >= STALE_DAYS else ""
    return f"{stamp[:10]} ({days} d){flag}"


def cmd_next(ledger: dict) -> int:
    epics, tasks, decisions = index(ledger)
    total = len([t for t in ledger["tasks"] if t["status"] != "withdrawn"])
    done = len([t for t in ledger["tasks"] if t["status"] == "done"])
    wave = current_wave(ledger)
    print(f"Progress: {done} of {total} tasks done.")
    # First, because it is the newest instruction there is: the owner answered a decision on the
    # website and no session has turned it into a ruling yet.
    waiting = waiting_answers(ledger)
    if waiting:
        print(f"ANSWERED BY {OWNER.upper()} ON THE WEBSITE, NOT YET APPLIED:")
        for d, a in waiting:
            said = a.get("text", "") if a.get("choice") == OTHER else a.get("choice", "")
            note = f"  | note: {a['note']}" if str(a.get("note", "")).strip() else ""
            print(f"  ANSWERED     {d['id']}  -> {said}{note}  (answered {a.get('answeredAt', '?')})")
        print("  Apply each with `ledger.py apply <decision>`, commit the ledger (--role Ledger), then start what it gates.")
    # Department subtasks the hub has sent out: handed in and waiting for its confirm, or still
    # with their agent. Listed here, not among the hub's own in-progress work below.
    sent = [t for t in ledger["tasks"] if is_subtask(t) and t.get("dispatchedAt") and t["status"] in ("in-progress", "blocked")]
    to_confirm = [t for t in sent if t["status"] == "in-progress" and t.get("submittedAt")]
    running = [t for t in sent if t not in to_confirm]
    if to_confirm:
        print("TO CONFIRM (handed in by a department: re-open one pointer, route each finding, then `confirm`):")
        for t in to_confirm:
            fs = [f for f in t.get("findings") or [] if isinstance(f, dict)]
            unrouted = [f"#{f.get('n')}" for f in fs if not f.get("routed")]
            print(f"  TO CONFIRM   {t['id']}  [{t['kind']}, {t['owner']}]  {t['title']}  -- handed in "
                  f"{age_text(t['submittedAt'])}")
            print(f"               {len(fs)} finding(s); not yet routed: {', '.join(unrouted) or 'none'}")
    if running:
        print("RUNNING (a department agent holds these; the ledger cannot see whether it still runs - if it does not, "
              "`dispatch <id> --by \"<hub>\" --restart`):")
        for t in running:
            print(f"  RUNNING      {t['id']}  [{t['kind']}, {t['owner']}]  {t['title']}  -- dispatched by "
                  f"{t.get('dispatchedBy', '?')}, last touched {age_text(last_touched(t))}")
            if t["status"] == "blocked":
                print(f"               blocked on: {t.get('blockedOn')}")
    # Confirm now refuses while any finding is unrouted, so a finding left unrouted here sits on a
    # subtask that stopped (blocked) or was confirmed before that rule. In-progress ones are counted above.
    loose = [(t, f) for t, f in all_findings(ledger, unrouted=True) if t["status"] != "in-progress"]
    if loose:
        print("TO ROUTE (file the fixing task in the wave its work belongs to and `route --to` it, or `route --reject`):")
        for t, f in loose:
            print(f"  FINDING      {t['id']}#{f['n']}  [{f['severity']}]  {f['text']}  -- {f['evidence']}")
    if wave is None:
        # A new project's empty ledger said "Every task is finished."
        print("Every task is finished." if ledger["tasks"] else "The ledger holds no tasks yet.")
        return 0
    wtitle = ledger.get("waves", {}).get(str(wave), "")
    print(f"Current wave: {wave} - {wtitle}")
    active = [t for t in ledger["tasks"] if t["status"] == "in-progress" and t not in sent]
    blocked = [t for t in ledger["tasks"] if t["status"] == "blocked" and t not in sent]
    # Every in-progress and blocked line carries its age, and every id a blocker names carries
    # that id's state now, so a blocker whose decision was answered reads as answered here, not as the owner's to do.
    for t in active:
        print(f"  IN PROGRESS  {t['id']}  (wave {t['wave']})  {t['title']}  -- last touched {age_text(last_touched(t))}")
    # In-progress tasks with no dated note were read
    # as some other session's and walked past for later waves. Nobody held them. Say so where the list is read.
    stale_active = [t for t in active if "STALE?" in age_text(last_touched(t))]
    if stale_active:
        print(f"  -> {len(stale_active)} in-progress task(s) flagged STALE? are nobody's unless a dated note names who "
              f"holds them: continue them, lowest wave first, BEFORE any later-wave READY task below.")
    for t in blocked:
        w = t.get("waitsOn") or []
        named = list(dict.fromkeys(w + RE_ANY_ID.findall(t["blockedOn"])))
        named = [x for x in named if x != t["id"]]
        states = "; ".join(f"{x} {id_state(ledger, x)}" for x in named)
        print(f"  BLOCKED      {t['id']}  {t['title']}  -- last checked {age_text(last_touched(t))}")
        print(f"               waits on: {', '.join(w) if w else 'a person or an event (no id releases it)'}"
              + (f"  | ids it names: {states}" if states else ""))
        print(f"               on: {t['blockedOn']}")
    ready = [t for t in ready_tasks(ledger) if t["wave"] == wave]

    def sent_by(t: dict) -> str:
        return f"  [{t['kind']} for {t['owner']}: dispatch it]" if is_subtask(t) else ""

    for t in ready:
        print(f"  READY        {t['id']}  {t['title']}{sent_by(t)}")
    if not (active or blocked or ready):
        print("  Nothing in this wave can start: every remaining task waits on a dependency.")
    # A blocked task can hold its wave open for a long time (a
    # play session, a decision). Later waves whose dependencies are met can
    # still run, so name them rather than leave them invisible.
    # Whenever this wave has nothing READY, not only when it also has nothing in progress: a
    # handful of stale in-progress tasks in one wave hid every later ready task.
    later = sorted((t for t in ready_tasks(ledger) if t["wave"] > wave), key=lambda t: (t["wave"], t["id"]))
    if later and not ready:
        print("Also ready in later waves (their dependencies are met):")
        for t in later:
            print(f"  READY        {t['id']}  (wave {t['wave']})  {t['title']}{sent_by(t)}")
    open_ = [d for d in ledger["decisions"] if d["status"] == "open"]
    if open_:
        gating = {}
        for t in ledger["tasks"]:
            for dep in t.get("dependsOn") or []:
                if dep in decisions:
                    gating.setdefault(dep, []).append(t)
        first = sorted(open_, key=lambda d: min([x["wave"] for x in gating.get(d["id"], [])] or [99]))[0]
        waves = sorted({x["wave"] for x in gating.get(first["id"], [])})
        print(f"Next decision for {OWNER}: {first['id']} - {first['question']}")
        print(f"  Recommended: {first['recommendation']}")
        for o in first.get("options") or []:
            print(f"  Option: {o['label']}{' (recommended)' if o.get('recommended') else ''} - {o['detail']}")
        print(f"  Gates waves: {waves}  ({len(open_) - 1} more open after this one)")
    return 0


def cmd_status(ledger: dict) -> int:
    for e in ledger["epics"]:
        mine = [t for t in ledger["tasks"] if t["epic"] == e["id"]]
        live = [t for t in mine if t["status"] != "withdrawn"]
        d = len([t for t in live if t["status"] == "done"])
        waves = sorted({t["wave"] for t in mine})
        print(f"{e['id']}  {d:>2}/{len(live):<2}  waves {waves}  {e['status']:<11}  {e['title']}")
    return 0


def cmd_show(ledger: dict, ident: str) -> int:
    epics, tasks, decisions = index(ledger)
    item = tasks.get(ident) or decisions.get(ident) or epics.get(ident)
    if not item:
        print(f"unknown id {ident}", file=sys.stderr)
        return 2
    print(json.dumps(item, indent=2, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- status changes

def set_status(ledger: dict, ident: str, status: str, **extra) -> list[str]:
    _, tasks, decisions = index(ledger)
    t = tasks.get(ident)
    if not t:
        return [f"unknown task {ident}"]
    trial = copy.deepcopy(ledger)
    tt = index(trial)[1][ident]
    tt["status"] = status
    for k, v in extra.items():
        if v is not None:
            tt[k] = v
    if status != "blocked":
        for k in ("blockedOn", "waitsOn", "blockedAt"):
            tt.pop(k, None)
    else:
        tt["blockedAt"] = stamp_now()
        if not tt.get("waitsOn"):
            tt.pop("waitsOn", None)
    if status == "in-progress":
        tt["startedAt"] = stamp_now()
    errs = [e for e in validate(trial) if e.startswith(ident + ":")]
    if errs:
        return errs
    t.clear()
    t.update(tt)
    return []


def block_errors(ledger: dict, ident: str, on: str | None, until: list[str] | None, extra: dict) -> list[str]:
    """Check a `block` and fill extra['blockedOn'] / extra['waitsOn'].

    A new block needs --on. Leaving --on out re-checks a task that is already blocked: same text and waitsOn, new
    date. When the text names a decision or task that is not yet decided or finished, --until must say whether that
    is what releases it (list the ids) or not ('none'), so a blocker cannot quietly outlive its question.
    """
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if on is None:
        if t.get("status") != "blocked":
            return [f"{ident} is {t.get('status')}, not blocked: a new block needs --on saying what it waits on"]
        extra["blockedOn"] = t["blockedOn"]
        if until is None:
            extra["waitsOn"] = t.get("waitsOn")
            return []
    if until is None:
        unmet = [x for x in unmet_ids(ledger, extra["blockedOn"]) if x != ident]
        if unmet:
            return [f"the blocker names {', '.join(unmet)}, not yet decided or finished. Say what releases it: "
                    f"--until {' '.join(unmet)} (released by itself once they are), or --until none (it waits on "
                    f"a person or an event)"]
        return []
    if [x.lower() for x in until] == ["none"]:
        extra["waitsOn"] = None
        return []
    errs = []
    for x in until:
        s = satisfied(ledger, x)
        if s is None:
            errs.append(f"--until {x}: no such decision or task")
        elif s:
            errs.append(f"--until {x}: it is already {id_state(ledger, x)}, so it cannot be what {ident} waits on")
    extra["waitsOn"] = list(dict.fromkeys(until))
    return errs


def cmd_decide(ledger: dict, ident: str, ruling: str, by: str, date: str) -> list[str]:
    _, _, decisions = index(ledger)
    d = decisions.get(ident)
    if not d:
        return [f"unknown decision {ident}"]
    # --by defaulted to the owner, so anything could record a ruling in the owner's name.
    if not (by or "").strip():
        return [f"{ident}: decide names who ruled, with --by ({OWNER}'s website answers go through apply instead)"]
    d["status"] = "decided"
    d["ruling"] = ruling
    d["ruledBy"] = by
    d["ruledOn"] = date
    return []


def cmd_apply(ledger: dict, ident: str, answers: dict) -> list[str]:
    """Turn the answer the owner gave on the website into the decision's ruling.

    Changes the ledger in memory only. The caller saves the ledger first and takes the answer out of the inbox
    after, so a failed save cannot lose the answer.
    """
    _, _, decisions = index(ledger)
    ans = answers.get(ident)
    if not isinstance(ans, dict):
        return [f"no website answer on file for {ident}"]
    d = decisions.get(ident)
    errs = answer_errors(d, str(ans.get("choice", "")), str(ans.get("text", "")))
    if errs:
        return [f"{ident}: the answer on file cannot be applied: {e}" for e in errs]
    errs = cmd_decide(ledger, ident, answer_ruling(d, ans), OWNER, str(ans.get("answeredAt", ""))[:10])
    if errs:
        return errs
    d["chosen"] = ans["choice"]
    d["ruledVia"] = "website"
    if str(ans.get("note", "")).strip():
        d["note"] = ans["note"].strip()
    return []


# --------------------------------------------------------------------------- department subtasks
# The hub dispatches a subtask (this prints the agent's prompt), the department agent starts it
# with --by, records findings and hands it in with submit; the hub re-checks one pointer and confirms, which is the
# only way a subtask becomes done. Docs/Ledger/README.md "Department subtasks".

def dump_folder(ident: str) -> str:
    """Where the graph dump for a review subtask goes (Saved/ is not versioned: the dump is a working copy)."""
    return f"Saved/Ledger/review_{ident}_{datetime.now().strftime('%Y-%m-%d')}"


def dump_command(ident: str) -> str:
    """What dumps a review's Blueprint graphs inside the running editor (Docs/Ledger/review_graph_dump.py): Python
    run in the editor, from its Output Log (Python mode) or over Unreal's remote execution."""
    script = (HERE / "review_graph_dump.py").as_posix()
    return (f"in the editor's Python: exec(open(r'{script}', encoding='utf-8').read()); "
            f"run('{ident}')")


def covered_files(ledger: dict, covers: list[str]) -> list[str]:
    tasks = index(ledger)[1]
    return list(dict.fromkeys(f for c in covers if c in tasks for f in tasks[c].get("files") or []))


def needs_dump(files: list[str], root: Path | None = None) -> bool:
    """A review covering Blueprints needs their graphs dumped first: the department agent has no editor, and an
    offline reader sees property values, not graph wiring. Never in a project that is not
    Unreal (ledger_config.json).

    A .uasset or .umap file counts. A Content/ folder counts when it holds one, or when it is not there to look in
    (it may be filled before the review runs). root is the repository to look in; the default is this one."""
    if not UNREAL:
        return False
    base = REPO if root is None else root

    # Any Content/ folder counted, so a review covering a Content/ folder
    # of JSON files only was refused for want of a graph dump, while review_graph_dump.py --plan, which walks the folder, found
    # nothing to dump and refused too. A folder that exists now counts only if it holds an asset, as the dump script
    # judges it.
    def holds_assets(folder: str) -> bool:
        path = base / folder
        if not path.is_dir():
            return True
        return any(p.suffix.lower() in (".uasset", ".umap") for p in path.rglob("*") if p.is_file())

    # A map (.umap) holds a level Blueprint, so it counts too.
    return any(norm(f).endswith((".uasset", ".umap"))
               or (norm(f).endswith("/") and norm(f).startswith("Content/") and holds_assets(norm(f)))
               for f in files)


DUMP_MAPS_LINE = "Maps not dumped"  # review_graph_dump.py writes this line for maps it cannot read


def dump_index_line(ident: str, covers: list[str]) -> str:
    """The first line review_graph_dump.py writes to _index.txt, naming the review and exactly what it covers."""
    return f"Review {ident}, covering {', '.join(covers)}."


def dump_errors(folder: Path, ident: str, covers: list[str]) -> list[str]:
    """Why a dump folder cannot be used for this review: it has no _index.txt (the run did not finish, or the folder is
    empty or another's), or its index is for a different review or a different set of covered tasks (stale).
    Any folder that existed was taken."""
    idx = folder / "_index.txt"
    if not idx.is_file():
        return [f"{folder} has no _index.txt: the dump did not finish. Run it again"]
    first = (idx.read_text(encoding="utf-8").splitlines() or [""])[0]
    if not first.startswith(dump_index_line(ident, covers)):
        return [f"{folder}/_index.txt is for '{first[:120]}', not this review as it stands "
                f"('{dump_index_line(ident, covers)}'). Dump again"]
    return []


def dump_maps(folder: str | None) -> str | None:
    """The line naming the maps a dump could not read ('Maps not dumped ...'), or None."""
    try:
        lines = (REPO / folder / "_index.txt").read_text(encoding="utf-8").splitlines() if folder else []
    except FileNotFoundError:
        return None
    return next((x for x in lines if x.startswith(DUMP_MAPS_LINE)), None)


def unmet_for_dispatch(ledger: dict, t: dict) -> list[str]:
    """What keeps a planned subtask from being dispatched: unfinished dependencies or covered tasks, open decisions."""
    _, tasks, decisions = index(ledger)
    out = []
    for dep in t.get("dependsOn") or []:
        if dep in tasks and tasks[dep]["status"] not in FINISHED:
            out.append(f"{dep} {tasks[dep]['status']}")
        if dep in decisions and decisions[dep]["status"] != "decided":
            out.append(f"{dep} open")
    for c in covered_ids(ledger, t):
        if c in tasks and tasks[c]["status"] not in FINISHED:
            out.append(f"{c} {tasks[c]['status']} (covered)")
    return list(dict.fromkeys(out))


def dispatch_prompt(ledger: dict, t: dict, dept: dict) -> str:
    """The agent's whole brief, written from the task so nobody retells it from memory."""
    tasks = index(ledger)[1]
    name = f"{t['owner']} agent"
    L = [f"You are the {name}, working ledger task {t['id']}, a {t['kind']} subtask: {t['title']}",
         f"Your name for every --by is \"{name}\".", "",
         "Follow the department steps in your agent file, in order. The first is:",
         f"    python Docs/Ledger/ledger.py show {t['id']}", ""]
    covers = t.get("covers") or []
    if covers:
        L.append(f"What this {t['kind']} covers. Read these files at their landed commits; edit nothing:")
        for c in covers:
            ct = tasks.get(c, {})
            L.append(f"  {c} {ct.get('title', '')} (landed {ct.get('landed', 'not landed')})")
            for f in ct.get("files") or []:
                L.append(f"      {f}")
    else:
        L.append(f"This {t['kind']} covers no other task: its own files list, acceptance and notes are the brief.")
    # A produce is told exactly what it may change.
    if t["kind"] == "produce":
        L += ["", "What you make or change - only these files and folders (this task's files list):"]
        L += [f"  {f}" for f in t.get("files") or []]
    if t.get("dump"):
        L += ["", f"Graph dump of the covered Blueprints: {t['dump']}/ - one file per graph, listed in _index.txt. "
                  f"A finding points at the asset and the node (/Game/... plus the node name), not at the dump file."]
        maps = dump_maps(t["dump"])
        if maps:
            L.append(f"The dump does not hold these maps' level Blueprints ({maps}). Give each such map the verdict "
                     f"'not read', never 'clean'.")
    acc = t.get("acceptance") or {}
    L += ["", f"Check: {acc.get('check', '')}", f"Passing shows: {acc.get('expect', '')}", ""]
    # The editor and game processes are Unreal's; an agent in another project is told only that
    # the hub waits for it.
    if t["kind"] == "review" or dept.get("editor") == "none":
        L.append("Editor: do not use the Unreal editor; everything you need is listed above." if UNREAL
                 else "Everything you need is listed above.")
    elif UNREAL:
        L.append("Editor: you own the Unreal editor for this run; the hub waits until you return. Anything that runs a "
                 f"game process (a server, a client, a load test) needs {OWNER} at the keyboard: if {OWNER} is not "
                 "there, block the task with --until none and return.")
    else:
        L.append("You run in the foreground: the hub waits until you return.")
    # Name every hub-only command, not only done and commit.
    # A produce changes its listed files and nothing else; every other kind edits nothing.
    edits = ("Change only the files and folders listed above; the hub commits them after it confirms."
             if t["kind"] == "produce" else "Do not edit files.")
    L.append("Hand the work in with submit. Your ledger.py commands are show, next, findings, start, finding, submit "
             "and block. The hub's are not yours: do not run dispatch, confirm, route, decide, apply, add-task, "
             f"add-decision, note, done or commit. {edits} Do not run claude or start agents by any route.")
    return "\n".join(L)


def cmd_dispatch(ledger: dict, ident: str, by: str | None, dump: str | None,
                 restart: bool) -> tuple[list[str], dict | None]:
    """Freeze what a subtask covers, record who sent it, and return the agent to start and its prompt."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"], None
    if not is_subtask(t):
        return [f"{ident} has no 'kind': only a department subtask is dispatched"], None
    if not (by or "").strip():
        return ["dispatch needs --by \"<the hub session's title>\": the department's reply goes there"], None
    dept = (department_index(ledger) or {}).get(t["owner"])
    if not dept or dept.get("hub"):
        return [f"{ident}: owner '{t['owner']}' is not a department on the list"], None
    if not dept.get("agent"):
        return [f"{ident}: {t['owner']} has no agent file yet (meta.json departments 'agent')"], None
    st = t["status"]
    if st in FINISHED:
        return [f"{ident} is {st}"], None
    if t.get("submittedAt"):
        return [f"{ident} has been handed in ({t['submittedAt']}): the hub confirms it (or routes its findings), it is "
                f"not dispatched again"], None
    if st in ("in-progress", "blocked") and not restart:
        return [f"{ident} is already {st} (dispatched {t.get('dispatchedAt', '?')} by {t.get('dispatchedBy', '?')}). "
                f"The ledger cannot tell whether that agent still runs; if it does not, dispatch again with --restart"], None
    if st == "planned":
        unmet = unmet_for_dispatch(ledger, t)
        if unmet:
            return [f"{ident} is not ready to dispatch: {', '.join(unmet)}"], None
    covers = t["covers"] if t.get("covers") is not None else covered_ids(ledger, t)
    # A review of nothing was ready at once and passed as complete.
    if t["kind"] == "review" and not covers:
        return [f"{ident}: this review covers no task (its parent {t.get('parent')} holds none without kind); file it "
                f"against a parent with work in it"], None
    folder = dump or t.get("dump")
    if t["kind"] == "review" and needs_dump(covered_files(ledger, covers)):
        if not folder or not (REPO / folder).is_dir():
            return [f"{ident}: this review covers Blueprints, so their graphs are dumped first, with the editor open:\n"
                    f"      {dump_command(ident)}\n"
                    f"    then dispatch again with --dump {dump_folder(ident)}"], None
        errs = dump_errors(REPO / folder, ident, covers)
        if errs:
            return [f"{ident}: {errs[0]}:\n      {dump_command(ident)}"], None
    t["covers"] = covers
    t["dispatchedAt"] = stamp_now()
    t["dispatchedBy"] = by.strip()
    if folder:
        t["dump"] = norm(folder)
    if restart and st != "planned":
        cmd_note(ledger, ident, f"Restarted by {by.strip()}: a new {dept['agent']} agent is started on this task; the "
                                f"findings already recorded stay.")
    return [], {"agent": dept["agent"], "background": t["kind"] == "review", "prompt": dispatch_prompt(ledger, t, dept)}


def start_errors(ledger: dict, ident: str, by: str | None) -> list[str]:
    """A subtask is started by its department agent, after the hub dispatched it, and names that agent."""
    t = index(ledger)[1].get(ident)
    if not t or not is_subtask(t):
        return []
    if not (by or "").strip():
        return [f"{ident} is a department subtask: start it with --by \"{t['owner']} agent\""]
    if not t.get("dispatchedAt"):
        return [f"{ident} has not been dispatched: the hub runs `ledger.py dispatch {ident} --by \"<hub title>\"` first"]
    # Start again on a handed-in subtask replaced the holder that confirm checks.
    want = f"{t['owner']} agent"
    if by.strip() != want:
        return [f"{ident}: a {t['owner']} subtask is started by --by \"{want}\", not \"{by.strip()}\""]
    if t.get("submittedAt"):
        return [f"{ident} has been handed in ({t['submittedAt']}); the hub confirms it, it is not started again"]
    if t["status"] == "in-progress" and t.get("heldBy") and t["heldBy"] != want:
        return [f"{ident} is in progress under {t['heldBy']}; the restart path is `dispatch {ident} --restart`"]
    return []


def cmd_submit(ledger: dict, ident: str, evidence: str) -> list[str]:
    """The department agent hands its work in. The task stays in progress until the hub confirms it."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if not is_subtask(t):
        return [f"{ident} has no 'kind': submit is for department subtasks; close a task with done"]
    if t["status"] != "in-progress":
        return [f"{ident} is {t['status']}, not in progress"]
    if not has_pointer(evidence):
        return [f"{ident}: the evidence names nothing checkable - give {pointer_kinds()}"]
    # A review names every file it covers with a verdict, so one that read one
    # file cannot be handed in as a review of all of them.
    if t["kind"] == "review":
        said = norm(evidence)
        missing = [f for f in covered_files(ledger, t.get("covers") or []) if norm(f) not in said]
        if missing:
            return [f"{ident}: a review names every covered file with its verdict (clean or finding N); the evidence "
                    f"does not name {', '.join(missing)}"]
    t["evidence"] = evidence.strip()
    t["submittedAt"] = stamp_now()
    return []


def hub_errors(t: dict, by: str, new_hub: str | None) -> list[str]:
    """Only the hub that dispatched a subtask confirms it or routes its findings, unless a new hub says why it takes over
    (the old hub session is gone). Any name but the holder's was taken."""
    sent = t.get("dispatchedBy")
    if sent and by.strip() != sent and len((new_hub or "").strip()) < REASON_MIN:
        return [f"{t['id']} was dispatched by {sent}; confirm and route come from that hub. A new hub taking over says why "
                f"with --new-hub \"<why>\" ({REASON_MIN}+ characters), and it is recorded"]
    return []


def cmd_confirm(ledger: dict, ident: str, by: str | None, evidence: str, new_hub: str | None = None) -> list[str]:
    """The hub re-checked one pointer: the subtask becomes done. The one way a subtask is marked done."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if not is_subtask(t):
        return [f"{ident} has no 'kind': confirm is for department subtasks"]
    if t["status"] != "in-progress" or not t.get("submittedAt"):
        return [f"{ident} has not been handed in (it is {t['status']}, with no submit)"]
    if not (by or "").strip():
        return ["confirm needs --by \"<the hub session's title>\""]
    if by.strip().lower() == str(t.get("heldBy", "")).strip().lower():
        return [f"{ident}: --by is its own holder ({t.get('heldBy')}); the hub, a different context, confirms"]
    errs = hub_errors(t, by, new_hub)
    if errs:
        return errs
    # Every finding, not only blockers: with no hardening wave to wait for, one left unrouted at
    # confirm has nowhere to surface but `next`, and the review's context is freshest now.
    open_findings = [f"#{f.get('n')}" for f in t.get("findings") or [] if isinstance(f, dict) and not f.get("routed")]
    if open_findings:
        return [f"{ident}: finding(s) {', '.join(open_findings)} not routed yet: `route {ident}#<n> --to <task>` "
                f"or `--reject \"<why>\"` first"]
    if not has_pointer(evidence):
        return [f"{ident}: say which pointer was re-opened and what it showed (a path, "
                + ("a /Game/ asset and node, " if UNREAL else "") + f"or a {VCS.id_name})"]
    confirmed = {"by": by.strip(), "at": stamp_now(), "evidence": evidence.strip()}
    if t.get("dispatchedBy") and by.strip() != t["dispatchedBy"]:
        confirmed["newHub"] = new_hub.strip()
    return set_status(ledger, ident, "done", confirmed=confirmed)


def cmd_finding(ledger: dict, ident: str, severity: str, text: str, evidence: str) -> tuple[list[str], int | None]:
    """A department agent records one finding on its subtask, the moment it finds it. Returns its number."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"], None
    if not is_subtask(t):
        return [f"{ident} has no 'kind': findings are recorded on department subtasks"], None
    if t["status"] != "in-progress":
        return [f"{ident} is {t['status']}, not in progress: findings are recorded while the work runs"], None
    if severity not in SEVERITIES:
        return [f"severity '{severity}' is not one of {SEVERITIES}"], None
    if not (text or "").strip():
        return ["a finding needs --text saying what is wrong"], None
    if not has_pointer(evidence):
        return [f"{ident}: the finding's evidence names nothing checkable - give {pointer_kinds()}"], None
    fs = t.setdefault("findings", [])
    n = max([f.get("n", 0) for f in fs if isinstance(f, dict)] or [0]) + 1
    fs.append({"n": n, "severity": severity, "text": text.strip(), "evidence": evidence.strip(),
               "by": t.get("heldBy") or "?", "at": stamp_now(), "routed": None})
    return [], n


def all_findings(ledger: dict, unrouted: bool = False, severity: str | None = None) -> list[tuple[dict, dict]]:
    """(task, finding) for every finding in the ledger, finished epics included, oldest task first."""
    out = []
    for t in ledger["tasks"]:
        for f in t.get("findings") or []:
            if not isinstance(f, dict) or (unrouted and f.get("routed")) or (severity and f.get("severity") != severity):
                continue
            out.append((t, f))
    return out


def routed_text(f: dict) -> str:
    r = f.get("routed")
    if not r:
        return "not routed"
    return f"routed to {r['task']}" if r.get("task") else f"rejected: {r.get('rejected')}"


def cmd_route(ledger: dict, ref: str, to: str | None, reject: str | None, by: str | None,
              new_hub: str | None = None) -> list[str]:
    """The hub routes one finding: to the task that fixes it, or rejected with a reason. Either severity routes at any
    time; the fixing task's wave says when the work runs. Findings routed under the old rule may carry 'early' (the
    word for filing ahead of the old hardening wave); it is kept as history and nothing reads it."""
    m = RE_FINDING_REF.fullmatch((ref or "").strip())
    if not m:
        return [f"'{ref}' is not a finding: write it as {PREFIX}-Enn.mm#n"]
    ident, n = m.group(1), int(m.group(2))
    tasks = index(ledger)[1]
    t = tasks.get(ident)
    f = next((x for x in (t or {}).get("findings") or [] if isinstance(x, dict) and x.get("n") == n), None)
    if not f:
        return [f"{ref}: no such finding"]
    if f.get("routed"):
        return [f"{ref} is already {routed_text(f)}"]
    if bool(to) == bool(reject):
        return ["route takes one of --to <task> or --reject \"<why>\""]
    if not (by or "").strip():
        return ["route needs --by \"<the hub session's title>\""]
    errs = hub_errors(t, by, new_hub)
    if errs:
        return errs
    extra = {}
    if t.get("dispatchedBy") and by.strip() != t["dispatchedBy"]:
        extra["newHub"] = new_hub.strip()
    if to:
        if to not in tasks or to == ident:
            return [f"--to {to}: no other task by that id; file the fixing task first (add-task)"]
        f["routed"] = {"task": to, "by": by.strip(), "at": stamp_now(), **extra}
    else:
        if len(reject.strip()) < REASON_MIN:
            return [f"--reject needs a reason of at least {REASON_MIN} characters"]
        f["routed"] = {"rejected": reject.strip(), "by": by.strip(), "at": stamp_now(), **extra}
    return []


# --------------------------------------------------------------------------- render

def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def finished_epics(ledger: dict) -> set[str]:
    """Epics that have tasks and whose every task is done or withdrawn.

    LEDGER.md keeps one line for each and leaves out their task tables and task detail, which
    had grown the page past 4,000 lines. The rule reads the tasks, not the epic's own status field, so an epic is
    back in full the moment a task is added to it. Nothing leaves the JSON: open tasks depend on finished ones, and
    the audit matches old commits to them. A finished task is read with `show <id>` or on the ledger website.
    """
    by_epic: dict[str, list[dict]] = {}
    for t in ledger["tasks"]:
        by_epic.setdefault(t["epic"], []).append(t)
    return {e for e, mine in by_epic.items() if mine and all(t["status"] in FINISHED for t in mine)}


def _epic_section(ledger: dict, e: dict) -> list[str]:
    mine = [t for t in ledger["tasks"] if t["epic"] == e["id"]]
    live = [t for t in mine if t["status"] != "withdrawn"]
    d = len([t for t in live if t["status"] == "done"])
    L = [f"### {e['id']} - {e['title']}", "",
         f"Status **{e['status']}**, {d} of {len(live)} done. Plan: `{e['plan']}`."]
    if e.get("goal"):
        L.append("")
        L.append(e["goal"])
    L.append("")
    L.append("| ID | Task | Wave | Size | Status | Depends on | Landed |")
    L.append("|---|---|---|---|---|---|---|")
    for t in mine:
        deps = ", ".join(t.get("dependsOn") or []) or "-"
        L.append(f"| {t['id']} | {md_escape(t['title'])} | {t['wave']} | {t['size']} | {t['status']} | "
                 f"{deps} | {t.get('landed', '-')} |")
    L.append("")
    return L


def _task_detail(t: dict) -> list[str]:
    L = [f"### {t['id']} - {t['title']}", "",
         f"- **Epic:** {t['epic']}. **Wave:** {t['wave']}. **Owner:** {t['owner']}. "
         f"**Size:** {t['size']}. **Status:** {t['status']}."]
    if t.get("kind"):
        L.append(f"- **Department subtask:** {t['kind']}" + (f", parent {t['parent']}" if t.get("parent") else "") + "."
                 + (f" Covers {', '.join(t['covers']) or 'no other task'}." if t.get("covers") is not None else "")
                 + (f" Dispatched {t['dispatchedAt']} by {t.get('dispatchedBy', '?')}." if t.get("dispatchedAt") else "")
                 + (f" Held by {t['heldBy']}." if t.get("heldBy") else "")
                 + (f" Graph dump `{t['dump']}`." if t.get("dump") else ""))
    if t.get("heldBy") and not t.get("kind"):
        L.append(f"- **Held by:** {t['heldBy']}")
    if t.get(NEEDS_OWNER):
        L.append(f"- **Needs {OWNER}:** {t[NEEDS_OWNER]}")
    if t.get("dependsOn"):
        L.append(f"- **Depends on:** {', '.join(t['dependsOn'])}")
    L.append("- **Files it may edit:**")
    for f in t["files"]:
        L.append(f"  - `{f}`")
    L.append(f"- **Check:** {t['acceptance']['check']}")
    L.append(f"- **Passing shows:** {t['acceptance']['expect']}")
    if t.get("notes"):
        L.append(f"- **Notes:** {t['notes']}")
    if t.get("blockedOn"):
        L.append(f"- **Blocked on:** {t['blockedOn']}")
    if t.get("findings"):
        L.append("- **Findings:**")
        for f in t["findings"]:
            L.append(f"  - #{f.get('n')} {f.get('severity')}: {f.get('text')} ({f.get('evidence')}; {f.get('by')}, "
                     f"{f.get('at')}) - {routed_text(f)}")
    if t.get("evidence"):
        L.append(f"- **Evidence:** {t['evidence']}" + (f" (handed in {t['submittedAt']})" if t.get("submittedAt") else ""))
    if t.get("confirmed"):
        c = t["confirmed"]
        L.append(f"- **Confirmed:** by {c.get('by')}, {c.get('at')}: {c.get('evidence')}")
    if t.get("landed"):
        L.append(f"- **Landed:** `{t['landed']}`")
    L.append("")
    return L


def render(ledger: dict) -> str:
    epics, tasks, decisions = index(ledger)
    fin = finished_epics(ledger)
    L: list[str] = []
    L.append(f"# {ledger.get('title', 'Ledger')}")
    L.append("")
    L.append("> **Rendered from `Docs/Ledger/meta.json` and `Docs/Ledger/epics/*.json` by "
             "`python Docs/Ledger/ledger.py render`. Do not hand-edit: change the ledger with ledger.py.**")
    L.append("")
    total = len([t for t in ledger["tasks"] if t["status"] != "withdrawn"])
    done = len([t for t in ledger["tasks"] if t["status"] == "done"])
    wave = current_wave(ledger)
    L.append(f"Progress: **{done} of {total}** tasks done. Current wave: **{wave}**. "
             f"Audit window starts after `{ledger['auditFrom']}`.")
    L.append("")
    if ledger.get("summary"):
        L.append(ledger["summary"])
        L.append("")

    L.append("## Sequence")
    L.append("")
    L.append("| Wave | What | Editor |")
    L.append("|---|---|---|")
    for w, text in sorted(ledger.get("waves", {}).items(), key=lambda kv: int(kv[0])):
        ed = ledger.get("waveEditor", {}).get(w, "")
        L.append(f"| {w} | {md_escape(text)} | {ed} |")
    L.append("")

    L.append("## Decisions")
    L.append("")
    L.append("| ID | Question | Recommended | Status | Ruling |")
    L.append("|---|---|---|---|---|")
    for d in ledger["decisions"]:
        ruling = d.get("ruling", "")
        if ruling:
            ruling = f"{ruling} ({d.get('ruledBy', '')}, {d.get('ruledOn', '')})"
        L.append(f"| {d['id']} | {md_escape(d['question'])} | {md_escape(d['recommendation'])} | "
                 f"{d['status']} | {md_escape(ruling)} |")
    L.append("")

    L.append("## Epics")
    L.append("")
    for e in ledger["epics"]:
        if e["id"] not in fin:
            L.extend(_epic_section(ledger, e))

    if fin:
        L.append("### Finished epics")
        L.append("")
        L.append("Every task in these is done or withdrawn. Read one with `python Docs/Ledger/ledger.py show <id>` "
                 "or on the ledger website.")
        L.append("")
        L.append("| ID | Epic | Tasks done | Withdrawn | Plan |")
        L.append("|---|---|---|---|---|")
        for e in ledger["epics"]:
            if e["id"] in fin:
                mine = [t for t in ledger["tasks"] if t["epic"] == e["id"]]
                n_done = len([t for t in mine if t["status"] == "done"])
                L.append(f"| {e['id']} | {md_escape(e['title'])} | {n_done} | {len(mine) - n_done} | `{e['plan']}` |")
        L.append("")

    L.append("## Task detail")
    L.append("")
    if fin:
        L.append("Tasks of the finished epics are not listed here; see Finished epics above.")
        L.append("")
    for t in ledger["tasks"]:
        if t["epic"] not in fin:
            L.extend(_task_detail(t))

    L.append("## Gates")
    L.append("")
    for k, v in ledger["gates"].items():
        L.append(f"- **{k}:** {v}")
    L.append("")
    L.append("## Conventions")
    L.append("")
    for k, v in ledger["conventions"].items():
        L.append(f"- **{k}:** {v}")
    L.append("")
    if ledger.get("outsideCommits"):
        L.append("## Outside commits recorded")
        L.append("")
        for o in ledger["outsideCommits"]:
            L.append(f"- `{o['id']}` {md_escape(o['subject'])} - {md_escape(o['reason'])} "
                     f"(recorded {o['recordedOn']})")
        L.append("")
    return "\n".join(L)


def _on_disk(ledger: dict | None) -> dict:
    """The ledger as the files hold it now. LEDGER.md is a view of the files, every session's saves included, so
    it is rendered from a fresh read and not from a copy this command loaded earlier. A ledger that did not come
    from the default files (a test fixture) is used as it is."""
    tracked = _SNAPSHOTS.get(id(ledger)) if ledger is not None else None
    if ledger is None or (tracked and tracked[1] == HERE):
        return load(track=False)
    return ledger


def render_check(ledger: dict | None = None) -> list[str]:
    want = render(_on_disk(ledger))
    try:
        have = LEDGER_MD.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{LEDGER_MD_REL} does not exist; run render"]
    if have.replace("\r\n", "\n") != want:
        return [f"{LEDGER_MD_REL} is stale or hand-edited; run render"]
    return []


def write_render(ledger: dict | None = None) -> None:
    _write_atomic(LEDGER_MD, render(_on_disk(ledger)))


# --------------------------------------------------------------------------- commit rules (pure)

def subject_id(subject: str) -> str | None:
    m = RE_SUBJECT_ID.match(subject.strip())
    return m.group(1) if m else None


def role_lines(message: str) -> list[str]:
    return RE_ROLE_LINE.findall(message)


def files_outside(task: dict, paths: list[str]) -> list[str]:
    """The paths a commit naming this task would carry that its files list does not allow."""
    return [norm(p) for p in paths if not is_ledger_file(p) and not path_allowed(p, task.get("files") or [])]


def add_files(ledger: dict, ident: str, paths: list[str], reason: str, on: str) -> list[str]:
    """Add paths to a task's files list, recording why in its filesAdded. In memory; the caller saves."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"{ident}: no such task"]
    if len((reason or "").strip()) < REASON_MIN:
        return [f"{ident}: adding files to the task needs --reason of at least {REASON_MIN} characters"]
    new = [norm(p) for p in paths if not path_allowed(p, t["files"])]
    if not new:
        return []
    t["files"].extend(new)
    t.setdefault("filesAdded", []).append({"paths": new, "reason": reason.strip(), "on": on})
    return []


def check_commit(ledger: dict, message: str, role: str | None, reason: str | None,
                 paths: list[str]) -> list[str]:
    errs: list[str] = []
    epics, tasks, _ = index(ledger)
    subject = message.strip().splitlines()[0] if message.strip() else ""
    ident = subject_id(subject)
    paths = [norm(p) for p in paths]
    if not paths:
        errs.append("no paths given; paths are explicit and there is no -a")
    roles = role_lines(message) + ([role] if role else [])
    for r in roles:
        if r not in ROLES:
            errs.append(f"role '{r}' is not one of {ROLES}")
    if role and not (reason and len(reason.strip()) >= REASON_MIN):
        errs.append(f"a role commit needs --reason of at least {REASON_MIN} characters")
    if not ident and not roles:
        errs.append(f"subject names no task ({PREFIX}-Enn.mm) or epic ({PREFIX}-Enn), and no --role was given")
        return errs

    if ident and RE_TASK.match(ident):
        t = tasks.get(ident)
        if not t:
            errs.append(f"{ident}: no such task")
            return errs
        if t["status"] == "planned":
            errs.append(f"{ident}: task is still planned; run `start {ident}` first")
        if t["status"] == "done" and t.get("landed") and role != "Repair":
            errs.append(f"{ident}: already done and landed at {t['landed']}; new work on it is --role Repair")
        if t["status"] == "done" and not t.get("landed") and epic_rel(t["epic"]) not in paths:
            errs.append(f"{ident}: closing a task commits {epic_rel(t['epic'])} with it")
        if not role:
            outside = files_outside(t, paths)
            if outside:
                errs.append(f"{ident}: paths outside the task's files list: {outside}. If they are this task's work, "
                            f"commit again with --reason \"<why they belong to it>\" ({REASON_MIN}+ characters) and they "
                            f"are added to its files list")
    elif ident and RE_EPIC.match(ident):
        if ident not in epics:
            errs.append(f"{ident}: no such epic")
            return errs
        if epic_rel(ident) not in paths:
            errs.append(f"{ident}: an epic-level commit changes the ledger, so {epic_rel(ident)} is among the paths")
        if not role:
            allowed = [f for t in ledger["tasks"] if t["epic"] == ident for f in t["files"]]
            outside = [p for p in paths if not is_ledger_file(p) and not path_allowed(p, allowed)]
            if outside:
                errs.append(f"{ident}: paths outside every file list in the epic: {outside}")

    if any(is_ledger_data(p) for p in paths):
        errs.extend(validate(ledger))
        errs.extend(render_check(ledger))
    return errs


# --------------------------------------------------------------------------- audit (pure core)

def judge_commits(ledger: dict, commits: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """commits: newest first, each {id, subject, message, paths}. Returns (errors, in_scope, out_of_scope)."""
    errs: list[str] = []
    in_scope: list[str] = []
    out: list[str] = []
    epics, tasks, _ = index(ledger)
    recorded = {o["id"]: o for o in ledger.get("outsideCommits", [])}
    active_files = [f for t in ledger["tasks"] if t["status"] == "in-progress" for f in t["files"]]
    for c in commits:
        ident = subject_id(c["subject"])
        roles = role_lines(c.get("message", ""))
        bad_roles = [r for r in roles if r not in ROLES]
        if bad_roles:
            errs.append(f"{c['id']}: carries role(s) {bad_roles} that are not in {ROLES}")
        if ident:
            in_scope.append(c["id"])
            if RE_TASK.match(ident):
                t = tasks.get(ident)
                if not t:
                    errs.append(f"{c['id']}: names unknown task {ident}")
                elif t["status"] == "planned":
                    errs.append(f"{c['id']}: names {ident}, which is still planned")
            elif ident not in epics:
                errs.append(f"{c['id']}: names unknown epic {ident}")
            continue
        if roles and not bad_roles:
            in_scope.append(c["id"])
            continue
        touched = [p for p in c.get("paths", []) if path_allowed(p, active_files)]
        if touched:
            if c["id"] in recorded:
                in_scope.append(c["id"])
            else:
                errs.append(f"{c['id']}: names no task or role but touched in-progress task files {touched[:5]}; "
                            f"record it in outsideCommits with a reason, or re-commit through this tool")
        else:
            out.append(c["id"])
    ids_in_window = {c["id"] for c in commits}
    for t in ledger["tasks"]:
        if t["status"] != "done":
            continue
        if not t.get("landed"):
            errs.append(f"{t['id']}: done but has no landed commit")
        if not t.get("evidence"):
            errs.append(f"{t['id']}: done but has no evidence")
        if t.get("landed") and commit_num(t["landed"]) > commit_num(ledger["auditFrom"]):
            if t["landed"] not in ids_in_window:
                errs.append(f"{t['id']}: landed {t['landed']} is not in the {VCS.cmd} log window")
            else:
                c = next(c for c in commits if c["id"] == t["landed"])
                if subject_id(c["subject"]) != t["id"]:
                    errs.append(f"{t['id']}: landed {t['landed']} does not name the task in its subject")
    for o in ledger.get("outsideCommits", []):
        if commit_num(o["id"]) > commit_num(ledger["auditFrom"]) and o["id"] not in ids_in_window:
            errs.append(f"outsideCommits {o['id']}: not in the {VCS.cmd} log window (a stale entry reassures)")
    return errs, in_scope, out


def new_commit_id(output: str) -> str | None:
    """The id a commit just created, read from the version control's output (the backend's rule)."""
    return VCS.new_commit_id(output)


def commit_num(cid: str) -> int:
    """A number that grows with each commit, so ids compare by when they were made (the backend's rule)."""
    return VCS.order(cid)


# --------------------------------------------------------------------------- version control
# Every call goes through the configured backend, VCS (apply_config). dv, parse_log and _dv_log_page keep the names
# older scripts call them by.

def dv(*args: str) -> str:
    """Run the version control's command with these arguments and return what it printed."""
    return VCS.run(*args)


def parse_log(text: str) -> list[dict]:
    return VCS.parse_log(text)


def commit_paths(cid: str, cache: dict | None = None) -> list[str]:
    """The paths a commit changed. A commit never changes, so `cache` (the audit's, kept in PATHS_CACHE) is
    trusted once filled."""
    if cache is not None and cid in cache:
        return cache[cid]
    paths = VCS.commit_paths(cid)
    if cache is not None:
        cache[cid] = paths
    return paths


def walk_log(fetch, mark: str, max_pages: int = 50) -> list[dict]:
    """Every commit from head back to `mark` (inclusive when reached), newest first. fetch(until) returns one
    log page of at most LOG_LIMIT commits, from head when until is None, else at or before that time.
    Dv log answers 1000 commits at most, and auditFrom had fallen more than 1000 commits back, so the
    audit refused before it judged anything."""
    commits: list[dict] = []
    seen: set[str] = set()
    until = None
    for _ in range(max_pages):
        page = parse_log(fetch(until))
        fresh = [c for c in page if c["id"] not in seen]
        commits.extend(fresh)
        seen.update(c["id"] for c in fresh)
        if mark in seen or not fresh or LOG_LIMIT is None or len(page) < LOG_LIMIT:
            break
        oldest = min(fresh, key=lambda c: commit_num(c["id"]))
        try:
            # one second after the oldest, so commits sharing its second come back (and are de-duplicated)
            when = datetime.fromisoformat(oldest.get("date", "").replace("Z", "+00:00")) + timedelta(seconds=1)
        except ValueError:
            break
        until = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    commits.sort(key=lambda c: commit_num(c["id"]), reverse=True)
    return commits


def _dv_log_page(until: str | None) -> str:
    return VCS.log_page(until)


def cmd_audit(ledger: dict, quiet: bool = False) -> list[str]:
    mark = ledger["auditFrom"]
    commits = walk_log(_dv_log_page, mark)
    if not commits:
        return [f"audit: {VCS.cmd} log returned nothing; a scan that sees nothing cannot pass"]
    ids = [c["id"] for c in commits]
    if mark not in ids:
        return [f"audit: {VCS.cmd} log ({len(commits)} commits, paged) does not reach auditFrom {mark}; "
                f"the window cannot judge every commit after the mark"]
    window = commits[: ids.index(mark)]
    try:
        cache = json.loads(PATHS_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    for c in window:
        c["paths"] = commit_paths(c["id"], cache)
    with contextlib.suppress(OSError):
        _write_atomic(PATHS_CACHE, json.dumps(cache))
    errs, ins, outs = judge_commits(ledger, window)
    if not quiet:
        head = commits[0]["id"]
        print(f"audit: {len(window)} commits after {mark} (head {head}); {len(ins)} in the ledger's scope, "
              f"{len(outs)} outside it")
        if outs:
            print(f"  outside scope (other work, not judged): {len(outs)} commits, newest "
                  f"{', '.join(outs[:12])}" + (" ..." if len(outs) > 12 else ""))
    return errs


# --------------------------------------------------------------------------- commit (effects)

def changed_paths(under: str) -> dict[str, str]:
    """Uncommitted changes under a repo path: {path: status letter}, from the version control."""
    return VCS.changed_paths(under)


def commit_plan(ledger: dict, message: str, role: str | None, paths: list[str],
                ledger_changes: dict[str, str]) -> tuple[list[str], list[str]]:
    """The paths a commit really carries, and notes saying why they differ from the caller's. Pure.
    - Docs/Ledger/ledger.json (the single file's old name) stands for the named task's or epic's file, or in a
      role commit every ledger file with changes - unless the change is the old file's own deletion.
    - A task or epic commit always carries that epic's ledger file and LEDGER.md."""
    paths = [norm(p) for p in paths]
    notes: list[str] = []
    epics, tasks, _ = index(ledger)
    ident = subject_id(message.strip().splitlines()[0]) if message.strip() else None
    epic = (tasks[ident]["epic"] if ident and RE_TASK.match(ident) and ident in tasks
            else ident if ident and RE_EPIC.match(ident) and ident in epics else None)
    if LEDGER_REL in paths and ledger_changes.get(LEDGER_REL) != "D":
        paths = [p for p in paths if p != LEDGER_REL]
        stand_in = ([epic_rel(epic)] if epic else
                    sorted(p for p in ledger_changes if is_ledger_data(p) and p != LEDGER_REL))
        paths += [p for p in stand_in if p not in paths]
        notes.append(f"{LEDGER_REL} is split into meta.json and epics/; committing "
                     f"{', '.join(stand_in) or 'no ledger file'} in its place")
    if epic:
        paths += [p for p in (epic_rel(epic), LEDGER_MD_REL) if p not in paths]
    return paths, notes


def do_commit(ledger: dict, message: str, role: str | None, reason: str | None, agent: str | None,
              paths: list[str], dry_run: bool) -> int:
    def refuse(errs: list[str]) -> int:
        print("REFUSED:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    paths, notes = commit_plan(ledger, message, role, paths, changed_paths("Docs/Ledger"))
    for n in notes:
        print(f"note: {n}")
    ident = subject_id(message.strip().splitlines()[0]) if message.strip() else None
    added: list[str] = []
    task = index(ledger)[1].get(ident) if ident and RE_TASK.match(ident) else None
    if task and not role and reason and files_outside(task, paths):
        # A file the task needs is added to its list with a written reason, not refused.
        added = files_outside(task, paths)
        if dry_run:
            print(f"dry run: would add to {ident}'s files: {added} (reason: {reason.strip()})")
            paths_checked = [p for p in paths if p not in added]
            errs = check_commit(ledger, message, role, reason, paths_checked)
            return refuse(errs) if errs else (print("dry run: every other rule passed; paths:", paths) or 0)
        errs = add_files(ledger, ident, added, reason, datetime.now().strftime("%Y-%m-%d"))
        if errs:
            return refuse(errs)
        errs = [e for e in validate(ledger) if e.startswith(ident + ":")]
        if errs:
            return refuse(errs)
        save(ledger)
        write_render(ledger)
        print(f"added to {ident}'s files: {', '.join(added)}")
    errs = check_commit(ledger, message, role, reason, paths)
    if errs:
        return refuse(errs)
    body = message.rstrip()
    if added:
        body += f"\n\nFiles added to {ident}'s list: {', '.join(added)}\nWhy: {reason.strip()}"
    if role:
        body += f"\n\nRole: {role}\nReason: {reason.strip()}"
    if agent:
        body += f"\n\nCo-Authored-By: {agent} <noreply@anthropic.com>"
    if dry_run:
        print("dry run: every rule passed; would commit:")
        print("  paths:", paths)
        print("  message:\n" + "\n".join("    " + l for l in body.splitlines()))
        return 0
    cid, out = VCS.commit(paths, body)
    if not cid:
        print(out)
        print(f"commit ran but no commit id was found in {VCS.cmd}'s output; read `{VCS.cmd} log -n 1` before "
              f"continuing", file=sys.stderr)
        return 1
    print(f"committed {cid}")
    print(VCS.show(cid))
    if ident and RE_TASK.match(ident):
        ledger2 = load()
        t = index(ledger2)[1].get(ident)
        if t and t["status"] == "done" and not t.get("landed"):
            t["landed"] = cid
            save(ledger2)
            write_render(ledger2)
            stamp = f"{ident} ledger: landed {cid}"
            if agent:
                stamp += f"\n\nCo-Authored-By: {agent} <noreply@anthropic.com>"
            cid2, _ = VCS.commit([epic_rel(t["epic"]), LEDGER_MD_REL], stamp)
            print(f"stamped landed={cid} on {ident}; ledger committed as "
                  f"{cid2 or f'(id not found in {VCS.cmd} output)'}")
            if cid2:
                print(VCS.show(cid2))
    return 0


# --------------------------------------------------------------------------- selftest

def _fixture() -> dict:
    return {
        "schema": "example.ledger", "schemaVersion": 1, "title": "t", "auditFrom": "dv.commit.10",
        "gates": {"g": "x"}, "conventions": {"c": "x"}, "waves": {"1": "a"}, "outsideCommits": [],
        "departments": [{"name": "Engineering", "hub": True, "agent": None, "does": "files and hands out", "editor": "owns"},
                        {"name": "Security Department", "agent": None, "does": "reviews", "editor": "none"}],
        "decisions": [{"id": "PR-D01", "epic": "PR-E01", "question": "q", "recommendation": "r", "status": "open",
                       "options": [{"label": "Yes", "detail": "do it", "recommended": True},
                                   {"label": "No", "detail": "leave it"}]}],
        "epics": [{"id": "PR-E01", "title": "e", "status": "in-progress", "plan": "p.md"}],
        "tasks": [
            {"id": "PR-E01.01", "epic": "PR-E01", "title": "a", "owner": "Engineering", "size": "S", "wave": 1,
             "files": ["Source/A.cpp", "Content/Dir/"], "acceptance": {"check": "c", "expect": "e"},
             "status": "in-progress"},
            {"id": "PR-E01.02", "epic": "PR-E01", "title": "b", "owner": "Engineering", "size": "S", "wave": 1,
             "files": ["Source/B.cpp"], "dependsOn": ["PR-E01.01"], "acceptance": {"check": "c", "expect": "e"},
             "status": "planned"},
            {"id": "PR-E01.03", "epic": "PR-E01", "title": "c", "owner": "Engineering", "size": "S", "wave": 2,
             "files": ["Source/C.cpp"], "dependsOn": ["PR-D01"], "acceptance": {"check": "c", "expect": "e"},
             "status": "planned"},
        ],
    }


def _decided(f: dict) -> dict:
    """The fixture with PR-D01 decided."""
    cmd_decide(f, "PR-D01", "yes", "Sam", "2026-10-16")
    return f


def selftest() -> int:
    """Planted faults must each refuse and clean controls must pass. The fixtures are written in fixed
    settings (SELFTEST_PROFILE), so the checks run under those whatever this project's config says; a made-up project's
    settings are checked near the end. The project's own config is put back after."""
    saved = dict(CONFIG)
    apply_config(SELFTEST_PROFILE)
    try:
        return _selftest()
    finally:
        apply_config(saved)


def _selftest() -> int:
    fails = 0
    ran = 0

    def expect(name: str, errs: list[str], should_refuse: bool, needle: str = "") -> None:
        nonlocal fails, ran
        ran += 1
        refused = bool(errs) and (not needle or any(needle in e for e in errs))
        ok = refused if should_refuse else not errs
        print(f"  {'PASS' if ok else 'FAIL'}  {'refuses' if should_refuse else 'passes '}  {name}"
              + ("" if ok else f"   got: {errs}"))
        if not ok:
            fails += 1

    print("validate:")
    base = _fixture()
    expect("clean ledger (control)", validate(base), False)
    f = _fixture(); f["tasks"].append(copy.deepcopy(f["tasks"][0]))
    expect("duplicate task id", validate(f), True, "duplicate id")
    f = _fixture(); f["tasks"][1]["dependsOn"] = ["PR-E09.09"]
    expect("dependsOn an unknown id", validate(f), True, "does not exist")
    f = _fixture(); f["tasks"][0]["dependsOn"] = ["PR-E01.02"]
    expect("dependency cycle", validate(f), True, "cycle")
    f = _fixture(); f["tasks"][0]["status"] = "done"
    expect("done without evidence", validate(f), True, "without evidence")
    f = _fixture(); f["tasks"][1]["status"] = "in-progress"
    expect("in progress while a dependency is unfinished", validate(f), True, "dependency PR-E01.01")
    f = _fixture(); f["tasks"][2]["status"] = "in-progress"
    expect("in progress while a gating decision is open", validate(f), True, "still open")
    f = _fixture(); f["epics"][0]["status"] = "done"
    expect("done epic carrying unfinished tasks", validate(f), True, "unfinished")
    f = _fixture(); f["epics"][0]["status"] = "planned"
    expect("an epic marked planned with a task in progress", validate(f), True, "does not match its tasks")
    f = _fixture()
    for t in f["tasks"]:
        t["status"] = "done"; t["evidence"] = "ran-it"; t["landed"] = "dv.commit.11"
    f["decisions"][0].update(status="decided", ruling="x", ruledOn="2026-10-12")
    expect("an epic marked in-progress with every task finished", validate(f), True, "does not match its tasks")
    changed = sync_epic_status(f)
    ok = changed == ["PR-E01"] and f["epics"][0]["status"] == "done" and not validate(f)
    expect("saving sets the epic to done when its tasks are (control)", [] if ok else [f"changed={changed} {validate(f)}"], False)
    f = _fixture(); del f["tasks"][0]["acceptance"]["expect"]
    expect("acceptance without expect", validate(f), True, "acceptance")
    f = _fixture(); f["tasks"][0]["files"] = ["C:/abs/path.cpp"]
    expect("absolute file path", validate(f), True, "repo-relative")
    f = _fixture(); f["tasks"][0]["wave"] = 1; f["tasks"][1]["dependsOn"] = ["PR-E01.03"]; f["tasks"][1]["wave"] = 1
    expect("dependency in a later wave", validate(f), True, "later wave")
    f = _fixture(); f["decisions"][0]["status"] = "decided"
    expect("decided decision without a ruling", validate(f), True, "ruling")
    # withdraw
    f = _fixture()
    expect("withdraw an unknown task", cmd_withdraw(f, "PR-E01.99", "the plan changed for good"), True, "unknown task")
    expect("withdraw with too short a reason", cmd_withdraw(f, "PR-E01.02", "no"), True, "reason of at least")
    f = _fixture(); f["tasks"][0]["status"] = "done"; f["tasks"][0]["evidence"] = "ran-it"
    expect("withdraw a done task", cmd_withdraw(f, "PR-E01.01", "the plan changed for good"), True, "only an unfinished")
    f = _fixture()
    errs = cmd_withdraw(f, "PR-E01.02", "the plan changed for good")
    t = index(f)[1]["PR-E01.02"]
    ok = not errs and t["status"] == "withdrawn" and "Withdrawn: the plan changed for good" in t.get("notes", "") and not validate(f)
    expect("withdraw a planned task, its reason noted (control)", errs or ([] if ok else [f"status={t['status']} {validate(f)}"]), False)

    print("commit rules:")
    ok_paths = ["Source/A.cpp", "Content/Dir/Sub/X.uasset"]
    expect("task commit inside its files (control)",
           [e for e in check_commit(base, "PR-E01.01 work", None, None, ok_paths)], False)
    expect("subject names nothing and no role", check_commit(base, "did stuff", None, None, ok_paths), True,
           "names no task")
    expect("subject names a planned task", check_commit(base, "PR-E01.02 early", None, None, ["Source/B.cpp"]),
           True, "still planned")
    expect("path outside the task's files", check_commit(base, "PR-E01.01 x", None, None, ["Source/Z.cpp"]),
           True, "outside the task")
    expect("role line in the message that is not a role",
           check_commit(base, "x\n\nRole: Audio", None, None, ok_paths), True, "not one of")
    expect("role without a reason", check_commit(base, "x", "Checkpoint", "", ok_paths), True, "reason")
    expect("role commit with a reason (control)",
           check_commit(base, "Checkpoint everything", "Checkpoint", "full checkpoint at owner request", ["Any/Path"]),
           False)
    f = _fixture(); f["tasks"][0]["status"] = "done"; f["tasks"][0]["evidence"] = "ran-it"
    expect("closing a task without the ledger among the paths",
           check_commit(f, "PR-E01.01 close", None, None, ["Source/A.cpp"]), True, "commits Docs/Ledger")
    f["tasks"][0]["landed"] = "dv.commit.11"
    expect("new work on a landed task without Repair",
           check_commit(f, "PR-E01.01 more", None, None, ["Source/A.cpp"]), True, "Repair")
    expect("prefix look-alike is not a task id", [] if subject_id("PR-E01.011 x") is None else ["matched"], False)

    print("audit:")
    commits = [
        {"id": "dv.commit.14", "subject": "unrelated art", "message": "unrelated art", "paths": ["Content/Art/X.uasset"]},
        {"id": "dv.commit.13", "subject": "PR-E01.01 work", "message": "PR-E01.01 work", "paths": ["Source/A.cpp"]},
        {"id": "dv.commit.12", "subject": "Checkpoint", "message": "Checkpoint\n\nRole: Checkpoint", "paths": ["Source/A.cpp"]},
        {"id": "dv.commit.11", "subject": "tweak", "message": "tweak", "paths": ["Content/Dir/Y.uasset"]},
    ]
    errs, ins, outs = judge_commits(base, commits)
    expect("unledgered commit touching an in-progress task's files", errs, True, "dv.commit.11")
    expect("out-of-scope commit is counted, not refused (control)",
           [] if outs == ["dv.commit.14"] else [f"outs={outs}"], False)
    f = _fixture(); f["outsideCommits"] = [{"id": "dv.commit.11", "subject": "tweak", "reason": "owner hotfix in editor",
                                            "recordedOn": "2026-09-29"}]
    errs, _, _ = judge_commits(f, commits)
    expect("the same commit once recorded in outsideCommits (control)", errs, False)
    bad = commits + []
    bad[1] = {"id": "dv.commit.13", "subject": "PR-E01.02 early", "message": "PR-E01.02 early", "paths": []}
    errs, _, _ = judge_commits(f, bad)
    expect("audited commit naming a planned task", errs, True, "still planned")
    f2 = copy.deepcopy(f); f2["tasks"][0]["status"] = "done"; f2["tasks"][0]["evidence"] = "ran-it"
    errs, _, _ = judge_commits(f2, commits)
    expect("done task with no landed", errs, True, "no landed")
    f2["tasks"][0]["landed"] = "dv.commit.14"
    errs, _, _ = judge_commits(f2, commits)
    expect("landed commit that does not name the task", errs, True, "does not name")
    f3 = copy.deepcopy(f); f3["outsideCommits"].append({"id": "dv.commit.99", "subject": "x", "reason": "stale entry here",
                                                        "recordedOn": "2026-09-29"})
    errs, _, _ = judge_commits(f3, commits)
    expect("stale outsideCommits entry", errs, True, "stale")

    print("log parser:")
    sample = ("commit dv.commit.5 (dv.branch.1)\nAuthor: a <b>\nDate:   2026-09-29T00:00:00Z\n\n"
              "\tPR-E01.01 subject line\n\n\tbody\n\tRole: Repair\n\n\n"
              "commit dv.commit.4 (dv.branch.1)\nAuthor: a <b>\nDate:   2026-09-28T00:00:00Z\n\n\tolder\n")
    parsed = parse_log(sample)
    good = (len(parsed) == 2 and parsed[0]["subject"] == "PR-E01.01 subject line"
            and role_lines(parsed[0]["message"]) == ["Repair"] and parsed[1]["subject"] == "older")
    expect("parses subjects and role lines (control)", [] if good else [f"parsed={parsed}"], False)
    g = Git()
    sample = ("\x1e" + "a" * 40 + "\x1f2026-09-29T00:00:00+00:00\x1fPR-E01.01 subject line\n\nbody\nRole: Repair\n\n"
              "\x1e" + "b" * 40 + "\x1f2026-09-28T00:00:00+00:00\x1folder\n")
    parsed = g.parse_log(sample)
    good = (len(parsed) == 2 and parsed[0]["id"] == "git:" + "a" * 12 and g.RE_ID.fullmatch(parsed[1]["id"])
            and parsed[0]["subject"] == "PR-E01.01 subject line" and role_lines(parsed[0]["message"]) == ["Repair"]
            and parsed[1]["subject"] == "older" and g.sha(parsed[0]["id"]) == "a" * 12)
    expect("git: parses ids, subjects and role lines (control)", [] if good else [f"parsed={parsed}"], False)
    expect("git: a bare hash is not a commit id", [] if not g.RE_ID.fullmatch("a" * 12) else ["matched"], False)

    print("commit id parsing:")
    got = new_commit_id("Committing on top of dv.commit.403...\nNew commit ID: dv.commit.404")
    expect("takes the New commit ID line, not the parent", [] if got == "dv.commit.404" else [f"got {got}"], False)
    got = new_commit_id("parent dv.commit.403 then dv.commit.404")
    expect("falls back to the highest id, not the first", [] if got == "dv.commit.404" else [f"got {got}"], False)

    print("decision options and website answers:")
    f = _fixture(); del f["decisions"][0]["options"]
    expect("an open decision with no options", validate(f), True, "needs 'options'")
    f = _fixture(); f["decisions"][0]["options"] = f["decisions"][0]["options"][:1]
    expect("an open decision with one option", validate(f), True, "needs 'options'")
    f = _fixture(); f["decisions"][0]["options"][1]["label"] = "Yes"
    expect("two options with one label", validate(f), True, "labels must differ")
    f = _fixture(); f["decisions"][0]["options"][1]["label"] = OTHER
    expect("an option labelled Other", validate(f), True, "free-text choice")
    f = _fixture(); f["decisions"][0]["options"][1]["recommended"] = True
    expect("two recommended options", validate(f), True, "at most one")
    f = _fixture(); f["decisions"][0]["options"][1]["detail"] = ""
    expect("an option with no detail", validate(f), True, "needs a 'label' and a 'detail'")
    f = _fixture()
    f["decisions"][0].update(status="decided", ruling="x", ruledOn="2026-10-12"); del f["decisions"][0]["options"]
    f["tasks"][2]["dependsOn"] = []
    expect("a decided decision needs no options (control)", validate(f), False)
    when = "2026-10-12 10:45"
    f = _fixture()
    errs = cmd_apply(f, "PR-D01", {"PR-D01": {"choice": "Yes", "note": "go", "answeredAt": when}})
    d0 = f["decisions"][0]
    ok = (not errs and d0["status"] == "decided" and d0["ruling"] == "Yes. do it Sam's note: go"
          and d0["ruledBy"] == "Sam" and d0["ruledOn"] == "2026-10-12" and d0["ruledVia"] == "website" and not validate(f))
    expect("a website answer becomes the ruling (control)", [] if ok else [f"errs={errs} decision={d0}"], False)
    f = _fixture()
    errs = cmd_apply(f, "PR-D01", {"PR-D01": {"choice": OTHER, "text": "something else", "answeredAt": when}})
    ok = not errs and f["decisions"][0]["ruling"] == "something else"
    expect("a free-text answer becomes the ruling (control)", [] if ok else [f"errs={errs}"], False)
    expect("an answer naming no listed option", cmd_apply(_fixture(), "PR-D01", {"PR-D01": {"choice": "Maybe"}}), True, "not one of")
    expect("a free-text answer with no text", cmd_apply(_fixture(), "PR-D01", {"PR-D01": {"choice": OTHER, "text": " "}}), True, "written out")
    expect("apply with nothing on file", cmd_apply(_fixture(), "PR-D01", {}), True, "no website answer")
    f = _fixture(); f["decisions"][0].update(status="decided", ruling="x", ruledOn="2026-10-12")
    expect("an answer for a decision already decided", cmd_apply(f, "PR-D01", {"PR-D01": {"choice": "Yes"}}), True, "already decided")

    print("render (a finished epic is one line):")
    f = _fixture()
    ok = "### PR-E01.01 - a" in render(f) and "### Finished epics" not in render(f)
    expect("an epic with open tasks is rendered in full (control)", [] if ok else ["live epic rendered wrong"], False)
    f = _fixture()
    for t in f["tasks"]:
        t["status"] = "done"; t["evidence"] = "ran-it"; t["landed"] = "dv.commit.11"
    ok = "### PR-E01.01 - a" not in render(f) and "| PR-E01 | e | 3 | 0 |" in render(f)
    expect("a finished epic is one line, its tasks left out", [] if ok else ["finished epic rendered wrong"], False)
    f["tasks"].append({"id": "PR-E01.04", "epic": "PR-E01", "title": "d", "owner": "Engineering", "size": "S", "wave": 2,
                       "files": ["Source/D.cpp"], "acceptance": {"check": "c", "expect": "e"}, "status": "planned"})
    ok = "### PR-E01.01 - a" in render(f) and "### PR-E01.04 - d" in render(f)
    expect("a new task brings a finished epic back in full", [] if ok else ["epic did not come back"], False)

    print("files outside a task's list, added with a reason:")
    f = _fixture()
    errs = check_commit(f, "PR-E01.01 x", None, "it needs Z for the hook", ["Source/Z.cpp"])
    expect("a path outside the list is still refused until it is added", errs, True, "--reason")
    errs = add_files(f, "PR-E01.01", ["Source/Z.cpp"], "it needs Z for the hook", "2026-10-15")
    ok = (not errs and "Source/Z.cpp" in f["tasks"][0]["files"]
          and f["tasks"][0]["filesAdded"] == [{"paths": ["Source/Z.cpp"], "reason": "it needs Z for the hook", "on": "2026-10-15"}]
          and not validate(f) and not check_commit(f, "PR-E01.01 x", None, None, ["Source/Z.cpp"]))
    expect("added with a reason: listed, recorded, and the commit passes (control)", [] if ok else [f"errs={errs} {validate(f)}"], False)
    expect("adding files with too short a reason", add_files(_fixture(), "PR-E01.01", ["Source/Z.cpp"], "needed", "2026-10-15"),
           True, "reason")
    f = _fixture(); f["tasks"][0]["filesAdded"] = [{"paths": ["Source/Q.cpp"], "reason": "a long enough reason here", "on": "x"}]
    expect("filesAdded naming a path the list does not hold", validate(f), True, "not in the task's files")
    plan, notes = commit_plan(_fixture(), "PR-E01.01 x", None, ["Source/A.cpp", LEDGER_REL], {})
    ok = plan == ["Source/A.cpp", epic_rel("PR-E01"), LEDGER_MD_REL] and notes
    expect("the old ledger.json path stands for the task's epic file (control)", [] if ok else [f"plan={plan}"], False)
    plan, _ = commit_plan(_fixture(), "Checkpoint", "Checkpoint", [LEDGER_REL],
                          {epic_rel("PR-E01"): "M", LEDGER_MD_REL: "M", META_REL: "M"})
    ok = plan == [epic_rel("PR-E01"), META_REL]
    expect("in a role commit it stands for every changed ledger data file (control)", [] if ok else [f"plan={plan}"], False)
    plan, _ = commit_plan(_fixture(), "Checkpoint", "Checkpoint", [LEDGER_REL], {LEDGER_REL: "D"})
    expect("its own deletion is committed as itself (control)", [] if plan == [LEDGER_REL] else [f"plan={plan}"], False)

    print("one file per epic: load, save, conflicts:")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = _fixture()
        del f["decisions"][0]["epic"]  # filed by the task it gates
        f["epics"].append({"id": "PR-E02", "title": "two", "status": "planned", "plan": "q.md"})
        f["tasks"].append({"id": "PR-E02.01", "epic": "PR-E02", "title": "z", "owner": "Engineering", "size": "S", "wave": 2,
                           "files": ["Source/Z.cpp"], "acceptance": {"check": "c", "expect": "e"}, "status": "planned"})
        wrote = save(f, root)
        ok = sorted(wrote) == sorted(["Docs/Ledger/meta.json", "Docs/Ledger/epics/PR-E01.json", "Docs/Ledger/epics/PR-E02.json"])
        expect("a ledger in memory is written as meta.json plus one file per epic (control)", [] if ok else [f"wrote={wrote}"], False)
        back = load(root)
        ok = ([t["id"] for t in back["tasks"]] == [t["id"] for t in f["tasks"]] and back["decisions"][0]["epic"] == "PR-E01"
              and not validate(back))
        expect("loading it back gives the same ledger, the decision filed under the epic it gates (control)",
               [] if ok else [f"{[t['id'] for t in back['tasks']]} {validate(back)}"], False)
        a1, a2 = load(root), load(root)
        index(a1)[1]["PR-E01.01"]["notes"] = "from session one"
        index(a2)[1]["PR-E02.01"]["notes"] = "from session two"
        w1, w2 = save(a1), save(a2)
        both = load(root, track=False)
        ok = (w1 == ["Docs/Ledger/epics/PR-E01.json"] and w2 == ["Docs/Ledger/epics/PR-E02.json"]
              and index(both)[1]["PR-E01.01"].get("notes") == "from session one"
              and index(both)[1]["PR-E02.01"].get("notes") == "from session two")
        expect("two sessions changing different epics both keep their change (control)", [] if ok else [f"w1={w1} w2={w2}"], False)
        b1, b2 = load(root), load(root)
        index(b1)[1]["PR-E01.01"]["notes"] = "one"
        index(b2)[1]["PR-E01.02"]["notes"] = "two"
        save(b1)
        try:
            save(b2)
            errs = []
        except LedgerConflict as e:
            errs = [str(e)]
        expect("a save over a file another session changed after the load", errs, True, "another session changed")
        (root / "ledger.json").write_text("{}", encoding="utf-8")
        try:
            load(root)
            errs = []
        except LedgerError as e:
            errs = [str(e)]
        expect("the old single ledger.json reappearing", errs, True, "exists")
        (root / "ledger.json").unlink()
        c = load(root)
        c["decisions"].append({"id": "PR-D02", "question": "q", "recommendation": "r", "status": "decided", "ruling": "x",
                               "ruledOn": "2026-10-15"})
        try:
            save(c)
            errs = []
        except LedgerError as e:
            errs = [str(e)]
        expect("a decision that names no epic and gates no task cannot be filed", errs, True, "names no 'epic'")
        f = _fixture(); del f["decisions"][0]["epic"]
        f["tasks"][2]["dependsOn"] = []
        expect("validate refuses it too", validate(f), True, "no file to live in")
    f = _fixture()
    errs, ident = cmd_add_task(f, "PR-E01", {"title": "n", "owner": "Engineering", "size": "S", "wave": 2, "files": ["Source/N.cpp"],
                                            "acceptance": {"check": "c", "expect": "e"}})
    ok = not errs and ident == "PR-E01.04" and index(f)[1]["PR-E01.04"]["status"] == "planned"
    expect("add-task takes the next free id in the epic (control)", [] if ok else [f"errs={errs} ident={ident}"], False)
    errs, _ = cmd_add_task(_fixture(), "PR-E01", {"title": "n"})
    expect("add-task with a task missing its fields", errs, True, "missing")
    errs, ident = cmd_add_decision(f, "PR-E01", {"question": "q2", "recommendation": "r2",
                                                 "options": [{"label": "A", "detail": "a", "recommended": True}, {"label": "B", "detail": "b"}]})
    ok = not errs and ident == "PR-D02" and index(f)[2]["PR-D02"]["epic"] == "PR-E01"
    expect("add-decision takes the next free PR-Dnn and files it under the epic (control)", [] if ok else [f"errs={errs}"], False)
    errs, _ = cmd_add_decision(_fixture(), "PR-E01", {"question": "q2", "recommendation": "r2"})
    expect("add-decision without options", errs, True, "options")
    f = _fixture()
    ok = not cmd_note(f, "PR-E01.01", "first") and not cmd_note(f, "PR-E01.01", "second")
    n = index(f)[1]["PR-E01.01"]["notes"]
    ok = ok and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d\. first \d{4}-\d\d-\d\d \d\d:\d\d\. second", n)
    expect("note appends, stamped from the clock (control)", [] if ok else [f"notes={n!r}"], False)

    print("blockers name what releases them:")

    def blocked_fixture() -> dict:
        f = _fixture()
        f["tasks"][2]["dependsOn"] = []
        return f

    f = blocked_fixture(); extra = {"blockedOn": "Decision PR-D01 on the website"}
    expect("a blocker naming an open decision must say whether it releases it",
           block_errors(f, "PR-E01.03", extra["blockedOn"], None, extra), True, "--until PR-D01")
    f = blocked_fixture(); extra = {"blockedOn": "Decision PR-D01 on the website"}
    errs = block_errors(f, "PR-E01.03", extra["blockedOn"], ["PR-D01"], extra) or set_status(f, "PR-E01.03", "blocked", **extra)
    t = index(f)[1]["PR-E01.03"]
    ok = not errs and t.get("waitsOn") == ["PR-D01"] and RE_NOTE_STAMP.match(str(t.get("blockedAt", "")) + ".")
    expect("--until records waitsOn and dates the block (control)", errs or ([] if ok else [f"task={t}"]), False)
    cmd_decide(f, "PR-D01", "yes", "Sam", "2026-10-16")
    expect("validate calls the block stale once its decision is decided", validate(f), True, "stale blocker")
    released = release_blocked(f)
    t = index(f)[1]["PR-E01.03"]
    ok = released == ["PR-E01.03"] and t["status"] == "planned" and "waitsOn" not in t and "blockedOn" not in t \
        and "Released from blocked: PR-D01 decided 2026-10-16" in t.get("notes", "") and not validate(f)
    expect("deciding it releases the task to planned, with a note (control)", [] if ok else [f"task={t}"], False)
    f = blocked_fixture(); extra = {"blockedOn": "Parked by ruling PR-D01"}
    errs = block_errors(f, "PR-E01.03", extra["blockedOn"], ["none"], extra) or set_status(f, "PR-E01.03", "blocked", **extra)
    cmd_decide(f, "PR-D01", "park it", "Sam", "2026-10-16")
    ok = not errs and release_blocked(f) == [] and index(f)[1]["PR-E01.03"]["status"] == "blocked" and not validate(f)
    expect("--until none: a block that waits on a person stays blocked (control)", [] if ok else [f"errs={errs}"], False)
    f = _decided(blocked_fixture()); extra = {"blockedOn": "x"}
    expect("--until a decision already decided", block_errors(f, "PR-E01.03", "x", ["PR-D01"], extra), True,
           "already decided")
    f = blocked_fixture(); extra = {"blockedOn": "x"}
    expect("--until an id the ledger does not hold", block_errors(f, "PR-E01.03", "x", ["PR-D09"], extra), True,
           "no such decision")
    f = blocked_fixture(); extra = {}
    expect("a new block without --on", block_errors(f, "PR-E01.03", None, None, extra), True, "needs --on")
    f = blocked_fixture(); extra = {"blockedOn": "Sam plays it"}
    set_status(f, "PR-E01.03", "blocked", **extra)
    index(f)[1]["PR-E01.03"]["blockedAt"] = "2026-01-01 09:00"
    extra = {}
    errs = block_errors(f, "PR-E01.03", None, None, extra) or set_status(f, "PR-E01.03", "blocked", **extra)
    t = index(f)[1]["PR-E01.03"]
    ok = not errs and t["blockedOn"] == "Sam plays it" and days_since(t["blockedAt"]) == 0
    expect("block without --on re-checks: same text, dated today (control)", errs or ([] if ok else [f"task={t}"]), False)
    f = blocked_fixture(); index(f)[1]["PR-E01.02"]["waitsOn"] = ["PR-D01"]
    expect("waitsOn on a task that is not blocked", validate(f), True, "only a blocked task waits")
    f = blocked_fixture(); t = index(f)[1]["PR-E01.03"]
    t.update({"status": "blocked", "blockedOn": "x", "waitsOn": ["PR-D77"]})
    expect("waitsOn an unknown id", validate(f), True, "does not exist")
    ok = "STALE?" in age_text("2026-01-01 09:00") and "STALE?" in age_text(None) and "STALE?" not in age_text(stamp_now())
    expect("age flags an old or undated blocker, not today's (control)", [] if ok else ["age_text"], False)

    print("audit walks dv log past its 1000-commit page:")

    def fake_fetch(total: int, page: int):
        calls = []

        def fetch(until):
            calls.append(until)
            top = total if until is None else int(until[11:13]) * 3600 + int(until[14:16]) * 60 + int(until[17:19]) - 1
            ids = list(range(top, max(top - page, 0), -1))
            return "".join(f"commit dv.commit.{i} (dv.branch.1)\nAuthor: a <b>\nDate:   "
                           f"2026-10-15T{i // 3600:02d}:{i % 3600 // 60:02d}:{i % 60:02d}Z\n\n\tc{i}\n\n" for i in ids)
        return fetch, calls

    global LOG_LIMIT
    saved_limit, LOG_LIMIT = LOG_LIMIT, 10
    try:
        fetch, calls = fake_fetch(35, 10)
        got = walk_log(fetch, "dv.commit.3")
        ok = [c["id"] for c in got][:1] == ["dv.commit.35"] and "dv.commit.3" in [c["id"] for c in got] and len(calls) == 4
        expect("pages back until it reaches the mark (control)", [] if ok else [f"{len(got)} commits, {len(calls)} calls"], False)
        fetch, calls = fake_fetch(35, 10)
        got = walk_log(fetch, "dv.commit.99")
        ok = "dv.commit.99" not in [c["id"] for c in got] and len(got) == 35
        expect("a mark the log never reaches is not invented (control)", [] if ok else [f"{len(got)}"], False)
    finally:
        LOG_LIMIT = saved_limit

    print("departments and owners:")
    f = _fixture(); f["tasks"][1]["owner"] = "Performance"
    expect("an open task whose owner is not on the departments list", validate(f), True, "not on meta.json's departments")
    f = _fixture(); f["tasks"][0].update(status="done", evidence="ran-it", owner="Engineering, play by Sam")
    expect("a finished task keeps its free-text owner (control)", validate(f), False)
    f = _fixture(); f["tasks"][1]["owner"] = OWNER
    expect("an open task owned by Sam (control)", validate(f), False)
    f = _fixture(); f["tasks"][1]["owner"] = "Security Department"
    expect("an open task owned by a department other than the hub, with no kind", validate(f), True, "needs 'kind'")
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="review", parent="PR-E01.01", files=[])
    expect("a review subtask with a parent and no files (control)", validate(f), False)
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="build", files=[])
    expect("a kind that is not review, measure or produce", validate(f), True, "not one of")
    # A produce changes files, so it lists them.
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="produce", files=[])
    expect("a produce with no files", validate(f), True, "missing 'files'")
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="produce", files=["Content/Art/"])
    expect("a produce that lists what it makes (control)", validate(f), False)
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="review", files=[])
    expect("a review with no parent", validate(f), True, "names its 'parent'")
    f = _fixture(); f["tasks"][1].update(kind="measure", files=[])
    expect("a subtask owned by the hub", validate(f), True, "other than the hub")
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="review", parent="PR-E01.01", files=[])
    f["tasks"][2].update(owner="Security Department", kind="review", parent="PR-E01.02", files=[])
    expect("a subtask whose parent is a subtask", validate(f), True, "itself a subtask")
    f = _fixture(); f["tasks"][1].update(owner="Security Department", kind="review", parent="PR-E01", files=[])
    expect("a review whose parent is an epic (control)", validate(f), False)
    f = _fixture(); f["tasks"][1]["files"] = []
    expect("a task with no kind still needs files", validate(f), True, "missing 'files'")
    f = _fixture(); f["tasks"][1]["needsSam"] = " "
    expect("a needsSam with no words", validate(f), True, "needsSam")
    f = _fixture(); f["tasks"][1]["needsSam"] = "plays it"
    expect("a needsSam saying what Sam does (control)", validate(f), False)
    f = _fixture(); f["departments"][1]["editor"] = "sometimes"
    expect("a department's editor use not in the list", validate(f), True, "editor")
    f = _fixture(); f["departments"][1]["hub"] = True
    expect("two hubs", validate(f), True, "exactly one entry is the hub")
    global AGENTS_DIR
    saved_agents = AGENTS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        AGENTS_DIR = Path(tmp)
        try:
            f = _fixture(); f["departments"][1]["agent"] = "security-department"
            expect("a department naming an agent file that does not exist", validate(f), True, "does not exist")
            (Path(tmp) / "security-department.md").write_text("---\nname: security-department\n---\n", encoding="utf-8")
            expect("the same once the agent file exists (control)", validate(f), False)
        finally:
            AGENTS_DIR = saved_agents

    print("department subtasks: dispatch, start, submit, confirm:")
    import io
    with tempfile.TemporaryDirectory() as tmp:
        AGENTS_DIR = Path(tmp)
        (Path(tmp) / "security-department.md").write_text("---\nname: security-department\n---\n", encoding="utf-8")
        dump_dir = Path(tmp) / "dump"
        dump_dir.mkdir()
        try:
            def sub_fixture(parent: str = "PR-E01.01", landed: bool = True) -> dict:
                f = _fixture()
                f["departments"][1]["agent"] = "security-department"
                f["tasks"][0]["files"] = ["Source/A.cpp"]
                if landed:
                    f["tasks"][0].update(status="done", evidence="ran-it", landed="dv.commit.11")
                f["tasks"].append({"id": "PR-E01.04", "epic": "PR-E01", "title": "review a", "owner": "Security Department",
                                   "kind": "review", "parent": parent, "size": "S", "wave": 1, "files": [],
                                   "acceptance": {"check": "read it", "expect": "a verdict per file"}, "status": "planned"})
                return f

            def go(f: dict, *steps) -> list[str]:
                """Run steps in order, stopping at the first refusal. Each is (fn, args...)."""
                for fn, *args in steps:
                    errs = fn(f, *args)
                    errs = errs[0] if isinstance(errs, tuple) else errs
                    if errs:
                        return errs
                return []

            HUB, AG = "Ledger Work", "Security Department agent"
            sent = lambda f: go(f, (cmd_dispatch, "PR-E01.04", HUB, None, False))
            begun = lambda f: sent(f) or go(f, (start_errors, "PR-E01.04", AG)) or set_status(f, "PR-E01.04", "in-progress", heldBy=AG)
            handed = lambda f: begun(f) or cmd_submit(f, "PR-E01.04", "Source/A.cpp: clean")

            expect("dispatch a task that has no kind", go(sub_fixture(), (cmd_dispatch, "PR-E01.02", HUB, None, False)),
                   True, "has no 'kind'")
            expect("dispatch a review whose parent has not landed", sent(sub_fixture(landed=False)), True, "not ready")
            f = sub_fixture(); errs, out = cmd_dispatch(f, "PR-E01.04", HUB, None, False)
            t = index(f)[1]["PR-E01.04"]
            ok = (not errs and t["covers"] == ["PR-E01.01"] and t["dispatchedBy"] == HUB and t.get("dispatchedAt")
                  and out["agent"] == "security-department" and out["background"]
                  and "python Docs/Ledger/ledger.py show PR-E01.04" in out["prompt"] and "Source/A.cpp" in out["prompt"]
                  and not validate(f))
            expect("dispatch a ready review: covers frozen, prompt written from the task (control)",
                   errs or ([] if ok else [f"task={t} out={out}"]), False)
            expect("dispatch with no --by", go(sub_fixture(), (cmd_dispatch, "PR-E01.04", " ", None, False)), True, "--by")
            f = sub_fixture(); f["departments"][1]["agent"] = None
            expect("dispatch to a department with no agent file", sent(f), True, "no agent file")
            f = sub_fixture(); f["tasks"][0]["files"] = ["Content/Dir/"]
            expect("dispatch a review covering Blueprints with no graph dump", sent(f), True, "graphs are dumped first")
            f = sub_fixture(); f["tasks"][0]["files"] = ["Content/Dir/"]
            expect("dispatch on a dump folder with no _index.txt (an unfinished run)",
                   cmd_dispatch(f, "PR-E01.04", HUB, str(dump_dir), False)[0], True, "did not finish")
            (dump_dir / "_index.txt").write_text(dump_index_line("PR-E01.04", ["PR-E01.01", "PR-E01.02"]) + "\n",
                                                 encoding="utf-8")
            expect("dispatch on a dump made for other covers (stale)",
                   cmd_dispatch(f, "PR-E01.04", HUB, str(dump_dir), False)[0], True, "Dump again")
            (dump_dir / "_index.txt").write_text(dump_index_line("PR-E01.04", ["PR-E01.01"]) + " Read with ...\n"
                                                 + f"{DUMP_MAPS_LINE} (1): /Game/Maps/M\n", encoding="utf-8")
            errs, out = cmd_dispatch(f, "PR-E01.04", HUB, str(dump_dir), False)
            expect("dispatch on a complete dump for this review (control)", errs, False)
            ok = bool(out) and "'not read', never 'clean'" in out["prompt"] and "/Game/Maps/M" in out["prompt"]
            expect("the prompt says which maps the dump could not read (control)", [] if ok else ["not in the prompt"], False)
            expect("a map counts as a Blueprint file (control)", [] if needs_dump(["Content/Maps/M.umap"]) else ["no"], False)
            # A Content/ folder that exists counts only if it holds an asset.
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "Content" / "Data").mkdir(parents=True)
                (root / "Content" / "Data" / "rules.json").write_text("{}", encoding="utf-8")
                (root / "Content" / "Art" / "Sub").mkdir(parents=True)
                (root / "Content" / "Art" / "Sub" / "BP_X.uasset").write_bytes(b"")
                expect("a Content/ folder holding an asset needs a graph dump",
                       [] if not needs_dump(["Content/Art/"], root) else ["needs a graph dump"], True, "graph dump")
                expect("a Content/ folder holding only data files needs no graph dump (control)",
                       [] if not needs_dump(["Content/Data/"], root) else ["asked for a dump"], False)
                expect("a Content/ folder not yet on disk still counts (control)",
                       [] if needs_dump(["Content/Later/"], root) else ["no dump asked"], False)
            f = sub_fixture(); begun(f)
            expect("dispatch a subtask already in progress, without --restart", sent(f), True, "--restart")
            errs, _ = cmd_dispatch(f, "PR-E01.04", HUB, None, True)
            ok = not errs and "Restarted by Ledger Work" in index(f)[1]["PR-E01.04"].get("notes", "")
            expect("--restart starts a new agent on it, with a dated note (control)", errs or ([] if ok else ["no note"]), False)
            f = sub_fixture(); sent(f)
            expect("start a subtask without --by", start_errors(f, "PR-E01.04", None), True, "--by")
            expect("start a subtask the hub has not dispatched", start_errors(sub_fixture(), "PR-E01.04", AG), True,
                   "has not been dispatched")
            f = sub_fixture(); errs = begun(f)
            ok = not errs and index(f)[1]["PR-E01.04"]["heldBy"] == AG and not validate(f)
            expect("start a dispatched subtask with --by: holder recorded (control)", errs or ([] if ok else ["no holder"]), False)
            expect("start --by is not needed on a task without kind (control)", start_errors(_fixture(), "PR-E01.02", None), False)
            f = sub_fixture(); begun(f)
            expect("submit with evidence that names nothing checkable", cmd_submit(f, "PR-E01.04", "looked fine"), True,
                   "names nothing checkable")
            f = sub_fixture(); errs = handed(f)
            t = index(f)[1]["PR-E01.04"]
            ok = not errs and t["status"] == "in-progress" and t.get("submittedAt") and not validate(f)
            expect("submit hands it in and leaves it in progress (control)", errs or ([] if ok else [f"task={t}"]), False)
            expect("confirm a subtask that was not handed in", (lambda f: begun(f) or cmd_confirm(
                f, "PR-E01.04", HUB, "re-read Source/A.cpp:1"))(sub_fixture()), True, "has not been handed in")
            f = sub_fixture(); handed(f)
            expect("confirm --by its own holder", cmd_confirm(f, "PR-E01.04", AG, "re-read Source/A.cpp:1"), True,
                   "its own holder")
            f = sub_fixture(); handed(f)
            index(f)[1]["PR-E01.04"]["findings"] = [{"n": 1, "severity": "blocker", "text": "x", "evidence": "Source/A.cpp:3",
                                                     "by": AG, "at": stamp_now(), "routed": None}]
            expect("confirm with a blocker finding not routed", cmd_confirm(f, "PR-E01.04", HUB, "re-read Source/A.cpp:3"),
                   True, "not routed yet")
            f = sub_fixture(); handed(f)
            errs = cmd_confirm(f, "PR-E01.04", HUB, "re-read Source/A.cpp:1, clean")
            t = index(f)[1]["PR-E01.04"]
            ok = not errs and t["status"] == "done" and t["confirmed"]["by"] == HUB and not validate(f)
            expect("confirm marks it done (control)", errs or ([] if ok else [f"task={t} {validate(f)}"]), False)
            f = sub_fixture(); handed(f)
            ok = [e for e in set_status(f, "PR-E01.04", "done") if "confirmed by the hub" in e]
            expect("a subtask set done without the hub's confirm", ok, True, "confirmed by the hub")
            f = sub_fixture(); index(f)[1]["PR-E01.03"].update(dependsOn=[], status="blocked", blockedOn="the review",
                                                                waitsOn=["PR-E01.04"])
            handed(f)
            not_yet = release_blocked(f) == []
            cmd_confirm(f, "PR-E01.04", HUB, "re-read Source/A.cpp:1")
            ok = not_yet and release_blocked(f) == ["PR-E01.03"]
            expect("a handed-in subtask releases a block --until on it only once confirmed (control)",
                   [] if ok else ["released too early, or not at all"], False)
            f = sub_fixture(parent="PR-E01")
            for t in f["tasks"][1:3]:
                t.update(status="done", evidence="ran-it", landed="dv.commit.12", dependsOn=[])
            errs = begun(f)
            covers = index(f)[1]["PR-E01.04"]["covers"]
            f["tasks"].append({"id": "PR-E01.05", "epic": "PR-E01", "title": "routed blocker", "owner": "Engineering",
                               "size": "S", "wave": 1, "files": ["Source/E.cpp"],
                               "acceptance": {"check": "c", "expect": "e"}, "status": "planned"})
            ok = not errs and covers == ["PR-E01.01", "PR-E01.02", "PR-E01.03"] and not validate(f)
            expect("a task added to the parent epic after dispatch leaves the review valid (control)",
                   errs or ([] if ok else [f"covers={covers} {validate(f)}"]), False)
            f = _fixture()
            for t in f["tasks"]:
                t.update(status="done", evidence="ran-it", landed="dv.commit.11")
            f["tasks"][0]["owner"] = "Security Department"
            f["decisions"][0].update(status="decided", ruling="x", ruledOn="2026-10-12")
            sync_epic_status(f)
            ok = f["epics"][0]["status"] == "done" and not validate(f)
            expect("a finished epic holding a finished department task with no kind stays finished (control)",
                   [] if ok else [f"{f['epics'][0]['status']} {validate(f)}"], False)
            f = sub_fixture(); handed(f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_next(f)
            g = sub_fixture(); begun(g)
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                cmd_next(g)
            ok = "TO CONFIRM   PR-E01.04" in buf.getvalue() and "RUNNING      PR-E01.04" in buf2.getvalue()
            expect("next lists a handed-in subtask under TO CONFIRM and a running one under RUNNING (control)",
                   [] if ok else [buf.getvalue()[-400:]], False)

            print("department subtasks: findings and routing:")
            rec = lambda f, sev, ev, text="a problem": cmd_finding(f, "PR-E01.04", sev, text, ev)[0]
            f = sub_fixture(); sent(f)
            expect("a finding on a subtask that is not in progress", rec(f, "blocker", "Source/A.cpp:3"), True,
                   "not in progress")
            f = sub_fixture(); begun(f)
            expect("a finding whose evidence names nothing checkable", rec(f, "hardening", "it looked wrong"), True,
                   "names nothing checkable")
            expect("a finding that points at a bare file name with no folder", rec(f, "hardening", "see ledger.py"),
                   True, "names nothing checkable")
            for ev in ("Source/MyGame/Private/X.cpp:120", "/Game/MyGame/Blueprints/BP_Bank, ServerTake, node "
                       "K2Node_CallFunction_12", "Saved/Logs/MyGame.log", "dv.commit.1999",
                       r"D:\MyGame\Saved\Logs\run.log"):
                f = sub_fixture(); begun(f)
                expect(f"a finding pointing at {ev.split(',')[0][:40]} (control)", rec(f, "hardening", ev), False)
            f = sub_fixture(); begun(f)
            errs = rec(f, "blocker", "Source/A.cpp:3") or rec(f, "hardening", "Source/A.cpp:9")
            fs = index(f)[1]["PR-E01.04"]["findings"]
            ok = not errs and [x["n"] for x in fs] == [1, 2] and fs[0]["by"] == AG and fs[0]["routed"] is None and not validate(f)
            expect("findings are numbered and carry their holder (control)", errs or ([] if ok else [f"findings={fs}"]), False)
            index(f)[1]["PR-E01.04"]["findings"][0]["severity"] = "urgent"
            expect("a finding whose severity is not on the list", validate(f), True, "severity")
            f = sub_fixture(); begun(f); rec(f, "blocker", "Source/A.cpp:3"); rec(f, "hardening", "Source/A.cpp:9")
            expect("route --to a task that does not exist", cmd_route(f, "PR-E01.04#1", "PR-E01.99", None, HUB), True,
                   "no other task")
            expect("route --reject with too short a reason", cmd_route(f, "PR-E01.04#2", None, "wrong", HUB), True,
                   "reason of at least")
            expect("route a finding that does not exist", cmd_route(f, "PR-E01.04#7", "PR-E01.02", None, HUB), True,
                   "no such finding")
            errs = cmd_route(f, "PR-E01.04#2", None, "the call is guarded two lines up", HUB)
            expect("route --reject a hardening finding at any time (control)", errs, False)
            errs = cmd_route(f, "PR-E01.04#1", "PR-E01.02", None, HUB)
            ok = not errs and index(f)[1]["PR-E01.04"]["findings"][0]["routed"]["task"] == "PR-E01.02" and not validate(f)
            expect("route --to a blocker finding (control)", errs or ([] if ok else ["not routed"]), False)
            expect("route the same finding twice", cmd_route(f, "PR-E01.04#1", "PR-E01.03", None, HUB), True,
                   "already routed")
            cmd_submit(f, "PR-E01.04", "Source/A.cpp: finding 1")
            expect("confirm once every finding is routed (control)",
                   cmd_confirm(f, "PR-E01.04", HUB, "re-read Source/A.cpp:3"), False)
            # Hardening findings route before confirm too, now there is no hardening wave.
            f = sub_fixture(); begun(f); rec(f, "hardening", "Source/A.cpp:9", "late cleanup")
            cmd_submit(f, "PR-E01.04", "Source/A.cpp: finding 1")
            expect("confirm with a hardening finding not routed",
                   cmd_confirm(f, "PR-E01.04", HUB, "re-read Source/A.cpp:9"), True, "not routed yet")
            index(f)[1]["PR-E01.04"]["status"] = "done"   # as if confirmed under the old rule, when only blockers had to route
            for t in f["tasks"]:
                if t["status"] != "done":
                    t.update(status="withdrawn")
            f["decisions"][0].update(status="decided", ruling="x", ruledOn="2026-10-12")
            sync_epic_status(f)
            rows = all_findings(f, unrouted=True)
            ok = f["epics"][0]["status"] == "done" and [(t["id"], x["n"]) for t, x in rows] == [("PR-E01.04", 1)]
            expect("findings lists an unrouted finding in a finished epic (control)", [] if ok else [f"rows={rows}"], False)
            f = sub_fixture(); begun(f); rec(f, "hardening", "Source/A.cpp:9")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_next(f)
            ok = "TO ROUTE" not in buf.getvalue()
            expect("next leaves a running subtask's findings to its confirm (control)", [] if ok else [buf.getvalue()[-300:]],
                   False)
            ok = "#1 hardening: a problem (Source/A.cpp:9" in render(f)
            expect("LEDGER.md shows a subtask's findings (control)", [] if ok else ["not rendered"], False)
            index(f)[1]["PR-E01.04"]["status"] = "done"   # as if confirmed under the old rule
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_next(f)
            ok = "TO ROUTE" in buf.getvalue() and "PR-E01.04#1  [hardening]" in buf.getvalue()
            expect("next lists a finding left unrouted on a finished subtask (control)", [] if ok else [buf.getvalue()[-300:]],
                   False)

            print("department subtasks: who may do what:")
            f = sub_fixture(); handed(f)
            expect("confirm by a hub other than the one that dispatched it",
                   cmd_confirm(f, "PR-E01.04", "Some Other Session", "re-read Source/A.cpp:1"), True, "--new-hub")
            f = sub_fixture(); handed(f)
            errs = cmd_confirm(f, "PR-E01.04", "Fresh Hub", "re-read Source/A.cpp:1", "Ledger Work was archived by Sam")
            ok = not errs and index(f)[1]["PR-E01.04"]["confirmed"].get("newHub") == "Ledger Work was archived by Sam"
            expect("a new hub confirms with --new-hub, and the reason is recorded (control)", errs or ([] if ok else ["not recorded"]), False)
            f = sub_fixture(); begun(f); rec(f, "blocker", "Source/A.cpp:3")
            expect("route by a hub other than the one that dispatched it",
                   cmd_route(f, "PR-E01.04#1", "PR-E01.02", None, "Some Other Session"), True, "--new-hub")
            f = sub_fixture(); begun(f); rec(f, "hardening", "Source/A.cpp:9")
            errs = cmd_route(f, "PR-E01.04#1", "PR-E01.02", None, HUB)
            r = index(f)[1]["PR-E01.04"]["findings"][0]["routed"]
            ok = not errs and r.get("task") == "PR-E01.02" and "early" not in r and not validate(f)
            expect("route --to a hardening finding in any wave, no early word needed (control)",
                   errs or ([] if ok else [f"routed={r}"]), False)
            index(f)[1]["PR-E01.04"]["findings"][0]["routed"]["early"] = "Sam, in chat: route them now"
            expect("a finding routed early under the old rule still validates (control)", validate(f), False)
            expect("decide with no --by", cmd_decide(_fixture(), "PR-D01", "yes", "", "2026-10-18"), True, "names who ruled")
            f = sub_fixture(); sent(f)
            expect("start a subtask under a name other than '<owner> agent'", start_errors(f, "PR-E01.04", "Ledger Work"),
                   True, "is started by")
            f = sub_fixture(); handed(f)
            expect("start again a subtask already handed in", start_errors(f, "PR-E01.04", AG), True, "handed in")
            f = sub_fixture(); begun(f); index(f)[1]["PR-E01.04"]["heldBy"] = "an older holder"
            expect("start a subtask in progress under another holder", start_errors(f, "PR-E01.04", AG), True, "--restart")
            f = sub_fixture(); handed(f)
            expect("dispatch --restart of a subtask already handed in", cmd_dispatch(f, "PR-E01.04", HUB, None, True)[0],
                   True, "handed in")
            f = sub_fixture(); f["tasks"][0]["files"] = ["Source/A.cpp", "Source/A.h"]; begun(f)
            expect("submit a review that does not name every covered file",
                   cmd_submit(f, "PR-E01.04", "Source/A.cpp: clean"), True, "does not name Source/A.h")
            expect("the same naming both (control)", cmd_submit(f, "PR-E01.04", "Source/A.cpp: clean; Source/A.h: clean"), False)
            f = sub_fixture(parent="PR-E02")
            f["epics"].append({"id": "PR-E02", "title": "empty", "status": "planned", "plan": "p.md"})
            f["tasks"].append({"id": "PR-E02.01", "epic": "PR-E02", "title": "only a subtask", "owner": "Security Department",
                               "kind": "measure", "size": "S", "wave": 1, "files": [], "status": "planned",
                               "acceptance": {"check": "c", "expect": "e"}})
            expect("dispatch a review that covers no task", sent(f), True, "covers no task")
            f = _fixture(); del f["departments"]
            expect("a ledger with no departments list", validate(f), True, "departments")
        finally:
            AGENTS_DIR = saved_agents

    print("the lock and the answers inbox:")
    global LOCK, ANSWERS_JSON
    saved_lock, saved_answers = LOCK, ANSWERS_JSON
    with tempfile.TemporaryDirectory() as tmp:
        LOCK, ANSWERS_JSON = Path(tmp) / "ledger.lock", Path(tmp) / "answers.json"
        try:
            def try_lock(wait: float = 0.3) -> list[str]:
                try:
                    with ledger_lock(wait=wait):
                        return []
                except LedgerError as e:
                    return [str(e)]

            LOCK.write_text(f"{os.getpid()} someone-else 2000-01-01T00:00:00\n", encoding="utf-8")
            expect("a lock whose holder still runs is not taken over, however old", try_lock(), True, "still running")
            gone = subprocess.Popen([sys.executable, "-c", "pass"])
            gone.wait()
            LOCK.write_text(f"{gone.pid} crashed-run 2026-10-18T00:00:00\n", encoding="utf-8")
            expect("a lock whose holder has exited is taken over (control)",
                   try_lock() or ([] if not LOCK.exists() else ["lock left behind"]), False)
            with ledger_lock(wait=0.3):
                LOCK.write_text(f"{os.getpid()} another-runs-token x\n", encoding="utf-8")
            ok = LOCK.exists() and "another-runs-token" in LOCK.read_text(encoding="utf-8")
            expect("a run leaves alone a lock that no longer holds its own token (control)", [] if ok else ["removed it"], False)
            LOCK.unlink()
            with ledger_lock(wait=0.3):
                inner = try_lock(0.3)
            expect("the lock is re-entrant within one run (control)", inner or ([] if not LOCK.exists() else ["left"]), False)
            root = Path(tmp) / "root"
            f = _fixture()
            save(f, root)
            (root / ".ledger.lock").write_text(f"{os.getpid()} a-live-other-run x\n", encoding="utf-8")
            saved_wait = LOCK_WAIT_S
            globals()["LOCK_WAIT_S"] = 0.3
            try:
                index(f)[1]["PR-E01.01"]["notes"] = "changed"
                try:
                    save(f, root)
                    errs = []
                except LedgerError as e:
                    errs = [str(e)]
            finally:
                globals()["LOCK_WAIT_S"] = saved_wait
            expect("save() waits for the lock another run holds, and refuses rather than write around it", errs, True,
                   "still running")
            save_answers({"PR-D01": {"choice": "Yes"}, "PR-D02": {"choice": "No"}})
            stale = load_answers()  # what apply read before it saved the ledger
            save_answers({**stale, "PR-D03": {"choice": "Later"}})  # the website files another answer meanwhile
            take_answer("PR-D01")
            ok = sorted(load_answers()) == ["PR-D02", "PR-D03"]
            expect("apply's removal re-reads the inbox, so an answer filed meanwhile stays (control)",
                   [] if ok else [f"inbox={load_answers()}"], False)
            ANSWERS_JSON.write_text("{ not json", encoding="utf-8")
            try:
                load_answers()
                errs = []
            except LedgerError as e:
                errs = [str(e)]
            expect("an unreadable inbox is refused, not read as empty", errs, True, "cannot be read")
        finally:
            LOCK, ANSWERS_JSON = saved_lock, saved_answers

    print("project config: the settings that tie the tool to one project:")
    good = dict(SELFTEST_PROFILE)
    expect("the fixtures' settings (control)", config_errors(good), False)
    expect("a config missing a key", config_errors({k: v for k, v in good.items() if k != "vcs"}), True,
           "missing key 'vcs'")
    expect("a config with a key the tool does not know", config_errors({**good, "prefix": "PR"}), True, "unknown key")
    expect("a note key starting with _ (control)", config_errors({**good, "_about": "see the README"}), False)
    expect("an id prefix in lower case", config_errors({**good, "idPrefix": "bi"}), True, "idPrefix")
    expect("an id prefix with a hyphen in it", config_errors({**good, "idPrefix": "B-I"}), True, "idPrefix")
    expect("a version control with no backend", config_errors({**good, "vcs": "perforce"}), True, "no backend")
    expect("unreal written as a word", config_errors({**good, "unreal": "yes"}), True, "true or false")
    expect("an owner with no name", config_errors({**good, "owner": " "}), True, "owner")
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "ledger_config.json"

        def try_load() -> list[str]:
            try:
                load_config(cfg_path)
                return []
            except LedgerError as e:
                return [str(e)]

        expect("no config file", try_load(), True, "is missing")
        cfg_path.write_text('{"idPrefix": "PR",', encoding="utf-8")
        expect("a config file that is not JSON", try_load(), True, "cannot be read")
        cfg_path.write_text(json.dumps({**good, "unreal": 1}), encoding="utf-8")
        expect("a config file with a bad value", try_load(), True, "true or false")

    def retag(obj):
        """The fixture as a made-up project's: ids XY-..., owner Ana."""
        if isinstance(obj, dict):
            return {retag(k): retag(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [retag(v) for v in obj]
        return obj.replace("PR-", "XY-").replace("Sam", "Ana") if isinstance(obj, str) else obj

    apply_config({"idPrefix": "XY", "owner": "Ana", "vcs": "diversion", "unreal": False})
    try:
        expect("a made-up project's ledger under its own prefix (control)", validate(retag(_fixture())), False)
        expect("ids with another project's prefix", validate(_fixture()), True, "must look like XY-")
        xy = retag(_fixture())
        ok = (subject_id("XY-E01.01 work") == "XY-E01.01" and subject_id("PR-E01.01 work") is None
              and RE_ANY_ID.findall("XY-D01 PR-D01 XY-E01.02") == ["XY-D01", "XY-E01.02"]
              and next_decision_id(xy) == "XY-D02" and bool(RE_EPIC_FILE.match("Docs/Ledger/epics/XY-E01.json"))
              and not RE_EPIC_FILE.match("Docs/Ledger/epics/PR-E01.json")
              and RE_FINDING_REF.fullmatch("XY-E01.04#2") is not None)
        expect("ids, commit subjects, findings and epic files follow the prefix (control)",
               [] if ok else ["the prefix is not followed"], False)
        f = retag(_fixture()); f["tasks"][1]["owner"] = "Ana"
        expect("an open task owned by the configured owner (control)", validate(f), False)
        f = retag(_fixture()); f["tasks"][1]["owner"] = "Sam"
        expect("an open task owned by another project's owner", validate(f), True, "(or Ana)")
        f = retag(_fixture()); f["tasks"][1]["needsAna"] = " "
        expect("the owner's needs field with no words in it", validate(f), True, "needsAna says")
        f = retag(_fixture())
        errs = cmd_apply(f, "XY-D01", {"XY-D01": {"choice": "Yes", "note": "go", "answeredAt": "2026-10-22 09:33"}})
        d0 = f["decisions"][0]
        ok = not errs and d0["ruledBy"] == "Ana" and d0["ruling"] == "Yes. do it Ana's note: go"
        expect("a website answer is ruled in the owner's name (control)", errs or ([] if ok else [f"{d0}"]), False)
        ok = (not needs_dump(["Content/Dir/X.uasset", "Content/Maps/M.umap", "Content/Dir/"])
              and not has_pointer("/Game/MyGame/BP_Bank") and has_pointer("Source/A.cpp:3")
              and has_pointer("dv.commit.7") and "/Game/" not in pointer_kinds())
        expect("not Unreal: no graph dump, and a /Game/ path is no pointer (control)", [] if ok else ["Unreal rules"],
               False)
        f = retag(_fixture())
        prompt = dispatch_prompt(f, dict(f["tasks"][1], kind="measure", owner="Security Department"),
                                 {"editor": "foreground"})
        ok = "Unreal" not in prompt and "the hub waits" in prompt and "ledger.py show XY-E01.02" in prompt
        expect("not Unreal: the dispatch prompt names no editor (control)", [] if ok else [prompt[-300:]], False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrote = save(retag(_fixture()), root)
            ok = (sorted(wrote) == ["Docs/Ledger/epics/XY-E01.json", "Docs/Ledger/meta.json"]
                  and [e["id"] for e in load(root, track=False)["epics"]] == ["XY-E01"])
            expect("epic files are named and found by the prefix (control)", [] if ok else [f"wrote={wrote}"], False)
    finally:
        apply_config(SELFTEST_PROFILE)
    ok = needs_dump(["Content/Maps/M.umap"]) and has_pointer("/Game/MyGame/BP_Bank") and OWNER == "Sam"
    expect("back on the fixtures' settings, the Unreal rules apply again (control)", [] if ok else ["not restored"], False)

    print(f"selftest: {ran} checks, {fails} failed")
    if ran == 0:
        print("selftest ran nothing; a check that scans nothing fails")
        return 1
    return 1 if fails else 0


# --------------------------------------------------------------------------- adding to the ledger

def stamp_now() -> str:
    """A note's stamp, from this machine's local clock."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def cmd_note(ledger: dict, ident: str, text: str) -> list[str]:
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if not text.strip():
        return ["a note needs text"]
    t["notes"] = (t.get("notes", "").rstrip() + " " if t.get("notes") else "") + f"{stamp_now()}. {text.strip()}"
    return []


# Withdraw: a ruling that drops a feature leaves unfinished tasks no other command can take out of the
# plan ("withdrawn" was a status with no command).
def cmd_withdraw(ledger: dict, ident: str, reason: str) -> list[str]:
    """Takes an unfinished task out of the plan: its status becomes withdrawn (finished, so what depends on it may go
    on) and the reason is stamped into its notes. Never a done task, and never a department subtask in progress."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if t.get("status") in FINISHED:
        return [f"{ident} is {t['status']}: only an unfinished task is withdrawn"]
    if is_subtask(t) and t.get("status") == "in-progress":
        return [f"{ident} is a department subtask in progress: its agent hands it in first"]
    if len((reason or "").strip()) < REASON_MIN:
        return [f"withdraw needs --reason of at least {REASON_MIN} characters: what took it out of the plan"]
    trial = copy.deepcopy(ledger)
    errs = cmd_note(trial, ident, f"Withdrawn: {reason.strip()}") or set_status(trial, ident, "withdrawn")
    if errs:
        return errs
    ledger.clear()
    ledger.update(trial)
    return []


def next_task_id(ledger: dict, epic: str) -> str:
    nums = [int(t["id"].split(".")[1]) for t in ledger["tasks"] if t["id"].startswith(epic + ".")]
    return f"{epic}.{(max(nums) + 1 if nums else 1):02d}"


def next_decision_id(ledger: dict) -> str:
    nums = [decision_num(d["id"]) for d in ledger["decisions"]]
    return f"{PREFIX}-D{(max(nums) + 1 if nums else 1):02d}"


def cmd_add_task(ledger: dict, epic: str, spec: dict) -> tuple[list[str], str | None]:
    """Add a task to an epic under the next free id. spec holds the task's fields; its id and epic are ignored."""
    if epic not in index(ledger)[0]:
        return [f"unknown epic {epic}"], None
    ident = next_task_id(ledger, epic)
    t = {"id": ident, "epic": epic, **{k: v for k, v in spec.items() if k not in ("id", "epic")}}
    t.setdefault("status", "planned")
    trial = copy.deepcopy(ledger)
    trial["tasks"].append(t)
    errs = [e for e in validate(trial) if e.startswith(ident + ":") or e.startswith(epic + ":")]
    if errs:
        return errs, None
    ledger["tasks"].append(t)
    return [], ident


def cmd_add_decision(ledger: dict, epic: str, spec: dict) -> tuple[list[str], str | None]:
    """Add an open decision, filed under an epic, with the next free PR-Dnn. spec needs question, recommendation
    and options; its id is ignored."""
    if epic not in index(ledger)[0]:
        return [f"unknown epic {epic}"], None
    ident = next_decision_id(ledger)
    d = {"id": ident, **{k: v for k, v in spec.items() if k not in ("id", "epic")}, "epic": epic}
    d.setdefault("status", "open")
    trial = copy.deepcopy(ledger)
    trial["decisions"].append(d)
    errs = [e for e in validate(trial) if e.startswith(ident + ":")]
    if errs:
        return errs, None
    ledger["decisions"].append(d)
    return [], ident


# --------------------------------------------------------------------------- main

MUTATING = {"start", "done", "block", "withdraw", "note", "add-task", "add-decision", "decide", "apply", "render", "commit",
            "dispatch", "submit", "confirm", "finding", "route"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Task ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next"); sub.add_parser("status"); sub.add_parser("validate")
    sub.add_parser("audit"); sub.add_parser("check"); sub.add_parser("selftest")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("start"); p.add_argument("task")
    p.add_argument("--by", help="who holds it; required on a department subtask (\"<department> agent\")")
    p = sub.add_parser("done"); p.add_argument("task"); p.add_argument("--evidence", required=True)
    p = sub.add_parser("dispatch", help="hub: send a department subtask out; prints the agent and its prompt")
    p.add_argument("task"); p.add_argument("--by", help="the hub session's title")
    p.add_argument("--dump", help="the graph dump folder, for a review that covers Blueprints")
    p.add_argument("--restart", action="store_true", help="the subtask's agent is gone: start a new one on it")
    p = sub.add_parser("submit", help="department agent: hand the work in (the task stays in progress)")
    p.add_argument("task"); p.add_argument("--evidence", required=True)
    p = sub.add_parser("confirm", help="hub: one pointer re-checked; marks the subtask done")
    p.add_argument("task"); p.add_argument("--by", help="the hub session's title")
    p.add_argument("--evidence", required=True, help="which pointer was re-opened and what it showed")
    p.add_argument("--new-hub", help="a hub other than the one that dispatched it takes over: why")
    p = sub.add_parser("finding", help="department agent: record one finding on its subtask, as soon as it is found")
    p.add_argument("task"); p.add_argument("--severity", required=True, choices=SEVERITIES)
    p.add_argument("--text", required=True); p.add_argument("--evidence", required=True)
    p = sub.add_parser("findings", help="list findings across every epic, finished ones included")
    p.add_argument("--unrouted", action="store_true"); p.add_argument("--severity", choices=SEVERITIES)
    p = sub.add_parser("route", help=f"hub: route one finding ({PREFIX}-Enn.mm#n) to a task, or reject it")
    p.add_argument("finding"); p.add_argument("--to"); p.add_argument("--reject")
    p.add_argument("--by", help="the hub session's title")
    p.add_argument("--new-hub", help="a hub other than the one that dispatched it takes over: why")
    p = sub.add_parser("block"); p.add_argument("task")
    p.add_argument("--on", help="what it waits on, in words; leave out to re-check a blocked task (keeps the text, "
                                "re-dates it)")
    p.add_argument("--until", nargs="+", help="the decisions or tasks whose ruling or finish releases it "
                                               "(save() then releases it by itself), or 'none' when it waits on a "
                                               "person or an event")
    p = sub.add_parser("withdraw", help="take an unfinished task out of the plan, with the reason")
    p.add_argument("task"); p.add_argument("--reason", required=True, help="what took it out (a ruling, a change)")
    p = sub.add_parser("note"); p.add_argument("task"); p.add_argument("--text", required=True)
    p = sub.add_parser("add-task"); p.add_argument("--epic", required=True)
    p.add_argument("--from", dest="spec", required=True, help="a JSON file holding the task's fields")
    p = sub.add_parser("add-decision"); p.add_argument("--epic", required=True)
    p.add_argument("--from", dest="spec", required=True, help="a JSON file: question, recommendation, options, source")
    p = sub.add_parser("decide"); p.add_argument("decision"); p.add_argument("--ruling", required=True)
    p.add_argument("--by", required=True, help=f"who ruled - {OWNER}, for a ruling given in chat")
    p.add_argument("--date", required=True)
    p = sub.add_parser("apply"); p.add_argument("decision")
    p = sub.add_parser("render"); p.add_argument("--check", action="store_true")
    p = sub.add_parser("commit")
    p.add_argument("--message", required=True, help="file whose first line is the subject")
    p.add_argument("--role"); p.add_argument("--agent")
    p.add_argument("--reason", help="with --role: why the role commit; on a task commit: why files outside the "
                                    "task's list belong to it (they are then added to the list)")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return selftest()
    writes = a.cmd in MUTATING and not (a.cmd == "render" and a.check)
    try:
        with (ledger_lock() if writes else contextlib.nullcontext()):
            return _run(a)
    except LedgerError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1


def _run(a) -> int:
    ledger = load()
    if a.cmd == "next":
        return cmd_next(ledger)
    if a.cmd == "status":
        return cmd_status(ledger)
    if a.cmd == "show":
        return cmd_show(ledger, a.id)
    if a.cmd == "validate":
        errs = validate(ledger)
    elif a.cmd == "render":
        if a.check:
            errs = validate(ledger) + render_check(ledger)
        else:
            errs = validate(ledger)
            if not errs:
                write_render(ledger)
                print(f"wrote {LEDGER_MD_REL}")
    elif a.cmd == "audit":
        errs = cmd_audit(ledger)
    elif a.cmd == "check":
        errs = validate(ledger) + render_check(ledger)
        if not errs:
            errs = cmd_audit(ledger)
    elif a.cmd in ("start", "done", "block"):
        status = {"start": "in-progress", "done": "done", "block": "blocked"}[a.cmd]
        extra = {"evidence": getattr(a, "evidence", None), "blockedOn": getattr(a, "on", None),
                 "heldBy": (getattr(a, "by", None) or "").strip() or None}
        errs = block_errors(ledger, a.task, a.on, a.until, extra) if a.cmd == "block" else []
        if a.cmd == "start":
            errs = errs or start_errors(ledger, a.task, a.by)
        t = index(ledger)[1].get(a.task)
        if a.cmd == "done" and t and is_subtask(t):
            errs = [f"{a.task} is a department subtask: its agent hands it in with `submit`, and the hub's `confirm` "
                    f"marks it done"]
        errs = errs or set_status(ledger, a.task, status, **extra)
        if not errs:
            wrote = save(ledger)
            write_render(ledger)
            print(f"{a.task} -> {status}; saved {', '.join(wrote) or 'nothing new'} and re-rendered "
                  f"(commit it with the work)")
    elif a.cmd == "withdraw":
        errs = cmd_withdraw(ledger, a.task, a.reason)
        if not errs:
            wrote = save(ledger)
            write_render(ledger)
            print(f"{a.task} -> withdrawn; saved {', '.join(wrote) or 'nothing new'} and re-rendered (commit it --role Ledger)")
    elif a.cmd == "note":
        errs = cmd_note(ledger, a.task, a.text)
        if not errs:
            wrote = save(ledger)
            write_render(ledger)
            print(f"{a.task}: note added; saved {', '.join(wrote)}")
    elif a.cmd in ("add-task", "add-decision"):
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        fn = cmd_add_task if a.cmd == "add-task" else cmd_add_decision
        errs, ident = fn(ledger, a.epic, spec)
        if not errs:
            wrote = save(ledger)
            write_render(ledger)
            print(f"added {ident}; saved {', '.join(wrote)}. Commit it now: ledger.py commit --role Ledger "
                  f"--reason \"<why>\" --message <file> {epic_rel(a.epic)} {LEDGER_MD_REL} - with a subject like "
                  f"'Ledger: file {ident} ...', not one starting with the id (that names a task not yet started)")
    elif a.cmd == "decide":
        errs = cmd_decide(ledger, a.decision, a.ruling, a.by, a.date)
        if not errs:
            save(ledger)
            write_render(ledger)
            print(f"{a.decision} decided; ledger saved and re-rendered")
    elif a.cmd == "apply":
        answers = load_answers()
        errs = cmd_apply(ledger, a.decision, answers)
        if not errs:
            save(ledger)
            write_render(ledger)
            take_answer(a.decision)
            d = index(ledger)[2][a.decision]
            gated = [t["id"] for t in ledger["tasks"] if a.decision in (t.get("dependsOn") or [])]
            print(f"{a.decision} decided from {OWNER}'s website answer: {d['ruling']}")
            print(f"  ledger saved and re-rendered; commit {epic_rel(decision_epic(ledger, d))} (--role Ledger). "
                  f"It gates: {', '.join(gated) or 'no task'}")
    elif a.cmd == "dispatch":
        errs, out = cmd_dispatch(ledger, a.task, a.by, a.dump, a.restart)
        if not errs:
            wrote = save(ledger)
            write_render(ledger)
            t = index(ledger)[1][a.task]
            print(f"{a.task} dispatched by {t['dispatchedBy']}; covers {', '.join(t['covers']) or 'no other task'}; "
                  f"saved {', '.join(wrote)}")
            print(f"Start it now with the Agent tool: subagent_type \"{out['agent']}\", run_in_background "
                  f"{'true' if out['background'] else 'false (the hub waits: the agent may own the editor)'}, "
                  f"and this prompt, word for word:")
            print("----- prompt -----")
            print(out["prompt"])
            print("----- end of prompt -----")
    elif a.cmd == "submit":
        errs = cmd_submit(ledger, a.task, a.evidence)
        if not errs:
            save(ledger)
            write_render(ledger)
            print(f"{a.task} handed in; it stays in progress until the hub confirms it. Reply to the hub in one line.")
    elif a.cmd == "confirm":
        errs = cmd_confirm(ledger, a.task, a.by, a.evidence, a.new_hub)
        if not errs:
            save(ledger)
            write_render(ledger)
            t = index(ledger)[1][a.task]
            print(f"{a.task} confirmed and done. Commit it now, with a subject starting '{a.task}': ledger.py commit "
                  f"--message <file> {epic_rel(t['epic'])} (the tool stamps landed)")
    elif a.cmd == "finding":
        errs, n = cmd_finding(ledger, a.task, a.severity, a.text, a.evidence)
        if not errs:
            save(ledger)
            write_render(ledger)
            print(f"recorded {a.task}#{n} ({a.severity}); saved")
    elif a.cmd == "findings":
        rows = all_findings(ledger, a.unrouted, a.severity)
        for t, f in rows:
            print(f"{t['id']}#{f['n']}  {f['severity']:<9}  {routed_text(f)}  [{t['status']}]  {f['text']}  -- {f['evidence']}")
        print(f"{len(rows)} finding(s)")
        return 0
    elif a.cmd == "route":
        errs = cmd_route(ledger, a.finding, a.to, a.reject, a.by, a.new_hub)
        if not errs:
            save(ledger)
            write_render(ledger)
            print(f"{a.finding} routed; saved. Commit the epic file (--role Ledger).")
    elif a.cmd == "commit":
        msg = Path(a.message).read_text(encoding="utf-8")
        return do_commit(ledger, msg, a.role, a.reason, a.agent, a.paths, a.dry_run)
    else:
        errs = [f"unknown command {a.cmd}"]
    if errs:
        print("REFUSED:" if a.cmd not in ("validate", "check", "audit") else "FAILED:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if a.cmd in ("validate", "check", "audit") or (a.cmd == "render" and a.check):
        print(f"{a.cmd}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
