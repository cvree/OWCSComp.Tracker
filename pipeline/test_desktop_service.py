#!/usr/bin/env python3
"""
test_desktop_service.py — the background service, storage and backups.

These are the three subsystems that run unattended, so the failure modes that
matter are the ones nobody is watching:

  * a job whose stage raises must not kill the service, and must be recorded
    as a failed attempt so the retry policy applies;
  * a second copy of the service must refuse to start rather than race the
    first for jobs;
  * a full disk must pause processing, not corrupt a download;
  * the cleanup must never, under any budget pressure, delete evidence;
  * a publish must be atomic, and a rollback must refuse a corrupted backup.

Every collaborator is injected, so all of this is exercised without video,
network, or a real queue.

Run: python3 pipeline/test_desktop_service.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

from owcs_desktop import backup, paths, storage, supervisor, tray  # noqa: E402
from owcs_desktop.settings import Settings  # noqa: E402


class TempHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="owcs-test-svc-")
        self._old = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self._tmp.name
        paths.ensure_layout()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old
        self._tmp.cleanup()


# ------------------------------------------------------------ fake queue
class FakeJob:
    def __init__(self, key: str, state: str = "ARCHIVED"):
        self.job_key = key
        self.state = state


class FakeStore:
    """The smallest thing that behaves like JobStore for the loop."""

    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.claimed: list[str] = []
        self.attempts: list[tuple] = []
        self.closed = 0
        self.con = None

    def claim_next(self, kinds, worker_id, now=None):
        if not self.jobs:
            return None
        job = self.jobs.pop(0)
        self.claimed.append(job.job_key)
        return job

    def record_attempt(self, job_key, ok=True, detail=""):
        self.attempts.append((job_key, ok, detail))

    def close(self):
        self.closed += 1


# ------------------------------------------------------------- supervisor
class TestSupervisorLoop(TempHome):
    def build(self, *, jobs=None, advance=None, free_gb=1000.0):
        store = FakeStore(jobs)
        sup = supervisor.Supervisor(
            store_factory=lambda: store,
            advance=advance or (lambda s, k: {"ok": True, "state": "DOWNLOADED"}),
            free_gb=lambda path: free_gb,
            log=lambda m: None)
        return sup, store

    def test_idle_when_there_is_no_work(self):
        sup, _ = self.build()
        self.assertEqual(sup.process_once()["action"], "idle")

    def test_advances_a_job(self):
        sup, store = self.build(jobs=[FakeJob("j1")])
        outcome = sup.process_once()
        self.assertEqual(outcome["action"], "advanced")
        self.assertEqual(store.claimed, ["j1"])
        self.assertEqual(sup.processed, 1)

    def test_a_crashing_stage_does_not_kill_the_service(self):
        """The single most important property: one bad job must not stop the
        queue draining forever."""
        def explode(store, key):
            raise RuntimeError("ffmpeg segfaulted")

        sup, store = self.build(jobs=[FakeJob("bad")], advance=explode)
        outcome = sup.process_once()
        self.assertEqual(outcome["action"], "error")
        self.assertEqual(sup.failures, 1)
        # Recorded as a FAILED attempt, so the store's retry/backoff policy
        # applies rather than the job being silently dropped.
        self.assertEqual(len(store.attempts), 1)
        self.assertEqual(store.attempts[0][0], "bad")
        self.assertFalse(store.attempts[0][1])
        # and the loop keeps going
        self.assertEqual(sup.process_once()["action"], "idle")

    def test_the_loop_survives_every_iteration_failing(self):
        def explode(store, key):
            raise RuntimeError("still broken")

        sup, _ = self.build(jobs=[FakeJob(f"j{i}") for i in range(5)],
                            advance=explode)
        result = sup.run_forever(max_iterations=5, sleep=lambda s: None)
        self.assertEqual(result["iterations"], 5)
        self.assertEqual(result["failures"], 5)

    def test_a_full_disk_pauses_instead_of_processing(self):
        sup, store = self.build(jobs=[FakeJob("j1")], free_gb=0.5)
        outcome = sup.process_once()
        self.assertEqual(outcome["action"], "paused")
        self.assertIn("below", outcome["reason"])
        self.assertEqual(store.claimed, [],
                         "a job was claimed with no disk space left")

    def test_an_unopenable_database_is_reported_not_raised(self):
        def boom():
            raise OSError("database is locked")

        sup = supervisor.Supervisor(store_factory=boom,
                                    free_gb=lambda p: 1000.0,
                                    log=lambda m: None)
        outcome = sup.process_once()
        self.assertEqual(outcome["action"], "error")
        self.assertIn("database", outcome["reason"])

    def test_the_store_is_always_closed(self):
        def explode(store, key):
            raise RuntimeError("boom")

        sup, store = self.build(jobs=[FakeJob("j1")], advance=explode)
        sup.process_once()
        self.assertEqual(store.closed, 1, "a crashed stage leaked a connection")

    def test_stop_flag_ends_the_loop(self):
        sup, _ = self.build(jobs=[FakeJob(f"j{i}") for i in range(50)])
        supervisor.request_stop()
        result = sup.run_forever(max_iterations=50, sleep=lambda s: None)
        self.assertEqual(result["iterations"], 0,
                         "the stop request was ignored")
        supervisor.clear_stop()
        self.assertFalse(supervisor.stop_requested())


class TestHeartbeat(TempHome):
    def test_written_and_read_back(self):
        supervisor.write_heartbeat({"processed": 3, "running": True})
        beat = supervisor.read_heartbeat()
        self.assertEqual(beat["processed"], 3)
        self.assertEqual(beat["pid"], os.getpid())
        self.assertFalse(beat["stale"])

    def test_missing_heartbeat_is_none(self):
        self.assertIsNone(supervisor.read_heartbeat())

    def test_an_old_heartbeat_is_stale(self):
        old = (dt.datetime.now(dt.timezone.utc)
               - dt.timedelta(seconds=supervisor.HEARTBEAT_STALE_SECONDS + 60))
        from owcs_desktop.settings import atomic_write_json
        atomic_write_json(paths.heartbeat_file(),
                          {"at": old.isoformat(), "pid": os.getpid()})
        self.assertTrue(supervisor.read_heartbeat()["stale"])

    def test_a_dead_process_is_stale_even_with_a_fresh_timestamp(self):
        """A service killed with -9 leaves its last heartbeat behind. Trusting
        the timestamp alone would show 'running' for two minutes after a
        crash, exactly when the user is looking."""
        from owcs_desktop.settings import atomic_write_json
        atomic_write_json(paths.heartbeat_file(), {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pid": 999_999})
        self.assertTrue(supervisor.read_heartbeat()["stale"])

    def test_a_corrupt_heartbeat_is_none_not_a_crash(self):
        with open(paths.heartbeat_file(), "w", encoding="utf-8") as f:
            f.write("garbage")
        self.assertIsNone(supervisor.read_heartbeat())


class TestSingleInstance(TempHome):
    def test_the_second_copy_is_refused(self):
        first = supervisor.SingleInstance()
        self.assertTrue(first.acquire())
        second = supervisor.SingleInstance()
        # Simulate a different live process owning it.
        from owcs_desktop.settings import atomic_write_json
        atomic_write_json(second.path, {"pid": os.getppid() or 1})
        self.assertFalse(second.acquire(),
                         "two services could both own the queue")

    def test_a_stale_lock_is_taken_over(self):
        from owcs_desktop.settings import atomic_write_json
        lock = supervisor.SingleInstance()
        atomic_write_json(lock.path, {"pid": 999_999})
        self.assertTrue(lock.acquire(),
                        "a lock left by a crashed service blocked startup "
                        "forever")

    def test_release_removes_only_our_own_lock(self):
        lock = supervisor.SingleInstance()
        lock.acquire()
        lock.release()
        self.assertFalse(os.path.exists(lock.path))


# ---------------------------------------------------------------- storage
class TestStoragePruning(TempHome):
    def media_job(self, key: str, *, size: int = 4096, age_days: float = 0.0):
        directory = os.path.join(paths.sub("media"), key)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "source.mp4")
        with open(path, "wb") as f:
            f.write(b"\0" * size)
        if age_days:
            when = dt.datetime.now().timestamp() - age_days * 86400
            os.utime(path, (when, when))
        return directory

    def test_finished_and_old_media_is_planned_for_removal(self):
        self.media_job("done", age_days=10)
        plan = storage.plan_prune(retention_days=3,
                                  job_states={"done": "PUBLISHED"})
        self.assertEqual([r["jobKey"] for r in plan["remove"]], ["done"])

    def test_an_active_job_is_never_pruned_at_any_age(self):
        self.media_job("live", age_days=400)
        plan = storage.plan_prune(retention_days=1,
                                  job_states={"live": "DOWNLOADING"})
        self.assertEqual(plan["remove"], [],
                         "media for a job still being processed was deleted")

    def test_an_active_job_is_never_pruned_under_budget_pressure(self):
        self.media_job("live", size=50_000, age_days=400)
        plan = storage.plan_prune(retention_days=1, budget_gb=0.0,
                                  job_states={"live": "PROCESSING"})
        self.assertEqual(plan["remove"], [])

    def test_budget_pressure_removes_oldest_finished_first(self):
        self.media_job("old", size=60_000, age_days=2)
        self.media_job("new", size=60_000, age_days=0)
        plan = storage.plan_prune(retention_days=30, budget_gb=0.00005,
                                  job_states={"old": "PUBLISHED",
                                              "new": "PUBLISHED"})
        removed = [r["jobKey"] for r in plan["remove"]]
        self.assertIn("old", removed)
        self.assertEqual(removed[0], "old", "newest media was removed first")

    def test_an_unreadable_queue_deletes_nothing(self):
        """If the job database cannot be read, every job must be assumed
        active. Guessing the other way deletes a download in progress."""
        self.media_job("mystery", age_days=99)
        plan = storage.plan_prune(retention_days=1, job_states={})
        self.assertEqual(plan["remove"], [])

    def test_evidence_directories_are_refused_at_the_point_of_deletion(self):
        """The guard is not only in the planner. A hand-built plan aimed at
        the audit trail must still be refused."""
        for area in ("evidence", "reports", "quarantine", "backups", "db",
                     "logs", "layouts"):
            with self.subTest(area=area):
                plan = {"remove": [{"dir": paths.sub(area), "bytes": 1}]}
                result = storage.apply_prune(plan)
                self.assertEqual(result["removed"], [])
                self.assertEqual(len(result["errors"]), 1)
                self.assertIn("audit trail", result["errors"][0]["error"])
                self.assertTrue(os.path.isdir(paths.sub(area)))

    def test_a_path_outside_media_is_refused(self):
        outside = os.path.join(self._tmp.name, "not-media")
        os.makedirs(outside, exist_ok=True)
        result = storage.apply_prune({"remove": [{"dir": outside, "bytes": 1}]})
        self.assertEqual(result["removed"], [])
        self.assertTrue(os.path.isdir(outside))

    def test_apply_actually_frees_space(self):
        directory = self.media_job("gone", size=8192, age_days=9)
        plan = storage.plan_prune(retention_days=3,
                                  job_states={"gone": "PUBLISHED"})
        result = storage.apply_prune(plan)
        self.assertEqual(result["removed"], [directory])
        self.assertFalse(os.path.exists(directory))
        self.assertGreaterEqual(result["freedBytes"], 8192)

    def test_usage_report_marks_the_protected_areas(self):
        report = storage.usage_report()
        protected = {a["area"] for a in report["areas"] if a["protected"]}
        self.assertEqual(protected, set(storage.PROTECTED_SUBDIRS))


# ---------------------------------------------------------------- backups
class TestBackupAndPublish(TempHome):
    def test_atomic_publish_replaces_whole(self):
        target = os.path.join(self._tmp.name, "public.js")
        first = backup.atomic_publish(target, "window.X = 1;")
        self.assertFalse(first["replaced"])
        second = backup.atomic_publish(target, "window.X = 2;")
        self.assertTrue(second["replaced"])
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "window.X = 2;")

    def test_a_failed_publish_leaves_the_original_intact(self):
        target = os.path.join(self._tmp.name, "public.js")
        backup.atomic_publish(target, "the good version")
        real_replace = os.replace

        def explode(src, dst):
            raise OSError("simulated power cut")

        os.replace = explode
        try:
            with self.assertRaises(OSError):
                backup.atomic_publish(target, "the half-written version")
        finally:
            os.replace = real_replace
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "the good version")
        leftovers = [n for n in os.listdir(self._tmp.name)
                     if n.startswith(".publish-")]
        self.assertEqual(leftovers, [], "a failed publish left scratch files")

    def test_snapshot_records_hashes_and_verifies(self):
        source = os.path.join(self._tmp.name, "thing.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("original")
        snap = backup.create_snapshot(reason="test", files=[("thing.txt", source)])
        self.assertTrue(backup.verify_snapshot(snap["id"])["ok"])

    def test_a_tampered_snapshot_fails_verification_and_is_not_restored(self):
        source = os.path.join(self._tmp.name, "thing.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("original")
        snap = backup.create_snapshot(reason="test",
                                      files=[("thing.txt", source)])
        stored = os.path.join(paths.sub("backups"), snap["id"], "thing.txt")
        with open(stored, "w", encoding="utf-8") as f:
            f.write("corrupted in storage")

        check = backup.verify_snapshot(snap["id"])
        self.assertFalse(check["ok"])
        result = backup.restore_snapshot(snap["id"],
                                         files=[("thing.txt", source)])
        self.assertFalse(result["ok"])
        with open(source, encoding="utf-8") as f:
            self.assertEqual(f.read(), "original",
                             "a corrupted backup was restored over live data")

    def test_restore_round_trip_and_is_itself_reversible(self):
        source = os.path.join(self._tmp.name, "thing.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("version one")
        snap = backup.create_snapshot(reason="v1",
                                      files=[("thing.txt", source)])
        with open(source, "w", encoding="utf-8") as f:
            f.write("version two")

        result = backup.restore_snapshot(snap["id"],
                                         files=[("thing.txt", source)])
        self.assertTrue(result["ok"], result)
        with open(source, encoding="utf-8") as f:
            self.assertEqual(f.read(), "version one")

        # The state before the restore was itself snapshotted.
        pre = result["preRestoreSnapshot"]
        again = backup.restore_snapshot(pre, files=[("thing.txt", source)])
        self.assertTrue(again["ok"])
        with open(source, encoding="utf-8") as f:
            self.assertEqual(f.read(), "version two")

    def test_pruning_keeps_the_newest_and_never_empties(self):
        source = os.path.join(self._tmp.name, "thing.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("x")
        ids = [backup.create_snapshot(reason=f"s{i}", keep=99,
                                      files=[("thing.txt", source)])["id"]
               for i in range(6)]
        backup.prune_snapshots(keep=2)
        remaining = [s["id"] for s in backup.list_snapshots()]
        self.assertEqual(len(remaining), 2)
        self.assertIn(ids[-1], remaining, "the newest backup was pruned")

        backup.prune_snapshots(keep=0)
        self.assertGreaterEqual(len(backup.list_snapshots()), 1,
                                "pruning removed every backup")

    def test_a_sqlite_snapshot_is_a_usable_database(self):
        import sqlite3
        db_path = os.path.join(self._tmp.name, "thing.sqlite")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE t (a INTEGER)")
        con.execute("INSERT INTO t VALUES (42)")
        con.commit()
        try:
            snap = backup.create_snapshot(reason="db",
                                          files=[("thing.sqlite", db_path)])
        finally:
            con.close()
        copied = os.path.join(paths.sub("backups"), snap["id"], "thing.sqlite")
        con2 = sqlite3.connect(copied)
        try:
            self.assertEqual(con2.execute("SELECT a FROM t").fetchone()[0], 42)
        finally:
            con2.close()


# ------------------------------------------------------- child supervision
class FakeProcess:
    def __init__(self, alive: bool = True, returncode: int = 0):
        self._alive = alive
        self.returncode = returncode
        self.pid = 4242
        self.terminated = False

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self._alive = False


class TestChildSupervision(TempHome):
    def test_a_dead_child_is_restarted(self):
        spawned = []

        def spawn(cmd, **kwargs):
            proc = FakeProcess(alive=len(spawned) > 0)   # first one dies
            spawned.append(proc)
            return proc

        child = tray.Child("service", ["x"], spawn=spawn)
        sup = tray.ProcessSupervisor([child], log=lambda m: None,
                                     sleep=lambda s: None)
        sup.start_all()
        events = sup.check_once()
        self.assertEqual([e["action"] for e in events], ["restarted"])
        self.assertEqual(len(spawned), 2)
        self.assertTrue(child.alive())

    def test_it_gives_up_rather_than_restarting_forever(self):
        def spawn(cmd, **kwargs):
            return FakeProcess(alive=False, returncode=1)

        child = tray.Child("service", ["x"], spawn=spawn)
        sup = tray.ProcessSupervisor([child], log=lambda m: None,
                                     sleep=lambda s: None)
        sup.start_all()
        for _ in range(tray.MAX_RESTARTS + 2):
            sup.check_once()
        self.assertTrue(child.gave_up)
        self.assertIn("failed restarts", child.last_error)

    def test_a_child_that_will_not_start_is_reported(self):
        def spawn(cmd, **kwargs):
            raise OSError("no such file")

        child = tray.Child("service", ["missing"], spawn=spawn)
        self.assertFalse(child.start())
        self.assertIn("no such file", child.last_error)

    def test_the_child_environment_carries_per_user_storage(self):
        env = tray.child_environment()
        self.assertEqual(env["OWCS_DB"], paths.content_db())
        self.assertIn(os.path.join(paths.app_root(), "desktop"),
                      env["PYTHONPATH"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
