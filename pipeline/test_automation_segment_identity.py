#!/usr/bin/env python3
"""
test_automation_segment_identity.py — Phase 4 automatic segment identity.

Offline throughout: a FAKE OCR reader (the injected `read_fn` contract
`ocr_hud.make_reader` produces), generated frames, and temporary databases.

Covered behaviors:
  * map OCR consensus, ambiguity, and UNKNOWN-with-a-reason
  * mode always derived from the catalog, never OCR'd independently
  * team consensus per side + the "same team on both sides" contradiction
  * side continuity and a detected side swap
  * chronological map order
  * nameplate OCR against a roster; candidate players are NEVER created
  * duplicate-slot handling
  * every signal present in the proposal, none silently missing
  * ingest_findings rows carry source, confidence and evidence
  * accept-proposed refuses a blocked or incomplete proposal
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import db as content_db  # noqa: E402
import map_identify as mid  # noqa: E402
import player_identify as pid  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import segment_identity as si  # noqa: E402
from automation import segmentation as seg  # noqa: E402

from test_automation_layout_resolver import (  # noqa: E402
    gameplay_frame, hud_layout, FRAME_W, FRAME_H)

KNOWN_MAPS = [
    {"id": "nepal", "name": "Nepal", "mode": "Control"},
    {"id": "busan", "name": "Busan", "mode": "Control"},
    {"id": "kingsrow", "name": "King's Row", "mode": "Hybrid"},
    {"id": "njc", "name": "New Junk City", "mode": "Flashpoint"},
    {"id": "nqs", "name": "New Queen Street", "mode": "Push"},
]
KNOWN_TEAMS = [
    {"id": "qadsiah", "name": "Al Qadsiah", "code": "QAD"},
    {"id": "twis", "name": "Twisted Minds", "code": "TWIS"},
    {"id": "cr", "name": "Crazy Raccoon", "code": "CR"},
]
ROSTER = [
    {"id": "p-lip", "handle": "LIP", "teamId": "qadsiah"},
    {"id": "p-smurf", "handle": "Smurf", "teamId": "qadsiah"},
    {"id": "p-fielder", "handle": "Fielder", "teamId": "qadsiah"},
    {"id": "p-hanbin", "handle": "Hanbin", "teamId": "qadsiah"},
    {"id": "p-shu", "handle": "Shu", "teamId": "qadsiah"},
    {"id": "p-zoux", "handle": "ZOX", "teamId": "twis"},
    {"id": "p-hydron", "handle": "Hydron", "teamId": "twis"},
    {"id": "p-kilo", "handle": "Kilo", "teamId": "twis"},
    {"id": "p-hawk", "handle": "Hawk", "teamId": "twis"},
    {"id": "p-chiyo", "handle": "Chiyo", "teamId": "twis"},
]


def item(text, box, conf=0.9):
    return {"text": text, "conf": conf, "box": list(box)}


def box_in(zone):
    """A tight box centered inside `zone` — so ocr_hud._center_in accepts it."""
    x, y, w, h = zone
    cx, cy = x + w // 2, y + h // 2
    return [cx - 10, cy - 6, 20, 12]


class TestMatchMap(unittest.TestCase):
    def test_exact_and_case_insensitive(self):
        self.assertEqual(mid.match_map("NEPAL", KNOWN_MAPS)["map"], "nepal")
        self.assertEqual(mid.match_map("nepal", KNOWN_MAPS)["map"], "nepal")
        self.assertEqual(mid.match_map("King's Row", KNOWN_MAPS)["map"],
                         "kingsrow")

    def test_catalog_name_inside_a_longer_strip(self):
        r = mid.match_map("CONTROL - NEPAL", KNOWN_MAPS)
        self.assertEqual(r["map"], "nepal")
        self.assertEqual(r["method"], "contains")

    def test_two_catalog_names_in_one_string_is_ambiguous(self):
        r = mid.match_map("NEPAL VS BUSAN", KNOWN_MAPS)
        self.assertIsNone(r["map"])
        self.assertIn("ambiguous", r["reason"])

    def test_fuzzy_typo_resolves(self):
        r = mid.match_map("NEPAI", KNOWN_MAPS)
        self.assertEqual(r["map"], "nepal")
        self.assertEqual(r["method"], "fuzzy")

    def test_near_tie_returns_unknown_rather_than_the_marginal_winner(self):
        """Two catalog names within FUZZY_MARGIN of each other must produce
        UNKNOWN. A synthetic catalog is used deliberately: the point is the
        margin rule, and no two REAL Overwatch map names are this close."""
        catalog = [{"id": "alpha", "name": "Sanctum Alpha", "mode": "Control"},
                   {"id": "beta", "name": "Sanctum Aleph", "mode": "Control"}]
        r = mid.match_map("SANCTUM ALPHH", catalog)
        self.assertIsNone(r["map"])
        self.assertIn("ambiguous fuzzy", r["reason"])

    def test_real_map_names_are_far_enough_apart_to_resolve(self):
        """The flip side: the margin rule must not make real names unusable.
        'NEW UEEN CITY' (a dropped Q) is genuinely much closer to New Junk
        City than to New Queen Street, so resolving it is correct."""
        r = mid.match_map("NEW UEEN CITY", KNOWN_MAPS)
        self.assertEqual(r["map"], "njc")

    def test_garbage_and_short_text_never_match(self):
        for text in ("", "X", "  ", "12", "@@@"):
            self.assertIsNone(mid.match_map(text, KNOWN_MAPS)["map"], text)

    def test_empty_catalog_is_reported_not_crashed(self):
        r = mid.match_map("NEPAL", [])
        self.assertIsNone(r["map"])
        self.assertIn("no known maps", r["reason"])


class TestIdentifyMap(unittest.TestCase):
    def setUp(self):
        self.layout = hud_layout()
        self.zones = mid.zones_for(self.layout, FRAME_W, FRAME_H)

    def _frames(self, texts_per_frame, zone="objective_strip"):
        return [(float(i * 30),
                 [item(t, box_in(self.zones[zone])) for t in texts])
                for i, texts in enumerate(texts_per_frame)]

    def test_consensus_names_the_map_and_takes_mode_from_the_catalog(self):
        res = mid.identify_map(self._frames([["NEPAL"], ["NEPAL"], ["NEPAL"]]),
                               KNOWN_MAPS, layout=self.layout,
                               fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["map"], "nepal")
        self.assertEqual(res["mode"], "Control")
        self.assertEqual(res["nFrames"], 3)
        self.assertIn("from the map catalog, not OCR", res["reason"])
        self.assertTrue(res["evidence"])

    def test_one_frame_is_never_enough(self):
        res = mid.identify_map(self._frames([["NEPAL"]]), KNOWN_MAPS,
                               layout=self.layout, fw=FRAME_W, fh=FRAME_H)
        self.assertIsNone(res["map"])
        self.assertEqual(res["weakCandidate"], "nepal")
        self.assertIn("consensus", res["reason"])

    def test_two_maps_both_reaching_consensus_is_a_disagreement(self):
        res = mid.identify_map(
            self._frames([["NEPAL"], ["NEPAL"], ["BUSAN"], ["BUSAN"]]),
            KNOWN_MAPS, layout=self.layout, fw=FRAME_W, fh=FRAME_H)
        self.assertIsNone(res["map"])
        self.assertTrue(res["disagreement"])
        self.assertIn("spans more than one map", res["reason"])

    def test_no_map_text_at_all_is_unknown_with_a_reason(self):
        res = mid.identify_map(self._frames([["CASTERS"], ["SPONSOR"]]),
                               KNOWN_MAPS, layout=self.layout,
                               fw=FRAME_W, fh=FRAME_H)
        self.assertIsNone(res["map"])
        self.assertIn("none matched a known map", res["reason"])
        self.assertTrue(res["unresolved"])

    def test_a_printed_mode_word_that_contradicts_the_catalog_is_flagged(self):
        res = mid.identify_map(
            self._frames([["NEPAL", "PUSH"], ["NEPAL", "PUSH"]]),
            KNOWN_MAPS, layout=self.layout, fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["map"], "nepal")
        self.assertEqual(res["mode"], "Control")     # the catalog still wins
        self.assertIn("modeConflict", res)
        self.assertTrue(res["disagreement"])

    def test_matching_mode_word_is_not_a_conflict(self):
        res = mid.identify_map(
            self._frames([["NEPAL", "CONTROL"], ["NEPAL", "CONTROL"]]),
            KNOWN_MAPS, layout=self.layout, fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["map"], "nepal")
        self.assertNotIn("modeConflict", res)
        self.assertFalse(res["disagreement"])

    def test_text_outside_the_zones_is_ignored(self):
        frames = [(0.0, [item("NEPAL", [5, 690, 40, 12])]),
                  (30.0, [item("NEPAL", [5, 690, 40, 12])])]
        res = mid.identify_map(frames, KNOWN_MAPS, layout=self.layout,
                               fw=FRAME_W, fh=FRAME_H)
        self.assertIsNone(res["map"])


class TestMatchPlayer(unittest.TestCase):
    def test_exact_handle(self):
        self.assertEqual(pid.match_player("LIP", ROSTER)["player"], "p-lip")
        self.assertEqual(pid.match_player("hydron", ROSTER)["player"], "p-hydron")

    def test_fuzzy_typo_within_cutoff(self):
        r = pid.match_player("Hanbln", ROSTER)
        self.assertEqual(r["player"], "p-hanbin")
        self.assertEqual(r["method"], "fuzzy")

    def test_unknown_handle_never_invents_a_player(self):
        r = pid.match_player("SOMEBODY-ELSE", ROSTER)
        self.assertIsNone(r["player"])
        self.assertIn("no roster handle matches", r["reason"])

    def test_numeric_and_short_text_rejected(self):
        for text in ("42", "", "K", "  "):
            self.assertIsNone(pid.match_player(text, ROSTER)["player"], text)

    def test_ambiguous_fuzzy_returns_unknown(self):
        roster = [{"id": "p-a", "handle": "Kiloz", "teamId": "t"},
                  {"id": "p-b", "handle": "Kilos", "teamId": "t"}]
        r = pid.match_player("Kilox", roster)
        self.assertIsNone(r["player"])
        self.assertIn("ambiguous", r["reason"])

    def test_duplicate_handles_in_the_roster_are_refused(self):
        roster = [{"id": "p-a", "handle": "Shu", "teamId": "t1"},
                  {"id": "p-b", "handle": "Shu", "teamId": "t2"}]
        r = pid.match_player("Shu", roster)
        self.assertIsNone(r["player"])
        self.assertIn("shared by", r["reason"])

    def test_empty_roster_is_reported(self):
        r = pid.match_player("LIP", [])
        self.assertIsNone(r["player"])
        self.assertIn("no known roster", r["reason"])


class TestIdentifyPlayers(unittest.TestCase):
    def setUp(self):
        self.layout = hud_layout()
        self.zones = pid.slot_nameplate_zones(self.layout, FRAME_W, FRAME_H)

    def _frames(self, per_slot_texts, repeats=3):
        """per_slot_texts: {(side, index): text}"""
        out = []
        for f in range(repeats):
            items = []
            for z in self.zones:
                text = per_slot_texts.get((z["side"], z["index"]))
                if text:
                    items.append(item(text, box_in(z["zone"])))
            out.append((float(f * 30), items))
        return out

    def test_all_ten_nameplates_resolve(self):
        mapping = {}
        handles_a = ["LIP", "Smurf", "Fielder", "Hanbin", "Shu"]
        handles_b = ["ZOX", "Hydron", "Kilo", "Hawk", "Chiyo"]
        for i, h in enumerate(handles_a, 1):
            mapping[("a", i)] = h
        for i, h in enumerate(handles_b, 1):
            mapping[("b", i)] = h
        res = pid.identify_players(self._frames(mapping), self.layout, ROSTER,
                                   fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["resolved"], 10)
        self.assertEqual(res["unknown"], 0)
        self.assertEqual(res["candidatePlayers"], [])
        self.assertFalse(res["disagreement"])

    def test_an_unknown_nameplate_becomes_a_candidate_not_a_player(self):
        res = pid.identify_players(
            self._frames({("a", 1): "LIP", ("a", 2): "NEWGUY99"}),
            self.layout, ROSTER, fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["resolved"], 1)
        cands = res["candidatePlayers"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["raw"], "NEWGUY99")
        self.assertIn("NEVER created automatically", cands[0]["note"])
        self.assertEqual(cands[0]["slots"], ["a2"])

    def test_one_frame_never_resolves_a_slot(self):
        res = pid.identify_players(
            self._frames({("a", 1): "LIP"}, repeats=1),
            self.layout, ROSTER, fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["resolved"], 0)
        slot = next(s for s in res["slots"] if s["side"] == "a" and s["index"] == 1)
        self.assertEqual(slot["weakCandidate"], "p-lip")

    def test_a_player_cannot_hold_two_slots(self):
        res = pid.identify_players(
            self._frames({("a", 1): "LIP", ("a", 2): "LIP"}),
            self.layout, ROSTER, fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["resolved"], 0)
        self.assertEqual(len(res["duplicates"]), 1)
        self.assertEqual(res["duplicates"][0]["player"], "p-lip")
        self.assertTrue(res["disagreement"])
        for s in res["slots"][:2]:
            self.assertIn("cannot hold two slots", s["reason"])

    def test_conflicting_reads_for_one_slot_are_unknown(self):
        frames = [
            (0.0, [item("LIP", box_in(self.zones[0]["zone"]))]),
            (30.0, [item("LIP", box_in(self.zones[0]["zone"]))]),
            (60.0, [item("Smurf", box_in(self.zones[0]["zone"]))]),
            (90.0, [item("Smurf", box_in(self.zones[0]["zone"]))]),
        ]
        res = pid.identify_players(frames, self.layout, ROSTER,
                                   fw=FRAME_W, fh=FRAME_H)
        slot = res["slots"][0]
        self.assertIsNone(slot["player"])
        self.assertIn("conflicting nameplate evidence", slot["reason"])

    def test_slots_with_no_text_are_unknown_with_that_reason(self):
        res = pid.identify_players(self._frames({}), self.layout, ROSTER,
                                   fw=FRAME_W, fh=FRAME_H)
        self.assertEqual(res["unknown"], 10)
        self.assertIn("no nameplate text", res["slots"][0]["reason"])

    def test_nameplate_zone_sits_below_the_portrait(self):
        zone = pid.nameplate_zone([1200, 160, 48, 48], FRAME_W, FRAME_H)
        self.assertGreaterEqual(zone[1], 160 + 48)
        self.assertGreater(zone[2], 48)          # wider than the portrait
        self.assertLessEqual(zone[0] + zone[2], FRAME_W)
        self.assertLessEqual(zone[1] + zone[3], FRAME_H)

    def test_nameplate_zone_is_clamped_at_the_frame_edges(self):
        """A slot at the very bottom/right must not produce an out-of-frame
        zone — probe boxes that overflow are silently skipped downstream, so
        the clamp is what keeps the read possible at all."""
        zone = pid.nameplate_zone([FRAME_W - 50, FRAME_H - 60, 48, 48],
                                  FRAME_W, FRAME_H)
        self.assertGreaterEqual(zone[0], 0)
        self.assertGreaterEqual(zone[1], 0)
        self.assertLessEqual(zone[0] + zone[2], FRAME_W)
        self.assertLessEqual(zone[1] + zone[3], FRAME_H)


class IdentityDbBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.content = content_db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        content_db.init_schema(self.content)
        for m in KNOWN_MAPS:
            self.content.execute(
                "INSERT OR REPLACE INTO game_maps (id,name,mode) VALUES (?,?,?)",
                (m["id"], m["name"], m["mode"]))
        for t in KNOWN_TEAMS:
            self.content.execute(
                "INSERT OR REPLACE INTO teams (id,name,code,region) VALUES (?,?,?,?)",
                (t["id"], t["name"], t["code"], "emea"))
        for p in ROSTER:
            self.content.execute(
                "INSERT OR REPLACE INTO players (id,team_id,nickname) VALUES (?,?,?)",
                (p["id"], p["teamId"], p["handle"]))
        self.content.commit()
        self.layout = hud_layout()

    def tearDown(self):
        self.content.close()
        self.store.close()
        self.tmp.cleanup()

    def make_segment(self, start=100.0, end=700.0, video_id="vidX",
                     match_id="m1"):
        return seg.store_candidates(
            self.store.con, video_id, match_id,
            [{"start_time": start, "end_time": end, "confidence": 0.9,
              "signals": {}}])[0]

    def write_frames(self, count=4, hue_a=15, hue_b=110):
        """Real generated frames so scale_layout_to_frame / side_hue run for
        real; OCR is injected separately."""
        d = os.path.join(self.tmp.name, "frames")
        os.makedirs(d, exist_ok=True)
        out = []
        for i in range(count):
            frame = gameplay_frame(self.layout)
            p = os.path.join(d, f"f{i:04d}.png")
            cv2.imwrite(p, frame)
            out.append((float(100 + i * 100), p))
        return out

    def fake_reader(self, *, map_text="NEPAL", team_a="Al Qadsiah",
                    team_b="Twisted Minds", nameplates=None):
        """Injected OCR: emits map/team/nameplate items in the right zones."""
        import ocr_hud
        map_zones = mid.zones_for(self.layout, FRAME_W, FRAME_H)
        team_zones = {k: ocr_hud.zone_px(v, FRAME_W, FRAME_H)
                      for k, v in ocr_hud.DEFAULT_ZONES.items()}
        plate_zones = {(z["side"], z["index"]): z["zone"]
                       for z in pid.slot_nameplate_zones(self.layout,
                                                         FRAME_W, FRAME_H)}

        def read(frame_bgr):
            h, w = frame_bgr.shape[:2]
            sx, sy = w / FRAME_W, h / FRAME_H

            def scaled(zone):
                x, y, zw, zh = zone
                return [int(x * sx), int(y * sy), max(2, int(zw * sx)),
                        max(2, int(zh * sy))]
            items = []
            if map_text:
                items.append(item(map_text, box_in(scaled(map_zones["objective_strip"]))))
            if team_a:
                items.append(item(team_a, box_in(scaled(team_zones["team_left"]))))
            if team_b:
                items.append(item(team_b, box_in(scaled(team_zones["team_right"]))))
            for key, text in (nameplates or {}).items():
                items.append(item(text, box_in(scaled(plate_zones[key]))))
            return items
        return read


class TestProposeIdentity(IdentityDbBase):
    def test_full_proposal_carries_every_signal_with_provenance(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(), read_fn=self.fake_reader())
        for signal in si.SIGNALS:
            self.assertIn(signal, proposal, signal)
            field = proposal[signal]
            self.assertIn("source", field)
            self.assertIn("confidence", field)
            self.assertIn("reason", field)
            self.assertIn("evidence", field)
        self.assertEqual(proposal["map"]["value"], "nepal")
        self.assertEqual(proposal["mode"]["value"], "Control")
        self.assertEqual(proposal["mode"]["source"], "map-catalog")
        self.assertEqual(proposal["teamA"]["value"], "qadsiah")
        self.assertEqual(proposal["teamB"]["value"], "twis")
        self.assertEqual(proposal["sideAssignment"]["value"], "team_a_left")
        self.assertEqual(proposal["mapOrder"]["value"], 1)
        self.assertEqual(proposal["identityStatus"], "proposed")
        self.assertEqual(proposal["reviewTasks"], [])

    def test_unknown_map_becomes_a_blocking_review_task(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(),
            read_fn=self.fake_reader(map_text="SPONSOR BREAK"))
        self.assertIsNone(proposal["map"]["value"])
        self.assertIsNone(proposal["mode"]["value"])
        self.assertEqual(proposal["identityStatus"], "blocked")
        kinds = [t["kind"] for t in proposal["reviewTasks"]]
        self.assertIn("map_identity", kinds)

    def test_same_team_on_both_sides_is_a_blocking_contradiction(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(),
            read_fn=self.fake_reader(team_b="Al Qadsiah"))
        reasons = " ".join(t["reason"] for t in proposal["reviewTasks"])
        self.assertIn("one team on both sides", reasons)
        self.assertEqual(proposal["identityStatus"], "blocked")

    def test_unknown_team_blocks_and_makes_side_assignment_unknown(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(),
            read_fn=self.fake_reader(team_b="Some Unknown Org"))
        self.assertIsNone(proposal["teamB"]["value"])
        self.assertIsNone(proposal["sideAssignment"]["value"])
        self.assertIn("follows the team identities",
                      proposal["sideAssignment"]["reason"])

    def test_candidate_players_are_recorded_without_creating_rows(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        before = self.content.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(),
            read_fn=self.fake_reader(nameplates={("a", 1): "LIP",
                                                 ("a", 2): "TOTALLYNEW"}))
        after = self.content.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        self.assertEqual(before, after, "identity must never create a player")
        cands = proposal["candidatePlayers"]
        self.assertTrue(any(c["raw"] == "TOTALLYNEW" for c in cands))
        advisory = [t for t in proposal["reviewTasks"]
                    if t["kind"] == "player_identity"]
        self.assertTrue(advisory)
        self.assertEqual(advisory[0]["severity"], "advisory")

    def test_no_readable_frames_yields_all_unknown_and_a_blocking_task(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=[(0.0, os.path.join(self.tmp.name, "nope.png"))],
            read_fn=self.fake_reader())
        for signal in si.SIGNALS:
            self.assertIsNone(proposal[signal]["value"], signal)
        self.assertTrue(proposal["reviewTasks"])

    def test_map_order_is_chronological_across_segments(self):
        first = self.make_segment(start=100.0, end=700.0)
        second = self.make_segment(start=900.0, end=1500.0)
        for expected, sid in ((1, first), (2, second)):
            segment = seg.get_segment(self.store.con, sid)
            proposal = si.propose_identity(
                self.store.con, self.content, segment, layout=self.layout,
                frames=self.write_frames(count=2), read_fn=self.fake_reader())
            self.assertEqual(proposal["mapOrder"]["value"], expected)
            self.assertEqual(proposal["mapOrder"]["confidence"], 1.0)

    def test_a_rejected_sibling_does_not_consume_a_map_order(self):
        rejected = self.make_segment(start=50.0, end=200.0)
        seg.reject_segment(self.store.con, rejected, reason="desk segment")
        real = self.make_segment(start=300.0, end=900.0)
        segment = seg.get_segment(self.store.con, real)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(count=2), read_fn=self.fake_reader())
        self.assertEqual(proposal["mapOrder"]["value"], 1)


class TestSideContinuity(IdentityDbBase):
    def _frames_with_hues(self, hue_pairs):
        d = os.path.join(self.tmp.name, "hueframes")
        os.makedirs(d, exist_ok=True)
        out = []
        for i, (ha, hb) in enumerate(hue_pairs):
            frame = np.full((FRAME_H, FRAME_W, 3), 30, np.uint8)
            probe = self.layout["hud_probe"]
            for side, hue in (("a", ha), ("b", hb)):
                for rect in probe[f"chips_{side}"]:
                    x, y, w, h = rect
                    patch = np.zeros((h, w, 3), np.uint8)
                    patch[:, :] = (hue, 230, 240)
                    frame[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
            p = os.path.join(d, f"h{i}.png")
            cv2.imwrite(p, frame)
            out.append((float(i * 60), cv2.imread(p)))
        return out

    def test_stable_hues_report_one_side_assignment(self):
        res = si.side_continuity(self._frames_with_hues(
            [(15, 110), (15, 110), (16, 111)]), self.layout)
        self.assertFalse(res["swapCandidate"])
        self.assertIn("stable", res["reason"])

    def test_crossed_hues_are_a_side_swap_candidate(self):
        res = si.side_continuity(self._frames_with_hues(
            [(15, 110), (15, 110), (110, 15), (110, 15)]), self.layout)
        self.assertTrue(res["swapCandidate"])
        self.assertIn("swapped sides", res["reason"])

    def test_too_few_samples_is_reported_not_guessed(self):
        res = si.side_continuity(self._frames_with_hues([(15, 110)]), self.layout)
        self.assertFalse(res["swapCandidate"])
        self.assertIn("not enough", res["reason"])

    def test_a_detected_swap_makes_the_side_assignment_unknown(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        d = os.path.join(self.tmp.name, "swapframes")
        os.makedirs(d, exist_ok=True)
        paths = []
        for i, (ha, hb) in enumerate([(15, 110), (15, 110), (110, 15), (110, 15)]):
            frame = gameplay_frame(self.layout)
            probe = self.layout["hud_probe"]
            for side, hue in (("a", ha), ("b", hb)):
                for rect in probe[f"chips_{side}"]:
                    x, y, w, h = rect
                    patch = np.zeros((h, w, 3), np.uint8)
                    patch[:, :] = (hue, 230, 240)
                    frame[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
            p = os.path.join(d, f"s{i}.png")
            cv2.imwrite(p, frame)
            paths.append((float(i * 100), p))
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=paths, read_fn=self.fake_reader())
        self.assertTrue(proposal["sideContinuity"]["swapCandidate"])
        self.assertIsNone(proposal["sideAssignment"]["value"])
        kinds = [t["kind"] for t in proposal["reviewTasks"]]
        self.assertIn("side_assignment", kinds)


class TestPersistence(IdentityDbBase):
    def _proposal(self, **reader_kwargs):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(), read_fn=self.fake_reader(**reader_kwargs))
        si.store_proposals(self.store.con, sid, proposal)
        return sid, proposal

    def test_proposals_round_trip_and_never_touch_confirmed_columns(self):
        sid, proposal = self._proposal()
        loaded = si.load_proposals(self.store.con, sid)
        self.assertEqual(loaded["map"]["value"], "nepal")
        row = seg.get_segment(self.store.con, sid)
        self.assertEqual(row["identity_status"], "proposed")
        # The human-confirmed columns are still empty — a proposal is not truth.
        self.assertIsNone(row["map_name"])
        self.assertIsNone(row["team_a"])
        self.assertEqual(row["review_status"], "pending")

    def test_findings_carry_source_confidence_and_evidence(self):
        _sid, proposal = self._proposal()
        self.content.execute(
            """INSERT INTO ingest_runs (id, source_id, detector_version, status)
               VALUES ('ing-1', 'src', 'det-test', 'complete')""")
        self.content.commit()
        n = si.record_findings(self.content, "ing-1", proposal)
        self.assertEqual(n, len(si.SIGNALS))
        rows = list(self.content.execute(
            "SELECT * FROM ingest_findings WHERE ingest_id='ing-1'"))
        self.assertEqual(len(rows), len(si.SIGNALS))
        by_field = {r["field"]: r for r in rows}
        self.assertEqual(by_field["map"]["value"], "nepal")
        self.assertEqual(by_field["map"]["method"], "ocr-consensus")
        self.assertEqual(by_field["map"]["status"], "proposed")
        self.assertIsNotNone(by_field["map"]["confidence"])
        self.assertIn("reason", json.loads(by_field["map"]["notes"]))

    def test_rerunning_findings_is_idempotent(self):
        _sid, proposal = self._proposal()
        self.content.execute(
            """INSERT INTO ingest_runs (id, source_id, detector_version, status)
               VALUES ('ing-2', 'src', 'det-test', 'complete')""")
        self.content.commit()
        si.record_findings(self.content, "ing-2", proposal)
        si.record_findings(self.content, "ing-2", proposal)
        n = self.content.execute(
            "SELECT COUNT(*) FROM ingest_findings WHERE ingest_id='ing-2'"
        ).fetchone()[0]
        self.assertEqual(n, len(si.SIGNALS))

    def test_unknown_signals_are_stored_with_status_unknown(self):
        _sid, proposal = self._proposal(map_text="SPONSOR")
        self.content.execute(
            """INSERT INTO ingest_runs (id, source_id, detector_version, status)
               VALUES ('ing-3', 'src', 'det-test', 'complete')""")
        self.content.commit()
        si.record_findings(self.content, "ing-3", proposal)
        row = self.content.execute(
            "SELECT * FROM ingest_findings WHERE ingest_id='ing-3' AND field='map'"
        ).fetchone()
        self.assertEqual(row["status"], "unknown")


class TestAcceptProposed(IdentityDbBase):
    def _prepare(self, **reader_kwargs):
        sid = self.make_segment()
        self.store.con.execute(
            "UPDATE map_segments SET layout_id='owcs_jksix_qwc' WHERE id=?", (sid,))
        self.store.con.commit()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(), read_fn=self.fake_reader(**reader_kwargs))
        si.store_proposals(self.store.con, sid, proposal)
        return sid

    def test_accepting_a_complete_proposal_approves_the_segment(self):
        sid = self._prepare()
        row = si.accept_proposed(self.store.con, sid)
        self.assertEqual(row["review_status"], "approved")
        self.assertEqual(row["map_name"], "nepal")
        self.assertEqual(row["map_mode"], "Control")
        self.assertEqual(row["team_a"], "qadsiah")
        self.assertEqual(row["team_b"], "twis")
        self.assertEqual(row["candidate_map_order"], 1)
        self.assertIn("accepted the automatic identity proposal",
                      row["reviewer_note"])

    def test_a_blocked_proposal_is_refused(self):
        sid = self._prepare(map_text="SPONSOR")
        with self.assertRaises(ValueError) as ctx:
            si.accept_proposed(self.store.con, sid)
        self.assertIn("blocked proposal", str(ctx.exception))
        self.assertEqual(seg.get_segment(self.store.con, sid)["review_status"],
                         "pending")

    def test_a_segment_with_no_proposal_is_refused(self):
        sid = self.make_segment(video_id="other")
        with self.assertRaises(ValueError) as ctx:
            si.accept_proposed(self.store.con, sid)
        self.assertIn("no identity proposal", str(ctx.exception))

    def test_a_segment_without_a_layout_is_refused(self):
        sid = self.make_segment()
        segment = seg.get_segment(self.store.con, sid)
        proposal = si.propose_identity(
            self.store.con, self.content, segment, layout=self.layout,
            frames=self.write_frames(), read_fn=self.fake_reader())
        si.store_proposals(self.store.con, sid, proposal)
        with self.assertRaises(ValueError) as ctx:
            si.accept_proposed(self.store.con, sid)
        self.assertIn("no layout_id", str(ctx.exception))


class TestFindingsMigration(unittest.TestCase):
    def test_an_old_database_is_widened_in_place_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.sqlite")
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            con.executescript("""
                CREATE TABLE ingest_runs (id TEXT PRIMARY KEY, source_id TEXT,
                                          status TEXT);
                CREATE TABLE ingest_findings (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ingest_id TEXT NOT NULL REFERENCES ingest_runs(id),
                  kind TEXT NOT NULL CHECK (kind IN ('team_identity')),
                  field TEXT, raw_text TEXT, value TEXT, confidence REAL,
                  method TEXT, evidence_path TEXT,
                  status TEXT NOT NULL DEFAULT 'candidate'
                         CHECK (status IN ('candidate','confirmed','rejected')),
                  notes TEXT, created_at TEXT);
                INSERT INTO ingest_runs VALUES ('r1','s','complete');
                INSERT INTO ingest_findings (ingest_id, kind, field, value)
                  VALUES ('r1','team_identity','a','twis');
            """)
            con.commit()
            # Before: the new kind is rejected outright.
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO ingest_findings (ingest_id, kind) "
                            "VALUES ('r1','segment_identity')")
            content_db._widen_ingest_findings(con)
            con.commit()
            # After: the old row survives and the new kind/status are allowed.
            self.assertEqual(con.execute(
                "SELECT value FROM ingest_findings WHERE field='a'"
            ).fetchone()["value"], "twis")
            con.execute("INSERT INTO ingest_findings (ingest_id, kind, status) "
                        "VALUES ('r1','segment_identity','unknown')")
            con.commit()
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM ingest_findings").fetchone()[0], 2)
            # Idempotent: a second run changes nothing.
            content_db._widen_ingest_findings(con)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM ingest_findings").fetchone()[0], 2)
            con.close()

    def test_schema_and_migration_allow_the_same_kinds(self):
        with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as f:
            ddl = f.read()
        for kind in content_db.FINDING_KINDS:
            self.assertIn(f"'{kind}'", ddl, kind)
        for status in content_db.FINDING_STATUSES:
            self.assertIn(f"'{status}'", ddl, status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
