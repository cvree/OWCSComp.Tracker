#!/usr/bin/env python3
"""
test_label_batch.py — the labelling loop, held to what it claims.

Offline, on the real Nepal evidence crops committed under
reports/ingest/qad-twis-nepal. The detector's own confident readings there
serve as the answer key for PURITY (a card must hold one hero) and for the
timeline LINKS (a linked card must be the same hero). They are never used
as labels by the code under test.

    python3 pipeline/test_label_batch.py
"""
from __future__ import annotations

import collections
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402
import label_batch as lb  # noqa: E402
import label_forge as lf  # noqa: E402

REPORT = os.path.join(db.REPO_ROOT, "reports", "ingest", "qad-twis-nepal")
LAYOUT = os.path.join(db.REPO_ROOT, "layouts", "owcs_jksix_qwc.json")
FIXED_TIME = "2026-10-29T00:00:00+00:00"


def answer_key() -> dict[str, str]:
    """{crop file: hero} for crops the detector read with confidence."""
    out = {}
    with open(os.path.join(REPORT, "observations.jsonl"), encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            for s in (o.get("slots") or {}).values():
                if s.get("crop") and s.get("hero") and s.get("score", 0) > 0.8:
                    out[s["crop"]] = s["hero"]
    return out


class Built:
    """One batch built once and shared, in a throwaway repo root."""
    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            import capture
            root = tempfile.mkdtemp(prefix="label-test-")
            crops = lb.crops_from_report(REPORT)
            batch = lb.build_batch(
                crops, batch_id="nepal", layout=capture.load_layout(LAYOUT),
                layout_id="owcs_jksix_qwc", source={"title": "Nepal"},
                out_dir=os.path.join(root, "labelling", "nepal"),
                crops_dir=os.path.join(REPORT, "evidence"), write_crops=False,
                created_at=FIXED_TIME, repo_root=db.REPO_ROOT)
            cls._cache = (root, crops, batch)
        return cls._cache


def majority(card: dict, key: dict[str, str]) -> str | None:
    votes = collections.Counter(key[m[0]] for m in card["members"] if m[0] in key)
    return votes.most_common(1)[0][0] if votes else None


class TestCards(unittest.TestCase):
    def test_three_thousand_portraits_become_a_page_of_cards(self):
        _root, crops, batch = Built.get()
        self.assertEqual(len(crops), 3070)
        self.assertLessEqual(len(batch["cards"]), 24,
                             "a person should name a page, not a phone book")
        self.assertGreaterEqual(len(batch["cards"]), 8)

    def test_a_card_holds_one_hero(self):
        """Purity, measured on every member the answer key can speak for."""
        _root, _crops, batch = Built.get()
        key = answer_key()
        agree = total = 0
        for card in batch["cards"]:
            votes = collections.Counter(key[m[0]] for m in card["members"] if m[0] in key)
            if votes:
                agree += votes.most_common(1)[0][1]
                total += sum(votes.values())
        self.assertGreater(total, 1000)
        self.assertGreaterEqual(agree / total, 0.98, f"purity {agree / total:.3f}")

    def test_every_hero_on_the_map_has_a_card(self):
        _root, _crops, batch = Built.get()
        key = answer_key()
        found = {majority(c, key) for c in batch["cards"]} - {None}
        self.assertEqual(found, set(key.values()))

    def test_a_timeline_link_joins_the_same_hero(self):
        """A grey or ult-flash portrait sits inside its hero's own stint, in
        its hero's own slot. Every link the batch makes must hold up."""
        _root, _crops, batch = Built.get()
        key = answer_key()
        by_id = {c["id"]: c for c in batch["cards"]}
        links = [c for c in batch["cards"] if c["nestedIn"]]
        self.assertGreaterEqual(len(links), 3)
        for c in links:
            self.assertEqual(majority(c, key), majority(by_id[c["nestedIn"]], key),
                             f"{c['id']} linked to {c['nestedIn']}")
            self.assertIsNone(by_id[c["nestedIn"]]["nestedIn"],
                              "a link points at a root, never along a chain")

    def test_cards_are_shown_largest_first_with_their_evidence(self):
        _root, _crops, batch = Built.get()
        sizes = [c["size"] for c in batch["cards"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        for c in batch["cards"]:
            self.assertGreaterEqual(c["size"], lb.MIN_CLUSTER)
            self.assertLessEqual(len(c["members"]), lb.MEMBER_CAP)
            self.assertTrue(c["slots"])
            self.assertLessEqual(c["tFirst"], c["tLast"])

    def test_images_are_written_and_crops_are_referenced_not_copied(self):
        root, _crops, batch = Built.get()
        out = os.path.join(root, "labelling", "nepal")
        for c in batch["cards"]:
            self.assertTrue(os.path.exists(os.path.join(out, c["image"])))
            for s in c["samples"]:
                self.assertTrue(os.path.exists(os.path.join(out, s["image"])))
            for name, _t, _slot in c["members"]:
                self.assertTrue(os.path.exists(os.path.join(REPORT, "evidence", name)))
        self.assertEqual(batch["cropsDir"], "reports/ingest/qad-twis-nepal/evidence")

    def test_rebuilding_from_the_same_crops_is_byte_identical(self):
        import capture
        root, crops, _batch = Built.get()
        out = os.path.join(root, "again")
        lb.build_batch(crops, batch_id="nepal", layout=capture.load_layout(LAYOUT),
                       layout_id="owcs_jksix_qwc", source={"title": "Nepal"},
                       out_dir=out, crops_dir=os.path.join(REPORT, "evidence"),
                       write_crops=False, created_at=FIXED_TIME)
        a = open(os.path.join(root, "labelling", "nepal", "batch.json")).read()
        b = open(os.path.join(out, "batch.json")).read()
        self.assertEqual(a, b)

    def test_no_card_carries_a_name(self):
        """A card is a grouping. Nothing in the batch may look like a label."""
        _root, _crops, batch = Built.get()
        for c in batch["cards"]:
            self.assertFalse({"hero", "label", "suggestion", "heroId"} & set(c))


class TestIndex(unittest.TestCase):
    def test_the_committed_index_is_what_a_fresh_build_produces(self):
        """The page renders this file; a stale one shows cards that do not
        exist, or hides ones that do."""
        with open(lb.INDEX_JS, encoding="utf-8") as f:
            self.assertEqual(f.read(), lb.index_text(lb.build_index()))

    def test_the_index_carries_no_wall_clock(self):
        root, _crops, _batch = Built.get()
        a = lb.index_text(lb.build_index(os.path.join(root, "labelling")))
        b = lb.index_text(lb.build_index(os.path.join(root, "labelling")))
        self.assertEqual(a, b)
        self.assertIn(FIXED_TIME, a)

    def test_the_index_never_ships_member_lists(self):
        root, _crops, _batch = Built.get()
        idx = lb.build_index(os.path.join(root, "labelling"))
        for c in idx["batches"][0]["cards"]:
            self.assertNotIn("members", c)

    def test_state_follows_the_files(self):
        root, _crops, _batch = Built.get()
        d = tempfile.mkdtemp()
        shutil.copytree(os.path.join(root, "labelling", "nepal"), os.path.join(d, "nepal"))
        self.assertEqual(lb.build_index(d)["batches"][0]["state"], "needs-labels")
        with open(os.path.join(d, "nepal", "labels.json"), "w") as f:
            json.dump({"labels": {"c00": "kiriko"}}, f)
        self.assertEqual(lb.build_index(d)["batches"][0]["state"], "labelled")
        with open(os.path.join(d, "nepal", "forge-report.json"), "w") as f:
            json.dump({"promotion": {"promotedHeroes": ["kiriko"]}}, f)
        row = lb.build_index(d)["batches"][0]
        self.assertEqual((row["state"], row["promoted"]), ("forged", ["kiriko"]))


class TestPicking(unittest.TestCase):
    def row(self, vid, title, dur):
        return {"videoId": vid, "title": title, "durationSeconds": dur,
                "url": f"https://www.twitch.tv/videos/{vid}"}

    def test_prefers_a_named_original_and_then_the_longest(self):
        q = [self.row("1", "[DROPS] CAH Season Playoffs | Day 1", 90000),
             self.row("2", "[REBROADCAST] OWCS 2026 | Midseason Day 1", 50000),
             self.row("3", "OWCS 2026 | Midseason Day 1", 41000),
             self.row("4", "OWCS 2026 | Stage 2 Week 1", 25000)]
        self.assertEqual(lb.pick_broadcast(q, allowed_families=["owcs"])["videoId"], "3")

    def test_never_repeats_a_batched_broadcast(self):
        q = [self.row("3", "OWCS 2026 | Midseason Day 1", 41000),
             self.row("4", "OWCS 2026 | Stage 2 Week 1", 25000)]
        self.assertEqual(lb.pick_broadcast(q, allowed_families=["owcs"],
                                           exclude={"3"})["videoId"], "4")
        self.assertIsNone(lb.pick_broadcast(q, allowed_families=["owcs"],
                                            exclude={"3", "4"}))

    def test_a_title_that_names_no_family_is_not_harvested(self):
        q = [self.row("1", "Overwatch Spotlight", 30000)]
        self.assertIsNone(lb.pick_broadcast(q, allowed_families=["owcs"]))

    def test_dense_offsets_cover_every_window_within_budget(self):
        wins = [{"start": 1000, "end": 1900}, {"start": 5000, "end": 5900}]
        offs = lb.dense_offsets(wins, step=10, pad=60, duration=9000, budget=60)
        self.assertLessEqual(len(offs), 60)
        self.assertTrue(any(t < 2000 for t in offs) and any(t > 4900 for t in offs))
        self.assertEqual(offs, sorted(offs))


class TestForge(unittest.TestCase):
    """label_forge: refusals first, then the real thing."""

    def setUp(self):
        root, _crops, batch = Built.get()
        self.batch = batch
        self.root = tempfile.mkdtemp(prefix="label-forge-")
        shutil.copytree(os.path.join(root, "labelling", "nepal"),
                        os.path.join(self.root, "nepal"))
        self.roster = lf.roster_ids()

    def test_a_typo_never_becomes_a_hero(self):
        with self.assertRaises(lf.ForgeRefusal) as cm:
            lf.resolve_labels(self.batch, {"labels": {"c00": "kirikoo"}}, self.roster)
        self.assertIn("not a hero id", str(cm.exception))

    def test_a_card_the_batch_does_not_have_is_refused(self):
        with self.assertRaises(lf.ForgeRefusal):
            lf.resolve_labels(self.batch, {"labels": {"c99": "kiriko"}}, self.roster)

    def test_labels_for_another_batch_are_refused(self):
        with self.assertRaises(lf.ForgeRefusal):
            lf.resolve_labels(self.batch, {"batchId": "other",
                                           "labels": {"c00": "kiriko"}}, self.roster)

    def test_nothing_named_is_nothing_to_forge(self):
        with self.assertRaises(lf.ForgeRefusal):
            lf.resolve_labels(self.batch, {"labels": {"c00": "skip"}}, self.roster)

    def test_every_crop_says_how_it_was_labelled(self):
        m = lf.build_manifest(self.batch, {"c00": "kiriko"})
        self.assertTrue(m["crops"])
        self.assertEqual({c["labelSource"] for c in m["crops"]}, {"human-cluster"})
        self.assertEqual({c["clusterId"] for c in m["crops"]}, {"c00"})

    def test_missing_crops_say_where_to_get_them(self):
        b = dict(self.batch, cropsDir="work/nowhere",
                 cropsArtifact={"name": "label-crops-x", "runId": 7})
        with self.assertRaises(lf.ForgeRefusal) as cm:
            lf.build_manifest(b, {"c00": "kiriko"})
        self.assertIn("label-crops-x", str(cm.exception))

    def test_named_cards_become_templates_that_pass_on_unseen_frames(self):
        """The whole loop on real footage: name every card the way a person
        reading the portrait would, forge, validate on held-out frames —
        and the leave-one-hero-out safety test must pass too."""
        key = answer_key()
        labels = {c["id"]: (majority(c, key) or "skip") for c in self.batch["cards"]}
        with open(os.path.join(self.root, "nepal", "labels.json"), "w") as f:
            json.dump({"batchId": "nepal", "labelledBy": "test", "labels": labels}, f)
        s = lf.run("nepal", promote=False, root=self.root,
                   staging_root=os.path.join(self.root, "_staging"))
        verdicts = {h: e["verdict"] for h, e in s["heroes"].items()}
        self.assertEqual(set(verdicts), set(key.values()))
        self.assertTrue(all(v == "VALIDATED" for v in verdicts.values()), verdicts)
        self.assertTrue(s["leaveOneOut"]["passed"], s["leaveOneOut"]["note"])
        self.assertTrue(s["promotion"]["dryRun"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "nepal", "forge-report.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
