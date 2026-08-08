"""
proc_text.py — how this pipeline decodes what its subprocesses say.

`subprocess.run(..., text=True)` decodes the child's bytes with
`locale.getencoding()`. On Linux that is UTF-8 and nobody notices. On Windows
it is the ANSI code page — cp1252 on an English install — and the three
programs this pipeline shells out to all emit UTF-8 regardless:

  * yt-dlp `-J` returns JSON containing the video's real title. OWCS
    broadcasts are titled in Korean, Japanese and Portuguese as often as in
    English, and one unmapped byte raises UnicodeDecodeError *inside
    subprocess*, before `json.loads` is ever reached. The download fails with
    an error that names an encoding rather than anything the user did.
  * ffmpeg and ffprobe write UTF-8 diagnostics and file paths.
  * every Python child of the desktop application writes UTF-8, because
    `owcs_desktop.paths.apply_environment()` sets PYTHONIOENCODING.

So the encoding is never in question — it is UTF-8 — and asking the locale
is simply the wrong question. Spread these over every captured subprocess:

    subprocess.run(cmd, capture_output=True, text=True, **proc_text.PIPE_TEXT)

`errors="replace"` is deliberate rather than lazy. This decoding sits between
a finished download and the record of it; a stray byte in a video title must
degrade to U+FFFD, not fail a job that already spent ten minutes of bandwidth.
Text is worth degrading. It is never worth crashing over.

`pipeline/test_subprocess_text.py` walks the AST of every module here and in
`desktop/` and fails if a captured subprocess call is left decoding by locale.

The same code page bites in the other direction, on the way OUT. This
pipeline's own output is full of `→`, `≥`, `■` and `✓`, none of which exist
in cp1252. Printing them to a real Windows console window is fine — the
console is UTF-16 underneath — but the moment output is redirected,

    python pipeline\\automation\\cli.py link-status > status.txt

Python falls back to the ANSI code page for the pipe and the command dies
with UnicodeEncodeError instead of writing the file. Every entry point here
therefore calls `enable_utf8_stdio()` before it prints anything, which makes
the two directions agree: UTF-8 in, UTF-8 out, on every platform.
"""
from __future__ import annotations

import sys

#: Decoding kwargs for any `subprocess` call that captures text output.
PIPE_TEXT = {"encoding": "utf-8", "errors": "replace"}


def enable_utf8_stdio() -> None:
    """Make this process's stdout/stderr UTF-8, whatever the code page says.

    Idempotent, and deliberately unable to fail: a stream that cannot be
    reconfigured (already detached, replaced by a test double, not a real
    file) is left exactly as it was. `errors="replace"` is the belt to the
    encoding's braces — a character that somehow still cannot be written
    degrades to `?` rather than killing a command that has already done its
    work.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            # Detached, already closed, or a stand-in that only pretends to
            # be a stream. Printing is still the caller's business.
            pass
