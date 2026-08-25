#!/usr/bin/env python3
"""
test_template_pipeline.py — the quality gate, the evidence builder, the
forge, and held-out validation.

Everything here is offline and deterministic. Where a test needs a portrait
it synthesises one; where it needs real footage it uses the committed
`reports/ingest/qad-twis-nepal` evidence and SKIPS if that is not present,
so the suite runs on a checkout without it.

The tests are organised around the ways this machinery could lie:

  * a bad crop becoming a template                (TestQualityGate)
  * a stint's label being invented rather than    (TestEvidenceLabelling)
    derived, or a transition being labelled
  * a template being scored on its own frame      (TestHeldOutIsEnforced)
  * an uncovered hero being confidently named     (TestUnknownRatherThanGuess)
  * a failing hero being promoted anyway          (TestPromotionGate)
  * coverage being reported as readiness          (TestCoverageNeverRoundsUp)

Run: python3 pipeline/test_template_pipeline.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import detect  # noqa: E402
import site_paths  # noqa: E402  (cross-drive-safe relative paths)
import hero_coverage as hc  # noqa: E402
import portrait_roi as proi  # noqa: E402
import template_evidence as te  # noqa: E402
import template_forge as tf  # noqa: E402
import template_quality as tq  # noqa: E402
import template_validate as tv  # noqa: E402

NEPAL = os.path.join(ROOT, "reports", "ingest", "qad-twis-nepal")
HAS_NEPAL = os.path.exists(os.path.join(NEPAL, "observations.jsonl"))


# ------------------------------------------------------------- synthetics
def portrait(seed: int, size: int = 40) -> np.ndarray:
    """A busy, distinctive fake portrait. Deterministic per seed."""
    rng = np.random.default_rng(seed)
    img = rng.integers(20, 236, (size, size, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    cv2.circle(img, (size // 2, size // 2), size // 3,
               tuple(int(v) for v in rng.integers(0, 256, 3)), -1)
    cv2.putText(img, str(seed % 10), (2, size - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return img


def gray(img) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def tinted(img, side: str) -> np.ndarray:
    """The blue/red cast broadcasts apply per team."""
    out = img.astype(np.float32)
    if side == "a":
        out[:, :, 0] *= 1.35        # blue up
    else:
        out[:, :, 2] *= 1.35        # red up
    return np.clip(out, 0, 255).astype(np.uint8)


def compressed(img, quality: int = 28) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img


def rescaled(img, factor: float) -> np.ndarray:
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(4, int(w * factor)), max(4, int(h * factor))))
    return cv2.resize(small, (w, h))


def obstructed(img, frac: float = 0.55) -> np.ndarray:
    out = img.copy()
    h = out.shape[0]
    out[int(h * (1 - frac)):, :] = (200, 200, 200)
    return out


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tplpipe_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ---------------------------------------------------------- quality gate
class TestQualityGate(Temp):
    def test_a_flat_crop_is_rejected(self):
        """The exact defect found in production: a solid-grey template.

        `templates/owcs_jksix_qwc/mauga.v1.png` was a 35x35 block of one
        colour, harvested because the old variant picker maximised
        DIFFERENCE with no quality floor — and a blank frame is maximally
        different from a portrait by construction.
        """
        flat = np.full((35, 35), 131, np.uint8)
        result = tq.assess_crop(flat, hero_id="mauga",
                                require_provenance=False)
        self.assertEqual(result["verdict"], tq.REJECT)
        names = {c["name"] for c in result["checks"] if c["status"] == tq.FAIL}
        self.assertIn("contrast", names)
        self.assertIn("not-flat", names)

    def test_a_blurred_crop_is_rejected(self):
        blurry = cv2.GaussianBlur(gray(portrait(1)), (15, 15), 0)
        result = tq.assess_crop(blurry, hero_id="ana",
                                require_provenance=False)
        self.assertEqual(result["verdict"], tq.REJECT)

    def test_an_overlay_band_is_rejected(self):
        result = tq.assess_crop(gray(obstructed(portrait(2))),
                                hero_id="ana", require_provenance=False)
        self.assertEqual(result["verdict"], tq.REJECT)
        self.assertTrue(any(c["name"] == "unobstructed" and c["status"] == tq.FAIL
                            for c in result["checks"]))

    def test_a_clean_portrait_is_accepted(self):
        result = tq.assess_crop(gray(portrait(3)), hero_id="ana",
                                require_provenance=False)
        self.assertEqual(result["verdict"], tq.ACCEPT, result["reasons"])

    def test_a_crop_that_looks_like_another_hero_is_rejected(self):
        """A mislabeled cluster is invisible to every per-crop metric.

        The only thing that can catch it is comparison with the rest of the
        set: two different heroes do not correlate at 0.93.
        """
        art = gray(portrait(4))
        result = tq.assess_crop(art, hero_id="reaper",
                                other_heroes={"ana.png": art.copy()},
                                require_provenance=False)
        self.assertEqual(result["verdict"], tq.REJECT)
        self.assertTrue(any("mislabeled" in r for r in result["reasons"]))

    def test_a_duplicate_of_the_same_hero_adds_nothing(self):
        art = gray(portrait(5))
        result = tq.assess_crop(art, hero_id="ana",
                                same_hero={"ana.png": art.copy()},
                                require_provenance=False)
        self.assertEqual(result["verdict"], tq.REJECT)
        self.assertTrue(any("same picture again" in r
                            for r in result["reasons"]))

    def test_official_art_can_never_become_a_template(self):
        result = tq.assess_crop(
            gray(portrait(6)), hero_id="ana",
            provenance={"sourceVideo": "assets/img/heroes/official/ana/icon.png",
                        "offset": 0.0})
        self.assertEqual(result["verdict"], tq.REJECT)
        self.assertTrue(any("official hero art" in r
                            for r in result["reasons"]))

    def test_an_untraceable_crop_is_rejected_when_provenance_is_required(self):
        result = tq.assess_crop(gray(portrait(7)), hero_id="ana",
                                provenance={}, require_provenance=True)
        self.assertEqual(result["verdict"], tq.REJECT)

    def test_the_committed_production_set_has_no_rejects(self):
        """A regression guard on the real repository, not a fixture.

        This is what would have caught mauga.v1 the day it was committed.
        """
        import template_bootstrap as tb
        checked = 0
        for name in sorted(os.listdir(os.path.join(ROOT, "templates"))):
            tdir = os.path.join(ROOT, "templates", name)
            if not os.path.isdir(tdir) or name.startswith("_"):
                continue
            report = tq.audit_set(tdir, provenance=tb.load_provenance(tdir))
            checked += 1
            self.assertFalse(
                report["rejected"],
                f"{name} ships template(s) the quality gate rejects: "
                + "; ".join(f"{fn}: {report['results'][fn]['reasons']}"
                            for fn in report["rejected"]))
        self.assertTrue(checked, "no per-package template sets were checked")


# ------------------------------------------------------- evidence labels
class TestEvidenceLabelling(Temp):
    def _obs(self, rows):
        path = os.path.join(self.tmp, "observations.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        os.makedirs(os.path.join(self.tmp, "evidence"), exist_ok=True)
        for row in rows:
            for obs in (row.get("slots") or {}).values():
                if obs.get("crop"):
                    cv2.imwrite(os.path.join(self.tmp, "evidence",
                                             obs["crop"]), portrait(1))
        return self.tmp

    def _run(self, hero_at, n=60, step=5.0):
        rows = []
        for i in range(n):
            t = 1000.0 + i * step
            hero, score = hero_at(t)
            rows.append({"t": t, "state": "gameplay", "slots": {
                "a1": {"hero": hero, "score": score,
                       "crop": f"t{t:09.1f}_a1.png"}}})
        return te.build_from_report(self._obs(rows), layout_id="test")

    def test_a_long_agreeing_stint_becomes_ground_truth(self):
        man = self._run(lambda t: ("ana", 0.95))
        self.assertGreater(man["counts"]["labeled"], 40)
        self.assertEqual(set(man["counts"]["byHero"]), {"ana"})

    def test_an_unknown_read_inside_a_stint_is_still_labelled(self):
        """The whole point: a crop the detector got wrong is still evidence.

        If UNKNOWNs were dropped, validation would only ever be scored on
        frames the detector already handles, which measures nothing.
        """
        man = self._run(lambda t: ("UNKNOWN", 0.1) if t == 1100.0
                        else ("ana", 0.95))
        labelled = {c["file"]: c for c in man["crops"]}
        self.assertIn("t0001100.0_a1.png", labelled)
        self.assertEqual(labelled["t0001100.0_a1.png"]["hero"], "ana")
        self.assertEqual(labelled["t0001100.0_a1.png"]["detectorHero"],
                         "UNKNOWN")

    def test_a_short_low_confidence_run_is_excluded_not_absorbed(self):
        """The bug this rule exists for, reproduced.

        Slot a5 of the real Nepal footage shows Lúcio for ten seconds before
        swapping to Juno, read at ~0.51. An earlier version of `_segment`
        only split on reads above 0.6, so those frames were absorbed into
        the Juno stint and labelled `juno` — and validation then reported
        the detector as WRONG for correctly reading them as Lúcio.
        """
        man = self._run(lambda t: ("lucio", 0.51) if t < 1030.0
                        else ("juno", 0.95))
        labelled = {c["file"]: c["hero"] for c in man["crops"]}
        for t in (1000.0, 1010.0, 1025.0):
            self.assertNotEqual(
                labelled.get(f"t{t:09.1f}_a1.png"), "juno",
                "a weak but consistent run of a different hero was absorbed "
                "into its neighbour's stint")
        excluded = {c["file"] for c in man["excluded"]}
        self.assertIn("t0001000.0_a1.png", excluded)

    def test_frames_next_to_a_swap_are_excluded(self):
        man = self._run(lambda t: ("ana", 0.95) if t < 1150.0
                        else ("lucio", 0.95))
        by_t = {c["t"]: c for c in man["crops"]}
        self.assertNotIn(1145.0, by_t, "a frame 5s before a swap was labelled")
        self.assertNotIn(1150.0, by_t, "the swap frame itself was labelled")
        self.assertIn(1100.0, by_t, "the middle of a stint should be labelled")

    def test_a_human_label_overrides_consensus_and_says_so(self):
        rows = []
        for i in range(40):
            t = 1000.0 + i * 5.0
            rows.append({"t": t, "state": "gameplay", "slots": {
                "a1": {"hero": "ana", "score": 0.95,
                       "crop": f"t{t:09.1f}_a1.png"}}})
        man = te.build_from_report(
            self._obs(rows), layout_id="test",
            human_labels={"t0001100.0_a1.png": "kiriko"})
        rec = next(c for c in man["crops"]
                   if c["file"] == "t0001100.0_a1.png")
        self.assertEqual(rec["hero"], "kiriko")
        self.assertEqual(rec["labelSource"], te.LABEL_HUMAN)

    def test_the_manifest_says_its_labels_are_not_human(self):
        man = self._run(lambda t: ("ana", 0.95))
        self.assertIn("NOT a human", man["labelPolicy"]["honesty"])


# -------------------------------------------------------- held-out rules
class TestHeldOutIsEnforced(Temp):
    def _set(self, *, with_provenance: bool, offsets=(100.0,)):
        tdir = os.path.join(self.tmp, "templates")
        os.makedirs(tdir, exist_ok=True)
        cv2.imwrite(os.path.join(tdir, "ana.png"), portrait(11))
        cv2.imwrite(os.path.join(tdir, "lucio.png"), portrait(22))
        if with_provenance:
            import template_bootstrap as tb
            tb.write_provenance(tdir, [
                {"file": "ana.png", "sourceReport": "src", "offset": offsets[0]},
                {"file": "lucio.png", "sourceReport": "src", "offset": offsets[0]},
            ])
        return tdir

    def _manifest(self, times, hero="ana"):
        crops = os.path.join(self.tmp, "crops")
        os.makedirs(crops, exist_ok=True)
        records = []
        for t in times:
            fn = f"t{t:09.1f}_a1.png"
            cv2.imwrite(os.path.join(crops, fn), portrait(11))
            records.append({"file": fn, "hero": hero, "slot": "a1", "t": t,
                            "state": "gameplay", "side": "a",
                            "labelSource": te.LABEL_CONSENSUS})
        return {
            "evidenceSetVersion": te.MANIFEST_VERSION,
            # site_relpath, not os.path.relpath: on a Windows runner the
            # checkout is on D: and tempfile hands back C:, and there is no
            # relative path between two drives — relpath raises ValueError.
            # That is the exact bug site_paths exists to absorb.
            "sourceReport": "src", "cropsDir": site_paths.site_relpath(crops, ROOT),
            "layoutId": "test", "crops": records, "excluded": [],
            "counts": {"labeled": len(records)}, "stints": [],
            "labelPolicy": {"method": "test"},
        }

    def test_without_provenance_a_hero_is_unverifiable_not_passing(self):
        report = tv.validate(self._set(with_provenance=False),
                             self._manifest([500.0 + i * 10 for i in range(30)]))
        self.assertEqual(report["heroes"]["ana"]["verdict"], tv.UNVERIFIABLE)
        self.assertIn("no provenance", report["heroes"]["ana"]["why"])

    def test_a_crop_near_a_template_frame_is_excluded_from_the_score(self):
        tdir = self._set(with_provenance=True, offsets=(500.0,))
        man = self._manifest([500.0, 505.0, 510.0])
        report = tv.validate(tdir, man, min_gap=30.0)
        entry = report["heroes"]["ana"]
        self.assertEqual(entry["trials"], 0)
        self.assertEqual(entry["contaminated"], 3)
        self.assertEqual(entry["verdict"], tv.UNVERIFIABLE)

    def test_a_distant_crop_counts_and_validates(self):
        tdir = self._set(with_provenance=True, offsets=(100.0,))
        man = self._manifest([500.0 + i * 10 for i in range(30)])
        report = tv.validate(tdir, man, min_gap=30.0)
        entry = report["heroes"]["ana"]
        self.assertEqual(entry["trials"], 30)
        self.assertEqual(entry["verdict"], tv.VALIDATED, entry["why"])

    def test_too_few_trials_is_weak_not_validated(self):
        tdir = self._set(with_provenance=True, offsets=(100.0,))
        report = tv.validate(tdir, self._manifest([500.0, 600.0, 700.0]),
                             min_gap=30.0)
        self.assertEqual(report["heroes"]["ana"]["verdict"], tv.WEAK)

    def test_one_wrong_answer_fails_the_hero_outright(self):
        """Accuracy is not the bar. A confident wrong hero is disqualifying.

        An UNKNOWN loses a data point; a wrong hero writes false data into a
        published composition, so no amount of otherwise-good accuracy
        redeems it.
        """
        tdir = self._set(with_provenance=True, offsets=(100.0,))
        man = self._manifest([500.0 + i * 10 for i in range(40)])
        # One crop is actually the OTHER hero's art, still labelled 'ana'.
        bad = man["crops"][5]
        cv2.imwrite(os.path.join(ROOT, man["cropsDir"], bad["file"]),
                    portrait(22))
        report = tv.validate(tdir, man, min_gap=30.0)
        entry = report["heroes"]["ana"]
        self.assertEqual(entry["wrong"], 1)
        self.assertEqual(entry["verdict"], tv.FAILED)
        self.assertGreater(entry["accuracy"], 0.9)   # still "accurate"
        self.assertFalse(report["passed"])


# ------------------------------------------------- UNKNOWN, never a guess
class TestUnknownRatherThanGuess(Temp):
    def _lib(self, heroes):
        tdir = os.path.join(self.tmp, "t")
        os.makedirs(tdir, exist_ok=True)
        for i, h in enumerate(heroes):
            cv2.imwrite(os.path.join(tdir, f"{h}.png"), portrait(100 + i))
        return detect.load_templates(tdir), tdir

    def test_an_unknown_portrait_is_not_named(self):
        lib, _ = self._lib(["ana", "lucio", "rein"])
        stranger = gray(portrait(999))
        self.assertEqual(detect.read_slot(stranger, lib)["hero"], "UNKNOWN")

    def test_noise_and_blank_slots_are_unknown(self):
        lib, _ = self._lib(["ana", "lucio", "rein"])
        for probe in (np.zeros((40, 40), np.uint8),
                      np.full((40, 40), 255, np.uint8),
                      np.random.default_rng(0).integers(
                          0, 256, (40, 40), dtype=np.uint8)):
            self.assertEqual(detect.read_slot(probe, lib)["hero"], "UNKNOWN")

    def test_the_default_margin_is_the_measured_one_not_the_old_one(self):
        """0.04 let a *typical* unknown portrait through as a hero.

        Measured on 2,008 held-out Nepal crops with one hero removed, the
        MEDIAN impostor margin was 0.071 — above the old bar. This asserts
        the constant, because a well-meaning revert would silently restore
        a 58%-of-unknowns-named detector.
        """
        self.assertGreaterEqual(detect.MIN_MARGIN, 0.12)

    def test_a_layout_can_tighten_but_the_default_is_unchanged(self):
        self.assertEqual(detect.detector_profile(None)["floor"],
                         detect.UNKNOWN_FLOOR)
        tight = detect.detector_profile({"unknown_floor": 0.7,
                                         "min_margin": 0.3})
        self.assertEqual(tight["floor"], 0.7)
        self.assertEqual(tight["minMargin"], 0.3)


# ---------------------------------------------------------- robustness
class TestPortraitStates(Temp):
    """Alive/tinted/compressed/scaled/mirrored, against one template each."""

    def setUp(self):
        super().setUp()
        tdir = os.path.join(self.tmp, "t")
        os.makedirs(tdir, exist_ok=True)
        self.art = {h: portrait(200 + i)
                    for i, h in enumerate(("ana", "lucio", "rein", "dva"))}
        for h, img in self.art.items():
            cv2.imwrite(os.path.join(tdir, f"{h}.png"), img)
        self.lib = detect.load_templates(tdir)

    def _reads(self, transform):
        return {h: detect.read_slot(gray(transform(img)), self.lib)["hero"]
                for h, img in self.art.items()}

    def test_team_tint_does_not_change_the_answer(self):
        for side in ("a", "b"):
            self.assertEqual(self._reads(lambda i, s=side: tinted(i, s)),
                             {h: h for h in self.art})

    def test_broadcast_compression_does_not_change_the_answer(self):
        self.assertEqual(self._reads(lambda i: compressed(i, 30)),
                         {h: h for h in self.art})

    def test_a_downscaled_capture_still_matches(self):
        self.assertEqual(self._reads(lambda i: rescaled(i, 0.5)),
                         {h: h for h in self.art})

    def test_a_slot_cropped_at_another_resolution_still_matches(self):
        """Templates are normalized to the probe's size, both directions."""
        for size in (24, 32, 64, 96):
            with self.subTest(size=size):
                reads = {h: detect.read_slot(
                    cv2.resize(gray(img), (size, size)), self.lib)["hero"]
                    for h, img in self.art.items()}
                self.assertEqual(reads, {h: h for h in self.art})

    def test_a_mirrored_portrait_is_never_read_as_a_different_hero(self):
        """Broadcasts mirror slot ORDER, never the portrait art itself.

        So a flipped crop means the capture or the layout is wrong. The
        guarantee asserted here is the one the detector can actually make
        and the one that matters: a flipped portrait may read as UNKNOWN, or
        (if the art is near-symmetric) as itself — but it must never come
        back as *some other hero*, because that is the answer that would put
        false data in a published composition rather than a gap in it.
        """
        reads = self._reads(lambda i: cv2.flip(i, 1))
        wrong = {h: v for h, v in reads.items()
                 if v not in ("UNKNOWN", h)}
        self.assertFalse(wrong, f"a mirrored portrait became another hero: "
                                f"{wrong}")

    def test_an_obstructed_portrait_is_never_read_as_a_different_hero(self):
        """Same bar, for the case an ult flash or killfeed card creates.

        Requiring UNKNOWN outright would be over-claiming: measured on real
        Nepal frames, an ult-flash crop is bright and busy, not statistically
        flat, so no image-quality gate separates it from a clean portrait.
        What IS guaranteed is that obstruction turns a read into UNKNOWN or
        leaves it correct — never into a confident different hero.
        """
        for frac in (0.5, 0.7, 0.85):
            with self.subTest(obstruction=frac):
                reads = self._reads(lambda i, f=frac: obstructed(i, f))
                wrong = {h: v for h, v in reads.items()
                         if v not in ("UNKNOWN", h)}
                self.assertFalse(wrong, f"obstruction produced a wrong hero: "
                                        f"{wrong}")


# ------------------------------------------------------------- ROI probe
class TestPortraitRoiDiscovery(Temp):
    def test_a_name_strip_is_found_and_cut(self):
        crops = []
        rng = np.random.default_rng(4)
        for i in range(40):
            img = gray(portrait(300 + i, size=35))
            img[25:30, :] = 90          # flat separator bar
            img[30:, :] = rng.integers(0, 256, (5, 35), dtype=np.uint8)
            crops.append(img)
        result = proi.discover(crops)
        self.assertTrue(result["confident"])
        self.assertIsNotNone(result["roi"], result["reason"])
        self.assertAlmostEqual(result["roi"][3], 25 / 35, places=2)

    def test_an_all_portrait_slot_proposes_nothing(self):
        """No flat band anywhere -> no ROI, and matching is left alone.

        Uses pure textured noise rather than `portrait()`: the synthetic
        portrait has quiet rows below its circle, which is exactly the
        signal the probe is built to find, so it would (correctly) propose
        a cut and this test would be asserting the wrong thing.
        """
        rng = np.random.default_rng(9)
        crops = [rng.integers(0, 256, (35, 35), dtype=np.uint8)
                 for _ in range(40)]
        result = proi.discover(crops)
        self.assertIsNone(result["roi"], result["reason"])
        self.assertTrue(result["confident"])

    def test_too_few_samples_refuses_rather_than_guesses(self):
        result = proi.discover([gray(portrait(1, size=35))] * 3)
        self.assertIsNone(result["roi"])
        self.assertFalse(result["confident"])

    def test_the_roi_is_applied_to_templates_and_probes_alike(self):
        roi = detect.portrait_roi({"portrait_roi": [0.0, 0.0, 1.0, 0.7]})
        img = np.arange(100 * 50, dtype=np.uint8).reshape(100, 50)
        self.assertEqual(detect.apply_roi(img, roi).shape, (70, 50))
        self.assertIsNone(detect.portrait_roi({}))

    def test_a_nonsense_roi_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            detect.portrait_roi({"portrait_roi": [0.0, 0.9, 1.0, 0.2]})


# ------------------------------------------------------- promotion gate
class TestPromotionGate(Temp):
    def test_only_validated_heroes_are_promoted(self):
        staging = os.path.join(self.tmp, "staging")
        production = os.path.join(self.tmp, "production")
        os.makedirs(staging)
        os.makedirs(production)
        for h in ("ana", "lucio"):
            cv2.imwrite(os.path.join(staging, f"{h}.png"), portrait(1))
        import template_bootstrap as tb
        tb.write_provenance(staging, [
            {"file": "ana.png", "sourceReport": "s", "offset": 1.0},
            {"file": "lucio.png", "sourceReport": "s", "offset": 1.0}])
        validation = {
            "heroes": {"ana": {"verdict": tv.VALIDATED},
                       "lucio": {"verdict": tv.FAILED}},
            "evidence": {"sourceReport": "s", "layoutId": "l"},
        }
        result = tf.promote(staging, production, validation)
        self.assertEqual(result["promoted"], ["ana.png"])
        self.assertEqual(result["refused"], {"lucio": tv.FAILED})
        self.assertTrue(os.path.exists(os.path.join(production, "ana.png")))
        self.assertFalse(os.path.exists(os.path.join(production, "lucio.png")))

    def test_promotion_carries_provenance_for_promoted_files_only(self):
        staging = os.path.join(self.tmp, "s")
        production = os.path.join(self.tmp, "p")
        os.makedirs(staging)
        for h in ("ana", "lucio"):
            cv2.imwrite(os.path.join(staging, f"{h}.png"), portrait(1))
        import template_bootstrap as tb
        tb.write_provenance(staging, [
            {"file": "ana.png", "sourceReport": "s", "offset": 1.0},
            {"file": "lucio.png", "sourceReport": "s", "offset": 2.0}])
        tf.promote(staging, production, {
            "heroes": {"ana": {"verdict": tv.VALIDATED},
                       "lucio": {"verdict": tv.WEAK}},
            "evidence": {"sourceReport": "s", "layoutId": "l"}})
        prov = tb.load_provenance(production)
        self.assertEqual([e["file"] for e in prov["entries"]], ["ana.png"])


# ------------------------------------------------- the legacy harvest path
class TestLegacyHarvestIsGatedToo(Temp):
    """`harvest_templates.py --labels` is still a supported entry point.

    Everything the forge does can be bypassed by running the older tool, so
    the two defects that produced the committed junk templates are pinned
    here as well: no quality floor on variant selection, and a wipe of the
    whole output directory on every run.
    """

    def test_a_blank_crop_never_wins_the_diversity_contest(self):
        import harvest_templates as ht
        good = [os.path.join(self.tmp, f"good{i}.png") for i in range(3)]
        for i, path in enumerate(good):
            cv2.imwrite(path, portrait(500 + i))
        blank = os.path.join(self.tmp, "blank.png")
        cv2.imwrite(blank, np.full((40, 40, 3), 131, np.uint8))
        chosen = ht.pick_variants(good + [blank], 4)
        self.assertNotIn(blank, chosen,
                         "a solid-colour crop was picked as a template "
                         "because it was maximally different")
        self.assertTrue(chosen)

    def test_nothing_is_written_when_every_candidate_is_junk(self):
        import harvest_templates as ht
        junk = []
        for i in range(3):
            path = os.path.join(self.tmp, f"junk{i}.png")
            cv2.imwrite(path, np.full((40, 40, 3), 100 + i, np.uint8))
            junk.append(path)
        self.assertEqual(ht.pick_variants(junk, 3), [])

    def test_a_second_harvest_cannot_delete_the_first(self):
        """Adding two heroes must not remove the six already there."""
        import harvest_templates as ht

        out = os.path.join(self.tmp, "out")
        cand = os.path.join(out, "_candidates")
        members = os.path.join(cand, "members", "a1")
        os.makedirs(members)
        for hero in ("ana", "lucio", "rein"):
            cv2.imwrite(os.path.join(out, f"{hero}.png"), portrait(1))
        for i in range(3):
            cv2.imwrite(os.path.join(members, f"c0_t{i}.png"),
                        portrait(700 + i))
        with open(os.path.join(cand, "clusters.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"a1_c0": {"file": "x.png", "count": 3}}, f)

        class Args:
            labels = os.path.join(self.tmp, "labels.json")
            out_dir = out
            variants = 2
            replace_all = False
        Args.out = out
        with open(Args.labels, "w", encoding="utf-8") as f:
            json.dump({"a1_c0": "kiriko"}, f)

        ht.stage_labels(Args, ["a1"])
        survivors = {fn for fn in os.listdir(out) if fn.endswith(".png")}
        for hero in ("ana", "lucio", "rein"):
            self.assertIn(f"{hero}.png", survivors,
                          f"harvesting kiriko deleted the existing {hero} "
                          f"template")
        self.assertTrue(any(fn.startswith("kiriko") for fn in survivors))


# --------------------------------------------------------- the gap finder
class TestGapFinder(Temp):
    """Finding what a package cannot name, without naming it."""

    def setUp(self):
        super().setUp()
        import test_pipeline_synthetic as syn
        self.syn = syn
        self.frames = os.path.join(self.tmp, "frames")
        self.tdir = os.path.join(self.tmp, "templates")
        os.makedirs(self.frames)
        os.makedirs(self.tdir)
        self.covered = syn.COMP_A1[:3] + syn.COMP_B1[:3]
        for h in self.covered:
            cv2.imwrite(os.path.join(self.tdir, f"{h}.png"), syn.hero_icon(h))
        for i in range(30):
            cv2.imwrite(os.path.join(self.frames, f"{i:06d}.png"),
                        syn.make_frame(syn.COMP_A1, syn.COMP_B1, offset=i))
        self.layout = {"frame_width": syn.W, "frame_height": syn.H,
                       "slots_a": syn.SLOTS_A, "slots_b": syn.SLOTS_B}

    def test_it_finds_exactly_the_uncovered_slots(self):
        import hero_gap_finder as gf
        scan = gf.scan_frames(self.frames, self.layout, self.tdir)
        self.assertEqual(scan["framesScanned"], 30)
        slots = sorted(c["slot"] for c in scan["clusters"])
        self.assertEqual(slots, ["a4", "a5", "b4", "b5"],
                         "the finder should surface one candidate per slot "
                         "holding a hero with no template, and nothing else")

    def test_a_covered_slot_is_never_offered_as_a_candidate(self):
        import hero_gap_finder as gf
        scan = gf.scan_frames(self.frames, self.layout, self.tdir)
        for cluster in scan["clusters"]:
            self.assertNotIn(cluster["slot"], ("a1", "a2", "a3",
                                               "b1", "b2", "b3"))

    def test_a_one_frame_flicker_is_not_a_candidate(self):
        """Persistence is what separates a hero from a dissolve."""
        import hero_gap_finder as gf
        full = os.path.join(self.tmp, "full")
        os.makedirs(full)
        for h in self.syn.ALL_HEROES:
            cv2.imwrite(os.path.join(full, f"{h}.png"),
                        self.syn.hero_icon(h))
        blip = os.path.join(self.tmp, "blip")
        os.makedirs(blip)
        for i in range(30):
            frame = self.syn.make_frame(self.syn.COMP_A1, self.syn.COMP_B1,
                                        offset=i)
            if i == 7:            # one frame of something unrecognisable
                x, y, w, h = self.syn.SLOTS_A[0]
                frame[y:y + h, x:x + w] = np.random.default_rng(1).integers(
                    0, 256, (h, w, 3), dtype=np.uint8)
            cv2.imwrite(os.path.join(blip, f"{i:06d}.png"), frame)
        scan = gf.scan_frames(blip, self.layout, full)
        self.assertEqual(scan["clusters"], [],
                         "a single unrecognisable frame was offered as an "
                         "uncovered hero")

    def test_candidates_are_written_unlabelled(self):
        import hero_gap_finder as gf
        scan = gf.scan_frames(self.frames, self.layout, self.tdir)
        payload = gf.write_candidates(scan, os.path.join(self.tmp, "cand"),
                                      layout_id="syn")
        self.assertEqual(len(payload["items"]), 4)
        for item in payload["items"]:
            self.assertIsNone(item["heroId"],
                              "the finder guessed a hero name; it cannot "
                              "know one, and a guess becomes a template")
            self.assertTrue(os.path.exists(os.path.join(
                self.tmp, "cand", "_review", item["file"])))

    def test_the_plan_never_invents_a_source(self):
        import db
        import hero_gap_finder as gf
        con = db.connect()
        try:
            db.init_schema(con)
            plan = gf.plan_from_db(con, "owcs_jksix_qwc")
        finally:
            con.close()
        for hero in plan["reachable"]:
            self.assertTrue(hero["bestSource"]["matchId"],
                            "a harvest target with no real match behind it")
        self.assertEqual(
            len(plan["reachable"]) + len(plan["neverSeen"]),
            plan["rosterSize"] - plan["covered"],
            "every missing hero must be accounted for as either reachable "
            "or never-seen — silently dropping one hides a gap")


# ------------------------------------------------- coverage never lies
class TestCoverageNeverRoundsUp(unittest.TestCase):
    def test_the_readiness_line_reports_both_numbers(self):
        line = hc.readiness_line(8, 8, 52)
        self.assertIn("8/52", line)
        self.assertIn("8 validated", line)
        self.assertNotIn("52/52", line)

    def test_full_coverage_only_when_covered_and_validated(self):
        self.assertIn("52/52", hc.readiness_line(52, 52, 52))
        self.assertNotIn("52/52 heroes covered and validated",
                         hc.readiness_line(52, 40, 52))

    def test_the_repository_does_not_claim_full_coverage(self):
        """A guard against exactly the claim this project must not make.

        If a future change makes every layout report 52/52, this test should
        fail and force whoever made it to prove the coverage rather than
        adjust the assertion.
        """
        import db
        con = db.connect()
        try:
            db.init_schema(con)
            report = hc.all_layouts(con)
        finally:
            con.close()
        for layout in report["layouts"]:
            if layout["validated"] == layout["rosterSize"]:
                self.fail(
                    f"{layout['layoutId']} claims every hero validated — if "
                    f"that is genuinely true, this guard needs a deliberate "
                    f"update along with the evidence that earned it")

    def test_a_hero_without_provenance_is_unproven_not_broken(self):
        import db
        con = db.connect()
        try:
            db.init_schema(con)
            report = hc.layout_coverage(con, "owcs_8c105lnzlam")
        finally:
            con.close()
        covered = [h for h in report["heroes"] if h["templates"]]
        self.assertTrue(covered)
        self.assertTrue(all(h["state"] in (hc.UNPROVEN, hc.READY)
                            for h in covered),
                        "a legacy unprovenanced template was called BLOCKED, "
                        "which would push an operator to delete it")


# ------------------------------------------- the real broadcast evidence
@unittest.skipUnless(HAS_NEPAL, "committed Nepal ingest evidence not present")
class TestAgainstRealFootage(unittest.TestCase):
    """The claims in HANDOFF.md, re-derived from the committed evidence.

    These are slow-ish (a few thousand template matches) but they are the
    only tests here that can catch a regression in the thing that actually
    matters: whether the shipped package still reads a real broadcast.
    """

    @classmethod
    def setUpClass(cls):
        cls.manifest = te.build_from_report(
            NEPAL, layout_id="owcs_jksix_qwc")
        with open(os.path.join(ROOT, "layouts", "owcs_jksix_qwc.json"),
                  encoding="utf-8") as f:
            cls.layout = json.load(f)
        cls.tdir = os.path.join(ROOT, "templates", "owcs_jksix_qwc")

    def test_the_shipped_package_is_fully_provenanced(self):
        import template_bootstrap as tb
        prov = tb.load_provenance(self.tdir) or {}
        traced = {e["file"] for e in prov.get("entries", [])}
        on_disk = {f for f in os.listdir(self.tdir)
                   if f.endswith(".png") and not f.startswith("_")}
        self.assertEqual(on_disk - traced, set(),
                         "a shipped template has no recorded source frame")

    def test_every_shipped_hero_validates_on_held_out_frames(self):
        report = tv.validate(self.tdir, self.manifest, min_gap=60.0,
                             layout=self.layout, limit_per_hero=90)
        self.assertTrue(report["heroes"], "no heroes were scored")
        for hero_id, entry in sorted(report["heroes"].items()):
            with self.subTest(hero=hero_id):
                self.assertEqual(entry["wrong"], 0,
                                 f"{hero_id}: {entry['confusedWith']}")
                self.assertEqual(entry["verdict"], tv.VALIDATED, entry["why"])

    def test_removing_a_hero_makes_its_portraits_unknown_not_another_hero(self):
        """Leave-one-out: the honest test of partial coverage.

        A package covering 8 of 52 heroes shows the detector 44 heroes it
        cannot know, on every frame. Before the measured thresholds landed,
        34% of those came back as a confident (wrong) hero.
        """
        report = tv.leave_one_out(self.tdir, self.manifest, min_gap=60.0,
                                  layout=self.layout, limit_per_hero=60)
        self.assertGreater(report["trials"], 100)
        self.assertLess(report["rate"], 0.10,
                        f"unknown portraits are being named: {report['note']}")

    def test_the_portrait_roi_is_rediscoverable_from_the_footage(self):
        crops = proi.load_crops(os.path.join(NEPAL, "evidence"), limit=200)
        result = proi.discover(crops)
        self.assertIsNotNone(result["roi"], result["reason"])
        self.assertAlmostEqual(result["roi"][3],
                               self.layout["portrait_roi"][3], places=2,
                               msg="the layout's portrait_roi no longer "
                                   "matches what the footage says it is")


if __name__ == "__main__":
    unittest.main(verbosity=2)
