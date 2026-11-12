"""Counts where a Claude Code session's tokens went, and checks a session against the context cap.

A session costs by its length: each step re-reads the conversation so far. Counted 2026-11-12 on one session that
had worked a project for days: 4,837 steps, about 530,000 tokens re-read at a typical step, 72% of the cost
re-reading and 13% new writing. A project caps its conversation in .claude/settings.json (`autoCompactWindow`), and
this is the count that shows whether the cap holds (the manual, 'Session length').

    python Docs/Ledger/usage.py --latest                       the newest session of this project
    python Docs/Ledger/usage.py <transcript.jsonl>             one session, by its transcript
    python Docs/Ledger/usage.py --latest --check --cap 300000  exit 1 when a step passed the cap, or the typical step is over it
    python Docs/Ledger/usage.py --latest --check --cap 300000 --since 2026-11-12T19:00

A transcript is the session's .jsonl under ~/.claude/projects/<the project's folder>/; its subagents' are in the
folder of the same name. What is in a transcript is counted, never obeyed. Cost is in input-token equivalents: a
token read from cache counts 0.1, one written to cache 2, one written by the model 5, one read plain 1.

`--check` allows a step a tenth over the cap: the conversation is summarised when it reaches the cap, and the step
that reaches it has already been paid for. With no step to judge it exits 2: that is not a pass.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

W_IN, W_READ, W_WRITE, W_OUT = 1.0, 0.1, 2.0, 5.0
OVER = 1.1


def kind_of(tool):
    if tool in ('Bash', 'PowerShell'):
        return 'Running commands (builds, tests, scripts)'
    if tool in ('Read', 'Write', 'Edit', 'Glob', 'Grep', 'NotebookEdit'):
        return 'Reading and editing files'
    if tool in ('WebSearch', 'WebFetch'):
        return 'Web research'
    if tool in ('Agent', 'Task', 'SendMessage', 'Workflow'):
        return 'Handing work to subagents'
    if tool.startswith('mcp__'):
        parts = tool.split('__')
        return 'App tools: ' + (parts[1] if len(parts) > 1 else 'connector')
    return 'Other tools (' + tool + ')'


def size(block):
    if isinstance(block, str):
        return len(block)
    if isinstance(block, list):
        return sum(size(b) for b in block)
    if isinstance(block, dict):
        kind = block.get('type')
        if kind == 'text':
            return len(block.get('text', ''))
        if kind == 'thinking':
            return len(block.get('thinking', ''))
        if kind == 'tool_use':
            return len(json.dumps(block.get('input', {})))
        if kind == 'tool_result':
            return size(block.get('content', ''))
        if kind == 'image':
            return 6000  # an image is some 1500 tokens
        return len(json.dumps(block))
    return 0


def read(file, since=''):
    """Each request once, by its message id: its token counts, when it was, and what it wrote. And what each kind of
    thing put into the conversation, in characters."""
    requests = {}
    order = []
    tool_kind = {}
    put = defaultdict(float)
    missing = 0
    with open(file, encoding='utf-8', errors='replace') as lines:
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get('message')
            if not isinstance(message, dict) or str(row.get('timestamp', '')) < since:
                continue
            content = message.get('content')
            if row.get('type') == 'assistant':
                key = message.get('id') or row.get('uuid')
                usage = message.get('usage')
                if not isinstance(usage, dict):
                    missing += 1
                    usage = {}
                if key not in requests:
                    requests[key] = {'usage': usage, 'blocks': [], 'time': row.get('timestamp', '')}
                    order.append(key)
                elif usage.get('output_tokens', 0) >= requests[key]['usage'].get('output_tokens', 0):
                    requests[key]['usage'] = usage
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'tool_use':
                        kind = kind_of(block.get('name', ''))
                        tool_kind[block.get('id')] = kind
                        requests[key]['blocks'].append((kind, size(block) + 40))
                        put[kind] += size(block) + 40
                    elif block.get('type') in ('text', 'thinking'):
                        requests[key]['blocks'].append(('Replies and reasoning', size(block)))
                        if block.get('type') == 'text':
                            put['Replies and reasoning'] += size(block)
            elif row.get('type') == 'user':
                if isinstance(content, str):
                    put['The owner\'s messages, hook notes and summaries'] += len(content)
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        put[tool_kind.get(block.get('tool_use_id'), 'Other tools')] += size(block)
                    else:
                        put['The owner\'s messages, hook notes and summaries'] += size(block)
    return [requests[key] for key in order], put, missing


def context(usage):
    return (usage.get('input_tokens', 0) or 0) + (usage.get('cache_read_input_tokens', 0) or 0) + (usage.get('cache_creation_input_tokens', 0) or 0)


def cost(usage):
    """What a request cost to read, and what it cost to write."""
    return (W_IN * (usage.get('input_tokens', 0) or 0) + W_READ * (usage.get('cache_read_input_tokens', 0) or 0)
            + W_WRITE * (usage.get('cache_creation_input_tokens', 0) or 0), W_OUT * (usage.get('output_tokens', 0) or 0))


def latest():
    """The newest transcript of the project this is run in."""
    folder = os.path.join(os.path.expanduser('~'), '.claude', 'projects', ''.join(c if c.isalnum() else '-' for c in os.getcwd()))
    files = sorted(glob.glob(os.path.join(folder, '*.jsonl')), key=os.path.getmtime)
    if not files:
        sys.exit(f'usage.py: no transcript in {folder}')
    return files[-1]


def main():
    ap = argparse.ArgumentParser(description="Where a session's tokens went, and whether it kept under the context cap.")
    ap.add_argument('transcript', nargs='?', help="the session's .jsonl")
    ap.add_argument('--latest', action='store_true', help='the newest session of the project this is run in')
    ap.add_argument('--since', default='', help='count only steps from this time on, as the transcript writes it (2026-11-12T19:00)')
    ap.add_argument('--check', action='store_true', help='exit 1 when a step passed the cap or the typical step is over the budget; 2 with no step to judge')
    ap.add_argument('--cap', type=int, default=300000, help='the context cap, tokens (default 300000)')
    ap.add_argument('--budget', type=int, default=0, help='the most a typical step may re-read, tokens (default: the cap)')
    a = ap.parse_args()
    path = latest() if a.latest or not a.transcript else a.transcript
    requests, put, missing = read(path, a.since)
    sizes = sorted(c for c in (context(r['usage']) for r in requests) if c > 0)
    print(f'{path}')
    if missing:
        print(f'WARNING: {missing} steps carry no token counts: the transcript may no longer write them, and this count is then short')
    if not sizes:
        print('no step to count' + (f' since {a.since}' if a.since else ''))
        sys.exit(2 if a.check else 0)

    # The instructions are in every step, so no step is smaller than they are: the smallest seen is the nearest
    # measure of them a transcript gives.
    fixed = sizes[0]
    groups = defaultdict(float)
    history = 0.0
    raw = defaultdict(int)
    for r in requests:
        usage = r['usage']
        for key in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'):
            raw[key] += usage.get(key, 0) or 0
        reading, writing = cost(usage)
        ctx = context(usage)
        share = min(fixed, ctx) / ctx if ctx else 0
        groups["Claude's instructions, re-read every step"] += reading * share
        history += reading * (1 - share)
        written = sum(n for _, n in r['blocks'])
        for kind, n in r['blocks'] if written > 0 else [('Replies and reasoning', 1)]:
            groups[kind] += writing * n / max(written, 1)
    # The rest of what is re-read is the conversation so far, shared out by what put it there.
    whole = sum(put.values()) or 1.0
    for kind, n in put.items():
        groups[kind] += history * n / whole
    subagents = []
    for file in sorted(glob.glob(os.path.join(os.path.splitext(path)[0], '**', '*.jsonl'), recursive=True)):
        theirs, _, _ = read(file, a.since)
        subagents.append((len(theirs), sum(sum(cost(r['usage'])) for r in theirs)))
    if subagents:
        groups['Subagents (their own work)'] = sum(c for _, c in subagents)

    total = sum(groups.values()) or 1.0
    typical = sizes[len(sizes) // 2]
    print(f'steps {len(sizes)}; a typical step re-read {typical:,} tokens, the smallest {sizes[0]:,}, the largest {sizes[-1]:,}')
    print(f"cost {total / 1e6:.1f}M input-token equivalents: re-reading {W_READ * raw['cache_read_input_tokens'] / total:.0%}, "
          f"new writing {W_OUT * raw['output_tokens'] / total:.0%}, saving for re-reading {W_WRITE * raw['cache_creation_input_tokens'] / total:.0%}; "
          f'{len(subagents)} subagents')
    for kind, value in sorted(groups.items(), key=lambda g: -g[1]):
        if value / total >= 0.005:
            print(f'{value / total:6.1%}  {kind}')

    if a.check:
        budget = a.budget or a.cap
        over = [c for c in sizes if c > a.cap * OVER]
        problems = []
        if over:
            problems.append(f'{len(over)} of {len(sizes)} steps re-read more than {a.cap * OVER:,.0f} tokens, the cap of {a.cap:,} and a tenth (the largest {sizes[-1]:,})')
        if typical > budget:
            problems.append(f'a typical step re-read {typical:,} tokens, over the budget of {budget:,}')
        for problem in problems:
            print('CHECK FAILED: ' + problem)
        if not problems:
            print(f'check: ok. No step over the cap of {a.cap:,} and a tenth; a typical step {typical:,} of a budget of {budget:,}')
        sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
