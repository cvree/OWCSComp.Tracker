#!/usr/bin/env node
/* =====================================================================
   tests/browser/live.mjs — a smoke check of the DEPLOYED site.

     OWCS_LIVE_URL=https://<owner>.github.io/<repo>/ node tests/browser/live.mjs

   Separate from run.mjs on purpose. run.mjs tests the repository's own
   files with the clock frozen, so it is repeatable. This opens whatever
   is published right now, with the real clock, and checks only what must
   hold for any day's data: every public page loads without an error, has
   content, and its data is not stale. It needs the network, so it is not
   part of the gate.
   ===================================================================== */
import path from "node:path";
import { ROOT, launch, newPage, probe, VIEWPORTS, ensureDir } from "./lib.mjs";

const base = (process.env.OWCS_LIVE_URL || "").replace(/\/?$/, "/");
if (!process.env.OWCS_LIVE_URL) {
  console.error("Set OWCS_LIVE_URL to the deployed site, e.g. https://<owner>.github.io/<repo>/");
  process.exit(2);
}
const OUT = ensureDir(path.join(ROOT, "tests/browser/output/live"));
const PAGES = ["index.html", "stats.html", "tiers.html", "teams.html", "series.html", "versus.html", "draft.html", "meta.html"];
const { browser, info } = await launch();
console.log(info.engine + " " + info.version + " → " + base);
let failed = 0;
for (const p of PAGES) {
  const { context, page, log } = await newPage(browser, VIEWPORTS[0], { clock: false });
  const problems = [];
  try {
    const r = await page.goto(base + p, { waitUntil: "load", timeout: 60000 });
    if (!r || r.status() >= 400) problems.push("HTTP " + (r ? r.status() : "no response"));
    await page.waitForTimeout(600);
    const pr = await probe(page);
    if (log.pageErrors.length) problems.push("uncaught exception: " + log.pageErrors[0]);
    if (log.bad.length) problems.push("bad response: " + log.bad.slice(0, 3).join(", "));
    if (pr.overflowX) problems.push("page scrolls sideways");
    if (pr.mainChars < 200) problems.push("main is nearly empty");
    const stale = await page.$$eval('.ftr__fresh li[data-stale="true"]', (ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ")));
    if (stale.length) problems.push("stale data: " + stale.join(" | "));
    await page.screenshot({ path: path.join(OUT, p.replace(".html", "") + ".png") });
  } catch (e) {
    problems.push(String(e.message || e).split("\n")[0]);
  }
  if (problems.length) failed += 1;
  console.log((problems.length ? "  FAIL " : "  ok   ") + p + (problems.length ? "\n         " + problems.join("\n         ") : ""));
  await context.close();
}
await browser.close();
process.exit(failed ? 1 : 0);
