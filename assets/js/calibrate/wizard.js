/* =====================================================================
   wizard.js — the five screens that turn "I have a VOD" into a layout.

   Design rule for this page, which is different from the operator surfaces:
   the person using it may never have heard of a HUD layout. So every screen
   asks for exactly one thing, in plain words, and the page does the rest.
   Nothing is uploaded, nothing is installed, and there is no step that ends
   in "now run this command".

   The wizard never claims more than it measured. When the detector is
   unsure it says so and sends the user straight to the adjust step rather
   than presenting a guess as a result.
   ===================================================================== */
(function () {
  'use strict';

  const E = window.OWCSCalibrate;
  const $ = (id) => document.getElementById(id);
  const STEPS = ['source', 'frames', 'detect', 'adjust', 'done'];

  /* Captured frames: {imageData, dataUrl, time}. Held in memory only. */
  const shots = [];
  let stepIndex = 0;
  let result = null;       // last calibration result
  let boxes = { a: [], b: [] };
  let viewFrame = 0;       // which captured frame the adjust stage shows
  let adjusted = false;
  let objectUrl = null;

  /* --------------------------------------------------------- plumbing */

  function note(el, kind, msg) {
    el.className = 'note ' + (kind || '');
    el.textContent = msg || '';
  }

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ------------------------------------------------------- 0. handoff
     The portal can send someone here mid-flow, carrying the broadcast they
     were trying to convert. Arriving with the wizard already knowing which
     VOD it is for — and with a button straight to it — is the difference
     between "another page to work out" and "carry on where you were".      */

  const params = (() => {
    try { return new URLSearchParams(window.location.search); }
    catch (err) { return new URLSearchParams(''); }
  })();
  const handoffUrl = (params.get('url') || '').trim();
  const fromPortal = params.get('from') === 'portal' || !!handoffUrl;

  /** A YouTube id, purely so the layout can be given a sensible default name. */
  function videoIdOf(url) {
    const m = url.match(/[?&]v=([\w-]{6,})/) || url.match(/youtu\.be\/([\w-]{6,})/)
      || url.match(/youtube\.com\/live\/([\w-]{6,})/);
    return m ? m[1] : '';
  }

  if (fromPortal) {
    const back = $('calBack');
    if (back) {
      back.href = 'submit.html';
      back.textContent = '← Back to submitting a game';
    }
    const ret = $('backToPortal');
    if (ret) {
      ret.href = 'submit.html' + (handoffUrl
        ? '?url=' + encodeURIComponent(handoffUrl) : '');
    }
  }

  if (handoffUrl) {
    const box = $('calHandoff');
    if (box) {
      box.hidden = false;
      box.innerHTML =
        '<b>Calibrating for this broadcast</b>'
        + '<a class="cal-handoff-url" href="' + esc(handoffUrl) + '" target="_blank"'
        + ' rel="noopener noreferrer">' + esc(handoffUrl) + ' ↗</a>'
        + '<span>The wizard needs pictures, not a link — open the VOD, grab four to'
        + ' six screenshots of live play, and drop them below. The steps are spelled'
        + ' out under “I only have a YouTube link”.</span>';
    }
    /* That question is now the reason they are here, so it starts open. */
    const det = $('ytDetails');
    if (det) det.open = true;
    const open = $('ytOpen');
    if (open) {
      open.hidden = false;
      open.innerHTML = ' <a class="btn btn-ghost btn-tiny" href="' + esc(handoffUrl)
        + '" target="_blank" rel="noopener noreferrer">Open the VOD ↗</a>';
    }
  }

  function show(i) {
    stepIndex = Math.max(0, Math.min(STEPS.length - 1, i));
    STEPS.forEach((name, n) => {
      document.querySelector(`.cal-step[data-step="${name}"]`)
        .classList.toggle('active', n === stepIndex);
      document.querySelector(`#steps li[data-step="${name}"]`).dataset.state =
        n < stepIndex ? 'done' : (n === stepIndex ? 'active' : '');
    });
    window.scrollTo({ top: 0, behavior: 'auto' });
  }

  document.querySelectorAll('[data-back]').forEach((b) =>
    b.addEventListener('click', () => show(stepIndex - 1)));

  const fmtTime = (s) => {
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  };

  /* ------------------------------------------------------ 1. source */

  const drop = $('drop');
  const fileInput = $('fileInput');
  const video = $('video');

  ['dragenter', 'dragover'].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault(); drop.classList.add('over');
    }));
  ['dragleave', 'drop'].forEach((ev) =>
    drop.addEventListener(ev, () => drop.classList.remove('over')));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => handleFiles(fileInput.files));

  function handleFiles(list) {
    const files = Array.from(list || []);
    if (!files.length) return;
    const videos = files.filter((f) => f.type.startsWith('video/'));
    const images = files.filter((f) => f.type.startsWith('image/'));

    if (videos.length) {
      loadVideo(videos[0]);
      return;
    }
    if (images.length) {
      loadImages(images);
      return;
    }
    note($('sourceNote'), 'bad',
      'That was not a video or an image. Drop an MP4/MKV/WebM/MOV, or PNG/JPG '
      + 'screenshots.');
  }

  function loadVideo(file) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    video.src = objectUrl;
    note($('sourceNote'), '', '');
    video.addEventListener('loadedmetadata', function once() {
      video.removeEventListener('loadedmetadata', once);
      if (!video.videoWidth) {
        note($('sourceNote'), 'bad',
          `This browser could not decode ${file.name}. Try an MP4, or drop in `
          + 'screenshots instead.');
        return;
      }
      $('videoWrap').hidden = false;
      $('framesHint').innerHTML =
        'Find moments of <strong>live play</strong> — both teams\' hero '
        + 'portraits visible along the top. Avoid replays, the desk and kill '
        + 'cams. Four to six moments spread across the map is plenty.';
      $('scrub').max = String(Math.max(1, Math.floor(video.duration)));
      video.currentTime = Math.min(30, video.duration / 4);
      show(1);
    }, { once: true });
    video.addEventListener('error', () => {
      note($('sourceNote'), 'bad',
        `This browser could not open ${file.name}. Try an MP4, or use `
        + 'screenshots instead.');
    }, { once: true });
  }

  async function loadImages(files) {
    note($('sourceNote'), '', '');
    let added = 0, rejected = 0;
    for (const file of files) {
      try {
        const bitmap = await createImageBitmap(file);
        addShot(bitmap, null, bitmap.width, bitmap.height);
        added++;
      } catch (err) {
        rejected++;
      }
    }
    $('videoWrap').hidden = true;
    $('framesHint').innerHTML = added
      ? `Loaded <strong>${added}</strong> image(s). Each should show live play `
        + 'with both teams\' hero portraits along the top. Add more by dropping '
        + 'them on the previous screen.'
      : 'None of those images could be read.';
    if (rejected) {
      note($('framesNote'), 'warn', `${rejected} file(s) could not be read.`);
    }
    renderShots();
    show(1);
  }

  /* ------------------------------------------------------ 2. frames */

  const scrub = $('scrub');
  scrub.addEventListener('input', () => {
    if (!video.duration) return;
    video.currentTime = Number(scrub.value);
  });
  video.addEventListener('timeupdate', () => {
    $('timeLabel').textContent = fmtTime(video.currentTime);
    if (document.activeElement !== scrub) scrub.value = String(video.currentTime);
  });

  function addShot(source, time, w, h) {
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(source, 0, 0, w, h);
    shots.push({
      imageData: ctx.getImageData(0, 0, w, h),
      dataUrl: canvas.toDataURL('image/jpeg', 0.7),
      time, width: w, height: h,
    });
  }

  function captureCurrentFrame() {
    if (!video.videoWidth) return false;
    addShot(video, video.currentTime, video.videoWidth, video.videoHeight);
    return true;
  }

  $('capture').addEventListener('click', () => {
    if (captureCurrentFrame()) {
      renderShots();
      note($('framesNote'), '', '');
    }
  });

  $('autoCapture').addEventListener('click', async () => {
    const btn = $('autoCapture');
    btn.disabled = true;
    btn.textContent = 'Picking…';
    // Spread across the middle 80% of the video: the opening and the very
    // end are almost always the desk, a countdown, or credits.
    const d = video.duration;
    const times = [0.12, 0.28, 0.42, 0.56, 0.70, 0.84].map((f) => f * d);
    for (const t of times) {
      await seekTo(t);
      captureCurrentFrame();
      renderShots();
    }
    btn.disabled = false;
    btn.textContent = 'Pick 6 for me';
    note($('framesNote'), 'ok',
      'Picked six moments spread through the video. If any of them is a '
      + 'replay or the desk, remove it with the × and grab another.');
  });

  function seekTo(t) {
    return new Promise((resolve) => {
      const done = () => { video.removeEventListener('seeked', done); resolve(); };
      video.addEventListener('seeked', done);
      video.currentTime = Math.min(t, Math.max(0, video.duration - 0.1));
      setTimeout(resolve, 3000);           // never hang on a stubborn seek
    });
  }

  function renderShots() {
    const wrap = $('shots');
    wrap.textContent = '';
    shots.forEach((shot, i) => {
      const fig = document.createElement('div');
      fig.className = 'shot';
      const img = document.createElement('img');
      img.src = shot.dataUrl;
      img.alt = `Captured frame ${i + 1}`;
      const del = document.createElement('button');
      del.className = 'shot-del';
      del.type = 'button';
      del.textContent = '×';
      del.title = 'Remove this frame';
      del.addEventListener('click', () => {
        shots.splice(i, 1); renderShots();
      });
      fig.appendChild(img);
      if (shot.time !== null) {
        const t = document.createElement('span');
        t.className = 'shot-time';
        t.textContent = fmtTime(shot.time);
        fig.appendChild(t);
      }
      fig.appendChild(del);
      wrap.appendChild(fig);
    });
    $('toDetect').disabled = shots.length < 2;
    if (shots.length && shots.length < 2) {
      note($('framesNote'), 'warn',
        'Add at least one more. Comparing frames is how the tracker tells a '
        + 'hero portrait from the artwork around it.');
    }
  }

  /* ------------------------------------------------------ 3. detect */

  $('toDetect').addEventListener('click', () => { show(2); runDetection(); });
  $('redetect').addEventListener('click', () => { show(2); runDetection(); });

  function runDetection() {
    $('toAdjust').hidden = true;
    note($('detectNote'), '', '');
    setProgress(0, 'Starting…');
    // Yield first so the progress bar paints before the CPU work begins.
    setTimeout(() => {
      let out;
      try {
        out = E.calibrate(shots.map((s) => s.imageData), {
          onProgress: (f, label) => setProgress(f, label),
        });
      } catch (err) {
        note($('detectNote'), 'bad',
          `Something went wrong while searching: ${err.message}`);
        return;
      }
      result = out;
      if (!out.boxesA) {
        setProgress(1, 'No luck');
        note($('detectNote'), 'bad',
          (out.reasons || ['The hero portraits could not be found.']).join(' '));
        return;
      }
      boxes = { a: out.boxesA.map((b) => b.slice()), b: out.boxesB.map((b) => b.slice()) };
      adjusted = false;
      setProgress(1, 'Found them');

      const pct = Math.round(out.confidence * 100);
      if (out.ok && !out.reasons.length) {
        note($('detectNote'), 'ok',
          `Found all ten hero slots — confidence ${pct}%. Have a quick look to `
          + 'be sure.');
      } else if (out.ok) {
        note($('detectNote'), 'warn',
          `Found all ten hero slots — confidence ${pct}%, with something worth `
          + `checking: ${out.reasons.join(' ')}`);
      } else {
        note($('detectNote'), 'warn',
          `Confidence is only ${pct}%, below the ${Math.round(out.floor * 100)}% `
          + 'the tracker trusts. The boxes below are its best attempt — drag '
          + 'them onto the heroes and it will work just as well. '
          + out.reasons.join(' '));
      }
      $('toAdjust').hidden = false;
    }, 60);
  }

  function setProgress(fraction, label) {
    $('progBar').style.width = Math.round(fraction * 100) + '%';
    if (label) $('progLabel').textContent = label;
  }

  $('toAdjust').addEventListener('click', () => { show(3); drawStage(); });

  /* ------------------------------------------------------ 4. adjust */

  $('nextFrame').addEventListener('click', () => {
    viewFrame = (viewFrame + 1) % shots.length; drawStage();
  });
  $('prevFrame').addEventListener('click', () => {
    viewFrame = (viewFrame - 1 + shots.length) % shots.length; drawStage();
  });

  function drawStage() {
    const shot = shots[Math.min(viewFrame, shots.length - 1)];
    if (!shot) return;
    const canvas = $('stageCanvas');
    canvas.width = shot.width;
    canvas.height = shot.height;
    canvas.getContext('2d').putImageData(shot.imageData, 0, 0);

    const stage = $('stage');
    stage.querySelectorAll('.cal-box').forEach((n) => n.remove());
    ['a', 'b'].forEach((side) => {
      boxes[side].forEach((box, i) => {
        stage.appendChild(makeBox(side, i, box, shot.width, shot.height));
      });
    });
    drawZoom();
    note($('adjustNote'), '',
      shots.length > 1
        ? `Showing frame ${viewFrame + 1} of ${shots.length}. Flick through them `
          + 'to be sure the boxes work on every one.'
        : '');
  }

  function makeBox(side, index, box, fw, fh) {
    const [x, y, w, h] = box;
    const el = document.createElement('div');
    el.className = 'cal-box';
    el.dataset.side = side;
    el.tabIndex = 0;
    el.style.left = (x / fw * 100) + '%';
    el.style.top = (y / fh * 100) + '%';
    el.style.width = (w / fw * 100) + '%';
    el.style.height = (h / fh * 100) + '%';

    const label = document.createElement('span');
    label.className = 'bx-label';
    label.textContent = `${side.toUpperCase()}${index + 1}`;
    const grip = document.createElement('span');
    grip.className = 'bx-grip';
    el.appendChild(label);
    el.appendChild(grip);

    el.addEventListener('pointerdown', (ev) => {
      const resizing = ev.target === grip;
      const rect = $('stage').getBoundingClientRect();
      const scale = fw / rect.width;
      const startX = ev.clientX, startY = ev.clientY;
      // Moving one box moves its whole row: the five slots of a team are a
      // rigid, evenly-spaced strip, so dragging them one at a time would be
      // five times the work and would let them drift out of alignment.
      const originals = boxes[side].map((b) => b.slice());
      el.setPointerCapture(ev.pointerId);
      document.querySelectorAll('.cal-box').forEach((n) => n.classList.remove('sel'));
      el.classList.add('sel');

      const move = (e) => {
        const dx = Math.round((e.clientX - startX) * scale);
        const dy = Math.round((e.clientY - startY) * scale);
        boxes[side] = originals.map((b) => {
          if (resizing) {
            const size = Math.max(8, b[2] + dx);
            return [b[0], b[1], size, size];
          }
          return [
            Math.max(0, Math.min(fw - b[2], b[0] + dx)),
            Math.max(0, Math.min(fh - b[3], b[1] + dy)),
            b[2], b[3],
          ];
        });
        adjusted = true;
        syncBoxes(fw, fh);
        drawZoom();
      };
      const up = () => {
        el.releasePointerCapture(ev.pointerId);
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', up);
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', up);
      ev.preventDefault();
    });

    el.addEventListener('keydown', (ev) => {
      const step = ev.shiftKey ? 10 : 1;
      const map = {
        ArrowLeft: [-step, 0], ArrowRight: [step, 0],
        ArrowUp: [0, -step], ArrowDown: [0, step],
      };
      if (!map[ev.key]) return;
      ev.preventDefault();
      boxes[side] = boxes[side].map((b) => [
        Math.max(0, Math.min(fw - b[2], b[0] + map[ev.key][0])),
        Math.max(0, Math.min(fh - b[3], b[1] + map[ev.key][1])),
        b[2], b[3],
      ]);
      adjusted = true;
      syncBoxes(fw, fh);
      drawZoom();
    });
    return el;
  }

  function syncBoxes(fw, fh) {
    const nodes = $('stage').querySelectorAll('.cal-box');
    let i = 0;
    ['a', 'b'].forEach((side) => {
      boxes[side].forEach((b) => {
        const el = nodes[i++];
        if (!el) return;
        el.style.left = (b[0] / fw * 100) + '%';
        el.style.top = (b[1] / fh * 100) + '%';
        el.style.width = (b[2] / fw * 100) + '%';
        el.style.height = (b[3] / fh * 100) + '%';
      });
    });
  }

  /**
   * Ten zoomed crops under the frame. At broadcast scale a portrait box is
   * ~35px wide, which is far too small to judge on a laptop screen — this is
   * what actually lets someone answer "is that a hero?".
   */
  function drawZoom() {
    const strip = $('zoomStrip');
    const shot = shots[Math.min(viewFrame, shots.length - 1)];
    if (!shot) return;
    strip.textContent = '';
    const src = $('stageCanvas');
    ['a', 'b'].forEach((side) => {
      boxes[side].forEach((b, i) => {
        const fig = document.createElement('figure');
        fig.dataset.side = side;
        const c = document.createElement('canvas');
        c.width = 72; c.height = 72;
        const ctx = c.getContext('2d');
        ctx.imageSmoothingEnabled = false;
        try {
          ctx.drawImage(src, b[0], b[1], b[2], b[3], 0, 0, 72, 72);
        } catch (err) { /* box off-frame: leave the tile blank */ }
        const cap = document.createElement('figcaption');
        cap.textContent = `${side.toUpperCase()}${i + 1}`;
        fig.appendChild(c);
        fig.appendChild(cap);
        strip.appendChild(fig);
      });
    });
  }

  $('toDone').addEventListener('click', () => { show(4); renderSummary(); });

  /* -------------------------------------------------------- 5. done */

  function renderSummary() {
    const shot = shots[0];
    const dl = document.createElement('dl');
    const rows = [
      ['Video size', `${shot.width} × ${shot.height}`],
      ['Frames used', String(shots.length)],
      ['Hero slots', `${boxes.a.length + boxes.b.length} (5 per team)`],
      ['Portrait size', `${boxes.a[0][2]} × ${boxes.a[0][3]} pixels`],
      ['Confidence', `${Math.round((result.confidence || 0) * 100)}%`],
      ['Adjusted by hand', adjusted ? 'yes' : 'no'],
    ];
    rows.forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.appendChild(dt); dl.appendChild(dd);
    });
    const box = $('summary');
    box.textContent = '';
    box.appendChild(dl);
  }

  /** The cleaned-up layout id, and the layout itself, from the name field. */
  function cleanName() {
    const raw = $('layoutName').value.trim() || 'my-broadcast';
    const name = raw.toLowerCase().replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'my-broadcast';
    $('layoutName').value = name;
    return name;
  }

  function buildLayout(name) {
    return E.toLayout(
      Object.assign({}, result, { boxesA: boxes.a, boxesB: boxes.b }),
      { name, adjusted });
  }

  /**
   * Where the file has to end up, spelled out per platform.
   *
   * "Put it in layouts/" assumes the reader knows where the project is and
   * how to move a file into it from a terminal. Half of them do not, which
   * is exactly the gap that made the old walkthrough stall here.
   */
  function renderMoveHint(name) {
    const row = $('calMoveRow');
    if (!row) return;
    const win = /Win/i.test(navigator.platform || navigator.userAgent || '');
    const cmd = win
      ? 'move "%USERPROFILE%\\Downloads\\' + name + '.json" layouts\\'
      : 'mv ~/Downloads/' + name + '.json layouts/';
    row.innerHTML =
      '<p class="cal-cmdrow-lead">Or move it from the terminal, run from inside the'
      + ' project folder:</p>'
      + '<div class="cal-cmd"><code>' + esc(cmd) + '</code>'
      + '<button type="button" class="cal-cmd-copy" data-copy="' + esc(cmd)
      + '">copy</button></div>';
  }

  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest && ev.target.closest('.cal-cmd-copy');
    if (!btn) return;
    const text = btn.getAttribute('data-copy') || '';
    const done = () => {
      btn.textContent = 'copied';
      setTimeout(() => { btn.textContent = 'copy'; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else { done(); }
  });

  $('copyJson').addEventListener('click', () => {
    const name = cleanName();
    const text = JSON.stringify(buildLayout(name), null, 2) + '\n';
    const finish = (ok) => {
      note($('doneNote'), ok ? 'ok' : 'warn', ok
        ? 'Layout copied. Paste it into a new file called ' + name + '.json inside '
          + 'the project\'s layouts/ folder.'
        : 'Could not reach the clipboard. Use “Download my layout” instead.');
      $('nextSteps').hidden = false;
      renderMoveHint(name);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => finish(true), () => finish(false));
    } else { finish(false); }
  });

  $('download').addEventListener('click', () => {
    const name = cleanName();
    const layout = buildLayout(name);

    const blob = new Blob([JSON.stringify(layout, null, 2) + '\n'],
      { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 5000);

    note($('doneNote'), 'ok', `Saved ${name}.json to your downloads.`);
    $('nextSteps').hidden = false;
    renderMoveHint(name);
  });

  /**
   * Share the layout with the project.
   *
   * Anyone visiting the site may send one — a calibration for a broadcast
   * nobody has covered yet is the single most useful contribution there is,
   * and gatekeeping it would mean fewer broadcasts get read. What arrives is
   * still reviewed like any browser-built layout: it carries `browser_probe`,
   * not `hud_probe`, so it cannot enter production trusted.
   *
   * A pre-filled issue is the whole mechanism. The public site is static —
   * there is no backend to receive an upload — and inventing one would mean
   * a server, an account, and a moderation queue. This costs nothing, works
   * today, and the submitter reads every line before anything is sent.
   */
  $('share').addEventListener('click', () => {
    const name = ($('layoutName').value.trim() || 'my-broadcast')
      .toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
    const by = $('shareBy').value.trim();
    const layout = E.toLayout(
      Object.assign({}, result, { boxesA: boxes.a, boxesB: boxes.b }),
      { name, adjusted });

    const body = [
      `**Broadcast:** ${name}`,
      by ? `**Calibrated by:** ${by}` : '',
      `**Frame size:** ${result.frameW}×${result.frameH}`,
      `**Frames used:** ${shots.length}`,
      `**Confidence:** ${Math.round((result.confidence || 0) * 100)}%`,
      `**Adjusted by hand:** ${adjusted ? 'yes' : 'no'}`,
      '',
      'Built with the browser calibration wizard (`calibrate.html`).',
      'Carries `browser_probe`, not `hud_probe`, so it is not',
      'production-calibrated and its detections go through review.',
      '',
      '```json',
      JSON.stringify(layout, null, 2),
      '```',
    ].filter(Boolean).join('\n');

    const url = 'https://github.com/cvree/owcscomp.tracker/issues/new'
      + '?title=' + encodeURIComponent(`Layout: ${name}`)
      + '&body=' + encodeURIComponent(body);

    if (url.length > 7500) {
      // Some browsers and proxies truncate very long URLs silently, which
      // would submit a layout with its JSON cut in half. Say so rather than
      // let that happen.
      note($('doneNote'), 'warn',
        'This layout is too large to pre-fill a submission with. Download it '
        + 'and attach the file to a new issue instead.');
      return;
    }
    window.open(url, '_blank', 'noopener');
    note($('doneNote'), 'ok',
      'Opened a pre-filled submission in a new tab. Nothing is sent until you '
      + 'press submit there.');
  });

  $('startOver').addEventListener('click', () => { window.location.reload(); });

  /* A layout arriving from the portal gets named after the broadcast it came
     from, so the file is recognisable a month later without anyone having to
     invent a naming scheme on the spot. */
  const vid = videoIdOf(handoffUrl);
  if (vid) {
    $('layoutName').value = ('owcs-' + vid).toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-');
  }

  if (window.OWCSIdentity) window.OWCSIdentity.bindAll('[data-identity]');
  show(0);
})();
