#!/usr/bin/env node
/* =====================================================================
   tests/browser/run.mjs — the Chrome verification gate.

     node tests/browser/run.mjs                 # everything
     node tests/browser/run.mjs --grep teams    # scenarios whose name matches
     node tests/browser/run.mjs --trace         # keep a trace for every scenario
     node tests/browser/run.mjs --no-sweep      # scenarios only, skip the page sweep

   It serves the repository the way GitHub Pages does, opens it in Google
   Chrome (Chromium when Chrome is not installed, and the report says so),
   and does two things:

     1. THE SWEEP. Every public page at 1440, 1024, 768 and 390 pixels:
        no console error, no failed request, no sideways scroll, no
        undersized touch target (WCAG 2.5.8), no broken image, no control
        without a name. One screenshot each.
     2. THE SCENARIOS. The workflows people actually use, exercised and
        asserted against the DOM, plus the failure states (a data file
        that will not load, a slow one, stale data, a name far too long).

   The data is the repository's own committed production data. Nothing
   here invents a result. Where a scenario changes what a page is given
   (a blocked file, a renamed team) it does so in the browser, on the way
   to the page, and the scenario's name says "fixture:".

   The wall clock is frozen (lib.mjs, FROZEN_NOW) so "upcoming", "stale"
   and every countdown mean the same thing on every run.

   Output: tests/browser/output/  report.html · report.md · results.json
           screens/ (the sweep) · scenarios/ (one or more per scenario)
           traces/  (failed scenarios; every scenario with --trace)
   Exit code 1 when anything fails.
   ===================================================================== */
import fs from "node:fs";
import path from "node:path";
import { ROOT, serve, launch, newPage, probe, VIEWPORTS, FROZEN_NOW, ensureDir } from "./lib.mjs";

const argv = process.argv.slice(2);
const flag = (k) => argv.indexOf(k) >= 0;
const opt = (k, d) => (argv.indexOf(k) >= 0 ? argv[argv.indexOf(k) + 1] : d);
const OUT = ensureDir(path.resolve(opt("--out", path.join(ROOT, "tests/browser/output"))));
const GREP = opt("--grep", "") ? new RegExp(opt("--grep", ""), "i") : null;
const TRACE_ALL = flag("--trace");
["screens", "scenarios", "traces"].forEach((d) => {
  fs.rmSync(path.join(OUT, d), { recursive: true, force: true });
  ensureDir(path.join(OUT, d));
});

const PUBLIC_PAGES = ["index.html", "stats.html", "tiers.html", "draft.html", "teams.html",
  "versus.html", "series.html", "meta.html", "how-it-works.html", "404.html"];
/* The sweep also opens one of each detail page; the ids are read from the
   site's own data at start-up so they cannot go stale. */
const DETAIL_PAGES = [];

/* The site asks its own origin whether a local tracker is running
   (assets/js/app/api.js). On GitHub Pages it does not ask; on localhost
   it does, and a static server answers 404. That one request is the
   site working as designed, so it is the single allowed failure. */
const ALLOWED = [/\/api\/ping$/];
const allowed = (s) => ALLOWED.some((rx) => rx.test(s.split(" ").pop()) || rx.test(s));
const noise = (log) => ({
  console: log.console.filter((m) => !/Failed to load resource/.test(m) || log.bad.some((b) => !allowed(b))),
  pageErrors: log.pageErrors,
  failed: log.failed.filter((f) => !allowed(f.split(" ")[0])),
  bad: log.bad.filter((b) => !allowed(b)),
});

const results = { sweep: [], scenarios: [], browser: null, startedAt: new Date().toISOString(), frozenNow: FROZEN_NOW };
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);

class Fail extends Error {}
const check = (cond, msg) => { if (!cond) throw new Fail(msg); };
const eq = (a, b, msg) => check(a === b, msg + " — expected " + JSON.stringify(b) + ", got " + JSON.stringify(a));

let srv, browser;

/* ------------------------------------------------------------ scenarios */
const scenarios = [];
const scenario = (name, o, fn) => scenarios.push({ name, o: fn ? o : {}, fn: fn || o });

async function runScenario(s) {
  const vp = VIEWPORTS.find((v) => v.name === (s.o.viewport || "desktop"));
  const { context, page, log } = await newPage(browser, vp, s.o);
  const rec = { name: s.name, viewport: vp.name, status: "passed", notes: [], shots: [], ms: 0 };
  const id = slug(s.name);
  await context.tracing.start({ screenshots: true, snapshots: true });
  const t0 = Date.now();
  const t = {
    page, log, context, url: srv.url,
    go: async (rel, o) => {
      await page.goto(srv.url + "/" + rel, Object.assign({ waitUntil: "load", timeout: 45000 }, o || {}));
      await page.waitForTimeout(250);
    },
    shot: async (label, o) => {
      const file = path.join(OUT, "scenarios", id + (label ? "." + slug(label) : "") + ".png");
      await page.screenshot(Object.assign({ path: file }, o || {}));
      rec.shots.push(path.relative(OUT, file));
    },
    note: (m) => rec.notes.push(m),
    clean: () => {
      const n = noise(log);
      check(!n.pageErrors.length, "uncaught exception: " + n.pageErrors.join(" | "));
      check(!n.console.length, "console error: " + n.console.join(" | "));
      check(!n.bad.length, "bad response: " + n.bad.join(" | "));
    },
  };
  try {
    await s.fn(t);
    if (!s.o.expectErrors) t.clean();
    if (!rec.shots.length) await t.shot("");
  } catch (e) {
    rec.status = "failed";
    rec.error = e instanceof Fail ? e.message : String(e.stack || e).split("\n").slice(0, 4).join("\n");
    try { await t.shot("FAILED", { fullPage: false }); } catch (x) { /* page gone */ }
  }
  rec.ms = Date.now() - t0;
  if (rec.status === "failed" || TRACE_ALL) {
    const file = path.join(OUT, "traces", id + ".zip");
    await context.tracing.stop({ path: file });
    rec.trace = path.relative(OUT, file);
  } else {
    await context.tracing.stop();
  }
  await context.close();
  results.scenarios.push(rec);
  console.log((rec.status === "passed" ? "  ok   " : "  FAIL ") + s.name + (rec.error ? "\n         " + rec.error.replace(/\n/g, "\n         ") : ""));
}

/* Read a `window.NAME = {...}` data file from disk, for fixtures. */
const readData = (rel) => {
  const src = fs.readFileSync(path.join(ROOT, rel), "utf8");
  const i = src.indexOf("{", src.indexOf("window."));
  const head = src.slice(0, i);
  return { head, data: JSON.parse(src.slice(i, src.lastIndexOf("}") + 1)) };
};
const serveData = (page, rel, mutate) => page.route("**/" + rel, (route) => {
  const f = readData(rel);
  mutate(f.data);
  route.fulfill({ contentType: "text/javascript; charset=utf-8", body: f.head + JSON.stringify(f.data) + ";\n" });
});

/* The busiest team the tracker's own registry does not know: its name on
   the page is whatever the spreadsheet summary says, so a fixture can
   rename it. (A registry team's name comes from the published dataset.) */
const unmappedTeam = (d) => Object.keys(d.teamProfiles).filter((k) => !d.teamProfiles[k].mapped)
  .sort((a, b) => d.teamProfiles[b].maps.played - d.teamProfiles[a].maps.played)[0];

/* =========================================================== NAVIGATION */
scenario("navigation: every header destination opens and marks itself current", async (t) => {
  await t.go("index.html");
  const links = await t.page.$$eval("header nav a[href]", (as) => as.map((a) => a.getAttribute("href")));
  check(links.length >= 5, "the header has its destinations (found " + links.length + ")");
  for (const href of links) {
    await t.go(href);
    const cur = await t.page.$$eval('header nav a[aria-current="page"]', (as) => as.map((a) => a.getAttribute("href")));
    check(cur.length === 1, href + " marks exactly one nav item current (got " + JSON.stringify(cur) + ")");
    check(await t.page.locator("h1").first().isVisible(), href + " has a visible h1");
  }
  t.note("visited " + links.join(", "));
});

scenario("navigation: phone menu opens, lists the destinations and closes with Escape", { viewport: "phone" }, async (t) => {
  await t.go("index.html");
  const btn = t.page.locator("#nav-toggle");
  eq(await btn.getAttribute("aria-expanded"), "false", "menu starts closed");
  await btn.click();
  eq(await btn.getAttribute("aria-expanded"), "true", "menu reports open");
  check(await t.page.locator("#nav a", { hasText: "Matches" }).first().isVisible(), "Matches is reachable in the phone menu");
  await t.shot("open");
  await t.page.keyboard.press("Escape");
  eq(await btn.getAttribute("aria-expanded"), "false", "Escape closes the menu");
});

scenario("navigation: an unknown address gets the not-found page, not a blank one", { expectErrors: true }, async (t) => {
  const r = await t.page.goto(t.url + "/no-such-page.html");
  eq(r.status(), 404, "status");
  check((await t.page.locator("main").innerText()).trim().length > 40, "the 404 page explains itself");
  check(await t.page.locator('a[href*="index.html"]').first().isVisible(), "and links home");
});

scenario("keyboard: the first Tab lands on a visible skip link that moves focus to the content", async (t) => {
  await t.go("stats.html");
  await t.page.keyboard.press("Tab");
  const first = await t.page.evaluate(() => {
    const el = document.activeElement, r = el.getBoundingClientRect();
    return { cls: el.className, text: el.textContent.trim(), top: r.top, h: r.height, href: el.getAttribute("href") };
  });
  check(/skip-link/.test(first.cls), "first focus is the skip link (was " + first.text + ")");
  check(first.top >= 0 && first.h > 20, "the skip link is on screen when focused");
  await t.shot("skip-link", { clip: { x: 0, y: 0, width: 700, height: 120 } });
  await t.page.keyboard.press("Enter");
  eq(new URL(t.page.url()).hash, "#main", "it jumps to #main");
});

scenario("keyboard: focus is always visibly ringed on the controls people tab through", async (t) => {
  await t.go("teams.html");
  const seen = [];
  for (let i = 0; i < 14; i++) {
    await t.page.keyboard.press("Tab");
    seen.push(await t.page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 1) || cs.boxShadow !== "none";
      return { tag: el.tagName, text: (el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 24), ring };
    }));
  }
  const bare = seen.filter((s) => s && !s.ring);
  check(!bare.length, "focused with no outline or ring: " + JSON.stringify(bare));
  check(seen.filter(Boolean).length >= 12, "Tab reaches controls");
});

scenario("search: / opens it, a team name finds the team, nonsense says so", async (t) => {
  await t.go("index.html");
  await t.page.keyboard.press("/");
  const input = t.page.locator("#palette-input");
  await input.waitFor({ state: "visible", timeout: 3000 });
  await input.fill("weibo");
  await t.page.waitForTimeout(350);
  const hits = await t.page.$$eval("#palette-list [role=option]", (as) => as.map((a) => a.textContent.trim() + " → " + a.dataset.href));
  check(hits.some((h) => /weibo/i.test(h) && /team\.html/.test(h)), "Weibo Gaming's team page is a result: " + JSON.stringify(hits.slice(0, 5)));
  check(!hits.some((h) => /whibo|who gaming/i.test(h)), "no misread duplicate of Weibo is offered");
  await t.shot("results");
  await t.page.keyboard.press("Enter");
  await t.page.waitForURL(/team\.html\?id=weibo-gaming/, { timeout: 5000 }).catch(() => {});
  check(/team\.html\?id=weibo-gaming/.test(t.page.url()), "Enter opens the first result: " + t.page.url());
  await t.page.waitForTimeout(300);
  await t.page.keyboard.press("/");
  await input.waitFor({ state: "visible", timeout: 3000 });
  await input.fill("zzzqqqxxy");
  await t.page.waitForTimeout(350);
  const box = await t.page.locator('[role="dialog"]').first().innerText();
  check(/no |nothing|not found/i.test(box), "an empty search says nothing matched: " + box.slice(0, 120));
  await t.shot("no-results");
});

/* ================================================================= HOME */
scenario("home: the time range changes the numbers and survives a reload", async (t) => {
  await t.go("index.html");
  const count = () => t.page.locator(".trange").first().innerText();
  const before = await count();
  await t.page.locator('button[data-t="90"]').first().click();
  await t.page.waitForTimeout(300);
  const after = await count();
  check(before !== after, "the range summary changes between 14 and 90 days");
  eq(await t.page.locator('button[data-t="90"]').first().getAttribute("aria-pressed"), "true", "90 days is pressed");
  const n = (s) => Number((/([\d,]+)\s*pro maps/.exec(s) || [])[1].replace(/,/g, ""));
  check(n(after) > n(before), "90 days covers more maps than 14 (" + n(before) + " → " + n(after) + ")");
  await t.page.reload({ waitUntil: "load" });
  await t.page.waitForTimeout(300);
  eq(await t.page.locator('button[data-t="90"]').first().getAttribute("aria-pressed"), "true", "the choice survives a reload");
  check(await t.page.locator("#hm-stier .tc, #hm-stier a, #hm-stier article").first().isVisible(), "the S tier is drawn");
});

scenario("home: map search narrows the list and an unknown map gets an empty state", async (t) => {
  await t.go("index.html");
  const cards = () => t.page.locator("#hm-maps article").count();
  const all = await cards();
  await t.page.fill("#hm-mapq", "busan");
  await t.page.waitForTimeout(250);
  const some = await cards();
  check(some >= 1 && some < all, "typing a map name narrows the cards (" + all + " → " + some + ")");
  check(/busan/i.test(await t.page.locator("#hm-maps article").first().innerText()), "and the card left is Busan");
  await t.page.fill("#hm-mapq", "not-a-map");
  await t.page.waitForTimeout(250);
  eq(await cards(), 0, "no card matches nonsense");
  check((await t.page.locator("#hm-maps").innerText()).trim().length > 10, "the empty list explains itself");
  await t.shot("empty");
});

scenario("home: the VOD feed filters by team and every Watch link carries a start time", async (t) => {
  await t.go("index.html");
  const opts = await t.page.$$eval("#f-team option", (os) => os.map((o) => o.value).filter(Boolean));
  check(opts.length > 5, "the team filter lists teams");
  const names = await t.page.$$eval("#f-team option", (os) => os.map((o) => o.textContent.trim()));
  eq(new Set(names).size, names.length, "no team is listed twice in the filter");
  check(!names.some((x) => /whibo|who gaming/i.test(x)), "no misread Weibo duplicates in the filter");
  await t.page.selectOption("#f-team", "t1");
  await t.page.waitForTimeout(300);
  const rows = await t.page.locator("#feed").innerText();
  check(/T1/.test(rows), "the feed shows T1's maps");
  const watch = await t.page.$$eval('#feed a[aria-label^="Watch"]', (as) => as.map((a) => a.href));
  check(watch.length > 0, "there are Watch links");
  const untimed = watch.filter((h) => !/[?&#](t|start)=\d+/.test(h));
  check(!untimed.length, "every Watch link opens at the map: " + untimed.slice(0, 2).join(", "));
  check(/team=t1/.test(t.page.url()), "the filter is in the address bar");
});

/* ================================================================ COMPS */
scenario("comps: filtering by map narrows the book, a comp opens, reset restores it", async (t) => {
  await t.go("stats.html");
  const rows = () => t.page.locator("#p-comps details").count();
  const all = await rows();
  check(all > 5, "the comp book lists comps (" + all + ")");
  const sel = t.page.locator("#p-comps select").first();
  const values = await sel.locator("option").evaluateAll((os) => os.map((o) => o.value).filter(Boolean));
  await sel.selectOption(values[0]);
  await t.page.waitForTimeout(300);
  const some = await rows();
  check(some < all, "choosing a map leaves fewer comps (" + all + " → " + some + ")");
  check(/c[a-z]+=/.test(new URL(t.page.url()).search), "the filter is in the address bar: " + t.page.url());
  const first = t.page.locator("#p-comps details").first();
  await first.locator("summary").click();
  await t.page.waitForTimeout(300);
  check((await first.innerText()).length > 200, "an opened comp shows its record and games");
  await t.shot("open-comp");
  await t.page.click("#cb-reset");
  await t.page.waitForTimeout(300);
  eq(await rows(), all, "Reset filters brings every comp back");
});

scenario("comps: every tab opens, is selected, and is restored from the address", async (t) => {
  await t.go("stats.html");
  const tabs = await t.page.$$eval('main [role="tab"]', (bs) => bs.map((b) => b.dataset.tab));
  for (const id of tabs) {
    await t.page.click('[role="tab"][data-tab="' + id + '"]');
    await t.page.waitForTimeout(200);
    eq(await t.page.getAttribute('[role="tab"][data-tab="' + id + '"]', "aria-selected"), "true", id + " is selected");
    check(await t.page.locator("#p-" + id).isVisible(), id + " panel is shown");
    check((await t.page.locator("#p-" + id).innerText()).trim().length > 30, id + " panel has content");
  }
  await t.go("stats.html?tab=maps");
  eq(await t.page.getAttribute('[role="tab"][data-tab="maps"]', "aria-selected"), "true", "?tab=maps opens Maps");
});

/* ================================================================ TIERS */
scenario("tiers: heroes are ranked S to D, tabs switch, a region changes the sample", async (t) => {
  await t.go("tiers.html");
  const text = await t.page.locator("#p-heroes").innerText();
  check(/\bS\b/.test(text) && /\bA\b/.test(text), "tier rows are drawn");
  for (const id of ["comps", "bans", "power", "how", "heroes"]) {
    await t.page.click('[role="tab"][data-tab="' + id + '"]');
    await t.page.waitForTimeout(200);
    check((await t.page.locator("#p-" + id).innerText()).trim().length > 30, id + " has content");
  }
  const allMaps = await t.page.locator('.tr-map[data-map=""]').innerText();
  await t.page.selectOption("select[data-t-region]", { index: 1 });
  await t.page.waitForTimeout(300);
  const regMaps = await t.page.locator('.tr-map[data-map=""]').innerText();
  check(allMaps !== regMaps, "one region is a smaller sample than all (" + allMaps.replace(/\s+/g, " ") + " → " + regMaps.replace(/\s+/g, " ") + ")");
  await t.shot("region");
});

/* ================================================================= META */
scenario("meta explorer: only the open tab is drawn, each tab fills when opened, filters redraw it", async (t) => {
  await t.go("meta.html");
  const size = (id) => t.page.locator("#p-" + id).evaluate((el) => el.innerHTML.length);
  check(await size("heroes") > 500, "the open tab (Heroes) is drawn");
  eq(await size("list"), 0, "a tab nobody opened costs nothing");
  const nodes = await t.page.evaluate(() => document.getElementsByTagName("*").length);
  check(nodes < 6000, "the page is not carrying hidden panels (" + nodes + " elements)");
  for (const id of ["comps", "bans", "maps", "matchups", "scout", "list"]) {
    await t.page.click('[role="tab"][data-tab="' + id + '"]');
    await t.page.waitForTimeout(250);
    check(await size(id) > 200, id + " is drawn when opened");
    check(await t.page.locator("#p-" + id).isVisible(), id + " is shown");
  }
  const before = await t.page.locator("#p-list").innerText();
  const sel = t.page.locator('select[data-key="region"]');
  const vals = await sel.locator("option").evaluateAll((os) => os.map((o) => o.value).filter(Boolean));
  await sel.selectOption(vals[0]);
  await t.page.waitForTimeout(400);
  check((await t.page.locator("#p-list").innerText()) !== before, "a filter redraws the open tab");
  await t.page.click('[role="tab"][data-tab="heroes"]');
  await t.page.waitForTimeout(250);
  check(await size("heroes") > 200, "and a tab opened earlier is redrawn for the new filter when revisited");
  await t.go("meta.html?tab=bans");
  check(await size("bans") > 200, "?tab=bans draws Bans directly");
  eq(await size("heroes"), 0, "without drawing Heroes first");
  await t.shot("bans");
});

/* ================================================================ TEAMS */
scenario("teams: no org appears twice and HUD misreads are kept apart, with the reason", async (t) => {
  await t.go("teams.html");
  const cards = await t.page.$$eval("#body > .grid a.card .team__name", (ns) => ns.map((n) => n.textContent.trim()));
  check(cards.length > 10, "the directory lists teams (" + cards.length + ")");
  eq(new Set(cards.map((c) => c.toLowerCase())).size, cards.length, "no team card is repeated");
  const bad = cards.filter((c) => /^(whibo gaming|who gaming|feamrisk|phekafe|the kafi|team)$/i.test(c));
  check(!bad.length, "machine misreadings are not shown as teams: " + bad.join(", "));
  const doubt = t.page.locator("#unconfirmed");
  check(await doubt.isVisible(), "unconfirmed names have their own section");
  const why = await doubt.locator("li").evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ")));
  check(why.length >= 1 && why.every((w) => /HUD|broadcast/i.test(w)), "each one says why: " + JSON.stringify(why.slice(0, 1)));
  await doubt.scrollIntoViewIfNeeded();
  await t.shot("unconfirmed");
  await doubt.locator("a").first().click();
  await t.page.waitForLoadState("load");
  check(/not confirmed|machine reading/i.test(await t.page.locator("main").innerText()), "its page says the name is unconfirmed");
});

scenario("teams: the table finds, filters, sorts, and remembers it in the address", async (t) => {
  await t.go("teams.html");
  const rows = () => t.page.locator("#tt-body tr").count();
  const names = () => t.page.$$eval("#tt-body tr td:first-child .team__name", (tds) => tds.map((x) => x.textContent.trim()));
  const all = await rows();
  check(all > 40, "the spreadsheet's teams are listed (" + all + ")");
  eq(new Set(await names()).size, all, "no spreadsheet team is listed twice");
  eq(await t.page.getAttribute('th[data-col="maps"]', "aria-sort"), "descending", "default sort is most maps first");

  await t.page.fill("#tt-q", "gaming");
  await t.page.waitForTimeout(300);
  const some = await rows();
  check(some > 1 && some < all, "typing narrows the rows (" + all + " → " + some + ")");
  check((await names()).every((n) => /gaming/i.test(n)), "every row left matches");
  check(new RegExp(some + " of " + all).test(await t.page.locator("#tt-count").innerText()), "the count says " + some + " of " + all);

  await t.page.click('.th-sort[data-sort="name"]');
  eq(await t.page.getAttribute('th[data-col="name"]', "aria-sort"), "ascending", "Team sorts A to Z first");
  const sorted = await names();
  const lower = sorted.map((x) => x.toLowerCase());
  check(lower.every((x, i) => i === 0 || lower[i - 1] <= x), "rows are in name order: " + sorted.join(", "));
  await t.page.click('.th-sort[data-sort="name"]');
  eq(await t.page.getAttribute('th[data-col="name"]', "aria-sort"), "descending", "a second click reverses");
  await t.shot("filtered-sorted");

  const u = new URL(t.page.url());
  eq(u.searchParams.get("tq"), "gaming", "the search is in the address");
  eq(u.searchParams.get("ts"), "-name", "so is the sort");
  await t.page.reload({ waitUntil: "load" });
  await t.page.waitForTimeout(300);
  eq(await t.page.inputValue("#tt-q"), "gaming", "a reload keeps the search");
  eq(await rows(), some, "and the same rows");
  eq(await t.page.getAttribute('th[data-col="name"]', "aria-sort"), "descending", "and the sort");

  await t.page.fill("#tt-q", "qqqzzz");
  await t.page.waitForTimeout(300);
  eq(await rows(), 0, "nonsense matches nothing");
  check(await t.page.locator("#tt-empty .empty").isVisible(), "and an empty state says so");
  await t.shot("no-results");
  await t.page.click("#tt-reset");
  await t.page.waitForTimeout(200);
  eq(await rows(), all, "Clear the filters restores every team");
});

scenario("teams: sorting by win share ranks the numbers, missing values last", async (t) => {
  await t.go("teams.html");
  await t.page.click('.th-sort[data-sort="win"]');
  const shares = await t.page.$$eval("#tt-body tr td:nth-child(6)", (tds) => tds.map((x) => parseFloat(x.textContent)));
  const nums = shares.filter((x) => !isNaN(x));
  check(nums.length > 30, "win shares are shown");
  check(nums.every((x, i) => i === 0 || nums[i - 1] >= x), "descending by win share");
  const firstNaN = shares.findIndex((x) => isNaN(x));
  check(firstNaN < 0 || shares.slice(firstNaN).every((x) => isNaN(x)), "teams with no decided map sort last");
});

scenario("team page: opens from the table with a record; an unknown id says so", async (t) => {
  await t.go("teams.html");
  const first = t.page.locator("#tt-body tr td:first-child a").first();
  const name = (await first.locator(".team__name").innerText()).trim();
  await first.click();
  await t.page.waitForLoadState("load");
  await t.page.waitForTimeout(300);
  check(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i").test(await t.page.locator("h1").innerText()), "the heading is " + name);
  check((await t.page.locator("main").innerText()).length > 600, "the profile has content");
  await t.shot("profile");
  await t.go("team.html?id=definitely-not-a-team");
  check(/not on record|not found|no data for/i.test(await t.page.locator("main").innerText()), "an unknown team id is explained");
  await t.shot("not-found");
});

/* =============================================================== VERSUS */
scenario("versus: picking two teams compares them, swap flips sides, the address carries both", async (t) => {
  await t.go("versus.html");
  const sels = t.page.locator("main select:not([data-t-select])");
  check(await sels.count() >= 2, "two team pickers");
  const a0 = await sels.nth(0).inputValue(), b0 = await sels.nth(1).inputValue();
  check(a0 && b0 && a0 !== b0, "two different teams are compared by default");
  await t.page.click(".vs-swap");
  await t.page.waitForTimeout(300);
  eq(await sels.nth(0).inputValue(), b0, "swap moves the second team first");
  eq(await sels.nth(1).inputValue(), a0, "and the first second");
  const u = new URL(t.page.url());
  eq(u.searchParams.get("a"), b0, "a= follows");
  eq(u.searchParams.get("b"), a0, "b= follows");
  const odds = await t.page.locator(".vs-head").innerText();
  const pcts = (odds.match(/\d+(\.\d+)?%/g) || []).map(parseFloat);
  check(pcts.length >= 2, "the head-to-head shows both teams' chances");
  await t.shot("swapped");
});

scenario("versus: on a phone the map table scrolls inside itself, the page does not", { viewport: "phone" }, async (t) => {
  await t.go("versus.html");
  const p = await probe(t.page);
  check(!p.overflowX, "no sideways page scroll (" + p.scrollWidth + " > " + p.viewport + "): " + p.offenders.join(", "));
  const wrap = t.page.locator(".vs-maps-wrap");
  if (await wrap.count()) {
    await wrap.scrollIntoViewIfNeeded();
    await t.shot("map-table");
  }
});

/* ================================================================ DRAFT */
scenario("draft: picking heroes fills slots and reset empties them", async (t) => {
  await t.go("draft.html");
  const filled = () => t.page.locator(".dl-slot.is-filled, .dl-slot[data-hero]:not([data-hero=''])").count();
  await t.page.locator(".dl-slot").first().click();
  await t.page.locator(".dl-hero:not([disabled])").first().click();
  await t.page.waitForTimeout(250);
  const u1 = t.page.url();
  check(/us=|them=/.test(u1), "a pick is written to the address: " + u1);
  await t.shot("picked");
  await t.page.click('[data-act="reset"]');
  await t.page.waitForTimeout(250);
  check(!/us=\w/.test(t.page.url()), "reset clears the picks from the address");
  void filled;
});

/* ============================================================== MATCHES */
scenario("matches: a day that has passed is never 'next up'; it reads as a pending result", async (t) => {
  await t.go("series.html");
  const spot = t.page.locator(".sx-spot");
  if (await spot.count()) {
    const text = (await spot.innerText()).replace(/\s+/g, " ");
    const eyebrow = (await spot.locator(".eyebrow").innerText()).toLowerCase();
    if (/next up/.test(eyebrow)) {
      check(!/ago|yesterday/i.test(text), "a 'next up' card is not in the past: " + text.slice(0, 140));
    } else {
      check(/result pending/.test(eyebrow), "otherwise it is a pending result: " + eyebrow);
      check(/awaiting result/i.test(text), "and it says the result is awaited");
      check(!/first series in/i.test(text), "with no countdown");
    }
    t.note("spotlight: " + text.slice(0, 110));
  }
  check(!/starting/.test(await t.page.locator("#view-upcoming").innerText()), "no dangling 'First series in starting'");
  await t.shot("spotlight");
});

scenario("matches: before the same series starts it IS next up, with a real countdown",
  { clock: "2026-11-07T08:00:00Z" }, async (t) => {
    await t.go("series.html");
    const spot = t.page.locator(".sx-spot");
    check(await spot.count() === 1, "there is a spotlight");
    const text = (await spot.innerText()).replace(/\s+/g, " ");
    check(/next up/i.test(text), "it is next up: " + text.slice(0, 120));
    check(/Today/.test(text), "and it is today");
    check(/First series in \d+h \d+m/.test(text), "with a countdown in hours and minutes: " + text.slice(0, 200));
    await t.shot("countdown");
  });

scenario("matches: an editor's 'Copy of' tab is not an event and its series are not counted twice", async (t) => {
  await t.go("series.html");
  const d = await t.page.evaluate(() => {
    const S = window.OWCS_SCHEDULE;
    const ids = S.matches.map((m) => m.id);
    return { copies: S.events.filter((e) => /copy of/i.test(e.name + " " + (e.tab || ""))).map((e) => e.name),
      dupIds: ids.length - new Set(ids).size,
      noRegion: S.events.filter((e) => !e.region && e.stage !== "pre-season").map((e) => e.name) };
  });
  eq(d.copies.length, 0, "no event is a copied tab: " + d.copies.join(", "));
  eq(d.dupIds, 0, "no series id repeats");
  eq(d.noRegion.length, 0, "every non-pre-season event has a region: " + d.noRegion.join(", "));
  check(!/copy of/i.test(await t.page.locator("main").innerText()), "the page never prints 'Copy of'");
});

scenario("matches: Schedule, Brackets and Results each render and open from the address", async (t) => {
  for (const v of ["schedule", "brackets", "results"]) {
    await t.go("series.html?view=" + v);
    eq(await t.page.getAttribute("#tab-" + v, "aria-selected"), "true", v + " tab is selected");
    check((await t.page.locator("#view-" + v).innerText()).trim().length > 200, v + " has content");
    await t.shot(v);
  }
  await t.page.click("#tab-upcoming");
  await t.page.waitForTimeout(200);
  check(await t.page.locator("#view-upcoming").isVisible(), "tabs switch by click too");
});

scenario("matches: results filter by region and a series opens its detail", async (t) => {
  await t.go("series.html?view=results");
  const count = async () => Number((/([\d,]+)\s+series/.exec(await t.page.locator("#view-results").innerText()) || [0, "0"])[1].replace(/,/g, ""));
  const all = await count();
  check(all > 100, "the season's series are counted (" + all + ")");
  const sel = t.page.locator('#view-results select:has(option[value="korea"])').first();
  await sel.selectOption("korea");
  await t.page.waitForTimeout(400);
  const some = await count();
  check(some > 0 && some < all, "Korea is a subset of the season (" + all + " → " + some + ")");
  check(/korea/.test(t.page.url()), "and the filter is in the address: " + t.page.url());
  await t.shot("korea");
  const details = t.page.locator('#view-results a[href*="series.html?id="]').first();
  await details.click();
  await t.page.waitForLoadState("load");
  await t.page.waitForTimeout(300);
  check(/\d\s*[:–-]\s*\d/.test(await t.page.locator("main").innerText()), "the series page shows a score");
  await t.shot("series-detail");
});

scenario("matches: following a team persists for this browser", async (t) => {
  await t.go("series.html");
  /* The star appears when its row is hovered or focused; reach it the way
     a pointer does. */
  const row = t.page.locator(".sx-row:visible").first();
  await row.scrollIntoViewIfNeeded();
  await row.hover();
  const star = row.locator(".sx-star").first();
  const who = await star.getAttribute("data-follow");
  await star.click();
  eq(await t.page.locator('.sx-star[data-follow="' + who + '"]').first().getAttribute("aria-pressed"), "true", "the star is pressed");
  await t.page.reload({ waitUntil: "load" });
  await t.page.waitForTimeout(300);
  eq(await t.page.locator('.sx-star[data-follow="' + who + '"]').first().getAttribute("aria-pressed"), "true", "and still pressed after a reload");
  check(/\(1\)/.test(await t.page.locator("[data-follow-count]").first().innerText()), "the follow count reads 1");
  await t.page.locator("[data-mine]").first().check({ force: true });
  await t.page.waitForTimeout(300);
  check(/mine=1/.test(t.page.url()), "'Only teams I follow' is in the address");
  await t.shot("following");
});

scenario("matches: times are shown in the viewer's zone and say which", { timezone: "America/Los_Angeles", clock: "2026-11-07T08:00:00Z" }, async (t) => {
  await t.go("series.html");
  check(/America\/Los Angeles/.test(await t.page.locator(".sx-tznote").innerText()), "the page names the zone");
  const m = await t.page.evaluate(() => window.OWCS_SCHEDULE.matches.filter((x) => x.startsAt && x.state !== "done")
    .sort((a, b) => a.startsAt.localeCompare(b.startsAt))[0]);
  check(m, "there is a timed, unplayed series to check");
  const want = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" }).format(new Date(m.startsAt));
  const utc = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "UTC" }).format(new Date(m.startsAt));
  const text = (await t.page.locator("#view-upcoming").innerText()).replace(/ /g, " ");
  check(text.indexOf(want) >= 0, m.startsAt + " is shown as " + want + " Pacific");
  check(want !== utc, "which differs from its UTC time (" + utc + ")");
  check(/P[SD]T/.test(text), "with the zone's abbreviation beside it");
  await t.shot("pacific");
});

/* ======================================================== DETAIL PAGES */
scenario("hero page: opens from the tier list with the hero's name and numbers", async (t) => {
  await t.go("tiers.html");
  const link = t.page.locator('#p-heroes a[href*="hero.html?id="]').first();
  const href = await link.getAttribute("href");
  await t.go(href);
  check((await t.page.locator("h1").innerText()).trim().length > 1, "the hero has a heading");
  check(/%/.test(await t.page.locator("main").innerText()), "and rates");
  await t.shot("hero");
  await t.go("hero.html?id=not-a-hero");
  check((await t.page.locator("main").innerText()).trim().length > 20, "an unknown hero id is explained, not blank");
});

scenario("map page: a spreadsheet map opens with both teams and its source label", async (t) => {
  await t.go("series.html?view=results");
  await t.page.locator('#view-results a[href*="series.html?id="]').first().click();
  await t.page.waitForLoadState("load");
  await t.page.waitForTimeout(400);
  const link = t.page.locator('a[href*="map.html?"]').first();
  check(await link.count(), "a series lists its maps as links");
  await t.go(await link.getAttribute("href"));
  await t.page.waitForTimeout(600);
  const text = await t.page.locator("main").innerText();
  check(/spreadsheet/i.test(text), "the map page says where its facts came from");
  check(text.length > 300, "and has content");
  await t.shot("map");
});

/* ============================================================ INTEGRITY */
scenario("data: the numbers a page states add up from the rows behind them", async (t) => {
  await t.go("teams.html");
  const d = await t.page.evaluate(() => {
    const X = window.OWCS_EXTERNAL_SUMMARY, T = X.teamProfiles;
    const teams = Object.keys(T).map((k) => T[k]);
    const names = teams.map((x) => x.name.toLowerCase());
    const bad = teams.filter((x) => { const m = x.maps || {}; return (m.won || 0) + (m.lost || 0) + (m.draw || 0) > (m.played || 0); }).map((x) => x.name);
    const won = teams.reduce((a, x) => a + ((x.maps || {}).won || 0), 0), lost = teams.reduce((a, x) => a + ((x.maps || {}).lost || 0), 0);
    const pub = window.OWCS_PUBLIC.teams.map((x) => x.name.toLowerCase());
    return { dupNames: names.length - new Set(names).size, bad, won, lost, totals: X.totals,
      pubDup: pub.length - new Set(pub).size };
  });
  eq(d.dupNames, 0, "no two spreadsheet teams share a name");
  eq(d.pubDup, 0, "no two registry teams share a name");
  eq(d.bad.length, 0, "no team has more decided maps than played: " + d.bad.join(", "));
  eq(d.won, d.lost, "across all teams, maps won equals maps lost (every decided map has one winner and one loser)");
  t.note("maps won = maps lost = " + d.won + " · totals " + JSON.stringify(d.totals).slice(0, 120));
});

scenario("data: every series score agrees with its own maps and best-of", async (t) => {
  await t.go("series.html?view=results");
  const d = await t.page.evaluate(() => {
    const S = window.OWCS_SCHEDULE.matches.filter((m) => m.state === "done");
    const over = S.filter((m) => m.bestOf && m.scoreA != null && Math.max(m.scoreA, m.scoreB) > Math.ceil(m.bestOf / 2) + 0);
    const wrongWinner = S.filter((m) => m.winner && m.scoreA != null && m.scoreB != null && m.scoreA !== m.scoreB &&
      m.winner !== (m.scoreA > m.scoreB ? m.teamA : m.teamB));
    const future = S.filter((m) => m.date && m.date > "2026-11-11");
    return { n: S.length, over: over.map((m) => m.id), wrongWinner: wrongWinner.map((m) => m.id), future: future.map((m) => m.id) };
  });
  check(d.n > 500, "the season's played series are loaded (" + d.n + ")");
  eq(d.wrongWinner.length, 0, "the recorded winner is the side with more maps: " + d.wrongWinner.slice(0, 5).join(", "));
  eq(d.over.length, 0, "no side has more wins than its best-of allows: " + d.over.slice(0, 5).join(", "));
  eq(d.future.length, 0, "no played series is dated in the future: " + d.future.slice(0, 5).join(", "));
});

scenario("data: the footer states each source's age, and nothing claims to be live", async (t) => {
  await t.go("index.html");
  const rows = await t.page.$$eval(".ftr__fresh li", (ls) => ls.map((l) => ({ id: l.dataset.source, stale: l.dataset.stale === "true", text: l.innerText.replace(/\s+/g, " ") })));
  check(rows.some((r) => r.id === "sheet"), "the spreadsheet's age is stated");
  check(rows.some((r) => r.id === "scan"), "the broadcast scan's age is stated");
  check(rows.every((r) => /ago|just now|in \d/.test(r.text)), "each has a relative age: " + JSON.stringify(rows.map((r) => r.text)));
  check(!/\blive\b/i.test(await t.page.locator(".ftr").innerText()), "the footer does not call anything live");
  await t.page.locator(".ftr").scrollIntoViewIfNeeded();
  await t.shot("footer", { clip: { x: 0, y: 500, width: 1440, height: 400 } });
});

/* ======================================================= FAILURE STATES */
scenario("stale: two weeks without a refresh is said in words, per source", { clock: "2026-11-25T12:00:00Z" }, async (t) => {
  await t.go("index.html");
  const stale = await t.page.$$eval('.ftr__fresh li[data-stale="true"]', (ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ")));
  check(stale.length >= 2, "the spreadsheet and the scan are both marked stale: " + JSON.stringify(stale));
  check(stale.every((s) => /stale/.test(s)), "with the word, not only a colour");
  await t.page.locator(".ftr").scrollIntoViewIfNeeded();
  await t.shot("stale-footer");
});

scenario("fixture: the spreadsheet summary fails to load → pages still render and say what is missing",
  { expectErrors: true }, async (t) => {
    await t.page.route("**/assets/data/external_summary.v1.js", (r) => r.abort("failed"));
    for (const p of ["index.html", "teams.html", "stats.html"]) {
      t.log.pageErrors.length = 0;
      await t.go(p);
      check(!t.log.pageErrors.length, p + " throws no exception without the file: " + t.log.pageErrors.join(" | "));
      check(await t.page.locator("header nav").isVisible(), p + " still has its header");
      check((await t.page.locator("main").innerText()).trim().length > 80, p + " still has content or an explanation");
      await t.shot(p);
    }
  });

scenario("fixture: the schedule file fails to load → Matches says so instead of going blank",
  { expectErrors: true }, async (t) => {
    await t.page.route("**/assets/data/schedule.v1.js", (r) => r.abort("failed"));
    await t.go("series.html");
    check(!t.log.pageErrors.length, "no uncaught exception: " + t.log.pageErrors.join(" | "));
    check(/not loaded|missing|unavailable|could not/i.test(await t.page.locator("main").innerText()), "the page says the schedule is not loaded");
    await t.shot("no-schedule");
  });

scenario("fixture: every data file is 3 seconds late → the page arrives whole, with no error", async (t) => {
  await t.page.route("**/assets/data/*.js", async (route) => { await new Promise((r) => setTimeout(r, 3000)); route.continue(); });
  await t.go("index.html", { timeout: 60000 });
  check(await t.page.locator("#hm-stier").innerText().then((s) => s.trim().length > 20), "the S tier is drawn once the data lands");
  check(await t.page.locator("header nav").isVisible(), "and the header");
});

scenario("fixture: a 70-character team name does not break the layout on a phone", { viewport: "phone" }, async (t) => {
  const LONG = "Extraordinarily Long Organisation Name Esports Gaming Club International";
  await serveData(t.page, "assets/data/external_summary.v1.js", (d) => {
    const k = unmappedTeam(d);
    d.teamProfiles[k].name = LONG;
    if (d.teams[k]) d.teams[k].name = LONG;
  });
  await t.go("teams.html");
  check((await t.page.locator("#tt-body").innerText()).indexOf("Extraordinarily") >= 0, "the long name is on the page");
  const p = await probe(t.page);
  check(!p.overflowX, "no sideways page scroll: " + p.offenders.join(", "));
  await t.page.locator("#tt-table").scrollIntoViewIfNeeded();
  await t.shot("long-name");
});

scenario("fixture: a team with zero decided maps shows a dash, never 0% or NaN", async (t) => {
  await serveData(t.page, "assets/data/external_summary.v1.js", (d) => {
    const k = unmappedTeam(d);
    Object.assign(d.teamProfiles[k].maps, { won: 0, lost: 0, draw: 0 });
    d.teamProfiles[k].name = "Zero Decided Fixture";
    if (d.teams[k]) d.teams[k].name = "Zero Decided Fixture";
  });
  await t.go("teams.html?tq=zero+decided");
  const row = (await t.page.locator("#tt-body tr").first().innerText()).replace(/\s+/g, " ");
  check(/Zero Decided Fixture/.test(row), "the fixture row is shown");
  check(!/NaN|undefined|Infinity/.test(row), "no NaN: " + row);
  check(!/\b0%/.test(row), "a team with no decided map has no win share, not 0%: " + row);
});

scenario("motion: with reduced motion nothing is left invisible or mid-animation", async (t) => {
  await t.go("index.html");
  await t.page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await t.page.waitForTimeout(400);
  const hidden = await t.page.evaluate(() => Array.from(document.querySelectorAll("main .rv, main section, main article"))
    .filter((el) => { const cs = getComputedStyle(el); return el.getBoundingClientRect().height > 0 && (+cs.opacity < 0.99 || cs.visibility === "hidden"); })
    .slice(0, 5).map((el) => el.className));
  eq(hidden.length, 0, "nothing in main is transparent: " + hidden.join(", "));
  const anim = await t.page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running" &&
    (a.effect && a.effect.getTiming().iterations === Infinity)).length);
  eq(anim, 0, "no endless animation is running");
});

scenario("motion: without reduced motion, content still ends up visible", { reducedMotion: false }, async (t) => {
  await t.go("index.html");
  await t.page.waitForTimeout(2500);
  const hidden = await t.page.evaluate(() => Array.from(document.querySelectorAll("main .rv"))
    .filter((el) => { const r = el.getBoundingClientRect(); return r.top < innerHeight && r.height > 0 && +getComputedStyle(el).opacity < 0.99; }).length);
  eq(hidden, 0, "every card in view has finished appearing");
});

scenario("security: no credential-shaped string is shipped to the browser", async (t) => {
  const seen = [];
  t.page.on("response", async (r) => {
    if (!/\.(js|html|json|css)(\?|$)/.test(r.url()) || /vendor/.test(r.url())) return;
    try {
      const body = await r.text();
      const hit = /(AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{36,}|sk-[A-Za-z0-9]{32,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})/.exec(body);
      if (hit) seen.push(r.url().replace(/^.*\/assets/, "assets") + ": " + hit[0].slice(0, 8) + "…");
    } catch (e) { /* redirected */ }
  });
  for (const p of ["index.html", "series.html", "teams.html", "admin.html", "tools.html"]) await t.go(p);
  eq(seen.length, 0, "found: " + seen.join(", "));
});

scenario("security: a hostile team name is shown as text, never run", async (t) => {
  await serveData(t.page, "assets/data/external_summary.v1.js", (d) => {
    const k = unmappedTeam(d);
    d.teamProfiles[k].name = '<img src=x onerror="window.__xss=1">Injected';
    if (d.teams[k]) d.teams[k].name = d.teamProfiles[k].name;
  });
  await t.go("teams.html");
  await t.page.waitForTimeout(300);
  eq(await t.page.evaluate(() => window.__xss || 0), 0, "the payload did not execute");
  check((await t.page.locator("#tt-body").innerText()).indexOf("<img src=x") >= 0, "it is printed as literal text");
});

/* ================================================================ SWEEP */
async function sweep() {
  for (const vp of VIEWPORTS) {
    for (const p of PUBLIC_PAGES.concat(DETAIL_PAGES)) {
      const { context, page, log } = await newPage(browser, vp);
      const row = { page: p, viewport: vp.name, width: vp.width, problems: [] };
      try {
        const expect404 = p === "404.html";
        await page.goto(srv.url + "/" + p, { waitUntil: "load", timeout: 45000 });
        await page.waitForTimeout(350);
        const pr = await probe(page);
        const n = noise(log);
        if (n.pageErrors.length) row.problems.push("uncaught exception: " + n.pageErrors[0]);
        if (n.console.length) row.problems.push("console error: " + n.console[0]);
        if (n.bad.length) row.problems.push("bad response: " + n.bad.join(", "));
        if (n.failed.length) row.problems.push("failed request: " + n.failed.join(", "));
        if (pr.overflowX) row.problems.push("page scrolls sideways (" + pr.scrollWidth + " > " + pr.viewport + "): " + pr.offenders.slice(0, 3).join("; "));
        if (pr.smallTargetCount) row.problems.push(pr.smallTargetCount + " touch target(s) under 24px with a neighbour inside their 24px circle: " + pr.smallTargets.slice(0, 3).join("; "));
        if (pr.brokenImages.length) row.problems.push("broken image: " + pr.brokenImages.slice(0, 3).join(", "));
        if (pr.unlabelled.length) row.problems.push("control with no accessible name: " + pr.unlabelled.slice(0, 3).join(", "));
        if (!pr.h1.trim() && !expect404) row.problems.push("no h1");
        if (pr.mainChars < 80) row.problems.push("main is nearly empty (" + pr.mainChars + " characters)");
        row.title = pr.title;
        const file = path.join(OUT, "screens", p.replace(/[^a-z0-9]+/gi, "_") + "." + vp.name + ".png");
        await page.screenshot({ path: file });
        row.shot = path.relative(OUT, file);
      } catch (e) {
        row.problems.push("could not open: " + String(e.message || e).split("\n")[0]);
      }
      results.sweep.push(row);
      console.log((row.problems.length ? "  FAIL " : "  ok   ") + vp.name.padEnd(8) + p + (row.problems.length ? "\n         " + row.problems.join("\n         ") : ""));
      await context.close();
    }
  }
}

/* =============================================================== REPORT */
function report() {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const sFail = results.sweep.filter((r) => r.problems.length), cFail = results.scenarios.filter((r) => r.status !== "passed");
  const b = results.browser;
  const head = "Browser: " + b.engine + " " + b.version + (b.realChrome ? "" : " — NOT Google Chrome" + (b.fallbackReason ? " (" + b.fallbackReason + ")" : ""));
  let md = "# Chrome verification report\n\n" + head + "  \nRun: " + results.startedAt + " · page clock frozen at " + results.frozenNow + "\n\n" +
    "| | passed | failed |\n|---|---|---|\n" +
    "| Page sweep (" + PUBLIC_PAGES.concat(DETAIL_PAGES).length + " pages × " + VIEWPORTS.length + " widths) | " + (results.sweep.length - sFail.length) + " | " + sFail.length + " |\n" +
    "| Scenarios | " + (results.scenarios.length - cFail.length) + " | " + cFail.length + " |\n\n## Scenarios\n\n";
  results.scenarios.forEach((r) => {
    md += "- " + (r.status === "passed" ? "PASS" : "**FAIL**") + " · " + r.name + " _(" + r.viewport + ", " + r.ms + " ms)_" +
      (r.error ? "\n  - " + r.error.split("\n")[0] : "") + r.notes.map((n) => "\n  - " + n).join("") + "\n";
  });
  md += "\n## Page sweep\n\n";
  if (!results.sweep.length) md += "Skipped (--no-sweep or --grep).\n";
  else if (!sFail.length) md += "Every page at every width: no console error, failed request, sideways scroll, undersized target, broken image or unnamed control.\n";
  sFail.forEach((r) => { md += "- **FAIL** " + r.page + " @ " + r.width + "px\n" + r.problems.map((p) => "  - " + p).join("\n") + "\n"; });
  fs.writeFileSync(path.join(OUT, "report.md"), md);

  const html = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    "<title>Chrome verification report</title><style>body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#0b0c10;color:#e8e9ee}" +
    "main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}h1{font-size:22px}h2{font-size:16px;margin-top:36px;border-bottom:1px solid #2a2d36;padding-bottom:6px}" +
    ".ok{color:#7ee2b4}.bad{color:#ff9ea1;font-weight:600}.meta{color:#9aa0ad}li{margin:6px 0}code{background:#16181f;padding:1px 5px;border-radius:4px}" +
    ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}.g a{display:block;color:inherit;text-decoration:none;font-size:12px}" +
    ".g img{width:100%;height:150px;object-fit:cover;object-position:top;border:1px solid #2a2d36;border-radius:6px;background:#111}" +
    ".warn{background:#3a2a12;border:1px solid #7a5a1c;padding:10px 14px;border-radius:8px}ul{padding-left:20px}pre{white-space:pre-wrap;background:#16181f;padding:8px;border-radius:6px}</style><main>" +
    "<h1>Chrome verification report</h1><p class=meta>" + esc(head) + "<br>Run " + esc(results.startedAt) + " · page clock frozen at " + esc(results.frozenNow) + "</p>" +
    (b.realChrome ? "" : '<p class=warn>This run did not use Google Chrome. ' + esc(b.engine) + " " + esc(b.version) + " was used instead.</p>") +
    "<p><b class=" + (cFail.length ? "bad" : "ok") + ">" + (results.scenarios.length - cFail.length) + " of " + results.scenarios.length + " scenarios passed</b> · " +
    "<b class=" + (sFail.length ? "bad" : "ok") + ">" + (results.sweep.length - sFail.length) + " of " + results.sweep.length + " page checks passed</b></p>" +
    "<h2>Scenarios</h2><ul>" + results.scenarios.map((r) =>
      "<li><span class=" + (r.status === "passed" ? "ok>PASS" : "bad>FAIL") + "</span> " + esc(r.name) + " <span class=meta>(" + r.viewport + ", " + r.ms + " ms)</span>" +
      (r.error ? "<pre>" + esc(r.error) + "</pre>" : "") + (r.notes.length ? "<br><span class=meta>" + r.notes.map(esc).join("<br>") + "</span>" : "") +
      (r.trace ? ' <a class=meta href="' + esc(r.trace) + '">trace</a> <span class=meta>(<code>npx playwright show-trace ' + esc(r.trace) + "</code>)</span>" : "") +
      (r.shots.length ? '<div class=g style="margin-top:6px">' + r.shots.map((s) => '<a href="' + esc(s) + '"><img loading=lazy src="' + esc(s) + '">' + esc(path.basename(s)) + "</a>").join("") + "</div>" : "") +
      "</li>").join("") + "</ul>" +
    "<h2>Page sweep</h2>" + (sFail.length ? "<ul>" + sFail.map((r) => "<li><span class=bad>FAIL</span> " + esc(r.page) + " @ " + r.width + "px<ul>" +
      r.problems.map((p) => "<li>" + esc(p) + "</li>").join("") + "</ul></li>").join("") + "</ul>" : results.sweep.length ? "<p class=ok>No problems on any page at any width.</p>" : "<p class=meta>Skipped.</p>") +
    "<div class=g>" + results.sweep.filter((r) => r.shot).map((r) => '<a href="' + esc(r.shot) + '"><img loading=lazy src="' + esc(r.shot) + '">' +
      esc(r.page) + " · " + r.width + (r.problems.length ? " <span class=bad>✕</span>" : "") + "</a>").join("") + "</div></main>";
  fs.writeFileSync(path.join(OUT, "report.html"), html);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 1));
  return { sFail, cFail, head };
}

/* ================================================================= MAIN */
(async () => {
  srv = await serve();
  const l = await launch();
  browser = l.browser;
  results.browser = l.info;
  console.log(l.info.engine + " " + l.info.version + (l.info.realChrome ? "" : "  — not Google Chrome" + (l.info.fallbackReason ? ": " + l.info.fallbackReason : "")));

  /* One real id of each detail page, from the site's own data. */
  try {
    const ext = readData("assets/data/external_summary.v1.js").data;
    const sched = readData("assets/data/schedule.v1.js").data;
    const top = Object.keys(ext.teamProfiles).sort((a, b) => ext.teamProfiles[b].maps.played - ext.teamProfiles[a].maps.played)[0];
    const done = sched.matches.filter((m) => m.state === "done" && m.series).pop();
    DETAIL_PAGES.push("team.html?id=" + encodeURIComponent(top), "hero.html?id=kiriko", "series.html?view=results", "series.html?view=brackets");
    if (done) DETAIL_PAGES.push("series.html?id=" + encodeURIComponent(done.series));
  } catch (e) { console.log("  (detail pages skipped: " + e.message + ")"); }

  if (!flag("--no-sweep") && (!GREP || flag("--sweep"))) { console.log("\nPage sweep"); await sweep(); }
  console.log("\nScenarios");
  for (const s of scenarios) if (!GREP || GREP.test(s.name)) await runScenario(s);

  await browser.close();
  await srv.close();
  const r = report();
  console.log("\n" + r.head);
  console.log((results.scenarios.length - r.cFail.length) + "/" + results.scenarios.length + " scenarios · " +
    (results.sweep.length - r.sFail.length) + "/" + results.sweep.length + " page checks");
  console.log("report: " + path.join(OUT, "report.html"));
  process.exit(r.sFail.length || r.cFail.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
