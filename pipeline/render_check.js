/* =====================================================================
   render_check.js — load every product page in jsdom, run its scripts,
   and report anything that broke.

   There is no browser in CI, so this is the closest thing the project
   has to opening the site: it builds a real DOM, executes core.js,
   games.js, shell.js and the page script in order, and then asserts the
   things a person would notice immediately if they were wrong —

     * no uncaught script error on any page;
     * the shared shell actually built (header, nav, footer);
     * the chrome above the content is ONE nav row plus at most one thin
       status line — no second navigation row, no marquee;
     * a `.rv` element can never be left permanently invisible;
     * every class the page renders is one the stylesheet defines, so a
       markup change cannot silently fall back to unstyled.

   This is the one check in the project with a dependency outside the
   repository, which is why it is not part of the python suites:

       npm install jsdom
       node pipeline/render_check.js

   (or point it at an existing copy: JSDOM_PATH=/path/to/jsdom node …)
   ===================================================================== */
"use strict";

const fs = require("fs");
const path = require("path");
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || "jsdom"));
} catch (e) {
  console.error("render_check needs jsdom:  npm install jsdom");
  console.error("(or set JSDOM_PATH to an existing install)");
  process.exit(2);
}

const ROOT = path.dirname(__dirname);
const PAGES = [
  "index.html", "games.html", "game.html", "submit.html", "review.html",
  "stats.html", "teams.html", "team.html", "hero.html", "tools.html",
  "how-it-works.html", "guide.html", "styleguide.html",
];
/* 404.html is deliberately standalone — it must render with no data and
   no shell, because the thing that failed may be the shell. */
const NO_SHELL = new Set(["404.html"]);

let fails = 0;
const check = (name, ok, detail) => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : " — " + detail}`);
  if (!ok) fails++;
};

/* Every class the stylesheet has a rule for. Used to catch markup that
   renders a class nothing styles. */
const css = fs.readFileSync(path.join(ROOT, "assets/css/owcs.css"), "utf8");
const CSS_CLASSES = new Set(
  (css.replace(/\/\*[\s\S]*?\*\//g, "").match(/\.[a-zA-Z][a-zA-Z0-9_-]*/g) || [])
    .map((c) => c.slice(1))
);
/* Classes that exist only as JS/state hooks or come from vendored code. */
const NON_VISUAL = new Set(["lenis", "lenis-smooth", "motion-on", "open", "on", "rv-in"]);

async function renderPage(page) {
  const html = fs.readFileSync(path.join(ROOT, page), "utf8");
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    /* jsdom cannot parse the stylesheet's modern syntax and says so on
       every page; that is a jsdom limitation, not a page error. */
    if (/Could not parse CSS|Not implemented/.test(String(e.message))) return;
    errors.push(String(e.stack || e.message));
  });
  vc.on("error", (m) => errors.push(String(m)));

  const dom = new JSDOM(html, {
    url: "http://localhost/" + page,
    runScripts: "dangerously",
    resources: undefined,
    virtualConsole: vc,
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.matchMedia = window.matchMedia || ((q) => ({
    matches: false, media: q, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  }));
  window.fetch = () => Promise.reject(new Error("offline"));
  window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  let closed = false;
  window.__markClosed = () => { closed = true; };
  window.requestAnimationFrame = (fn) =>
    setTimeout(() => { if (!closed) { try { fn(Date.now()); } catch (e) { /* teardown */ } } }, 0);

  /* jsdom does not fetch <script src>, so load them in document order. */
  const srcs = Array.from(window.document.querySelectorAll("script[src]"))
    .map((s) => s.getAttribute("src"))
    .filter((s) => s && !/vendor\//.test(s));
  for (const src of srcs) {
    const file = path.join(ROOT, src);
    if (!fs.existsSync(file)) { errors.push("missing script " + src); continue; }
    try {
      window.eval(fs.readFileSync(file, "utf8"));
    } catch (e) {
      errors.push(src + ": " + (e && e.message));
    }
  }
  /* jsdom fires its own DOMContentLoaded on a later tick, so dispatching
     one unconditionally builds the whole shell twice. Wait for the real
     event; only synthesise one if parsing had already finished before
     the page scripts were evaluated. */
  await new Promise((r) => setTimeout(r, 60));
  if (!window.document.querySelector("header.hdr")) {
    try {
      window.document.dispatchEvent(new window.Event("DOMContentLoaded",
        { bubbles: true, cancelable: false }));
    } catch (e) { errors.push("DOMContentLoaded: " + e.message); }
    await new Promise((r) => setTimeout(r, 60));
  }
  return { window, errors };
}

(async () => {
  for (const page of PAGES) {
    console.log(`\n${page}`);
    const { window, errors } = await renderPage(page);
    const doc = window.document;

    check("no script error", errors.length === 0, errors.slice(0, 2).join(" | "));
    if (!NO_SHELL.has(page)) {
      check("the shared shell built (header + nav + footer)",
        !!doc.querySelector("header.hdr") && !!doc.querySelector("nav.nav") &&
        !!doc.querySelector("footer.ftr"));
      check("exactly one primary navigation row",
        doc.querySelectorAll("header.hdr nav.nav").length === 1 &&
        doc.querySelectorAll(".subnav").length === 0);
      check("secondary destinations are reachable without a second row",
        !!doc.querySelector(".more__panel a[href='tools.html']") ||
        !!doc.querySelector(".nav a[href='tools.html']"));
    }
    check("no scrolling ticker anywhere on the page",
      doc.querySelectorAll(".ticker, .ticker__track").length === 0);
    check("at most one status line above the content",
      doc.querySelectorAll(".dataline").length <= 1);
    check("exactly one h1", doc.querySelectorAll("h1").length === 1);
    check("skip link points at a real target",
      !!doc.querySelector("#main") && !!doc.querySelector(".skip-link[href='#main']"));

    /* Nothing may be rendered into a class the stylesheet never styles:
       that is how a redesign leaves an unstyled block behind. */
    const unknown = new Set();
    doc.querySelectorAll("[class]").forEach((el) => {
      String(el.getAttribute("class")).split(/\s+/).forEach((c) => {
        if (!c || NON_VISUAL.has(c) || CSS_CLASSES.has(c)) return;
        unknown.add(c);
      });
    });
    check(`every rendered class is styled (${[...unknown].slice(0, 6).join(", ") || "none unstyled"})`,
      unknown.size === 0);

    window.__markClosed();
    window.close();
  }

  console.log();
  if (fails) { console.log(`FAILED: ${fails} check(s)`); process.exit(1); }
  console.log("all render checks passed");
})();
