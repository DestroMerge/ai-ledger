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

## Session length

- **A session costs by its length.** Each step re-reads the conversation so far. `.claude/settings.json` caps the
  conversation (`autoCompactWindow`, tokens): Claude Code summarises it there.
- **`python Docs/Ledger/usage.py --latest`** counts where a session's tokens went, and with `--check --cap <tokens>`
  fails when a step passed the cap.
- **Write the task's `note` before a summary can take the detail:** at a task boundary, and whenever a stretch of
  measuring has left numbers that are nowhere but in the conversation.

## Compact instructions

When this conversation is summarised, keep: the ledger tasks this session holds, and what was uncommitted; my last
instruction, and any question still open to me, in my own words; what was measured and is not yet in a task's note;
data changed for a test and not put back; jobs still running in the background; and what I am waiting for, with the
one message that would give it to me.

## Subagents

- **Default to doing the work inline.** Most work is "verify, then decide", which subagents do worst; handoffs lose
  detail, and their reasoning is invisible afterwards.
- **Split by context, never by role.** A subagent earns its place only when its work needs little from this session
  and returns a compact result: a read-only search across many files, truly parallel independent work on files this
  session is not touching, or a black-box review.
- **The departments are reviewers**, not owners: they review or measure and return findings in the ledger's one
  shape. A new kind of know-how becomes a skill, not an agent that mirrors a job title.

## Replies

- **What I must read or act on is the last message of a reply.** An answer, a command to run, a path, a choice:
  whole and standing alone, at the end. I may be shown only a summary of what you write between tool calls. If a
  reply goes on after such a message, its new last message says it again in full.
- **A question from me is answered soon, and does not park the work.** When it arrives while you are working: make
  no more tool calls than the answer needs, leave a long step running in the background or stop where nothing is
  half-changed, and end the reply on the answer. The work then goes on: when the background step reports, or when
  the stop hook continues the reply. End on the answer alone only when it needs my decision before the work can go
  on.
- **An instruction or a report from me is work.** "Do X", "this looks wrong": one line saying you have it, then do
  it in the same reply; what came of it goes in the last message.
- **Never end a reply promising work.** Either the work is done or running when you stop, or you name exactly what
  blocks it: my answer, a build, a missing tool.
- **One decision at a time.** `ledger.py next` asks one; a message asks one too.
- **A reply restarted with nothing new is one or two lines.** When a stop hook or a `/goal` continues a reply and
  nothing has changed since its last message (no message from me, no step finished), the new last message is the
  open question and its choices again, not the whole answer a second time.

(The hooks back these up: `owner_message_check.py` tells Claude after its next tool call that you wrote, and
`ledger_stop.py` blocks a reply that ends with "I'll do it" or skips your message.)
