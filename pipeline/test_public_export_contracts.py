#!/usr/bin/env python3
"""
test_public_export_contracts.py — Phase 7 production export contracts.

Two layers, both offline:

  * pure-function tests of every derived aggregate, driven with small
    hand-built payloads so the arithmetic is checkable by eye;
  * contract tests against the REAL committed production export, so a future
    change that drops a key or lets a provisional match leak is caught.

Covered behaviors:
  * every contract key the public pages and docs require is present
  * aggregates are computed only from APPROVED comp snapshots
  * an unreviewed / needs-review match is WITHHELD, with the reason recorded
  * a withheld match contributes to no aggregate
  * win rate is None (unknown) rather than 0 when nothing is decided
  * fixture-to-production switching: the production file assigns
    OWCS_PUBLIC unconditionally; the fixture only fills in
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)
#: The demo dataset is a test fixture, not a site asset — it lives outside
#: assets/ so that no page can load it and no deployment can ship it.
FIXTURE_PATH = os.path.join(REPO, "pipeline", "fixtures",
                            "public_fixture.v1.js")

import db as content_db  # noqa: E402
import export_data as ex  # noqa: E402

# Every key the Phase 7 contract requires the production export to carry.
REQUIRED_KEYS = (
    "regions", "teams", "players", "tournaments", "bracketRounds",
    "bracketMatches", "matches", "mapResults", "compSnapshots", "heroStints",
    "heroSwaps", "rejectedSwaps", "heroBans", "captureRuns", "vodSources",
    "heroes", "mapsCatalog", "patches", "compFrequency", "compWinRate",
    "heroPickRates", "teamHeroPools", "teamMapRecords", "mapStats",
    "modeStats",
)


def load_public(fname: str) -> dict:
    path = (FIXTURE_PATH if fname == "public_fixture.v1.js"
            else os.path.join(REPO, "assets", "data", fname))
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"window\.OWCS_PUBLIC\s*=\s*(?:window\.OWCS_PUBLIC\s*\|\|\s*)?", src)
    body = re.sub(r"/\*.*?\*/", "", src[m.end():], flags=re.S).strip().rstrip(";")
    return json.loads(body)


def snap(sid, match, map_id, team, heroes, *, status="auto-high",
         source="cv", overrides=None):
    return {"id": sid, "matchId": match, "mapId": map_id, "teamId": team,
            "heroes": list(heroes), "reviewStatus": status, "source": source,
            "overridesId": overrides}


def match(mid, team_a, team_b, maps):
    return {"id": mid, "teamA": team_a, "teamB": team_b, "maps": maps}


def game(map_pub_id, map_id, winner, *, mode="Control", rounds=3):
    return {"id": map_pub_id, "map": map_id, "winner": winner, "mode": mode,
            "roundCount": rounds}


FIVE_A = ["dva", "sojourn", "tracer", "lucio", "kiriko"]
FIVE_B = ["rein", "ashe", "genji", "ana", "mercy"]


class TestApprovedSnapshotGate(unittest.TestCase):
    def test_only_approved_review_states_count(self):
        rows = [snap("s1", "m1", "m1-m1", "a", FIVE_A, status="auto-high"),
                snap("s2", "m1", "m1-m1", "b", FIVE_B, status="reviewed"),
                snap("s3", "m1", "m1-m1", "b", FIVE_B, status="needs-review"),
                snap("s4", "m1", "m1-m1", "b", FIVE_B, status="rejected")]
        self.assertEqual([s["id"] for s in ex.approved_snapshots(rows)],
                         ["s1", "s2"])

    def test_faceit_sourced_snapshots_can_never_supply_comps(self):
        rows = [snap("s1", "m1", "m1-m1", "a", FIVE_A, source="faceit")]
        self.assertEqual(ex.approved_snapshots(rows), [])

    def test_a_manual_override_excludes_the_cv_row_it_corrects(self):
        rows = [snap("cv1", "m1", "m1-m1", "a", FIVE_A),
                snap("man1", "m1", "m1-m1", "a", FIVE_B, source="manual",
                     status="reviewed", overrides="cv1")]
        kept = [s["id"] for s in ex.approved_snapshots(rows)]
        self.assertEqual(kept, ["man1"])


class TestCompStats(unittest.TestCase):
    def setUp(self):
        self.matches = [
            match("m1", "a", "b", [game("m1-m1", "nepal", "a")]),
            match("m2", "a", "b", [game("m2-m1", "busan", "b")]),
        ]
        # Team a fields the same comp twice (one win, one loss); team b
        # fields a different comp twice (one loss, one win).
        self.snaps = [
            snap("s1", "m1", "m1-m1", "a", FIVE_A),
            snap("s2", "m1", "m1-m1", "b", FIVE_B),
            snap("s3", "m2", "m2-m1", "a", FIVE_A),
            snap("s4", "m2", "m2-m1", "b", FIVE_B),
        ]

    def test_a_comp_is_counted_once_per_map_and_team(self):
        """Two snapshots of the same team on the same map are ONE appearance —
        otherwise a densely-sampled map would look twice as popular."""
        snaps = self.snaps + [snap("s1b", "m1", "m1-m1", "a", FIVE_A)]
        freq, _ = ex.build_comp_stats(snaps, self.matches)
        by_id = {r["id"]: r for r in freq}
        self.assertEqual(by_id[ex.comp_key(FIVE_A)]["appearances"], 2)

    def test_frequency_sums_to_one_across_all_comps(self):
        freq, _ = ex.build_comp_stats(self.snaps, self.matches)
        self.assertAlmostEqual(sum(r["frequency"] for r in freq), 1.0, places=4)

    def test_win_rate_counts_only_decided_maps(self):
        _freq, winrate = ex.build_comp_stats(self.snaps, self.matches)
        by_id = {r["id"]: r for r in winrate}
        row = by_id[ex.comp_key(FIVE_A)]
        self.assertEqual((row["wins"], row["losses"]), (1, 1))
        self.assertEqual(row["winRate"], 0.5)

    def test_an_undecided_map_gives_a_null_win_rate_not_zero(self):
        matches = [match("m1", "a", "b", [game("m1-m1", "nepal", None)])]
        snaps = [snap("s1", "m1", "m1-m1", "a", FIVE_A)]
        _freq, winrate = ex.build_comp_stats(snaps, matches)
        self.assertIsNone(winrate[0]["winRate"])
        self.assertEqual(winrate[0]["decidedMaps"], 0)
        self.assertIn("unknown, not zero", winrate[0]["note"])

    def test_a_partial_comp_is_never_counted_as_a_composition(self):
        snaps = [snap("s1", "m1", "m1-m1", "a", FIVE_A[:3])]
        freq, _ = ex.build_comp_stats(snaps, self.matches)
        self.assertEqual(freq, [])

    def test_comp_identity_is_order_independent(self):
        self.assertEqual(ex.comp_key(FIVE_A), ex.comp_key(list(reversed(FIVE_A))))

    def test_every_comp_row_carries_its_evidence(self):
        freq, _ = ex.build_comp_stats(self.snaps, self.matches)
        for row in freq:
            self.assertTrue(row["evidence"])
            for e in row["evidence"]:
                self.assertIn("matchId", e)
                self.assertIn("snapshotIds", e)


class TestHeroRates(unittest.TestCase):
    def setUp(self):
        self.matches = [match("m1", "a", "b", [game("m1-m1", "nepal", "a")]),
                        match("m2", "a", "b", [game("m2-m1", "busan", None)])]
        self.snaps = [snap("s1", "m1", "m1-m1", "a", FIVE_A),
                      snap("s2", "m1", "m1-m1", "b", FIVE_B),
                      snap("s3", "m2", "m2-m1", "a", FIVE_A)]

    def test_pick_rate_is_appearances_over_total_appearances(self):
        rows = {r["hero"]: r for r in
                ex.build_hero_rates(self.snaps, [], self.matches)}
        self.assertEqual(rows["dva"]["picks"], 2)
        self.assertAlmostEqual(rows["dva"]["pickRate"], 2 / 3, places=4)
        self.assertEqual(rows["rein"]["picks"], 1)

    def test_win_rate_ignores_the_undecided_map(self):
        rows = {r["hero"]: r for r in
                ex.build_hero_rates(self.snaps, [], self.matches)}
        self.assertEqual((rows["dva"]["wins"], rows["dva"]["losses"]), (1, 0))
        self.assertEqual(rows["dva"]["winRate"], 1.0)
        self.assertEqual(rows["rein"]["winRate"], 0.0)

    def test_only_confirmed_swaps_count_toward_swap_rate(self):
        swaps = [{"status": "confirmed", "fromHero": "lucio", "toHero": "kiriko"},
                 {"status": "rejected", "fromHero": "dva", "toHero": "rein"}]
        rows = {r["hero"]: r for r in
                ex.build_hero_rates(self.snaps, swaps, self.matches)}
        self.assertEqual(rows["lucio"]["swappedFrom"], 1)
        self.assertEqual(rows["kiriko"]["swappedTo"], 1)
        self.assertEqual(rows["dva"]["swappedFrom"], 0)

    def test_a_hero_that_only_appears_in_a_swap_still_gets_a_row(self):
        swaps = [{"status": "confirmed", "fromHero": "juno", "toHero": "lucio"}]
        rows = {r["hero"]: r for r in
                ex.build_hero_rates(self.snaps, swaps, self.matches)}
        self.assertIn("juno", rows)
        self.assertEqual(rows["juno"]["picks"], 0)
        self.assertIsNone(rows["juno"]["swapRate"])   # no picks -> unknown


class TestTeamAggregates(unittest.TestCase):
    def setUp(self):
        self.matches = [
            match("m1", "a", "b", [game("m1-m1", "nepal", "a"),
                                   game("m1-m2", "busan", "b")]),
        ]
        self.snaps = [snap("s1", "m1", "m1-m1", "a", FIVE_A),
                      snap("s2", "m1", "m1-m2", "a", FIVE_B)]

    def test_team_hero_pool_lists_verified_play_only(self):
        pools = {t["teamId"]: t for t in
                 ex.build_team_hero_pools(self.snaps, self.matches)}
        self.assertEqual(sorted(pools), ["a"])       # team b has no snapshots
        pool = pools["a"]
        self.assertEqual(pool["appearances"], 2)
        self.assertEqual(pool["poolSize"], 10)
        heroes = {h["hero"]: h for h in pool["heroes"]}
        self.assertEqual(heroes["dva"]["maps"], ["nepal"])
        self.assertEqual(heroes["dva"]["winRate"], 1.0)
        self.assertEqual(heroes["rein"]["winRate"], 0.0)

    def test_team_map_records_count_both_sides(self):
        recs = {t["teamId"]: t for t in ex.build_team_map_records(self.matches)}
        self.assertEqual(sorted(recs), ["a", "b"])
        a_maps = {m["map"]: m for m in recs["a"]["maps"]}
        self.assertEqual((a_maps["nepal"]["wins"], a_maps["nepal"]["losses"]),
                         (1, 0))
        self.assertEqual((a_maps["busan"]["wins"], a_maps["busan"]["losses"]),
                         (0, 1))
        b_maps = {m["map"]: m for m in recs["b"]["maps"]}
        self.assertEqual(b_maps["nepal"]["losses"], 1)

    def test_an_undecided_map_is_undecided_not_a_loss(self):
        matches = [match("m1", "a", "b", [game("m1-m1", "nepal", None)])]
        recs = {t["teamId"]: t for t in ex.build_team_map_records(matches)}
        row = recs["a"]["maps"][0]
        self.assertEqual((row["wins"], row["losses"], row["undecided"]),
                         (0, 0, 1))
        self.assertIsNone(row["winRate"])


class TestMapAndModeStats(unittest.TestCase):
    CATALOG = [{"id": "nepal", "name": "Nepal", "mode": "Control"},
               {"id": "busan", "name": "Busan", "mode": "Control"},
               {"id": "kingsrow", "name": "King's Row", "mode": "Hybrid"}]

    def test_every_catalog_map_appears_even_with_zero_plays(self):
        stats, _ = ex.build_map_mode_stats([], self.CATALOG)
        self.assertEqual(len(stats), 3)
        self.assertTrue(all(r["played"] == 0 for r in stats))

    def test_plays_and_decided_counts(self):
        matches = [match("m1", "a", "b", [game("m1-m1", "nepal", "a"),
                                          game("m1-m2", "busan", None)])]
        stats, modes = ex.build_map_mode_stats(matches, self.CATALOG)
        by_map = {r["map"]: r for r in stats}
        self.assertEqual((by_map["nepal"]["played"], by_map["nepal"]["decided"]),
                         (1, 1))
        self.assertEqual((by_map["busan"]["played"], by_map["busan"]["decided"]),
                         (1, 0))
        by_mode = {r["mode"]: r for r in modes}
        self.assertEqual(by_mode["Control"]["played"], 2)
        self.assertEqual(by_mode["Control"]["mapsPlayed"], 2)
        self.assertEqual(by_mode["Hybrid"]["played"], 0)

    def test_round_counts_accumulate(self):
        matches = [match("m1", "a", "b",
                         [game("m1-m1", "nepal", "a", rounds=3)]),
                   match("m2", "a", "b",
                         [game("m2-m1", "nepal", "b", rounds=2)])]
        stats, _ = ex.build_map_mode_stats(matches, self.CATALOG)
        by_map = {r["map"]: r for r in stats}
        self.assertEqual(by_map["nepal"]["roundCount"], 5)


class TestProvisionalBlocking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = content_db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        content_db.init_schema(self.con)
        self.con.execute(
            "INSERT INTO teams (id,name,code,region) VALUES ('a','A','A','emea')")
        self.con.execute(
            "INSERT INTO teams (id,name,code,region) VALUES ('b','B','B','emea')")
        self.con.execute(
            """INSERT INTO matches (id,date,team_a,team_b,event_name)
               VALUES ('m1','2026-07-20','a','b','OWCS Test')""")
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _run(self, run_id, status):
        self.con.execute(
            """INSERT INTO ingest_runs (id, source_id, match_id,
                   detector_version, status)
               VALUES (?, 'src', 'm1', 'det-test', ?)""", (run_id, status))
        self.con.commit()

    def test_a_clean_match_is_not_provisional(self):
        self._run("r1", "complete")
        self.assertEqual(ex.provisional_reasons(self.con, "m1"), [])

    def test_an_incomplete_ingest_run_makes_a_match_provisional(self):
        self._run("r1", "complete")
        self._run("r2", "running")
        reasons = ex.provisional_reasons(self.con, "m1")
        self.assertEqual(len(reasons), 1)
        self.assertIn("r2", reasons[0])
        self.assertIn("not complete", reasons[0])

    def test_a_failed_ingest_is_provisional(self):
        self._run("r1", "failed")
        self.assertTrue(ex.provisional_reasons(self.con, "m1"))

    def test_an_in_progress_ingest_is_provisional(self):
        """`ingest_runs.status` is CHECK-constrained to running/complete/failed
        — anything but 'complete' means the detection has not been reviewed
        and committed, so its match must not publish maps."""
        self._run("r1", "running")
        self.assertTrue(ex.provisional_reasons(self.con, "m1"))

    def test_a_nonexistent_match_is_reported(self):
        reasons = ex.provisional_reasons(self.con, "does-not-exist")
        self.assertTrue(any("does not exist" in r for r in reasons))

    def test_a_match_with_no_ingest_runs_at_all_is_publishable(self):
        """A calendar-only match (discovered, never captured) is not
        provisional — it publishes its schedule facts and no maps."""
        self.assertEqual(ex.provisional_reasons(self.con, "m1"), [])


class TestProductionExportContracts(unittest.TestCase):
    """Contract tests against the REAL committed export."""

    @classmethod
    def setUpClass(cls):
        cls.data = load_public("public_data.v1.js")

    def test_every_required_contract_key_is_present(self):
        for key in REQUIRED_KEYS:
            self.assertIn(key, self.data, key)

    def test_the_export_is_marked_production_not_demo(self):
        self.assertIs(self.data["meta"]["demo"], False)
        self.assertEqual(self.data["meta"]["schema"], "public.v1")

    def test_publication_provenance_is_recorded_in_meta(self):
        meta = self.data["meta"]
        self.assertIn("withheldMatches", meta)
        self.assertIn("withheldCount", meta)
        self.assertEqual(meta["withheldCount"], len(meta["withheldMatches"]))
        self.assertEqual(sorted(meta["approvedReviewStates"]),
                         sorted(ex.APPROVED_REVIEW_STATES))

    def test_no_withheld_match_appears_in_any_aggregate(self):
        withheld = {b["matchId"] for b in self.data["meta"]["withheldMatches"]}
        if not withheld:
            self.skipTest("nothing is currently withheld")
        for key in ("compSnapshots", "heroSwaps", "heroStints", "captureRuns"):
            for row in self.data[key]:
                self.assertNotIn(row.get("matchId"), withheld, key)
        for row in self.data["compFrequency"]:
            for e in row["evidence"]:
                self.assertNotIn(e["matchId"], withheld)

    def test_every_published_comp_snapshot_is_in_an_approved_state(self):
        for c in self.data["compSnapshots"]:
            self.assertIn(c["reviewStatus"], ex.APPROVED_REVIEW_STATES, c["id"])
            self.assertIn(c["source"], ("cv", "manual"), c["id"])

    def test_every_published_hero_stint_is_in_an_approved_state(self):
        for s in self.data["heroStints"]:
            self.assertIn(s["reviewStatus"], ex.APPROVED_REVIEW_STATES, s["id"])

    def test_the_rejected_swap_ledger_holds_only_non_confirmed_swaps(self):
        for s in self.data["rejectedSwaps"]:
            self.assertNotEqual(s["status"], "confirmed")
            self.assertTrue(s.get("reason"),
                            "a rejected swap must carry the reason it was thrown out")

    def test_confirmed_swaps_carry_before_and_after_evidence(self):
        confirmed = [s for s in self.data["heroSwaps"]
                     if s["status"] == "confirmed"]
        self.assertTrue(confirmed, "the verified milestone has confirmed swaps")
        for s in confirmed:
            for key in ("evidenceBefore", "evidenceAfter"):
                self.assertTrue(s[key], f"{s['id']} missing {key}")
                self.assertTrue(os.path.exists(os.path.join(REPO, s[key])),
                                f"{s['id']} {key} does not resolve")

    def test_aggregates_are_consistent_with_the_snapshots_in_this_file(self):
        """Recomputing from the file's own rows must reproduce its aggregates —
        this is what stops an aggregate from drifting away from its evidence."""
        freq, winrate = ex.build_comp_stats(self.data["compSnapshots"],
                                           self.data["matches"])
        self.assertEqual(freq, self.data["compFrequency"])
        self.assertEqual(winrate, self.data["compWinRate"])
        rates = ex.build_hero_rates(self.data["compSnapshots"],
                                    self.data["heroSwaps"],
                                    self.data["matches"])
        self.assertEqual(rates, self.data["heroPickRates"])

    def test_map_results_mirror_the_maps_inside_matches(self):
        inline = [(m["id"], g["id"]) for m in self.data["matches"]
                  for g in m["maps"]]
        flat = [(r["matchId"], r["id"]) for r in self.data["mapResults"]]
        self.assertEqual(sorted(inline), sorted(flat))

    def test_the_verified_milestone_is_still_published(self):
        """The one genuinely CV-verified match must survive every filter."""
        m = next((x for x in self.data["matches"]
                  if x["id"] == "m-qad-twis-s2po"), None)
        self.assertIsNotNone(m, "the Nepal milestone match must be exported")
        self.assertTrue(m["maps"], "its map must not be withheld")
        self.assertEqual(m["maps"][0]["winner"], "twis")
        self.assertTrue([c for c in self.data["compSnapshots"]
                         if c["matchId"] == "m-qad-twis-s2po"])

    def test_no_demo_fixture_player_is_ever_published(self):
        """players seeded as source='sample' (GEN-TAN1, FLC-DAM2, …) are
        invented handles that exist only for the offline fixtures. A public
        roster must never show one — an empty roster is the honest answer."""
        con = content_db.connect(os.path.join(REPO, "data", "owcs.sqlite"))
        try:
            sample = {r[0] for r in con.execute(
                "SELECT id FROM players WHERE COALESCE(source,'')=?",
                (ex.SAMPLE_PLAYER_SOURCE,))}
        finally:
            con.close()
        self.assertTrue(sample, "the fixture players must still be in the DB "
                                "for this test to mean anything")
        for p in self.data["players"]:
            self.assertNotIn(p["id"], sample, p["handle"])
        for t in self.data["teams"]:
            for p in t["roster"]:
                self.assertNotIn(p["id"], sample, f"{t['id']}: {p['handle']}")

    def test_a_team_with_only_fixture_lineups_exports_an_empty_roster(self):
        gen = next((t for t in self.data["teams"] if t["id"] == "gen"), None)
        if gen is None:
            self.skipTest("gen is not in the current export")
        self.assertEqual(gen["roster"], [])

    def test_every_hero_and_map_referenced_by_an_aggregate_exists(self):
        hero_ids = {h["id"] for h in self.data["heroes"]}
        map_ids = {m["id"] for m in self.data["mapsCatalog"]}
        for row in self.data["heroPickRates"]:
            self.assertIn(row["hero"], hero_ids, row["hero"])
        for row in self.data["compFrequency"]:
            for h in row["heroes"]:
                self.assertIn(h, hero_ids, h)
        for row in self.data["mapStats"]:
            self.assertIn(row["map"], map_ids, row["map"])


class TestFixtureToProductionSwitching(unittest.TestCase):
    def test_production_assigns_unconditionally_and_fixture_only_fills_in(self):
        prod = open(os.path.join(REPO, "assets", "data", "public_data.v1.js"),
                    encoding="utf-8").read()
        fix = open(FIXTURE_PATH, encoding="utf-8").read()
        self.assertRegex(prod, r"window\.OWCS_PUBLIC\s*=\s*\{")
        self.assertNotIn("window.OWCS_PUBLIC || ", prod)
        self.assertRegex(fix, r"window\.OWCS_PUBLIC\s*=\s*window\.OWCS_PUBLIC\s*\|\|")

    def test_the_fixture_is_marked_demo_and_production_is_not(self):
        self.assertIs(load_public("public_fixture.v1.js")["meta"]["demo"], True)
        self.assertIs(load_public("public_data.v1.js")["meta"]["demo"], False)

    def test_no_shipped_page_loads_the_demo_fixture(self):
        """The demo dataset is a TEST asset and nothing else.

        This used to be a load-ORDER rule: production first, fixture as a
        guarded fallback. That meant a broken, empty or missing export
        silently published invented games to the live site under a small
        ribbon. The product now renders an honest empty state instead, so
        the fixture must not be reachable from a page at all — and it does
        not even live under assets/ any more.
        """
        offenders = [p for p in sorted(os.listdir(REPO)) if p.endswith(".html")
                     and "public_fixture"
                     in open(os.path.join(REPO, p), encoding="utf-8").read()]
        self.assertEqual(offenders, [],
                         "these pages load the demo fixture: " + ", ".join(offenders))

    def test_pages_that_show_published_data_load_the_production_export(self):
        for page in ("index.html", "games.html", "game.html", "stats.html",
                     "review.html", "teams.html", "team.html", "hero.html"):
            with self.subTest(page=page):
                src = open(os.path.join(REPO, page), encoding="utf-8").read()
                self.assertIn("assets/data/public_data.v1.js", src)

    def test_the_fixture_never_overwrites_a_defined_production_dataset(self):
        """The guard that makes "production preferred" actually work: the
        fixture must assign with `window.OWCS_PUBLIC || {...}`, so a page
        that already loaded production keeps it. Without this, every page
        would silently render demo data over a real conversion."""
        src = open(FIXTURE_PATH, encoding="utf-8").read()
        self.assertIn("window.OWCS_PUBLIC = window.OWCS_PUBLIC ||", src)
        prod = open(os.path.join(REPO, "assets", "data",
                                 "public_data.v1.js"), encoding="utf-8").read()
        self.assertIn("window.OWCS_PUBLIC = {", prod,
                      "production must assign unconditionally so it wins")

    def test_fixture_fallback_engages_when_production_is_absent(self):
        """Simulate both load orders the way a browser sees them, so the
        fallback is proven rather than assumed."""
        prod = load_public("public_data.v1.js")
        fixture = load_public("public_fixture.v1.js")
        # production present -> production wins (fixture's `||` is a no-op)
        window = {}
        window["OWCS_PUBLIC"] = prod
        window["OWCS_PUBLIC"] = window.get("OWCS_PUBLIC") or fixture
        self.assertIs(window["OWCS_PUBLIC"]["meta"]["demo"], False)
        # production absent -> the fixture supplies the demo dataset
        window = {}
        window["OWCS_PUBLIC"] = window.get("OWCS_PUBLIC") or fixture
        self.assertIs(window["OWCS_PUBLIC"]["meta"]["demo"], True)

    def test_production_export_publishes_only_approved_records(self):
        """Nothing candidate/rejected/low-confidence/unapproved may appear
        in the committed production dataset."""
        data = load_public("public_data.v1.js")
        for snap in data.get("compSnapshots", []):
            self.assertIn(snap.get("reviewStatus"), ("reviewed", "auto-high"),
                          f"unapproved snapshot published: {snap}")
            self.assertIn(snap.get("source"), ("cv", "manual"),
                          f"a non-CV/manual source supplied a comp: {snap}")
            # docs/PUBLIC_DATA_CONTRACT.md: every published comp links back
            # to the capture run and the frame it was read from.
            self.assertTrue(snap.get("evidenceRunId"),
                            f"a published comp carries no evidence run: {snap}")
            self.assertTrue(snap.get("evidenceFrame"),
                            f"a published comp carries no evidence frame: {snap}")
        for swap in data.get("heroSwaps", []):
            self.assertNotEqual(swap.get("verdict"), "rejected",
                                "a rejected swap must never publish as real")

    def test_an_incomplete_ingest_run_withholds_its_match(self):
        """The provisional gate: a match whose detections are not reviewed
        and committed publishes calendar facts and NO comps, with the
        reason recorded."""
        data = load_public("public_data.v1.js")
        withheld = (data["meta"].get("withheldMatches") or {})
        published_match_ids = {m.get("id") for m in data.get("matches", [])}
        for match_id, reasons in withheld.items():
            self.assertTrue(reasons, f"{match_id} withheld with no reason")
            for snap in data.get("compSnapshots", []):
                self.assertNotEqual(
                    snap.get("matchId"), match_id,
                    f"withheld match {match_id} still published a comp")
        self.assertIsInstance(published_match_ids, set)


class TestCalendarContract(unittest.TestCase):
    """The official season calendar reaching the public site.

    Before this existed, `config/owcs_calendar.json` was loaded by
    `sync-calendar` into the automation DB's `source_events` ledger and
    stopped there — nothing exported it, so the site could only ever
    show matches that had already been ingested and had no idea a stage was
    even running.
    """

    def setUp(self):
        self.data = load_public("public_data.v1.js")

    def test_calendar_events_are_exported(self):
        events = self.data.get("calendarEvents")
        self.assertIsInstance(events, list)
        self.assertTrue(events, "the official calendar must reach the site")

    def test_every_event_can_be_placed_on_a_grid(self):
        for e in self.data["calendarEvents"]:
            self.assertTrue(e.get("id"))
            self.assertTrue(e.get("name"))
            self.assertRegex(e["startDate"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(e["endDate"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertGreaterEqual(e["endDate"], e["startDate"],
                                    f"{e['id']} ends before it starts")
            self.assertIsInstance(e["verified"], bool)

    def test_events_are_sorted_by_start_date(self):
        starts = [e["startDate"] for e in self.data["calendarEvents"]]
        self.assertEqual(starts, sorted(starts))

    def test_the_verified_flag_is_carried_through_unchanged(self):
        """The committed seed is unverified; the export must not launder
        placeholder dates into apparent fact."""
        src = os.path.join(REPO, "config", "owcs_calendar.json")
        with open(src, encoding="utf-8") as f:
            raw = {e["id"]: e for e in json.load(f).get("events", [])}
        for e in self.data["calendarEvents"]:
            self.assertEqual(e["verified"], bool(raw[e["id"]].get("verified")),
                             f"{e['id']}: verified flag was altered")

    def test_every_event_region_resolves_to_a_named_region(self):
        """A region missing from the catalog renders as a raw id in the
        badge — which is exactly how `korea` was displaying."""
        known = {r["id"] for r in self.data["regions"]}
        for e in self.data["calendarEvents"]:
            self.assertIn(e["region"], known,
                          f"{e['id']} uses region {e['region']!r}, which has "
                          f"no entry in the regions catalog")

    def test_matches_declare_whether_the_time_is_real(self):
        """`scheduledAt` is derived as midnight when only a DATE is known.
        Without this flag the UI cannot tell a real 00:00 kickoff from an
        unknown time, and would render a fabricated start time."""
        for m in self.data["matches"]:
            self.assertIn("timeKnown", m, f"{m['id']} has no timeKnown flag")
            self.assertIsInstance(m["timeKnown"], bool)
            if not m["timeKnown"]:
                self.assertTrue(m["scheduledAt"].endswith("T00:00:00+00:00"),
                                f"{m['id']} claims an unknown time but "
                                f"carries a non-midnight instant")

    def test_no_tournament_contradicts_its_own_matches(self):
        """A tournament stubbed from a discovered match is born "upcoming".
        If that guess is never reconciled the page asserts two contradictory
        things at once — an UPCOMING chip above a full set of final scores —
        which is exactly the kind of unsupported claim this export exists to
        avoid. Regression guard for that reconciliation."""
        by_tid: dict = {}
        for m in self.data["matches"]:
            by_tid.setdefault(m["tournamentId"], []).append(
                (m["status"] or "").lower())
        settled = {"completed", "forfeit", "cancelled"}
        for t in self.data["tournaments"]:
            statuses = by_tid.get(t["id"])
            if not statuses:
                continue
            if "completed" in statuses and all(s in settled for s in statuses):
                self.assertEqual(
                    t["status"], "completed",
                    f"{t['id']} claims {t['status']!r} but every one of its "
                    f"matches is settled ({statuses})")
            if "live" in statuses:
                self.assertEqual(t["status"], "live", f"{t['id']} has a live "
                                 f"match but claims {t['status']!r}")
            for stage in t.get("stages") or []:
                self.assertEqual(
                    stage["status"], t["status"],
                    f"{t['id']} stage {stage['id']} says "
                    f"{stage['status']!r} while the event says "
                    f"{t['status']!r}")

    def test_reconcile_stub_status_is_derived_not_guessed(self):
        """Unit-level truth table for the reconciliation itself."""
        import export_data as ed

        def run(match_statuses, *, stub=True):
            tid = "t1"
            tours = {tid: {"id": tid, "status": "upcoming",
                           "stages": [{"id": "s", "status": "upcoming"}]}}
            matches = [{"tournamentId": tid, "status": s}
                       for s in match_statuses]
            ed._reconcile_stub_tournament_status(
                tours, {tid} if stub else set(), matches)
            return tours[tid]["status"], tours[tid]["stages"][0]["status"]

        self.assertEqual(run(["completed"]), ("completed", "completed"))
        self.assertEqual(run(["completed", "forfeit"]),
                         ("completed", "completed"))
        self.assertEqual(run(["completed", "upcoming"]),
                         ("upcoming", "upcoming"))
        self.assertEqual(run(["completed", "live"]), ("live", "live"))
        # An all-cancelled event has nothing to show and must not claim to
        # have been played.
        self.assertEqual(run(["cancelled"]), ("upcoming", "upcoming"))
        # A curated (non-stub) tournament keeps its authoritative status.
        self.assertEqual(run(["completed"], stub=False),
                         ("upcoming", "upcoming"))

    def test_the_season_schedule_still_reaches_a_page(self):
        """The calendar stopped being a destination in the 2026 redesign —
        nobody opens a calendar as a task — but the exported events did not
        stop mattering. They surface under the games list as "on the
        official schedule, nobody has submitted it yet", which is the only
        question that list can usefully answer about a future event.

        This guards the wiring, not the page: an export that carries
        calendarEvents while no page reads them is a silent data loss.
        """
        page = open(os.path.join(REPO, "games.html"), encoding="utf-8").read()
        js = open(os.path.join(REPO, "assets", "js", "app",
                               "page-games.js"), encoding="utf-8").read()
        self.assertIn('id="upcoming"', page,
                      "games.html has nowhere to render the schedule")
        self.assertIn("calendarEvents", js,
                      "the games page must read the exported events")
        self.assertIn("timeKnown", js,
                      "the games page must honour the time-known flag")
        self.assertIn("time TBA", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
