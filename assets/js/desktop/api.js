/* =====================================================================
   api.js — the one place the desktop pages talk to the local application.

   Every call goes to http://127.0.0.1:<port>/api/desktop/*, served by
   pipeline/serve.py from this same origin. There is no remote endpoint and
   no key in the browser: the pages are only ever loaded from the local
   control room, so a relative URL is both correct and the security
   boundary.

   Everything returns a plain object. A network failure becomes
   {ok:false, error:"..."} rather than a rejected promise, because every
   caller in these pages wants to render the failure, not crash on it.
   ===================================================================== */
window.OWCSDesktop = (function () {
  'use strict';

  const BASE = '/api/desktop/';

  async function request(route, options) {
    try {
      const res = await fetch(BASE + route, Object.assign({
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store'
      }, options || {}));
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      if (body === null) {
        return { ok: false, error: 'the application returned an unreadable response (HTTP ' + res.status + ')' };
      }
      if (!res.ok && body.error === undefined) body.error = 'HTTP ' + res.status;
      if (!res.ok && body.ok === undefined) body.ok = false;
      return body;
    } catch (err) {
      return {
        ok: false,
        offline: true,
        error: 'The OWCS Comp Tracker background application is not responding. ' +
               'It may have been closed — reopen it from the tray icon or the Start menu.'
      };
    }
  }

  const get = (route) => request(route);
  const post = (route, payload) =>
    request(route, { method: 'POST', body: JSON.stringify(payload || {}) });

  /* -------------------------------------------------------- formatting */
  function gb(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return (Number(n) < 10 ? Number(n).toFixed(2) : Number(n).toFixed(1)) + ' GB';
  }
  function bytes(n) {
    if (!n && n !== 0) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = Number(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v : v.toFixed(v < 10 ? 2 : 1)) + ' ' + units[i];
  }
  function ago(iso) {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    if (isNaN(then)) return String(iso);
    const s = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    return Math.round(s / 86400) + 'd ago';
  }
  function clock(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const s = Math.max(0, Math.round(Number(seconds)));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  /* ------------------------------------------------------------- DOM */
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    });
    (Array.isArray(children) ? children : children ? [children] : [])
      .filter(Boolean)
      .forEach((c) => node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return node;
  }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function note(node, kind, message) {
    if (!node) return;
    node.className = 'note ' + (kind || '');
    node.textContent = message || '';
  }

  /* Render a {ok, detail/error} result into a note element. */
  function outcome(node, result, okMessage) {
    if (!result) return note(node, 'bad', 'no response');
    if (result.ok) note(node, 'ok', okMessage || result.detail || 'Done.');
    else note(node, 'bad', result.error || result.detail || 'That did not work.');
  }

  /* Poll a long-running task until it finishes; onUpdate gets each snapshot. */
  async function pollTask(onUpdate, intervalMs) {
    const wait = intervalMs || 1500;
    for (;;) {
      const snap = await get('task');
      if (onUpdate) onUpdate(snap);
      if (!snap || snap.offline || !snap.running) return snap;
      await new Promise((r) => setTimeout(r, wait));
    }
  }

  return { get, post, gb, bytes, ago, clock, el, clear, note, outcome, pollTask };
})();
