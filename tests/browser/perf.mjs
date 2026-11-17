#!/usr/bin/env node
/* =====================================================================
   tests/browser/perf.mjs — measure page load, do not guess at it.

     node tests/browser/perf.mjs [--root DIR] [--out FILE] [--runs 5]
     node tests/browser/perf.mjs --compare before.json after.json

   Each page is loaded cold `--runs` times in a fresh context with the CPU
   slowed 4x (a mid-range phone) and the median is kept: DOMContentLoaded,
   load, largest contentful paint, total long-task time, script and total
   bytes, request count and DOM size. `--root` points at another checkout
   (a git worktree of the commit to compare against), so before and after
   are measured by the same code on the same machine.
   ===================================================================== */
import fs from "node:fs";
import path from "node:path";
import { ROOT, serve, launch, newPage, timings, VIEWPORTS } from "./lib.mjs";

const argv = process.argv.slice(2);
const opt = (k, d) => (argv.indexOf(k) >= 0 ? argv[argv.indexOf(k) + 1] : d);
const PAGES = ["index.html", "stats.html", "tiers.html", "teams.html", "series.html", "meta.html", "versus.html", "draft.html"];
const KEYS = ["domContentLoaded", "load", "lcp", "longTaskMs", "scriptKB", "decodedKB", "requests", "domNodes"];
const median = (a) => a.slice().sort((x, y) => x - y)[Math.floor(a.length / 2)];

if (argv[0] === "--compare") {
  const A = JSON.parse(fs.readFileSync(argv[1], "utf8")), B = JSON.parse(fs.readFileSync(argv[2], "utf8"));
  const pad = (s, n) => String(s).padStart(n);
  console.log("page".padEnd(14) + KEYS.map((k) => pad(k, 22)).join(""));
  A.pages.forEach((a) => {
    const b = B.pages.find((x) => x.page === a.page);
    if (!b) return;
    console.log(a.page.padEnd(14) + KEYS.map((k) => {
      const d = a[k] ? Math.round((b[k] - a[k]) / a[k] * 100) : 0;
      return pad(a[k] + " → " + b[k] + " (" + (d > 0 ? "+" : "") + d + "%)", 22);
    }).join(""));
  });
  process.exit(0);
}

const root = path.resolve(opt("--root", ROOT));
const runs = Number(opt("--runs", 5));
const srv = await serve(root);
const { browser, info } = await launch();
const out = { browser: info, root, runs, cpuThrottle: 4, pages: [] };
for (const p of PAGES) {
  const samples = [];
  for (let i = 0; i < runs; i++) {
    const { context, page } = await newPage(browser, VIEWPORTS[0], { clock: false });
    const cdp = await context.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await page.goto(srv.url + "/" + p, { waitUntil: "load" });
    await page.waitForTimeout(600);
    samples.push(await timings(page));
    await context.close();
  }
  const row = { page: p };
  KEYS.forEach((k) => { row[k] = median(samples.map((s) => s[k])); });
  out.pages.push(row);
  console.log(p.padEnd(14) + KEYS.map((k) => k + "=" + row[k]).join("  "));
}
await browser.close();
await srv.close();
if (opt("--out", "")) fs.writeFileSync(opt("--out", ""), JSON.stringify(out, null, 1));
