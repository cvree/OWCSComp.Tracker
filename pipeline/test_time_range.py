#!/usr/bin/env python3
"""
test_time_range.py — the site-wide time range (assets/js/app/timerange.js).

The time window is the most-used control on the site, so its rules are
held here:

  * the default is the last 14 days, counted back from the newest map —
    and, with a region chosen, from that region's own newest map;
  * presets resolve to the dates they say (24 / 48 / 72 hours, 7 / 14 / 30 /
    60 / 90 days, custom from / to), and a relative window carries the
    window of equal length before it, for the rising / falling arrows;
  * there is no whole-season or per-stage range any more: the control does
    not offer one, and an old ?t=season / ?t=stage:… link opens the default;
  * a page can put a region menu in the bar;
  * the URL wins over the remembered choice, the remembered choice over
    the default, and anything malformed falls back rather than breaking;
  * older links (tiers' ?range= / ?asof= / ?from=, home's ?w=, comps'
    ?cw=) still open the window they meant;
  * every page that counts comps or heroes loads the module before its own
    script and gives the bar a place directly in <main>, so it can stay
    pinned under the header.

The module runs in Node over the committed export with a minimal stand-in
for the page; skips that half (saying so) when `node` is not installed.
Run: python3 pipeline/test_time_range.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NODE = shutil.which("node")
MODULE = os.path.join(ROOT, "assets", "js", "app", "timerange.js")
EXPORT = os.path.join(ROOT, "assets", "data", "sheet_tiers.v1.js")
PATCHES = os.path.join(ROOT, "assets", "data", "patches.v1.js")

DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [modPath, exportPath, patchesPath] = process.argv.slice(1);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const cases = JSON.parse(buf);
  const out = {};
  const run = (name, query, stored, fn) => {
    const store = {};
    if (stored) store["owcs.range.v1"] = JSON.stringify(stored);
    const url = new URL("http://x/page.html" + (query || ""));
    const ctx = { console, URL, Date, JSON, Math, Number, String, Object, Array, Map, Set, RegExp };
    ctx.window = ctx;
    ctx.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
    ctx.document = { querySelectorAll: () => [], addEventListener: () => {}, getElementById: () => null };
    ctx.OWCS = { esc: (s) => String(s), fmtDate: (d) => d,
      qs: (k) => url.searchParams.get(k),
      setQs: (kv) => { Object.keys(kv).forEach((k) => { if (kv[k] == null || kv[k] === "") url.searchParams.delete(k); else url.searchParams.set(k, kv[k]); }); } };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(exportPath, "utf8"), ctx);
    if (!/^noPatch/.test(name)) vm.runInContext(fs.readFileSync(patchesPath, "utf8"), ctx);
    vm.runInContext(fs.readFileSync(modPath, "utf8"), ctx);
    out[name] = fn(ctx.OWCS.timeRange, ctx, url, store);
  };
  const pick = (r) => ({ key: r.key, from: r.from, to: r.to, days: r.days, prev: r.prev });
  run("default", "", null, (TR, ctx) => ({ r: pick(TR.resolve()), max: ctx.OWCS_SHEET_TIERS.dates.max, min: ctx.OWCS_SHEET_TIERS.dates.min,
    def: TR.DEFAULT }));
  run("region", "", null, (TR, ctx) => {
    const T = ctx.OWCS_SHEET_TIERS;
    const latest = (r) => T.matches.filter((m) => m.d && m.r === r).reduce((d, m) => (!d || m.d > d ? m.d : d), null);
    const regions = [...new Set(T.matches.map((m) => m.r).filter(Boolean))];
    return regions.map((r) => ({ r, to: TR.resolve({ region: r }).to, latest: latest(r) }));
  });
  run("presets", "", null, (TR) => ["7", "30", "60", "90"].map((t) => { TR.set({ t }); return pick(TR.resolve()); }));
  run("hours", "", null, (TR) => ["1", "2", "3"].map((t) => { TR.set({ t }); const r = TR.resolve();
    return { r: pick(r), label: r.label, phrase: TR.phrase() }; }));
  run("noSeason", "?t=season", null, (TR) => { TR.set({ t: "season" }); return TR.get().t; });
  run("noStage", "?t=stage:stage-3", { t: "season" }, (TR) => TR.get().t);
  run("control", "", null, (TR) => {
    const el = { dataset: {}, classList: { add: () => {} }, setAttribute: () => {}, addEventListener: () => {},
      querySelector: () => null, innerHTML: "" };
    TR.mount(el, { regions: [["", "All regions"], ["na", "North America"], ["emea", "EMEA"]], region: "emea" });
    return el.innerHTML;
  });
  run("custom", "?t=custom&tf=2026-06-01&tt=2026-06-14", null, (TR) => pick(TR.resolve()));
  run("urlBeatsStore", "?t=7", { t: "90" }, (TR) => TR.get().t);
  run("storeBeatsDefault", "", { t: "90" }, (TR) => TR.get().t);
  run("badUrl", "?t=banana", { t: "nope" }, (TR) => TR.get().t);
  run("badCustom", "?t=custom&tf=2026-06-14&tt=2026-06-01", null, (TR) => TR.get().t);
  run("legacyRange", "?range=60", null, (TR) => TR.get().t);
  run("legacySeason", "?range=season", null, (TR) => TR.get().t);
  run("legacyStage", "?cs=stage-3", null, (TR) => TR.get().t);
  run("legacyAsof", "?range=30&asof=2026-06-30", null, (TR) => pick(TR.resolve()));
  run("legacyHome", "?w=season", null, (TR) => TR.get().t);
  run("legacyComps", "?cw=90", null, (TR) => TR.get().t);
  run("setWrites", "?range=60&asof=2026-06-30", null, (TR, ctx, url, store) => {
    TR.set({ t: "30" });
    return { search: url.search, stored: store["owcs.range.v1"] };
  });
  run("setDefaultClearsUrl", "?t=30", null, (TR, ctx, url) => { TR.set({ t: "14" }); return url.search; });
  run("buildOpts", "", null, (TR) => TR.buildOpts());
  run("widen", "", null, (TR) => (TR.widenHtml().match(/data-t-widen="([^"]+)"/g) || []).map((m) => m.split('"')[1]));
  run("patch", "?t=patch", null, (TR, ctx) => {
    const r = TR.resolve();
    const el = { dataset: {}, classList: { add: () => {} }, setAttribute: () => {}, addEventListener: () => {},
      querySelector: () => null, innerHTML: "" };
    TR.mount(el, {});
    return { r: pick(r), patch: r.patch && r.patch.id, phrase: TR.phrase(), vs: TR.prevPhrase(),
      patches: ctx.OWCS_PATCHES.patches, max: ctx.OWCS_SHEET_TIERS.dates.max, html: el.innerHTML };
  });
  run("patchRemembered", "", { t: "patch" }, (TR) => TR.get().t);
  run("noPatchFile", "?t=patch", null, (TR) => {
    const el = { dataset: {}, classList: { add: () => {} }, setAttribute: () => {}, addEventListener: () => {},
      querySelector: () => null, innerHTML: "" };
    TR.mount(el, {});
    return { t: TR.get().t, vs: TR.prevPhrase(), offered: /value="patch"|data-t="patch"/.test(el.innerHTML) };
  });
  run("listeners", "", null, (TR) => { let seen = null; TR.onChange((r) => { seen = r.key; }); TR.set({ t: "90" }); return seen; });
  console.log(JSON.stringify(out));
});
"""


def add_days(iso: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


@unittest.skipUnless(NODE, "node is not installed")
class TimeRange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, "-e", DRIVER, MODULE, EXPORT, PATCHES], input="{}", capture_output=True,
                             text=True, encoding="utf-8", timeout=120)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.o = json.loads(out.stdout)

    def test_default_is_the_last_14_days(self):
        d = self.o["default"]
        self.assertEqual(d["def"], "14")
        self.assertEqual(d["r"]["key"], "14")
        self.assertEqual(d["r"]["to"], d["max"])
        self.assertEqual(d["r"]["from"], add_days(d["max"], -13))
        self.assertEqual(d["r"]["prev"], {"from": add_days(d["max"], -27), "to": add_days(d["max"], -14), "days": 14})

    def test_a_region_counts_back_from_its_own_newest_map(self):
        for row in self.o["region"]:
            self.assertEqual(row["to"], row["latest"], row["r"])

    def test_presets_resolve_to_what_they_say(self):
        max_ = self.o["default"]["max"]
        for r, n in zip(self.o["presets"][:4], (7, 30, 60, 90)):
            self.assertEqual((r["key"], r["to"], r["from"], r["days"]), (str(n), max_, add_days(max_, -(n - 1)), n))

    def test_24_48_72_hours_are_the_newest_one_two_three_match_days(self):
        max_ = self.o["default"]["max"]
        for row, n, h in zip(self.o["hours"], (1, 2, 3), (24, 48, 72)):
            r = row["r"]
            self.assertEqual((r["key"], r["to"], r["from"], r["days"]), (str(n), max_, add_days(max_, -(n - 1)), n))
            self.assertEqual(r["prev"], {"from": add_days(max_, -(2 * n - 1)), "to": add_days(max_, -n), "days": n})
            self.assertEqual(row["label"], "Last %d hours" % h)
            self.assertEqual(row["phrase"], "the last %d hours" % h)

    def test_no_season_or_stage_range(self):
        self.assertEqual(self.o["noSeason"], "14")
        self.assertEqual(self.o["noStage"], "14")
        html = self.o["control"]
        self.assertNotIn("season", html.lower())
        self.assertNotIn("stage", html.lower())
        self.assertNotIn("optgroup", html)

    def test_the_custom_menu_offers_24_48_72_hours(self):
        html = self.o["control"]
        more = re.search(r'class="trange__more".*?</select>', html, re.S).group(0)
        for label in ("Custom…", "Last 24 hours", "Last 48 hours", "Last 72 hours", "Last 60 days", "Pick dates…"):
            self.assertIn(label, more)
        self.assertLess(more.find("Last 24 hours"), more.find("Last 72 hours"))

    def test_a_page_can_put_a_region_menu_in_the_bar(self):
        html = self.o["control"]
        reg = re.search(r'<select[^>]*data-t-region.*?</select>', html, re.S).group(0)
        self.assertIn('value="na"', reg)
        self.assertRegex(reg, r'value="emea" selected')

    def test_custom_dates(self):
        c = self.o["custom"]
        self.assertEqual((c["key"], c["from"], c["to"], c["days"]), ("custom", "2026-06-01", "2026-06-14", 14))
        self.assertEqual(c["prev"], {"from": "2026-05-18", "to": "2026-05-31", "days": 14})

    def test_url_then_memory_then_default(self):
        self.assertEqual(self.o["urlBeatsStore"], "7")
        self.assertEqual(self.o["storeBeatsDefault"], "90")
        self.assertEqual(self.o["badUrl"], "14")
        self.assertEqual(self.o["badCustom"], "14")

    def test_older_links_still_open_their_window(self):
        self.assertEqual(self.o["legacyRange"], "60")
        # season and stage links no longer have a window of their own
        self.assertEqual(self.o["legacySeason"], "14")
        self.assertEqual(self.o["legacyStage"], "14")
        a = self.o["legacyAsof"]
        self.assertEqual((a["key"], a["from"], a["to"]), ("custom", "2026-06-01", "2026-06-30"))
        self.assertEqual(self.o["legacyHome"], "14")
        self.assertEqual(self.o["legacyComps"], "90")

    def test_a_change_is_written_to_the_url_and_remembered(self):
        w = self.o["setWrites"]
        self.assertEqual(w["search"], "?t=30")
        self.assertEqual(json.loads(w["stored"]), {"t": "30"})
        self.assertEqual(self.o["setDefaultClearsUrl"], "")
        self.assertEqual(self.o["listeners"], "90")

    def test_options_and_widening(self):
        b = self.o["buildOpts"]
        self.assertEqual(b["asOf"], self.o["default"]["max"])
        self.assertEqual(b["from"], add_days(self.o["default"]["max"], -13))
        self.assertEqual(self.o["widen"], ["30", "60"])


    def test_since_patch_day(self):
        o = self.o["patch"]
        newest = max(o["patches"], key=lambda p: p.get("owcsFrom") or p["date"])
        start = newest.get("owcsFrom") or newest["date"]
        self.assertEqual(o["r"]["key"], "patch")
        self.assertEqual(o["patch"], newest["id"])
        self.assertEqual(o["r"]["from"], start)
        # up to the newest map; a patch newer than every map is an empty window, never a negative one
        self.assertEqual(o["r"]["to"], max(o["max"], start))
        # the arrows compare against the patch before (or an equal window when there is none)
        self.assertEqual(o["r"]["prev"]["to"], add_days(start, -1))
        older = sorted((p.get("owcsFrom") or p["date"] for p in o["patches"]), reverse=True)
        if len(older) > 1:
            self.assertEqual(o["r"]["prev"]["from"], older[1])
        self.assertIn(start, o["phrase"])
        self.assertEqual(o["vs"], "the previous patch")
        self.assertIn('data-t="patch"', o["html"])
        self.assertIn('<option value="patch" selected>', o["html"])
        self.assertEqual(self.o["patchRemembered"], "patch")

    def test_no_patch_on_record_means_no_patch_option(self):
        o = self.o["noPatchFile"]
        self.assertEqual(o["t"], "14")
        self.assertEqual(o["vs"], "the previous window")
        self.assertFalse(o["offered"])


class EveryCompAndHeroPageHasTheBar(unittest.TestCase):
    PAGES = {  # page: (its own script, the bar's mount id)
        "index.html": ("page-home.js", "hm-range"),
        "stats.html": ("page-compbook.js", "cb-range"),
        "tiers.html": ("page-tiers.js", "tr-range"),
        "team.html": ("page-profiles.js", "pf-range"),
        "hero.html": ("page-profiles.js", "pf-range"),
        "draft.html": ("page-draft.js", "dl-range"),
        "versus.html": ("page-versus.js", "vs-range"),
    }

    def test_module_loads_before_the_page_and_the_bar_sits_in_main(self):
        for page, (script, mount) in self.PAGES.items():
            with open(os.path.join(ROOT, page), encoding="utf-8") as f:
                html = f.read()
            at_tr = html.find('src="assets/js/app/timerange.js"')
            at_page = html.find('src="assets/js/app/' + script + '"')
            self.assertGreater(at_tr, 0, page)
            self.assertGreater(at_page, at_tr, page)
            main = re.search(r'<main[^>]*>(.*?)</main>', html, re.S).group(1)
            # a direct child of <main>: not inside the page head or a panel,
            # or position: sticky stops at that parent's edge
            self.assertRegex(main, r'\n  <div id="' + mount + r'"></div>', page)

    def test_pages_read_the_shared_range_and_redraw_on_change(self):
        for script in ("page-home.js", "page-compbook.js", "page-tiers.js", "page-profiles.js", "page-draft.js", "page-versus.js"):
            with open(os.path.join(ROOT, "assets", "js", "app", script), encoding="utf-8") as f:
                js = f.read()
            self.assertIn("timeRange", js, script)
            self.assertIn("onChange(", js, script)

    def test_the_bar_is_pinned(self):
        with open(os.path.join(ROOT, "assets", "css", "owcs.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertRegex(css, r"\.trange--sticky \{ position: sticky; top: var\(--header-h\);")


if __name__ == "__main__":
    unittest.main(verbosity=2)
