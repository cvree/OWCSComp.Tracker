#!/usr/bin/env node
/* =====================================================================
   tests/browser/crawl.mjs — open every page at every width and record
   what the browser saw.

     node tests/browser/crawl.mjs [--out DIR] [--pages a.html,b.html]
                                  [--widths desktop,phone] [--no-shots]

   Writes one screenshot per page and width, plus crawl.json: console
   errors, failed requests, horizontal overflow and its offenders, small
   touch targets, unlabelled controls, broken images and load timings.
   It asserts nothing. `run.mjs` is the gate; this is the survey, and the
   tool for a before/after comparison.
   ===================================================================== */
import fs from "node:fs";
import path from "node:path";
import { ROOT, serve, launch, newPage, probe, timings, VIEWPORTS, ensureDir } from "./lib.mjs";

const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i > 0 ? process.argv[i + 1] : d;
};
const OUT = ensureDir(path.resolve(arg("--out", path.join(ROOT, "tests/browser/output/crawl"))));
const SHOTS = process.argv.indexOf("--no-shots") < 0;

export const PUBLIC_PAGES = [
  "index.html", "meta.html", "stats.html", "tiers.html", "draft.html", "teams.html",
  "versus.html", "series.html", "guide.html", "how-it-works.html",
];
export const ADMIN_PAGES = [
  "admin.html", "games.html", "submit.html", "review.html", "label.html",
  "tools.html", "sheet-sync.html", "styleguide.html",
];

const pages = (arg("--pages", "") || PUBLIC_PAGES.concat(ADMIN_PAGES).join(",")).split(",");
const widths = (arg("--widths", "") || VIEWPORTS.map((v) => v.name).join(",")).split(",");

const main = async () => {
  const srv = await serve();
  const { browser, info } = await launch();
  const rows = [];
  for (const vp of VIEWPORTS.filter((v) => widths.indexOf(v.name) >= 0)) {
    for (const p of pages) {
      const { context, page, log } = await newPage(browser, vp);
      const row = { page: p, viewport: vp.name, width: vp.width };
      try {
        await page.goto(srv.url + "/" + p, { waitUntil: "load", timeout: 45000 });
        await page.waitForTimeout(400);
        row.timing = await timings(page);
        row.probe = await probe(page);
        if (SHOTS) {
          const file = path.join(OUT, p.replace(/[?&=.]/g, "_") + "." + vp.name + ".png");
          await page.screenshot({ path: file, fullPage: false });
          row.shot = path.relative(OUT, file);
        }
      } catch (e) {
        row.error = String(e.message || e).split("\n")[0];
      }
      row.console = log.console; row.pageErrors = log.pageErrors;
      row.failed = log.failed; row.bad = log.bad;
      rows.push(row);
      await context.close();
      const pr = row.probe || {};
      console.log([
        vp.name.padEnd(7), p.padEnd(20),
        row.error ? "ERROR " + row.error : "",
        "err=" + (log.console.length + log.pageErrors.length),
        "req!=" + (log.failed.length + log.bad.length),
        pr.overflowX ? "OVERFLOW " + pr.scrollWidth : "",
        "small=" + (pr.smallTargetCount || 0),
        row.timing ? "load=" + row.timing.load + "ms js=" + row.timing.scriptKB + "KB dom=" + row.timing.domNodes : "",
      ].filter(Boolean).join("  "));
    }
  }
  fs.writeFileSync(path.join(OUT, "crawl.json"), JSON.stringify({ browser: info, rows }, null, 1));
  await browser.close();
  await srv.close();
  console.log("\n" + info.engine + " " + info.version +
    (info.fallbackReason ? "  (fallback: " + info.fallbackReason + ")" : ""));
  console.log("wrote " + path.join(OUT, "crawl.json"));
};

if (import.meta.url === "file://" + process.argv[1]) main().catch((e) => { console.error(e); process.exit(1); });
