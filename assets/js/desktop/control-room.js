/* =====================================================================
   control-room.js — the desktop operations application.

   One page, thirteen views, one polling loop. Views render lazily (only the
   visible one refetches) so leaving the window open overnight costs one
   overview call every few seconds rather than thirteen.

   The rules this file follows, because the UI is where "looks finished" and
   "is finished" most easily diverge:

     * every button calls a real endpoint and renders the real result — there
       are no stubs, no optimistic success states, and no control that does
       nothing;
     * a failure is shown as a failure, with the reason the application gave;
     * anything attributable (intake, review decisions, layout edits) refuses
       to proceed without a name, exactly as the CLI does;
     * when the background application is not reachable, every view says so
       instead of rendering stale data as if it were live.
   ===================================================================== */
(function () {
  'use strict';
  const D = window.OWCSDesktop;
  const { el, clear, note, outcome } = D;
  const $ = (id) => document.getElementById(id);

  let current = 'overview';
  let settingsSchema = [];
  let lastUpdateInfo = null;
  let downloadedInstaller = null;

  /* ------------------------------------------------------- navigation */
  function switchTo(view) {
    current = view;
    document.querySelectorAll('.view').forEach((v) =>
      v.classList.toggle('active', v.dataset.view === view));
    document.querySelectorAll('#nav button').forEach((b) =>
      b.setAttribute('aria-current', String(b.dataset.view === view)));
    if (location.hash.slice(1) !== view) history.replaceState(null, '', '#' + view);
    render(view);
  }
  document.querySelectorAll('#nav button').forEach((b) =>
    b.addEventListener('click', () => switchTo(b.dataset.view)));
  window.addEventListener('hashchange', () => {
    const view = location.hash.slice(1);
    if (view && view !== current) switchTo(view);
  });

  const RENDERERS = {};
  function render(view) { if (RENDERERS[view]) RENDERERS[view](); }

  /* --------------------------------------------------------- overview */
  function badge(id, count, tone) {
    const node = $('cnt-' + id);
    if (!node) return;
    node.textContent = count ? String(count) : '';
    node.dataset.empty = count ? '0' : '1';
    node.className = 'navcount' + (count ? ' ' + (tone || 'warm') : '');
  }

  function statCard(k, v, d, tone) {
    return el('div', { class: 'stat ' + (tone || '') }, [
      el('div', { class: 'k', text: k }),
      el('div', { class: 'v', html: v }),
      el('div', { class: 'd', text: d || '' })
    ]);
  }

  async function refreshOverview() {
    const o = await D.get('overview');
    if (o.offline) {
      note($('intakeNote'), 'bad', o.error);
      $('workerState').innerHTML = '<span class="dot bad"></span>unreachable';
      $('workerDetail').textContent = 'The application is not responding.';
      return o;
    }
    $('brandVersion').textContent = 'v' + o.version + (o.frozen ? '' : ' (source)');

    /* worker card */
    const running = o.worker.running;
    const beat = o.worker.heartbeat || {};
    $('workerState').innerHTML =
      `<span class="dot ${running ? 'ok' : 'bad'}"></span>${running ? 'Running' : 'Stopped'}`;
    $('workerDetail').textContent = running
      ? (beat.currentJob ? 'Processing ' + beat.currentJob
                         : (beat.idleReason || 'idle') + ' · ' + (beat.storage || ''))
      : 'Nothing is being processed.';
    $('workerToggle').textContent = running ? 'Pause' : 'Start';

    badge('review', o.review.total, 'warm');
    badge('queue', o.queue.waitingOnYou, 'warm');
    badge('health', o.health.counts.fail, 'hot');
    /* The hero badge counts CANDIDATES WAITING, not heroes missing. A "44"
       that never changes is wallpaper; a number that only appears when
       there is something to click is a prompt. */
    badge('heroes', (o.heroes && o.heroes.templateReview) || 0, 'warm');

    if (current === 'overview') {
      const stats = $('overviewStats');
      clear(stats);
      stats.appendChild(statCard('Service', running ? 'Running' : 'Stopped',
        beat.processed !== undefined ? beat.processed + ' advanced this session' : '',
        running ? 'ok' : 'bad'));
      stats.appendChild(statCard('Active jobs', String(o.queue.active),
        o.queue.waitingOnYou + ' waiting on you',
        o.queue.waitingOnYou ? 'warn' : ''));
      stats.appendChild(statCard('Review inbox', String(o.review.total),
        o.review.stints + ' picks · ' + o.review.swaps + ' swaps',
        o.review.total ? 'warn' : 'ok'));
      stats.appendChild(statCard('Health',
        o.health.ok ? 'OK' : o.health.counts.fail + ' failing',
        o.health.counts.warn + ' warning(s)', o.health.ok ? 'ok' : 'bad'));
      stats.appendChild(statCard('Storage', D.gb(o.storage.totalGb),
        D.gb(o.storage.freeGb) + ' free of budget ' + D.gb(o.storage.budgetGb),
        o.storage.freeGb < 10 ? 'warn' : ''));
      if (o.heroes && o.heroes.available) {
        stats.appendChild(statCard('Hero detection',
          o.heroes.provenSomewhere + '/' + o.heroes.rosterSize + ' proven',
          o.heroes.detectableSomewhere + ' have a template somewhere',
          o.heroes.provenSomewhere >= o.heroes.rosterSize ? 'ok' : 'warn'));
      }
      stats.appendChild(statCard('Autostart',
        o.autoStart.supported ? (o.autoStart.enabled ? 'On' : 'Off') : 'N/A',
        o.autoStart.supported ? (o.autoStart.stale ? 'points at an old install' : 'starts with Windows')
                              : 'Windows only',
        o.autoStart.supported && o.autoStart.enabled ? 'ok' : 'warn'));

      const paths = await D.get('paths');
      const list = $('pathList');
      clear(list);
      [['Installed at', paths.appRoot], ['Your data', paths.dataRoot],
       ['Results database', paths.contentDb], ['Job queue', paths.automationDb],
       ['Downloads', paths.media], ['Logs', paths.logs], ['Backups', paths.backups]]
        .forEach(([k, v]) => {
          list.appendChild(el('dt', { text: k }));
          list.appendChild(el('dd', { text: v || '—' }));
        });

      const q = await D.get('queue');
      const needs = $('needsYou');
      clear(needs);
      const waiting = (q.jobs || []).filter((j) => j.waitingOnYou);
      if (!waiting.length) needs.appendChild(el('div', { class: 'empty', text: 'Nothing is waiting on a decision.' }));
      waiting.slice(0, 8).forEach((j) => needs.appendChild(jobRow(j, true)));

      const recent = $('recentJobs');
      clear(recent);
      if (!(q.jobs || []).length) recent.appendChild(el('div', { class: 'empty', text: 'No jobs yet. Add a broadcast to get started.' }));
      (q.jobs || []).slice(0, 8).forEach((j) => recent.appendChild(jobRow(j, false)));
    }
    return o;
  }
  RENDERERS.overview = refreshOverview;

  $('workerToggle').addEventListener('click', async () => {
    const btn = $('workerToggle');
    const starting = btn.textContent === 'Start';
    btn.disabled = true;
    await D.post(starting ? 'worker/start' : 'worker/stop', {});
    btn.disabled = false;
    refreshOverview();
  });

  function jobRow(j, showGate) {
    const tone = j.state === 'PUBLISHED' ? 'ok'
      : (j.state.indexOf('FAIL') === 0 ? 'bad'
      : (j.waitingOnYou ? 'warn' : 'info'));
    const bar = el('div', { class: 'bar' + (j.progress >= 100 ? ' done' : (tone === 'bad' ? ' stuck' : '')) },
      [el('i', { style: 'width:' + Math.max(2, j.progress) + '%' })]);
    return el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: j.title || j.jobKey }),
        el('div', { class: 'detail', text: j.jobKey + ' · ' + j.kind + ' · updated ' + D.ago(j.updatedAt) }),
        bar,
        showGate && j.gateHelp ? el('div', { class: 'revwhy', text: j.gateHelp }) : null,
        j.lastError ? el('div', { class: 'detail', text: 'Last error: ' + j.lastError }) : null
      ]),
      el('div', { class: 'actions' }, [el('span', { class: 'pill ' + tone, text: j.state })])
    ]);
  }

  /* ----------------------------------------------------------- intake */
  const intakeInput = $('intakeInput');
  let classifyTimer = null;

  intakeInput.addEventListener('input', () => {
    clearTimeout(classifyTimer);
    classifyTimer = setTimeout(async () => {
      const value = intakeInput.value.trim();
      if (!value) { note($('intakeVerdict'), '', ''); $('intakeSubmit').disabled = true; return; }
      const v = await D.get('intake/classify?q=' + encodeURIComponent(value));
      $('intakeSubmit').disabled = !v.accepted;
      note($('intakeVerdict'), v.accepted ? 'ok' : 'bad',
        (v.accepted ? 'Recognised as a ' + v.label + '. ' : v.label + ' — ') +
        (v.detail || v.reason || ''));
    }, 250);
  });

  $('intakeSubmit').addEventListener('click', async () => {
    const by = $('intakeBy').value.trim();
    const btn = $('intakeSubmit');
    btn.disabled = true;
    note($('intakeNote'), 'warn', 'Working…');
    $('intakeOut').textContent = '';
    const started = await D.post('intake/submit', { input: intakeInput.value.trim(), requestedBy: by });
    if (!started.ok) { outcome($('intakeNote'), started); btn.disabled = false; return; }
    await D.pollTask((s) => {
      if (s && s.running) note($('intakeNote'), 'warn', 'Working… ' + Math.round(s.elapsedSeconds || 0) + 's');
    });
    const final = await D.get('task');
    btn.disabled = false;
    const r = final.result || {};
    if (final.error) { note($('intakeNote'), 'bad', final.error); return; }
    note($('intakeNote'), r.ok ? 'ok' : 'bad', r.detail || r.error || 'Done.');
    $('intakeOut').textContent = JSON.stringify(
      { queued: r.queued, skipped: r.skipped, matches: r.matches }, null, 2);
    if (r.ok) { intakeInput.value = ''; $('intakeSubmit').disabled = true; refreshOverview(); }
  });

  /* ------------------------------------------------------------ queue */
  RENDERERS.queue = async function () {
    const q = await D.get('queue');
    const list = $('queueList');
    clear(list);
    if (q.offline) { list.appendChild(el('div', { class: 'empty', text: q.error })); return; }
    if (!(q.jobs || []).length) {
      list.appendChild(el('div', { class: 'empty', text: 'The queue is empty. Add a broadcast to get started.' }));
      return;
    }
    q.jobs.forEach((j) => list.appendChild(jobRow(j, true)));
  };
  $('queueRefresh').addEventListener('click', () => RENDERERS.queue());

  /* ----------------------------------------------------------- review */
  RENDERERS.review = async function () {
    const r = await D.get('review');
    const list = $('reviewList');
    clear(list);
    if (r.offline) { list.appendChild(el('div', { class: 'empty', text: r.error })); return; }
    if (!(r.items || []).length) {
      list.appendChild(el('div', { class: 'empty', text: 'Nothing is waiting for review. Every detection so far either cleared the confidence gate or was rejected with a reason.' }));
      return;
    }
    r.items.forEach((item) => list.appendChild(reviewItem(item)));
  };

  function reviewItem(item) {
    const title = item.kind === 'stint'
      ? `${item.heroId} — slot ${item.slot} (${item.teamId})`
      : item.kind === 'swap'
        ? `${item.fromHero || '?'} → ${item.toHero} at ${D.clock(item.offsetSeconds)}`
        : `${item.taskKind}: ${item.refKey}`;
    const meta = item.kind === 'stint'
      ? `${item.matchId} · ${D.clock(item.startOffset)}–${D.clock(item.endOffset)} · ${item.observations || 0} samples · mean ${fmt(item.meanConfidence)} / min ${fmt(item.minConfidence)}`
      : item.kind === 'swap'
        ? `${item.matchId} · confidence ${fmt(item.confidence)} · ${item.detector || ''}`
        : `lane ${item.lane} · created ${D.ago(item.createdAt)}`;

    const shots = el('div', { class: 'revshots' });
    (item.evidence || []).forEach((path, i) => {
      const src = path.replace(/\\/g, '/').replace(/^\/+/, '');
      const img = el('img', { src: src, alt: 'evidence crop', loading: 'lazy' });
      const fig = el('figure', {}, [img, el('figcaption', { text: i === 0 ? 'before' : 'after' })]);
      img.addEventListener('error', () => {
        fig.replaceChildren(el('div', { class: 'revmiss', text: 'evidence crop not on this PC: ' + path }));
      });
      shots.appendChild(fig);
    });
    if (!(item.evidence || []).length) {
      shots.appendChild(el('div', { class: 'revmiss', text: 'No evidence crop was recorded for this item.' }));
    }

    const noteNode = el('div', { class: 'note' });
    const act = (decision) => async () => {
      const reviewer = $('reviewer').value.trim();
      const res = await D.post('review/decide', {
        kind: item.kind, id: item.id, decision: decision, reviewer: reviewer
      });
      outcome(noteNode, res, decision === 'approve' ? 'Approved.' : 'Rejected.');
      if (res.ok) setTimeout(() => RENDERERS.review(), 700);
    };

    return el('div', { class: 'revitem' }, [
      el('div', { class: 'revhead' }, [
        el('b', { text: title }),
        el('span', { class: 'pill warn', text: item.kind })
      ]),
      el('div', { class: 'detail', text: meta }),
      el('div', { class: 'revwhy', text: 'Held because: ' + (item.why || 'the gate was not met') }),
      shots,
      el('div', { class: 'btnrow', style: 'margin-top:6px' }, [
        el('button', { class: 'btn btn-sm btn-primary', text: 'Approve', onclick: act('approve') }),
        el('button', { class: 'btn btn-sm danger', text: 'Reject', onclick: act('reject') })
      ]),
      noteNode
    ]);
  }
  const fmt = (n) => (n === null || n === undefined) ? '—' : Number(n).toFixed(2);

  /* ----------------------------------------------------- hero coverage */
  /* Four states, four colours, one meaning each. The palette is
     deliberately not a gradient: "covered but unproven" is not 75% of
     "ready", it is a different situation with a different fix, and a
     gradient would invite reading it as nearly-done. */
  const HERO_TONE = { READY: 'ok', UNPROVEN: 'warn', BLOCKED: 'bad', MISSING: '' };
  const HERO_WHAT = {
    READY: 'template exists, quality-gated, traced to a source frame, and '
         + 'proven correct on held-out frames',
    UNPROVEN: 'a template exists but nothing has scored it on frames it '
            + 'never saw — it may be fine, it is not shown to be',
    BLOCKED: 'something is measurably wrong: a failing quality check or a '
           + 'confident wrong answer on held-out footage',
    MISSING: 'no template at all — every frame of this hero reads UNKNOWN'
  };

  RENDERERS.heroes = async function () {
    const r = await D.get('heroes/coverage');
    const stats = $('heroStats');
    const list = $('heroLayouts');
    clear(stats); clear(list);
    if (r.offline) { list.appendChild(el('div', { class: 'empty', text: r.error })); return; }
    if (!r.available) {
      list.appendChild(el('div', { class: 'empty', text: r.error || r.note || 'Hero coverage is unavailable on this machine.' }));
      return;
    }
    stats.appendChild(statCard('Proven anywhere',
      (r.provenSomewhere || []).length + '/' + r.rosterSize,
      'validated on held-out frames in at least one package',
      (r.provenSomewhere || []).length >= r.rosterSize ? 'ok' : 'warn'));
    stats.appendChild(statCard('Covered anywhere',
      (r.detectableSomewhere || []).length + '/' + r.rosterSize,
      'a template exists in at least one package'));
    stats.appendChild(statCard('Best package', r.bestLayout || '—',
      r.bestLayoutReadiness || ''));
    (r.layouts || []).forEach((lay) => list.appendChild(heroLayoutRow(lay)));

    const inbox = await D.get('templates/review');
    const tr = $('templateReview');
    clear(tr);
    if (inbox.offline) { tr.appendChild(el('div', { class: 'empty', text: inbox.error })); return; }
    if (!(inbox.items || []).length) {
      tr.appendChild(el('div', { class: 'empty', text: 'No portrait candidates are waiting. Every harvested crop was either clean enough to accept or bad enough to reject outright.' }));
      return;
    }
    inbox.items.forEach((item) => tr.appendChild(templateCandidate(item)));
  };

  function heroLayoutRow(lay) {
    const c = lay.counts || {};
    const grid = el('div', { class: 'herogrid' });
    (lay.heroes || []).forEach((h) => {
      const cell = el('span', {
        class: 'herocell ' + (HERO_TONE[h.state] || ''),
        text: h.id,
        title: h.name + ' — ' + h.state + ': ' + (h.blocker || HERO_WHAT[h.state])
             + (h.templates && h.templates.length
                 ? '\n' + h.templates.length + ' template(s): ' + h.templates.join(', ')
                 : '')
             + (h.action ? '\nNext: ' + h.action : '')
      });
      grid.appendChild(cell);
    });

    const kids = [
      el('div', { class: 'revhead' }, [
        el('b', { text: lay.layoutId }),
        el('span', { class: 'pill ' + (c.READY === lay.rosterSize ? 'ok' : 'warn'),
                     text: lay.readiness })
      ]),
      el('div', { class: 'detail', text:
        `${c.READY || 0} ready · ${c.UNPROVEN || 0} unproven · `
        + `${c.BLOCKED || 0} blocked · ${c.MISSING || 0} missing`
        + (lay.templatesDir ? ' · ' + lay.templatesDir : ' · no templates_dir') }),
      grid
    ];

    const prof = lay.detectorProfile || {};
    if (prof.portraitRoi || prof.unknownFloor) {
      kids.push(el('div', { class: 'detail', text:
        'Detector profile: '
        + (prof.portraitRoi ? 'portrait ROI ' + JSON.stringify(prof.portraitRoi) : 'full slot')
        + (prof.unknownFloor ? ' · UNKNOWN below ' + prof.unknownFloor : '') }));
    }

    const run = lay.validationRun;
    if (run) {
      const fm = run.falseMatch || {};
      if (fm.note) kids.push(el('div', { class: 'revwhy', text: fm.note }));
      (run.confusablePairs || []).slice(0, 4).forEach((p) =>
        kids.push(el('div', { class: 'revwhy', text: 'Confusable: ' + p.note })));
    } else {
      kids.push(el('div', { class: 'revwhy', text:
        'No validation report for this package. Nothing here has been scored '
        + 'on frames it did not help build, so "covered" is all that can be '
        + 'claimed.' }));
    }

    /* The blockers, with the command that clears each one. A coverage page
       that lists problems without their remedy just relocates the problem. */
    const blocked = (lay.heroes || []).filter((h) => h.state === 'BLOCKED' || h.state === 'UNPROVEN');
    if (blocked.length) {
      const seen = {};
      blocked.forEach((h) => { if (h.action) seen[h.action] = (seen[h.action] || 0) + 1; });
      Object.keys(seen).forEach((action) =>
        kids.push(el('div', { class: 'detail', text:
          `${seen[action]} hero(es) → ${action}` })));
    }
    return el('div', { class: 'revitem' }, kids);
  }

  function templateCandidate(item) {
    const src = String(item.cropPath || '').replace(/\\/g, '/').replace(/^\/+/, '');
    const img = el('img', { src: src, alt: 'candidate portrait', loading: 'lazy' });
    const fig = el('figure', {}, [img, el('figcaption', { text: item.heroId || '?' })]);
    img.addEventListener('error', () => {
      fig.replaceChildren(el('div', { class: 'revmiss', text: 'crop not on this PC: ' + item.cropPath }));
    });
    return el('div', { class: 'revitem' }, [
      el('div', { class: 'revhead' }, [
        el('b', { text: (item.heroId || 'unlabelled') + ' — ' + item.package }),
        el('span', { class: 'pill warn', text: 'candidate' })
      ]),
      el('div', { class: 'detail', text:
        `${item.layoutId || ''} · slot ${item.slot || '?'} · t=${D.clock(item.t || 0)}` }),
      el('div', { class: 'revwhy', text: 'Marginal because: ' + ((item.reasons || []).join('; ') || 'unspecified') }),
      el('div', { class: 'revshots' }, [fig]),
      el('div', { class: 'note', text:
        'Approving a portrait template is not wired to a button on purpose. '
        + 'It changes what every future detection compares against, so it '
        + 'goes through pipeline/template_forge.py, which re-runs the quality '
        + 'gate and held-out validation before anything is promoted.' })
    ]);
  }

  const heroRefreshBtn = $('heroRefresh');
  if (heroRefreshBtn) heroRefreshBtn.addEventListener('click', () => RENDERERS.heroes());

  /* ------------------------------------------------------ calibration */
  let editing = null;   /* {name, width, height, boxes:[{id,kind,label,rect}]} */
  let selected = -1;

  RENDERERS.calibration = async function () {
    const res = await D.get('calibration');
    const list = $('layoutList');
    clear(list);
    if (res.offline) { list.appendChild(el('div', { class: 'empty', text: res.error })); return; }
    (res.layouts || []).forEach((l) => {
      const open = el('button', { class: 'btn btn-sm btn-primary', text: 'Open editor' });
      open.addEventListener('click', () => openEditor(l.name));
      list.appendChild(el('div', { class: 'row' }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'name', text: l.name }),
          el('div', { class: 'detail', text: l.error
            ? 'unreadable: ' + l.error
            : `${l.frameWidth}×${l.frameHeight} · ${l.slots} portrait slots · ${l.boxes} editable box(es)` +
              (l.calibrated
                ? ` · calibrated${l.version ? ' (' + l.version + ')' : ''} from ${l.framesUsed || '?'} frames` +
                  (l.residual !== null && l.residual !== undefined ? `, grid residual ${l.residual}` : '')
                : ' · not calibrated — rectangles are guesses until edited or recalibrated') +
              (l.manualEdits ? ` · ${l.manualEdits} manual edit(s)` : '') })
        ]),
        el('div', { class: 'actions' }, [
          el('span', { class: 'pill ' + (l.calibrated ? 'ok' : 'warn'),
                       text: l.calibrated ? 'calibrated' : 'uncalibrated' }),
          open
        ])
      ]));
    });
  };

  async function openEditor(name) {
    const res = await D.get('calibration?name=' + encodeURIComponent(name));
    if (!res.boxes) { note($('calNote'), 'bad', res.error || 'Could not read that layout.'); return; }
    editing = {
      name: name, width: res.frameWidth, height: res.frameHeight,
      boxes: res.boxes.map((b) => ({ id: b.id, kind: b.kind, label: b.label, rect: b.rect.slice() }))
    };
    selected = -1;
    $('editorName').textContent = name;
    $('editorCard').style.display = '';
    note($('calNote'), res.editable ? '' : 'warn',
      res.editable ? '' : 'This layout file is read-only on disk, so saving will fail.');
    drawStage();
    $('editorCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function drawStage() {
    const stage = $('calStage');
    const canvas = $('calCanvas');
    const W = editing.width || 1280;
    const H = editing.height || 720;
    canvas.width = W; canvas.height = H;

    /* A representative frame is not always on disk, so the stage draws a
       schematic of the broadcast instead of pretending to show one. The
       boxes are what matters and they are to scale. */
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#05080f'; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#16203a'; ctx.lineWidth = 1;
    for (let x = 0; x < W; x += Math.round(W / 16)) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += Math.round(H / 9)) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    ctx.fillStyle = '#20304f';
    ctx.fillRect(0, 0, W, Math.round(H * 0.08));
    ctx.fillStyle = '#8ea0bd';
    ctx.font = `${Math.round(H / 26)}px sans-serif`;
    ctx.fillText(`${W}×${H} broadcast frame — drag the boxes onto the HUD elements`, 16, Math.round(H * 0.055));

    stage.querySelectorAll('.calbox').forEach((n) => n.remove());
    editing.boxes.forEach((box, i) => stage.appendChild(boxNode(box, i, W, H)));
    renderBoxList();
  }

  function boxNode(box, i, W, H) {
    const [x, y, w, h] = box.rect;
    const node = el('div', {
      class: 'calbox' + (i === selected ? ' sel' : ''),
      'data-kind': box.kind, tabindex: '0',
      style: `left:${x / W * 100}%;top:${y / H * 100}%;width:${w / W * 100}%;height:${h / H * 100}%`
    }, [el('span', { class: 'tag', text: box.label }), el('span', { class: 'hnd' })]);

    node.addEventListener('pointerdown', (ev) => {
      selected = i;
      renderBoxList();
      document.querySelectorAll('.calbox').forEach((n, n2) => n.classList.toggle('sel', n2 === i));
      const resizing = ev.target.classList.contains('hnd');
      const stage = $('calStage').getBoundingClientRect();
      const scale = W / stage.width;
      const startX = ev.clientX, startY = ev.clientY;
      const orig = box.rect.slice();
      node.setPointerCapture(ev.pointerId);

      const move = (e) => {
        const dx = (e.clientX - startX) * scale;
        const dy = (e.clientY - startY) * scale;
        if (resizing) {
          box.rect[2] = Math.max(4, Math.round(orig[2] + dx));
          box.rect[3] = Math.max(4, Math.round(orig[3] + dy));
        } else {
          box.rect[0] = Math.max(0, Math.min(W - box.rect[2], Math.round(orig[0] + dx)));
          box.rect[1] = Math.max(0, Math.min(H - box.rect[3], Math.round(orig[1] + dy)));
        }
        node.style.left = box.rect[0] / W * 100 + '%';
        node.style.top = box.rect[1] / H * 100 + '%';
        node.style.width = box.rect[2] / W * 100 + '%';
        node.style.height = box.rect[3] / H * 100 + '%';
        renderBoxList();
      };
      const up = (e) => {
        node.releasePointerCapture(ev.pointerId);
        node.removeEventListener('pointermove', move);
        node.removeEventListener('pointerup', up);
      };
      node.addEventListener('pointermove', move);
      node.addEventListener('pointerup', up);
      ev.preventDefault();
    });

    node.addEventListener('keydown', (ev) => {
      const step = ev.shiftKey ? 10 : 1;
      const map = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
      if (!map[ev.key]) return;
      ev.preventDefault();
      box.rect[0] = Math.max(0, Math.min(W - box.rect[2], box.rect[0] + map[ev.key][0]));
      box.rect[1] = Math.max(0, Math.min(H - box.rect[3], box.rect[1] + map[ev.key][1]));
      node.style.left = box.rect[0] / W * 100 + '%';
      node.style.top = box.rect[1] / H * 100 + '%';
      renderBoxList();
    });
    return node;
  }

  function renderBoxList() {
    const list = $('calList');
    clear(list);
    editing.boxes.forEach((box, i) => {
      const row = el('div', { class: 'row' + (i === selected ? ' sel' : '') }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'name', text: box.label }),
          el('div', { class: 'detail', text: box.rect.map(Math.round).join(', ') })
        ])
      ]);
      row.addEventListener('click', () => {
        selected = i;
        document.querySelectorAll('.calbox').forEach((n, n2) => n.classList.toggle('sel', n2 === i));
        renderBoxList();
        const node = document.querySelectorAll('.calbox')[i];
        if (node) node.focus();
      });
      list.appendChild(row);
    });
  }

  $('calSave').addEventListener('click', async () => {
    if (!editing) return;
    const editor = $('calEditor').value.trim();
    if (!editor) { note($('calNote'), 'bad', 'Enter your name — layout edits are attributed.'); return; }
    const res = await D.post('calibration/save', {
      name: editing.name, editor: editor,
      boxes: editing.boxes.map((b) => ({ id: b.id, rect: b.rect.map(Math.round) }))
    });
    outcome($('calNote'), res, res.detail);
    if (res.ok) RENDERERS.calibration();
  });

  $('calReset').addEventListener('click', () => { if (editing) openEditor(editing.name); });

  /* Importing a layout built by the browser wizard. */
  $('importLayout').addEventListener('click', () => {
    if (!$('importBy').value.trim()) {
      note($('importNote'), 'bad', 'Enter your name first — imports are attributed.');
      return;
    }
    $('importFile').click();
  });
  $('importFile').addEventListener('change', async () => {
    const file = $('importFile').files[0];
    if (!file) return;
    let layout;
    try {
      layout = JSON.parse(await file.text());
    } catch (err) {
      note($('importNote'), 'bad', `${file.name} is not readable JSON.`);
      $('importFile').value = '';
      return;
    }
    const suggested = file.name.replace(/\.json$/i, '');
    const res = await D.post('calibration/import', {
      layout: layout, name: suggested,
      importer: $('importBy').value.trim(),
    });
    outcome($('importNote'), res, res.detail);
    $('importFile').value = '';
    if (res.ok) RENDERERS.calibration();
  });

  /* ---------------------------------------------------------- publish */
  RENDERERS.publish = async function () {
    const p = await D.get('publish');
    const info = $('publishInfo');
    clear(info);
    if (p.offline) { info.appendChild(el('dd', { text: p.error })); return; }
    [['File', p.path], ['Exists', p.exists ? 'yes' : 'no'],
     ['Size', D.bytes(p.bytes)], ['Last written', p.modified || '—'],
     ['Dataset', p.demo === false ? 'production (real data)'
       : p.demo === true ? 'demo fixture' : 'unknown'],
     ['Generated by', p.generatedBy || '—']]
      .forEach(([k, v]) => { info.appendChild(el('dt', { text: k })); info.appendChild(el('dd', { text: String(v) })); });

    const runs = $('publishRuns');
    clear(runs);
    if (!(p.runs || []).length) runs.appendChild(el('div', { class: 'empty', text: 'No publication has been recorded yet.' }));
    (p.runs || []).forEach((r) => runs.appendChild(el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: r.state || r.status || 'publication' }),
        el('div', { class: 'detail', text: JSON.stringify(r).slice(0, 300) })
      ])
    ])));

    renderSite(p.site || {});
  };

  /* The live website half. Everything that stops a publish is listed as a
     specific, fixable thing rather than the button being greyed out with no
     explanation — "not ready" is useless if it does not say what to do. */
  function renderSite(site) {
    const info = $('siteInfo');
    clear(info);
    [['Repository', site.repo || 'not set'],
     ['Branch', site.branch || '—'],
     ['GitHub token', site.hasToken ? 'saved' : 'not saved'],
     ['Files to upload', (site.files || []).map((f) => f.path).join(', ') || '—']]
      .forEach(([k, v]) => { info.appendChild(el('dt', { text: k })); info.appendChild(el('dd', { text: String(v) })); });

    const blockers = $('siteBlockers');
    clear(blockers);
    (site.blockers || []).forEach((b) => blockers.appendChild(
      el('div', { class: 'note bad', text: b })));
    $('publishSite').disabled = !site.ready;

    $('autoPublishToggle').checked = !!site.autoPublish;

    const hist = $('siteHistory');
    clear(hist);
    if (!(site.history || []).length) {
      hist.appendChild(el('div', { class: 'empty', text: 'Nothing has been uploaded to the website from this machine yet.' }));
    }
    (site.history || []).forEach((h) => hist.appendChild(el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: (h.ok ? 'Published' : 'Refused') + ' — ' + (h.at || '') }),
        el('div', { class: 'detail', text: h.ok
          ? `${(h.files || []).length} file(s) to ${h.repo}@${h.branch}, by ${h.by}`
          : (h.error || 'no reason recorded') })
      ]),
      el('span', { class: 'pill ' + (h.ok ? 'ok' : 'bad'), text: h.ok ? 'ok' : 'failed' })
    ])));
  }

  $('autoPublishToggle').addEventListener('change', async (ev) => {
    const res = await D.post('settings',
      { settings: { autoPublishToSite: ev.target.checked } });
    note($('siteNote'), res.ok ? 'ok' : 'bad',
      res.ok ? (ev.target.checked
        ? 'Finished broadcasts will be published to the website automatically.'
        : 'Automatic publishing is off. Use the button above when you want to.')
             : (res.error || 'Could not save that setting.'));
    if (!res.ok) ev.target.checked = !ev.target.checked;
  });

  $('publishSite').addEventListener('click', async () => {
    const btn = $('publishSite');
    btn.disabled = true;
    note($('siteNote'), 'warn', 'Regenerating, verifying and uploading…');
    $('siteOut').textContent = '';
    const started = await D.post('publish/site', {
      by: $('publishBy').value.trim(),
      message: $('publishMessage').value.trim()
    });
    if (!started.ok) { outcome($('siteNote'), started); btn.disabled = false; return; }
    await D.pollTask((s) => {
      if (s && s.running) note($('siteNote'), 'warn', 'Working… ' + Math.round(s.elapsedSeconds || 0) + 's');
    });
    const final = await D.get('task');
    btn.disabled = false;
    const r = final.result || {};
    note($('siteNote'), r.ok ? 'ok' : 'bad',
      r.ok ? (r.detail || 'Published to the website.')
           : (r.error || final.error || 'The publish failed.'));
    $('siteOut').textContent = (r.files || [])
      .map((f) => `${f.status.padEnd(10)} ${f.path}${f.commit ? '  ' + f.commit.slice(0, 10) : ''}`)
      .join('\n');
    RENDERERS.publish();
  });

  $('publishExport').addEventListener('click', async () => {
    const btn = $('publishExport');
    btn.disabled = true;
    note($('publishNote'), 'warn', 'Backing up, regenerating and verifying…');
    const started = await D.post('publish/export', {});
    if (!started.ok) { outcome($('publishNote'), started); btn.disabled = false; return; }
    await D.pollTask((s) => {
      if (s && s.running) note($('publishNote'), 'warn', 'Working… ' + Math.round(s.elapsedSeconds || 0) + 's');
    });
    const final = await D.get('task');
    btn.disabled = false;
    const r = final.result || {};
    note($('publishNote'), r.ok ? 'ok' : 'bad',
      r.ok ? `Published ${D.bytes(r.bytes)}. Backup ${r.backup} was taken first.`
           : (r.error || final.error || 'The publish failed.'));
    $('publishOut').textContent = r.detail || '';
    RENDERERS.publish();
  });

  /* ----------------------------------------------------------- health */
  RENDERERS.health = async function () {
    const report = await D.get('health');
    const list = $('healthList');
    clear(list);
    if (report.offline) { list.appendChild(el('div', { class: 'empty', text: report.error })); return; }
    (report.checks || []).forEach((c) => list.appendChild(healthRow(c)));
    note($('healthNote'), report.ok ? (report.counts.warn ? 'warn' : 'ok') : 'bad',
      `${report.counts.ok} ok · ${report.counts.warn} warning(s) · ${report.counts.fail} failure(s)`);

    const repairs = await D.get('repairs');
    const rlist = $('repairList');
    clear(rlist);
    (repairs.actions || []).forEach((a) => {
      const btn = el('button', { class: 'btn btn-sm btn-ghost', text: 'Run' });
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = 'Running…';
        const res = await D.post('repair', { action: a.id });
        outcome($('healthNote'), res);
        btn.disabled = false; btn.textContent = 'Run';
        RENDERERS.health();
      });
      rlist.appendChild(el('div', { class: 'row' }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'name', text: a.label }),
          el('div', { class: 'detail', text: a.help })
        ]),
        el('div', { class: 'actions' }, [btn])
      ]));
    });
  };

  function healthRow(c) {
    const actions = el('div', { class: 'actions' }, [
      el('span', { class: 'pill ' + (c.status === 'ok' ? 'ok' : c.status === 'warn' ? 'warn' : 'bad'), text: c.status })
    ]);
    if (c.repair && c.repair.indexOf('repair.') === 0) {
      const btn = el('button', { class: 'btn btn-sm btn-ghost', text: 'Fix this' });
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = 'Fixing…';
        const res = await D.post('repair', { action: c.repair });
        outcome($('healthNote'), res);
        RENDERERS.health();
      });
      actions.appendChild(btn);
    }
    return el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: c.label }),
        el('div', { class: 'detail', text: c.detail })
      ]), actions
    ]);
  }

  $('healthRefresh').addEventListener('click', () => RENDERERS.health());
  $('healthReadiness').addEventListener('click', async () => {
    const btn = $('healthReadiness');
    btn.disabled = true;
    $('healthOut').textContent = '';
    note($('healthNote'), 'warn', 'Building a test broadcast and running the whole pipeline over it…');
    const started = await D.post('readiness', {});
    if (!started.ok) { outcome($('healthNote'), started); btn.disabled = false; return; }
    await D.pollTask((s) => {
      if (s && s.running) note($('healthNote'), 'warn', 'Running… ' + Math.round(s.elapsedSeconds || 0) + 's');
    });
    const final = await D.get('task');
    btn.disabled = false;
    const r = final.result;
    if (!r) { note($('healthNote'), 'bad', final.error || 'No result.'); return; }
    $('healthOut').textContent = (r.suites || [])
      .map((s) => `${s.status.toUpperCase()}  ${s.label}\n${(s.detail || '').split('\n').slice(-8).join('\n')}`).join('\n\n');
    note($('healthNote'), r.ok ? 'ok' : 'bad',
      `${r.passed} passed · ${r.failed} failed · ${r.skipped} skipped`);
  });

  /* ---------------------------------------------------------- storage */
  RENDERERS.storage = async function () {
    const s = await D.get('storage');
    const stats = $('storageStats');
    clear(stats);
    if (s.offline) { stats.appendChild(el('div', { class: 'empty', text: s.error })); return; }
    stats.appendChild(statCard('Used by the app', D.gb(s.usage.totalGb), s.usage.root));
    stats.appendChild(statCard('Free on this drive', D.gb(s.usage.freeGb), ''));
    stats.appendChild(statCard('Reclaimable now', D.gb(s.plan.reclaimGb),
      s.plan.remove.length + ' finished job(s)', s.plan.reclaimGb > 0 ? 'warn' : 'ok'));

    const areas = $('storageAreas');
    clear(areas);
    s.usage.areas.filter((a) => a.bytes > 0 || a.protected).forEach((a) =>
      areas.appendChild(el('div', { class: 'row' }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'name', text: a.area }),
          el('div', { class: 'detail', text: a.path })
        ]),
        el('div', { class: 'actions' }, [
          el('span', { class: 'pill info', text: D.bytes(a.bytes) }),
          a.protected ? el('span', { class: 'pill ok', text: 'never pruned' }) : null
        ])
      ])));

    const plan = $('prunePlan');
    clear(plan);
    if (!s.plan.remove.length) {
      plan.appendChild(el('div', { class: 'empty', text: 'Nothing is eligible for cleanup. Media belonging to an unfinished job is never touched.' }));
    }
    s.plan.remove.forEach((r) => plan.appendChild(el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: r.jobKey }),
        el('div', { class: 'detail', text: `${r.fileCount} file(s) · ${D.gb(r.gb)} · ${r.state} · ${r.reason}` })
      ])
    ])));
  };

  $('pruneRun').addEventListener('click', async () => {
    const res = await D.post('storage/prune', {});
    const applied = res.applied || {};
    note($('storageNote'), res.ok ? 'ok' : 'bad',
      res.ok ? `Freed ${D.gb(applied.freedGb)} from ${(applied.removed || []).length} folder(s).`
             : (res.error || 'Cleanup failed.'));
    RENDERERS.storage();
  });

  /* ---------------------------------------------------------- backups */
  RENDERERS.backups = async function () {
    const res = await D.get('backups');
    const list = $('backupList');
    clear(list);
    if (res.offline) { list.appendChild(el('div', { class: 'empty', text: res.error })); return; }
    if (!(res.backups || []).length) {
      list.appendChild(el('div', { class: 'empty', text: 'No backups yet. One is taken automatically before every publish.' }));
      return;
    }
    res.backups.forEach((b) => {
      const btn = el('button', { class: 'btn btn-sm danger', text: 'Restore' });
      btn.disabled = !b.valid;
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        const res2 = await D.post('backups/restore', { id: b.id });
        note($('backupNote'), res2.ok ? 'ok' : 'bad',
          res2.ok ? `Restored ${res2.restored.length} file(s). The previous state was saved as ${res2.preRestoreSnapshot}.`
                  : (res2.error || (res2.problems || []).join('; ')));
        RENDERERS.backups();
      });
      list.appendChild(el('div', { class: 'row' }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'name', text: b.id }),
          el('div', { class: 'detail', text: `${b.reason} · ${D.ago(b.createdAt)} · ${D.bytes(b.totalBytes)} · ${(b.files || []).filter((f) => f.present).length} file(s)` })
        ]),
        el('div', { class: 'actions' }, [
          el('span', { class: 'pill ' + (b.valid ? 'ok' : 'bad'), text: b.valid ? 'verified' : 'corrupt' }),
          btn
        ])
      ]));
    });
  };

  $('backupCreate').addEventListener('click', async () => {
    const res = await D.post('backups/create', { reason: 'manual' });
    note($('backupNote'), res.ok ? 'ok' : 'bad',
      res.ok ? 'Backup ' + res.backup.id + ' taken.' : (res.error || 'Backup failed.'));
    RENDERERS.backups();
  });

  /* ---------------------------------------------------------- updates */
  RENDERERS.updates = async function () { await checkUpdates(false); };

  async function checkUpdates(explicit) {
    const info = await D.get('updates');
    lastUpdateInfo = info;
    const list = $('updateInfo');
    clear(list);
    [['Installed', info.current], ['Latest', info.latest || '—'],
     ['Channel', info.prerelease ? 'prerelease' : 'stable'],
     ['Published', info.publishedAt || '—'], ['Status', info.detail || info.error || '—']]
      .forEach(([k, v]) => { list.appendChild(el('dt', { text: k })); list.appendChild(el('dd', { text: String(v) })); });
    if (info.notes) {
      list.appendChild(el('dt', { text: 'Release notes' }));
      list.appendChild(el('dd', { text: info.notes.slice(0, 1200) }));
    }
    $('updateDownload').disabled = !info.available;
    $('updateApply').disabled = !downloadedInstaller;
    if (explicit) note($('updateNote'), info.available ? 'warn' : (info.ok ? 'ok' : 'bad'),
      info.detail || info.error || '');
  }

  $('updateCheck').addEventListener('click', () => checkUpdates(true));
  $('updateDownload').addEventListener('click', async () => {
    const btn = $('updateDownload');
    btn.disabled = true;
    note($('updateNote'), 'warn', 'Downloading and verifying…');
    const started = await D.post('updates/download', {});
    if (!started.ok) { outcome($('updateNote'), started); return; }
    await D.pollTask((s) => {
      if (s && s.running) note($('updateNote'), 'warn', 'Downloading… ' + Math.round(s.elapsedSeconds || 0) + 's');
    });
    const final = await D.get('task');
    const r = final.result || {};
    if (r.ok) {
      downloadedInstaller = r.path;
      $('updateApply').disabled = false;
      note($('updateNote'), 'ok', `Downloaded and verified (SHA-256 ${String(r.sha256).slice(0, 16)}…). Click Install when you are ready.`);
    } else {
      note($('updateNote'), 'bad', r.error || final.error || 'The download failed.');
      btn.disabled = false;
    }
  });
  $('updateApply').addEventListener('click', async () => {
    const res = await D.post('updates/apply', { path: downloadedInstaller });
    outcome($('updateNote'), res, res.detail);
  });

  /* ------------------------------------------------------ credentials */
  RENDERERS.credentials = async function () {
    const res = await D.get('credentials');
    const container = $('credList');
    clear(container);
    if (res.offline) { container.appendChild(el('div', { class: 'empty', text: res.error })); return; }
    (res.credentials || []).forEach((cred) => {
      const input = el('input', {
        type: 'password', autocomplete: 'off',
        placeholder: cred.present ? '•••••••• (saved — type to replace)' : 'Paste your key here'
      });
      const save = el('button', { class: 'btn btn-sm btn-primary', text: 'Save' });
      save.addEventListener('click', async () => {
        const r = await D.post('credentials', { name: cred.name, value: input.value });
        input.value = '';
        outcome($('credNote'), r, cred.label + ' saved.');
        RENDERERS.credentials();
      });
      const del = el('button', { class: 'btn btn-sm btn-ghost', text: 'Remove' });
      del.addEventListener('click', async () => {
        const r = await D.post('credentials', { name: cred.name, value: '' });
        outcome($('credNote'), r, cred.label + ' removed.');
        RENDERERS.credentials();
      });
      container.appendChild(el('div', { class: 'field' }, [
        el('label', {}, [
          el('span', { text: cred.label + ' ' }),
          el('span', { class: 'pill ' + (cred.present ? 'ok' : 'info'), text: cred.present ? 'saved' : 'not set' }),
          el('span', { class: 'pill ' + (cred.encrypted ? 'ok' : 'warn'), text: cred.protection })
        ]),
        el('div', { class: 'help' }, [
          el('span', { text: cred.help + ' ' }),
          el('a', { href: cred.url, target: '_blank', rel: 'noopener', text: 'Get a key' })
        ]),
        input,
        el('div', { class: 'btnrow', style: 'margin-top:8px' }, [save, cred.present ? del : null])
      ]));
    });
  };

  /* ------------------------------------------------------------- logs */
  RENDERERS.logs = async function () {
    const res = await D.get('logs?lines=400');
    const container = $('logList');
    clear(container);
    if (res.offline) { container.appendChild(el('div', { class: 'empty', text: res.error })); return; }
    if (!(res.logs || []).length) {
      container.appendChild(el('div', { class: 'empty', text: 'No log files yet — the background service writes one as soon as it starts.' }));
      return;
    }
    res.logs.forEach((log) => {
      container.appendChild(el('h3', { text: log.name + ' (' + D.bytes(log.bytes) + ')',
                                       style: 'font-size:.85rem;margin:12px 0 6px' }));
      container.appendChild(el('div', { class: 'out', text: log.lines.join('\n') }));
    });
  };
  $('logRefresh').addEventListener('click', () => RENDERERS.logs());

  /* --------------------------------------------------------- settings */
  RENDERERS.settings = async function () {
    const res = await D.get('settings');
    settingsSchema = res.schema || [];
    const container = $('settingsFields');
    clear(container);
    if (res.offline) { container.appendChild(el('div', { class: 'empty', text: res.error })); return; }
    settingsSchema.forEach((spec) => container.appendChild(settingField(spec)));
  };

  function settingField(spec) {
    const id = 'cr_' + spec.key;
    if (spec.type === 'bool') {
      const input = el('input', { type: 'checkbox', id: id, 'data-key': spec.key });
      input.checked = !!spec.value;
      return el('div', { class: 'field' }, [
        el('label', { class: 'switch', for: id }, [input, el('span', { text: spec.label })]),
        el('div', { class: 'help', text: spec.help })
      ]);
    }
    let input;
    if (spec.choices) {
      input = el('select', { id: id, 'data-key': spec.key },
        spec.choices.map((c) => el('option', { value: c, text: c, selected: c === spec.value })));
    } else {
      input = el('input', {
        id: id, 'data-key': spec.key,
        type: spec.type === 'str' ? 'text' : 'number',
        step: spec.type === 'float' ? '0.01' : '1',
        min: spec.min, max: spec.max,
        value: spec.value === null || spec.value === undefined ? '' : spec.value
      });
    }
    return el('div', { class: 'field' }, [
      el('label', { for: id, text: spec.label }),
      el('div', { class: 'help', text: spec.help }),
      input
    ]);
  }

  $('settingsSave').addEventListener('click', async () => {
    const patch = {};
    document.querySelectorAll('#settingsFields [data-key]').forEach((input) => {
      const key = input.dataset.key;
      if (input.type === 'checkbox') patch[key] = input.checked;
      else if (input.type === 'number') { if (input.value !== '') patch[key] = Number(input.value); }
      else patch[key] = input.value;
    });
    const res = await D.post('settings', { settings: patch });
    outcome($('settingsNote'), res, 'Settings saved.');
    if (res.ok) refreshOverview();
  });
  $('openSetup').addEventListener('click', () => { window.location.href = 'setup.html'; });

  /* --------------------------------------------------------- start up */
  // Attribution is unchanged; it is just asked for ONCE. See
  // assets/js/identity.js.
  if (window.OWCSIdentity) window.OWCSIdentity.bindAll('[data-identity]');

  const initial = location.hash.slice(1);
  switchTo(document.querySelector(`#nav button[data-view="${initial}"]`) ? initial : 'overview');
  refreshOverview();
  setInterval(() => {
    refreshOverview();
    if (current === 'queue') RENDERERS.queue();
  }, 6000);
})();
