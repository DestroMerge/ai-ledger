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
    python Docs/Ledger/ledger.py version               which copy of the tool this is (`check` prints it too)
    python Docs/Ledger/ledger.py start <task> [--by NAME] [--files P...] [--takeover "<why>"] [--reopen "<why>"]
                                                       planned -> in-progress (refuses unmet dependencies). Records
                                                       who holds it (--by, else this Claude Code session); refuses
                                                       a task another holder has unless --takeover; a done task
                                                       comes back only with --reopen. --files narrows its list.
                                                       --by is required on a department subtask
    python Docs/Ledger/ledger.py done <task> --evidence "<ran-it|read-in-the-code|read-in-a-document: what showed>"
    python Docs/Ledger/ledger.py edit-task <task> --reason "<why>" [--title T] [--wave N] [--depends-on ID...|none]
                                                       [--files P...] [--check C] [--expect E]
  Department subtasks (a task with 'kind'; README "Department subtasks"):
    python Docs/Ledger/ledger.py dispatch <task> --by "<hub title>" [--dump <folder>] [--restart] [--dirty-ok "<why>"]
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
    python Docs/Ledger/ledger.py add-plan --from <plan.json> [--dry-run]         epics, tasks, decisions and wave
                                                       names in one go, all or nothing (README "Filing a plan")
    python Docs/Ledger/ledger.py decide <decision> --ruling "<text>" --by <who ruled> --date YYYY-MM-DD
                                                       [--supersede "<why>"] (a ruled decision changes only so)
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
  7. Paths are explicit. There is no -a, and no shell sits between this tool and the version control.
  8. A role is named with --role only. A `Role:` line typed into the message file is refused: the tool writes the
     Role and Reason lines itself, so the audit can trust them.
  The old name Docs/Ledger/ledger.json among the paths stands for the named task's epic file (or, in a role
  commit, every ledger file with changes), so callers written before the split still commit the right file.
  After the commit, a task that is done and unlanded gets landed stamped with the new commit id, and its
  epic's ledger file is committed alone as a second commit naming the same task.

Audit: walks the version control's log (`git log`, or `dv log`) from the ledger's auditFrom (exclusive) to head,
and refuses when:
  - a commit names an unknown id, or a task still planned;
  - a commit names a task (or epic) and carries no role, and a path it changed lies outside that task's files list
    (every list the task has held counts, so a list narrowed later does not fail old commits) - a commit made with
    the version control directly cannot slip work past the file rule;
  - a commit carries a Role line with no Reason line of 15+ characters (the tool writes both);
  - a commit names nothing, carries no valid role, AND touched a file of a task that is in progress now,
    unless the commit is recorded in outsideCommits with a reason;
  - a done task has no landed, or no evidence;
  - the log is empty or does not reach auditFrom.
  Diversion's dv log answers at most 1000 commits a call, so the walk pages back with --until until it reaches
  auditFrom; git's log comes whole. A commit's paths are cached in Saved/Ledger/commit_paths.json (a commit never
  changes).
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Which copy of the tool this is. Projects copy the tool from the ai-ledger repository (update_ledger.py), so a copy
# that has fallen behind shows here and in `check`. Raise it with every change to the tool files.
VERSION = "2026.11.11"

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
LOCK_WAIT_S = 30.0  # how long a run waits for the run holding the lock before it refuses
PATHS_CACHE = REPO / "Saved" / "Ledger" / "commit_paths.json"
# On Windows a file another program has open (the website re-reading LEDGER.md, a sync agent, an editor) cannot be
# replaced until it lets go, and os.replace fails with "Access is denied". A write retries this long first.
REPLACE_RETRY_S = 3.0
# LEDGER.md retries for less: it is held open far more often (the website re-reads it), a run holds the ledger lock
# while it waits, and a LEDGER.md left behind is caught up by the next render. Under a reader that held it open
# nearly all the time, 24 runs at once each waited the full 3 s inside the lock and most timed out queueing for it.
RENDER_RETRY_S = 1.0
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
# An epic or task number is two digits (01-99), then three (100-999): a busy project filled E99 and .99.
NUM = r"(?:\d{2}|[1-9]\d{2})"
ID_MAX = 999
RE_ROLE_LINE = re.compile(r"^\s*Role:\s*(\S+)\s*$", re.M)
RE_REASON_LINE = re.compile(r"^\s*Reason:\s*(.*\S)\s*$", re.M)
FINISHED = {"done", "withdrawn"}
# The moves a task's status may make through set_status. A done or withdrawn task comes back only through
# `start --reopen`; a task is started before it is done. (save() releases a blocked task to planned by itself.)
MOVES = {
    "planned": {"in-progress", "blocked", "withdrawn"},
    "in-progress": {"in-progress", "blocked", "done", "withdrawn"},
    "blocked": {"blocked", "in-progress", "planned", "withdrawn"},
    "done": set(),
    "withdrawn": set(),
}
# Evidence for `done` starts with one of these labels (meta.json conventions, "evidence").
EVIDENCE_LABELS = ("ran-it", "read-in-the-code", "read-in-a-document")
RE_EVIDENCE_LABEL = re.compile(r"^\s*(?:" + "|".join(map(re.escape, EVIDENCE_LABELS)) + r")\b")
# The fields each record may carry. validate() refuses any other, so a misspelled 'dependOn' cannot pass as no
# dependency at all. The owner's needs field (needsSam) is added from the config.
EPIC_FIELDS = {"id", "title", "status", "plan", "goal"}
TASK_FIELDS = {"id", "epic", "title", "owner", "size", "wave", "files", "acceptance", "status", "dependsOn", "notes",
               "evidence", "landed", "blockedOn", "waitsOn", "blockedAt", "startedAt", "heldBy", "session", "kind",
               "parent", "covers", "dispatchedAt", "dispatchedBy", "dispatchedSession", "dump", "submittedAt",
               "confirmed", "findings", "filesAdded", "reopened", "edits"}
DECISION_FIELDS = {"id", "epic", "question", "recommendation", "status", "options", "ruling", "ruledBy", "ruledOn",
                   "ruledVia", "chosen", "note", "source", "superseded"}
FINDING_FIELDS = {"n", "severity", "text", "evidence", "by", "at", "routed"}
# The task fields edit-task may change.
EDITABLE = ("title", "wave", "dependsOn", "files", "acceptance")
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
# with a reason - before it confirms. 'blocker': its fixing task goes on the critical path now; what counts as one is
# the department's to define (its agent file, and meta.json's conventions, "findings"). 'hardening' is everything
# else: its fixing task goes in the wave the work belongs to.
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
# Two more may be left out (OPTIONAL_CONFIG gives their defaults):
#   stampZone     a short time zone label written after every note stamp ("PT" makes "2026-10-16 19:45 PT"), for a
#                 team whose machines keep different clocks; empty by default
#   editorPython  (Unreal) the command that runs Python inside the open editor, such as a remote-execution script;
#                 when set, `dispatch` prints the whole graph-dump command line with it
# Paths stay fixed: the tool lives in Docs/Ledger/ at the repository root, and its working files in Saved/Ledger/.

CONFIG_JSON = HERE / "ledger_config.json"
CONFIG_KEYS = ("idPrefix", "owner", "vcs", "unreal")
OPTIONAL_CONFIG = {"stampZone": "", "editorPython": ""}
RE_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
RE_ZONE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+:\-]{0,9})?$")
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
    errs = [f"unknown key '{k}' (the keys are {', '.join(CONFIG_KEYS + tuple(OPTIONAL_CONFIG))}; a key starting with "
            f"'_' is a note)" for k in cfg if k not in CONFIG_KEYS and k not in OPTIONAL_CONFIG
            and not str(k).startswith("_")]
    errs += [f"missing key '{k}'" for k in CONFIG_KEYS if k not in cfg]
    if "stampZone" in cfg and not (isinstance(cfg["stampZone"], str) and RE_ZONE.match(cfg["stampZone"])):
        errs.append("stampZone is a short time zone label such as PT or UTC+1, or empty")
    if "editorPython" in cfg and not isinstance(cfg["editorPython"], str):
        errs.append("editorPython is the command that runs Python in the open editor, as text")
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
    return {**{k: cfg[k] for k in CONFIG_KEYS}, **{k: cfg.get(k, d) for k, d in OPTIONAL_CONFIG.items()}}


def apply_config(cfg: dict) -> None:
    """Set every project-specific name from a checked config. Run once at import; the selftest runs it to switch to its
    fixtures' settings and to a made-up project's, and back."""
    global CONFIG, PREFIX, OWNER, NEEDS_OWNER, UNREAL, VCS, LOG_LIMIT, EPIC_GLOB, RE_EPIC, RE_TASK
    global RE_DECISION, RE_SUBJECT_ID, RE_EPIC_FILE, RE_ANY_ID, RE_FINDING_REF, RE_COMMIT_ID, RE_POINTER
    global STAMP_ZONE, EDITOR_PYTHON
    CONFIG = dict(cfg)
    PREFIX, OWNER, UNREAL = cfg["idPrefix"], cfg["owner"], cfg["unreal"]
    STAMP_ZONE = cfg.get("stampZone", OPTIONAL_CONFIG["stampZone"])
    EDITOR_PYTHON = cfg.get("editorPython", OPTIONAL_CONFIG["editorPython"])
    NEEDS_OWNER = "needs" + re.sub(r"\W", "", OWNER)
    VCS = VCS_BACKENDS[cfg["vcs"]]()
    LOG_LIMIT = VCS.LOG_LIMIT
    p = re.escape(PREFIX)
    EPIC_GLOB = f"{PREFIX}-E*.json"
    RE_EPIC = re.compile(rf"^{p}-E{NUM}$")
    RE_TASK = re.compile(rf"^{p}-E{NUM}\.{NUM}$")
    RE_DECISION = re.compile(rf"^{p}-D\d{{2,3}}$")
    RE_SUBJECT_ID = re.compile(rf"^({p}-E{NUM}(?:\.{NUM})?)(?![\d.])")
    RE_EPIC_FILE = re.compile(rf"^Docs/Ledger/epics/({p}-E{NUM})\.json$")
    RE_ANY_ID = re.compile(rf"\b{p}-(?:D\d{{2,3}}|E{NUM}\.{NUM})\b")
    RE_FINDING_REF = re.compile(rf"({p}-E{NUM}\.{NUM})#(\d+)")
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


def epic_num(i: str) -> int:
    """The number of an epic id (PR-E07 -> 7); epic files sort by it, so E100 comes after E99, not after E10."""
    m = re.search(r"-E(\d+)$", i)
    return int(m.group(1)) if m else 0


def task_num(i: str) -> int:
    """The number of a task within its epic (PR-E07.12 -> 12)."""
    m = re.search(r"\.(\d+)$", i)
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
    for p in sorted((root / "epics").glob(EPIC_GLOB), key=lambda p: (epic_num(p.stem), p.stem)):
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


_os_replace = os.replace  # the selftest stands in a replace that fails, as a file held open makes it on Windows


def _replace(tmp: Path, p: Path, retry: float | None = None) -> None:
    """os.replace, retried for REPLACE_RETRY_S while the target is held open by another program (Windows refuses to
    replace an open file: the website re-reads LEDGER.md whenever the ledger changes, and a sync agent opens files)."""
    deadline = time.monotonic() + (REPLACE_RETRY_S if retry is None else retry)
    while True:
        try:
            _os_replace(tmp, p)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _stage(p: Path, text: str) -> Path:
    """Write text to a temporary file beside p, named for this run so two runs never share one. Returns its path."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return tmp


def _write_atomic(p: Path, text: str, retry: float | None = None) -> None:
    """Replace p with text in one step, or leave it as it was. The temporary file never outlives a failure."""
    tmp = _stage(p, text)
    try:
        _replace(tmp, p, retry)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


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
    changed, conflicts, was = [], [], {}
    for rel, text in want.items():
        p = root / rel
        on_disk = p.read_text(encoding="utf-8") if p.exists() else None
        before = snap.get(rel) if snap is not None else on_disk
        if text == before:
            continue
        if snap is not None and on_disk != before:
            conflicts.append(rel)
        changed.append(rel)
        was[rel] = on_disk
    if conflicts:
        raise LedgerConflict(f"another session changed {', '.join(conflicts)} after this command read the ledger; "
                             f"nothing was written. Run the command again (it reads the files afresh).")
    # Every file is written to its temporary first, then each is swapped in. If one cannot be swapped in (another
    # program holds it open past the retry), the files already swapped are put back, so a save that touches two files
    # never lands half.
    staged: list[tuple[str, Path]] = []
    replaced: list[str] = []
    try:
        for rel in changed:
            staged.append((rel, _stage(root / rel, want[rel])))
        for rel, tmp in staged:
            _replace(tmp, root / rel)
            replaced.append(rel)
    except OSError as e:
        failed = next((rel for rel, _ in staged if rel not in replaced), "?")
        unrestored = []
        for rel in replaced:
            try:
                if was[rel] is None:
                    (root / rel).unlink()
                else:
                    _write_atomic(root / rel, was[rel])
            except OSError:
                unrestored.append(rel)
        for _, tmp in staged:
            with contextlib.suppress(OSError):
                tmp.unlink()
        if unrestored:
            raise LedgerError(f"could not write Docs/Ledger/{failed} ({e.strerror or e}), and could not put back "
                              f"{', '.join(unrestored)}, which now hold this command's change: check them with the "
                              f"version control's diff before going on")
        raise LedgerError(f"could not write Docs/Ledger/{failed} ({e.strerror or e}): another program holds it open. "
                          f"Nothing was saved; run the command again")
    if snap is not None:
        for rel in changed:
            snap[rel] = want[rel]
    return [f"Docs/Ledger/{rel}" for rel in changed]


_LOCK_HELD: dict[tuple, int] = {}  # (lock path, thread) -> how deep that thread holds it (the lock is re-entrant)
# The byte the lock is taken on, far past the holder note at the start of the file, so a waiting run can still read
# the note (Windows locks are mandatory: a locked byte cannot be read by anyone else).
_LOCK_BYTE = 1 << 30


def _try_os_lock(fd: int) -> bool:
    """Take the operating system's lock on the lock file, without waiting. True when taken."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    with contextlib.suppress(OSError):
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def ledger_lock(path: Path | None = None, wait: float | None = None):
    """Serialise every change to the ledger files.

    The lock is the operating system's lock on a file that is never deleted (msvcrt.locking on Windows, flock
    elsewhere). The system frees it when the holding process ends, however it ends, so a crashed run cannot leave a
    lock behind, and there is no takeover to race over. The holder writes a one-line note at the start of the file
    (its process id, when, what it runs) for the message a waiting run prints. A run waits up to LOCK_WAIT_S for the
    holder, then refuses. Re-entrant within one thread: main() holds it for a changing command and save() takes it
    again. Two threads of one process (the website) each open the file, so they exclude each other too."""
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
    for old in path.parent.glob(path.name + ".dead-*"):  # left by the lock this one replaced
        with contextlib.suppress(OSError):
            old.unlink()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
        while not _try_os_lock(fd):
            if time.monotonic() > deadline:
                try:
                    held = path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    held = ""
                raise LedgerError(f"another ledger.py run holds {path} ({held or 'no note'}) and is still running; "
                                  f"the system frees the lock when it ends, so try again when it finishes")
            time.sleep(0.1)
        with contextlib.suppress(OSError):
            note = f"pid {os.getpid()}, since {datetime.now().isoformat(timespec='seconds')}: {' '.join(sys.argv[1:3])}\n"
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, note.encode("utf-8", "replace"))
        _LOCK_HELD[key] = 1
        try:
            yield
        finally:
            _LOCK_HELD.pop(key, None)
            _os_unlock(fd)
    finally:
        os.close(fd)


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


def audit_allowed(path: str, allowed: list[str]) -> bool:
    """path_allowed, as the audit reads a commit. A version control lists every file of a committed folder and the new
    folder itself, so a list entry that names a folder allows what is in it whether or not it ends in / (the tool adds
    a folder named on the command line as it was written: 'Content/Art/Props/WreckPanel'), and a folder above an entry
    is allowed (it holds nothing of its own). Run on a working ledger's 2237 commits, the stricter test flagged 34 commits made
    through the tool."""
    p = norm(path).rstrip("/")
    for a in allowed:
        a = norm(a).rstrip("/")
        if a and (p == a or p.startswith(a + "/") or a.startswith(p + "/")):
            return True
    return False


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

    # A field the tool does not know is refused: a misspelled 'dependOn' was accepted and ignored, and the task was
    # listed READY while its real dependency was unfinished.
    known = {"epics": EPIC_FIELDS, "tasks": TASK_FIELDS | {NEEDS_OWNER}, "decisions": DECISION_FIELDS}
    for coll, fields in known.items():
        for item in ledger[coll]:
            for k in item:
                if k not in fields:
                    errs.append(f"{item.get('id', '?')}: unknown field '{k}' (a misspelling? the fields a "
                                f"{coll[:-1]} may carry are listed in ledger.py, {coll[:-1].upper()}_FIELDS)")
    waves = ledger.get("waves")

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
        if d.get("ruledOn") and not date_ok(d["ruledOn"]):
            errs.append(f"{i}: ruledOn '{d['ruledOn']}' is not a date (YYYY-MM-DD)")
        for n, s in enumerate(d.get("superseded") or []):
            if not (isinstance(s, dict) and s.get("ruling") and s.get("on")
                    and len(str(s.get("why", "")).strip()) >= REASON_MIN):
                errs.append(f"{i}: superseded entry {n + 1} needs the old 'ruling', 'on' and a 'why' of "
                            f"{REASON_MIN}+ characters")
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
        if "wave" in t and (not isinstance(t["wave"], int) or isinstance(t["wave"], bool)):
            errs.append(f"{i}: wave '{t['wave']}' is a whole number")
        elif waves is not None and "wave" in t and str(t["wave"]) not in waves:
            errs.append(f"{i}: wave {t['wave']} is not in meta.json's waves table; name the wave there first "
                        f"(add-plan can), or use one it has")
        if "session" in t and not (isinstance(t["session"], str) and t["session"].strip()):
            errs.append(f"{i}: session is the id of the Claude Code session that holds it")
        for n, ed in enumerate(t.get("edits") or []):
            if not (isinstance(ed, dict) and ed.get("on") and isinstance(ed.get("changed"), dict)
                    and len(str(ed.get("reason", "")).strip()) >= REASON_MIN):
                errs.append(f"{i}: edits entry {n + 1} needs 'on', 'changed' and a 'reason' of {REASON_MIN}+ characters")
        for n, r in enumerate(t.get("reopened") or []):
            if not (isinstance(r, dict) and r.get("on") and len(str(r.get("why", "")).strip()) >= REASON_MIN):
                errs.append(f"{i}: reopened entry {n + 1} needs 'on' and a 'why' of {REASON_MIN}+ characters")
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
                errs.extend(f"{n}: unknown field '{k}' in a finding" for k in f if k not in FINDING_FIELDS)
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
    def wave_of(x: dict) -> int:
        w = x.get("wave", 0)
        return w if isinstance(w, int) else 0  # a wave that is not a number is refused above

    for t in ledger["tasks"]:
        for dep in t.get("dependsOn") or []:
            if dep in tasks and wave_of(tasks[dep]) > wave_of(t):
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


def date_ok(s) -> bool:
    """True for a real calendar date written YYYY-MM-DD."""
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s))) and bool(datetime.strptime(str(s), "%Y-%m-%d"))
    except ValueError:
        return False


def current_session() -> str | None:
    """The Claude Code session running this command, from the CLAUDE_CODE_SESSION_ID Claude Code sets in its shells
    (it is the session's transcript name, and the session_id its hooks receive). None outside Claude Code."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    return sid or None


def holder_text(t: dict) -> str:
    """Who holds a task, in words, for `next`."""
    if t.get("heldBy"):
        return f"held by {t['heldBy']}"
    return "no holder recorded" if not t.get("session") else f"held by session {t['session'][:8]}"


def has_holder(t: dict) -> bool:
    return bool(t.get("heldBy") or t.get("session"))


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
        print(f"  IN PROGRESS  {t['id']}  (wave {t['wave']})  {t['title']}  -- {holder_text(t)}, last touched "
              f"{age_text(last_touched(t))}")
    # In-progress tasks with no dated note were read as some other session's and walked past for later waves. Nobody
    # held them. Say so where the list is read. A task whose holder is recorded is that holder's: another session
    # takes it only with `start --takeover`.
    stale_active = [t for t in active if "STALE?" in age_text(last_touched(t))]
    unheld = [t for t in stale_active if not has_holder(t)]
    if unheld:
        print(f"  -> {len(unheld)} in-progress task(s) flagged STALE? have no holder recorded, so they are nobody's: "
              f"continue them (`start` records you as the holder), lowest wave first, BEFORE any later-wave READY "
              f"task below.")
    if len(stale_active) > len(unheld):
        print(f"  -> {len(stale_active) - len(unheld)} STALE? task(s) are held by another session: theirs, unless that "
              f"session is gone - then `start <id> --takeover \"<why>\"`.")
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
    # A decision the owner has already answered on the website is waiting to be applied (listed first, above), not a
    # question to ask again: a session reading only the last lines asked it a second time.
    answered = {d["id"] for d, _ in waiting}
    open_ = [d for d in ledger["decisions"] if d["status"] == "open" and d["id"] not in answered]
    if open_:
        gating = {}
        for t in ledger["tasks"]:
            for dep in t.get("dependsOn") or []:
                if dep in decisions:
                    gating.setdefault(dep, []).append(t)
        first = sorted(open_, key=lambda d: min([x["wave"] for x in gating.get(d["id"], [])] or [10 ** 9]))[0]
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

def move_errors(t: dict, status: str) -> list[str]:
    """Why a task's status may not make this move (MOVES), with what to run instead."""
    old = t.get("status")
    if status in MOVES.get(old, set()):
        return []
    if old in FINISHED and status == "in-progress":
        return [f"{t['id']} is {old}: it comes back only with `start {t['id']} --reopen \"<why>\"`, which clears its "
                f"evidence and landed commit and keeps them in its history"]
    if old == "planned" and status == "done":
        return [f"{t['id']} was never started: `start {t['id']}` first (it records who holds it), then done"]
    if old == "blocked" and status == "done":
        return [f"{t['id']} is blocked: `start {t['id']}` once the blocker is gone, then done"]
    return [f"{t['id']} is {old}: it cannot move to {status}"]


def set_status(ledger: dict, ident: str, status: str, reopen: bool = False, **extra) -> list[str]:
    _, tasks, decisions = index(ledger)
    t = tasks.get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if not reopen:
        errs = move_errors(t, status)
        if errs:
            return errs
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


def overlaps(a: str, b: str) -> bool:
    """True when two files-list entries can name the same file: equal, or one is a folder holding the other."""
    a, b = norm(a), norm(b)
    return a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def shared_entries(ledger: dict, t: dict) -> dict[str, list[str]]:
    """Each of a task's files entries that another open task can also edit: {entry: [the other tasks' ids]}."""
    out: dict[str, list[str]] = {}
    for f in t.get("files") or []:
        others = [x["id"] for x in ledger["tasks"] if x["id"] != t["id"] and x["status"] not in FINISHED
                  and any(overlaps(f, g) for g in x.get("files") or [])]
        if others:
            out[f] = others
    return out


def files_ever(t: dict) -> list[str]:
    """Every files list entry the task has held: its list now, plus what edit-task or start --files narrowed away. A
    commit made while an entry was on the list stays inside the task's files for the audit."""
    out = list(t.get("files") or [])
    for ed in t.get("edits") or []:
        before = ((ed.get("changed") or {}).get("files") or [None])[0] if isinstance(ed, dict) else None
        out += [f for f in before or [] if f not in out]
    return out


def edit_record(t: dict, changed: dict, reason: str, by: str | None = None) -> None:
    """Append one edit to the task's history: {on, by, reason, changed: {field: [before, after]}}."""
    entry = {"on": stamp_now(), "reason": reason.strip(), "changed": changed}
    if by:
        entry["by"] = by
    t.setdefault("edits", []).append(entry)


def narrow_errors(old: list[str], new: list[str]) -> list[str]:
    """Why a files list cannot be narrowed to this one: every new entry lies inside an entry of the old list."""
    if not new:
        return ["--files names at least one path"]

    def inside(f: str, o: str) -> bool:
        f, o = norm(f), norm(o)
        return f == o or (o.endswith("/") and f.startswith(o))

    wider = [f for f in new if not any(inside(f, o) for o in old)]
    return [f"--files can only narrow the list at start; not inside it: {wider} (to widen it, commit with --reason, or "
            f"edit-task)"] if wider else []


def cmd_start(ledger: dict, ident: str, by: str | None, session: str | None = None, takeover: str | None = None,
              reopen: str | None = None, files: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Start a task, or re-claim one. Returns (errors, lines to print).

    Nothing claimed a task: `start` on a task in progress under another session passed silently and replaced its
    holder, `done` passed on a task never started, and `start` on a done task reopened it with its old evidence. Now
    `start` records the holder (--by, else this Claude Code session), refuses a task another holder has unless
    --takeover says why, and brings a done or withdrawn task back only with --reopen, which clears its evidence."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"], []
    if is_subtask(t):
        errs = start_errors(ledger, ident, by)
        if errs:
            return errs, []
        return set_status(ledger, ident, "in-progress", heldBy=by.strip()), []
    by = (by or "").strip() or None
    session = (session or "").strip() or None
    me = by or (f"session {session[:8]}" if session else None)
    lines: list[str] = []
    st = t["status"]
    extra: dict = {"heldBy": me, "session": session}
    if st in FINISHED:
        if len((reopen or "").strip()) < REASON_MIN:
            return move_errors(t, "in-progress") + ([f"--reopen needs a reason of at least {REASON_MIN} characters"]
                                                    if reopen else []), []
        trial = copy.deepcopy(ledger)
        tt = index(trial)[1][ident]
        tt.setdefault("reopened", []).append({k: v for k, v in {
            "on": stamp_now(), "why": reopen.strip(), "by": me, "was": st,
            "evidence": tt.pop("evidence", None), "landed": tt.pop("landed", None)}.items() if v is not None})
        errs = set_status(trial, ident, "in-progress", reopen=True, **extra)
        if errs:
            return errs, []
        cmd_note(trial, ident, f"Reopened by {me or 'an unnamed run'}: {reopen.strip()}")
        ledger.clear()
        ledger.update(trial)
        t = index(ledger)[1][ident]
        lines.append(f"{ident} reopened: its evidence and landed commit moved to its 'reopened' history")
    elif st == "in-progress":
        same = (session and t.get("session") == session) or (not session and me and t.get("heldBy") == me) \
            or (session and not t.get("session") and me and t.get("heldBy") == me)
        if same:
            lines.append(f"{ident} is already in progress under you ({holder_text(t)}); nothing changed")
            if not files:
                return [], lines
        elif has_holder(t):
            if len((takeover or "").strip()) < REASON_MIN:
                return [f"{ident} is in progress, {holder_text(t)} (started {t.get('startedAt', '?')}). It is that "
                        f"session's: if that session is gone, take it over with --takeover \"<why>\" "
                        f"({REASON_MIN}+ characters), and the takeover is noted"], []
            cmd_note(ledger, ident, f"Taken over from {holder_text(t)[8:]} by {me or 'an unnamed run'}: "
                                    f"{takeover.strip()}")
            for k in ("heldBy", "session"):
                t.pop(k, None)
            t.update({k: v for k, v in extra.items() if v})
            lines.append(f"{ident} taken over; the note says from whom and why")
        else:
            t.update({k: v for k, v in extra.items() if v})
            lines.append(f"{ident} had no holder recorded; it is now {holder_text(t)}")
    else:
        errs = set_status(ledger, ident, "in-progress", **extra)
        if errs:
            return errs, []
        t = index(ledger)[1][ident]
        if not me:
            lines.append(f"{ident}: no holder recorded (no --by, and not run from a Claude Code session)")
    if files:
        new = [norm(f) for f in files]
        errs = narrow_errors(t.get("files") or [], new)
        if errs:
            return errs, []
        if new != t.get("files"):
            edit_record(t, {"files": [list(t.get("files") or []), new]}, "narrowed at start (start --files)", me)
            t["files"] = new
            lines.append(f"{ident}: files narrowed to {', '.join(new)}")
    shared = shared_entries(ledger, t)
    if shared:
        lines.append(f"{ident} shares files entries with other open tasks (a commit by either can touch the same file; "
                     f"narrow with `start {ident} --files <paths>`):")
        for f, others in shared.items():
            lines.append(f"    {f}  also on {', '.join(others[:8])}" + (f" and {len(others) - 8} more" if len(others) > 8 else ""))
    return [], lines


def done_errors(ledger: dict, ident: str, evidence: str | None) -> list[str]:
    """Why `done` cannot close this task: a subtask is confirmed by the hub, and evidence starts with its label."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if is_subtask(t):
        return [f"{ident} is a department subtask: its agent hands it in with `submit`, and the hub's `confirm` "
                f"marks it done"]
    if not RE_EVIDENCE_LABEL.match(evidence or ""):
        return [f"{ident}: evidence starts with its label - {', '.join(EVIDENCE_LABELS)} - then says what was "
                f"observed: --evidence \"ran-it: <what ran and what it showed>\""]
    return []


def cmd_edit_task(ledger: dict, ident: str, reason: str, changes: dict, by: str | None = None) -> list[str]:
    """Change an unfinished task's title, wave, dependsOn, files or acceptance, recording each change and why in its
    'edits'. Plans used to change through scripts that wrote the JSON, with nothing in the ledger saying so."""
    t = index(ledger)[1].get(ident)
    if not t:
        return [f"unknown task {ident}"]
    if t["status"] in FINISHED:
        return [f"{ident} is {t['status']}: a finished task's record stays as it was closed"]
    if len((reason or "").strip()) < REASON_MIN:
        return [f"edit-task needs --reason of at least {REASON_MIN} characters"]
    bad = [k for k in changes if k not in EDITABLE]
    if bad:
        return [f"edit-task changes only {', '.join(EDITABLE)}, not {', '.join(bad)}"]
    changed = {k: [copy.deepcopy(t.get(k)), v] for k, v in changes.items() if t.get(k) != v}
    if not changed:
        return [f"{ident}: nothing to change (the values given are the ones it has)"]
    trial = copy.deepcopy(ledger)
    tt = index(trial)[1][ident]
    for k, (_, v) in changed.items():
        if v in (None, []) and k == "dependsOn":
            tt.pop(k, None)
        else:
            tt[k] = v
    edit_record(tt, changed, reason, by)
    errs = [e for e in validate(trial) if e.startswith(ident + ":") or e.startswith(ident + "#")]
    if errs:
        return errs
    ledger.clear()
    ledger.update(trial)
    return []


def cmd_decide(ledger: dict, ident: str, ruling: str, by: str, date: str, supersede: str | None = None,
               via: str = "chat") -> list[str]:
    _, _, decisions = index(ledger)
    d = decisions.get(ident)
    if not d:
        return [f"unknown decision {ident}"]
    # --by defaulted to the owner, so anything could record a ruling in the owner's name.
    if not (by or "").strip():
        return [f"{ident}: decide names who ruled, with --by ({OWNER}'s website answers go through apply instead)"]
    if not (ruling or "").strip():
        return [f"{ident}: decide needs the ruling's text"]
    # A date that is not one passed validate, and a ruling in the future is a typo.
    if not date_ok(date):
        return [f"{ident}: --date '{date}' is not a date; write YYYY-MM-DD"]
    if date > datetime.now().strftime("%Y-%m-%d"):
        return [f"{ident}: --date {date} is in the future"]
    # A second decide overwrote the ruling, its name and its date without a word.
    if d.get("status") == "decided":
        if len((supersede or "").strip()) < REASON_MIN:
            return [f"{ident} is already decided ({d.get('ruledBy', '?')}, {d.get('ruledOn', '?')}): a new ruling "
                    f"replaces it only with --supersede \"<why>\" ({REASON_MIN}+ characters); the old one is kept"]
        d.setdefault("superseded", []).append({k: v for k, v in {
            "ruling": d.get("ruling"), "ruledBy": d.get("ruledBy"), "ruledOn": d.get("ruledOn"),
            "ruledVia": d.get("ruledVia"), "chosen": d.pop("chosen", None), "why": supersede.strip(),
            "on": datetime.now().strftime("%Y-%m-%d")}.items() if v is not None})
    d["status"] = "decided"
    d["ruling"] = ruling
    d["ruledBy"] = by
    d["ruledOn"] = date
    d["ruledVia"] = via
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
    errs = cmd_decide(ledger, ident, answer_ruling(d, ans), OWNER, str(ans.get("answeredAt", ""))[:10], via="website")
    if errs:
        return errs
    d["chosen"] = ans["choice"]
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
    code = f"exec(open(r'{script}', encoding='utf-8').read()); run('{ident}')"
    if EDITOR_PYTHON:  # ledger_config.json names the command that runs Python in the open editor
        return f"{EDITOR_PYTHON} \"{code}\""
    return f"in the editor's Python: {code}"


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


def dispatch_prompt(ledger: dict, t: dict, dept: dict, dirty: list[str] | None = None) -> str:
    """The agent's whole brief, written from the task so nobody retells it from memory. dirty: the covered files that
    had uncommitted changes when it was dispatched (with --dirty-ok)."""
    tasks = index(ledger)[1]
    name = f"{t['owner']} agent"
    L = [f"You are the {name}, working ledger task {t['id']}, a {t['kind']} subtask: {t['title']}",
         f"Your name for every --by is \"{name}\".", "",
         "Follow the department steps in your agent file, in order. The first is:",
         f"    python Docs/Ledger/ledger.py show {t['id']}", ""]
    covers = t.get("covers") or []
    if covers:
        # The prompt said "at their landed commits", but Diversion has no command that prints a file as of a commit, so
        # an agent read the working copy, other sessions' uncommitted edits included. dispatch now checks the working
        # copy instead, and the prompt says what is read.
        dirty = dirty or []
        L.append(f"What this {t['kind']} covers. Read these files as they stand in the working copy; edit nothing. "
                 + ("At dispatch none of them had uncommitted changes, so the working copy is what landed:"
                    if not dirty else "At dispatch these had uncommitted changes, so judge the code, and say in a "
                    f"finding's text when a line you cite may be someone's unfinished edit: {', '.join(dirty)}"))
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


def uncommitted(files: list[str]) -> list[str]:
    """The files under these files-list entries that have uncommitted changes, from the version control. Raises
    RuntimeError when it cannot be asked. The selftest replaces it (UNCOMMITTED) with a stand-in."""
    out: list[str] = []
    for f in dict.fromkeys(norm(x) for x in files):
        out += [p for p in changed_paths(f.rstrip("/") or ".") if overlaps(f, p) and p not in out]
    return out


UNCOMMITTED = uncommitted


def cmd_dispatch(ledger: dict, ident: str, by: str | None, dump: str | None,
                 restart: bool, dirty_ok: str | None = None,
                 session: str | None = None) -> tuple[list[str], dict | None]:
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
    dirty: list[str] = []
    if t["kind"] == "review" and covers:
        try:
            dirty = UNCOMMITTED(covered_files(ledger, covers))
        except (RuntimeError, OSError) as e:
            return [f"{ident}: could not ask {VCS.name} which covered files have uncommitted changes ({e}); a review "
                    f"reads the working copy, so dispatch checks it first"], None
        if dirty and len((dirty_ok or "").strip()) < REASON_MIN:
            shown = ", ".join(dirty[:10]) + (f" and {len(dirty) - 10} more" if len(dirty) > 10 else "")
            return [f"{ident}: covered files have uncommitted changes, and the agent reads the working copy, not the "
                    f"landed commits: {shown}. Commit or wait for them, or dispatch with --dirty-ok \"<why>\" "
                    f"({REASON_MIN}+ characters) and the prompt names them"], None
    t["covers"] = covers
    t["dispatchedAt"] = stamp_now()
    t["dispatchedBy"] = by.strip()
    if session:
        t["dispatchedSession"] = session
    if folder:
        t["dump"] = norm(folder)
    if dirty:
        cmd_note(ledger, ident, f"Dispatched with uncommitted changes in {', '.join(dirty[:20])}: {dirty_ok.strip()}")
    if restart and st != "planned":
        cmd_note(ledger, ident, f"Restarted by {by.strip()}: a new {dept['agent']} agent is started on this task; the "
                                f"findings already recorded stay.")
    return [], {"agent": dept["agent"], "background": t["kind"] == "review", "prompt": dispatch_prompt(ledger, t, dept, dirty)}


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


def write_render(ledger: dict | None = None, required: bool = False) -> bool:
    """Write LEDGER.md from the files. A command that has already saved the ledger does not fail on this: when another
    program holds LEDGER.md open past the retry, it says the change is saved and LEDGER.md is behind (the next render,
    by any command, catches it up), rather than end in a traceback after the save. `render` itself passes
    required=True and refuses."""
    try:
        _write_atomic(LEDGER_MD, render(_on_disk(ledger)), min(RENDER_RETRY_S, REPLACE_RETRY_S))
        return True
    except OSError as e:
        msg = (f"{LEDGER_MD_REL} could not be written ({e.strerror or e}; another program holds it open), so it is "
               f"behind the ledger files. Run `python Docs/Ledger/ledger.py render` to catch it up")
        if required:
            raise LedgerError(msg)
        print(f"WARNING: the change is saved, but {msg}", file=sys.stderr)
        return False


def persist(ledger: dict) -> str:
    """save() then write_render(), in words for the command's last line."""
    wrote = save(ledger)
    saved = f"saved {', '.join(wrote) or 'nothing new'}"
    return f"{saved} and re-rendered" if write_render(ledger) else f"{saved}; LEDGER.md is behind (run render)"


# --------------------------------------------------------------------------- commit rules (pure)

def subject_id(subject: str) -> str | None:
    """The first task or epic id a commit subject starts with, or None."""
    m = RE_SUBJECT_ID.match(subject.strip())
    return m.group(1) if m else None


def subject_ids(subject: str) -> list[str]:
    """Every task or epic id at the start of a subject, in order: 'PR-E01.02 PR-E01.03: ...', 'PR-E01.02, PR-E01.03
    ...' and 'PR-E01.02 and PR-E01.03 ...' all name both. A commit that did several tasks' work named them all and the
    tool read only the first, so the others were never stamped landed (16 of 39 such tasks in one ledger)."""
    out: list[str] = []
    rest = subject.strip()
    while True:
        m = RE_SUBJECT_ID.match(rest)
        if not m:
            break
        out.append(m.group(1))
        rest = rest[m.end():]
        rest = rest[re.match(r"\s*(?:,|&|\+|and\b)?\s*", rest).end():]
    return list(dict.fromkeys(out))


def named_files(ledger: dict, ids: list[str], ever: bool = False) -> list[str]:
    """The files a commit naming these ids may carry: each named task's list, and for an epic id every list in it.
    ever=True takes every list a task has held (files_ever), as the audit does."""
    epics, tasks, _ = index(ledger)
    lists = files_ever if ever else (lambda t: t.get("files") or [])
    out: list[str] = []
    for i in ids:
        if i in tasks:
            out += lists(tasks[i])
        elif i in epics:
            out += [f for t in ledger["tasks"] if t["epic"] == i for f in lists(t)]
    return out


def unlanded_in(ledger: dict, paths: list[str], named: list[str]) -> list[str]:
    """The done tasks with no landed commit, in the epic files among these paths, that the commit does not name. A
    task's `done` committed in another commit (a "Ledger: ... done" role commit, or the next task's commit, which
    carries the same epic file) was never stamped landed: 39 tasks in one ledger."""
    epics = {m.group(1) for m in (RE_EPIC_FILE.match(norm(p)) for p in paths) if m}
    return [t["id"] for t in ledger["tasks"] if t["epic"] in epics and t["status"] == "done" and not t.get("landed")
            and t["id"] not in named]


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
    ids = subject_ids(subject)
    ident = ids[0] if ids else None
    paths = [norm(p) for p in paths]
    if not paths:
        errs.append("no paths given; paths are explicit and there is no -a")
    # A Role line typed into the message file passed with no --role and no --reason, and then no path rule applied.
    # The role comes from the flag only; the tool writes the Role and Reason lines itself.
    typed = role_lines(message)
    if typed:
        errs.append(f"the message file carries a 'Role: {typed[0]}' line; name the role with --role and say why with "
                    f"--reason (the tool writes both lines)")
    roles = [role] if role else []
    for r in roles:
        if r not in ROLES:
            errs.append(f"role '{r}' is not one of {ROLES}")
    if role and not (reason and len(reason.strip()) >= REASON_MIN):
        errs.append(f"a role commit needs --reason of at least {REASON_MIN} characters")
    if not ident and not roles:
        errs.append(f"subject names no task ({PREFIX}-Enn.mm) or epic ({PREFIX}-Enn), and no --role was given")
        return errs

    # Several ids may lead the subject; each named task is checked, and the paths may be in any of their lists.
    for i in ids:
        if RE_TASK.match(i):
            t = tasks.get(i)
            if not t:
                errs.append(f"{i}: no such task")
                continue
            if t["status"] == "planned":
                errs.append(f"{i}: task is still planned; run `start {i}` first")
            if t["status"] == "done" and t.get("landed") and role != "Repair":
                errs.append(f"{i}: already done and landed at {t['landed']}; new work on it is --role Repair")
            if t["status"] == "done" and not t.get("landed") and epic_rel(t["epic"]) not in paths:
                errs.append(f"{i}: closing a task commits {epic_rel(t['epic'])} with it")
        elif i not in epics:
            errs.append(f"{i}: no such epic")
        elif epic_rel(i) not in paths:
            errs.append(f"{i}: an epic-level commit changes the ledger, so {epic_rel(i)} is among the paths")
    if errs and any("no such" in e for e in errs):
        return errs
    if ids and not role:
        outside = [p for p in paths if not is_ledger_file(p) and not path_allowed(p, named_files(ledger, ids))]
        if outside and RE_TASK.match(ident):
            errs.append(f"{ident}: paths outside the task's files list: {outside}. If they are this task's work, "
                        f"commit again with --reason \"<why they belong to it>\" ({REASON_MIN}+ characters) and they "
                        f"are added to its files list")
        elif outside:
            errs.append(f"{ident}: paths outside every file list in the epic: {outside}")

    if any(is_ledger_data(p) for p in paths):
        # A task is closed by the commit whose subject names it: that is where landed is stamped. A `done` that rides
        # in any other commit leaves the task done with no landed commit, which the audit then refuses for good.
        loose = unlanded_in(ledger, paths, ids)
        if loose:
            errs.append(f"{', '.join(loose)} {'is' if len(loose) == 1 else 'are'} done with no landed commit, and this "
                        f"commit carries {'its' if len(loose) == 1 else 'their'} epic file without naming "
                        f"{'it' if len(loose) == 1 else 'them'}. Close {'it' if len(loose) == 1 else 'them'} with this "
                        f"commit by starting the subject with the id{'s' if len(loose) > 1 else ''} too (several may "
                        f"lead it: '{' '.join(loose[:3])}{' ...' if len(loose) > 3 else ''}: ...'), and the tool stamps "
                        f"landed")
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
        ids = subject_ids(c["subject"])
        ident = ids[0] if ids else None
        roles = role_lines(c.get("message", ""))
        bad_roles = [r for r in roles if r not in ROLES]
        if bad_roles:
            errs.append(f"{c['id']}: carries role(s) {bad_roles} that are not in {ROLES}")
        # The tool writes a Reason line under every Role line; a Role line without one was typed by hand into a commit
        # made outside the tool, and would excuse the commit from every path rule.
        if roles and not any(len(r) >= REASON_MIN for r in RE_REASON_LINE.findall(c.get("message", ""))):
            errs.append(f"{c['id']}: carries 'Role: {roles[0]}' with no 'Reason:' line of {REASON_MIN}+ characters; a "
                        f"role commit made through ledger.py always has one")
        if ident:
            in_scope.append(c["id"])
            known = True
            for i in ids:
                if RE_TASK.match(i):
                    t = tasks.get(i)
                    if not t:
                        errs.append(f"{c['id']}: names unknown task {i}")
                        known = False
                    elif t["status"] == "planned":
                        errs.append(f"{c['id']}: names {i}, which is still planned")
                        known = False
                elif i not in epics:
                    errs.append(f"{c['id']}: names unknown epic {i}")
                    known = False
            if known and not roles:
                # A commit made with the version control directly, naming a task, carried any file it liked.
                outside = [p for p in c.get("paths", []) if not is_ledger_file(p)
                           and not audit_allowed(p, named_files(ledger, ids, ever=True))]
                if outside:
                    where = ("outside every file list in it" if not RE_TASK.match(ident) else
                             "outside its files list" if len(ids) == 1 else "outside their files lists")
                    errs.append(f"{c['id']}: names {'epic ' if not RE_TASK.match(ident) else ''}{', '.join(ids)} but "
                                f"changed paths {where}: {outside[:5]}"
                                + (f" and {len(outside) - 5} more" if len(outside) > 5 else ""))
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
                if t["id"] not in subject_ids(c["subject"]):
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


def renamed_to(p: str) -> str:
    """A rename as Diversion lists it, 'old -> new', is the path it now has."""
    return norm(p.split(" -> ")[-1])


def commit_paths(cid: str, cache: dict | None = None) -> list[str]:
    """The paths a commit changed. A commit never changes, so `cache` (the audit's, kept in PATHS_CACHE) is
    trusted once filled."""
    if cache is not None and cid in cache:
        return [renamed_to(p) for p in cache[cid]]
    paths = [renamed_to(p) for p in VCS.commit_paths(cid)]
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
    ids = subject_ids(message.strip().splitlines()[0]) if message.strip() else []
    named_epics = list(dict.fromkeys(tasks[i]["epic"] if i in tasks else i for i in ids if i in tasks or i in epics))
    if LEDGER_REL in paths and ledger_changes.get(LEDGER_REL) != "D":
        paths = [p for p in paths if p != LEDGER_REL]
        stand_in = ([epic_rel(e) for e in named_epics] if named_epics else
                    sorted(p for p in ledger_changes if is_ledger_data(p) and p != LEDGER_REL))
        paths += [p for p in stand_in if p not in paths]
        notes.append(f"{LEDGER_REL} is split into meta.json and epics/; committing "
                     f"{', '.join(stand_in) or 'no ledger file'} in its place")
    if named_epics:
        paths += [p for p in [epic_rel(e) for e in named_epics] + [LEDGER_MD_REL] if p not in paths]
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
    ids = subject_ids(message.strip().splitlines()[0]) if message.strip() else []
    ident = ids[0] if ids else None
    added: list[str] = []
    task = index(ledger)[1].get(ident) if ident and RE_TASK.match(ident) else None
    outside = [p for p in paths if not is_ledger_file(p) and not path_allowed(p, named_files(ledger, ids))]
    if task and not role and reason and outside:
        # A file the task needs is added to its list (the first named task's) with a written reason, not refused.
        added = outside
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
    # Every task the subject names that is now done and not landed is closed by this commit.
    ledger2 = load()
    tasks2 = index(ledger2)[1]
    closing = [i for i in ids if i in tasks2 and tasks2[i]["status"] == "done" and not tasks2[i].get("landed")]
    if closing:
        for i in closing:
            tasks2[i]["landed"] = cid
        save(ledger2)
        write_render(ledger2)
        stamp = f"{' '.join(closing)} ledger: landed {cid}"
        if agent:
            stamp += f"\n\nCo-Authored-By: {agent} <noreply@anthropic.com>"
        files = list(dict.fromkeys(epic_rel(tasks2[i]["epic"]) for i in closing)) + [LEDGER_MD_REL]
        cid2, _ = VCS.commit(files, stamp)
        print(f"stamped landed={cid} on {', '.join(closing)}; ledger committed as "
              f"{cid2 or f'(id not found in {VCS.cmd} output)'}")
        if cid2:
            print(VCS.show(cid2))
    return 0


# --------------------------------------------------------------------------- selftest

def _fixture() -> dict:
    return {
        "schema": "example.ledger", "schemaVersion": 1, "title": "t", "auditFrom": "dv.commit.10",
        "gates": {"g": "x"}, "conventions": {"c": "x"}, "waves": {"1": "a", "2": "b"}, "outsideCommits": [],
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
    saved_uncommitted = UNCOMMITTED
    apply_config(SELFTEST_PROFILE)
    globals()["UNCOMMITTED"] = lambda files: []  # no version control is asked; checks that need it set their own
    try:
        return _selftest()
    finally:
        apply_config(saved)
        globals()["UNCOMMITTED"] = saved_uncommitted


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
    expect("a Role line typed into the message file (the role comes from --role only)",
           check_commit(base, "x\n\nRole: Checkpoint", None, None, ok_paths), True, "carries a 'Role: Checkpoint' line")
    expect("the same with --role and a reason too",
           check_commit(base, "x\n\nRole: Checkpoint", "Checkpoint", "full checkpoint at owner request", ok_paths),
           True, "carries a 'Role")
    expect("a --role that is not a role", check_commit(base, "x", "Audio", "a reason long enough here", ok_paths), True,
           "not one of")
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
        {"id": "dv.commit.12", "subject": "Checkpoint", "message": "Checkpoint\n\nRole: Checkpoint\nReason: a full "
         "checkpoint for the owner", "paths": ["Source/A.cpp"]},
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

            def holder(path: Path):
                """Another process that takes the lock on path and keeps it until it is killed."""
                code = (f"import sys, time; sys.path.insert(0, {str(HERE)!r}); import ledger\n"
                        f"with ledger.ledger_lock(ledger.Path({str(path)!r}), wait=5):\n"
                        f"    print('held', flush=True); time.sleep(60)\n")
                proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
                return proc, proc.stdout.readline().strip() == "held"

            # The old lock file named a process id; a crashed run's file, whose id now belonged to another program,
            # made every command wait 30 seconds and refuse. The system's own lock dies with its process.
            LOCK.write_text("4242 an old run's lock file 2026-01-01T00:00:00\n", encoding="utf-8")
            expect("an old lock file nobody holds does not block (control)", try_lock(), False)
            proc, held = holder(LOCK)
            try:
                errs = try_lock() if held else ["the other process never took the lock"]
                expect("a lock another process holds is not taken, and the refusal names the holder", errs, True,
                       "still running")
                expect("the refusal quotes the holder's note (control)",
                       [] if any(f"pid {proc.pid}" in e for e in errs) else [f"errs={errs}"], False)
            finally:
                proc.kill()
                proc.wait()
            expect("a holder that dies without letting go leaves no lock behind (control)", try_lock(), False)
            with ledger_lock(wait=0.3):
                inner = try_lock(0.3)
            expect("the lock is re-entrant within one thread (control)", inner, False)
            got: list = []
            with ledger_lock(wait=0.3):
                th = threading.Thread(target=lambda: got.extend(try_lock(0.3)))
                th.start()
                th.join()
            expect("another thread of the same process waits for it (the website's threads)", got, True, "still running")
            expect("the lock file stays, and no .dead file is left (control)",
                   [] if LOCK.exists() and not list(LOCK.parent.glob("*.dead-*")) else ["files"], False)
            root = Path(tmp) / "root"
            f = _fixture()
            save(f, root)
            saved_wait = LOCK_WAIT_S
            globals()["LOCK_WAIT_S"] = 0.3
            proc, held = holder(root / ".ledger.lock")
            try:
                index(f)[1]["PR-E01.01"]["notes"] = "changed"
                try:
                    save(f, root)
                    errs = [] if held else ["the other process never took the lock"]
                except LedgerError as e:
                    errs = [str(e)]
            finally:
                globals()["LOCK_WAIT_S"] = saved_wait
                proc.kill()
                proc.wait()
            expect("save() waits for the lock another run holds, and refuses rather than write around it", errs, True,
                   "still running")
            # R04: an answer waiting on the website is not asked again as the next decision.
            f = _fixture()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_next(f)
            ok = "Next decision for Sam: PR-D01" in buf.getvalue()
            expect("next asks an open decision nobody has answered (control)", [] if ok else [buf.getvalue()[-300:]], False)
            save_answers({"PR-D01": {"choice": "Yes", "answeredAt": "2026-10-12 10:45"}})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_next(f)
            ok = "ANSWERED     PR-D01" in buf.getvalue() and "Next decision" not in buf.getvalue()
            expect("next does not ask again a decision answered on the website and waiting to be applied",
                   [] if ok else [buf.getvalue()[-400:]], False)
            ANSWERS_JSON.unlink()
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

    _selftest_review_fixes(expect)
    _selftest_hooks(expect)

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
    expect("a stampZone that is a short label (control)", config_errors({**good, "stampZone": "PT"}), False)
    expect("a stampZone with a space in it", config_errors({**good, "stampZone": "Pacific Time"}), True, "stampZone")
    expect("an editorPython that is not text", config_errors({**good, "editorPython": 3}), True, "editorPython")
    apply_config({**good, "stampZone": "PT", "editorPython": "python run_in_editor.py -c"})
    try:
        ok = (stamp_now().endswith(" PT") and bool(RE_NOTE_STAMP.match(stamp_now())) and days_since(stamp_now()) == 0
              and dump_command("PR-E01.04").startswith('python run_in_editor.py -c "exec(open('))
    finally:
        apply_config(SELFTEST_PROFILE)
    expect("stampZone labels every stamp, and editorPython makes the graph-dump command (control)",
           [] if ok else [stamp_now(), dump_command("PR-E01.04")], False)
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


def _selftest_review_fixes(expect) -> None:
    """Checks for the fixes the ledger review of 2026-11-09 asked for: claims, evidence labels, files, rulings, unknown
    fields, edits, plans, ids past 99, writes to files held open, text encoding, dispatch of a dirty review."""
    import io
    import tempfile
    S1, S2 = "11111111-aaaa-bbbb-cccc-000000000001", "22222222-aaaa-bbbb-cccc-000000000002"
    acc = {"check": "c", "expect": "e"}

    def task(ident: str, title: str, wave: int = 2, **kw) -> dict:
        return {"id": ident, "epic": ident.split(".")[0], "title": title, "owner": "Engineering", "size": "S",
                "wave": wave, "files": ["Source/T.cpp"], "acceptance": dict(acc), "status": "planned", **kw}

    print("claims: who holds a task:")
    f = _fixture(); f["tasks"][2]["dependsOn"] = []
    errs, _ = cmd_start(f, "PR-E01.03", None, S1)
    t = index(f)[1]["PR-E01.03"]
    ok = (not errs and t["status"] == "in-progress" and t.get("session") == S1 and t.get("heldBy") == "session 11111111"
          and not validate(f))
    expect("start records the session that holds it (control)", errs or ([] if ok else [f"task={t}"]), False)
    errs, lines = cmd_start(f, "PR-E01.03", None, S1)
    expect("start again from the same session changes nothing (control)",
           errs or ([] if any("already in progress under you" in x for x in lines) else [str(lines)]), False)
    expect("start a task another session holds", cmd_start(f, "PR-E01.03", None, S2)[0], True, "--takeover")
    expect("--takeover with too short a reason", cmd_start(f, "PR-E01.03", None, S2, takeover="gone")[0], True,
           "--takeover")
    errs, _ = cmd_start(f, "PR-E01.03", None, S2, takeover="session one was closed by Sam")
    t = index(f)[1]["PR-E01.03"]
    ok = not errs and t["session"] == S2 and "Taken over from session 11111111" in t.get("notes", "") and not validate(f)
    expect("--takeover with a reason: the holder changes and a note says so (control)",
           errs or ([] if ok else [f"task={t}"]), False)
    f = _fixture()  # PR-E01.01 is in progress with no holder recorded, as every task was before holders
    errs, lines = cmd_start(f, "PR-E01.01", None, S1)
    ok = not errs and index(f)[1]["PR-E01.01"].get("session") == S1 and any("had no holder" in x for x in lines)
    expect("start on an in-progress task with no holder claims it (control)", errs or ([] if ok else [str(lines)]), False)
    expect("a named holder with no session claims it too (control)", cmd_start(_fixture(), "PR-E01.01", "Ledger Work")[0],
           False)
    f = _fixture(); index(f)[1]["PR-E01.01"]["heldBy"] = "Ledger Work"
    expect("start under another name, no session on either side", cmd_start(f, "PR-E01.01", "Other Work")[0], True,
           "--takeover")
    f = _fixture(); f["tasks"][2]["dependsOn"] = []
    expect("done on a task that was never started",
           done_errors(f, "PR-E01.03", "ran-it: x") or set_status(f, "PR-E01.03", "done", evidence="ran-it: x"), True,
           "never started")
    f = _fixture(); index(f)[1]["PR-E01.01"].update(status="done", evidence="ran-it: it ran", landed="dv.commit.11")
    expect("start on a done task without --reopen", cmd_start(f, "PR-E01.01", None, S1)[0], True, "--reopen")
    errs, _ = cmd_start(f, "PR-E01.01", None, S1, reopen="the fix did not hold in play")
    t = index(f)[1]["PR-E01.01"]
    r = (t.get("reopened") or [{}])[0]
    ok = (not errs and t["status"] == "in-progress" and "evidence" not in t and "landed" not in t
          and r.get("evidence") == "ran-it: it ran" and r.get("landed") == "dv.commit.11" and r.get("was") == "done"
          and not validate(f))
    expect("--reopen brings it back, its evidence and landed kept in its history (control)",
           errs or ([] if ok else [f"task={t} {validate(f)}"]), False)
    f = _fixture(); index(f)[1]["PR-E01.02"]["status"] = "withdrawn"
    expect("start a withdrawn task without --reopen", cmd_start(f, "PR-E01.02", None, S1)[0], True, "--reopen")

    print("evidence starts with its label:")
    expect("done with evidence that has no label", done_errors(_fixture(), "PR-E01.01", "trust me"), True,
           "starts with its label")
    for ev in ("ran-it: the test passed", "read-in-the-code: Source/A.cpp:3 guards it",
               "read-in-a-document: the spec says so"):
        expect(f"done with {ev.split(':')[0]} evidence (control)", done_errors(_fixture(), "PR-E01.01", ev), False)

    print("files: narrowing, sharing, and the audit's file rule:")
    f = _fixture(); f["tasks"].append(dict(task("PR-E01.04", "d", 1), files=["Content/Dir/Other/"]))
    errs, lines = cmd_start(f, "PR-E01.01", None, S1)
    ok = not errs and any("Content/Dir/  also on PR-E01.04" in x for x in lines)
    expect("start names the entries another open task shares (control)", errs or ([] if ok else [str(lines)]), False)
    expect("start --files that widens the list", cmd_start(_fixture(), "PR-E01.01", None, S1, files=["Source/Z.cpp"])[0],
           True, "only narrow")
    f = _fixture()
    errs, _ = cmd_start(f, "PR-E01.01", None, S1, files=["Source/A.cpp", "Content/Dir/Sub/"])
    t = index(f)[1]["PR-E01.01"]
    ok = (not errs and t["files"] == ["Source/A.cpp", "Content/Dir/Sub/"]
          and t["edits"][0]["changed"]["files"][0] == ["Source/A.cpp", "Content/Dir/"] and not validate(f))
    expect("start --files narrows the list and records the old one (control)", errs or ([] if ok else [f"task={t}"]),
           False)
    c = [{"id": "dv.commit.13", "subject": "PR-E01.01 work", "message": "PR-E01.01 work", "paths": ["Content/Dir/Y.uasset"]}]
    expect("a commit made before the narrowing is still inside the task's files (control)", judge_commits(f, c)[0], False)
    c = [{"id": "dv.commit.13", "subject": "PR-E01.01 work", "message": "PR-E01.01 work",
          "paths": ["Source/A.cpp", "Source/Z.cpp"]}]
    expect("a commit naming a task, made outside the tool, carrying a file outside its list",
           judge_commits(_fixture(), c)[0], True, "outside its files list")
    f = _fixture(); index(f)[1]["PR-E01.01"]["files"] = ["Source/A.cpp", "Content/Kit/Panel"]
    c = [{"id": "dv.commit.13", "subject": "PR-E01.01 kit", "message": "PR-E01.01 kit",
          "paths": ["Content/Kit", "Content/Kit/Panel", "Content/Kit/Panel/SM_Panel.uasset", "Source/A.cpp"]}]
    expect("a commit of a listed folder, as Diversion lists it: the folder, its parent, its files (control)",
           judge_commits(f, c)[0], False)
    expect("a rename as Diversion lists it is the new path (control)",
           [] if renamed_to("Docs/A_PROPOSED.md -> Docs/A.md") == "Docs/A.md" else ["no"], False)
    c = [{"id": "dv.commit.13", "subject": "PR-E01.01 kit", "message": "PR-E01.01 kit", "paths": ["Content/Kitchen/X.uasset"]}]
    expect("a folder entry does not allow a folder whose name merely starts the same", judge_commits(f, c)[0], True,
           "outside its files list")
    c = [{"id": "dv.commit.13", "subject": "PR-E01: tidy", "message": "PR-E01: tidy", "paths": ["Other/Q.cpp"]}]
    expect("the same for a commit naming an epic", judge_commits(_fixture(), c)[0], True, "outside every file list")
    c = [{"id": "dv.commit.13", "subject": "PR-E01.01 more", "paths": ["Source/Z.cpp"],
          "message": "PR-E01.01 more\n\nRole: Repair\nReason: the hotfix the owner asked for"}]
    expect("a role commit naming a task is not held to its files (control)", judge_commits(_fixture(), c)[0], False)
    c = [{"id": "dv.commit.13", "subject": "Checkpoint", "message": "Checkpoint\n\nRole: Checkpoint", "paths": ["X/Y.cpp"]}]
    expect("a Role line with no Reason line, typed into a commit made outside the tool", judge_commits(_fixture(), c)[0],
           True, "no 'Reason:' line")

    print("several tasks in one commit, and closing a task:")
    ok = (subject_ids("PR-E01.01 PR-E01.02: x") == ["PR-E01.01", "PR-E01.02"]
          and subject_ids("PR-E01.01, PR-E01.02 and PR-E01.03 done") == ["PR-E01.01", "PR-E01.02", "PR-E01.03"]
          and subject_ids("PR-E01.01 work on PR-E01.02") == ["PR-E01.01"] and subject_ids("PR-E01.011 x") == []
          and subject_ids("Ledger: close PR-E01.01") == [] and subject_ids("PR-E01 PR-E01.02 x") == ["PR-E01", "PR-E01.02"])
    expect("a subject's leading ids are read, however they are joined (control)", [] if ok else ["misread"], False)
    f = _fixture(); index(f)[1]["PR-E01.02"]["status"] = "in-progress"; index(f)[1]["PR-E01.01"]["status"] = "done"
    index(f)[1]["PR-E01.01"]["evidence"] = "ran-it: x"
    expect("a commit naming two tasks may carry files from both lists (control)",
           [e for e in check_commit(f, "PR-E01.02 PR-E01.01: both", None, None, ["Source/B.cpp", "Source/A.cpp",
                                                                                    epic_rel("PR-E01")])
            if "LEDGER.md" not in e], False)
    expect("a commit naming two tasks, one still planned",
           check_commit(_fixture(), "PR-E01.01 PR-E01.02: both", None, None, ["Source/A.cpp"]), True, "still planned")
    f = _fixture(); index(f)[1]["PR-E01.01"].update(status="done", evidence="ran-it: x")
    expect("a role commit carrying the epic file of a task done with no landed commit",
           [e for e in check_commit(f, "Ledger: PR-E01.01 done", "Ledger", "the task passed its check", [epic_rel("PR-E01")])
            if "LEDGER.md" not in e], True, "done with no landed commit")
    f["tasks"].append(dict(task("PR-E01.04", "d", 1), status="in-progress", files=["Source/D.cpp"]))
    expect("another task's commit carrying the same epic file",
           [e for e in check_commit(f, "PR-E01.04 work", None, None, ["Source/D.cpp", epic_rel("PR-E01")])
            if "LEDGER.md" not in e], True, "PR-E01.01 is done with no landed commit")
    expect("the same commit naming both closes it (control)",
           [e for e in check_commit(f, "PR-E01.04 PR-E01.01: work and close", None, None,
                                    ["Source/D.cpp", epic_rel("PR-E01")]) if "LEDGER.md" not in e], False)
    plan, _ = commit_plan(f, "PR-E01.04 PR-E02.01: x", None, ["Source/D.cpp"], {})
    expect("a commit naming tasks in two epics carries the files it names (control)",
           [] if plan == ["Source/D.cpp", epic_rel("PR-E01"), LEDGER_MD_REL] else [f"plan={plan}"], False)
    g = _fixture(); g["tasks"][0].update(status="done", evidence="ran-it: x", landed="dv.commit.13")
    c = [{"id": "dv.commit.13", "subject": "PR-E01.02 PR-E01.01: both", "message": "PR-E01.02 PR-E01.01: both",
          "paths": ["Source/A.cpp"]}]
    g["tasks"][1]["status"] = "in-progress"
    expect("the audit takes a landed commit that names the task second (control)", judge_commits(g, c)[0], False)

    print("decisions: a ruling changes only with --supersede:")
    f = _decided(_fixture())
    d = index(f)[2]["PR-D01"]
    expect("decide records that the ruling came from chat (control)", [] if d.get("ruledVia") == "chat" else [str(d)],
           False)
    expect("decide on a decision already decided", cmd_decide(f, "PR-D01", "no", "Sam", "2026-10-17"), True,
           "--supersede")
    errs = cmd_decide(f, "PR-D01", "no", "Sam", "2026-10-17", supersede="Sam changed the call after the playtest")
    d = index(f)[2]["PR-D01"]
    s0 = (d.get("superseded") or [{}])[0]
    ok = (not errs and d["ruling"] == "no" and d["ruledOn"] == "2026-10-17" and s0.get("ruling") == "yes"
          and s0.get("ruledOn") == "2026-10-16" and not validate(f))
    expect("--supersede replaces it and keeps the old ruling (control)", errs or ([] if ok else [str(d)]), False)
    expect("decide with a date that is not one", cmd_decide(_fixture(), "PR-D01", "yes", "Sam", "not-a-date"), True,
           "not a date")
    expect("decide with a date in the future", cmd_decide(_fixture(), "PR-D01", "yes", "Sam", "2999-01-01"), True,
           "future")
    f = _decided(_fixture()); index(f)[2]["PR-D01"]["ruledOn"] = "yesterday"
    expect("a ruledOn that is not a date", validate(f), True, "not a date")

    print("fields and waves the tool does not know:")
    f = _fixture(); f["tasks"][1]["dependOn"] = f["tasks"][1].pop("dependsOn")
    expect("a task with a misspelled field (dependOn)", validate(f), True, "unknown field 'dependOn'")
    f = _fixture(); f["tasks"][1]["wave"] = 99
    expect("a task in a wave the waves table does not name", validate(f), True, "not in meta.json's waves table")
    f = _fixture(); f["tasks"][1]["wave"] = "2"
    expect("a wave written as text", validate(f), True, "whole number")
    f = _fixture(); f["decisions"][0]["rulng"] = "x"
    expect("a decision with a misspelled field", validate(f), True, "unknown field 'rulng'")
    f = _fixture(); f["epics"][0]["gaol"] = "x"
    expect("an epic with a misspelled field", validate(f), True, "unknown field 'gaol'")
    f = _fixture(); f["tasks"][1].update(needsSam="plays it", notes="n")
    expect("the owner's needs field and notes are known fields (control)", validate(f), False)

    print("edit-task:")
    f = _fixture()
    errs = cmd_edit_task(f, "PR-E01.02", "it belongs with the second slice", {"wave": 2, "title": "b2"})
    t = index(f)[1]["PR-E01.02"]
    ok = (not errs and t["wave"] == 2 and t["title"] == "b2" and t["edits"][0]["changed"]["wave"] == [1, 2]
          and not validate(f))
    expect("edit-task changes fields, recording the change and why (control)", errs or ([] if ok else [str(t)]), False)
    expect("edit-task to a wave the table lacks",
           cmd_edit_task(_fixture(), "PR-E01.02", "it belongs with the second slice", {"wave": 99}), True, "waves table")
    expect("edit-task with too short a reason", cmd_edit_task(_fixture(), "PR-E01.02", "why", {"wave": 2}), True,
           "--reason")
    expect("edit-task on a field it does not edit",
           cmd_edit_task(_fixture(), "PR-E01.02", "it belongs with the second slice", {"status": "done"}), True,
           "changes only")
    f = _fixture(); index(f)[1]["PR-E01.01"].update(status="done", evidence="ran-it: x")
    expect("edit-task on a finished task",
           cmd_edit_task(f, "PR-E01.01", "it belongs with the second slice", {"title": "z"}), True, "finished task")
    f = _fixture()
    errs = cmd_edit_task(f, "PR-E01.02", "the dependency was filed by mistake", {"dependsOn": []})
    ok = not errs and "dependsOn" not in index(f)[1]["PR-E01.02"] and not validate(f)
    expect("edit-task --depends-on none clears the list (control)", errs or ([] if ok else ["still there"]), False)

    print("filing: titles, ids past 99, plans:")
    spec = {"title": "A", "owner": "Engineering", "size": "S", "wave": 1, "files": ["Source/N.cpp"], "acceptance": acc}
    expect("add-task with the title an open task carries (case and spacing aside)",
           cmd_add_task(_fixture(), "PR-E01", dict(spec))[0], True, "already carries the title")
    expect("the same with --same-title-ok (control)", cmd_add_task(_fixture(), "PR-E01", dict(spec), True)[0], False)
    f = _fixture(); index(f)[1]["PR-E01.01"].update(status="done", evidence="ran-it: x")
    expect("a finished task's title can be used again (control)", cmd_add_task(f, "PR-E01", dict(spec))[0], False)
    f = _fixture()
    f["tasks"] += [task(f"PR-E01.{n:02d}", f"t{n}") for n in range(4, 100)]
    errs, ident = cmd_add_task(f, "PR-E01", {**spec, "title": "the hundredth"})
    ok = (not errs and ident == "PR-E01.100" and not validate(f) and subject_id("PR-E01.100 work") == "PR-E01.100"
          and RE_ANY_ID.findall("see PR-E01.100.") == ["PR-E01.100"]
          and RE_FINDING_REF.fullmatch("PR-E01.100#2") is not None)
    expect("an epic past 99 tasks takes PR-E01.100, and every rule reads it (control)",
           errs or ([] if ok else [f"ident={ident} {validate(f)[:3]}"]), False)
    f["tasks"].append(task("PR-E01.999", "t999"))
    expect("an epic holding task 999 is full", cmd_add_task(f, "PR-E01", {**spec, "title": "one more"})[0], True,
           "is full")
    expect("a three-digit number with a leading zero is no task id (control)",
           [] if not RE_TASK.match("PR-E01.010") and subject_id("PR-E01.010 x") is None else ["matched"], False)
    with tempfile.TemporaryDirectory() as tmp:
        f = _fixture()
        for n in (2, 10, 100, 11):
            f["epics"].append({"id": f"PR-E{n:02d}", "title": f"epic {n}", "status": "planned", "plan": "p.md"})
            f["tasks"].append(task(f"PR-E{n:02d}.01", f"first of {n}"))
        save(f, Path(tmp))
        order = [e["id"] for e in load(Path(tmp), track=False)["epics"]]
        ok = order == ["PR-E01", "PR-E02", "PR-E10", "PR-E11", "PR-E100"] and next_epic_id(f) == "PR-E101"
        expect("epic files load in number order, E100 after E11 (control)", [] if ok else [f"order={order}"], False)
    plan = {"waves": {"3": "Save and load"},
            "epics": [{"key": "save", "title": "Save and load", "goal": "progress survives a restart",
                       "plan": "Docs/save.md",
                       "tasks": [{"key": "fmt", "title": "save format", "owner": "Engineering", "size": "S", "wave": 3,
                                  "files": ["Source/Save.cpp"], "acceptance": acc},
                                 {"title": "load it back", "owner": "Engineering", "size": "M", "wave": 3,
                                  "files": ["Source/Load.cpp"], "acceptance": acc, "dependsOn": ["@fmt", "@ask"]}]}],
            "tasks": [{"epic": "PR-E01", "title": "hook it up", "owner": "Engineering", "size": "S", "wave": 3,
                       "files": ["Source/Hook.cpp"], "acceptance": acc, "dependsOn": ["@fmt"]}],
            "decisions": [{"key": "ask", "epic": "@save", "question": "Autosave?", "recommendation": "yes",
                           "options": [{"label": "Yes", "detail": "every minute", "recommended": True},
                                       {"label": "No", "detail": "by hand only"}]}]}
    f = _fixture()
    errs, made = cmd_add_plan(f, copy.deepcopy(plan))
    _, tasks, decisions = index(f)
    ok = (not errs and [i for _, i in made] == ["PR-E02", "PR-D02", "PR-E02.01", "PR-E02.02", "PR-E01.04"]
          and tasks["PR-E02.02"]["dependsOn"] == ["PR-E02.01", "PR-D02"] and decisions["PR-D02"]["epic"] == "PR-E02"
          and f["waves"]["3"] == "Save and load" and not validate(f))
    expect("add-plan files a wave, an epic with tasks, a task and a decision, keys resolved (control)",
           errs or ([] if ok else [f"made={made} {validate(f)[:3]}"]), False)
    f = _fixture(); before = copy.deepcopy(f)
    bad = copy.deepcopy(plan); bad["tasks"][0]["dependsOn"] = ["@nothing"]
    expect("a plan naming a key it does not hold", cmd_add_plan(f, bad)[0], True, "names no key")
    expect("a refused plan writes nothing (control)", [] if f == before else ["the ledger changed"], False)
    bad = copy.deepcopy(plan); bad["epics"][0]["title"] = "E"  # the fixture's open epic is titled "e"
    expect("a plan filing an epic whose title an open epic carries", cmd_add_plan(_fixture(), bad)[0], True,
           "already carries the title")
    bad = copy.deepcopy(plan); bad["waves"] = {"1": "another name"}
    expect("a plan renaming a wave", cmd_add_plan(_fixture(), bad)[0], True, "does not rename")
    bad = copy.deepcopy(plan); bad["epics"][0]["tasks"][0]["wave"] = 7
    expect("a plan with a task in a wave nobody named", cmd_add_plan(_fixture(), bad)[0], True, "waves table")
    bad = copy.deepcopy(plan); bad["epics"][0]["tasks"] = []
    expect("a plan filing an epic with no task", cmd_add_plan(_fixture(), bad)[0], True, "at least one task")
    expect("a plan with a key the format does not have", cmd_add_plan(_fixture(), {"epic": []})[0], True, "unknown key")

    print("writes when another program holds a file open:")
    saved_replace, saved_retry, saved_md = _os_replace, REPLACE_RETRY_S, LEDGER_MD

    def failing(times: int, only: str = ""):
        n = {"calls": 0}

        def rep(a, b):
            n["calls"] += 1
            if (not only or only in str(b)) and n["calls"] <= times:
                raise PermissionError(13, "Access is denied")
            saved_replace(a, b)
        return rep

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        globals()["REPLACE_RETRY_S"] = 0.3
        try:
            target = root / "x.txt"
            target.write_text("old", encoding="utf-8")
            globals()["_os_replace"] = failing(2)
            _write_atomic(target, "new")
            ok = target.read_text(encoding="utf-8") == "new" and not list(root.glob("*.tmp"))
            expect("a write retries while the file is held open, then lands (control)", [] if ok else ["not written"],
                   False)
            globals()["_os_replace"] = failing(10 ** 6)
            try:
                _write_atomic(target, "newer")
                errs = []
            except PermissionError as e:
                errs = [str(e)]
            ok = target.read_text(encoding="utf-8") == "new" and not list(root.glob("*.tmp"))
            expect("a write that never lands fails, leaving the file as it was and no temporary file",
                   errs if ok else ["a .tmp file was left, or the file changed"], True, "denied")
            f = _fixture()
            f["epics"].append({"id": "PR-E02", "title": "two", "status": "planned", "plan": "q.md"})
            f["tasks"].append(task("PR-E02.01", "z"))
            globals()["_os_replace"] = saved_replace
            save(f, root / "led")
            g = load(root / "led")
            index(g)[1]["PR-E01.01"]["notes"] = "one"
            index(g)[1]["PR-E02.01"]["notes"] = "two"
            before = {p.name: p.read_text(encoding="utf-8") for p in (root / "led" / "epics").glob("*.json")}
            globals()["_os_replace"] = failing(10 ** 6, only="PR-E02")
            try:
                save(g)
                errs = []
            except LedgerError as e:
                errs = [str(e)]
            after = {p.name: p.read_text(encoding="utf-8") for p in (root / "led" / "epics").glob("*.json")}
            ok = after == before and not list((root / "led").rglob("*.tmp"))
            expect("a save whose second file cannot be written puts the first back, so nothing lands half",
                   errs if ok else ["a file changed, or a .tmp file was left"], True, "Nothing was saved")
            globals()["_os_replace"] = failing(10 ** 6)
            globals()["LEDGER_MD"] = root / "LEDGER.md"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                wrote = write_render(_fixture())
            ok = wrote is False and "the change is saved" in err.getvalue() and "ledger.py render" in err.getvalue()
            expect("LEDGER.md held open: the command says it saved and LEDGER.md is behind, no traceback (control)",
                   [] if ok else [err.getvalue()], False)
            try:
                write_render(_fixture(), required=True)
                errs = []
            except LedgerError as e:
                errs = [str(e)]
            expect("render itself refuses when it cannot write LEDGER.md", errs, True, "could not be written")
            if os.name == "nt":
                # The real thing: Windows will not replace a file another handle holds open.
                globals()["_os_replace"] = saved_replace
                held = open(target, encoding="utf-8")
                timer = threading.Timer(0.15, held.close)
                timer.start()
                try:
                    _write_atomic(target, "while held")
                    errs = []
                except OSError as e:
                    errs = [str(e)]
                timer.join()
                ok = target.read_text(encoding="utf-8") == "while held"
                expect("Windows: a file another handle holds open is written once the handle lets go (control)",
                       errs or ([] if ok else ["not written"]), False)
        finally:
            globals()["_os_replace"], globals()["REPLACE_RETRY_S"], globals()["LEDGER_MD"] = (saved_replace, saved_retry,
                                                                                              saved_md)

    print("text encoding and ordinary slips:")
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "msg.txt"
        m.write_bytes("\ufeffPR-E01.01 work\n".encode("utf-8"))
        msg = read_text_file(str(m), "--message")
        expect("a message file starting with a byte order mark still names its task (control)",
               [] if subject_id(msg) == "PR-E01.01" else [repr(msg[:20])], False)
        j = Path(tmp) / "task.json"
        j.write_bytes("\ufeff{\"title\": \"a \u2192 b\"}".encode("utf-8"))
        expect("a task file with a byte order mark and an arrow reads (control)",
               [] if read_json_file(str(j), "--from") == {"title": "a \u2192 b"} else ["not read"], False)
        for name, setup, needle in (("does not exist", None, "does not exist"), ("is not JSON", "{ nope", "not valid JSON")):
            p = Path(tmp) / "spec.json"
            if setup:
                p.write_text(setup, encoding="utf-8")
            try:
                read_json_file(str(p), "--from")
                errs = []
            except LedgerError as e:
                errs = [str(e)]
            expect(f"a --from file that {name} is one refusal, not a traceback", errs, True, needle)
    code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import ledger; sys.stdout.reconfigure(encoding='cp1252'); "
            f"ledger.main(['version']); print('\\u2192 arrow')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True)
    ok = r.returncode == 0 and "\u2192 arrow".encode("utf-8") in r.stdout
    expect("output through a pipe is UTF-8 whatever the code page, so an arrow prints (control)",
           [] if ok else [r.stderr.decode("utf-8", "replace")[-300:]], False)

    print("dispatch reads the working copy, and says so:")
    saved_agents = AGENTS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        globals()["AGENTS_DIR"] = Path(tmp)
        (Path(tmp) / "security-department.md").write_text("---\nname: security-department\n---\n", encoding="utf-8")

        def review() -> dict:
            f = _fixture()
            f["departments"][1]["agent"] = "security-department"
            f["tasks"][0].update(files=["Source/A.cpp"], status="done", evidence="ran-it: x", landed="dv.commit.11")
            f["tasks"].append(dict(task("PR-E01.04", "review a", 1), owner="Security Department", kind="review",
                                   parent="PR-E01.01", files=[]))
            return f
        try:
            globals()["UNCOMMITTED"] = lambda files: ["Source/A.cpp"]
            expect("dispatch a review whose covered files have uncommitted changes",
                   cmd_dispatch(review(), "PR-E01.04", "Ledger Work", None, False)[0], True, "uncommitted changes")
            f = review()
            errs, out = cmd_dispatch(f, "PR-E01.04", "Ledger Work", None, False,
                                     dirty_ok="another session is mid-edit on A; review it as it stands", session=S1)
            t = index(f)[1]["PR-E01.04"]
            ok = (not errs and "uncommitted changes" in out["prompt"] and "Source/A.cpp" in out["prompt"]
                  and t.get("dispatchedSession") == S1 and "Dispatched with uncommitted changes" in t.get("notes", "")
                  and not validate(f))
            expect("--dirty-ok sends it, the prompt names the files and a note says why (control)",
                   errs or ([] if ok else [f"task={t}"]), False)

            def cannot_ask(files):
                raise RuntimeError("dv diff failed")
            globals()["UNCOMMITTED"] = cannot_ask
            expect("dispatch when the version control cannot be asked",
                   cmd_dispatch(review(), "PR-E01.04", "Ledger Work", None, False)[0], True, "could not ask")
            globals()["UNCOMMITTED"] = lambda files: []
            errs, out = cmd_dispatch(review(), "PR-E01.04", "Ledger Work", None, False)
            ok = not errs and "as they stand in the working copy" in out["prompt"] and "is what landed" in out["prompt"]
            expect("a clean review's prompt says the working copy is what landed (control)",
                   errs or ([] if ok else [out["prompt"][:300]]), False)
        finally:
            globals()["AGENTS_DIR"] = saved_agents
            globals()["UNCOMMITTED"] = lambda files: []


def _load_hook(name: str):
    """A hook module from .claude/hooks/, or None in a project that does not have it."""
    import importlib.util
    path = REPO / ".claude" / "hooks" / f"{name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"_ledger_hook_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _selftest_hooks(expect) -> None:
    """The hooks read transcript fields Claude Code does not document (turnOrigin, queue-operation, queued_command);
    if an update renamed one, they would go quiet with no error. These run them on lines shaped like the transcripts
    Claude Code 2.1.3xx writes, and on the commands the fence and the Stop hook must and must not match."""
    import tempfile
    stop, fence, msgs = _load_hook("ledger_stop"), _load_hook("department_fence"), _load_hook("owner_message_check")
    if not (stop and fence and msgs):
        print("hooks: .claude/hooks/ lacks ledger_stop.py, department_fence.py or owner_message_check.py; not checked")
        return
    S1 = "11111111-aaaa-bbbb-cccc-000000000001"
    t0 = datetime(2026, 11, 9, 10, 0, 0)

    def at(seconds: int) -> str:
        return (t0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def user(text: str, s: int, origin: str | None = "human") -> dict:
        e = {"type": "user", "isSidechain": False, "timestamp": at(s), "message": {"role": "user", "content": text}}
        if origin:
            e["turnOrigin"] = origin
        return e

    def bash(command: str, s: int) -> dict:
        return {"type": "assistant", "isSidechain": False, "timestamp": at(s),
                "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
                                                              "input": {"command": command}}]}}

    def said(text: str, s: int) -> dict:
        return {"type": "assistant", "isSidechain": False, "timestamp": at(s),
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    def queued(text: str, kind: str, s: int) -> dict:
        return {"type": "attachment", "isSidechain": False, "timestamp": at(s),
                "attachment": {"type": "queued_command", "prompt": text, "origin": {"kind": kind}}}

    def enqueue(text: str, s: int) -> dict:
        return {"type": "queue-operation", "operation": "enqueue", "timestamp": at(s), "content": text}

    with tempfile.TemporaryDirectory() as tmp:
        def transcript(*entries) -> Path:
            p = Path(tmp) / f"t{uuid.uuid4().hex[:6]}.jsonl"
            p.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries), encoding="utf-8")
            return p

        print("hooks: the Stop hook:")
        tr = transcript(user("Do the next task", 0), bash("ls", 5), queued("is the build done?", "human", 9),
                        queued("<task-notification>x</task-notification>", "task-notification", 10))
        origin, start, mid, missing = stop.this_reply(tr)
        ok = origin == "human" and start == "Do the next task" and mid == ["is the build done?"] and not missing
        expect("reads who started the reply and what the owner wrote mid-reply (control)",
               [] if ok else [f"{origin} {start!r} {mid} {missing}"], False)
        missing = stop.this_reply(transcript(user("Do the next task", 0, origin=None), bash("ls", 5)))[3]
        expect("a transcript with user turns and no turnOrigin is flagged (Claude Code renamed it)",
               ["flagged"] if missing else [], True, "flagged")
        for text, promise in (("Done. I\u2019ll fix the bridge next.", True), ("I'll run the build now.", True),
                              ("If you want, I'll add a test for it.", False), ("Want me to add one?", False),
                              ("The fix is in and the test passes.", False)):
            got = stop.promised_work(transcript(user("go", 0), said(text, 5)))
            expect(f"promise check on {text!r} ({'a promise' if promise else 'no promise'}) (control)",
                   [] if bool(got) == promise else [f"got {got!r}"], False)
        tr = transcript(user("go", 0),
                        bash("uv run Docs/Ledger/ledger.py start PR-E01.01", 5),
                        bash("./Docs/Ledger/ledger.py start PR-E01.02 --by x", 6),
                        bash('"C:/Program Files/Python/python.exe" "D:/my proj/Docs/Ledger/ledger.py" start PR-E01.100', 7),
                        bash("cd x && X=1 python Docs/Ledger/ledger.py dispatch PR-E01.04 --by hub", 8),
                        bash('grep -n "ledger.py start PR-E01.05" README.md', 9),
                        bash('echo "then ledger.py start PR-E01.06"', 10))
        got = sorted(task for _, task, _ in stop.ledger_commands(tr))
        ok = got == ["PR-E01.01", "PR-E01.02", "PR-E01.04", "PR-E01.100"]
        expect("finds start and dispatch however python is spelled, never in a grep or an echo (control)",
               [] if ok else [f"got {got}"], False)
        f = _fixture()
        index(f)[1]["PR-E01.02"].update(session=S1)
        ok = stop.session_tasks(f, S1) == ["PR-E01.02"] and stop.session_tasks(f, "someone-else") == []
        expect("knows the tasks this session started from the session id start recorded (control)",
               [] if ok else ["no"], False)
        saved_answers = ANSWERS_JSON
        globals()["ANSWERS_JSON"] = Path(tmp) / "answers.json"
        try:
            L = sys.modules[__name__]
            lines = stop.hub_work(L, _fixture(), set())
            ok = any(x.startswith("CONTINUE  PR-E01.01") and "nobody's" in x for x in lines)
            expect("lists a stale in-progress task nobody holds (control)", [] if ok else [str(lines)], False)
            f = _fixture(); index(f)[1]["PR-E01.01"].update(heldBy="session 22222222", session="2222")
            lines = stop.hub_work(L, f, set())
            expect("does not push a session onto a task another session holds",
                   [x for x in lines if "PR-E01.01" in x] or ["not listed"], True, "not listed")
        finally:
            globals()["ANSWERS_JSON"] = saved_answers

        print("hooks: the owner's mid-reply messages:")
        entries = [enqueue("Do the next task", 0), user("Do the next task", 1), enqueue("is the build done?", 60),
                   enqueue("<task-notification>x</task-notification>", 61)]
        now = (t0 + timedelta(seconds=120)).replace(tzinfo=timezone.utc)
        got = msgs.unanswered([json.loads(json.dumps(e)) for e in entries], now)
        ok = [t for _, t in got] == ["is the build done?"]
        expect("flags a message sent mid-reply, not the one that started it nor a notification (control)",
               [] if ok else [f"got {got}"], False)

        print("hooks: the department fence:")
        hub = ["uv run Docs/Ledger/ledger.py confirm PR-E01.04 --by x --evidence y",
               "X=1 python Docs/Ledger/ledger.py done PR-E01.01 --evidence ran-it",
               "env python Docs/Ledger/ledger.py commit --message m Source/A.cpp",
               "./Docs/Ledger/ledger.py route PR-E01.04#1 --to PR-E01.02",
               "cd /d/proj && \"C:/Python 3/python.exe\" Docs/Ledger/ledger.py note PR-E01.04 --text x",
               "git -C . commit -m x", "git -c user.name=x commit -m y", "dv commit Source/A.cpp -m x",
               "echo {} > Docs/Ledger/meta.json", "cat x >> ./Docs/Ledger/epics/PR-E01.json",
               "python -c \"import importlib; importlib.import_module('ledger')\"",
               "python -c \"__import__('ledger').load()\"", "cd Docs/Ledger && python -c \"import ledger\"",
               "python - <<'EOF'\nfrom ledger import load\nEOF", "claude -p hello", "npx claude --help"]
        for command in hub:
            expect(f"fence refuses: {command.splitlines()[0][:60]}", [fence.refusal("Bash", {"command": command})]
                   if fence.refusal("Bash", {"command": command}) else [], True, "")
        allowed = ["grep -n \"ledger.py confirm\" Docs/Ledger/README.md", "echo \"then run ledger.py done\"",
                   "python Docs/Ledger/ledger.py show PR-E01.04",
                   "uv run Docs/Ledger/ledger.py finding PR-E01.04 --severity hardening --text t --evidence S/A.cpp:1",
                   "git log -n 3 --format=%s", "git show HEAD:Source/A.cpp", "cat Docs/Ledger/meta.json",
                   "dv show dv.commit.12 --name-status", "rg \"commit\" Source/", "python tools/check.py > out.txt"]
        for command in allowed:
            expect(f"fence allows: {command[:60]} (control)",
                   [fence.refusal("Bash", {"command": command})] if fence.refusal("Bash", {"command": command}) else [],
                   False)
        expect("fence refuses a PowerShell write into the ledger",
               [fence.refusal("PowerShell", {"command": "Set-Content Docs/Ledger/meta.json '{}'"}) or ""], True,
               "ledger")
        expect("fence refuses a Write tool into the ledger",
               [fence.refusal("Write", {"file_path": "D:/proj/Docs/Ledger/meta.json"}) or ""], True, "ledger")


# --------------------------------------------------------------------------- adding to the ledger

def stamp_now() -> str:
    """A note's stamp, from this machine's local clock, with the config's stampZone label after it when there is one
    ("2026-10-16 19:45 PT"). Readers take the first 10 or 16 characters, so the label never changes what they read."""
    return datetime.now().strftime("%Y-%m-%d %H:%M") + (f" {STAMP_ZONE}" if STAMP_ZONE else "")


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


def next_task_id(ledger: dict, epic: str) -> str | None:
    """The next free task id in an epic: two digits to 99, then three; None once the epic is full (ID_MAX)."""
    nums = [task_num(t["id"]) for t in ledger["tasks"] if t["id"].startswith(epic + ".")]
    n = max(nums) + 1 if nums else 1
    return f"{epic}.{n:02d}" if n <= ID_MAX else None


def next_epic_id(ledger: dict) -> str | None:
    nums = [epic_num(e["id"]) for e in ledger["epics"]]
    n = max(nums) + 1 if nums else 1
    return f"{PREFIX}-E{n:02d}" if n <= ID_MAX else None


def next_decision_id(ledger: dict) -> str:
    nums = [decision_num(d["id"]) for d in ledger["decisions"]]
    return f"{PREFIX}-D{(max(nums) + 1 if nums else 1):02d}"


def norm_title(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def same_title(ledger: dict, coll: str, title, skip: str | None = None) -> list[str]:
    """The open epics (coll 'epics') or open tasks ('tasks') that already carry this title, ignoring case and spacing.
    Two sessions filed the same epic four minutes apart; the second sat in the ledger, withdrawn, for good."""
    want = norm_title(title)
    return [x["id"] for x in ledger[coll] if x.get("id") != skip and x.get("status") not in FINISHED
            and want and norm_title(x.get("title")) == want]


def cmd_add_task(ledger: dict, epic: str, spec: dict, same_title_ok: bool = False) -> tuple[list[str], str | None]:
    """Add a task to an epic under the next free id. spec holds the task's fields; its id and epic are ignored."""
    if epic not in index(ledger)[0]:
        return [f"unknown epic {epic}"], None
    if not isinstance(spec, dict):
        return ["the task file holds one JSON object: the task's fields"], None
    ident = next_task_id(ledger, epic)
    if not ident:
        return [f"{epic} is full: it holds task {ID_MAX}. File the work under another open epic"], None
    dupes = [] if same_title_ok else same_title(ledger, "tasks", spec.get("title"))
    if dupes:
        return [f"an open task already carries the title '{spec.get('title')}': {', '.join(dupes)}. If this is "
                f"another task all the same, add it with --same-title-ok"], None
    t = {"id": ident, "epic": epic, **{k: v for k, v in spec.items() if k not in ("id", "epic")}}
    t.setdefault("status", "planned")
    trial = copy.deepcopy(ledger)
    trial["tasks"].append(t)
    errs = [e for e in validate(trial) if e.startswith(ident + ":") or e.startswith(epic + ":")]
    if errs:
        return errs, None
    ledger["tasks"].append(t)
    return [], ident


PLAN_KEYS = ("waves", "epics", "tasks", "decisions")


def cmd_add_plan(ledger: dict, plan: dict, same_title_ok: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """File a plan in one go: new wave names, epics with their tasks, tasks under existing epics, and decisions. All of
    it is checked together and written together, or nothing is. Returns (errors, [(where in the plan, new id)]).

    The largest change one ledger took (28 epics, 150 tasks, the wave names) went in through a script in a session's
    scratchpad, because no command filed in bulk. A plan is now a file to read:

        {"waves": {"12": "Save and load"},
         "epics": [{"key": "save", "title": "...", "goal": "...", "plan": "Docs/...",
                    "tasks": [{"key": "fmt", "title": "...", "owner": ..., "size": ..., "wave": 12, "files": [...],
                               "acceptance": {"check": ..., "expect": ...}}]}],
         "tasks": [{"epic": "PR-E03", "title": "...", "dependsOn": ["@fmt", "@ask"], ...}],
         "decisions": [{"key": "ask", "epic": "@save", "question": ..., "recommendation": ..., "options": [...]}]}

    A "key" names an item inside the plan, and "@key" in an epic, dependsOn or parent stands for the id it gets. Ids
    are the next free ones, assigned in the file's order."""
    if not isinstance(plan, dict):
        return ["a plan file holds one JSON object"], []
    unknown = [k for k in plan if k not in PLAN_KEYS and not str(k).startswith("_")]
    if unknown:
        return [f"unknown key(s) {unknown}: a plan holds {', '.join(PLAN_KEYS)} (a key starting with _ is a note)"], []
    trial = copy.deepcopy(ledger)
    errs: list[str] = []
    keyed: dict[str, str] = {}
    made: list[tuple[str, str]] = []

    def take_key(item: dict, where: str, ident: str) -> None:
        k = item.get("key")
        if k is None:
            return
        if not isinstance(k, str) or not k.strip() or k in keyed:
            errs.append(f"{where}: key {k!r} is empty or used twice in the plan")
        else:
            keyed[k] = ident

    def resolve(ref, where: str):
        if isinstance(ref, str) and ref.startswith("@"):
            if ref[1:] in keyed:
                return keyed[ref[1:]]
            errs.append(f"{where}: '{ref}' names no key in the plan")
        return ref

    def as_list(name: str) -> list:
        v = plan.get(name) or []
        if not isinstance(v, list):
            errs.append(f"'{name}' is a list")
            return []
        return v

    waves = plan.get("waves") or {}
    if not isinstance(waves, dict):
        errs.append("'waves' is an object: {\"<number>\": \"<what the wave is>\"}")
        waves = {}
    table = trial.setdefault("waves", {})
    for w, name in waves.items():
        if not re.fullmatch(r"\d+", str(w)) or not str(name or "").strip():
            errs.append(f"waves: '{w}' needs a whole number and a name")
        elif str(w) in table and table[str(w)] != name:
            errs.append(f"waves: wave {w} is already '{table[str(w)]}'; a plan adds waves, it does not rename them")
        else:
            table[str(w)] = name
    trial["waves"] = dict(sorted(table.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0))

    pending: list[tuple[str, object, object]] = []
    for n, e in enumerate(as_list("epics")):
        where = f"epics[{n}]"
        if not isinstance(e, dict):
            errs.append(f"{where}: an epic is an object")
            continue
        eid = next_epic_id(trial)
        if not eid:
            errs.append(f"{where}: the ledger holds epic {ID_MAX}; no epic id is left")
            break
        take_key(e, where, eid)
        bad = [k for k in e if k not in (EPIC_FIELDS - {"id", "status"}) | {"key", "tasks"}]
        if bad:
            errs.append(f"{where}: unknown field(s) {bad} (an epic takes title, goal, plan, key and tasks)")
        dupes = [] if same_title_ok else same_title(trial, "epics", e.get("title"))
        if dupes:
            errs.append(f"{where}: an open epic already carries the title '{e.get('title')}': {', '.join(dupes)} "
                        f"(--same-title-ok if it is another epic all the same)")
        trial["epics"].append({"id": eid, "title": e.get("title"), "status": "planned", "plan": e.get("plan"),
                               **({"goal": e["goal"]} if e.get("goal") else {})})
        made.append((e.get("key") or where, eid))
        tasks_in = e.get("tasks") or []
        if not isinstance(tasks_in, list) or not tasks_in:
            errs.append(f"{where}: an epic is filed with at least one task ('tasks')")
            tasks_in = []
        pending += [(f"{where}.tasks[{m}]", eid, t) for m, t in enumerate(tasks_in)]
    pending += [(f"tasks[{n}]", t.get("epic") if isinstance(t, dict) else None, t)
                for n, t in enumerate(as_list("tasks"))]

    new_decisions = []
    for n, d in enumerate(as_list("decisions")):
        where = f"decisions[{n}]"
        if not isinstance(d, dict):
            errs.append(f"{where}: a decision is an object")
            continue
        did = next_decision_id(trial)
        take_key(d, where, did)
        dd = {"id": did, **{k: v for k, v in d.items() if k not in ("id", "key", "epic")}, "epic": d.get("epic")}
        dd.setdefault("status", "open")
        trial["decisions"].append(dd)
        new_decisions.append((where, dd))
        made.append((d.get("key") or where, did))

    new_tasks = []
    for where, epic_ref, t in pending:
        if not isinstance(t, dict):
            errs.append(f"{where}: a task is an object")
            continue
        epic = resolve(epic_ref, where)
        if epic not in index(trial)[0]:
            errs.append(f"{where}: epic '{epic_ref}' is no epic in the ledger or the plan")
            continue
        tid = next_task_id(trial, epic)
        if not tid:
            errs.append(f"{where}: {epic} is full (it holds task {ID_MAX})")
            continue
        take_key(t, where, tid)
        dupes = [] if same_title_ok else same_title(trial, "tasks", t.get("title"))
        if dupes:
            errs.append(f"{where}: an open task already carries the title '{t.get('title')}': {', '.join(dupes)} "
                        f"(--same-title-ok if it is another task all the same)")
        task = {"id": tid, "epic": epic, **{k: v for k, v in t.items() if k not in ("id", "epic", "key")}}
        task.setdefault("status", "planned")
        trial["tasks"].append(task)
        new_tasks.append((where, task))
        made.append((t.get("key") or where, tid))

    for where, dd in new_decisions:
        dd["epic"] = resolve(dd["epic"], where)
    for where, task in new_tasks:
        if isinstance(task.get("dependsOn"), list):
            task["dependsOn"] = [resolve(x, where) for x in task["dependsOn"]]
        if task.get("parent"):
            task["parent"] = resolve(task["parent"], where)
    if not made and not waves:
        errs.append("the plan files nothing: it has no waves, epics, tasks or decisions")
    if errs:
        return errs, []
    try:
        split(trial)  # a decision whose epic does not exist cannot be filed
    except LedgerError as e:
        return [str(e)], []
    new_ids = {i for _, i in made}
    errs = [e for e in validate(trial) if e.split(":")[0].split("#")[0] in new_ids or e.startswith("waves")]
    if errs:
        return errs, []
    ledger.clear()
    ledger.update(trial)
    return [], made


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

MUTATING = {"start", "done", "block", "withdraw", "note", "add-task", "add-decision", "add-plan", "edit-task", "decide",
            "apply", "render", "commit", "dispatch", "submit", "confirm", "finding", "route"}


def read_text_file(path: str, what: str) -> str:
    """A file the caller named, read as UTF-8 with any byte order mark dropped (Windows PowerShell 5.1 writes one, and
    the mark hid the task id at the start of a commit subject)."""
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise LedgerError(f"{what} {path} does not exist")
    except UnicodeDecodeError as e:
        raise LedgerError(f"{what} {path} is not UTF-8 text ({e})")


def read_json_file(path: str, what: str):
    try:
        return json.loads(read_text_file(path, what))
    except json.JSONDecodeError as e:
        raise LedgerError(f"{what} {path} is not valid JSON: {e}")


def main(argv: list[str]) -> int:
    # Python's output through a pipe is the code page on Windows (1252), and one arrow in a task title ended `next`
    # in UnicodeEncodeError - after `dispatch` had already saved. Write UTF-8 whatever the console.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Task ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next"); sub.add_parser("status"); sub.add_parser("validate")
    sub.add_parser("audit"); sub.add_parser("check"); sub.add_parser("selftest"); sub.add_parser("version")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("start"); p.add_argument("task")
    p.add_argument("--by", help="who holds it (default: this Claude Code session); required on a department subtask "
                                "(\"<department> agent\")")
    p.add_argument("--takeover", help="the task is in progress under another holder whose session is gone: why")
    p.add_argument("--reopen", help="the task is done or withdrawn and must be worked again: why (its evidence and "
                                    "landed commit move to its history)")
    p.add_argument("--files", nargs="+", help="narrow the task's files list to these (each inside an entry it has)")
    p = sub.add_parser("done"); p.add_argument("task")
    p.add_argument("--evidence", required=True, help=f"starts with {', '.join(EVIDENCE_LABELS)}: then what showed")
    p = sub.add_parser("edit-task", help="change an unfinished task's title, wave, dependencies, files or acceptance")
    p.add_argument("task"); p.add_argument("--reason", required=True, help="why it changes (recorded in its edits)")
    p.add_argument("--title"); p.add_argument("--wave", type=int)
    p.add_argument("--depends-on", nargs="+", help="the whole new dependsOn list, or 'none'")
    p.add_argument("--files", nargs="+", help="the whole new files list")
    p.add_argument("--check", help="the acceptance check"); p.add_argument("--expect", help="what passing shows")
    p = sub.add_parser("dispatch", help="hub: send a department subtask out; prints the agent and its prompt")
    p.add_argument("task"); p.add_argument("--by", help="the hub session's title")
    p.add_argument("--dump", help="the graph dump folder, for a review that covers Blueprints")
    p.add_argument("--restart", action="store_true", help="the subtask's agent is gone: start a new one on it")
    p.add_argument("--dirty-ok", help="covered files have uncommitted changes and the review goes out anyway: why")
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
    p.add_argument("--same-title-ok", action="store_true", help="an open task has this title, and this is another")
    p = sub.add_parser("add-decision"); p.add_argument("--epic", required=True)
    p.add_argument("--from", dest="spec", required=True, help="a JSON file: question, recommendation, options, source")
    p = sub.add_parser("add-plan", help="file waves, epics, tasks and decisions from one JSON file, all or nothing")
    p.add_argument("--from", dest="spec", required=True, help="the plan file (README, 'Filing a plan')")
    p.add_argument("--same-title-ok", action="store_true", help="file items whose titles open ones already carry")
    p.add_argument("--dry-run", action="store_true", help="check it and print the ids it would take; write nothing")
    p = sub.add_parser("decide"); p.add_argument("decision"); p.add_argument("--ruling", required=True)
    p.add_argument("--by", required=True, help=f"who ruled - {OWNER}, for a ruling given in chat")
    p.add_argument("--date", required=True, help="the day of the ruling, YYYY-MM-DD")
    p.add_argument("--supersede", help="the decision is already ruled and this ruling replaces it: why")
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
    if a.cmd == "version":
        print(f"ledger.py {VERSION}")
        return 0
    writes = a.cmd in MUTATING and not (a.cmd == "render" and a.check) and not getattr(a, "dry_run", False)
    # Ordinary slips (a missing --message file, bad JSON in --from, a version control call that failed) ended in a
    # traceback; each is one REFUSED line.
    try:
        with (ledger_lock() if writes else contextlib.nullcontext()):
            return _run(a)
    except LedgerError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, RuntimeError) as e:
        print(f"REFUSED: {type(e).__name__}: {e}", file=sys.stderr)
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
                write_render(ledger, required=True)
                print(f"wrote {LEDGER_MD_REL}")
    elif a.cmd == "audit":
        errs = cmd_audit(ledger)
    elif a.cmd == "check":
        print(f"ledger.py {VERSION}")
        errs = validate(ledger) + render_check(ledger)
        if not errs:
            errs = cmd_audit(ledger)
    elif a.cmd == "start":
        errs, lines = cmd_start(ledger, a.task, a.by, current_session(), a.takeover, a.reopen, a.files)
        if not errs:
            t = index(ledger)[1][a.task]
            print(f"{a.task} -> in-progress, {holder_text(t)}; {persist(ledger)} (commit it with the work)")
            for line in lines:
                print(line)
    elif a.cmd in ("done", "block"):
        status = {"done": "done", "block": "blocked"}[a.cmd]
        extra = {"evidence": getattr(a, "evidence", None), "blockedOn": getattr(a, "on", None)}
        if a.cmd == "block":
            errs = block_errors(ledger, a.task, a.on, a.until, extra)
        else:
            errs = done_errors(ledger, a.task, a.evidence)
        errs = errs or set_status(ledger, a.task, status, **extra)
        if not errs:
            print(f"{a.task} -> {status}; {persist(ledger)} (commit it with the work)")
    elif a.cmd == "edit-task":
        changes: dict = {}
        if a.title is not None:
            changes["title"] = a.title
        if a.wave is not None:
            changes["wave"] = a.wave
        if a.depends_on is not None:
            changes["dependsOn"] = [] if [x.lower() for x in a.depends_on] == ["none"] else a.depends_on
        if a.files is not None:
            changes["files"] = [norm(f) for f in a.files]
        if a.check is not None or a.expect is not None:
            acc = dict(index(ledger)[1].get(a.task, {}).get("acceptance") or {})
            acc.update({k: v for k, v in (("check", a.check), ("expect", a.expect)) if v is not None})
            changes["acceptance"] = acc
        sid = current_session()
        errs = cmd_edit_task(ledger, a.task, a.reason, changes, f"session {sid[:8]}" if sid else None)
        if not errs:
            print(f"{a.task} edited ({', '.join(changes)}); {persist(ledger)}. Commit it --role Ledger")
    elif a.cmd == "withdraw":
        errs = cmd_withdraw(ledger, a.task, a.reason)
        if not errs:
            print(f"{a.task} -> withdrawn; {persist(ledger)} (commit it --role Ledger)")
    elif a.cmd == "note":
        errs = cmd_note(ledger, a.task, a.text)
        if not errs:
            print(f"{a.task}: note added; {persist(ledger)}")
    elif a.cmd in ("add-task", "add-decision"):
        spec = read_json_file(a.spec, "--from")
        if a.cmd == "add-task":
            errs, ident = cmd_add_task(ledger, a.epic, spec, a.same_title_ok)
        else:
            errs, ident = cmd_add_decision(ledger, a.epic, spec)
        if not errs:
            print(f"added {ident}; {persist(ledger)}. Commit it now: ledger.py commit --role Ledger "
                  f"--reason \"<why>\" --message <file> {epic_rel(a.epic)} {LEDGER_MD_REL} - with a subject like "
                  f"'Ledger: file {ident} ...', not one starting with the id (that names a task not yet started)")
    elif a.cmd == "add-plan":
        errs, made = cmd_add_plan(ledger, read_json_file(a.spec, "--from"), a.same_title_ok)
        if not errs:
            for where, ident in made:
                print(f"  {ident:<12} {where}")
            if a.dry_run:
                print(f"dry run: the plan passes; it would file the {len(made)} item(s) above. Nothing was written.")
                return 0
            print(f"filed {len(made)} item(s); {persist(ledger)}. Commit them now: ledger.py commit --role Ledger "
                  f"--reason \"<why>\" --message <file> Docs/Ledger/")
    elif a.cmd == "decide":
        errs = cmd_decide(ledger, a.decision, a.ruling, a.by, a.date, a.supersede)
        if not errs:
            print(f"{a.decision} decided; {persist(ledger)}")
    elif a.cmd == "apply":
        answers = load_answers()
        errs = cmd_apply(ledger, a.decision, answers)
        if not errs:
            said = persist(ledger)
            take_answer(a.decision)
            d = index(ledger)[2][a.decision]
            gated = [t["id"] for t in ledger["tasks"] if a.decision in (t.get("dependsOn") or [])]
            print(f"{a.decision} decided from {OWNER}'s website answer: {d['ruling']}")
            print(f"  {said}; commit {epic_rel(decision_epic(ledger, d))} (--role Ledger). "
                  f"It gates: {', '.join(gated) or 'no task'}")
    elif a.cmd == "dispatch":
        errs, out = cmd_dispatch(ledger, a.task, a.by, a.dump, a.restart, a.dirty_ok, current_session())
        if not errs:
            said = persist(ledger)
            t = index(ledger)[1][a.task]
            print(f"{a.task} dispatched by {t['dispatchedBy']}; covers {', '.join(t['covers']) or 'no other task'}; "
                  f"{said}")
            print(f"Start it now with the Agent tool: subagent_type \"{out['agent']}\", run_in_background "
                  f"{'true' if out['background'] else 'false (the hub waits: the agent may own the editor)'}, "
                  f"and this prompt, word for word:")
            print("----- prompt -----")
            print(out["prompt"])
            print("----- end of prompt -----")
    elif a.cmd == "submit":
        errs = cmd_submit(ledger, a.task, a.evidence)
        if not errs:
            print(f"{a.task} handed in; {persist(ledger)}. It stays in progress until the hub confirms it. Reply to "
                  f"the hub in one line.")
    elif a.cmd == "confirm":
        errs = cmd_confirm(ledger, a.task, a.by, a.evidence, a.new_hub)
        if not errs:
            said = persist(ledger)
            t = index(ledger)[1][a.task]
            print(f"{a.task} confirmed and done; {said}. Commit it now, with a subject starting '{a.task}': ledger.py "
                  f"commit --message <file> {epic_rel(t['epic'])} (the tool stamps landed)")
    elif a.cmd == "finding":
        errs, n = cmd_finding(ledger, a.task, a.severity, a.text, a.evidence)
        if not errs:
            print(f"recorded {a.task}#{n} ({a.severity}); {persist(ledger)}")
    elif a.cmd == "findings":
        rows = all_findings(ledger, a.unrouted, a.severity)
        for t, f in rows:
            print(f"{t['id']}#{f['n']}  {f['severity']:<9}  {routed_text(f)}  [{t['status']}]  {f['text']}  -- {f['evidence']}")
        print(f"{len(rows)} finding(s)")
        return 0
    elif a.cmd == "route":
        errs = cmd_route(ledger, a.finding, a.to, a.reject, a.by, a.new_hub)
        if not errs:
            print(f"{a.finding} routed; {persist(ledger)}. Commit the epic file (--role Ledger).")
    elif a.cmd == "commit":
        msg = read_text_file(a.message, "--message")
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
