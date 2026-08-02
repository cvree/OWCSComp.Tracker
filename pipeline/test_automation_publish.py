#!/usr/bin/env python3
"""
test_automation_publish.py — Phase I human-gated promotion/export/
publication orchestrator. Git operations run against an ISOLATED temporary
git repo (never the real working tree — publish.py's repo_root/push
parameters exist specifically so tests never touch this session's own
branches). export_data.py/validate_data.py/check_packaging.py are stubbed
via an injected runner so these tests stay fast and offline; the real
export/validate/packaging scripts have their own dedicated test suites.
Run: python3 pipeline/test_automation_publish.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)
from automation import job_store as js  # noqa: E402
from automation import models  # noqa: E402
from automation import publish as pub  # noqa: E402
from automation import segmentation as seg  # noqa: E402
from automation import state_machine as sm  # noqa: E402


class FakeRunner:
    """Real `git` (against the isolated repo tests point at); canned results
    for the python script invocations so tests never run the heavy real
    export/validate/packaging pipeline."""

    def __init__(self, script_results: dict | None = None):
        self.script_results = script_results or {}

    def run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, **kwargs)
        script = os.path.basename(cmd[1])
        rc, out, err = self.script_results.get(script, (0, "ok", ""))
        return subprocess.CompletedProcess(cmd, rc, out, err)


def _init_repo_root(tmp: str) -> str:
    """A tiny standalone git repo shaped like the real one just enough for
    publish.py's file paths (assets/data/public_data.v1.js) to resolve."""
    root = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(root, "assets", "data"), exist_ok=True)
    export_path = os.path.join(root, "assets", "data", "public_data.v1.js")
    with open(export_path, "w", encoding="utf-8") as f:
        f.write("window.OWCS_PUBLIC = {version: 0};\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


class PublishTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = _init_repo_root(self.tmp.name)
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.con = self.store.con
        # Content-DB-shaped tables so check_preconditions' match/team lookups
        # work without importing the full pipeline/schema.sql.
        self.con.execute("CREATE TABLE matches (id TEXT PRIMARY KEY)")
        self.con.execute("CREATE TABLE teams (id TEXT PRIMARY KEY)")
        self.con.execute("INSERT INTO matches (id) VALUES ('m-test')")
        self.con.execute("INSERT INTO teams (id) VALUES ('qad'), ('twis')")
        self.con.commit()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make_approved_job_and_segment(self, *, committed=True):
        k = models.process_key("vid1", "v1")
        self.store.enqueue(models.KIND_PROCESS, k, payload={"videoId": "vid1"},
                          state=sm.PROCESSING)
        self.store.transition(k, sm.NEEDS_REVIEW)
        self.store.transition(k, sm.APPROVED)
        if committed:
            self.store.update_payload(k, {"detection": {"db": {"stints": 5}}})
        job = self.store.get(k)

        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 300.0,
                                       "confidence": 0.9, "signals": {}}])
        seg.approve_segment(self.con, sid, map_order=1, map_name="Nepal",
                           map_mode="Control", team_a="qad", team_b="twis",
                           side_assignment="team_a_left",
                           layout_id=os.path.join(HERE, "..", "layouts",
                                                  "owcs_jksix_qwc.json"))
        self.con.execute(
            "UPDATE map_segments SET extracted_path=?, extracted_hash=? WHERE id=?",
            ("data/worker/jobs/x/media/clip.mp4", "a" * 64, sid))
        self.con.commit()
        return job, seg.get_segment(self.con, sid)


class TestPreconditions(PublishTestBase):
    def test_incomplete_review_refused(self):
        k = models.process_key("vid1", "v1")
        self.store.enqueue(models.KIND_PROCESS, k, state=sm.PROCESSING)
        job = self.store.get(k)
        with self.assertRaises(pub.PublishRefusal) as ctx:
            pub.check_preconditions(self.con, job, {})
        self.assertEqual(ctx.exception.code, "review_incomplete")

    def test_match_identity_unresolved(self):
        job, segment = self.make_approved_job_and_segment()
        segment = dict(segment, candidate_match_id="m-does-not-exist")
        with self.assertRaises(pub.PublishRefusal) as ctx:
            pub.check_preconditions(self.con, job, segment)
        self.assertEqual(ctx.exception.code, "match_identity_unresolved")

    def test_teams_unresolved(self):
        job, segment = self.make_approved_job_and_segment()
        segment = dict(segment, team_b="ghost-team")
        with self.assertRaises(pub.PublishRefusal) as ctx:
            pub.check_preconditions(self.con, job, segment)
        self.assertEqual(ctx.exception.code, "teams_unresolved")

    def test_layout_mismatch_refused(self):
        job, segment = self.make_approved_job_and_segment()
        segment = dict(segment, layout_id="layouts/does-not-exist.json")
        with self.assertRaises(pub.PublishRefusal) as ctx:
            pub.check_preconditions(self.con, job, segment)
        self.assertEqual(ctx.exception.code, "layout_mismatch")

    def test_evidence_missing_without_committed_detection(self):
        job, segment = self.make_approved_job_and_segment(committed=False)
        with self.assertRaises(pub.PublishRefusal) as ctx:
            pub.check_preconditions(self.con, job, segment)
        self.assertEqual(ctx.exception.code, "evidence_missing")

    def test_preconditions_pass_for_fully_resolved_job(self):
        job, segment = self.make_approved_job_and_segment()
        pub.check_preconditions(self.con, job, segment)  # must not raise


class TestSecretsAndMedia(unittest.TestCase):
    def test_scan_for_secrets_detects_common_patterns(self):
        self.assertTrue(pub.scan_for_secrets("AKIAABCDEFGHIJKLMNOP"))
        self.assertTrue(pub.scan_for_secrets("ghp_" + "a" * 36))
        self.assertTrue(pub.scan_for_secrets('api_key: "sk_live_1234567890abcd"'))
        self.assertFalse(pub.scan_for_secrets("assets/data/public_data.v1.js"))


class TestDryRunPublish(PublishTestBase):
    def test_dry_run_never_touches_git_or_job_state(self):
        job, segment = self.make_approved_job_and_segment()
        runner = FakeRunner()
        result = pub.publish_job(self.store, self.con, job, segment,
                                 dry_run=True, repo_root=self.repo_root,
                                 runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)
        branches = subprocess.run(["git", "branch"], cwd=self.repo_root,
                                  capture_output=True, text=True,
                                      **proc_text.PIPE_TEXT).stdout
        self.assertNotIn("data/publish-", branches)

    def test_refusal_recorded_on_job(self):
        k = models.process_key("vid1", "v1")
        self.store.enqueue(models.KIND_PROCESS, k, state=sm.PROCESSING)
        job = self.store.get(k)
        result = pub.publish_job(self.store, self.con, job, {}, dry_run=True,
                                 repo_root=self.repo_root, runner=FakeRunner())
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "review_incomplete")
        self.assertEqual(self.store.get(k).last_error_code, "review_incomplete")


class TestRealPublish(PublishTestBase):
    def test_publish_creates_branch_commit_and_transitions_job(self):
        job, segment = self.make_approved_job_and_segment()
        # Simulate the export step actually changing the export file —
        # publish_job's stubbed regenerate_and_validate_export won't touch
        # it, so mutate it directly the way a real export run would.
        export_path = os.path.join(self.repo_root, "assets", "data", "public_data.v1.js")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("window.OWCS_PUBLIC = {version: 1, match: 'm-test'};\n")

        result = pub.publish_job(self.store, self.con, job, segment,
                                 dry_run=False, repo_root=self.repo_root,
                                 push=False, runner=FakeRunner())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["branch"].startswith("data/publish-"))
        self.assertEqual(self.store.get(job.job_key).state, sm.PUBLISHED)
        row = self.con.execute("SELECT * FROM publication_runs").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "PUSHED")

    def test_no_media_ever_staged(self):
        job, segment = self.make_approved_job_and_segment()
        export_path = os.path.join(self.repo_root, "assets", "data", "public_data.v1.js")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("window.OWCS_PUBLIC = {version: 2};\n")
        # Sneak a media file into the working tree AND try to have it staged
        # by publish_job's own commit step — it must never be included.
        media_path = os.path.join(self.repo_root, "work", "clip.mp4")
        os.makedirs(os.path.dirname(media_path), exist_ok=True)
        with open(media_path, "wb") as f:
            f.write(b"\x00" * 16)
        subprocess.run(["git", "add", media_path], cwd=self.repo_root, check=True)
        # publish_job only ever adds the export files it names explicitly —
        # confirm staged_media_files would catch it if it somehow got in.
        media = pub.staged_media_files(repo_root=self.repo_root)
        self.assertIn("work/clip.mp4", media)
        subprocess.run(["git", "reset"], cwd=self.repo_root, check=True)

        result = pub.publish_job(self.store, self.con, job, segment,
                                 dry_run=False, repo_root=self.repo_root,
                                 push=False, runner=FakeRunner())
        self.assertTrue(result["ok"], result)
        show = subprocess.run(["git", "show", "--name-only", "--pretty=format:",
                              result["commitSha"]], cwd=self.repo_root,
                              capture_output=True, text=True,
                                  **proc_text.PIPE_TEXT).stdout
        self.assertNotIn("clip.mp4", show)

    def test_no_change_refuses_empty_commit(self):
        job, segment = self.make_approved_job_and_segment()
        # Export file left byte-identical to what's already committed.
        result = pub.publish_job(self.store, self.con, job, segment,
                                 dry_run=False, repo_root=self.repo_root,
                                 push=False, runner=FakeRunner())
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "export_validation_failed")
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)

    def test_republish_of_already_published_job_is_refused(self):
        job, segment = self.make_approved_job_and_segment()
        export_path = os.path.join(self.repo_root, "assets", "data", "public_data.v1.js")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("window.OWCS_PUBLIC = {version: 3};\n")
        first = pub.publish_job(self.store, self.con, job, segment,
                                dry_run=False, repo_root=self.repo_root,
                                push=False, runner=FakeRunner())
        self.assertTrue(first["ok"], first)
        # Job is now PUBLISHED; re-running publish on the same job must
        # refuse rather than silently duplicating a publication.
        job2 = self.store.get(job.job_key)
        second = pub.publish_job(self.store, self.con, job2, segment,
                                 dry_run=False, repo_root=self.repo_root,
                                 push=False, runner=FakeRunner())
        self.assertFalse(second["ok"])
        self.assertEqual(second["code"], "review_incomplete")

    def test_failing_offline_test_refuses_publication(self):
        job, segment = self.make_approved_job_and_segment()
        export_path = os.path.join(self.repo_root, "assets", "data", "public_data.v1.js")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("window.OWCS_PUBLIC = {version: 4};\n")
        runner = FakeRunner({"export_data.py": (0, "ok", ""),
                            "validate_data.py": (0, "ok", ""),
                            "check_packaging.py": (0, "ok", ""),
                            "test_fake_failing.py": (1, "", "no such test")})
        result = pub.publish_job(self.store, self.con, job, segment,
                                 dry_run=False, repo_root=self.repo_root,
                                 push=False, runner=runner,
                                 test_files=["pipeline/test_fake_failing.py"])
        # the fake test file doesn't exist -> non-zero exit -> refused
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "tests_failed")
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
