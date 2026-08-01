# The Windows application

The pipeline in `pipeline/` is unchanged in character — same state machine,
same evidence chain, same refusal to publish what it cannot prove. This
document covers the layer around it that turns those commands into something
a person installs and forgets about.

---

## For the person using it

**Install.** One file: `OWCSCompTracker-<version>-Setup.exe`. It needs no
administrator rights and installs per-user. Python, OpenCV, NumPy, yt-dlp,
ffmpeg, ffprobe, the calibrated layouts and the hero templates are all inside
it — nothing else to download, nothing to put on PATH.

**Set up.** The installer finishes by opening a graphical wizard: system
checks (with a *Fix this* button on anything failing), storage budget,
optional API keys, whether to start with Windows, and a real end-to-end test
that builds a broadcast on your PC and runs the whole pipeline over it.

**Use it.** The control room opens in your browser at `127.0.0.1`. Paste a
YouTube VOD or playlist, a FACEIT matchroom or tournament, or the path to a
video file on your PC. Everything after that is automatic until something
genuinely needs a human decision, at which point it appears under *Needs you*.

**Close the window.** Processing carries on: the control room is a viewer, and
the work happens in a separate background service. Reboot and it picks up
where it left off.

**Nothing here requires a terminal.** Every operation — including repairing a
broken dependency, restoring a backup and installing an update — is a button.

---

## Architecture

Three processes, deliberately:

```
OWCSCompTracker.exe --tray          the tray icon. Owns the other two,
  │                                 restarts either if it dies, and is the
  │                                 only thing autostart launches.
  ├── --control-room                pipeline/serve.py on 127.0.0.1.
  │                                 Serves the site, the pipeline's own API
  │                                 and /api/desktop/* . A VIEWER — killing
  │                                 it stops nothing.
  └── --service                     owcs_desktop.supervisor. Drains the job
                                    queue forever. All state is in SQLite,
                                    so a crash costs one step, not a job.
```

Splitting the UI from the work is what makes "close the window, keep
processing" true rather than a claim. It is verified on a real Windows runner
by the `windows-app` workflow, which starts the service through the API, kills
the control room, and asserts the service's heartbeat process is still alive.

### Where things live

| | |
|---|---|
| **App root** (read-only) | `%LOCALAPPDATA%\Programs\OWCS Comp Tracker` — the pipeline, layouts, templates, HTML, `vendor/bin` |
| **Data root** (writable) | `%LOCALAPPDATA%\OWCS Comp Tracker` — databases, downloads, evidence, logs, backups, credentials, settings |

`desktop/owcs_desktop/paths.py` is the only place that decides either, and
`apply_environment()` exports `OWCS_DB` / `OWCS_AUTOMATION_DB` /
`OWCS_MEDIA_ROOT` before any pipeline module is imported. That is how the
whole pipeline moves onto per-user storage without editing a single pipeline
default. `OWCS_HOME` relocates the data root (used by the tests and the
clean-machine smoke test, so nothing ever touches a real profile).

### The modules

| module | what it owns |
|---|---|
| `paths.py` | app root vs data root, the writable layout, env plumbing, first-run seeding |
| `settings.py` | non-secret configuration; atomic writes, strict on write, forgiving on read |
| `credentials.py` | API keys through Windows DPAPI (user scope + app entropy); presence-only reporting |
| `autostart.py` | the HKCU `Run` entry, including detecting one left stale by an upgrade |
| `supervisor.py` | the processing loop, single-instance lock, heartbeat, startup recovery |
| `health.py` | the system checks and the real end-to-end readiness proof |
| `storage.py` | disk budget and pruning — finished downloads only, never the audit trail |
| `backup.py` | snapshots, atomic publishing, hash-verified rollback |
| `updates.py` | release check, checksum-verified download; never installs silently |
| `repair.py` | one function per repair button |
| `intake.py` | classifies and routes whatever was pasted |
| `webapi.py` | every desktop operation as JSON, for the control room |
| `tray.py` | the tray icon and child-process supervision |

---

## Guarantees, and how each is enforced

**A secret never leaves.** Credential routes report presence and protection
only; there is no route that returns a value, and `mask()` redacts key-shaped
runs from error text as a second line of defence.
→ `test_desktop_api.py::TestNoSecretEverLeaves` asserts a stored key appears
in **no** read route's output.

**Protection is described honestly.** DPAPI where available; elsewhere the UI
says "stored with file permissions only". The code never claims encryption it
did not perform.

**Evidence is never deleted.** Pruning touches downloaded media belonging to
finished jobs and nothing else. The guard is at the point of deletion, not
only at planning.
→ A pruner pointed at `evidence/`, `reports/`, `quarantine/`, `backups/`,
`db/`, `logs/` or `layouts/` is refused, per directory, in
`test_desktop_service.py`.

**Publishing is atomic and reversible.** Write to a temp file in the same
directory, fsync, read back and compare, then `os.replace`. A backup is taken
first; a restore verifies every file against its recorded hash and refuses a
corrupted snapshot; and a restore snapshots the current state first, so a
rollback is itself reversible.

**One bad job cannot stop the queue.** A stage that raises is recorded as a
failed attempt through the job store — so the existing retry and backoff
policy applies — and the loop continues.

**An update is never unverified.** No published `SHA256SUMS`, or a mismatch,
deletes the download rather than keeping it. Nothing self-installs.

**No dead controls.** Every button id in the markup is referenced by the
script, every route the scripts call is handled in Python, and every route
handled in Python is called by a page.
→ `test_desktop_pages.py`. This found and removed a genuinely dead
`worker/resume` route.

---

## Building the installer

```
python packaging/build_windows.py
```

Stages: preflight (payload complete, icon matches its generator, version
resource synced) → vendor (fetch ffmpeg/ffprobe/yt-dlp) → exe (PyInstaller
onedir) → **verify** → installer (Inno Setup) → SHA256SUMS.

The verify stage runs the *frozen* executable's own `--check` on the build
agent before it is wrapped. A missing hidden import fails the build there
instead of shipping an application that starts and then cannot decode video.

`packaging/payload.py` is the single definition of what ships, imported by
both the PyInstaller spec and `pipeline/test_windows_packaging.py`, so the
build and the test cannot drift apart.

### What CI actually verifies

`.github/workflows/windows-app.yml`:

1. **test** — the full suite on `windows-latest`.
2. **build** — the installer, plus the frozen-exe verification above.
3. **clean-install** — a *second* runner with **no checkout and no Python**.
   It downloads only the installer artifact, checks it against the published
   checksum, installs it silently, and then verifies: the app reports its
   version; autostart was registered; `--check` passes with nothing else on
   the machine; `--readiness` processes a synthetic broadcast end to end using
   only bundled tools; the control room serves and its API reports healthy;
   the background service starts and **survives the control room being
   killed**; the repair actions run; and the uninstaller leaves nothing behind
   including the Run key.

That job is the closest available thing to a fresh Windows PC. A test that
checked the repository out first would prove nothing, so `test_windows_packaging.py`
asserts it does not.

---

## Honest limits

* **The installer has not been built or run on real Windows from this
  environment.** All of it was developed and tested on Linux. The build and
  the clean-machine verification are performed by `windows-app.yml` on a
  GitHub-hosted Windows runner; treat a green run of that workflow — not this
  document — as the evidence.
* **The installer is unsigned.** Without an Authenticode certificate,
  SmartScreen will warn on first run until the download builds reputation. The
  version resource and a stable AppId are in place, which is what a
  certificate would attach to.
* **`vendor/bin` is fetched at build time** from gyan.dev and the yt-dlp
  releases page. Those URLs are checked for being real executables and for a
  size floor, but they are not pinned to a hash — a reproducible-to-the-byte
  installer would need vendored binaries mirrored somewhere pinnable.
* **The tray needs `pystray` and `Pillow`**, which the build installs. Without
  them the app falls back to a small tkinter window, and without that to
  headless supervision. All three paths work; only the first is pretty.
* **Automatic calibration is the pipeline's existing resolver.** When it
  cannot reach production confidence the job stops at `NEEDS_LAYOUT` and the
  graphical editor is how a human resolves it. The editor adjusts geometry; it
  does not re-derive a `hud_probe`, and it marks any layout it touches
  `calibration_source: manual-edit` so nothing downstream claims a
  hand-adjusted layout was computationally calibrated.
