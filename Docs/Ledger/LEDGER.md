# Project Ledger

> **Rendered from `Docs/Ledger/meta.json` and `Docs/Ledger/epics/*.json` by `python Docs/Ledger/ledger.py render`. Do not hand-edit: change the ledger with ledger.py.**

Progress: **0 of 3** tasks done. Current wave: **1**. Audit window starts after `git:000000000000`.

The task ledger for this project. Epic PR-E01 is an example to read and then replace with your own.

## Sequence

| Wave | What | Editor |
|---|---|---|
| 1 | Foundations: the first working slice and the decisions it needs |  |
| 2 | Review and hardening of wave 1 |  |

## Decisions

| ID | Question | Recommended | Status | Ruling |
|---|---|---|---|---|
| PR-D01 | Which email provider sends the password-reset emails? | A transactional email service with a free tier and a test mode, so reset emails can be tested without sending real mail. | open |  |

## Epics

### PR-E01 - Example: sign-in for a small web app

Status **planned**, 0 of 3 done. Plan: `Docs/Ledger/README.md`.

An example epic to show the shape of the ledger: tasks in waves, a dependency, an open decision with options, and a review subtask for a department agent. Read it, then withdraw or replace it.

| ID | Task | Wave | Size | Status | Depends on | Landed |
|---|---|---|---|---|---|---|
| PR-E01.01 | Add a sign-in endpoint with hashed passwords | 1 | M | planned | - | - |
| PR-E01.02 | Send password-reset emails | 1 | S | planned | PR-E01.01, PR-D01 | - |
| PR-E01.03 | Code review of the sign-in epic | 2 | S | planned | - | - |

## Task detail

### PR-E01.01 - Add a sign-in endpoint with hashed passwords

- **Epic:** PR-E01. **Wave:** 1. **Owner:** Engineering. **Size:** M. **Status:** planned.
- **Files it may edit:**
  - `src/auth/`
  - `tests/test_auth.py`
- **Check:** Run the test suite (tests/test_auth.py).
- **Passing shows:** A correct password signs in, a wrong one is refused with 401, and the stored hash never equals the password. Changing the hash check to always pass makes a test fail.

### PR-E01.02 - Send password-reset emails

- **Epic:** PR-E01. **Wave:** 1. **Owner:** Engineering. **Size:** S. **Status:** planned.
- **Needs Sam:** Creates the email provider account once PR-D01 is ruled.
- **Depends on:** PR-E01.01, PR-D01
- **Files it may edit:**
  - `src/auth/reset.py`
  - `src/email/`
  - `tests/test_reset.py`
- **Check:** Run tests/test_reset.py against the email provider's test mode.
- **Passing shows:** A reset request for a known address sends one email with a single-use link; an unknown address sends nothing and gets the same response.

### PR-E01.03 - Code review of the sign-in epic

- **Epic:** PR-E01. **Wave:** 2. **Owner:** Code Review Department. **Size:** S. **Status:** planned.
- **Department subtask:** review, parent PR-E01.
- **Files it may edit:**
- **Check:** Read the covered tasks' files at their landed commits for correctness, data loss and security.
- **Passing shows:** The evidence names every covered file with a verdict, clean or finding N (a clean resting on a guard names it); each problem found is a finding with a pointer.

## Gates

- **ledger:** python Docs/Ledger/ledger.py check. Run python Docs/Ledger/ledger.py selftest as well whenever ledger.py changes.

## Conventions

- **ids:** PR-Enn is an epic, PR-Enn.mm a task, PR-Dnn a decision. Stable; never renumbered or reused.
- **wave:** The execution order. `next` shows the lowest wave with unfinished work. A task filed during later work takes the wave its work belongs to, not the wave being worked when it was filed.
- **files:** The files a task may edit. An entry ending in / allows everything under it. A commit naming the task that touches anything else is refused, unless it is given --reason: then the paths are added to the task's files and recorded in its filesAdded with the reason and the date.
- **acceptance:** check: what to run or read. expect: what passing shows. Every check must be able to fail.
- **status:** planned | in-progress | blocked | done | withdrawn. A status is a claim; read the tree.
- **evidence:** Required for done. Label it ran-it, read-in-the-code or read-in-a-document, and say what was observed, not what was concluded.
- **landed:** The commit that closed a done task, stamped by ledger.py commit.
- **decisions:** The owner's calls. A task that depends on an open decision cannot start. `next` asks one at a time. An open decision carries options (2 to 4, each a label and a detail, one recommended) so the owner can answer it on the ledger website; the answer waits in Saved/Ledger/answers.json until a session runs `apply`, and `next` prints waiting answers first.
- **commits:** Ledger work commits only through python Docs/Ledger/ledger.py commit. Its subject starts with a task or epic id, or it names a role (Owner, Repair, Checkpoint, Ledger) and a reason.
- **outsideCommits:** A commit made outside the tool that touched an in-progress task's files is RECORDED here with a reason, never excused by moving auditFrom.
- **auditFrom:** Move only to a commit inside the window, after a green audit at the old mark, and say so in the commit.
- **ledgerFiles:** The ledger is Docs/Ledger/meta.json plus one Docs/Ledger/epics/PR-Enn.json per epic, holding the epic, its tasks and the decisions filed under it. Change it through ledger.py (start, done, block, note, add-task, add-decision, decide, apply), which writes only the files that changed and refuses to overwrite a file another session changed after it read it. See Docs/Ledger/README.md.
- **owner:** One name from meta.json 'departments', or the project owner. A task owned by a department other than the hub is a department subtask and carries 'kind'. The owner's part of another owner's task is its needs<Owner> field, in words.
- **kind:** review, measure or produce: a department subtask. The hub dispatches it (ledger.py dispatch prints the department agent's prompt); the agent starts it with --by, records findings and hands it in with submit; only the hub's confirm, by a different --by, marks it done.
- **findings:** Recorded on the subtask by the agent; the hub routes every finding to a task, or rejects it with a reason, before it confirms. blocker: data loss, corruption, a security hole or a crash on the priority path; its fixing task goes on the critical path now. hardening: everything else; its fixing task goes in the wave its work belongs to. Every finding points at something checkable (a repo path with :line, a log, a commit id).
