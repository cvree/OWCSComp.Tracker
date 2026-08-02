"""
owcs_desktop — the Windows application layer around the OWCS Comp Tracker
pipeline.

The pipeline itself (`pipeline/`) is unchanged in character: the same state
machine, the same evidence chain, the same refusal to publish what it cannot
prove. This package adds everything that turns it into something a person can
install and forget about:

    paths        where the installed payload ends and per-user data begins
    settings     the non-secret configuration, written atomically
    credentials  API keys, through Windows DPAPI
    autostart    the per-user "start with Windows" registration
    supervisor   the background service that drains the queue forever
    health       system checks and the real end-to-end readiness proof
    storage      disk budget and safe pruning (never the audit trail)
    backup       snapshots, atomic publishing, verified rollback
    updates      release checks and verified installer downloads
    repair       the one-click fixes the control room offers as buttons
    tray         the tray application and its menu
    wizard       the graphical first-run setup

Nothing in this package hardcodes a developer machine path, and nothing in it
weakens a pipeline guarantee — it only decides *when* the pipeline runs and
*where* it keeps its files.
"""
from __future__ import annotations

__all__ = ["__version__", "APP_NAME"]

#: The application version. The installer, the update check and the About box
#: all read this one value.
__version__ = "1.0.0"

APP_NAME = "OWCS Comp Tracker"
