# CLAUDE.md - the ledger sections

Copy these sections into your project's `CLAUDE.md`, then change what is yours (the owner's name, your build and
test commands). Claude Code reads `CLAUDE.md` at the start of every session; this is where the workflow's rules live,
and the hooks in `.claude/hooks/` enforce the ones a rule alone kept failing.

---

## The ledger

`Docs/Ledger/` is the task ledger: epics, tasks, the owner's decisions, waves. `Docs/Ledger/README.md` is the
manual; read it before your first ledger command in a session.

```
python Docs/Ledger/ledger.py next                 # where the work is - run it at every task boundary
python Docs/Ledger/ledger.py show PR-E01.01       # one task, epic or decision in full
python Docs/Ledger/ledger.py start PR-E01.01
python Docs/Ledger/ledger.py note PR-E01.01 --text "what was found or decided"
python Docs/Ledger/ledger.py done PR-E01.01 --evidence "ran-it: ..."
python Docs/Ledger/ledger.py commit --message msg.txt --agent "Claude" <paths...>
python Docs/Ledger/ledger.py check                # the gate: validate, render --check, audit
```

- **Change the ledger only through `ledger.py`.** Never hand-edit the epic JSON, `meta.json` or `LEDGER.md`.
- **Every commit goes through `ledger.py commit`.** The subject starts with the task id, or the commit names a
  role (`--role Owner|Repair|Checkpoint|Ledger --reason "..."`). A file outside the task's `files` needs
  `--reason`. Commit an `add-task` or `add-decision` straight away with `--role Ledger`.
- **Evidence** for `done` is labelled `ran-it`, `read-in-the-code` or `read-in-a-document`, and says what was
  observed, not what was concluded. Every acceptance check must be able to fail.
- **Decisions are the owner's.** The owner answers open ones on the ledger website
  (`python Docs/Ledger/ledger_web.py`). `next` lists waiting answers first; `apply PR-Dnn` rules one, then commit
  it `--role Ledger`. An answer that is not a ruling ("undecided", or a question back) is not applied: ask.
- **Departments** are agents in `.claude/agents/` (Code Review, Game Design). This session is the hub: it files
  their subtasks, starts them with `dispatch`'s prompt word for word, and confirms and routes their findings. See
  the manual, "Department subtasks".
- **`start` records who holds a task** (this Claude Code session, or `--by`), and the stop hook knows this
  session's tasks from that. A task another session holds is theirs: take it over only with
  `start <id> --takeover "<why>"`, when that session is gone. A done task comes back only with `--reopen "<why>"`.
- **A plan change is a file:** `ledger.py add-plan --from plan.json` (`--dry-run` first) files waves, epics, tasks and
  decisions together; `edit-task <id> --reason "..."` changes one task. Never a script that writes the JSON.

## Rules

- **Verify before asserting.** Run it, grep the symbol, or compile it. Do not answer "is this used / dead / wired
  up" from reading alone.
- **Measure before the second fix.** When a fix does not work, do not guess another: instrument first (a log line,
  a first case against a second case, one variable changed) until the cause is seen, then fix that.
- **Ask the owner first**, with your reasoning and what breaks if we don't, before: adding a dependency, restructuring
  folders, anything that spends money, or any change whose rollback isn't obvious. Otherwise act; routine judgment
  calls are yours. A change a ledger task already lists, from a ruled decision, is already agreed.
- **Stamp non-obvious changes.** A comment explaining one carries the date and the reason.
- **Docs describe current state only:** no changelog sections. Proposals live in their own file marked PROPOSED.

## Subagents

- **Default to doing the work inline.** Most work is "verify, then decide", which subagents do worst; handoffs lose
  detail, and their reasoning is invisible afterwards.
- **Split by context, never by role.** A subagent earns its place only when its work needs little from this session
  and returns a compact result: a read-only search across many files, truly parallel independent work on files this
  session is not touching, or a black-box review.
- **The departments are reviewers**, not owners: they review or measure and return findings in the ledger's one
  shape. A new kind of know-how becomes a skill, not an agent that mirrors a job title.

## Replies

- **A question from me stops your work.** Whether it starts a reply or arrives while you are working, your next
  reply answers it - first, at the top - and then the reply ends. The work picks up after.
- **An instruction or a report from me is work, not a question.** "Do X", "this looks wrong": one line at the top
  saying you have it, then do it in the same reply. Only a question ends the reply.
- **Never end a reply promising work.** Either the work is done or running when you stop, or you name exactly what
  blocks it: my answer, a build, a missing tool.
- **One decision at a time.** `ledger.py next` asks one; a message asks one too.

(The hooks back these up: `owner_message_check.py` tells Claude after its next tool call that you wrote, and
`ledger_stop.py` blocks a reply that ends with "I'll do it" or skips your message.)
