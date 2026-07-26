"""
publish.py — human-gated promotion, public export, and publication
(Roadmap Phase I).

Reuses the EXISTING promotion/export/CI machinery end-to-end — this module
never hand-writes `public_data.v1.js`, never inserts comp/hero rows itself,
and never bypasses the safety gates already established elsewhere in the
repo. It only orchestrates, in the required order:

    promote approved detection -> regenerate + validate the public export
    -> run the relevant offline tests -> packaging/secret/media checks
    -> a scoped publication commit -> push a branch

Opening the PR, waiting for CI, merging, and confirming the GitHub Pages
deploy stay the repo's existing, already-working, human/CI-supervised git
flow (`docs/HANDOFF-AND-GOALS.md` §5: "Branch → commit → PR → CI green →
merge. Never push to main.") — this module deliberately does not reimplement
that, it produces the one thing that flow needs: a validated, scoped commit
on a fresh branch, then reports the next step.

Default is always a dry run (checks + export regeneration + tests, zero git
writes) — matching "Use explicit dry-run and write modes" and "Never
silently convert uncertainty into production data." Only `dry_run=False`
(the operator's explicit `--publish`) creates the branch/commit/push.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
REPO_ROOT = os.path.dirname(_PIPELINE_DIR)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import db as content_db  # noqa: E402

from . import detection_runner as dr  # noqa: E402
from . import job_store as js  # noqa: E402
from . import models  # noqa: E402
from . import state_machine as sm  # noqa: E402

MEDIA_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".ts", ".m3u8")

# Deliberately conservative patterns — false positives (refuse to publish)
# are always the safe failure mode; a missed secret never is.
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                      # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),            # GitHub tokens
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]


def log(msg: str) -> None:
    print(f"[publish] {msg}", flush=True)


class PublishRefusal(Exception):
    """Raised for every reason the sprint's Phase 6 gate names explicitly.
    Always carries a stable `code` so callers/tests never have to parse
    prose to know why publication was refused."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ------------------------------------------------------------ preconditions
def check_preconditions(con, job: models.Job, segment: dict) -> None:
    if job.state != sm.APPROVED:
        raise PublishRefusal("review_incomplete",
                             f"job {job.job_key} is {job.state}, not APPROVED "
                             f"— human review must be completed first")
    match_id = segment.get("candidate_match_id")
    if not match_id:
        raise PublishRefusal("match_identity_unresolved",
                             "segment has no candidate_match_id")
    if not con.execute("SELECT 1 FROM matches WHERE id=?", (match_id,)).fetchone():
        raise PublishRefusal("match_identity_unresolved",
                             f"match {match_id!r} does not exist in the content DB "
                             f"— a video/job never invents a match")
    for field in ("candidate_map_order", "map_name", "map_mode"):
        if not segment.get(field):
            raise PublishRefusal("map_identity_unresolved",
                                 f"segment is missing {field!r}")
    team_a, team_b = segment.get("team_a"), segment.get("team_b")
    if not team_a or not team_b:
        raise PublishRefusal("teams_unresolved", "segment is missing team_a/team_b")
    for tid in (team_a, team_b):
        if not con.execute("SELECT 1 FROM teams WHERE id=?", (tid,)).fetchone():
            raise PublishRefusal("teams_unresolved",
                                 f"team {tid!r} does not exist in the content DB")
    layout_id = segment.get("layout_id")
    if not layout_id or not os.path.exists(dr.layout_path(layout_id)):
        raise PublishRefusal("layout_mismatch",
                             f"layout {layout_id!r} does not resolve to a real file")
    if not segment.get("extracted_path") or not segment.get("extracted_hash"):
        raise PublishRefusal("evidence_missing",
                             "segment has no extracted clip/hash — nothing was "
                             "actually processed for this map")
    if not job.payload.get("detection", {}).get("db"):
        raise PublishRefusal("evidence_missing",
                             "no committed (write=True) detection result on this "
                             "job — run detection_runner.commit_approved_detection "
                             "before publishing")


# --------------------------------------------------------------- export/tests
def regenerate_and_validate_export(*, repo_root: str = REPO_ROOT,
                                   runner=subprocess) -> dict:
    """`export_data.py --public` then `validate_data.py` — the exact
    established pattern (`cli.py._run_export`), never a hand-patched file."""
    pipeline_dir = os.path.join(repo_root, "pipeline")
    export_script = os.path.join(pipeline_dir, "export_data.py")
    export_res = runner.run([sys.executable, export_script, "--public"],
                            cwd=repo_root, capture_output=True, text=True)
    if export_res.returncode != 0:
        raise PublishRefusal("export_validation_failed",
                             f"export_data.py --public failed:\n"
                             f"{export_res.stdout[-2000:]}\n{export_res.stderr[-2000:]}")
    validate_script = os.path.join(pipeline_dir, "validate_data.py")
    validate_res = runner.run([sys.executable, validate_script, "--strict"],
                              cwd=repo_root, capture_output=True, text=True)
    if validate_res.returncode != 0:
        raise PublishRefusal("export_validation_failed",
                             f"validate_data.py --strict failed:\n"
                             f"{validate_res.stdout[-2000:]}\n{validate_res.stderr[-2000:]}")
    return {"exportOk": True, "validateOk": True}


def run_offline_tests(test_files: list[str], *, repo_root: str = REPO_ROOT,
                      runner=subprocess) -> dict:
    failures = []
    for path in test_files:
        res = runner.run([sys.executable, path], cwd=repo_root,
                         capture_output=True, text=True)
        if res.returncode != 0:
            failures.append({"file": path, "tail": (res.stdout + res.stderr)[-1500:]})
    if failures:
        raise PublishRefusal("tests_failed",
                             f"{len(failures)} test file(s) failed: "
                             f"{', '.join(f['file'] for f in failures)}")
    return {"ran": len(test_files), "failures": []}


def run_packaging_check(*, repo_root: str = REPO_ROOT, runner=subprocess) -> dict:
    script = os.path.join(repo_root, "pipeline", "check_packaging.py")
    res = runner.run([sys.executable, script], cwd=repo_root,
                     capture_output=True, text=True)
    if res.returncode != 0:
        raise PublishRefusal("export_validation_failed",
                             f"check_packaging.py failed:\n{res.stdout[-2000:]}")
    return {"ok": True}


# ------------------------------------------------------------------- checks
def scan_for_secrets(text: str) -> list[str]:
    hits = []
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits


def staged_media_files(*, repo_root: str = REPO_ROOT, runner=subprocess) -> list[str]:
    res = runner.run(["git", "diff", "--cached", "--name-only"],
                     cwd=repo_root, capture_output=True, text=True)
    files = [f for f in (res.stdout or "").splitlines() if f.strip()]
    return [f for f in files if f.lower().endswith(MEDIA_EXTENSIONS)]


def _file_sha256(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------- git commit
def create_publication_commit(branch: str, message: str, files: list[str], *,
                              repo_root: str = REPO_ROOT, runner=subprocess,
                              push: bool = True) -> dict:
    """Create a fresh branch off the current HEAD, stage exactly `files`,
    commit, and (if push=True) push it — mirroring this repo's own
    "Branch → commit → PR" rule. Never touches main directly, never force-
    pushes. Raises PublishRefusal on any git failure so the caller can
    surface it as a refused publication rather than a half-done commit."""
    def _run(cmd):
        res = runner.run(cmd, cwd=repo_root, capture_output=True, text=True)
        if res.returncode != 0:
            raise PublishRefusal("git_operation_failed",
                                 f"{' '.join(cmd)} failed: {res.stderr or res.stdout}")
        return res

    _run(["git", "checkout", "-b", branch])
    _run(["git", "add", *files])
    status = runner.run(["git", "diff", "--cached", "--name-only"],
                        cwd=repo_root, capture_output=True, text=True)
    if not (status.stdout or "").strip():
        raise PublishRefusal("export_validation_failed",
                             "nothing changed in the public export — refusing "
                             "to create an empty publication commit")
    secrets = scan_for_secrets(status.stdout)
    media = staged_media_files(repo_root=repo_root, runner=runner)
    if media:
        raise PublishRefusal("media_staged",
                             f"media file(s) staged for commit: {media}")
    _run(["git", "commit", "-m", message])
    sha = runner.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                     capture_output=True, text=True).stdout.strip()
    if push:
        _run(["git", "push", "-u", "origin", branch])
    return {"branch": branch, "commitSha": sha, "pushed": push, "secretsFound": secrets}


# --------------------------------------------------------------- orchestrator
def publish_job(store: js.JobStore, con, job: models.Job, segment: dict, *,
                dry_run: bool = True, worker_id: str | None = None,
                test_files: list[str] | None = None,
                repo_root: str = REPO_ROOT, push: bool = True,
                runner=subprocess) -> dict:
    """The `process-approved-job --job <id> --publish` entry point.

    Always runs the full precondition/export/test/packaging gate. Only when
    every gate passes AND dry_run=False does it create+push the publication
    commit and move the job APPROVED -> PUBLISHED. Any refusal raises
    nothing to the caller by default — it's captured as a structured
    {"ok": False, "code": ..., "reason": ...} result and recorded on the job
    via record_attempt, exactly like every other automated step in this
    pipeline. `repo_root`/`push` exist so tests can point this at an
    isolated throwaway git repo instead of the real working tree.
    """
    export_path = os.path.join(repo_root, "assets", "data", "public_data.v1.js")
    try:
        check_preconditions(con, job, segment)
        prev_hash = _file_sha256(export_path)
        regenerate_and_validate_export(repo_root=repo_root, runner=runner)
        new_hash = _file_sha256(export_path)
        run_packaging_check(repo_root=repo_root, runner=runner)
        if test_files:
            run_offline_tests(test_files, repo_root=repo_root, runner=runner)

        result = {"ok": True, "dryRun": dry_run,
                  "prevExportHash": prev_hash, "newExportHash": new_hash}

        if dry_run:
            log(f"{job.job_key}: dry-run publish OK — export regenerated+validated, "
               f"packaging OK. Re-run with dry_run=False to commit + push.")
            return result

        safe_key = job.job_key.replace(":", "-").replace("/", "-")
        branch = f"data/publish-{safe_key}"
        message = (f"Publish {segment.get('map_name')} "
                  f"({segment.get('team_a')} vs {segment.get('team_b')})\n\n"
                  f"Job {job.job_key}. Export hash {new_hash}.")
        files = [os.path.relpath(export_path, repo_root)]
        team_cov = os.path.join(repo_root, "assets", "data", "team_coverage.v1.json")
        if os.path.exists(team_cov):
            files.append(os.path.relpath(team_cov, repo_root))
        commit_info = create_publication_commit(
            branch, message, files, repo_root=repo_root, runner=runner, push=push)
        result.update(commit_info)

        run_key = models.publish_key(new_hash or "unknown")
        con.execute(
            """INSERT INTO publication_runs
               (run_key, db_hash, prev_db_hash, export_hash, prev_export_hash,
                branch, source_commit, state, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'PUSHED', ?)
               ON CONFLICT(run_key) DO UPDATE SET
                 branch=excluded.branch, source_commit=excluded.source_commit,
                 state=excluded.state""",
            (run_key, new_hash, prev_hash, new_hash, prev_hash, branch,
             commit_info["commitSha"],
             dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()),
        )
        con.commit()

        store.record_attempt(job.job_key, ok=True, worker_id=worker_id,
                             diagnostic_path=branch)
        store.transition(job.job_key, sm.PUBLISHED)
        result["nextStep"] = (
            f"open a PR from {branch} against the default branch, wait for CI, "
            f"merge once green, then confirm the GitHub Pages deploy and the "
            f"live match page — the existing PR/CI/Pages flow, unchanged.")
        log(f"{job.job_key}: PUBLISHED — {result['nextStep']}")
        return result
    except PublishRefusal as refusal:
        store.record_error(job.job_key, error_code=refusal.code,
                           error_message=str(refusal))
        log(f"{job.job_key}: publication REFUSED [{refusal.code}] {refusal}")
        return {"ok": False, "code": refusal.code, "reason": str(refusal)}
