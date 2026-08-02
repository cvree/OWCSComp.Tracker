/* =====================================================================
   identity.js — "who are you?", asked once instead of every single time.

   Every action that changes the record is attributed: review decisions,
   layout edits, imports, intake. A published composition can be traced to
   the frame it came from AND to whoever signed it off.

   Giving a name is OPTIONAL. While this is a prototype every visitor is
   trusted to edit correctly, so nothing is refused for arriving unsigned —
   the audit row simply records `anonymous`. That is deliberate rather than
   lax: this field is free text that nothing verifies, so refusing a blank
   one protected nothing, while filling it in with a guess would put a false
   name into the very trail the column exists for.

   What it still avoids is making someone retype their name into a different
   box on every screen. So the name is remembered locally and pre-filled
   everywhere, for the people who do want their work credited.

   Stored in localStorage, per browser. It is a label, not a credential:
   nothing is authenticated by it and nothing should be. It exists so an
   audit row can say who to ask about a decision.
   ===================================================================== */
window.OWCSIdentity = (function () {
  'use strict';

  const KEY = 'owcs.identity.name';

  function get() {
    try {
      return (window.localStorage.getItem(KEY) || '').trim();
    } catch (err) {
      // Private browsing, or storage disabled. Falling back to "no
      // remembered name" is correct: the field is simply empty and the user
      // types it, exactly as before.
      return '';
    }
  }

  function set(name) {
    const clean = String(name || '').trim().slice(0, 120);
    try {
      if (clean) window.localStorage.setItem(KEY, clean);
      else window.localStorage.removeItem(KEY);
    } catch (err) { /* nothing to do; the value just isn't remembered */ }
    return clean;
  }

  /**
   * Pre-fill an input with the remembered name and keep it in sync.
   * Every name field in the app goes through here, so typing it into any
   * one of them fills in all the others next time.
   */
  function bind(input) {
    if (!input) return;
    const remembered = get();
    if (remembered && !input.value) input.value = remembered;
    input.addEventListener('change', () => set(input.value));
    input.addEventListener('blur', () => set(input.value));
  }

  /** Bind every name field on the page at once. */
  function bindAll(selector) {
    document.querySelectorAll(selector || '[data-identity]')
      .forEach((el) => bind(el));
  }

  return { get, set, bind, bindAll, KEY };
})();
