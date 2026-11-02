# Ledger - the manual

The task ledger for multi-session work with Claude Code. The repository's top-level README says why it exists and
how to set it up; this file is the reference for the tool. Examples use the id prefix `PR` and the owner `Sam`; yours
come from `ledger_config.json`.

| File | What it is |
|---|---|
| `epics/PR-Enn.json` | One file per epic: `{"epic": {...}, "tasks": [...], "decisions": [...]}` - the epic, its tasks, and the decisions filed under it. Together with `meta.json`, the source of truth |
| `meta.json` | What belongs to no one epic: waves, gates, conventions, departments, `auditFrom`, recorded outside commits |
| `LEDGER.md` | Rendered from the JSON files. Never hand-edit it; `render --check` refuses a stale or edited copy. An epic whose every task is done or withdrawn is one line here, without its tasks; read a finished task with `ledger.py show <id>` or on the web view. Nothing is ever removed from the JSON |
| `ledger.py` | The only tool that changes state or commits ledger work |
| `ledger_config.json` | The four settings that tie the tool to one project: id prefix, owner, version control, Unreal. See "Configuration" |
| `ledger_web.py`, `web/` | A local web view of the ledger. It never writes the ledger; it files the owner's answers to open decisions in an inbox the tool reads |
| `setup_ledger.py` | Writes the config, retags the example ledger to your prefix and owner, and starts the audit at your current commit |
| `add_epic.py` | Adds an epic with its first task (there is no `ledger.py` command for it) |
| `review_graph_dump.py` | Unreal projects only: dumps the Blueprint graphs a review covers, for a department agent that has no editor |

The ledger lives in version control, so every session and every machine reads the same state.

Python 3.10 or newer. No packages beyond the standard library.

## Configuration

`ledger_config.json` holds everything that ties the tool to one project. `ledger.py`, `ledger_web.py` and the ledger
hooks read it; nothing else in them names a project. `setup_ledger.py` writes it.

```json
{
  "idPrefix": "PR",
  "owner": "Sam",
  "vcs": "git",
  "unreal": false
}
```

| Key | What it sets |
|---|---|
| `idPrefix` | The letters every id starts with: epics `PR-E02`, tasks `PR-E02.01`, decisions `PR-D95`, epic files `epics/PR-E02.json`. 1 to 8 capital letters or digits, starting with a letter |
| `owner` | The one person who rules on decisions (a website answer is ruled in their name) and who may own an open task without being a department. The task field saying what they do on a task is `needs` plus the name without spaces: `needsSam` |
| `vcs` | The version control every log, diff and commit goes through: `git` or `diversion`. Each is a class in `ledger.py` (`Git`, `Diversion`); another system needs a class with the same attributes and methods, added to `VCS_BACKENDS` |
| `unreal` | `true`: a `/Game/` asset path counts as evidence, and a review covering `.uasset` or `.umap` files needs their Blueprint graphs dumped before it is dispatched. `false`: neither, and department prompts name no editor |

- A key starting with `_` is a note for the reader. A missing key, an unknown key or a bad value stops the tool with
  a message saying which.
- What is the same in every project: the tool lives in `Docs/Ledger/` at the repository root, its working files (the
  lock, the answers inbox, the commit cache) in `Saved/Ledger/` (ignored by version control), the department agents in
  `.claude/agents/`, and note stamps are the machine's local time.
- Commit ids: git commits are written `git:<12 hex>` (`git:3f9a2c1b7d04`), so a pointer in evidence cannot be
  mistaken for other hex. Diversion's are `dv.commit.N`.
- `selftest` runs its fixtures under fixed settings whatever the config says, then checks a made-up project's
  (prefix `XY`, owner Ana, not Unreal), so it passes in any project.

## Starting a session on the ledger

1. `python Docs/Ledger/ledger.py next` - where the work is: the current wave, what is in progress, blocked and
   ready, any answer the owner gave on the website that is waiting to be applied, and the one decision to ask the
   owner next. Run it at every task boundary.
2. `python Docs/Ledger/ledger.py show PR-E01.02` - one task (or decision, or epic) in full.
3. `start` a task before working on it, `done` it with evidence, and commit through `ledger.py commit`.

**The Stop hook.** `.claude/hooks/ledger_stop.py` runs every time Claude is about to end a reply. In a session that
has started or dispatched a task (the ledger shows the command took), it blocks the ending once when there is work
the hub can do without the owner - an answer to apply, a subtask to confirm, its own or a stale in-progress task, a
ready task - and lists up to six. Claude then starts the first, or ends saying in one line why each needs the owner.
It never acts in a session that has not worked the ledger, never blocks twice in a row, and on any error lets the
reply end with a one-line message. It is registered in the checked-in `.claude/settings.json`, so every machine runs
it.

The hook finds the tasks a session took on by the text `python ... ledger.py start <id>` in its tool calls. A shell
variable in front of `ledger.py` (`$PY Docs/Ledger/ledger.py start ...`) hides it; write `python` or `python3`.

## Daily use

```
python Docs/Ledger/ledger.py next
python Docs/Ledger/ledger.py start PR-E02.01
python Docs/Ledger/ledger.py note PR-E02.01 --text "what was found or decided"
python Docs/Ledger/ledger.py done PR-E02.01 --evidence "ran-it: ..."
python Docs/Ledger/ledger.py commit --message msg.txt --agent "Claude" <paths...>
python Docs/Ledger/ledger.py check
```

A department's review or measure runs through `dispatch`, `submit` and `confirm` instead of `start` and `done`:
see "Department subtasks" below.

- **`note`** appends to a task's notes, stamped from the clock.
- **`add-task --epic PR-Enn --from task.json`** adds a task under the next free id in that epic.
  The file holds the task's fields (`title`, `owner`, `size`, `wave`, `files`, `acceptance`, and
  `dependsOn` or `notes` if it has them); the tool sets `id`, `epic` and `status`.
- **`add-decision --epic PR-Enn --from decision.json`** adds an open decision under the next free
  `PR-Dnn`, filed in that epic's file. The file holds `question`, `recommendation`, `options` and
  `source`.
- **`withdraw <task> --reason "..."`** takes an unfinished task out of the plan (status `withdrawn`, the reason
  stamped into its notes). Never a done task, nor a department subtask in progress.
- **A new epic** comes from `add_epic.py`, with its first task.
- Commit an add straight away, with `--role Ledger`, so another session cannot take the same id.

### Blocked tasks

A blocker says in words what the task waits on, and names what releases it, so it cannot outlive
its question:

```
python Docs/Ledger/ledger.py block PR-E02.01 --on "Decision PR-D95 on the website" --until PR-D95
python Docs/Ledger/ledger.py block PR-E02.01 --on "Sam plays the build three times" --until none
python Docs/Ledger/ledger.py block PR-E02.01
```

- **`--until <ids>`**: the decisions or tasks whose ruling or finish ends the wait. They are stored
  as `waitsOn`. Once every one is decided or done, `save()` moves the task back to `planned` with a
  dated note and prints what it released - whichever command or script did the saving, including
  `decide` and `apply`.
- **`--until none`**: it waits on a person or an event (a play session, a deploy), which no id
  stands for.
- When the text names a decision or task that is not yet decided or finished, `block` refuses until
  `--until` says which of the two it is.
- **`block <task>` with no `--on`** re-checks a task that is already blocked: same text, dated today.
  Use it after confirming the blocker is still true.
- `validate` refuses a blocked task whose `waitsOn` is all decided or done, so a hand edit cannot
  leave one stale either.
- `next` prints, for every blocked task, what it waits on, the state of every id its text names
  (`PR-D96 decided 2026-10-16`), and when it was last checked. An in-progress task shows when it
  was last touched (its newest dated note, or when it was started). Either one older than
  three days, or never dated, is flagged `STALE?`: re-check it before reporting it.
- When the current wave has nothing ready, `next` also lists ready tasks in later waves, even while
  the current wave still has tasks in progress.

### Committing

The commit's subject starts with the task id (`PR-E02.01: ...`), an epic id, or the commit names a
role (`--role Owner|Repair|Checkpoint|Ledger --reason "..."`).

- The tool adds the named task's epic file and `LEDGER.md` to the paths itself.
- **A file the task's list does not hold**: commit with `--reason "<why it belongs to the task>"`
  (15 or more characters). The path is added to the task's `files`, the addition is recorded in its
  `filesAdded` with the reason and the date, and the commit goes on. Without `--reason` the commit is
  refused and says so.
- Closing a task makes two commits: the work (with the epic file), then the epic file alone with
  `landed` stamped.
- With git, the commit holds exactly the paths named: they are staged and committed with `git commit --only`, so
  anything else you have staged stays staged and out of it.

## Department subtasks

A department is an agent file in `.claude/agents/`, started fresh by the hub - the main Claude Code session working
the ledger - for one ledger task. The agent sees its own file and the prompt `dispatch` prints, nothing else, so
nothing stale or retold reaches it.

The template ships two example departments, listed in `meta.json` `departments`:

| Department | Agent file | Kinds | Editor |
|---|---|---|---|
| Code Review Department | `code-review-department` | review | none |
| Game Design Department | `game-design-department` | review | none |

- **A department subtask is a task with `kind`:** `review`, `measure` or `produce`. Its `owner` is a department from
  `meta.json` `departments`; the department's `agent` names its file.
- **A `produce` makes something** (an asset, an effect) and may change only the files its task lists, so its
  `files` list is required; it runs in the foreground like a measure, hands in with `submit`, and the hub commits
  its work after `confirm`. Every other kind edits nothing.
- **`parent`** (required for a review) is a task, or an epic standing for every task in it with no `kind`. It counts
  as a dependency, and `dispatch` freezes it into `covers`, so a task added to the epic later cannot make the
  review invalid.
- **Only the hub marks a subtask done.** The agent hands its work in with `submit` and the task stays in progress;
  the hub re-opens one pointer and runs `confirm`, with a `--by` other than the holder's.
- **Findings are recorded by the agent and routed by the hub.** Before it confirms, the hub routes every finding:
  `route <id>#<n> --to <task>` (filing the fixing task first if none exists) or `--reject "<why>"`. The severity
  says where the fixing task goes. `blocker` goes on the critical path now. `hardening` is everything else: it goes
  in the wave its work belongs to. A finding left unrouted on a subtask that is no longer in progress is listed by
  `next` under TO ROUTE.
- **Every finding, and every subtask's evidence, points at something checkable:** a repo path (with `:line`), a
  log or screenshot path, a commit id, or (in an Unreal project) a `/Game/` asset with its node named.
- **`needsSam`** (`needs` plus the owner's name) on any task says, in words, what the owner does on it. The
  website's "Waiting on" column reads it.
- **What keeps an agent to its own subtask is a hook, the tool's own checks, and rules.** Each agent file registers
  `.claude/hooks/department_fence.py` as a PreToolUse hook, which runs before every shell command and file write
  that agent makes and refuses: `ledger.py` with any subcommand but the agent's seven (`show`, `next`, `findings`,
  `start`, `finding`, `submit`, `block`); Python that imports the ledger module; running `claude`; `git commit` or
  `dv commit`; and a Write or Edit under `Docs/Ledger/`. It matches real invocations, so a grep or an echo that
  names a command passes. The tool takes `confirm` and `route` only from the dispatching hub and `start` only as
  `<owner> agent`. The rest is rules in the agent file and the dispatch prompt - a shell can reach `claude` by routes
  the hook does not parse. A permission allowlist is no fence: in auto mode, a command missing from it goes to the
  auto-mode classifier, not to a prompt. An agent file is read when a session starts, so a hub session older than a
  change to an agent file runs the old one.

### What the hub does

1. **File it** with `add-task`: owner, `kind`, `parent`, and an acceptance from the templates below. An epic's
   review is filed while the epic is still planned, with the epic as its parent, so the epic never closes and then
   reopens.
2. **When `next` lists it ready**, dispatch it. In an Unreal project, a review that covers Blueprints needs their
   graphs first: the department agent has no editor, so the hub, which owns it, runs `review_graph_dump.py` inside
   the editor (`dispatch` prints what to run), then dispatches with `--dump <folder>`.
3. **`dispatch <id> --by "<hub title>"`** prints the agent and its prompt. Start it with the Agent tool, that
   agent as the subagent type, the prompt word for word. A review runs in the background; a measure runs in the
   foreground, so a shared resource (an editor, a device) has one owner while it works. A background agent's
   permission prompt waits in the hub session for an answer.
4. **When it returns**, `next` lists it under `TO CONFIRM`. Re-open one pointer. Then ask the one question a
   per-change review cannot see by construction: does anything it covers have to land before or after something
   else (a deploy, a publish, build flags, a migration, the server before the client)? An ordering pair is two
   changes each right alone and wrong in one order; if there is one, it goes in the covered task's notes or a
   blocker. For each blocker finding, file the fixing task (`add-task`, committed `--role Ledger`) and
   `route <id>#<n> --to <task> --by "<hub>"`, or `--reject "<why>"`. Then
   `confirm <id> --by "<hub>" --evidence "<what the pointer showed>"`, and commit the epic file with a subject
   starting with the subtask's id; the tool stamps `landed`.
5. **Only the hub that dispatched a subtask confirms it or routes its findings.** A different hub (the old hub
   session is gone) adds `--new-hub "<why>"` to `confirm` or `route`, and the reason is recorded.
6. **A subtask under `RUNNING` whose agent is gone** (the hub session ended, the run was stopped) is started
   again with `dispatch <id> --by "<hub>" --restart`. The findings already recorded stay, and the new agent does not
   record them again.

### What the department agent does

These steps are in each agent file, word for word.

1. `show <id>`. If the task already has findings, a previous run was stopped: read them, and do not record them
   again.
2. `start <id> --by "<department> agent"`, unless it is already in progress under this department.
3. Do the work. Read only what the prompt lists, and edit nothing.
4. `finding <id> --severity blocker|hardening --text "..." --evidence "<pointer>"`, once per finding, as soon
   as it is found.
5. `submit <id> --evidence "..."`: for a review, every covered file with its verdict (`clean` or `finding N`),
   a `clean` that rests on a guard naming that guard in one clause; for a measure, the number, the budget, the
   output line and the log path.
6. Reply in one line: the id, `submitted`, the number of findings and of blockers.

It does not run `done` or `commit`, does not edit files and does not start agents. If it is stuck (a resource is not
free, the owner is needed), it runs `block <id> --on "..." --until none` and says why.

### Acceptance templates

| Kind | `check` | `expect` |
|---|---|---|
| `review` | Read the covered tasks' files at their landed commits for `<focus>` | The evidence names every covered file with a verdict, `clean` or `finding N` (a `clean` resting on a guard names it); each problem found is a finding with a pointer |
| `measure` | `<the command or test to run>` | `<metric>` is within `<budget>`, with the output line quoted and its log path given |
| `produce` | Make `<the asset>` with `<the pipeline>` | Every asset made or changed is named with its path and a capture; any approval gate still due is named |

`findings [--unrouted] [--severity S]` lists findings across every epic, finished ones included.

### Writing your own department

Copy one of the example agent files. Keep the frontmatter `hooks:` block (it registers the fence), the department
steps and the "How you work" list; rewrite "What you look for" and "Severity" for the department's job. Add it to
`meta.json` `departments` with its `name`, `agent` (the file name without `.md`), `editor` (`none`, `foreground`
for a department that needs a shared resource while it runs, or `owns` for the hub) and a `does` line. Start a new
Claude Code session afterwards: agent files are read when a session starts.

## Several sessions at once

Several sessions can use the ledger at the same time, in one working copy. The split into one file per epic is so
they do not overwrite each other:

- `load()` reads every file into one ledger; `save()` writes back **only the files whose content
  changed**. Two sessions working in different epics never touch each other's files.
- If another session changed a file after this command read it, and this command would change the
  same file, the save is **refused** (`LedgerConflict`) and nothing is written. Run the command again;
  it reads the files afresh.
- Commands that change the ledger hold `Saved/Ledger/ledger.lock` while they run, so two runs of the
  tool do not interleave. `save()` takes the same lock for its compare-and-write, so a script's save is
  covered too, and a run that already holds it takes it again freely. A lock is taken over only when the
  process it names has exited, never because it is old: a run waits up to 30 seconds for a live holder,
  then refuses. A run removes the lock only while it still holds its own token.
- The website's answers inbox (`Saved/Ledger/answers.json`) is written under the same lock by both the page
  and `apply`, which re-reads it just before removing the answer it applied. An inbox that cannot be read is
  refused, never read as empty.
- A script that needs something the commands do not offer uses `import ledger; led = ledger.load()`,
  changes `led`, then `ledger.save(led)` and `ledger.write_render()` - the same rules apply. Do not
  write the JSON files directly. `add_epic.py` is an example.

## The check gate

`python Docs/Ledger/ledger.py check` runs three things, and passes only if all three do:

1. **validate** - the ledger's own rules: every id is well formed and unique; every task has an
   owner, size, wave, files and an acceptance check; dependencies exist and do not loop; a task in
   progress or done has its dependencies finished and its decisions ruled; a done task has evidence;
   an open decision has options.
2. **render --check** - `LEDGER.md` matches the JSON.
3. **audit** - every commit after `auditFrom`, read from the version control's log, is judged:
   - a commit that names a task must name a real one that has been started;
   - a commit made outside the tool (no task, no role) that touched a file of a task in progress is
     refused, unless it is recorded in `meta.json` `outsideCommits` with a reason;
   - every done task has a `landed` commit, and that commit names the task.

The audit is the only check that sees work which bypassed the ledger - a commit made with `git` directly. Without
it, a task can be closed with no commit behind it, and edits can land in an open task's files with nothing recording
why.

Its known limit: it judges an old commit against the tasks in progress *now*, not when the commit was
made, so history can be flagged that was in scope at the time. It caches each commit's paths in
`Saved/Ledger/commit_paths.json`.

`auditFrom` moves only to a commit inside the window, after a green audit at the old mark.

## Web view

```
python Docs/Ledger/ledger_web.py
```

Serves `http://127.0.0.1:8717` and opens it in the browser (`--no-browser`, `--port N`; run a second project's on
another port). It is local: it answers only on 127.0.0.1, and serves nothing but the page, the ledger, the evidence
images beside it and the plan files the epics name. It never writes the ledger. The one thing it takes is an answer
to an open decision (below).

| Tab | What it shows |
|---|---|
| Now | The same answer as `next`: one cell per wave, what is in progress, blocked, ready, department work (running, or handed in and waiting for the hub's confirm), and waiting on the owner |
| Map | Every task in its wave, left to right. Point at one to trace what it needs and what waits on it |
| Epics | Progress per epic, one square per task |
| Tasks | The full list, filtered by status, epic, wave, owner, size or text |
| Decisions | Each open question with its options as buttons, to answer on the page; then each ruling and the tasks it gates |
| Activity | Every commit after `auditFrom` and the task it names, with tasks landed over time |
| Rules | Gates, conventions, recorded outside commits, and anything `validate` refuses |

"Ready", the current wave and the rule problems come from `ledger.py` itself (the page imports it),
so the page cannot disagree with the tool. It reloads when any ledger file changes on disk. To
change state, open a task and copy the command it offers.

### Answering a decision on the page

An open decision carries `options`: two to four choices, each a `label` and a `detail`, one of them
`"recommended": true`. `validate` refuses an open decision without them. The Decisions tab shows them
as buttons, recommended first, with `Other` for a written answer and an optional note.

1. The owner picks an option and saves it. The page files it in `Saved/Ledger/answers.json` - an inbox,
   unversioned, and not the ledger. It can be changed or withdrawn until a session applies it.
2. `python Docs/Ledger/ledger.py next` prints waiting answers before anything else.
3. A session runs `python Docs/Ledger/ledger.py apply PR-Dnn`. That makes the answer the ruling
   (by the owner, dated when they answered, `ruledVia` `website`), and empties it from the inbox. The
   session commits the decision's epic file with `--role Ledger` and starts the tasks it gated.

A session that writes a new decision writes its options with it (`add-decision`).

## What the tool refuses

- A commit whose subject names no task, epic or role.
- A commit naming a task that is still planned.
- A commit touching a file outside the named task's files list, unless `--reason` says why it
  belongs (then the file is added to the list).
- New work on a landed task without `--role Repair`.
- Starting a task whose dependencies are unfinished or whose decision is still open.
- Marking a task done without evidence.
- An epic whose status does not match its tasks. The tool writes it at every save (planned until a task starts,
  done when every task is done or withdrawn, in progress between); it is never set by hand.
- A decision that is filed under no epic.
- A save over a ledger file another session changed after this command read it.
- A stale or hand-edited `LEDGER.md`.
- An open task whose owner is not on `meta.json`'s departments list (or the owner), and an open task owned by a
  department other than the hub with no `kind`.
- Dispatching a subtask that is not ready, has no agent file, is already out without `--restart`, or (Unreal) is a
  review covering Blueprints with no graph dump; starting one without `--by` or before it is dispatched.
- `done` on a subtask; a `submit`, `finding` or `confirm` whose evidence points at nothing checkable; a review's
  `submit` that does not name every covered file; a `confirm` by the subtask's own holder or with any finding
  unrouted; a `confirm` or `route` from a hub other than the one that dispatched it, without `--new-hub`.
- Starting a subtask under any name but `<owner> agent`, or once it is handed in; dispatching a review that covers no
  task, or again once it is handed in; `decide` with no `--by`; a `meta.json` with no `departments` list.
- A commit outside the tool that touched an in-progress task's files, unless it is recorded in
  `outsideCommits` with a reason.

`python Docs/Ledger/ledger.py selftest` plants each of these faults and shows it refusing, with a
clean control beside each one. Run it whenever `ledger.py` changes.

## Waves, and why

Each task carries a wave, and the waves are the execution order. A wave groups work that needs the same conditions:
in an Unreal project, for example, the editor must be closed for a C++ build and open for Blueprint work, so
`meta.json` can carry `waveEditor` (`"open"` or `"CLOSED"` per wave) and `next` shows it. Group yours by whatever
is a serial resource in your project, or simply by milestone.
