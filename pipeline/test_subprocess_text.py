#!/usr/bin/env python3
"""
test_subprocess_text.py — the console code page may never decide anything.

This suite exists because of a specific failure. The clean-machine CI job
installed the application, verified its checksum, ran every health check
green, started the real end-to-end readiness test — and then died with

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

while printing the label of the suite it had just been about to run
("Synthetic broadcast: capture → detect → sync → export"). Nothing was wrong
with the machine or the pipeline. A status line contained an arrow, the
frozen executable's stdout was a pipe, and Python had chosen cp1252 for it.

There are two halves to never having that again, and both are asserted here:

  ENCODING   the application's own stdout and stderr are reconfigured to
             UTF-8 before anything can print, and every Python child is told
             to write UTF-8 through PYTHONIOENCODING.

  DECODING   every `subprocess` call that captures text says which encoding
             it is reading. `text=True` alone decodes with the *locale's*
             encoding, which on an English Windows install is cp1252 — so a
             Korean VOD title in yt-dlp's `-J` output raises UnicodeDecodeError
             inside subprocess, before `json.loads` is ever reached, and a
             ten-minute download fails citing a codec.

The decoding half is checked by walking the AST rather than grepping: these
modules discuss the hazard in their own docstrings, and prose about
`text=True` is not a call using it.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

import proc_text  # noqa: E402


#: Directories whose every module is swept. Anything that shells out and
#: reads what comes back belongs to one of them.
SWEPT_DIRS = ("pipeline", "desktop")

#: Files exempt from the decoding sweep, each with the reason.
EXEMPT = {
    # Test doubles: a fake runner's kwargs are the thing under test.
    "pipeline/test_subprocess_text.py": "this file",
}


def _captures_text(node: ast.Call) -> bool:
    """Does this call capture output AND decode it to str?

    Both halves matter. A call with `capture_output=True` but no `text=True`
    returns bytes and decodes nothing, so no encoding applies to it.
    """
    kwargs = {kw.arg for kw in node.keywords if kw.arg}
    if not ({"text", "universal_newlines"} & kwargs):
        return False
    if "stdout" in kwargs or "capture_output" in kwargs:
        return True
    return False


def _states_its_encoding(node: ast.Call) -> bool:
    """Explicit `encoding=`, or a `**`-unpacked PIPE_TEXT mapping."""
    for kw in node.keywords:
        if kw.arg == "encoding":
            return True
        if kw.arg is None:  # **something
            value = kw.value
            name = (value.attr if isinstance(value, ast.Attribute)
                    else value.id if isinstance(value, ast.Name) else "")
            if name.endswith("PIPE_TEXT"):
                return True
    return False


def _is_subprocess_call(node: ast.Call) -> bool:
    """`subprocess.run(...)`, `runner.run(...)`, `.Popen(...)`, `.check_output`.

    Matched on the attribute name rather than the object, because this repo
    injects a `runner=subprocess` seam everywhere for testability and the
    real call site is `runner.run`.
    """
    func = node.func
    return (isinstance(func, ast.Attribute)
            and func.attr in ("run", "Popen", "check_output", "call"))


def _python_files() -> list[str]:
    out = []
    for d in SWEPT_DIRS:
        for root, _dirs, files in os.walk(os.path.join(REPO, d)):
            if "__pycache__" in root:
                continue
            for name in sorted(files):
                if name.endswith(".py"):
                    out.append(os.path.relpath(os.path.join(root, name), REPO)
                               .replace(os.sep, "/"))
    return sorted(out)


class TestEveryCapturedSubprocessStatesItsEncoding(unittest.TestCase):
    def test_no_captured_subprocess_decodes_by_locale(self):
        offenders = []
        for rel in _python_files():
            if rel in EXEMPT:
                continue
            path = os.path.join(REPO, rel)
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            try:
                tree = ast.parse(source, filename=rel)
            except SyntaxError:  # pragma: no cover - would fail elsewhere
                continue
            lines = source.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not _is_subprocess_call(node) or not _captures_text(node):
                    continue
                if _states_its_encoding(node):
                    continue
                offenders.append(f"{rel}:{node.lineno}: "
                                 f"{lines[node.lineno - 1].strip()}")
        self.assertEqual(
            offenders, [],
            "these capture text from a subprocess but let the locale pick the "
            "encoding — which is cp1252 on Windows and will raise "
            "UnicodeDecodeError on the first non-Latin-1 byte. Add "
            "**proc_text.PIPE_TEXT (or **paths.PIPE_TEXT in desktop/):\n  "
            + "\n  ".join(offenders))

    def test_the_two_pipe_text_constants_agree(self):
        """`pipeline` and `desktop` each own a copy; they must not drift."""
        from owcs_desktop import paths
        self.assertEqual(paths.PIPE_TEXT, proc_text.PIPE_TEXT)

    def test_replace_not_strict(self):
        """A stray byte in a video title must degrade, never fail a job."""
        self.assertEqual(proc_text.PIPE_TEXT["errors"], "replace")
        self.assertEqual(proc_text.PIPE_TEXT["encoding"], "utf-8")


class TestChildrenAreToldToWriteUtf8(unittest.TestCase):
    def test_apply_environment_sets_pythonioencoding(self):
        from owcs_desktop import paths
        env: dict[str, str] = {}
        paths.apply_environment(env=env)
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8:replace")

    def test_it_is_set_even_when_one_is_already_present(self):
        """Unlike OWCS_DB, this is a contract, not a preference.

        The capture side decodes UTF-8 unconditionally. An inherited
        PYTHONIOENCODING of cp1252 would silently put the two halves out of
        step, which is worse than either choice on its own.
        """
        from owcs_desktop import paths
        env = {"PYTHONIOENCODING": "cp1252"}
        paths.apply_environment(env=env)
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8:replace")


class TestTheApplicationCanPrintItsOwnLabels(unittest.TestCase):
    """The exact regression: printing a suite label must not be able to fail."""

    def test_a_cp1252_stdout_reproduces_the_crash_without_the_fix(self):
        """Guard against the guard: prove the hazard is real, not theoretical.

        If this ever stops raising, the test below proves nothing.
        """
        import io
        from owcs_desktop import health

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")  # what Windows gave
        with self.assertRaises(UnicodeEncodeError):
            for _suite, label in health.READINESS_SUITES:
                print(label, file=stream)
            stream.flush()

    def test_the_same_stdout_prints_every_label_once_it_is_fixed(self):
        """The actual regression: --readiness printing its own suite labels."""
        import io
        import importlib

        from owcs_desktop import health
        owcs_app = importlib.import_module("owcs_app")

        raw = io.BytesIO()
        original = sys.stdout
        sys.stdout = io.TextIOWrapper(raw, encoding="cp1252")
        try:
            owcs_app._force_utf8_io()
            for _suite, label in health.READINESS_SUITES:
                print(label)
            sys.stdout.flush()
            # Read the bytes before restoring: dropping the last reference to
            # the wrapper closes the BytesIO under it.
            written = raw.getvalue().decode("utf-8")
        finally:
            sys.stdout = original

        for _suite, label in health.READINESS_SUITES:
            self.assertIn(label, written)
        self.assertIn("→", written)

    def test_the_entrypoint_forces_utf8_before_any_mode_runs(self):
        """`_force_utf8_io()` must run at import, not inside a mode.

        `--version` is handled by argparse during `parse_args`, and
        `--readiness` printed its first line before any of the modes had a
        chance to set anything up. The only placement that covers every path
        is module level.
        """
        with open(os.path.join(REPO, "desktop", "owcs_app.py"),
                  "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        top_level_calls = [
            n.value.func.id for n in tree.body
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
        ]
        self.assertIn("_force_utf8_io", top_level_calls,
                      "owcs_app.py must call _force_utf8_io() at module level")

    def test_it_tolerates_a_stream_that_cannot_be_reconfigured(self):
        """pythonw with no console gives streams that are None or dummies."""
        import io
        import importlib

        sys.path.insert(0, os.path.join(REPO, "desktop"))
        owcs_app = importlib.import_module("owcs_app")
        original = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()   # no .reconfigure
            sys.stderr = io.StringIO()
            owcs_app._force_utf8_io()    # must not raise
        finally:
            sys.stdout, sys.stderr = original


class TestARealChildRoundTrips(unittest.TestCase):
    """Not a mock: spawn a Python that prints an arrow and read it back.

    On Linux this passes either way — which is the point of also asserting
    the AST above. Here it proves the two halves compose.
    """

    def test_an_arrow_survives_the_round_trip(self):
        from owcs_desktop import paths
        env = dict(os.environ)
        paths.apply_environment(env=env)
        proc = subprocess.run(
            [sys.executable, "-c", "print('capture \\u2192 detect')"],
            capture_output=True, text=True, env=env, timeout=60,
            **proc_text.PIPE_TEXT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("→", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
