#!/usr/bin/env python3
"""
test_discovery_workflow.py — CI / workflow / portability invariants that
cannot be checked by running Actions (there is no network in this suite),
only by reading what the repo actually declares.

It started as regression coverage for the discovery #28 false-success
incident's OTHER half: even with cli.py fixed to never crash on a missing
cv2, `python3 ... | tee run-output.txt` without `pipefail` can still mask a
REAL failure (any future one) behind `tee`'s own zero exit status. It now
also locks down the workflow-wide invariants whose absence produced real,
nameable failures:

  1. The workflow YAML itself is valid and every report-producing step uses
     the hardened `set -euo pipefail` + `test -s` pattern (never `tee`
     alone), `upload-artifact` requires a real file
     (`if-no-files-found: error`), and the pinned actions are current.
  2. At the shell level (not just by reading the YAML): a failing command
     piped through plain `tee` reports success; the same pipeline under
     `set -euo pipefail` reports the real (non-zero) exit code — this is
     the exact mechanism the fix relies on, demonstrated directly.
  3. `test -s` fails validation on an empty report and passes on a real one.
  4. Every workflow declares `permissions:` and every job a
     `timeout-minutes:`; the CI reproducibility gate is REAL (it can fail);
     a workflow that pushes generated data retries its push instead of
     throwing the run's output away; a job that blocks on another
     workflow's checks outlives that workflow's own timeout.
  5. Every text-mode `open()` in the pipeline declares `encoding=` — on
     Windows the default is cp1252 and this repo reads and writes UTF-8
     JSON/JS containing non-ASCII team and player names.

Run: python3 pipeline/test_discovery_workflow.py
"""
from __future__ import annotations
import ast
import os
import re
import shlex
import subprocess
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
WORKFLOWS_DIR = os.path.join(REPO_ROOT, ".github", "workflows")
WORKFLOW_PATH = os.path.join(WORKFLOWS_DIR, "discovery.yml")
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # so `import export_data` resolves
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)


def _sh(path: str) -> str:
    """Make a path safe to interpolate into a `bash -c` string.

    On Windows, os.path.join gives backslash separators; bash's word
    parser treats an unquoted backslash as an escape character and
    silently drops it, mangling the path (e.g. `C:\\Users\\x` becomes
    `C:Usersx`). Forward slashes are accepted by both native bash and
    Windows' own APIs, so they're safe on every platform this suite runs.
    shlex.quote then guards against spaces in the path (this repo's own
    checkout dir has one) breaking bash's word-splitting."""
    return shlex.quote(path.replace(os.sep, "/"))


REPORT_MODES = [
    "verify-channels", "calendar-dryrun", "broadcast-dryrun", "coverage",
    "teams-dryrun", "team-coverage", "team-assets-dryrun", "match-audit",
    "match-repair-dryrun", "export-dryrun",
]


def _load_yaml():
    import yaml  # pip-installed in this environment; not a stdlib dep of
    # the pipeline itself (config.py's own note about "no PyYAML" concerns
    # the automation config LOADER, not this test's validation tooling).
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestWorkflowYamlValid(unittest.TestCase):
    def test_yaml_parses(self):
        data = _load_yaml()
        self.assertIn("jobs", data)
        self.assertIn("discover", data["jobs"])

    def test_steps_are_a_nonempty_list(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        self.assertGreater(len(steps), 10)

    def _step_by_id(self, step_id: str) -> dict:
        data = _load_yaml()
        for s in data["jobs"]["discover"]["steps"]:
            if s.get("id") == step_id:
                return s
        raise AssertionError(f"no step with id={step_id!r}")

    def test_every_report_step_has_an_id(self):
        # id: <name> is what lets the Summary step reference each one's
        # real conclusion — a step with a `tee`d report and no id can't be
        # reported on honestly.
        ids = ["verify_channels", "calendar_dryrun", "broadcast_dryrun",
              "coverage_report", "teams_dryrun", "team_coverage",
              "team_assets_dryrun", "match_audit", "match_repair_dryrun",
              "export_dryrun"]
        for step_id in ids:
            self._step_by_id(step_id)  # raises if missing

    def test_every_report_step_uses_pipefail_and_captures_stderr(self):
        ids = ["verify_channels", "calendar_dryrun", "broadcast_dryrun",
              "coverage_report", "teams_dryrun", "team_coverage",
              "team_assets_dryrun", "match_audit", "match_repair_dryrun",
              "export_dryrun"]
        for step_id in ids:
            step = self._step_by_id(step_id)
            run = step.get("run", "")
            with self.subTest(step=step_id):
                self.assertIn("set -euo pipefail", run)
                self.assertIn("2>&1", run, "must capture stderr, not just stdout")
                self.assertIn("test -s run-output.txt", run,
                             "must hard-fail on an empty report")

    def test_no_bare_tee_without_pipefail_guard(self):
        """Belt-and-suspenders: no `run:` block anywhere pipes into `tee`
        without `set -euo pipefail` appearing earlier in the SAME block —
        the exact incident pattern must never reappear undetected."""
        data = _load_yaml()
        for step in data["jobs"]["discover"]["steps"]:
            run = step.get("run", "")
            if "| tee" not in run:
                continue
            with self.subTest(step=step.get("name")):
                pipefail_pos = run.find("set -euo pipefail")
                tee_pos = run.find("| tee")
                self.assertNotEqual(pipefail_pos, -1,
                                   f"{step.get('name')!r} pipes into tee without pipefail")
                self.assertLess(pipefail_pos, tee_pos,
                               f"{step.get('name')!r}: pipefail must be set BEFORE the tee pipeline")

    def test_upload_artifact_requires_a_real_file(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
        self.assertEqual(upload["with"].get("if-no-files-found"), "error")

    def test_summary_step_reflects_report_step_conclusion(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        summary = next(s for s in steps if s.get("name") == "Summary")
        env = summary.get("env", {})
        self.assertIn("REPORT_STEP_CONCLUSION", env)
        run = summary.get("run", "")
        self.assertIn("FAILED", run,
                     "the summary must be able to say a report step failed, "
                     "not just silently omit it")

    def test_actions_are_pinned_to_current_versions(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        uses = {s["uses"].split("@")[0]: s["uses"] for s in steps if "uses" in s}
        self.assertEqual(uses["actions/checkout"], "actions/checkout@v6")
        self.assertEqual(uses["actions/setup-python"], "actions/setup-python@v6")
        self.assertEqual(uses["actions/upload-artifact"], "actions/upload-artifact@v6")
        # No actions/download-artifact is used in this workflow today; if one
        # is ever added it must be pinned the same way — not asserted here
        # since there is nothing to pin yet.


def _workflow_names() -> list[str]:
    return sorted(n for n in os.listdir(WORKFLOWS_DIR)
                  if n.endswith((".yml", ".yaml")))


def _load_workflow(name: str):
    import yaml
    with open(os.path.join(WORKFLOWS_DIR, name), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _workflow_text(name: str) -> str:
    with open(os.path.join(WORKFLOWS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


class TestEveryWorkflowIsWellFormed(unittest.TestCase):
    """Invariants that apply to ALL workflows, not just discovery."""

    def test_every_workflow_yaml_parses(self):
        for name in _workflow_names():
            with self.subTest(workflow=name):
                self.assertIsInstance(_load_workflow(name), dict)

    def test_every_workflow_declares_permissions(self):
        """Without an explicit `permissions:` block a workflow inherits the
        repository default, which on many repos is still read/write for
        every scope — a scan workflow silently able to push, delete
        releases and edit issues."""
        for name in _workflow_names():
            data = _load_workflow(name)
            job_perms = all("permissions" in j
                            for j in data.get("jobs", {}).values())
            with self.subTest(workflow=name):
                self.assertTrue("permissions" in data or job_perms,
                                f"{name} declares no permissions: block")

    def test_every_job_declares_a_timeout(self):
        """A job with no timeout-minutes inherits GitHub's 360-minute
        default: one wedged `yt-dlp` or a hung `gh pr checks --watch` burns
        six hours of the free Actions budget per occurrence."""
        for name in _workflow_names():
            for job_id, job in _load_workflow(name).get("jobs", {}).items():
                with self.subTest(workflow=name, job=job_id):
                    self.assertIn("timeout-minutes", job,
                                  f"{name}:{job_id} has no timeout-minutes")

    def test_data_writing_workflows_never_cancel_in_progress(self):
        """cancel-in-progress on a workflow that COMMITS would kill a run
        between `git commit` and `git push`, throwing away a data snapshot
        that only exists on that runner."""
        for name in _workflow_names():
            text = _workflow_text(name)
            if "git push" not in text:
                continue
            data = _load_workflow(name)
            conc = data.get("concurrency") or {}
            with self.subTest(workflow=name):
                self.assertIsInstance(conc, dict, f"{name}: no concurrency")
                self.assertFalse(conc.get("cancel-in-progress", False),
                                 f"{name} pushes data but cancels in-progress "
                                 f"runs — a cancel between commit and push "
                                 f"loses the snapshot")

    def test_repo_writing_workflows_share_one_concurrency_group(self):
        """Two workflows that commit to this repository must never run at
        once. discovery, match-finder, pipeline and update-data all push
        generated data; without ONE shared group they interleave, and the
        loser of the race force-pushes over the winner's snapshot or dies on
        a non-fast-forward with its only copy of the data on a discarded
        runner. A per-ref group is not enough — they all target the same ref.

        Workflows that push a *branch of their own* and workflows that push
        to the shared branch are equally covered: both mutate refs in this
        repository.
        """
        groups = {}
        for name in _workflow_names():
            text = _workflow_text(name)
            if "git push" not in text:
                continue
            conc = _load_workflow(name).get("concurrency") or {}
            groups[name] = conc.get("group")
        self.assertTrue(groups, "no repo-writing workflow found at all")
        distinct = set(groups.values())
        self.assertEqual(
            len(distinct), 1,
            "every workflow that commits to this repo must share ONE "
            f"concurrency group so they serialise; found {groups}")
        group = distinct.pop()
        self.assertNotIn(
            "${{", str(group),
            f"the shared data group is templated ({group!r}); a per-ref or "
            f"per-run group lets two data workflows run at the same time")

    def test_a_pushed_branch_is_never_left_without_a_pull_request(self):
        """A workflow that pushes a NEW branch and then opens a PR for it
        must clean up when the PR cannot be opened.

        discovery.yml used to end its PR step with
        `gh pr create ... || echo "PR may already exist."`. Any failure —
        a transient API error, a permissions change — left the branch pushed
        with nothing pointing at it. Running hourly, that quietly filled the
        repository with `auto/*` refs that had no PR, no CI and no
        notification, and which nobody could distinguish from live work.
        """
        for name in _workflow_names():
            text = _workflow_text(name)
            if "gh pr create" not in text or 'git push origin "$BR"' not in text:
                continue
            with self.subTest(workflow=name):
                self.assertNotIn(
                    'gh pr create \\\n            --title', text.replace(
                        "if gh pr create", "IF_GUARDED"),
                    f"{name} calls `gh pr create` unguarded")
                self.assertIn(
                    "git push origin --delete", text,
                    f"{name} pushes a branch and opens a PR for it, but has no "
                    f"path that deletes the branch when the PR cannot be "
                    f"opened — that orphans the branch")
                # The cleanup must actually fail the step. A cleanup that
                # swallows its own failure is how this regressed the first time.
                self.assertIn(
                    "exit 1", text,
                    f"{name} cleans up an orphaned branch but does not fail, "
                    f"so a run that shipped nothing still reports success")


class TestCiReproducibilityGateIsReal(unittest.TestCase):
    """ci.yml once ran the exporter and then `git diff --stat … || true`.
    `|| true` means the step could never fail: the gate CLAIMED to enforce
    that the committed public export matches the database and enforced
    nothing. Regenerating in place also left the working tree dirty, which
    made test_release_reproducibility's "the suite leaves the tree clean"
    guard skip itself on every CI run."""

    def _gate_step(self) -> dict:
        steps = _load_workflow("ci.yml")["jobs"]["test"]["steps"]
        for s in steps:
            if "export_data.py" in (s.get("run") or ""):
                return s
        raise AssertionError("ci.yml no longer runs the exporter at all")

    def test_the_gate_uses_check_mode_and_writes_nothing(self):
        run = self._gate_step().get("run", "")
        self.assertIn("--check", run,
                      "the reproducibility gate must use export_data.py "
                      "--check, which writes nothing and exits non-zero on "
                      "drift")

    def test_the_gate_cannot_be_neutered_by_a_trailing_true(self):
        run = self._gate_step().get("run", "")
        for line in run.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                continue
            self.assertFalse(stripped.endswith("|| true"),
                             f"the reproducibility gate can never fail: {line!r}")

    def test_check_mode_actually_detects_drift(self):
        """Prove the gate has teeth rather than trusting its flag name: feed
        the checker a deliberately corrupted export and require a failure."""
        import export_data  # noqa: E402  (repo module, added to sys.path below)
        with open(export_data.PUBLIC_OUT_PATH, encoding="utf-8",
                  newline="") as f:
            good = f.read()
        tampered = good.replace('"demo": false', '"demo": true', 1)
        self.assertNotEqual(good, tampered, "expected to tamper with something")
        self.assertNotEqual(export_data.normalize_generated(good),
                            export_data.normalize_generated(tampered),
                            "normalization must not erase a REAL difference")
        # ...and the timestamp, the one thing it must ignore, is ignored.
        stamped = re.sub(r'"generatedAt": "[^"]+"',
                         '"generatedAt": "1999-01-01T00:00:00+00:00"', good, 1)
        self.assertNotEqual(good, stamped)

    def test_a_clean_checkout_is_reproducible_right_now(self):
        """The gate must be passing, not merely present."""
        res = subprocess.run(
            [sys.executable, os.path.join(HERE, "export_data.py"),
             "--check", "--public"],
            cwd=REPO_ROOT, capture_output=True, text=True,
                **proc_text.PIPE_TEXT)
        self.assertEqual(res.returncode, 0,
                         f"committed exports do not match a fresh export:\n"
                         f"{(res.stdout + res.stderr)[-3000:]}")


class TestDataPushesAreNotLostOnAConflict(unittest.TestCase):
    """`git pull --rebase` followed by an unguarded `git push` loses the
    run's output whenever any push lands in the window between the two: the
    push is rejected, the job goes red, and the runner holding the only
    copy of the regenerated data is discarded."""

    def test_every_workflow_that_pushes_retries_its_push(self):
        for name in _workflow_names():
            text = _workflow_text(name)
            # A push to an EXISTING shared branch is the racy case; pushing a
            # brand-new branch (discovery's `git push origin "$BR"`) cannot
            # conflict with anyone.
            if 'git push origin "HEAD:${GITHUB_REF_NAME}"' not in text:
                continue
            with self.subTest(workflow=name):
                self.assertIn("for attempt in", text,
                              f"{name} pushes to the shared branch without a "
                              f"rebase-retry loop")
                self.assertIn("git pull --rebase", text)


class TestWorkflowsThatWaitOnOtherWorkflows(unittest.TestCase):
    def test_a_job_watching_ci_outlives_cis_own_timeout(self):
        """discovery.yml blocks on `gh pr checks --watch`, waiting for
        ci.yml. At 15 minutes it was killed by its own deadline while ci.yml
        was still allowed to run for 20 — a healthy data-update PR surfaced
        as a red discovery timeout and never merged."""
        ci_timeout = _load_workflow("ci.yml")["jobs"]["test"]["timeout-minutes"]
        for name in _workflow_names():
            text = _workflow_text(name)
            if "gh pr checks" not in text:
                continue
            for job_id, job in _load_workflow(name).get("jobs", {}).items():
                with self.subTest(workflow=name, job=job_id):
                    self.assertGreater(
                        job["timeout-minutes"], ci_timeout,
                        f"{name}:{job_id} waits on ci.yml (timeout "
                        f"{ci_timeout}m) but gives up sooner")


class TestDiscoveryAutoMergeIsRobust(unittest.TestCase):
    """Regression coverage for the PR #82 incident: the discovery workflow
    opened a real PR whose CI (ci.yml) went green about ten minutes later,
    but the workflow's own "Open data-update PR" step had already reported
    merged=false and moved on within ~6 seconds of pushing — before ci.yml
    had even started. `gh pr checks "$BR" --watch --fail-fast` was called
    immediately after `gh pr create`, and a PR that reports zero checks at
    that instant is treated by gh as "nothing to watch", not "not started
    yet". The fix polls for at least one check to be reported before ever
    calling --watch."""

    def setUp(self):
        self.text = _workflow_text("discovery.yml")

    def test_checks_watch_is_preceded_by_a_registration_wait(self):
        watch_idx = self.text.index('gh pr checks "$BR" --watch --fail-fast')
        preceding = self.text[:watch_idx]
        # The wait loop must appear AFTER the PR is created/pushed and
        # BEFORE --watch is ever called, so --watch always has a real
        # check to block on instead of racing ci.yml's check-suite creation.
        self.assertIn(
            'gh pr checks "$BR" --json state --jq \'length\'', preceding,
            "discovery.yml calls `gh pr checks --watch` without first "
            "confirming a check has actually been reported for $BR — this "
            "is the exact race that left PR #82 open despite green CI")
        self.assertIn("sleep 10", preceding)

    def test_no_uncontrolled_backlog_of_open_discovery_prs(self):
        """An open auto/discovery-* PR must block a new one from being
        opened on top of it — otherwise every hour with real changes and
        an unresolved previous PR compounds into a growing backlog (this
        is literally how #79, #80, #81 and #82 accumulated)."""
        self.assertIn('startswith("auto/discovery-")', self.text)
        guard_idx = self.text.index('startswith("auto/discovery-")')
        create_idx = self.text.index("if gh pr create")
        self.assertLess(
            guard_idx, create_idx,
            "the open-PR guard must run BEFORE a new branch/PR is created")

    def test_bot_pat_is_the_only_credential_for_push_and_pr_creation(self):
        """The checkout token and the PR step's GH_TOKEN must both be
        BOT_PAT, with no fallback to the default GITHUB_TOKEN — a
        GITHUB_TOKEN-authored push/PR cannot trigger ci.yml (GitHub's
        anti-recursion rule), which is the original #47-#77 incident."""
        self.assertIn("token: ${{ secrets.BOT_PAT }}", self.text)
        self.assertIn("GH_TOKEN: ${{ secrets.BOT_PAT }}", self.text)
        self.assertNotIn("secrets.GITHUB_TOKEN", self.text,
                          "discovery.yml must never reference the default "
                          "GITHUB_TOKEN as a fallback for push/PR auth")


class TestPipelineValidationActuallyBlocks(unittest.TestCase):
    """pipeline.yml used to run `validate_data.py || echo ...`, which meant
    a HARD referential-integrity error (validate_data.py's own exit code 1)
    was logged but never stopped the run — run_batch/export/commit all kept
    going and could publish on top of broken data."""

    def test_validate_data_step_has_no_error_swallowing_fallback(self):
        text = _workflow_text("pipeline.yml")
        idx = text.index("pipeline/validate_data.py")
        line = text[idx:text.index("\n", idx)]
        self.assertNotIn("||", line,
                          f"pipeline.yml swallows validate_data.py's exit "
                          f"code: {line!r}")


class TestTextIoIsPortable(unittest.TestCase):
    """Windows' default text encoding is cp1252, not UTF-8. This repo reads
    and writes UTF-8 JSON/JS containing non-ASCII team and player names
    (assets/data/public_data.v1.js, data/sources/*.json, config/*.json), so
    a text-mode open() without `encoding=` is a real crash or a real
    mojibake bug there, not a style question."""

    def _pipeline_python_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(HERE):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            out += [os.path.join(dirpath, f)
                    for f in filenames if f.endswith(".py")]
        return sorted(out)

    def test_every_text_mode_open_declares_an_encoding(self):
        offenders = []
        for path in self._pipeline_python_files():
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for node in ast.walk(ast.parse(src)):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "open"):
                    continue
                mode = "r"
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if not isinstance(mode, str) or "b" in mode:
                    continue          # binary: encoding is meaningless
                if any(kw.arg == "encoding" for kw in node.keywords):
                    continue
                rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
                offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "text-mode open() without encoding= (cp1252 on Windows): "
            + ", ".join(offenders))

    def test_the_generated_exports_are_written_with_explicit_newlines(self):
        """Without newline="" Python translates every \\n to \\r\\n on
        Windows, so the same database exports to a byte-different file
        depending on the operator's OS — and the CI reproducibility gate
        reports drift for data that never changed."""
        import export_data  # noqa: E402
        src = os.path.join(HERE, "export_data.py")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('newline=""', text,
                      "export_data.write_body must disable newline "
                      "translation")
        self.assertTrue(hasattr(export_data, "write_body"))

    def test_gitattributes_pins_line_endings_for_generated_data(self):
        """`* text=auto` asks git to GUESS text vs binary per file. The
        generated .js/.json files are exactly the ones whose bytes CI
        compares, so their eol must be declared, not inferred."""
        with open(os.path.join(REPO_ROOT, ".gitattributes"),
                  encoding="utf-8") as f:
            text = f.read()
        for needed in ("*.js text eol=lf", "*.json text eol=lf",
                       "*.webp binary"):
            self.assertIn(needed, text, f".gitattributes is missing {needed}")


#: These tests execute `bash` pipelines to prove the shell semantics that
#: discovery.yml depends on. That workflow runs on `ubuntu-latest` and nowhere
#: else, so on Windows this would be asserting the behaviour of a shell/
#: interpreter combination that never occurs in production — Git Bash exists
#: there but `python3` does not, and Windows paths inside a bash pipeline are
#: their own quoting problem. Skipped explicitly, with the reason visible in
#: the run, rather than silently passing or noisily failing.
_BASH = shutil.which("bash")
_SKIP_SHELL = unittest.skipIf(
    sys.platform == "win32" or not _BASH,
    "bash pipefail semantics are only asserted where discovery.yml runs "
    "(ubuntu-latest); no POSIX bash available here")


@_SKIP_SHELL
class TestPipefailMechanism(unittest.TestCase):
    """Prove the actual shell mechanism, independent of the YAML: this is
    what discovery #28 got wrong and what the fix relies on."""

    def test_failing_command_through_bare_tee_reports_success(self):
        """Reproduces the incident exactly: a failing command piped to
        `tee` (no pipefail) still exits 0 — tee's own success masks it."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            res = subprocess.run(
                ["bash", "-c", f"{shlex.quote(sys.executable)} -c 'import sys; print(\"partial\"); "
                              f"sys.exit(1)' | tee {_sh(out)}"],
                capture_output=True, text=True, **proc_text.PIPE_TEXT)
            self.assertEqual(res.returncode, 0,
                            "this is the bug: tee's exit code hides the "
                            "failing command's real status")
            self.assertTrue(os.path.exists(out))

    def test_same_failure_under_pipefail_reports_failure(self):
        """The fix: with pipefail, the pipeline's exit status is the
        FIRST failing command's — python3's — not tee's."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            res = subprocess.run(
                ["bash", "-c", f"set -euo pipefail; {shlex.quote(sys.executable)} -c 'import sys; "
                              f"print(\"partial\"); sys.exit(1)' 2>&1 | tee {_sh(out)}"],
                capture_output=True, text=True, **proc_text.PIPE_TEXT)
            self.assertNotEqual(res.returncode, 0,
                               "pipefail must surface the real python failure")

    def test_stderr_is_captured_in_the_report_with_2_and_1(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            subprocess.run(
                ["bash", "-c",
                 f"set -euo pipefail; "
                 f"({shlex.quote(sys.executable)} -c 'import sys; print(\"to stderr\", file=sys.stderr)'; true) "
                 f"2>&1 | tee {_sh(out)}"],
                capture_output=True, text=True, **proc_text.PIPE_TEXT)
            with open(out, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("to stderr", content)

    def test_empty_report_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            open(out, "w", encoding="utf-8").close()  # empty file
            res = subprocess.run(["bash", "-c", f"test -s {_sh(out)}"])
            self.assertNotEqual(res.returncode, 0)

    def test_nonempty_report_passes_validation(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write("some real discovery output\n")
            res = subprocess.run(["bash", "-c", f"test -s {_sh(out)}"])
            self.assertEqual(res.returncode, 0)

    def test_a_real_successful_dry_run_produces_a_nonempty_report(self):
        """End-to-end version of the same guarantee, via the real CLI (the
        exact command discovery.yml's mode=coverage step runs)."""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "automation.sqlite")
            out = os.path.join(d, "run-output.txt")
            cli_path = os.path.join(HERE, "automation", "cli.py")
            script = (f"set -euo pipefail; "
                     f"python3 {_sh(cli_path)} "
                     f"--db {_sh(db_path)} coverage --window 1 2>&1 | tee {_sh(out)}; "
                     f"test -s {_sh(out)}")
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                capture_output=True, text=True,
                                    **proc_text.PIPE_TEXT)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.getsize(out) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
