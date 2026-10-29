"""Click-and-drag availability grid (Streamlit Components v2, no build step).

Each date is a column; each row is one time slot (15 min by default).
Cell codes: '0' unavailable, '1' available, '2' preferred.

The browser keeps the working copy while you paint and sends it to Python at
the end of each stroke. Nothing is saved to the database until the faculty
member presses "Submit availability".
"""
from __future__ import annotations

import json

import streamlit as st

_CSS = r"""
:host { all: initial; }
.ag { font-family: "Public Sans", system-ui, sans-serif; color: #1B2420; font-size: 14px; }
.ag * { box-sizing: border-box; }
.ag-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 2px 0 12px; }
.ag-bar .lbl { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #5B6B63; margin-right: 4px; font-weight: 600;}
.ag-mode { display: inline-flex; align-items: center; gap: 8px; border: 1px solid #CFCBBF; background: #fff;
  border-radius: 999px; padding: 7px 14px 7px 10px; cursor: pointer; font: inherit; color: inherit; min-height: 40px; }
.ag-mode[aria-pressed="true"] { border-color: #0A5C41; box-shadow: inset 0 0 0 1px #0A5C41; background: #F2F8F4; font-weight: 600; }
.ag-mode:focus-visible, .ag-cell:focus-visible, .ag-mini:focus-visible { outline: 3px solid #1F5F8B; outline-offset: 1px; }
.sw { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #B9B4A6; display: inline-block; }
.sw.s1 { background: #8FCFAE; border-color: #4E9C76; }
.sw.s2 { background: #0A5C41 url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18'><text x='9' y='13.5' font-size='12' text-anchor='middle' fill='white'>★</text></svg>") center/contain no-repeat; border-color: #0A5C41; }
.sw.s0 { background: repeating-linear-gradient(135deg,#fff 0 4px,#ECEAE3 4px 6px); }
.ag-wrap { display: grid; gap: 0; border: 1px solid #DEDBD1; border-radius: 8px; background: #fff; overflow: hidden; }
.ag-head { position: sticky; top: 0; background: #FBFAF7; border-bottom: 1px solid #DEDBD1; padding: 8px 6px 6px; text-align: center; z-index: 1; }
.ag-head .d { font-weight: 700; font-size: 13.5px; }
.ag-head .s { font-size: 11.5px; color: #5B6B63; margin-top: 1px; line-height: 1.25; }
.ag-head .acts { display: flex; gap: 4px; justify-content: center; margin-top: 6px; flex-wrap: wrap; }
.ag-mini { font: inherit; font-size: 11.5px; padding: 3px 8px; border-radius: 4px; border: 1px solid #DEDBD1; background: #fff; cursor: pointer; color: #1B2420; }
.ag-mini:hover { border-color: #0A5C41; }
.ag-corner { background: #FBFAF7; border-bottom: 1px solid #DEDBD1; }
.ag-time { font-size: 11.5px; color: #5B6B63; text-align: right; white-space: nowrap; padding: 0 8px 0 4px; line-height: 1; transform: translateY(-6px);
  font-variant-numeric: tabular-nums; user-select: none; touch-action: pan-y; }
.ag-time.hr { color: #1B2420; font-weight: 600; }
.ag-cell { height: 26px; border-left: 1px solid #ECEAE3; border-top: 1px solid #F3F1EC; background: #fff;
  cursor: crosshair; touch-action: none; user-select: none; position: relative; }
.ag-cell.hr { border-top-color: #D9D5CA; }
.ag-cell[data-v="0"] { background: repeating-linear-gradient(135deg,#fff 0 6px,#F6F5F1 6px 8px); }
.ag-cell[data-v="1"] { background: #8FCFAE; border-top-color: #A9DCC1; }
.ag-cell[data-v="2"] { background: #0A5C41; border-top-color: #17704F; }
.ag-cell[data-v="2"]::after { content: "★"; color: #E8F4EC; font-size: 10px; position: absolute; right: 5px; top: 6px; }
.ag-cell.ro { cursor: default; }
.ag-foot { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: #5B6B63; margin-top: 10px; align-items: center; }
.ag-foot span { display: inline-flex; gap: 6px; align-items: center; }
.ag-tip { font-size: 12.5px; color: #5B6B63; margin: 0 0 10px; }
@media (max-width: 560px) { .ag-cell { height: 30px; } .ag-head .d { font-size: 12.5px; } }
"""

_JS = r"""
export default function (component) {
  const { data, setStateValue, parentElement } = component;
  let root = parentElement.querySelector('.ag');
  if (!root) { root = document.createElement('div'); root.className = 'ag'; parentElement.appendChild(root); }
  const st = root.__st || (root.__st = { rev: null, cells: {}, mode: '1' });
  if (st.rev !== data.rev) { st.rev = data.rev; st.cells = Object.assign({}, data.cells); }
  const RO = !!data.readonly;

  const send = () => setStateValue('cells', { rev: st.rev, cells: st.cells });
  const setCell = (k, i, v) => {
    const a = st.cells[k].split(''); if (a[i] === v) return false; a[i] = v; st.cells[k] = a.join(''); return true;
  };
  const fmt = (startMin, i, step) => {
    const t = startMin + i * step, h = Math.floor(t / 60), m = t % 60;
    return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
  };
  const names = { '0': 'unavailable', '1': 'available', '2': 'preferred' };

  root.replaceChildren();
  const tip = document.createElement('p'); tip.className = 'ag-tip';
  tip.textContent = RO ? 'Availability collection is closed — shown read-only.'
    : 'Choose a brush, then click or drag over the times. Drag again over painted times to erase. Keyboard: arrows move, Space paints.';
  root.appendChild(tip);

  if (!RO) {
    const bar = document.createElement('div'); bar.className = 'ag-bar';
    const l = document.createElement('span'); l.className = 'lbl'; l.textContent = 'Brush'; bar.appendChild(l);
    [['1', 'Available'], ['2', 'Preferred'], ['0', 'Erase']].forEach(([v, label]) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'ag-mode';
      b.setAttribute('aria-pressed', String(st.mode === v));
      const sw = document.createElement('span'); sw.className = 'sw s' + v; b.appendChild(sw);
      b.appendChild(document.createTextNode(label));
      b.onclick = () => { st.mode = v; bar.querySelectorAll('.ag-mode').forEach(x => x.setAttribute('aria-pressed', 'false')); b.setAttribute('aria-pressed', 'true'); };
      bar.appendChild(b);
    });
    root.appendChild(bar);
  }

  const dates = data.dates;
  const startMin = Math.min(...dates.map(d => d.startMin));
  const endMin = Math.max(...dates.map(d => d.startMin + d.n * d.step));
  const step = dates[0].step;
  const rows = Math.round((endMin - startMin) / step);

  const wrap = document.createElement('div'); wrap.className = 'ag-wrap';
  wrap.setAttribute('role', 'grid');
  wrap.style.gridTemplateColumns = `74px repeat(${dates.length}, minmax(64px, 1fr))`;
  const corner = document.createElement('div'); corner.className = 'ag-corner'; wrap.appendChild(corner);

  dates.forEach(d => {
    const h = document.createElement('div'); h.className = 'ag-head'; h.setAttribute('role', 'columnheader');
    const a = document.createElement('div'); a.className = 'd'; a.textContent = d.label; h.appendChild(a);
    const b = document.createElement('div'); b.className = 's'; b.textContent = d.sub; h.appendChild(b);
    if (!RO) {
      const acts = document.createElement('div'); acts.className = 'acts';
      const mk = (txt, v) => { const x = document.createElement('button'); x.type = 'button'; x.className = 'ag-mini'; x.textContent = txt;
        x.setAttribute('aria-label', `${txt} — ${d.label}`);
        x.onclick = () => { st.cells[d.key] = v.repeat(d.n); paintAll(); send(); }; return x; };
      acts.appendChild(mk('All free', '1')); acts.appendChild(mk('Clear', '0'));
      h.appendChild(acts);
    }
    wrap.appendChild(h);
  });

  const cellEls = {};
  for (let r = 0; r < rows; r++) {
    const t = startMin + r * step, isHr = t % 60 === 0;
    const tl = document.createElement('div'); tl.className = 'ag-time' + (isHr ? ' hr' : '');
    tl.textContent = (t % 30 === 0) ? fmt(startMin, r, step) : '';
    wrap.appendChild(tl);
    dates.forEach((d, ci) => {
      const i = Math.round((t - d.startMin) / step);
      const c = document.createElement('div');
      c.className = 'ag-cell' + (isHr ? ' hr' : '') + (RO ? ' ro' : '');
      if (i < 0 || i >= d.n) { c.style.background = '#F1EFEA'; c.style.cursor = 'not-allowed'; c.setAttribute('aria-hidden', 'true'); wrap.appendChild(c); return; }
      c.dataset.k = d.key; c.dataset.i = i; c.dataset.c = ci; c.dataset.r = r;
      c.setAttribute('role', 'gridcell'); c.tabIndex = -1;
      (cellEls[d.key] = cellEls[d.key] || [])[i] = c;
      wrap.appendChild(c);
    });
  }
  root.appendChild(wrap);

  const label = (d, i, v) => `${d.label} ${fmt(d.startMin, i, d.step)} to ${fmt(d.startMin, i + 1, d.step)}: ${names[v]}`;
  function paintAll() {
    dates.forEach(d => (cellEls[d.key] || []).forEach((c, i) => {
      if (!c) return; const v = st.cells[d.key][i]; c.dataset.v = v; c.setAttribute('aria-label', label(d, i, v));
    }));
  }
  paintAll();
  const first = wrap.querySelector('.ag-cell[data-k]'); if (first) first.tabIndex = 0;

  const foot = document.createElement('div'); foot.className = 'ag-foot';
  [['1', 'Available'], ['2', 'Preferred ★'], ['0', 'Not available']].forEach(([v, t]) => {
    const s = document.createElement('span'); const sw = document.createElement('i'); sw.className = 'sw s' + v; s.appendChild(sw); s.appendChild(document.createTextNode(t)); foot.appendChild(s);
  });
  root.appendChild(foot);
  if (RO) return;

  // ---- pointer painting (mouse, pen and touch) ----
  let painting = false, brush = null, changed = false;
  const cellAt = (x, y) => { const el = (parentElement.elementFromPoint ? parentElement : document).elementFromPoint(x, y); return el && el.dataset && el.dataset.k ? el : null; };
  const apply = (c) => { if (setCell(c.dataset.k, +c.dataset.i, brush)) { changed = true; c.dataset.v = brush; } };
  const down = (e) => {
    const c = e.target.closest && e.target.closest('.ag-cell[data-k]'); if (!c) return;
    e.preventDefault(); painting = true; changed = false;
    const cur = st.cells[c.dataset.k][+c.dataset.i];
    brush = (st.mode !== '0' && cur === st.mode) ? '0' : st.mode; // drag over painted = erase
    apply(c);
  };
  const move = (e) => { if (!painting) return; const c = cellAt(e.clientX, e.clientY); if (c) apply(c); };
  const up = () => { if (!painting) return; painting = false; if (changed) { paintAll(); send(); } };
  wrap.addEventListener('pointerdown', down);
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);

  // ---- keyboard ----
  wrap.addEventListener('keydown', (e) => {
    const c = e.target.closest('.ag-cell[data-k]'); if (!c) return;
    let r = +c.dataset.r, ci = +c.dataset.c;
    const go = (nr, nc) => { const n = wrap.querySelector(`.ag-cell[data-r="${nr}"][data-c="${nc}"][data-k]`); if (n) { c.tabIndex = -1; n.tabIndex = 0; n.focus(); if (e.shiftKey) { brush = st.mode; apply(n); paintAll(); send(); } } };
    if (e.key === 'ArrowDown') { e.preventDefault(); go(r + 1, ci); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); go(r - 1, ci); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(r, ci + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(r, ci - 1); }
    else if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault(); const cur = st.cells[c.dataset.k][+c.dataset.i];
      brush = (st.mode !== '0' && cur === st.mode) ? '0' : st.mode; apply(c); paintAll(); send();
    }
  });

  return () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
  };
}
"""

_component = st.components.v2.component("availability_grid", html="", css=_CSS, js=_JS)


def availability_grid(dates: list[dict], cells: dict[str, str], rev: int, key: str, readonly: bool = False,
                      on_change=None):
    """Render the grid. Returns the latest {'rev':..,'cells':{...}} sent by the browser, or None."""
    result = _component(
        data={"dates": dates, "cells": cells, "rev": rev, "readonly": readonly},
        key=key,
        on_cells_change=on_change or (lambda: None),
    )
    val = getattr(result, "cells", None)
    if isinstance(val, str):
        val = json.loads(val)
    return val
