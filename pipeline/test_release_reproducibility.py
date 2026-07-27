#!/usr/bin/env python3
"""
test_release_reproducibility.py — Phase 8 release reliability.

Proves that a FRESH ARCHIVE of this repo, extracted into a clean directory
with a different absolute path, still works — the property that actually
matters for handing the project to someone else, and the one that quietly
breaks whenever an absolute path, a hard-coded drive letter, or an
uncommitted-but-required file creeps in.

What runs here (all offline, no network):

  * `git archive` a fresh tarball of HEAD's tracked files, extract it into a
    temporary directory whose path differs from this checkout's;
  * run the packaging gate (`check_packaging.py --root <extraction>`) against
    the extraction, so every layout's templates + markers, the milestone DB,
    the production export and its evidence paths are verified THERE;
  * verify no absolute path or drive letter is baked into any committed
    layout, export, or config;
  * verify the worker diagnostics (`worker-doctor`) run and report honestly
    from the extraction;
  * verify the workflows that write the same generated files share ONE
    concurrency group, and that no placeholder source can be committed;
  * RE-RUN a known detection from the clean extraction and confirm it
    reproduces the committed milestone bit-for-bit in the facts that matter
    (hero stints, confirmed swaps).

The heavy checks are skipped with an explicit reason when `git`/`cv2` are
unavailable, so a minimal runner still passes rather than lying.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)

HAVE_GIT = bool(shutil.which("git")) and os.path.isdir(os.path.join(REPO, ".git"))
try:
    import cv2  # noqa: F401
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

# Files a release archive MUST carry for the milestone to render and for a
# detection re-run to be possible at all.
REQUIRED_IN_ARCHIVE = (
    "data/owcs.sqlite",
    "assets/data/public_data.v1.js",
    "assets/data/public_fixture.v1.js",
    "layouts/owcs_jksix_qwc.json",
    "templates/owcs_jksix_qwc",
    "pipeline/check_packaging.py",
    "pipeline/ingest_map.py",
    "pipeline/automation/cli.py",
    "pipeline/automation/schema.sql",
    "pipeline/schema.sql",
    "reports/ingest/qad-twis-nepal/report.html",
    "config/broadcast_channels.json",
    "requirements.txt",
)

# Files that must NEVER be in a release archive.
FORBIDDEN_PATTERNS = (
    re.compile(r"\.mp4$"), re.compile(r"\.mkv$"), re.compile(r"\.webm$"),
    re.compile(r"\.part$"),
    re.compile(r"^data/worker/"),
    re.compile(r"^data/automation\.sqlite"),
    re.compile(r"^data/raw/"),
    re.compile(r"^data/asset_staging/"),
    re.compile(r"\.env$"), re.compile(r"\.pem$"), re.compile(r"\.key$"),
    re.compile(r"^credentials"), re.compile(r"^secrets"),
    re.compile(r"_candidates/"),
)

_ARCHIVE_CACHE: dict[str, str] = {}


def build_archive(dest_dir: str) -> str:
    """`git archive HEAD` — exactly the tracked set a release would ship."""
    tar_path = os.path.join(dest_dir, "release.tar")
    subprocess.run(["git", "archive", "--format=tar", "-o", tar_path, "HEAD"],
                   cwd=REPO, check=True, capture_output=True)
    return tar_path


def extract_archive(tar_path: str, dest: str) -> str:
    root = os.path.join(dest, "owcs-extracted")
    os.makedirs(root, exist_ok=True)
    with tarfile.open(tar_path) as tf:
        # `filter='data'` refuses absolute paths and links escaping the
        # destination — the same protection a release consumer needs.
        try:
            tf.extractall(root, filter="data")
        except TypeError:              # Python < 3.12
            tf.extractall(root)
    return root


class ArchiveTestCase(unittest.TestCase):
    """One archive + extraction shared across this class's tests: building it
    costs a full `git archive`, and every test wants the same tree."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_GIT:
            raise unittest.SkipTest("needs git and a real .git directory")
        cls._tmp = tempfile.TemporaryDirectory(prefix="owcs_release_")
        cls.tar = build_archive(cls._tmp.name)
        cls.root = extract_archive(cls.tar, cls._tmp.name)
        with tarfile.open(cls.tar) as tf:
            cls.names = tf.getnames()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()


class TestArchiveContents(ArchiveTestCase):
    def test_the_extraction_path_differs_from_this_checkout(self):
        """If it didn't, a baked-in absolute path would pass by accident."""
        self.assertNotEqual(os.path.realpath(self.root),
                            os.path.realpath(REPO))

    def test_every_required_file_is_in_the_archive(self):
        for rel in REQUIRED_IN_ARCHIVE:
            path = os.path.join(self.root, rel)
            self.assertTrue(os.path.exists(path),
                            f"{rel} missing from a fresh archive")

    def test_no_media_database_sidecar_or_secret_is_in_the_archive(self):
        offenders = [n for n in self.names
                     for pat in FORBIDDEN_PATTERNS if pat.search(n)]
        self.assertEqual(offenders, [],
                         f"forbidden file(s) tracked in git: {offenders}")

    def test_the_committed_content_db_is_tracked_but_its_sidecars_are_not(self):
        self.assertIn("data/owcs.sqlite", self.names)
        for sidecar in ("data/owcs.sqlite-wal", "data/owcs.sqlite-shm"):
            self.assertNotIn(sidecar, self.names)

    def test_the_archive_carries_the_per_package_template_set(self):
        tdir = os.path.join(self.root, "templates", "owcs_jksix_qwc")
        pngs = [f for f in os.listdir(tdir) if f.endswith(".png")]
        self.assertGreater(len(pngs), 0,
                           "the verified package's templates must ship")


class TestPortablePaths(ArchiveTestCase):
    """No committed artifact may bake in a machine-specific path."""

    ABSOLUTE_PATTERNS = (
        # A Windows drive letter: exactly ONE letter followed by ':/' or ':\'.
        # The lookbehind is what keeps 'https://' out — there the char before
        # the ':' is 'p', part of a longer word, so it is not a drive letter.
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
        re.compile(r"/home/[a-zA-Z0-9_.-]+/"),  # a developer's home dir
        re.compile(r"/Users/[a-zA-Z0-9_.-]+/"),
        re.compile(r"(?<!\\)\\\\[A-Za-z0-9_.-]+\\"),   # UNC share
    )

    def _scan(self, rel: str) -> list[str]:
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        hits = []
        for pat in self.ABSOLUTE_PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{rel}: {m.group(0)!r}")
        return hits

    def test_no_layout_bakes_in_an_absolute_path(self):
        lay_dir = os.path.join(self.root, "layouts")
        hits = []
        for fn in sorted(os.listdir(lay_dir)):
            if fn.endswith(".json"):
                hits += self._scan(os.path.join("layouts", fn))
        self.assertEqual(hits, [], hits)

    def test_no_committed_data_file_bakes_in_an_absolute_path(self):
        hits = []
        for rel in ("assets/data/public_data.v1.js",
                    "assets/data/public_fixture.v1.js",
                    "assets/data/team_coverage.v1.json",
                    "config/automation.yml",
                    "config/broadcast_channels.json",
                    "data/sources/video_sources.json"):
            hits += self._scan(rel)
        self.assertEqual(hits, [], hits)

    def test_layout_template_and_marker_paths_are_repo_relative(self):
        lay_dir = os.path.join(self.root, "layouts")
        for fn in sorted(os.listdir(lay_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(lay_dir, fn), encoding="utf-8") as f:
                lay = json.load(f)
            paths = [lay.get("templates_dir")]
            for key in ("anchor", "replay"):
                cfg = lay.get(key)
                if isinstance(cfg, dict):
                    paths.append(cfg.get("template"))
            for marker in (lay.get("reject") or []):
                paths.append(marker.get("template"))
            for p in [p for p in paths if p]:
                self.assertFalse(os.path.isabs(p), f"{fn}: {p} is absolute")
                self.assertNotIn("\\", p, f"{fn}: {p} uses backslashes")

    def test_forward_slashes_in_every_exported_evidence_path(self):
        """A stored Windows backslash silently fails to resolve anywhere the
        site joins paths with '/'."""
        rel = os.path.join(self.root, "assets", "data", "public_data.v1.js")
        with open(rel, encoding="utf-8") as f:
            src = f.read()
        body = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        body = body[body.index("{"):body.rstrip().rstrip(";").rindex("}") + 1]
        data = json.loads(body)
        for run in data.get("captureRuns", []):
            for frame in run.get("frames", []):
                self.assertNotIn("\\", frame.get("file") or "")
            for crop in run.get("crops", []):
                self.assertNotIn("\\", crop or "")


class TestPackagingGateOnTheExtraction(ArchiveTestCase):
    def test_the_packaging_gate_passes_against_the_extraction(self):
        res = subprocess.run(
            [sys.executable, os.path.join(self.root, "pipeline",
                                          "check_packaging.py"),
             "--root", self.root],
            capture_output=True, text=True)
        self.assertEqual(res.returncode, 0,
                         f"packaging failed on a fresh extraction:\n"
                         f"{res.stdout[-3000:]}")
        self.assertIn("PACKAGING OK", res.stdout)

    def test_every_layouts_templates_and_markers_exist_in_the_extraction(self):
        """The specific guarantee Phase 8 asks for, asserted directly rather
        than only via the gate's exit code."""
        lay_dir = os.path.join(self.root, "layouts")
        checked = 0
        for fn in sorted(os.listdir(lay_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(lay_dir, fn), encoding="utf-8") as f:
                lay = json.load(f)
            tdir = lay.get("templates_dir")
            if tdir:
                full = os.path.join(self.root, tdir)
                self.assertTrue(os.path.isdir(full), f"{fn}: {tdir} missing")
                self.assertTrue([x for x in os.listdir(full)
                                 if x.endswith(".png") and not x.startswith("_")],
                                f"{fn}: {tdir} has no templates")
                checked += 1
            for marker in (lay.get("reject") or []):
                tpl = marker.get("template")
                if tpl:
                    self.assertTrue(os.path.exists(os.path.join(self.root, tpl)),
                                    f"{fn}: reject asset {tpl} missing")
        self.assertGreater(checked, 0, "expected at least one real package")

    def test_worker_diagnostics_run_from_the_extraction(self):
        res = subprocess.run(
            [sys.executable, os.path.join(self.root, "pipeline", "automation",
                                          "cli.py"),
             "--db", os.path.join(self.root, "data", "automation.sqlite"),
             "worker-doctor", "--json"],
            capture_output=True, text=True, cwd=self.root)
        # Exit code reflects READINESS (a missing yt-dlp is a legitimate
        # not-ready), so the assertion is on the report, not the status.
        report = json.loads(res.stdout)
        for key in ("python", "repoDependencies", "tools", "disk",
                    "workerCacheWritable", "artifactDirWritable",
                    "apiKeysPresent", "ok"):
            self.assertIn(key, report, key)
        self.assertNotIn("owcs.sqlite", json.dumps(report["apiKeysPresent"]))
        # Presence only — a doctor report must never carry a key's value.
        for present in report["apiKeysPresent"].values():
            self.assertIsInstance(present, bool)

    def test_the_cli_help_works_from_the_extraction(self):
        res = subprocess.run(
            [sys.executable, os.path.join(self.root, "pipeline", "automation",
                                          "cli.py"), "--help"],
            capture_output=True, text=True, cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        for cmd in ("ingest-link", "link-status", "approve-source",
                    "resolve-layout", "approve-layout", "propose-identity",
                    "accept-proposed", "intake-export"):
            self.assertIn(cmd, res.stdout, cmd)

    def test_intake_link_parses_a_url_from_the_extraction(self):
        """The headline operator command must work in a clean extraction with
        no network: --no-metadata records the link and blocks approval."""
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run(
                [sys.executable, os.path.join(self.root, "pipeline",
                                              "automation", "cli.py"),
                 "--db", os.path.join(d, "automation.sqlite"),
                 "ingest-link", "--url",
                 "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                 "--no-metadata", "--json"],
                capture_output=True, text=True, cwd=self.root)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertEqual(out["videoId"], "dQw4w9WgXcQ")
            self.assertTrue(out["created"])
            self.assertEqual(out["source"]["state"], "pending-approval")


@unittest.skipUnless(HAVE_CV2, "needs opencv to re-run a detection")
class TestKnownDetectionReproducesFromTheExtraction(ArchiveTestCase):
    """The strongest reproducibility claim available offline: re-run the
    committed milestone's detection FROM THE EXTRACTION and confirm the facts
    match. The full VOD is (correctly) not in the archive, so this runs the
    detector's own regression path — the committed frames + templates + layout
    — which is exactly the part a clean extraction must be able to reproduce.
    """

    def test_the_detection_regression_suite_passes_in_the_extraction(self):
        script = os.path.join(self.root, "pipeline",
                              "test_detection_regression.py")
        if not os.path.exists(script):
            self.skipTest("detection regression suite not in the archive")
        res = subprocess.run([sys.executable, script],
                             capture_output=True, text=True, cwd=self.root)
        self.assertEqual(res.returncode, 0,
                         f"a known detection did not reproduce from a clean "
                         f"extraction:\n{(res.stdout + res.stderr)[-4000:]}")

    def test_the_milestone_detection_facts_are_present_in_the_extracted_db(self):
        import sqlite3
        con = sqlite3.connect(os.path.join(self.root, "data", "owcs.sqlite"))
        con.row_factory = sqlite3.Row
        try:
            stints = con.execute(
                "SELECT COUNT(*) FROM hero_stints WHERE ingest_id='qad-twis-nepal'"
            ).fetchone()[0]
            swaps = con.execute(
                """SELECT from_hero, to_hero, offset_seconds FROM hero_swaps
                   WHERE ingest_id='qad-twis-nepal' AND status='confirmed'
                   ORDER BY offset_seconds""").fetchall()
        finally:
            con.close()
        self.assertGreaterEqual(stints, 10)
        self.assertEqual(len(swaps), 2)
        for s in swaps:
            self.assertEqual((s["from_hero"], s["to_hero"]), ("juno", "lucio"))


class TestWorkflowReliability(unittest.TestCase):
    """Static checks on the workflow files — no Actions run needed."""

    WORKFLOWS = os.path.join(REPO, ".github", "workflows")
    # Generated files more than one workflow writes.
    SHARED_ARTIFACTS = ("data/owcs.sqlite", "assets/js/data.js",
                        "assets/data/public_data.v1.js")

    def _read(self, name: str) -> str:
        with open(os.path.join(self.WORKFLOWS, name), encoding="utf-8") as f:
            return f.read()

    def _concurrency_group(self, text: str) -> str | None:
        m = re.search(r"^concurrency:\s*\n\s*group:\s*(\S+)", text, re.M)
        return m.group(1) if m else None

    def test_every_workflow_declares_a_concurrency_policy(self):
        for name in sorted(os.listdir(self.WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            self.assertIsNotNone(self._concurrency_group(self._read(name)),
                                 f"{name} has no concurrency group")

    def test_workflows_writing_the_same_generated_files_share_one_group(self):
        groups: dict[str, list[str]] = {}
        for name in sorted(os.listdir(self.WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            text = self._read(name)
            # A workflow "writes" a shared artifact when it git-adds it.
            adds = re.findall(r"git add ([^\n]+)", text)
            if not any(art in " ".join(adds) for art in self.SHARED_ARTIFACTS):
                continue
            groups.setdefault(self._concurrency_group(text), []).append(name)
        self.assertTrue(groups, "expected workflows that commit generated data")
        self.assertEqual(
            len(groups), 1,
            f"workflows mutating the same generated files must share ONE "
            f"concurrency group, found: {groups}")

    def test_no_workflow_stages_media_or_a_runtime_database(self):
        for name in sorted(os.listdir(self.WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            text = self._read(name)
            for add in re.findall(r"git add ([^\n]+)", text):
                for forbidden in ("data/worker", "data/automation.sqlite",
                                  ".mp4", ".mkv", "data/raw", ".env"):
                    self.assertNotIn(forbidden, add,
                                     f"{name} stages {forbidden}")

    def test_workflow_layout_defaults_point_at_a_calibrated_package(self):
        """A dispatch default of the placeholder starter layout produced
        confident-looking garbage instead of an honest refusal."""
        for name in sorted(os.listdir(self.WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            text = self._read(name)
            for ref in re.findall(r"layouts/([A-Za-z0-9_\-]+)\.json", text):
                path = os.path.join(REPO, "layouts", f"{ref}.json")
                self.assertTrue(os.path.exists(path),
                                f"{name} references a missing layout {ref}")
                with open(path, encoding="utf-8") as f:
                    lay = json.load(f)
                probe = lay.get("hud_probe") or {}
                self.assertTrue(probe.get("chips_a") and probe.get("chips_b"),
                                f"{name} defaults to {ref}, which has no "
                                f"calibrated hud_probe — it cannot detect")


class TestNoPlaceholderSources(unittest.TestCase):
    def test_no_committed_video_source_is_a_placeholder(self):
        path = os.path.join(REPO, "data", "sources", "video_sources.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for src in data.get("sources", []):
            blob = json.dumps(src)
            for token in ("REPLACE_WITH", "REPLACE_ME", "TODO", "<your"):
                self.assertNotIn(token, blob,
                                 f"placeholder source committed: {src}")

    def test_no_committed_faceit_competition_is_enabled_without_a_real_id(self):
        path = os.path.join(REPO, "config", "faceit_competitions.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for comp in data.get("competitions", []):
            if comp.get("enabled"):
                cid = comp.get("championshipId") or ""
                self.assertTrue(cid, f"{comp.get('id')} enabled with no id")
                self.assertNotIn("REPLACE", cid.upper())

    def test_no_committed_channel_is_enabled_without_a_verified_id(self):
        path = os.path.join(REPO, "config", "broadcast_channels.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for ch in data.get("channels", []):
            if ch.get("enabled"):
                self.assertTrue(ch.get("channelId"),
                                f"{ch.get('id')} enabled with no channelId")


class TestGitignoreProtections(unittest.TestCase):
    def test_gitignore_covers_media_runtime_state_and_secrets(self):
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
            text = f.read()
        for needed in ("*.mp4", "data/worker/", "data/automation.sqlite",
                       "data/raw/", "*.env", "*.key", "*.pem",
                       "credentials*.json", "secrets*.json", "reports/*"):
            self.assertIn(needed, text, f".gitignore is missing {needed}")

    def test_generated_evidence_directories_are_ignored_by_default(self):
        """reports/* is ignored with explicit allow-list exceptions, so the
        new layout/identity report dirs cannot be committed by accident."""
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        self.assertIn("reports/*", lines)
        allowed = [ln for ln in lines if ln.startswith("!reports/")]
        for entry in allowed:
            self.assertNotIn("layout", entry)
            self.assertNotIn("identity", entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
