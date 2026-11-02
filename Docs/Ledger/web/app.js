'use strict';
// Ledger view. It draws what /api/state returns and never writes the ledger. The one thing it sends is the
// owner's answer to an open decision (POST /api/answer), which the server files in an inbox for ledger.py.
// Everything that is a RULE (what is ready, the current wave, the validate problems) is computed by
// ledger.py on the server. This file only lays those answers out.

// ---------------------------------------------------------------- constants

const STATUS = {
  'done':        { label: 'Done',        glyph: '✓' },
  'in-progress': { label: 'In progress', glyph: '▸' },
  'blocked':     { label: 'Blocked',     glyph: '!' },
  'planned':     { label: 'Planned',     glyph: '○' },
  'withdrawn':   { label: 'Withdrawn',   glyph: '⊘' },
};
const STATUS_ORDER = ['done', 'in-progress', 'blocked', 'planned', 'withdrawn'];
const BAR_ORDER = ['done', 'in-progress', 'blocked', 'planned'];   // withdrawn is out of the total, as in ledger.py
const ATTENTION_RANK = { 'in-progress': 0, 'blocked': 1, 'planned': 2, 'done': 3, 'withdrawn': 4 };
const SIZE_RANK = { S: 0, M: 1, L: 2 };
const FINISHED = new Set(['done', 'withdrawn']);
const VIEWS = [['now', 'Now'], ['map', 'Map'], ['epics', 'Epics'], ['tasks', 'Tasks'],
               ['decisions', 'Decisions'], ['activity', 'Activity'], ['rules', 'Rules']];
const TOOL = 'python Docs/Ledger/ledger.py';
const POLL_MS = 2000;
const SVG_NS = 'http://www.w3.org/2000/svg';
// The id prefix, the owner, the task field naming the owner's part and the version control's
// names come from ledger_config.json, sent as derived.profile, so the page serves any project's ledger. applyProfile()
// builds RE_REF (the ids, commit ids and evidence images found in ledger prose) and RE_OWNER from it. A decision id
// takes two or three digits, as ledger.py's do; the old pattern took two, so PR-D100 and up were never linked.
let P = null, RE_REF = null, RE_OWNER = null;
const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function applyProfile(p) {
  if (!p) throw new Error('The ledger web server is older than this page: it does not send the project settings. '
    + 'Stop it (close its window) and start it again.');
  P = p;
  const x = reEscape(p.idPrefix);
  RE_REF = new RegExp(`${x}-E\\d{2}\\.\\d{2}(?!\\d)|${x}-E\\d{2}(?!\\d|\\.\\d)|${x}-D\\d{2,3}(?!\\d)|${p.commitId}`
    + '|[\\w-]+(?:\\.[\\w-]+)*\\.(?:png|jpe?g|webp)', 'g');
  RE_OWNER = new RegExp(`\\b${reEscape(p.owner)}\\b`);
}

const S = {
  ledger: null, derived: null, commits: null, stamp: null, offline: false, loadError: null,
  answers: {}, pick: {}, editing: new Set(), sending: false,
  view: 'now', sel: null,
  now: { showFinished: false },
  map: { dense: false, allEdges: false, epic: '', hover: null, left: null, scrollTo: null },
  tasks: { status: new Set(), epic: '', wave: '', owner: '', size: '', q: '', sort: 'id', dir: 1 },
  activity: { filter: 'all' },
};
const IX = { tasks: new Map(), epics: new Map(), decisions: new Map(), commits: new Map(),
             commitsByTask: new Map(), dependents: new Map(), byEpic: new Map(), byWave: new Map(),
             ready: new Set(), images: new Set(), waves: [] };

// ---------------------------------------------------------------- small helpers

const $ = (sel, root = document) => root.querySelector(sel);

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// Builds DOM from values, never from markup, so ledger text (which is full of < > and quotes) cannot
// be read as HTML.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  return append(el, kids);
}

function fill(el, ...kids) {
  el.replaceChildren();
  return append(el, kids);
}

function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
  return append(el, kids);
}

function clickable(el, fn) {
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  el.addEventListener('click', fn);
  el.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target === el) { e.preventDefault(); fn(e); }
  });
  return el;
}

function push(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const shortId = id => (P && id.startsWith(P.idPrefix + '-') ? id.slice(P.idPrefix.length + 1) : id);
const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
const fmtShortDay = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const fmtClock = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
const fmtFull = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dayKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function countStatuses(tasks) {
  const c = {};
  for (const s of STATUS_ORDER) c[s] = 0;
  for (const t of tasks) c[t.status] = (c[t.status] || 0) + 1;
  c.live = tasks.length - c.withdrawn;
  return c;
}

function unmetDeps(t) {
  return (t.dependsOn || []).filter(d => {
    const task = IX.tasks.get(d), dec = IX.decisions.get(d);
    return (task && !FINISHED.has(task.status)) || (dec && dec.status !== 'decided');
  });
}

function editorState(w) {
  const text = (S.ledger.waveEditor || {})[String(w)] || '';
  const closed = /CLOSED/.test(text), open = /\bopen\b/i.test(text);
  const kind = closed && open ? 'mixed' : closed ? 'closed' : open ? 'open' : 'either';
  const label = { mixed: 'Editor open, then closed to build', closed: 'Editor closed (build)',
                  open: 'Editor open', either: 'Editor either way' }[kind];
  return { text, kind, label };
}

// ---------------------------------------------------------------- index

function buildIndex() {
  const L = S.ledger;
  IX.tasks = new Map(L.tasks.map(t => [t.id, t]));
  IX.epics = new Map(L.epics.map(e => [e.id, e]));
  IX.decisions = new Map(L.decisions.map(d => [d.id, d]));
  IX.ready = new Set(S.derived.ready || []);
  IX.images = new Set(S.derived.images || []);
  IX.dependents = new Map();
  IX.byEpic = new Map();
  IX.byWave = new Map();
  for (const t of L.tasks) {
    push(IX.byEpic, t.epic, t);
    push(IX.byWave, t.wave, t);
    for (const d of t.dependsOn || []) push(IX.dependents, d, t.id);
  }
  const waves = new Set(Object.keys(L.waves || {}).map(Number));
  for (const t of L.tasks) waves.add(t.wave);
  IX.waves = [...waves].sort((a, b) => a - b);
}

function indexCommits() {
  IX.commits = new Map();
  IX.commitsByTask = new Map();
  for (const c of (S.commits && S.commits.commits) || []) {
    IX.commits.set(c.id, c);
    if (c.names) push(IX.commitsByTask, c.names, c);
  }
}

// ---------------------------------------------------------------- shared pieces

function glyph(status, ready) {
  return h('span', { class: `glyph st-${status}${ready ? ' ready' : ''}`, 'aria-hidden': 'true' },
           (STATUS[status] || {}).glyph || '?');
}

function statusTag(t) {
  const ready = IX.ready.has(t.id);
  return h('span', { class: `tag st-${t.status}` }, glyph(t.status, ready),
           STATUS[t.status].label + (ready ? ', ready to start' : ''));
}

// A reference to a task, decision, epic or commit that opens it. Unknown ids stay plain text.
function ref(id) {
  const open = e => { e.stopPropagation(); select(id); };
  const t = IX.tasks.get(id), d = IX.decisions.get(id), e = IX.epics.get(id);
  if (t) return h('button', { class: `ref st-${t.status}`, 'data-tip': `${t.title}\n${STATUS[t.status].label}`, onclick: open },
                  glyph(t.status, IX.ready.has(id)), shortId(id));
  if (d) return h('button', { class: 'ref', 'data-tip': `${d.question}\n${d.status === 'decided' ? 'Decided' : 'Open'}`, onclick: open },
                  h('span', { class: `glyph dec-${d.status}`, 'aria-hidden': 'true' }, '◆'), shortId(id));
  if (e) return h('button', { class: 'ref', 'data-tip': e.title, onclick: open }, shortId(id));
  if (IX.commits.has(id)) return h('button', { class: 'ref mono', 'data-tip': IX.commits.get(id).subject, onclick: open }, id);
  return h('span', { class: 'ref plain mono' }, id);
}

// Ledger prose with the ids inside it made clickable.
function rich(text) {
  text = String(text == null ? '' : text);
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of text.matchAll(RE_REF)) {
    const tok = m[0];
    let node = null;
    if (IX.tasks.has(tok) || IX.decisions.has(tok) || IX.epics.has(tok) || IX.commits.has(tok)) {
      node = h('button', { class: 'inref', onclick: () => select(tok) }, tok);
    } else if (IX.images.has(tok)) {
      node = h('a', { class: 'inref', href: `image/${encodeURIComponent(tok)}`, target: '_blank', rel: 'noopener' }, tok);
    }
    if (!node) continue;
    frag.append(text.slice(last, m.index), node);
    last = m.index + tok.length;
  }
  frag.append(text.slice(last));
  return frag;
}

function imagesIn(...texts) {
  const found = new Set();
  for (const text of texts) for (const m of String(text || '').matchAll(RE_REF)) if (IX.images.has(m[0])) found.add(m[0]);
  return [...found];
}

function stackBar(c, cls) {
  const said = BAR_ORDER.filter(s => c[s]).map(s => `${c[s]} ${STATUS[s].label.toLowerCase()}`).join(', ');
  const bar = h('span', { class: `bar ${cls || ''}`, role: 'img', 'aria-label': said || 'no tasks' });
  for (const s of BAR_ORDER) {
    if (c[s]) bar.append(h('span', { class: `seg st-${s}`, style: `flex-grow:${c[s]}`, 'data-tip': `${c[s]} ${STATUS[s].label.toLowerCase()}` }));
  }
  return bar;
}

function epicLabel(id) {
  const e = IX.epics.get(id);
  return e ? `${shortId(id)} · ${e.title}` : id;
}

function taskCard(t, extra) {
  const ready = IX.ready.has(t.id);
  return clickable(h('div', { class: `card st-${t.status}${ready ? ' ready' : ''}`, 'data-id': t.id },
    h('div', { class: 'card-top' }, glyph(t.status, ready), h('span', { class: 'mono id' }, t.id),
      h('span', { class: 'card-meta' }, `Wave ${t.wave} · ${t.size} · ${t.owner}${t.kind ? ` · ${t.kind}` : ''}`)),
    h('div', { class: 'card-title' }, t.title),
    h('div', { class: 'card-epic' }, epicLabel(t.epic)),
    extra), () => select(t.id));
}

function column(title, sub, items, empty) {
  return h('section', { class: 'col' },
    h('h2', {}, title, h('span', { class: 'count' }, items.length)),
    sub && h('p', { class: 'col-sub' }, sub),
    items.length ? items : h('p', { class: 'empty' }, empty));
}

function selectBox(label, options, value, onchange) {
  const sel = h('select', { 'aria-label': label, onchange: e => onchange(e.target.value) },
                options.map(([v, text]) => h('option', { value: v }, text)));
  sel.value = value;
  return sel;
}

function segmented(options, value, onpick) {
  return h('span', { class: 'segmented', role: 'group' }, options.map(([v, text]) =>
    h('button', { class: v === value ? 'on' : '', 'aria-pressed': String(v === value), onclick: () => onpick(v) }, text)));
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = h('textarea', { style: 'position:fixed;opacity:0' }, text);
    document.body.append(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast('Copied: ' + text);
}

let toastTimer = 0;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

function cmdRow(cmd, note) {
  return h('div', { class: 'cmd' }, h('code', {}, cmd),
           h('button', { class: 'btn', onclick: () => copy(cmd) }, 'Copy'),
           note && h('span', { class: 'cmd-note' }, note));
}

// ---------------------------------------------------------------- tooltip

function showTip(text, x, top, bottom) {
  const tip = $('#tip');
  tip.textContent = text;
  tip.hidden = false;
  const w = tip.offsetWidth, hgt = tip.offsetHeight;
  const left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2));
  let y = bottom + 8;
  if (y + hgt > window.innerHeight - 8) y = top - hgt - 8;
  tip.style.left = `${left}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

function hideTip() { $('#tip').hidden = true; }

function initTips() {
  const from = e => (e.target instanceof Element ? e.target.closest('[data-tip]') : null);
  const show = e => {
    const el = from(e);
    if (!el) { hideTip(); return; }
    const r = el.getBoundingClientRect();
    showTip(el.dataset.tip, r.left + r.width / 2, r.top, r.bottom);
  };
  document.addEventListener('mouseover', show);
  document.addEventListener('focusin', show);
  document.addEventListener('focusout', hideTip);
  document.addEventListener('scroll', hideTip, true);
}

// ---------------------------------------------------------------- routing

function parseHash() {
  const [v, sel] = location.hash.replace(/^#\/?/, '').split('/');
  S.view = VIEWS.some(([k]) => k === v) ? v : 'now';
  S.sel = sel ? decodeURIComponent(sel) : null;
}

function select(id) { location.hash = '#' + S.view + (id ? '/' + encodeURIComponent(id) : ''); }
function go(view) { location.hash = '#' + view + (S.sel ? '/' + encodeURIComponent(S.sel) : ''); }

function onRoute() {
  const prev = S.view;
  parseHash();
  if (!S.ledger) return;
  renderTabs();
  if (S.view !== prev) renderView();
  else { markSelected(); if (S.view === 'map') { applyFocus(); revealChip(); } }
  renderDrawer();
}

function markSelected() {
  for (const el of $('#view').querySelectorAll('[data-id]')) el.classList.toggle('is-sel', el.dataset.id === S.sel);
}

// ---------------------------------------------------------------- header

function renderHeader() {
  const c = countStatuses(S.ledger.tasks);
  fill($('#headline'),
    h('div', { class: 'hero' }, h('span', { class: 'hero-n' }, c.done), h('span', { class: 'hero-of' }, `of ${c.live} tasks done`)),
    h('div', { class: 'hero-bar' }, stackBar(c, 'bar-hero'),
      h('div', { class: 'legend' }, STATUS_ORDER.filter(s => c[s]).map(s =>
        h('button', { class: 'legend-item', 'data-tip': `Show the ${STATUS[s].label.toLowerCase()} tasks`, onclick: () => showStatus(s) },
          glyph(s), `${c[s]} ${STATUS[s].label.toLowerCase()}`)))));
  renderHealth();
}

function renderHealth() {
  if (!S.derived) return;
  const d = S.derived, saved = new Date(d.savedAt * 1000);
  const problems = (d.problems || []).length;
  fill($('#health'),
    h('span', { class: `pill ${S.offline ? 'bad' : 'live'}`, 'data-tip': S.offline
        ? 'The local server is not answering. Start it again:\npython Docs/Ledger/ledger_web.py'
        : 'This page re-reads the ledger whenever one of its files changes on disk.' },
      h('span', { class: 'dot' }), S.offline ? 'Server not answering' : 'Live'),
    h('span', { class: 'saved', 'data-tip': fmtFull.format(saved) }, `ledger saved ${fmtClock.format(saved)}`),
    problems
      ? h('button', { class: 'pill bad', onclick: () => go('rules') }, `${plural(problems, 'rule problem')}`)
      : h('span', { class: 'pill ok', 'data-tip': 'ledger.py validate finds nothing wrong.' }, 'Rules pass'),
    (d.renderStale || []).length ? h('button', { class: 'pill warn', 'data-tip': d.renderStale.join('\n'), onclick: () => go('rules') }, 'LEDGER.md is stale') : null,
    S.loadError ? h('span', { class: 'pill warn', 'data-tip': S.loadError }, 'Last read failed, retrying') : null);
}

function renderTabs() {
  const open = S.ledger.decisions.filter(d => d.status === 'open').length;
  $('#tabs').replaceChildren(...VIEWS.map(([k, label]) =>
    h('button', { class: `tab${k === S.view ? ' on' : ''}`, 'aria-current': k === S.view ? 'page' : null, onclick: () => go(k) },
      label, k === 'decisions' && open ? h('span', { class: 'tab-badge' }, `${open} open`) : null)));
}

function showStatus(s) {
  S.tasks.status = new Set([s]);
  if (S.view === 'tasks') renderView(); else go('tasks');
}

// ---------------------------------------------------------------- view: now

// A wave whose every task is done or withdrawn (or that has none left).
function waveFinished(w) {
  const c = countStatuses(IX.byWave.get(w) || []);
  return c.done === c.live;
}

function waveStrip() {
  const L = S.ledger, cw = S.derived.currentWave;
  const strip = h('div', { class: 'wavestrip' });
  for (const w of IX.waves) {
    // Finished waves are hidden by default; on a long project most cells are done ones.
    if (!S.now.showFinished && w !== cw && waveFinished(w)) continue;
    const c = countStatuses(IX.byWave.get(w) || []);
    const state = c.live === 0 ? 'empty' : c.done === c.live ? 'finished' : c['in-progress'] ? 'active' : c.blocked ? 'held' : 'open';
    const ed = editorState(w);
    const parts = BAR_ORDER.filter(s => c[s]).map(s => `${c[s]} ${STATUS[s].label.toLowerCase()}`).join(', ');
    strip.append(clickable(h('div', {
        class: `wave-cell ${state}${w === cw ? ' current' : ''}`,
        'data-tip': `Wave ${w}: ${(L.waves || {})[String(w)] || ''}\n${ed.label}\n${parts || 'no tasks'}` },
      h('span', { class: 'wave-n' }, w),
      stackBar(c, 'bar-mini'),
      h('span', { class: 'wave-frac' }, `${c.done}/${c.live}`)),
      () => { S.map.scrollTo = w; go('map'); }));
  }
  return strip;
}

function viewNow() {
  const L = S.ledger, cw = S.derived.currentWave;
  // Department subtasks the hub has sent out are listed apart, the way `ledger.py next` lists
  // them: handed in and waiting for the hub's confirm, or still with their agent.
  const sentOut = t => t.kind && t.dispatchedAt && (t.status === 'in-progress' || t.status === 'blocked');
  const prog = L.tasks.filter(t => t.status === 'in-progress' && !sentOut(t));
  const blocked = L.tasks.filter(t => t.status === 'blocked' && !sentOut(t));
  const dept = L.tasks.filter(sentOut).sort((a, b) => Number(!!b.submittedAt) - Number(!!a.submittedAt));
  // Every unrouted finding, not only blockers: confirm now refuses while any is unrouted.
  const toRoute = t => (t.findings || []).filter(f => !f.routed).length;
  const finishedWaves = IX.waves.filter(w => w !== cw && waveFinished(w));
  const ready = L.tasks.filter(t => IX.ready.has(t.id)).sort((a, b) => a.wave - b.wave);
  const waiting = L.tasks.filter(t => t.status === 'planned' && !IX.ready.has(t.id));
  // Owners come from meta.json's departments list now; the owner's part of a task owned
  // by Engineering is its needs<Owner> field, where it was words inside the owner text.
  const ownerTasks = L.tasks.filter(t => !FINISHED.has(t.status) && (RE_OWNER.test(t.owner) || t[P.needsField]));
  const openDec = L.decisions.filter(d => d.status === 'open');
  const progWaves = [...new Set(prog.map(t => t.wave))].sort((a, b) => a - b);

  let where;
  if (cw == null) where = h('p', { class: 'where' }, 'Every task is finished.');
  else {
    where = h('p', { class: 'where' },
      h('b', {}, `Current wave ${cw}: `), (L.waves || {})[String(cw)] || '',
      h('span', { class: 'muted' }, ' This is the earliest wave that still has unfinished work, the same answer as ledger.py next.'),
      progWaves.length && !(progWaves.length === 1 && progWaves[0] === cw)
        ? h('span', {}, ' Work in progress sits in ', h('b', {}, `wave${progWaves.length > 1 ? 's' : ''} ${progWaves.join(', ')}`), '.') : null);
  }

  const decisionCard = d => clickable(h('div', { class: 'card', 'data-id': d.id },
    h('div', { class: 'card-top' }, h('span', { class: 'glyph dec-open', 'aria-hidden': 'true' }, '◆'), h('span', { class: 'mono id' }, d.id),
      h('span', { class: 'card-meta' }, S.answers[d.id] ? 'Answered, waiting for a session' : `Decision for ${P.owner}`)),
    h('div', { class: 'card-title' }, d.question),
    h('div', { class: 'card-note' }, h('b', {}, 'Recommended: '), d.recommendation)), () => select(d.id));

  return h('div', { class: 'now' },
    h('section', { class: 'panel' },
      h('h2', {}, 'The sequence', h('span', { class: 'h-sub' },
        `one cell per ${S.now.showFinished ? '' : 'unfinished '}wave, in the order the work runs; click one to open it on the map`)),
      finishedWaves.length ? h('div', { class: 'filters' },
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: S.now.showFinished,
          onchange: e => { S.now.showFinished = e.target.checked; renderView(); } }),
          `Show the ${plural(finishedWaves.length, 'finished wave')}`)) : null,
      waveStrip(), where),
    h('div', { class: 'cols cols3' },
      column('In progress', null, prog.map(t => taskCard(t)), 'Nothing is in progress.'),
      column('Blocked', 'Each names the question that holds it.',
        blocked.map(t => taskCard(t, h('div', { class: 'card-note blocked-on' }, h('b', {}, 'Blocked on: '), rich(t.blockedOn)))),
        'Nothing is blocked.'),
      column('Ready to start', 'Planned, with every dependency finished.', ready.map(t => taskCard(t)), 'Nothing is ready: every planned task waits on something.')),
    h('div', { class: 'cols cols3' },
      column('Department work', 'Subtasks a department agent holds, or has handed in for the hub to confirm.',
        dept.map(t => taskCard(t, h('div', { class: 'card-note' }, t.submittedAt
          ? [h('b', {}, 'To confirm: '), `handed in ${t.submittedAt}`,
             toRoute(t) ? `, ${plural(toRoute(t), 'finding')} to route first` : '']
          : [h('b', {}, t.status === 'blocked' ? 'Blocked: ' : 'Running: '), `dispatched by ${t.dispatchedBy || '?'}`,
             t.status === 'blocked' ? [' - ', rich(t.blockedOn)] : null]))),
        'No department subtask is out.'),
      column(`Waiting on ${P.owner}`, `Open decisions, and unfinished tasks owned by ${P.owner} or marked as needing ${P.owner}.`,
        [...openDec.map(decisionCard), ...ownerTasks.map(t => taskCard(t))], `Nothing names ${P.owner} right now.`),
      column('Planned, not startable yet', 'What each one waits on.',
        waiting.map(t => taskCard(t, h('div', { class: 'card-note' }, 'Waits on ', unmetDeps(t).map(ref)))),
        'Every planned task is ready.')));
}

// ---------------------------------------------------------------- view: map

let MAP_CHIPS = new Map();

function mapChip(t) {
  const ready = IX.ready.has(t.id);
  const chip = h('button', {
      class: `chip st-${t.status}${ready ? ' ready' : ''}`, 'data-id': t.id,
      'data-tip': `${t.id}  ${t.title}\n${STATUS[t.status].label}${ready ? ', ready to start' : ''} · size ${t.size} · ${t.owner}`,
      onclick: () => select(S.sel === t.id ? null : t.id),
      onmouseenter: () => { S.map.hover = t.id; applyFocus(); },
      onmouseleave: () => { S.map.hover = null; applyFocus(); } },
    glyph(t.status, ready), h('span', { class: 'chip-id mono' }, t.id.slice(4)), h('span', { class: 'chip-title' }, t.title));
  MAP_CHIPS.set(t.id, chip);
  return chip;
}

function mapColumn(w) {
  const L = S.ledger, tasks = IX.byWave.get(w) || [], c = countStatuses(tasks), ed = editorState(w);
  const col = h('div', { class: `map-col${w === S.derived.currentWave ? ' current' : ''}`, 'data-wave': w },
    h('div', { class: 'map-col-head' },
      h('div', { class: 'map-col-n' }, h('span', { class: 'wave-word' }, 'Wave '), w,
        h('span', { class: 'map-col-frac' }, `${c.done}/${c.live}`)),
      stackBar(c, 'bar-mini'),
      h('div', { class: 'map-col-title', 'data-tip': (L.waves || {})[String(w)] || '' }, (L.waves || {})[String(w)] || ''),
      h('div', { class: `map-col-editor ed-${ed.kind}`, 'data-tip': `Editor: ${ed.text || 'not stated'}` }, ed.label)));
  let epic = null;
  for (const t of tasks) {
    if (t.epic !== epic) {
      epic = t.epic;
      col.append(h('div', { class: 'map-epic', 'data-tip': epicLabel(epic) }, epicLabel(epic)));
    }
    col.append(mapChip(t));
  }
  return col;
}

function viewMap() {
  MAP_CHIPS = new Map();
  const m = S.map;
  const canvas = h('div', { class: `map-canvas${m.dense ? ' dense' : ''}` }, IX.waves.map(mapColumn));
  canvas.append(svg('svg', { class: 'map-edges', 'aria-hidden': 'true' }));
  return h('div', { class: 'map' },
    h('div', { class: 'filters' },
      segmented([['0', 'Detailed'], ['1', 'Compact']], m.dense ? '1' : '0', v => { m.scrollTo = leftmostWave(); m.dense = v === '1'; renderView(); }),
      selectBox('Highlight an epic', [['', 'All epics'], ...S.ledger.epics.map(e => [e.id, `${shortId(e.id)}  ${e.title}`])], m.epic,
                v => { m.epic = v; applyFocus(); }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: m.allEdges, onchange: e => { m.allEdges = e.target.checked; applyFocus(); } }),
        'Draw every dependency'),
      h('span', { class: 'legend map-legend' },
        ['done', 'in-progress', 'blocked', 'planned', 'withdrawn'].map(s => h('span', { class: 'legend-item static' }, glyph(s), STATUS[s].label)),
        h('span', { class: 'legend-item static' }, glyph('planned', true), 'Ready to start'))),
    h('p', { class: 'trace', id: 'trace' }),
    h('div', { class: 'map-scroll' }, canvas));
}

function firstActiveWave() {
  const active = S.ledger.tasks.filter(t => t.status === 'in-progress').map(t => t.wave);
  return active.length ? Math.min(...active) : S.derived.currentWave;
}

// The wave at the left edge of the map right now, so a change of density keeps the same place in view.
function leftmostWave() {
  const sc = $('.map-scroll');
  if (!sc) return null;
  for (const col of sc.querySelectorAll('.map-col')) {
    if (col.offsetLeft + col.offsetWidth > sc.scrollLeft + 24) return Number(col.dataset.wave);
  }
  return null;
}

function afterMap() {
  const sc = $('.map-scroll'), m = S.map;
  let target = m.scrollTo;
  m.scrollTo = null;
  if (target == null && m.left == null) target = firstActiveWave();
  if (target != null) {
    const col = sc.querySelector(`.map-col[data-wave="${target}"]`);
    if (col) sc.scrollLeft = Math.max(0, col.offsetLeft - 24);
  } else sc.scrollLeft = m.left;
  m.left = sc.scrollLeft;
  sc.addEventListener('scroll', () => { m.left = sc.scrollLeft; }, { passive: true });
  applyFocus();
  if (S.sel) revealChip();
}

function revealChip() {
  const chip = MAP_CHIPS.get(S.sel);
  if (chip) chip.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function closure(start, next) {
  const seen = new Set(), queue = [start];
  while (queue.length) {
    for (const n of next(queue.pop())) if (!seen.has(n) && n !== start) { seen.add(n); queue.push(n); }
  }
  return seen;
}

// One task in focus (the selected one, else the hovered one): everything it needs, back to the start,
// and everything that waits on it, forward to the end. The rest dims.
function applyFocus() {
  if (!$('.map-canvas')) return;
  const selected = IX.tasks.has(S.sel) ? S.sel : null;
  const focus = selected || (IX.tasks.has(S.map.hover) ? S.map.hover : null);
  let up = new Set(), down = new Set();
  if (focus) {
    up = closure(focus, id => (IX.tasks.get(id).dependsOn || []).filter(d => IX.tasks.has(d)));
    down = closure(focus, id => IX.dependents.get(id) || []);
  }
  for (const [id, chip] of MAP_CHIPS) {
    const lit = focus ? (id === focus || up.has(id) || down.has(id)) : (!S.map.epic || IX.tasks.get(id).epic === S.map.epic);
    chip.classList.toggle('dim', !lit);
    chip.classList.toggle('is-focus', id === focus);
    chip.classList.toggle('is-sel', id === selected);
  }
  const trace = $('#trace');
  if (focus) {
    const open = [...up].filter(id => !FINISHED.has(IX.tasks.get(id).status)).length;
    fill(trace, h('b', { class: 'mono' }, focus), ` needs ${plural(up.size, 'task')} before it`,
      up.size ? ` (${open ? `${open} not finished` : 'all finished'})` : '', `, and ${plural(down.size, 'task')} wait${down.size === 1 ? 's' : ''} on it.`,
      selected ? null : h('span', { class: 'muted' }, ' Click to keep it traced.'));
  } else {
    fill(trace, h('span', { class: 'muted' }, 'Waves run left to right. Point at a task to trace what it needs and what waits on it; click to open it.'));
  }
  drawEdges(focus, up, down);
}

function drawEdges(focus, up, down) {
  const canvas = $('.map-canvas'), el = $('.map-edges');
  const W = canvas.scrollWidth, H = canvas.scrollHeight;
  el.setAttribute('width', W);
  el.setAttribute('height', H);
  el.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const marker = (id, cls) => svg('marker', { id, viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' },
                                  svg('path', { class: cls, d: 'M0.5,1 L7.5,4 L0.5,7 z' }));
  el.replaceChildren(svg('defs', {}, marker('arrow-strong', 'arrow strong'), marker('arrow-faint', 'arrow faint')));

  const base = canvas.getBoundingClientRect();
  const boxes = new Map();
  const box = id => {
    if (!boxes.has(id)) {
      const r = MAP_CHIPS.get(id).getBoundingClientRect();
      const px = v => Math.round(v * 10) / 10;
      boxes.set(id, { l: px(r.left - base.left), r: px(r.right - base.left), y: px(r.top - base.top + r.height / 2) });
    }
    return boxes.get(id);
  };
  const path = (a, b) => {
    if (Math.abs(a.l - b.l) < 1) {
      // Same wave, same column: loop out through the gutter on the left and back in.
      const g = Math.round(Math.min(24, 9 + Math.abs(b.y - a.y) * 0.1));
      return `M${a.l},${a.y} C${a.l - g},${a.y} ${b.l - g},${b.y} ${b.l - 1},${b.y}`;
    }
    const dx = Math.round(Math.max(20, (b.l - a.r) / 2));
    return `M${a.r},${a.y} C${a.r + dx},${a.y} ${b.l - dx},${b.y} ${b.l - 1},${b.y}`;
  };

  const faint = [], strong = [];
  for (const t of S.ledger.tasks) {
    for (const d of t.dependsOn || []) {
      if (!IX.tasks.has(d)) continue;   // a dependency on a decision has no chip to draw from
      const traced = focus && (((t.id === focus || up.has(t.id)) && up.has(d)) || ((d === focus || down.has(d)) && down.has(t.id)));
      if (traced) strong.push([d, t.id]);
      else if (S.map.allEdges && !focus) faint.push([d, t.id]);
    }
  }
  for (const [a, b] of faint) el.append(svg('path', { class: 'edge faint', d: path(box(a), box(b)), 'marker-end': 'url(#arrow-faint)' }));
  for (const [a, b] of strong) el.append(svg('path', { class: 'edge strong', d: path(box(a), box(b)), 'marker-end': 'url(#arrow-strong)' }));
}

// ---------------------------------------------------------------- view: epics

function epicCard(e) {
  const tasks = IX.byEpic.get(e.id) || [], c = countStatuses(tasks);
  const waves = [...new Set(tasks.map(t => t.wave))].sort((a, b) => a - b);
  const finished = c.live > 0 && c.done === c.live;
  return h('section', { class: 'epic-card', 'data-id': e.id },
    h('button', { class: 'epic-name', onclick: () => select(e.id) }, h('span', { class: 'mono id' }, e.id), h('span', { class: 'epic-title' }, e.title)),
    h('div', { class: 'epic-nums' }, h('span', { class: 'big' }, c.done), ` of ${c.live} done`,
      h('span', { class: 'muted' }, ` · wave${waves.length > 1 ? 's' : ''} ${waves.join(', ')}`)),
    stackBar(c),
    h('div', { class: 'tiles' }, tasks.map(t => h('button', {
        class: `tile st-${t.status}${IX.ready.has(t.id) ? ' ready' : ''}`, 'data-id': t.id,
        'data-tip': `${t.id}  ${t.title}\n${STATUS[t.status].label}${IX.ready.has(t.id) ? ', ready to start' : ''}`,
        'aria-label': `${t.id} ${t.title}, ${STATUS[t.status].label}`, onclick: () => select(t.id) },
      STATUS[t.status].glyph))),
    h('p', { class: 'epic-goal' }, e.goal || ''),
    // The epic's status is worked out from its tasks by ledger.py at every save, so the two cannot disagree.
    h('p', { class: 'epic-foot' }, 'This epic is ', h('b', {}, (STATUS[e.status] || { label: e.status }).label.toLowerCase()), '.'));
}

function viewEpics() {
  return h('div', { class: 'epic-grid' }, S.ledger.epics.map(epicCard));
}

// ---------------------------------------------------------------- view: tasks

function viewTasks() {
  const f = S.tasks, L = S.ledger;
  const again = () => refreshTaskTable();
  const owners = [...new Set(L.tasks.map(t => t.owner))].sort();
  const toggle = (key, label, g) => h('button', {
      class: `toggle${f.status.has(key) ? ' on' : ''}`, 'aria-pressed': String(f.status.has(key)),
      onclick: e => { f.status.has(key) ? f.status.delete(key) : f.status.add(key);
                      e.currentTarget.classList.toggle('on', f.status.has(key));
                      e.currentTarget.setAttribute('aria-pressed', String(f.status.has(key))); again(); } }, g, label);
  return h('div', { class: 'tasks' },
    h('div', { class: 'filters' },
      h('input', { type: 'search', id: 'task-q', placeholder: 'Search id, title, notes, files  ( / )', value: f.q, 'aria-label': 'Search tasks',
                   oninput: e => { f.q = e.target.value; again(); } }),
      h('span', { class: 'toggles' },
        ['in-progress', 'blocked', 'planned', 'done', 'withdrawn'].map(s => toggle(s, STATUS[s].label, glyph(s))),
        toggle('ready', 'Ready to start', glyph('planned', true))),
      selectBox('Epic', [['', 'All epics'], ...L.epics.map(e => [e.id, `${shortId(e.id)}  ${e.title}`])], f.epic, v => { f.epic = v; again(); }),
      selectBox('Wave', [['', 'All waves'], ...IX.waves.map(w => [String(w), `Wave ${w}`])], f.wave, v => { f.wave = v; again(); }),
      selectBox('Owner', [['', 'All owners'], ...owners.map(o => [o, o])], f.owner, v => { f.owner = v; again(); }),
      selectBox('Size', [['', 'All sizes'], ['S', 'Small'], ['M', 'Medium'], ['L', 'Large']], f.size, v => { f.size = v; again(); }),
      h('button', { class: 'btn', onclick: () => { Object.assign(f, { status: new Set(), epic: '', wave: '', owner: '', size: '', q: '' }); renderView(); } }, 'Clear')),
    h('div', { id: 'task-table' }));
}

function filteredTasks() {
  const f = S.tasks, q = f.q.trim().toLowerCase();
  const hay = t => [t.id, t.title, t.owner, t.kind, t[P.needsField], t.heldBy, ...(t.findings || []).map(f => `${f.text} ${f.evidence}`),
                    t.notes, t.evidence, t.blockedOn, t.landed, (t.acceptance || {}).check,
                    (t.acceptance || {}).expect, (t.files || []).join(' ')].join('\n').toLowerCase();
  const rows = S.ledger.tasks.filter(t =>
    (!f.status.size || f.status.has(t.status) || (f.status.has('ready') && IX.ready.has(t.id))) &&
    (!f.epic || t.epic === f.epic) && (!f.wave || String(t.wave) === f.wave) &&
    (!f.owner || t.owner === f.owner) && (!f.size || t.size === f.size) && (!q || hay(t).includes(q)));
  const key = { id: t => t.id, title: t => t.title.toLowerCase(), wave: t => t.wave, size: t => SIZE_RANK[t.size],
                status: t => ATTENTION_RANK[t.status], epic: t => t.epic, owner: t => t.owner }[f.sort];
  return rows.sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : a.id < b.id ? -1 : 1) * f.dir; });
}

function refreshTaskTable() {
  const host = $('#task-table'), f = S.tasks, rows = filteredTasks();
  const th = (key, label) => h('th', { class: f.sort === key ? 'sorted' : '', 'aria-sort': f.sort === key ? (f.dir > 0 ? 'ascending' : 'descending') : null },
    h('button', { onclick: () => { if (f.sort === key) f.dir = -f.dir; else { f.sort = key; f.dir = 1; } refreshTaskTable(); } },
      label, f.sort === key ? (f.dir > 0 ? ' ↑' : ' ↓') : ''));
  const row = t => {
    const unmet = unmetDeps(t), n = (t.dependsOn || []).length;
    return clickable(h('tr', { 'data-id': t.id, class: `st-${t.status}${t.id === S.sel ? ' is-sel' : ''}` },
      h('td', { class: 'nowrap' }, statusTag(t)),
      h('td', { class: 'mono nowrap' }, t.id),
      h('td', { class: 'title-cell' }, t.title),
      h('td', { class: 'nowrap muted', 'data-tip': epicLabel(t.epic) }, shortId(t.epic)),
      h('td', { class: 'num' }, t.wave),
      h('td', {}, t.size),
      h('td', { class: 'muted' }, t.owner),
      h('td', { class: 'nowrap' }, n ? `${n}` : '–', unmet.length ? h('span', { class: 'warn-text' }, ` (${unmet.length} open)`) : null),
      h('td', { class: 'mono muted nowrap' }, t.landed || '–')), () => select(t.id));
  };
  fill(host,
    h('p', { class: 'result-count' }, `${plural(rows.length, 'task')} of ${S.ledger.tasks.length}`),
    rows.length
      ? h('div', { class: 'table-scroll' }, h('table', { class: 'tbl' },
          h('thead', {}, h('tr', {}, th('status', 'Status'), th('id', 'ID'), th('title', 'Task'), th('epic', 'Epic'), th('wave', 'Wave'),
            th('size', 'Size'), th('owner', 'Owner'), h('th', {}, 'Depends on'), h('th', {}, 'Landed'))),
          h('tbody', {}, rows.map(row))))
      : h('p', { class: 'empty' }, 'No task matches these filters.'));
}

// ---------------------------------------------------------------- view: decisions

// ---------------------------------------------------------------- answering a decision

const OTHER = 'Other';   // ledger.py's name for the free-text choice

// What the owner has picked for a decision but not yet saved. Kept outside the DOM: the page redraws whenever the ledger
// changes on disk, and a half-written answer must survive that.
function pickFor(id) {
  if (!S.pick[id]) S.pick[id] = { choice: '', text: '', note: '' };
  return S.pick[id];
}

function redrawAnswers() { renderTabs(); renderView(); renderDrawer(); }

async function sendAnswer(body) {
  if (S.sending) return;
  S.sending = true;
  try {
    const r = await fetch('api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ledger-Answer': '1' }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    S.answers = j.answers || {};
    S.editing.delete(body.id);
    if (body.withdraw) toast(`${body.id}: answer withdrawn`);
    else { delete S.pick[body.id]; toast(`${body.id}: answer saved. A session picks it up from the ledger.`); }
  } catch (e) {
    toast('Not saved: ' + String(e.message || e));
  }
  S.sending = false;
  redrawAnswers();
}

// The options of an open decision as buttons, recommended first, with Other and a note; or, once an answer is
// saved, the answer and where it has got to.
function answerPanel(d) {
  const filed = S.answers[d.id];
  if (filed && !S.editing.has(d.id)) {
    const opt = (d.options || []).find(o => o.label === filed.choice);
    return h('div', { class: 'answer filed' },
      h('div', { class: 'answer-said' }, h('span', { class: 'answer-tick', 'aria-hidden': 'true' }, '✓'), h('b', {}, 'Your answer: '), filed.choice === OTHER ? filed.text : filed.choice),
      opt ? h('div', { class: 'opt-detail' }, opt.detail) : null,
      filed.note ? h('div', { class: 'answer-note' }, h('b', {}, 'Note: '), filed.note) : null,
      h('div', { class: 'answer-wait' }, `Saved ${filed.answeredAt}. Waiting for a session to apply it: it is the first thing `, h('code', {}, 'ledger.py next'), ' prints.'),
      h('div', { class: 'answer-actions' },
        h('button', { class: 'btn', type: 'button', onclick: () => { S.pick[d.id] = { choice: filed.choice, text: filed.text || '', note: filed.note || '' }; S.editing.add(d.id); redrawAnswers(); } }, 'Change answer'),
        h('button', { class: 'btn', type: 'button', onclick: () => sendAnswer({ id: d.id, withdraw: true }) }, 'Withdraw')));
  }
  const p = pickFor(d.id);
  const send = h('button', { class: 'btn primary', type: 'button', onclick: () => sendAnswer({ id: d.id, choice: p.choice, text: p.text, note: p.note }) });
  const syncSend = () => {
    send.disabled = !p.choice || (p.choice === OTHER && !p.text.trim());
    send.textContent = p.choice ? `Save answer: ${p.choice}` : 'Pick an option';
  };
  syncSend();
  const optButton = (label, detail, rec) => h('button', { class: `opt${p.choice === label ? ' on' : ''}`, type: 'button', 'aria-pressed': String(p.choice === label),
      onclick: () => { p.choice = label; redrawAnswers(); } },
    h('span', { class: 'opt-radio', 'aria-hidden': 'true' }),
    h('span', { class: 'opt-body' },
      h('span', { class: 'opt-label' }, label, rec ? h('span', { class: 'opt-rec' }, 'Recommended') : null),
      h('span', { class: 'opt-detail' }, detail)));
  const other = h('textarea', { class: 'answer-text', rows: '3', placeholder: 'Write your own answer', 'aria-label': `${d.id}: your own answer`,
    oninput: e => { p.text = e.target.value; syncSend(); } });
  other.value = p.text;
  const note = h('textarea', { class: 'answer-text', rows: '2', placeholder: 'Optional: a note to go with the answer', 'aria-label': `${d.id}: note`,
    oninput: e => { p.note = e.target.value; } });
  note.value = p.note;
  const options = [...(d.options || [])].sort((a, b) => (b.recommended ? 1 : 0) - (a.recommended ? 1 : 0));
  return h('div', { class: 'answer' },
    options.length ? null : h('p', { class: 'muted' }, 'No options are written for this decision yet. Use Other, or ask a session to write them.'),
    options.map(o => optButton(o.label, o.detail, o.recommended)),
    optButton(OTHER, 'None of these: write the answer yourself.', false),
    p.choice === OTHER ? other : null,
    p.choice && p.choice !== OTHER ? note : null,
    h('div', { class: 'answer-actions' }, send,
      S.editing.has(d.id) ? h('button', { class: 'btn', type: 'button', onclick: () => { S.editing.delete(d.id); delete S.pick[d.id]; redrawAnswers(); } }, 'Keep the saved answer') : null));
}

// ---------------------------------------------------------------- view: decisions

function viewDecisions() {
  const openList = S.ledger.decisions.filter(d => d.status === 'open');
  const decided = S.ledger.decisions.filter(d => d.status !== 'open');
  const open = openList.length, toAnswer = openList.filter(d => !S.answers[d.id]).length;
  const asking = (d, n) => {
    const gates = IX.dependents.get(d.id) || [];
    return h('section', { class: `decision open asking${S.answers[d.id] ? ' answered' : ''}`, 'data-id': d.id },
      h('div', { class: 'card-top' }, h('span', { class: 'glyph dec-open', 'aria-hidden': 'true' }, '◆'),
        h('span', { class: 'mono id' }, d.id), h('span', { class: 'card-meta' }, `Decision ${n + 1} of ${open}`)),
      h('h2', { class: 'ask-q' }, d.question),
      h('p', { class: 'prose ask-why' }, rich(d.recommendation)),
      gates.length ? h('div', { class: 'card-note' }, 'Cannot start until this is decided: ', gates.map(ref)) : null,
      answerPanel(d),
      h('div', { class: 'ask-foot' }, h('button', { class: 'inref', type: 'button', onclick: () => select(d.id) }, 'Where this came from')));
  };
  return h('div', { class: 'decisions' },
    h('p', { class: 'where' },
      open ? h('b', {}, `${plural(open, 'decision')} open` + (toAnswer === open ? '. ' : `, ${toAnswer} still to answer. `)) : 'Every decision has a ruling. ',
      h('span', { class: 'muted' }, open ? 'Pick an option and save it here; a session applies it. ' : '', 'A task that depends on a decision cannot start until it is decided.')),
    openList.map(asking),
    decided.map(d => clickable(h('section', { class: `decision ${d.status}`, 'data-id': d.id },
      h('div', { class: 'card-top' }, h('span', { class: `glyph dec-${d.status}`, 'aria-hidden': 'true' }, '◆'),
        h('span', { class: 'mono id' }, d.id), h('span', { class: 'card-meta' }, `Decided ${d.ruledOn || ''}${d.ruledBy ? ' by ' + d.ruledBy : ''}`)),
      h('div', { class: 'card-title' }, d.question),
      h('div', { class: 'card-note' }, h('b', {}, 'Ruling: '), d.ruling),
      (IX.dependents.get(d.id) || []).length ? h('div', { class: 'card-note' }, 'Gates ', (IX.dependents.get(d.id) || []).map(ref)) : null),
      () => select(d.id))));
}

// ---------------------------------------------------------------- view: activity

function landedSeries() {
  const perDay = new Map();
  let outside = 0;
  for (const t of S.ledger.tasks) {
    if (!t.landed) continue;
    const c = IX.commits.get(t.landed);
    if (!c || !c.date) { outside++; continue; }
    const k = dayKey(new Date(c.date));
    perDay.set(k, (perDay.get(k) || 0) + 1);
  }
  const days = [];
  if (perDay.size) {
    const keys = [...perDay.keys()].sort();
    const newest = S.commits.commits[0] && S.commits.commits[0].date ? dayKey(new Date(S.commits.commits[0].date)) : keys[keys.length - 1];
    const end = newest > keys[keys.length - 1] ? newest : keys[keys.length - 1];
    const [y, mo, d] = keys[0].split('-').map(Number);
    let cum = 0;
    for (let i = 0; i < 400; i++) {
      const date = new Date(y, mo - 1, d + i), k = dayKey(date);
      if (k > end) break;
      const n = perDay.get(k) || 0;
      cum += n;
      days.push({ k, date, n, cum });
    }
  }
  return { days, perDay, outside };
}

// Tasks landed, cumulative. One series, so no legend: the card title names it. The feed below is its
// table twin, day by day.
function landedChart(host, series) {
  const days = series.days, n = days.length;
  if (!n) { host.replaceChildren(h('p', { class: 'empty' }, 'No landed task points at a commit in the log window yet.')); return; }
  const W = Math.max(320, host.clientWidth), H = 230, m = { l: 40, r: 48, t: 14, b: 28 };
  const top = days[n - 1].cum;
  const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find(s => top / s <= 4) || 1000;
  const ymax = Math.max(step, Math.ceil(top / step) * step);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const x = i => m.l + (n === 1 ? pw / 2 : (i * pw) / (n - 1));
  const y = v => m.t + ph - (v / ymax) * ph;
  const root = svg('svg', { class: 'chart', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
                            'aria-label': `Tasks landed, cumulative: ${top} by ${fmtShortDay.format(days[n - 1].date)}` });
  for (let v = 0; v <= ymax; v += step) {
    root.append(svg('line', { class: v === 0 ? 'axis' : 'grid', x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }),
                svg('text', { class: 'tick', x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end' }, v));
  }
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 72))));
  days.forEach((d, i) => {
    if (i % every === 0) root.append(svg('text', { class: 'tick', x: x(i), y: H - 8, 'text-anchor': 'middle' }, fmtShortDay.format(d.date)));
  });
  const pts = days.map((d, i) => `${x(i).toFixed(1)},${y(d.cum).toFixed(1)}`);
  const cross = svg('line', { class: 'cross', y1: m.t, y2: m.t + ph, visibility: 'hidden' });
  const dot = svg('circle', { class: 'end', r: 4, visibility: 'hidden' });
  const hit = svg('rect', { x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent' });
  root.append(
    svg('path', { class: 'area', d: `M${x(0)},${y(0)} L${pts.join(' L')} L${x(n - 1)},${y(0)} Z` }),
    svg('path', { class: 'line', d: `M${pts.join(' L')}` }),
    svg('circle', { class: 'end', cx: x(n - 1), cy: y(top), r: 4 }),
    svg('text', { class: 'end-label', x: x(n - 1) + 9, y: y(top) + 4 }, top),
    cross, dot, hit);
  hit.addEventListener('mousemove', e => {
    const r = root.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - m.l) / pw) * (n - 1))));
    const d = days[i];
    for (const [k, v] of [['x1', x(i)], ['x2', x(i)], ['visibility', 'visible']]) cross.setAttribute(k, v);
    for (const [k, v] of [['cx', x(i)], ['cy', y(d.cum)], ['visibility', 'visible']]) dot.setAttribute(k, v);
    showTip(`${fmtDay.format(d.date)}\n${d.n} landed that day\n${d.cum} in total`, r.left + x(i), r.top + y(d.cum) - 6, r.top + y(d.cum) + 6);
  });
  hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); hideTip(); });
  host.replaceChildren(root);
}

function commitKind(c) {
  if (c.names) return 'named';
  if ((c.roles || []).length) return 'role';
  return 'unnamed';
}

function viewActivity() {
  const C = S.commits;
  if (!C) return h('p', { class: 'loading' }, `Reading ${P.vcsCmd} log...`);
  if (C.error) {
    return h('section', { class: 'panel error' }, h('h2', {}, 'The commit log could not be read'),
      h('pre', {}, C.error), h('p', {}, `This view needs the ${P.vcsCmd} command to answer. The rest of the page does not.`),
      h('button', { class: 'btn', onclick: () => refreshCommits() }, 'Try again'));
  }
  const all = C.commits, kinds = { named: 0, role: 0, unnamed: 0 };
  for (const c of all) kinds[commitKind(c)]++;
  const recorded = all.filter(c => commitKind(c) === 'unnamed' && c.outsideReason != null).length;
  const series = landedSeries();
  const shown = all.filter(c => S.activity.filter === 'all' || (S.activity.filter === 'named' ? commitKind(c) !== 'unnamed' : commitKind(c) === 'unnamed'));
  const tile = (n, label, sub) => h('div', { class: 'stat' }, h('div', { class: 'stat-n' }, n), h('div', { class: 'stat-label' }, label), sub && h('div', { class: 'stat-sub' }, sub));

  const feed = h('div', { class: 'feed' });
  let day = null;
  const perDayCommits = new Map();
  for (const c of all) { const k = c.date ? dayKey(new Date(c.date)) : '?'; perDayCommits.set(k, (perDayCommits.get(k) || 0) + 1); }
  for (const c of shown) {
    const date = c.date ? new Date(c.date) : null, k = date ? dayKey(date) : '?';
    if (k !== day) {
      day = k;
      const landed = series.perDay.get(k) || 0;
      feed.append(h('h3', { class: 'feed-day' }, date ? fmtDay.format(date) : 'No date',
        h('span', { class: 'h-sub' }, `${plural(perDayCommits.get(k) || 0, 'commit')}${landed ? `, ${plural(landed, 'task')} landed` : ''}`)));
    }
    const kind = commitKind(c);
    feed.append(clickable(h('div', { class: `feed-row ${kind}`, 'data-id': c.id },
      h('span', { class: 'feed-time' }, date ? fmtClock.format(date) : ''),
      h('span', { class: 'mono feed-id' }, c.id),
      h('span', { class: 'feed-what' }, kind === 'named' ? ref(c.names)
        : kind === 'role' ? h('span', { class: 'tag role' }, `Role: ${c.roles.join(', ')}`)
        : h('span', { class: 'tag quiet', 'data-tip': c.outsideReason != null ? `Recorded in outsideCommits:\n${c.outsideReason}` : 'Names no task and no role. Out of the ledger’s scope unless it touched an in-progress task’s files.' },
            c.outsideReason != null ? 'recorded outside' : 'names nothing')),
      h('span', { class: 'feed-subject' }, c.subject)), () => select(c.id)));
  }

  return h('div', { class: 'activity' },
    h('div', { class: 'stats' },
      tile(all.length, `commits after ${C.auditFrom}`, C.reachesMark ? `head is ${C.head}` : 'the log does not reach the audit mark'),
      tile(kinds.named, 'name a task or epic'),
      tile(kinds.role, 'carry a role instead'),
      tile(kinds.unnamed, 'name nothing', `${recorded} recorded in outsideCommits`)),
    h('section', { class: 'panel' },
      h('h2', {}, 'Tasks landed, cumulative', h('span', { class: 'h-sub' }, 'each task counted on the day of the commit it landed in')),
      h('div', { id: 'landed-chart' }),
      series.outside ? h('p', { class: 'muted small' }, `${plural(series.outside, 'landed task')} point${series.outside === 1 ? 's' : ''} at a commit outside the log window and ${series.outside === 1 ? 'is' : 'are'} not drawn.`) : null),
    h('div', { class: 'filters' },
      segmented([['all', 'All commits'], ['named', 'Ledger work'], ['unnamed', 'Names nothing']], S.activity.filter, v => { S.activity.filter = v; renderView(); }),
      h('span', { class: 'muted' }, `${plural(shown.length, 'commit')} shown`),
      h('button', { class: 'btn', onclick: () => refreshCommits() }, `Re-read ${P.vcsCmd} log`),
      h('span', { class: 'muted small' }, `read ${fmtClock.format(new Date(C.readAt * 1000))}`)),
    feed);
}

function afterActivity() {
  const host = $('#landed-chart');
  if (host) landedChart(host, landedSeries());
}

async function refreshCommits() {
  await loadCommits(true);
  if (S.view === 'activity') renderView();
  renderDrawer();
}

// ---------------------------------------------------------------- view: rules

function viewRules() {
  const L = S.ledger, d = S.derived;
  const dl = obj => h('dl', { class: 'defs' }, Object.entries(obj || {}).map(([k, v]) => [h('dt', {}, k), h('dd', {}, rich(v))]));
  return h('div', { class: 'rules' },
    (d.problems || []).length ? h('section', { class: 'panel error' }, h('h2', {}, 'Rule problems', h('span', { class: 'h-sub' }, 'what ledger.py validate refuses right now')),
      h('ul', {}, d.problems.map(p => h('li', {}, rich(p))))) : null,
    (d.renderStale || []).length ? h('section', { class: 'panel warn' }, h('h2', {}, 'LEDGER.md does not match the ledger files'),
      h('p', {}, d.renderStale.join(' ')), cmdRow(`${TOOL} render`)) : null,
    h('section', { class: 'panel' }, h('h2', {}, 'This page is read-only'),
      h('p', {}, 'ledger.py is the only tool that changes state or commits. Open a task to copy the command for its next step. The page reloads itself when the ledger changes.'),
      cmdRow(`${TOOL} next`, 'where the work is'), cmdRow(`${TOOL} check`, 'validate, render check and audit: the ledger gate')),
    h('section', { class: 'panel' }, h('h2', {}, L.title || 'Ledger', h('span', { class: 'h-sub' }, `audit window starts after ${L.auditFrom}`)),
      h('p', {}, rich(L.summary || ''))),
    h('section', { class: 'panel' }, h('h2', {}, 'Waves'),
      h('div', { class: 'table-scroll' }, h('table', { class: 'tbl' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Wave'), h('th', {}, 'What'), h('th', {}, 'Editor'), h('th', {}, 'Done'))),
        h('tbody', {}, IX.waves.map(w => { const c = countStatuses(IX.byWave.get(w) || []);
          return h('tr', {}, h('td', { class: 'num' }, w), h('td', {}, (L.waves || {})[String(w)] || ''),
                   h('td', { class: 'muted' }, (L.waveEditor || {})[String(w)] || ''), h('td', { class: 'nowrap' }, `${c.done} of ${c.live}`)); }))))),
    h('section', { class: 'panel' }, h('h2', {}, 'Gates'), dl(L.gates)),
    h('section', { class: 'panel' }, h('h2', {}, 'Conventions'), dl(L.conventions)),
    (L.outsideCommits || []).length ? h('section', { class: 'panel' },
      h('h2', {}, 'Outside commits recorded', h('span', { class: 'h-sub' }, 'made without the tool, on files of a task that was in progress')),
      h('dl', { class: 'defs' }, L.outsideCommits.map(o => [h('dt', { class: 'mono' }, o.id),
        h('dd', {}, h('b', {}, o.subject), h('br'), o.reason, h('span', { class: 'muted' }, ` (recorded ${o.recordedOn})`))]))) : null);
}

// ---------------------------------------------------------------- drawer

function section(title, body) {
  return body ? h('section', { class: 'd-sec' }, h('h3', {}, title), body) : null;
}

function drawerTask(t) {
  const e = IX.epics.get(t.epic), ed = editorState(t.wave), ready = IX.ready.has(t.id);
  const deps = t.dependsOn || [], needed = IX.dependents.get(t.id) || [], unmet = unmetDeps(t);
  const landed = t.landed && IX.commits.get(t.landed);
  const named = IX.commitsByTask.get(t.id) || [];
  const shots = imagesIn(t.evidence, t.notes);
  const cmds = [];
  if (t.kind) {
    // A department subtask is dispatched and confirmed by the hub; its agent starts it,
    // records findings and hands it in.
    if (t.status === 'planned' && ready) cmds.push(cmdRow(`${TOOL} dispatch ${t.id} --by "<hub session title>"`, 'prints the department agent to start and its prompt'));
    if (t.status === 'planned' && !ready) cmds.push(h('p', { class: 'muted' }, 'Cannot dispatch yet: what it covers or depends on is still open.'));
    if (t.status === 'in-progress' && t.submittedAt) {
      for (const f of (t.findings || []).filter(f => f.severity === 'blocker' && !f.routed)) {
        cmds.push(cmdRow(`${TOOL} route ${t.id}#${f.n} --to <task> --by "<hub session title>"`, 'or --reject "<why>"'));
      }
      cmds.push(cmdRow(`${TOOL} confirm ${t.id} --by "<hub session title>" --evidence ""`, 're-open one pointer first and say what it showed'));
    }
    if ((t.status === 'in-progress' || t.status === 'blocked') && !t.submittedAt && t.dispatchedAt) {
      cmds.push(cmdRow(`${TOOL} dispatch ${t.id} --by "<hub session title>" --restart`, 'only if its agent is gone'));
    }
  } else {
    if (t.status === 'planned' && ready) cmds.push(cmdRow(`${TOOL} start ${t.id}`, 'planned to in progress'));
    if (t.status === 'planned' && !ready) cmds.push(h('p', { class: 'muted' }, 'Cannot start yet: ', unmet.map(ref), ' still open.'));
    if (t.status === 'blocked') cmds.push(cmdRow(`${TOOL} start ${t.id}`, 'back to in progress, clears the block'));
    if (t.status === 'in-progress') {
      cmds.push(cmdRow(`${TOOL} done ${t.id} --evidence "ran-it: "`, 'say what ran and what it showed'));
      cmds.push(cmdRow(`${TOOL} block ${t.id} --on ""`, 'name the question that holds it'));
    }
  }
  cmds.push(cmdRow(`${TOOL} show ${t.id}`));
  const findings = t.findings || [];
  const routedText = f => !f.routed ? 'not routed' : f.routed.task ? ['routed to ', ref(f.routed.task)] : `rejected: ${f.routed.rejected}`;
  return h('div', {},
    h('div', { class: 'd-head' }, h('span', { class: 'mono id' }, t.id), statusTag(t)),
    h('h2', { class: 'd-title' }, t.title),
    h('dl', { class: 'meta' },
      h('dt', {}, 'Epic'), h('dd', {}, e ? h('button', { class: 'inref', onclick: () => select(e.id) }, `${e.id} ${e.title}`) : t.epic),
      h('dt', {}, 'Wave'), h('dd', {}, `${t.wave}: ${(S.ledger.waves || {})[String(t.wave)] || ''}`),
      h('dt', {}, 'Editor'), h('dd', {}, ed.text || 'not stated'),
      h('dt', {}, 'Owner'), h('dd', {}, t.owner),
      t.kind ? [h('dt', {}, 'Subtask'), h('dd', {}, t.kind, t.parent ? [' of ', ref(t.parent)] : null)] : null,
      t.covers ? [h('dt', {}, 'Covers'), h('dd', {}, t.covers.length ? h('div', { class: 'refs' }, t.covers.map(ref)) : 'no other task')] : null,
      t.dispatchedAt ? [h('dt', {}, 'Dispatched'), h('dd', {}, `${t.dispatchedAt} by ${t.dispatchedBy || '?'}`)] : null,
      t.heldBy ? [h('dt', {}, 'Held by'), h('dd', {}, t.heldBy)] : null,
      t.dump ? [h('dt', {}, 'Graph dump'), h('dd', { class: 'mono' }, t.dump)] : null,
      t[P.needsField] ? [h('dt', {}, `Needs ${P.owner}`), h('dd', {}, t[P.needsField])] : null,
      h('dt', {}, 'Size'), h('dd', {}, { S: 'Small', M: 'Medium', L: 'Large' }[t.size] || t.size)),
    t.blockedOn ? h('div', { class: 'callout blocked' }, h('b', {}, 'Blocked on: '), rich(t.blockedOn)) : null,
    t.submittedAt && t.status === 'in-progress' ? h('div', { class: 'callout' }, h('b', {}, 'Handed in, waiting for the hub: '),
      `${t.submittedAt}. Re-open one pointer, route each blocker finding, then confirm.`) : null,
    section('Depends on', deps.length ? h('div', { class: 'refs' }, deps.map(ref)) : h('p', { class: 'muted' }, 'Nothing.')),
    section('Needed by', needed.length ? h('div', { class: 'refs' }, needed.map(ref)) : h('p', { class: 'muted' }, 'Nothing waits on this.')),
    section('Check', h('p', { class: 'prose' }, rich((t.acceptance || {}).check))),
    section('Passing shows', h('p', { class: 'prose' }, rich((t.acceptance || {}).expect))),
    t.notes ? section('Notes', h('p', { class: 'prose' }, rich(t.notes))) : null,
    findings.length ? section(`Findings (${findings.length})`, h('ul', { class: 'commit-list' }, findings.map(f =>
      h('li', {}, h('b', {}, `#${f.n} ${f.severity}: `), rich(f.text), h('div', { class: 'muted' }, rich(f.evidence),
        ` - ${f.by || '?'}, ${f.at || ''} - `, routedText(f)))))) : null,
    t.evidence ? section(t.kind ? 'Evidence handed in' : 'Evidence', h('p', { class: 'prose' }, rich(t.evidence))) : null,
    t.confirmed ? section('Confirmed by the hub', h('p', { class: 'prose' }, `${t.confirmed.by}, ${t.confirmed.at}: `, rich(t.confirmed.evidence))) : null,
    shots.length ? h('div', { class: 'shots' }, shots.map(name => h('a', { href: `image/${encodeURIComponent(name)}`, target: '_blank', rel: 'noopener', 'data-tip': name },
      h('img', { src: `image/${encodeURIComponent(name)}`, alt: name, loading: 'lazy' })))) : null,
    t.landed ? section('Landed', h('p', {}, ref(t.landed), landed && landed.date ? h('span', { class: 'muted' }, ` ${fmtFull.format(new Date(landed.date))}`) : null)) : null,
    named.length ? section(`Commits naming this task (${named.length})`, h('ul', { class: 'commit-list' }, named.map(c => h('li', {}, ref(c.id), ' ', c.subject)))) : null,
    section(`Files it may edit (${(t.files || []).length})`, h('ul', { class: 'files mono' }, (t.files || []).map(f => h('li', {}, f)))),
    section('Commands', h('div', {}, cmds)));
}

function drawerEpic(e) {
  const tasks = IX.byEpic.get(e.id) || [], c = countStatuses(tasks);
  const hasPlan = (S.derived.plans || {})[e.plan];
  return h('div', {},
    h('div', { class: 'd-head' }, h('span', { class: 'mono id' }, e.id), h('span', { class: 'tag' }, `Ledger marks it ${e.status}`)),
    h('h2', { class: 'd-title' }, e.title),
    h('p', { class: 'd-progress' }, h('b', {}, `${c.done} of ${c.live} done`), stackBar(c)),
    section('Goal', h('p', { class: 'prose' }, rich(e.goal || ''))),
    section('Plan', h('p', {}, hasPlan ? h('a', { class: 'inref mono', href: `plan?path=${encodeURIComponent(e.plan)}`, target: '_blank', rel: 'noopener' }, e.plan)
                                        : h('span', { class: 'mono' }, e.plan), hasPlan ? h('span', { class: 'muted' }, ' opens as plain text') : h('span', { class: 'warn-text' }, ' file not found'))),
    section(`Tasks (${tasks.length})`, h('ul', { class: 'task-list' }, tasks.map(t => clickable(h('li', { class: `st-${t.status}` },
      glyph(t.status, IX.ready.has(t.id)), h('span', { class: 'mono' }, t.id.slice(4)), h('span', { class: 'tl-title' }, t.title), h('span', { class: 'muted' }, `W${t.wave}`)),
      () => select(t.id))))));
}

function drawerDecision(d) {
  const gates = IX.dependents.get(d.id) || [];
  const today = dayKey(new Date());
  return h('div', {},
    h('div', { class: 'd-head' }, h('span', { class: 'mono id' }, d.id),
      h('span', { class: 'tag' }, h('span', { class: `glyph dec-${d.status}`, 'aria-hidden': 'true' }, '◆'), d.status === 'decided' ? 'Decided' : 'Open')),
    h('h2', { class: 'd-title' }, d.question),
    section('Recommended', h('p', { class: 'prose' }, rich(d.recommendation))),
    d.ruling ? section('Ruling', h('p', { class: 'prose' }, rich(d.ruling), h('span', { class: 'muted' }, ` ${d.ruledBy ? d.ruledBy + ', ' : ''}${d.ruledOn || ''}`))) : null,
    d.source ? section('Source', h('p', { class: 'mono' }, d.source)) : null,
    section('Gates', gates.length ? h('div', { class: 'refs' }, gates.map(ref)) : h('p', { class: 'muted' }, 'No task depends on it.')),
    d.status === 'open' ? section('Your answer', answerPanel(d)) : null,
    d.status === 'open' ? section('Commands', S.answers[d.id]
      ? cmdRow(`${TOOL} apply ${d.id}`, 'makes the saved answer the ruling')
      : cmdRow(`${TOOL} decide ${d.id} --ruling "" --by "${P.owner}" --date ${today}`, `records the ruling as ${P.owner}’s`)) : null);
}

function drawerCommit(c) {
  const kind = commitKind(c);
  return h('div', {},
    h('div', { class: 'd-head' }, h('span', { class: 'mono id' }, c.id), c.date ? h('span', { class: 'muted' }, fmtFull.format(new Date(c.date))) : null),
    h('h2', { class: 'd-title' }, c.subject || '(no message)'),
    section('Names', kind === 'named' ? h('div', { class: 'refs' }, ref(c.names))
      : kind === 'role' ? h('p', {}, `No task. Role: ${c.roles.join(', ')}`)
      : h('p', {}, 'No task, epic or role.', c.outsideReason != null ? h('span', {}, ' Recorded in outsideCommits: ', c.outsideReason) : null)),
    section('Message', h('pre', { class: 'prose' }, rich(c.message || ''))),
    section('Commands', cmdRow(P.showCmd.replace('{id}', c.id.replace(/^git:/, '')), 'the files it touched')));
}

function renderDrawer() {
  const el = $('#drawer'), id = S.sel;
  document.body.classList.toggle('has-drawer', !!id);
  if (!id) { el.hidden = true; el.replaceChildren(); el.dataset.for = ''; return; }
  const keep = el.dataset.for === id ? el.scrollTop : 0;
  let body;
  if (IX.tasks.has(id)) body = drawerTask(IX.tasks.get(id));
  else if (IX.epics.has(id)) body = drawerEpic(IX.epics.get(id));
  else if (IX.decisions.has(id)) body = drawerDecision(IX.decisions.get(id));
  else if (IX.commits.has(id)) body = drawerCommit(IX.commits.get(id));
  else body = h('p', { class: 'muted' }, new RegExp(`^(?:${P.commitId})$`).test(id) && !S.commits
    ? `Reading ${P.vcsCmd} log...` : `Nothing in the ledger is called ${id}.`);
  el.replaceChildren(h('button', { class: 'drawer-close', 'aria-label': 'Close', 'data-tip': 'Close (Esc)', onclick: () => select(null) }, '×'), body);
  el.hidden = false;
  el.dataset.for = id;
  el.scrollTop = keep;
}

// ---------------------------------------------------------------- render and load

const VIEW = {
  now: [viewNow], map: [viewMap, afterMap], epics: [viewEpics], tasks: [viewTasks, refreshTaskTable],
  decisions: [viewDecisions], activity: [viewActivity, afterActivity], rules: [viewRules],
};

function renderView(refresh) {
  const host = $('#view'), [build, after] = VIEW[S.view];
  const same = host.dataset.view === S.view;
  const y = same ? window.scrollY : 0;
  try {
    // A ledger change while the task search has focus must not rebuild the box being typed in.
    if (refresh && same && S.view === 'tasks' && $('#task-table')) refreshTaskTable();
    else { host.replaceChildren(build()); if (after) after(); }
  } catch (e) {
    console.error(e);
    host.replaceChildren(h('section', { class: 'panel error' }, h('h2', {}, 'This view failed to draw'), h('pre', {}, String((e && e.stack) || e))));
  }
  host.dataset.view = S.view;
  markSelected();
  window.scrollTo(0, y);
}

function renderAll(refresh) {
  renderHeader();
  renderTabs();
  renderView(refresh);
  renderDrawer();
}

async function loadState() {
  const r = await fetch('api/state');
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  applyProfile(j.derived.profile);
  document.title = j.ledger.title || 'Ledger';
  S.ledger = j.ledger;
  S.derived = j.derived;
  S.answers = j.answers || {};
  S.stamp = j.derived.stamp;
  buildIndex();
}

async function loadCommits(force) {
  try {
    const r = await fetch('api/commits' + (force ? '?refresh=1' : ''));
    S.commits = await r.json();
  } catch (e) {
    S.commits = { error: String(e), commits: [], readAt: Date.now() / 1000 };
  }
  indexCommits();
}

async function poll() {
  try {
    const r = await fetch('api/stamp');
    const j = await r.json();
    S.offline = false;
    if (j.stamp !== S.stamp) {
      try {
        await loadState();
        S.loadError = null;
        renderAll(true);
        refreshCommits();   // a ledger change usually rides with a commit
      } catch (e) {
        S.loadError = String(e.message || e);   // most often a read that landed mid-write; the next poll retries
      }
    }
  } catch (e) {
    S.offline = true;
  }
  renderHealth();
  setTimeout(poll, POLL_MS);
}

let resizeTimer = 0;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (S.view === 'map') applyFocus();
    if (S.view === 'activity') afterActivity();
  }, 120);
});

window.addEventListener('hashchange', onRoute);

document.addEventListener('keydown', e => {
  const typing = e.target instanceof Element && e.target.matches('input, textarea, select');
  if (e.key === 'Escape' && S.sel) select(null);
  if (e.key === '/' && !typing) {
    e.preventDefault();
    if (S.view !== 'tasks') go('tasks');
    setTimeout(() => { const q = $('#task-q'); if (q) q.focus(); }, 40);   // after the hashchange has drawn the view
  }
});

(async function start() {
  initTips();
  parseHash();
  try {
    await loadState();
  } catch (e) {
    $('#view').replaceChildren(h('section', { class: 'panel error' }, h('h2', {}, 'The ledger could not be read'), h('pre', {}, String(e.message || e)),
      h('p', {}, 'The page keeps trying every two seconds.')));
    setTimeout(poll, POLL_MS);
    return;
  }
  renderAll();
  loadCommits(false).then(() => { if (S.view === 'activity') renderView(); renderDrawer(); });
  setTimeout(poll, POLL_MS);
})();
