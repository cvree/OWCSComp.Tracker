/* =====================================================================
   tests/browser/lib.mjs — shared plumbing for the Chrome verification loop.

   No framework and no build step, like the site itself: a static file
   server, a browser launcher that prefers real Google Chrome and says so
   when it had to fall back, and a page probe that records everything a
   screenshot cannot show (console errors, failed requests, horizontal
   overflow, small touch targets).
   ===================================================================== */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2",
  ".ico": "image/x-icon", ".ics": "text/calendar", ".txt": "text/plain",
};

/* A static server shaped like GitHub Pages: files as they are, 404.html
   for anything missing, no caching games. */
export function serve(root = ROOT) {
  const server = http.createServer((req, res) => {
    let rel;
    try { rel = decodeURIComponent(new URL(req.url, "http://x").pathname); }
    catch (e) { res.writeHead(400); return res.end(); }
    if (rel.endsWith("/")) rel += "index.html";
    const file = path.join(root, rel);
    if (!file.startsWith(root)) { res.writeHead(403); return res.end(); }
    fs.stat(file, (err, st) => {
      if (err || !st.isFile()) {
        res.writeHead(404, { "content-type": MIME[".html"] });
        return fs.createReadStream(path.join(root, "404.html"))
          .on("error", () => res.end("not found")).pipe(res);
      }
      res.writeHead(200, {
        "content-type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
        "content-length": st.size,
        "cache-control": "no-cache",
      });
      fs.createReadStream(file).pipe(res);
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({
    url: "http://127.0.0.1:" + server.address().port,
    close: () => new Promise((r) => server.close(r)),
  })));
}

/* Playwright is resolved from the project first, then from a global
   install, so the same command works in CI (npm ci) and on a machine
   that only has `npm i -g playwright`. */
export function loadPlaywright() {
  const require = createRequire(import.meta.url);
  try { return require("playwright"); } catch (e) { /* fall through */ }
  try {
    const g = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
    return createRequire(path.join(g, "x.js"))("playwright");
  } catch (e) {
    throw new Error("Playwright is not installed. Run `npm ci` in tests/browser " +
      "(or `npm i -g playwright`).");
  }
}

/* Real Google Chrome first (channel: "chrome"). When it is not installed
   the run still happens, in Playwright's Chromium, and the report states
   the fallback rather than calling it Chrome. OWCS_BROWSER=chromium skips
   the attempt; OWCS_REQUIRE_CHROME=1 turns the fallback into a failure. */
export async function launch() {
  const { chromium } = loadPlaywright();
  const want = process.env.OWCS_BROWSER || "chrome";
  let browser = null, fallback = null;
  if (want === "chrome") {
    try { browser = await chromium.launch({ channel: "chrome" }); }
    catch (e) { fallback = String(e.message || e).split("\n")[0]; }
  }
  if (!browser) {
    if (process.env.OWCS_REQUIRE_CHROME === "1") {
      throw new Error("Google Chrome is required and could not be launched: " + fallback);
    }
    browser = await chromium.launch();
  }
  const version = browser.version();
  /* The chrome channel launching is not proof of Google Chrome: some
     images put a Chromium or "Chrome for Testing" build at Chrome's path.
     The browser's own brand list is the evidence. */
  let branded = false;
  try {
    const ctx = await browser.newContext();
    const pg = await ctx.newPage();
    branded = await pg.evaluate(() => !!(navigator.userAgentData &&
      navigator.userAgentData.brands.some((b) => b.brand === "Google Chrome")));
    await ctx.close();
  } catch (e) { /* treated as not branded */ }
  if (fallback == null && want === "chrome" && !branded) {
    fallback = "the chrome channel on this machine is a Chromium / Chrome for Testing build, not branded Google Chrome";
  }
  if (!branded && process.env.OWCS_REQUIRE_CHROME === "1") {
    await browser.close();
    throw new Error("Google Chrome is required: " + fallback);
  }
  return {
    browser,
    info: {
      engine: branded ? "Google Chrome" : "Chromium (Chrome for Testing / Playwright build)",
      realChrome: branded,
      version,
      fallbackReason: fallback,
    },
  };
}

export const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1024, height: 768 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "phone", width: 390, height: 844, mobile: true },
];

/* The wall clock the fixtures-backed checks run at. The site computes
   "live", "upcoming", countdowns and staleness from Date.now(), so a
   screenshot or assertion that depends on today is not repeatable. The
   live smoke run does not pin it. */
export const FROZEN_NOW = "2026-11-11T12:00:00Z";

export async function newPage(browser, vp, opts = {}) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: opts.scale || 1,
    hasTouch: !!vp.mobile,
    isMobile: !!vp.mobile,
    reducedMotion: opts.reducedMotion === false ? "no-preference" : "reduce",
    timezoneId: opts.timezone || "UTC",
    locale: "en-US",
    colorScheme: "dark",
  });
  const page = await context.newPage();
  if (opts.clock !== false) await page.clock.setFixedTime(new Date(opts.clock || FROZEN_NOW));
  const log = { console: [], pageErrors: [], failed: [], bad: [], requests: 0, bytes: 0 };
  page.on("console", (m) => {
    if (m.type() === "error") log.console.push(m.text().slice(0, 300));
  });
  page.on("pageerror", (e) => log.pageErrors.push(String(e.message || e).slice(0, 300)));
  page.on("requestfailed", (r) => {
    const why = (r.failure() && r.failure().errorText) || "";
    if (/ERR_ABORTED/.test(why) && opts.allowAborts !== false) return;
    log.failed.push(r.url().replace(/^https?:\/\/[^/]+/, "") + " " + why);
  });
  page.on("response", (r) => {
    log.requests += 1;
    if (r.status() >= 400) log.bad.push(r.status() + " " + r.url().replace(/^https?:\/\/127[^/]+/, ""));
  });
  return { context, page, log };
}

/* Everything measurable about a rendered page that a person would call a
   layout defect. Runs in the page. */
export async function probe(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out = {
      title: document.title,
      h1: (document.querySelector("h1") || {}).textContent || "",
      scrollWidth: document.documentElement.scrollWidth,
      viewport: vw,
      overflowX: document.documentElement.scrollWidth > vw + 1,
      offenders: [],
      smallTargets: [],
      unlabelled: [],
      brokenImages: [],
      mainChars: ((document.querySelector("main") || document.body).innerText || "").trim().length,
    };
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return false;
      const cs = getComputedStyle(el);
      return cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity > 0.01;
    };
    const scrollsInside = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const o = getComputedStyle(p).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
      }
      return false;
    };
    const name = (el) => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += "#" + el.id;
      const c = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (c.length) s += "." + c.join(".");
      return s;
    };
    if (out.overflowX) {
      const seen = new Set();
      document.querySelectorAll("body *").forEach((el) => {
        if (out.offenders.length >= 8 || !visible(el)) return;
        const r = el.getBoundingClientRect();
        if (r.right > vw + 1 && !scrollsInside(el)) {
          const n = name(el);
          if (!seen.has(n)) { seen.add(n); out.offenders.push(n + " → " + Math.round(r.right)); }
        }
      });
    }
    /* Target size, WCAG 2.2 AA (2.5.8): a target under 24 by 24 CSS pixels
       passes only when a 24px circle centred on it touches no other target
       (and no other undersized target's circle), or when it is a link
       inside a sentence. That is the criterion, so it is what is counted;
       "anything under 24px" would also flag every well-spaced table link. */
    const targets = [];
    document.querySelectorAll("a[href], button, select, input, [role=button], [role=tab], summary")
      .forEach((el) => {
        if (!visible(el) || el.disabled) return;
        if (el.classList.contains("skip-link")) return;
        const r = el.getBoundingClientRect();
        const inSentence = el.tagName === "A" && getComputedStyle(el).display === "inline"
          && el.parentElement && /^(P|LI|TD|SPAN|DD|FIGCAPTION|SMALL|LABEL|EM|STRONG)$/.test(el.parentElement.tagName)
          && (el.parentElement.textContent || "").trim().length > (el.textContent || "").trim().length + 8;
        /* A checkbox or radio inside its label is hit through the label. */
        const viaLabel = el.tagName === "INPUT" && /^(checkbox|radio)$/.test(el.type) && el.closest("label");
        if (!viaLabel) targets.push({ el, r, small: !inSentence && (r.width < 24 || r.height < 24) });
        const label = (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent
          || el.getAttribute("placeholder") || el.value || "").trim()
          || (el.querySelector("img[alt]:not([alt=''])") ? "img" : "")
          || (el.id && document.querySelector('label[for="' + el.id + '"]') ? "label" : "")
          || (el.closest("label") ? "label" : "")
          || (el.getAttribute("aria-labelledby") ? "ref" : "");
        if (!label && out.unlabelled.length < 8) out.unlabelled.push(name(el));
      });
    const seenT = new Map();
    const hitsRect = (cx, cy, rad, r) => {
      const dx = Math.max(r.left - cx, 0, cx - r.right), dy = Math.max(r.top - cy, 0, cy - r.bottom);
      return dx * dx + dy * dy < rad * rad;
    };
    /* Only neighbours close enough to matter are compared (rows are sorted
       by vertical position), so a page with thousands of links stays fast. */
    targets.sort((a, b) => a.r.top - b.r.top);
    targets.forEach((t, i) => {
      if (!t.small) return;
      const cx = (t.r.left + t.r.right) / 2, cy = (t.r.top + t.r.bottom) / 2;
      let clash = false;
      const test = (o) => {
        if (o === t || o.el.contains(t.el) || t.el.contains(o.el)) return;
        if (o.small) {
          const ox = (o.r.left + o.r.right) / 2, oy = (o.r.top + o.r.bottom) / 2;
          if ((ox - cx) * (ox - cx) + (oy - cy) * (oy - cy) < 24 * 24) clash = true;
        } else if (hitsRect(cx, cy, 12, o.r)) clash = true;
      };
      for (let k = i - 1; k >= 0 && !clash && targets[k].r.top > cy - 400; k--) test(targets[k]);
      for (let k = i + 1; k < targets.length && !clash && targets[k].r.top < cy + 30; k++) test(targets[k]);
      if (clash) {
        const n = name(t.el) + " " + Math.round(t.r.width) + "×" + Math.round(t.r.height);
        seenT.set(n, (seenT.get(n) || 0) + 1);
      }
    });
    out.smallTargetCount = 0;
    seenT.forEach((n, k) => { out.smallTargetCount += n; out.smallTargets.push(k + " ×" + n); });
    out.smallTargets = out.smallTargets.slice(0, 10);
    document.querySelectorAll("img").forEach((img) => {
      if (img.complete && img.naturalWidth === 0 && visible(img) && out.brokenImages.length < 8) {
        out.brokenImages.push(img.getAttribute("src"));
      }
    });
    return out;
  });
}

export async function timings(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const nav = performance.getEntriesByType("navigation")[0] || {};
    const res = performance.getEntriesByType("resource");
    const out = {
      domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
      load: Math.round(nav.loadEventEnd || 0),
      requests: res.length + 1,
      transferKB: Math.round((res.reduce((a, r) => a + (r.transferSize || 0), 0) + (nav.transferSize || 0)) / 1024),
      decodedKB: Math.round((res.reduce((a, r) => a + (r.decodedBodySize || 0), 0) + (nav.decodedBodySize || 0)) / 1024),
      scriptKB: Math.round(res.filter((r) => /\.js(\?|$)/.test(r.name)).reduce((a, r) => a + (r.decodedBodySize || 0), 0) / 1024),
      domNodes: document.getElementsByTagName("*").length,
      fcp: 0, lcp: 0, longTaskMs: 0,
    };
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    if (fcp) out.fcp = Math.round(fcp.startTime);
    try {
      new PerformanceObserver((l) => {
        const e = l.getEntries(); if (e.length) out.lcp = Math.round(e[e.length - 1].startTime);
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => {
        l.getEntries().forEach((e) => { out.longTaskMs += Math.round(e.duration); });
      }).observe({ type: "longtask", buffered: true });
    } catch (e) { /* older engines */ }
    setTimeout(() => resolve(out), 150);
  }));
}

export function ensureDir(d) { fs.mkdirSync(d, { recursive: true }); return d; }
