"""Interactive scheduling timeline (Streamlit Components v2).

* Candidate lanes show fixed events and meetings.
* Participant rows show submitted availability (available / preferred / no
  response) and that person's existing bookings with every candidate.
* Click a meeting to edit it; drag a meeting sideways to move it (snaps to the
  grid); click an empty spot on a candidate lane to add a meeting there.

Every change is validated in Python before it is saved, so the drag-and-drop
layer is a convenience, never the source of truth.
"""
from __future__ import annotations

import streamlit as st

_CSS = r"""
:host { all: initial; }
.tl { font-family: "Public Sans", system-ui, sans-serif; color: #1B2420; font-size: 13px; }
.tl * { box-sizing: border-box; }
.sec { border: 1px solid #DEDBD1; border-radius: 8px; background: #fff; margin-bottom: 14px; overflow-x: auto; }
.sec-h { display: flex; justify-content: space-between; align-items: baseline; padding: 10px 14px 8px; border-bottom: 1px solid #ECEAE3; background: #FBFAF7; position: sticky; left: 0; }
.sec-h .d { font-family: "Source Serif 4", Georgia, serif; font-weight: 600; font-size: 16px; }
.sec-h .m { font-size: 11.5px; color: #5B6B63; }
.inner { min-width: 760px; }
.row { display: grid; grid-template-columns: 210px 1fr; min-height: 34px; border-top: 1px solid #F1EFEA; }
.row.cand { min-height: 54px; background: #FCFCFA; }
.row.group { min-height: 24px; background: #F6F5F0; }
.row.group .lab { font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase; color: #5B6B63; font-weight: 700; }
.lab { padding: 6px 10px 6px 14px; border-right: 1px solid #ECEAE3; display: flex; flex-direction: column; justify-content: center; min-width: 0; }
.lab .n { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lab .s { font-size: 11px; color: #5B6B63; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row.cand .lab .n { font-family: "Source Serif 4", Georgia, serif; font-size: 14.5px; }
.lab button.pl { all: unset; cursor: pointer; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lab button.pl:hover, .lab button.pl:focus-visible { text-decoration: underline; outline: none; }
.trk { position: relative; }
.axis { display: grid; grid-template-columns: 210px 1fr; height: 24px; border-bottom: 1px solid #ECEAE3; }
.axis .trk span { position: absolute; top: 5px; font-size: 10.5px; color: #5B6B63; transform: translateX(-50%); font-variant-numeric: tabular-nums; white-space: nowrap; }
.it { position: absolute; top: 5px; bottom: 5px; border-radius: 5px; padding: 3px 6px; overflow: hidden; font-size: 11.5px; line-height: 1.2; white-space: nowrap; text-overflow: ellipsis; }
.it .t { font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
.it .u { font-size: 10.5px; opacity: .85; overflow: hidden; text-overflow: ellipsis; }
.it.meeting { background: #0A5C41; color: #fff; cursor: grab; border: 1px solid #08482F; }
.it.meeting.auto { background: #1E6E54; }
.it.meeting.sel { box-shadow: 0 0 0 3px #F0B429; }
.it.meeting.warn { background: #8A5A00; border-color: #6B4600; }
.it.meeting.error { background: #B42318; border-color: #8C1B13; }
.it.meeting.locked { cursor: pointer; background-image: repeating-linear-gradient(135deg, rgba(255,255,255,.0) 0 7px, rgba(255,255,255,.13) 7px 9px); }
.it.event { background: #E6E3F2; color: #2E2A4A; border: 1px solid #C9C3E3; }
.it.event.meal, .it.event.break { background: #F4ECD8; color: #4A3B12; border-color: #E2D3A8; }
.it.event.arrival, .it.event.departure { background: #E8EEF3; color: #1F3B52; border-color: #C5D3DF; }
.it.proposal { background: repeating-linear-gradient(135deg,#FFF7E0 0 6px,#FDEFC3 6px 12px); color: #5A4300; border: 1.5px dashed #C99A06; }
.it.avail { top: 9px; bottom: 9px; background: #CDEBDB; border-radius: 3px; padding: 0; }
.it.pref { top: 9px; bottom: 9px; background: #7FC3A0; border-radius: 3px; padding: 0 4px; color: #0B3B28; font-size: 10px; }
.it.busy { top: 7px; bottom: 7px; background: #24352D; color: #fff; font-size: 10.5px; padding: 2px 5px; border-radius: 3px; }
.it.busy.other { background: #6B7A73; }
.it.none { top: 9px; bottom: 9px; left: 0; right: 0; background: repeating-linear-gradient(135deg,#F7F6F2 0 6px,#ECEAE3 6px 8px); color: #6B7A73; font-size: 10.5px; padding: 1px 6px; border-radius: 3px; }
.it.win { top: 0; bottom: 0; background: transparent; border-left: 1px dashed #B9B4A6; border-right: 1px dashed #B9B4A6; border-radius: 0; pointer-events: none; }
.trk.lane { cursor: copy; }
.ghost { position: absolute; top: 2px; bottom: 2px; border: 2px solid #F0B429; border-radius: 6px; pointer-events: none; background: rgba(240,180,41,.15); }
.ghost b { position: absolute; top: -20px; left: 0; background: #1B2420; color: #fff; font-size: 11px; padding: 1px 6px; border-radius: 3px; white-space: nowrap; }
.icon { display: inline-block; margin-right: 3px; }
.it:focus-visible { outline: 3px solid #1F5F8B; outline-offset: 1px; }
.empty { padding: 16px; color: #5B6B63; }
"""

_JS = r"""
export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.tl');
  if (!root) { root = document.createElement('div'); root.className = 'tl'; parentElement.appendChild(root); }
  root.replaceChildren();
  const step = data.step || 15;
  const fmt = (t) => { const h = Math.floor(t / 60), m = t % 60; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')}${h < 12 ? 'a' : 'p'}`; };
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  if (!data.sections.length) { root.appendChild(el('div', 'empty', data.emptyText || 'Nothing to show yet.')); return; }
  const cleanups = [];

  data.sections.forEach(sec => {
    const span = sec.endMin - sec.startMin;
    const pct = (t) => ((t - sec.startMin) / span) * 100;
    const box = el('section', 'sec'); box.setAttribute('aria-label', sec.label);
    const h = el('div', 'sec-h'); h.appendChild(el('span', 'd', sec.label)); h.appendChild(el('span', 'm', sec.meta || '')); box.appendChild(h);
    const inner = el('div', 'inner'); box.appendChild(inner);
    // axis
    const ax = el('div', 'axis'); ax.appendChild(el('div', '')); const at = el('div', 'trk');
    for (let t = Math.ceil(sec.startMin / 60) * 60; t <= sec.endMin; t += 60) { const s = el('span', '', fmt(t)); s.style.left = pct(t) + '%'; at.appendChild(s); }
    ax.appendChild(at); inner.appendChild(ax);
    const grid = `repeating-linear-gradient(90deg, transparent 0, transparent calc(${100 * 60 / span}% - 1px), #EEECE6 calc(${100 * 60 / span}% - 1px), #EEECE6 ${100 * 60 / span}%)`;

    sec.rows.forEach(row => {
      const r = el('div', 'row ' + (row.kind || ''));
      const lab = el('div', 'lab');
      if (row.kind === 'person' && row.pid) {
        const b = el('button', 'pl n', row.label); b.type = 'button'; b.title = 'Show details for ' + row.label;
        b.onclick = () => setTriggerValue('action', { type: 'person', id: row.pid });
        lab.appendChild(b);
      } else lab.appendChild(el('div', 'n', row.label));
      if (row.sub) lab.appendChild(el('div', 's', row.sub));
      r.appendChild(lab);
      const trk = el('div', 'trk' + (row.lane ? ' lane' : ''));
      if (row.kind !== 'group') { trk.style.backgroundImage = grid; }
      (row.items || []).forEach(it => {
        const b = el('div', 'it ' + it.cls);
        if (it.type !== 'none') { b.style.left = pct(it.s) + '%'; b.style.width = Math.max(0.6, pct(it.e) - pct(it.s)) + '%'; }
        if (it.icon) b.appendChild(el('span', 'icon', it.icon));
        if (it.t) b.appendChild(el('div', 't', it.t));
        if (it.u) b.appendChild(el('div', 'u', it.u));
        b.title = it.title || it.t || '';
        if (it.type === 'meeting') {
          b.tabIndex = 0; b.setAttribute('role', 'button');
          b.setAttribute('aria-label', (it.title || it.t) + (it.locked ? ' (locked)' : '') + '. Press Enter to edit.');
          b.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTriggerValue('action', { type: 'select', id: it.id }); } });
          let sx = 0, moved = false, startS = it.s, ghost = null, w = 0, dragging = false;
          b.addEventListener('pointerdown', (e) => {
            e.preventDefault(); e.stopPropagation(); sx = e.clientX; moved = false; dragging = true; w = trk.getBoundingClientRect().width;
            b.setPointerCapture(e.pointerId);
          });
          b.addEventListener('pointermove', (e) => {
            if (!dragging || it.locked) return;
            const dx = e.clientX - sx; if (Math.abs(dx) > 4) moved = true; if (!moved) return;
            let ns = Math.round((startS + dx / w * span) / step) * step;
            ns = Math.max(sec.startMin, Math.min(sec.endMin - (it.e - it.s), ns));
            if (!ghost) { ghost = el('div', 'ghost'); ghost.appendChild(el('b', '')); trk.appendChild(ghost); }
            ghost.style.left = pct(ns) + '%'; ghost.style.width = (pct(ns + it.e - it.s) - pct(ns)) + '%';
            ghost.firstChild.textContent = fmt(ns) + '–' + fmt(ns + it.e - it.s); ghost.dataset.ns = ns;
          });
          b.addEventListener('pointerup', (e) => {
            if (!dragging) return; dragging = false;
            if (moved && ghost && !it.locked) {
              const ns = +ghost.dataset.ns; ghost.remove(); ghost = null;
              if (ns !== it.s) setTriggerValue('action', { type: 'move', id: it.id, startMin: ns, date: sec.key });
            } else setTriggerValue('action', { type: 'select', id: it.id });
          });
        }
        trk.appendChild(b);
      });
      if (row.lane) {
        trk.title = 'Click an empty spot to add a meeting here';
        trk.addEventListener('click', (e) => {
          if (e.target !== trk) return;
          const rect = trk.getBoundingClientRect();
          const t = sec.startMin + (e.clientX - rect.left) / rect.width * span;
          setTriggerValue('action', { type: 'slot', candidateId: row.lane.cid, date: sec.key, startMin: Math.floor(t / step) * step });
        });
      }
      r.appendChild(trk); inner.appendChild(r);
    });
    root.appendChild(box);
  });
}
"""

_component = st.components.v2.component("schedule_timeline", html="", css=_CSS, js=_JS)


def timeline(sections: list[dict], key: str, step: int = 15, empty_text: str = "", on_action=None) -> dict | None:
    result = _component(
        data={"sections": sections, "step": step, "emptyText": empty_text},
        key=key,
        on_action_change=on_action or (lambda: None),
    )
    return getattr(result, "action", None)
