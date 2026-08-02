#!/usr/bin/env python3
"""
site_paths.py — one safe way to turn an absolute path into a stored one.

Everywhere this project records a path — a job payload, an evidence crop, a
report link, a layout reference — it stores it *relative to the repository
root, with forward slashes*, because those strings become URL fragments the
browser has to resolve. Backslashes never resolve as URL separators, so every
call site has to normalise them.

That much was already understood: `worker.py` and `team_assets.py` each grew
their own `_site_relpath` helper saying exactly that. What neither survived is
the OTHER way `os.path.relpath` behaves differently on Windows:

    ValueError: path is on mount 'C:', start on mount 'D:'

There is no relative path between two different drives, so `relpath` raises
rather than returning anything. On a GitHub Windows runner the checkout is on
D: and `tempfile.mkdtemp()` hands back C:, which is why the first real Windows
CI run failed ten suites on this single line — a `ValueError` escaping from
deep inside the worker, surfacing as `unknown_error` on a job that had
downloaded perfectly well.

It is not a test-only problem. A user whose media root is on a second drive —
which the desktop app positively encourages, since broadcasts are enormous —
would hit exactly this in production, and the job would fail with an error
naming neither the drive nor the path.

`site_relpath()` is therefore total: it returns a relative path when one
exists, and an absolute POSIX-style path when one does not. A consumer that
joins it to a base URL gets something wrong-but-obvious either way, instead of
an exception from a stack frame nowhere near the cause.
"""
from __future__ import annotations

import os

__all__ = ["site_relpath", "is_relative"]


def site_relpath(path: str, start: str) -> str:
    """`path` relative to `start`, forward-slashed. Never raises.

    Falls back to the absolute path (still forward-slashed) when the two are
    on different Windows drives, where no relative path can exist.
    """
    if not path:
        return ""
    try:
        rel = os.path.relpath(path, start)
    except ValueError:
        # Different drives on Windows. An absolute location is the honest
        # answer: it is at least resolvable on the machine that recorded it.
        rel = os.path.abspath(path)
    return rel.replace(os.sep, "/").replace("\\", "/")


def is_relative(path: str, start: str) -> bool:
    """True when `path` sits inside `start`.

    Cross-drive is False rather than an exception — the caller asking this
    question always wants "can I store this as a repo-relative path?", and on
    a different drive the answer is simply no.
    """
    if not path:
        return False
    try:
        rel = os.path.relpath(path, start)
    except ValueError:
        return False
    return not rel.startswith(os.pardir + os.sep) and rel != os.pardir
