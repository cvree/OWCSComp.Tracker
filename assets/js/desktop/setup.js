/* =====================================================================
   setup.js — the first-run wizard.

   Seven steps, and none of them lies about state. In particular:

     * the system step will not let you past a hard failure — it offers the
       repair the application actually knows how to perform, runs it, and
       re-checks. There is no "continue anyway" that leaves a machine that
       cannot decode video pretending it can;
     * the readiness step reports what the end-to-end suite really did. A
       skipped suite says skipped. Only a genuine pass turns the step green,
       and skipping is an explicit, labelled choice;
     * the finish step writes the setup marker only after the settings it
       collected are saved.
   ===================================================================== */
(function () {
  'use strict';
  const D = window.OWCSDesktop;
  const { el, clear, note, outcome } = D;

  const STEPS = ['welcome', 'checks', 'storage', 'keys', 'startup', 'test', 'done'];
  let index = 0;
  let schema = [];
  let testPassed = false;
  let testSkipped = false;

  const stepNode = (name) => document.querySelector(`.wizstep[data-step="${name}"]`);

  function show(i) {
    index = Math.max(0, Math.min(STEPS.length - 1, i));
    STEPS.forEach((name, n) => {
      stepNode(name).classList.toggle('active', n === index);
      const crumb = document.querySelector(`#steps li[data-step="${name}"]`);
      crumb.dataset.state = n < index ? 'done' : (n === index ? 'active' : '');
    });
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
    if (STEPS[index] === 'checks') runChecks();
    if (STEPS[index] === 'storage') renderSettings('storageFields',
      ['storageRoot', 'maxStorageGb', 'minFreeDiskGb', 'rawMediaRetentionDays']);
    if (STEPS[index] === 'keys') renderKeys();
    if (STEPS[index] === 'startup') renderSettings('startupFields',
      ['autoStart', 'startMinimized', 'autoPublish', 'autoPublishMinConfidence',
       'autoPublishMinRepeats', 'maxConcurrentJobs']);
  }

  document.querySelectorAll('[data-next]').forEach((b) =>
    b.addEventListener('click', () => show(index + 1)));
  document.querySelectorAll('[data-back]').forEach((b) =>
    b.addEventListener('click', () => show(index - 1)));

  /* ------------------------------------------------------- system step */
  async function runChecks() {
    const list = document.getElementById('checkList');
    clear(list);
    list.appendChild(el('div', { class: 'empty' }, [
      el('span', { class: 'spin' }), 'Running checks…']));

    const report = await D.get('health');
    clear(list);
    if (report.offline || !report.checks) {
      note(document.getElementById('checkNote'), 'bad', report.error || 'Could not run the checks.');
      return;
    }
    report.checks.forEach((c) => list.appendChild(checkRow(c)));

    const failures = report.counts.fail;
    document.getElementById('checksNext').disabled = failures > 0;
    note(document.getElementById('checkNote'),
      failures ? 'bad' : (report.counts.warn ? 'warn' : 'ok'),
      failures
        ? `${failures} thing(s) must be fixed before the app can process a broadcast. Use the fix buttons above.`
        : (report.counts.warn
            ? `Everything essential is working. ${report.counts.warn} optional item(s) are not set up — that is fine.`
            : 'Everything checks out.'));
  }

  function checkRow(c) {
    const actions = el('div', { class: 'actions' }, [
      el('span', { class: 'pill ' + (c.status === 'ok' ? 'ok' : c.status === 'warn' ? 'warn' : 'bad'), text: c.status })
    ]);
    if (c.repair && c.repair.indexOf('repair.') === 0) {
      const btn = el('button', { class: 'btn btn-sm btn-ghost', text: 'Fix this' });
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = 'Fixing…';
        const result = await D.post('repair', { action: c.repair });
        note(document.getElementById('checkNote'),
          result.ok ? 'ok' : 'bad', result.detail || result.error || '');
        await runChecks();
      });
      actions.appendChild(btn);
    }
    return el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [
        el('div', { class: 'name', text: c.label }),
        el('div', { class: 'detail', text: c.detail })
      ]),
      actions
    ]);
  }

  document.getElementById('recheck').addEventListener('click', runChecks);

  /* ---------------------------------------------------------- settings */
  async function loadSchema() {
    if (schema.length) return schema;
    const res = await D.get('settings');
    schema = res.schema || [];
    return schema;
  }

  async function renderSettings(containerId, keys) {
    const container = document.getElementById(containerId);
    clear(container);
    const all = await loadSchema();
    keys.forEach((key) => {
      const spec = all.find((s) => s.key === key);
      if (spec) container.appendChild(settingField(spec));
    });
  }

  function settingField(spec) {
    const id = 'set_' + spec.key;
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

  function collect(containerId) {
    const patch = {};
    document.querySelectorAll(`#${containerId} [data-key]`).forEach((input) => {
      const key = input.dataset.key;
      if (input.type === 'checkbox') patch[key] = input.checked;
      else if (input.type === 'number') {
        if (input.value !== '') patch[key] = Number(input.value);
      } else patch[key] = input.value;
    });
    return patch;
  }

  async function saveFrom(containerId, noteId) {
    const result = await D.post('settings', { settings: collect(containerId) });
    outcome(document.getElementById(noteId), result, 'Saved.');
    if (result.ok) schema = [];        // re-read so later steps show new values
    return result.ok;
  }

  document.getElementById('saveStorage').addEventListener('click', async () => {
    if (await saveFrom('storageFields', 'storageNote')) show(index + 1);
  });
  document.getElementById('saveStartup').addEventListener('click', async () => {
    if (await saveFrom('startupFields', 'startupNote')) show(index + 1);
  });

  /* ------------------------------------------------------------- keys */
  async function renderKeys() {
    const container = document.getElementById('keyFields');
    clear(container);
    const res = await D.get('credentials');
    (res.credentials || []).forEach((cred) => {
      const input = el('input', {
        type: 'password', id: 'cred_' + cred.name, autocomplete: 'off',
        placeholder: cred.present ? '•••••••• (saved — type to replace)' : 'Paste your key here'
      });
      const save = el('button', { class: 'btn btn-sm btn-primary', text: 'Save' });
      const del = el('button', { class: 'btn btn-sm btn-ghost', text: 'Remove' });
      save.addEventListener('click', async () => {
        const result = await D.post('credentials', { name: cred.name, value: input.value });
        input.value = '';
        outcome(document.getElementById('keyNote'), result, cred.label + ' saved.');
        renderKeys();
      });
      del.addEventListener('click', async () => {
        const result = await D.post('credentials', { name: cred.name, value: '' });
        outcome(document.getElementById('keyNote'), result, cred.label + ' removed.');
        renderKeys();
      });
      container.appendChild(el('div', { class: 'field' }, [
        el('label', { for: 'cred_' + cred.name }, [
          el('span', { text: cred.label + ' ' }),
          el('span', { class: 'pill ' + (cred.present ? 'ok' : 'info'),
                       text: cred.present ? 'saved' : 'not set' })
        ]),
        el('div', { class: 'help' }, [
          el('span', { text: cred.help + ' ' }),
          el('a', { href: cred.url, target: '_blank', rel: 'noopener', text: 'Get a key' })
        ]),
        input,
        el('div', { class: 'btnrow', style: 'margin-top:8px' }, [save, cred.present ? del : null]),
        el('div', { class: 'help', style: 'margin-top:6px',
                    text: cred.encrypted
                      ? 'Encrypted with Windows DPAPI for your Windows account.'
                      : 'Stored with file permissions only — Windows encryption is not available on this platform.' })
      ]));
    });
  }

  /* ------------------------------------------------------------- test */
  const testNote = document.getElementById('testNote');
  const testOut = document.getElementById('testOut');

  document.getElementById('runTest').addEventListener('click', async () => {
    const btn = document.getElementById('runTest');
    btn.disabled = true;
    testOut.textContent = '';
    note(testNote, 'warn', 'Building a test broadcast and running the pipeline over it…');

    const started = await D.post('readiness', {});
    if (!started.ok) {
      note(testNote, 'bad', started.error || started.detail || 'Could not start the test.');
      btn.disabled = false;
      return;
    }
    await D.pollTask((snap) => {
      if (snap && snap.running) {
        note(testNote, 'warn', `Running… ${Math.round(snap.elapsedSeconds || 0)}s elapsed. You can leave this window open.`);
      }
    });
    const final = await D.get('task');
    btn.disabled = false;
    const report = final.result;
    if (final.error || !report) {
      note(testNote, 'bad', final.error || 'The test did not produce a result.');
      return;
    }
    testOut.textContent = (report.suites || [])
      .map((s) => `${s.status.toUpperCase()}  ${s.label}\n${(s.detail || '').split('\n').slice(-6).join('\n')}`)
      .join('\n\n');
    testPassed = !!report.ok;
    document.getElementById('testNext').disabled = !testPassed;
    note(testNote, report.ok ? 'ok' : 'bad',
      report.ok
        ? `This PC processed a broadcast end to end. ${report.passed} suite(s) passed.`
        : `${report.failed} suite(s) failed and ${report.skipped} were skipped. The details are below — "Fix this" on the System step usually clears it.`);
  });

  document.getElementById('skipTest').addEventListener('click', () => {
    testSkipped = true;
    document.getElementById('testNext').disabled = false;
    note(testNote, 'warn',
      'Skipped. Setup will finish, but nothing has proved this PC can process a broadcast yet — ' +
      'you can run this any time from the control room\'s Health page.');
  });

  /* ------------------------------------------------------------- done */
  document.getElementById('finish').addEventListener('click', async () => {
    const by = document.getElementById('setupBy').value.trim();
    const result = await D.post('setup/complete', { by: by });
    if (!result.ok) {
      outcome(document.getElementById('doneNote'), result);
      return;
    }
    await D.post('worker/start', {});
    note(document.getElementById('doneNote'), 'ok',
      testPassed ? 'Setup complete. Opening the control room…'
                 : 'Setup complete (readiness test ' + (testSkipped ? 'skipped' : 'not passed') + '). Opening the control room…');
    setTimeout(() => { window.location.href = 'control-room.html'; }, 900);
  });

  if (window.OWCSIdentity) window.OWCSIdentity.bindAll('[data-identity]');
  show(0);
})();
