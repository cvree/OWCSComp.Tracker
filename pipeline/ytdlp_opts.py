#!/usr/bin/env python3
"""
ytdlp_opts.py — how this repo is allowed to talk to yt-dlp.

One place owns four things that were previously scattered or absent:

  1. **Explicit local authentication config.** Browser-cookie access is OFF
     unless an operator opts in through `OWCS_YTDLP_COOKIES_FROM_BROWSER`.
     There is no default that reads a browser profile, and there is no code
     path anywhere in this repo that writes a `cookies.txt`.
  2. **Redaction.** Cookie sources, browser profile paths, signed
     googlevideo media URLs, `pot`/`sig`/`token`/`ip` query parameters and
     any sensitive argv flag are stripped before anything is logged,
     exported to `intake.v1.json`, or sent to the browser.
  3. **Dependency detection with exact remediation** — yt-dlp (+ version and
     whether it is the same interpreter's install), ffmpeg, ffprobe, a
     supported JS runtime, `yt-dlp-ejs`, and `curl_cffi` (impersonation).
     Detection NEVER installs or upgrades anything; it prints the command.
  4. **Error classification.** A YouTube media 403 becomes the precise,
     retryable `youtube_media_forbidden` rather than a generic download
     error, so the state machine and the operator both know what happened.

Deliberately stdlib-only and import-light (no cv2, no numpy, no network at
import time): `worker-doctor`, the CLI's argparse tree, and the control-room
status panel all import this on machines that have none of the CV stack.

Why the ladder lives here and not in `video_ingest.py`: the fallback plan is
policy (what we are allowed to try, in what order, with what credentials),
while `video_ingest.py` is mechanism (run yt-dlp, watch for stalls). Keeping
policy here means the doctor, the control room and the tests can all read
and display the plan without importing the download machinery.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse

# --------------------------------------------------------------- env config
ENV_COOKIES_FROM_BROWSER = "OWCS_YTDLP_COOKIES_FROM_BROWSER"
ENV_BROWSER_PROFILE = "OWCS_YTDLP_BROWSER_PROFILE"
ENV_FORCE_IPV4 = "OWCS_YTDLP_FORCE_IPV4"
ENV_IMPERSONATE = "OWCS_YTDLP_IMPERSONATE"
ENV_EXTRA_ARGS = "OWCS_YTDLP_EXTRA_ARGS"

ALL_ENV_KEYS = (ENV_COOKIES_FROM_BROWSER, ENV_BROWSER_PROFILE,
                ENV_FORCE_IPV4, ENV_IMPERSONATE, ENV_EXTRA_ARGS)

# Browsers yt-dlp can read cookies from AND that we are willing to name. An
# unknown value is refused with the supported list rather than passed
# through — a typo must not become an opaque yt-dlp usage error mid-download.
SUPPORTED_BROWSERS = ("chrome", "edge", "firefox", "brave", "chromium",
                      "vivaldi", "opera", "safari")

# Impersonation targets are only usable when curl_cffi is installed. These
# are the stable yt-dlp target names; anything else is refused.
SUPPORTED_IMPERSONATE = ("chrome", "chrome-110", "chrome-116", "chrome-119",
                         "chrome-120", "chrome-124", "chrome-131", "edge",
                         "edge-99", "edge-101", "safari", "safari-15_3",
                         "safari-17_0", "firefox")

# --------------------------------------------------------------- arg policy
# Extra operator args are an escape hatch for real-world YouTube quirks
# (rate limits, player clients), NOT a general "run anything" channel.
# Allowlist only — a flag not named here is refused with its own reason.
SAFE_EXTRA_FLAGS: dict[str, int] = {
    # flag: number of VALUES it consumes
    "--sleep-requests": 1,
    "--sleep-interval": 1,
    "--max-sleep-interval": 1,
    "--limit-rate": 1,
    "--retries": 1,
    "--fragment-retries": 1,
    "--retry-sleep": 1,
    "--concurrent-fragments": 1,
    "--throttled-rate": 1,
    "--socket-timeout": 1,
    "--extractor-args": 1,     # e.g. youtube:player_client=android
    "--extractor-retries": 1,
    "--user-agent": 1,
    "--referer": 1,
    "--js-runtimes": 1,
    "--geo-bypass": 0,
    "--no-check-certificates": 0,
    "--prefer-insecure": 0,
    "--force-ipv4": 0,
    "--force-ipv6": 0,
    "--no-cache-dir": 0,
    "--rm-cache-dir": 0,
    "--ignore-config": 0,
}

# Flags that are refused in extra args no matter what. Some carry secrets,
# some redirect output (which would silently break the pipeline's own file
# contracts), some execute arbitrary commands.
DENIED_EXTRA_FLAGS = frozenset({
    "--cookies", "--cookies-from-browser",       # go through the config
    "--username", "--password", "--twofactor", "--netrc", "--netrc-cmd",
    "--video-password", "--ap-username", "--ap-password",
    "--exec", "--exec-before-download", "--post-processor-args",
    "-o", "--output", "--paths", "-P", "--batch-file", "-a",
    "--config-location", "--load-info-json", "--print", "--print-to-file",
    "--write-info-json", "--dump-user-agent", "--dump-json",
    "--cache-dir",
})

# Argv flags whose VALUE must never appear in a log, a report, or the
# browser. (The flag itself is fine — knowing that cookies were used is
# exactly what an operator needs to see.)
SENSITIVE_VALUE_FLAGS = frozenset({
    "--cookies-from-browser", "--cookies", "--username", "--password",
    "--twofactor", "--video-password", "--netrc-cmd", "--ap-username",
    "--ap-password", "--user-agent", "--referer",
})

REDACTED = "<redacted>"


class YtdlpConfigError(ValueError):
    """An OWCS_YTDLP_* value is unusable. Carries a stable `code`."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class AuthConfig:
    """Resolved local yt-dlp authentication/transport configuration.

    Immutable-ish value object. `problems` collects every rejected setting
    with its reason instead of raising, so the doctor and the control-room
    panel can SHOW a misconfiguration rather than crash on it; `strict()`
    raises when a caller wants the hard failure.
    """

    def __init__(self, *, cookies_from_browser: str | None = None,
                 browser_profile: str | None = None,
                 force_ipv4: bool = False,
                 impersonate: str | None = None,
                 extra_args: list[str] | None = None,
                 problems: list[str] | None = None):
        self.cookies_from_browser = cookies_from_browser
        self.browser_profile = browser_profile
        self.force_ipv4 = force_ipv4
        self.impersonate = impersonate
        self.extra_args = list(extra_args or [])
        self.problems = list(problems or [])

    # -- derived views ----------------------------------------------------
    @property
    def cookies_configured(self) -> bool:
        return bool(self.cookies_from_browser)

    @property
    def impersonate_configured(self) -> bool:
        return bool(self.impersonate)

    def cookie_args(self) -> list[str]:
        """`--cookies-from-browser browser[:profile]`, or [] when not
        configured. Building the spec here (rather than at every call site)
        is what guarantees the profile is only ever assembled in one place
        and always redacted by the same rule."""
        if not self.cookies_from_browser:
            return []
        spec = self.cookies_from_browser
        if self.browser_profile:
            spec = f"{spec}:{self.browser_profile}"
        return ["--cookies-from-browser", spec]

    def impersonate_args(self) -> list[str]:
        return ["--impersonate", self.impersonate] if self.impersonate else []

    def ipv4_args(self) -> list[str]:
        return ["--force-ipv4"] if self.force_ipv4 else []

    def base_args(self) -> list[str]:
        """Args applied to EVERY yt-dlp invocation from the configured
        baseline: forced IPv4 when the operator asked for it globally, plus
        any allowlisted extra args. Cookies/impersonation are deliberately
        NOT here — they are ladder rungs, opted into per attempt."""
        return [*self.ipv4_args(), *self.extra_args]

    def strict(self) -> "AuthConfig":
        if self.problems:
            raise YtdlpConfigError("invalid_auth_config",
                                   "; ".join(self.problems))
        return self

    def describe(self) -> dict:
        """Non-secret summary for the doctor, the control room and the
        intake export. The browser NAME is shown (an operator must be able
        to confirm which browser is configured); the PROFILE never is."""
        return {
            "cookiesFromBrowser": self.cookies_from_browser,
            "cookiesConfigured": self.cookies_configured,
            "browserProfileConfigured": bool(self.browser_profile),
            "browserProfile": REDACTED if self.browser_profile else None,
            "forceIpv4": self.force_ipv4,
            "impersonate": self.impersonate,
            "extraArgCount": len(self.extra_args),
            "extraArgs": redact_argv(self.extra_args),
            "problems": list(self.problems),
        }


def _parse_bool(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def parse_extra_args(raw: str | None) -> tuple[list[str], list[str]]:
    """(accepted_args, problems) for OWCS_YTDLP_EXTRA_ARGS.

    Split with shlex (so a quoted value survives), then walk flag-by-flag
    against the allowlist. A denied or unknown flag is DROPPED with a named
    reason — never silently kept, and never silently ignored either.
    """
    if not (raw or "").strip():
        return [], []
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        return [], [f"{ENV_EXTRA_ARGS} is not parseable ({exc})"]
    out: list[str] = []
    problems: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("-"):
            problems.append(
                f"{ENV_EXTRA_ARGS}: bare value {tok!r} without a flag — "
                f"ignored (extra args must be `--flag value` pairs)")
            i += 1
            continue
        flag = tok.split("=", 1)[0]
        inline = "=" in tok
        if flag in DENIED_EXTRA_FLAGS:
            problems.append(
                f"{ENV_EXTRA_ARGS}: {flag} is never accepted here "
                f"(credentials/output/exec flags are refused; use "
                f"{ENV_COOKIES_FROM_BROWSER} for browser cookies)")
            i += 1 + (0 if inline else SAFE_EXTRA_FLAGS.get(flag, 1))
            continue
        if flag not in SAFE_EXTRA_FLAGS:
            problems.append(
                f"{ENV_EXTRA_ARGS}: {flag} is not in the safe allowlist — "
                f"ignored. Allowed: {', '.join(sorted(SAFE_EXTRA_FLAGS))}")
            i += 1
            continue
        takes = SAFE_EXTRA_FLAGS[flag]
        if inline or takes == 0:
            out.append(tok)
            i += 1
            continue
        if i + 1 >= len(tokens):
            problems.append(f"{ENV_EXTRA_ARGS}: {flag} needs a value")
            break
        out.extend([flag, tokens[i + 1]])
        i += 2
    return out, problems


def load_auth_config(env: dict | None = None) -> AuthConfig:
    """Resolve the local auth/transport config from the environment.

    Default state is deliberately inert: no cookies, no impersonation, no
    forced IPv4, no extra args. Every rejected value lands in `problems`
    with a reason an operator can act on.
    """
    env = os.environ if env is None else env
    problems: list[str] = []

    browser = (env.get(ENV_COOKIES_FROM_BROWSER) or "").strip().lower() or None
    if browser and browser not in SUPPORTED_BROWSERS:
        problems.append(
            f"{ENV_COOKIES_FROM_BROWSER}={browser!r} is not supported — "
            f"use one of: {', '.join(SUPPORTED_BROWSERS)}")
        browser = None

    profile = (env.get(ENV_BROWSER_PROFILE) or "").strip() or None
    if profile and not browser:
        problems.append(
            f"{ENV_BROWSER_PROFILE} is set but {ENV_COOKIES_FROM_BROWSER} "
            f"is not — a profile alone grants no cookie access")
        profile = None
    if profile and ":" in profile:
        problems.append(
            f"{ENV_BROWSER_PROFILE} must not contain ':' (it separates "
            f"browser from profile in yt-dlp's own spelling)")
        profile = None

    impersonate = (env.get(ENV_IMPERSONATE) or "").strip() or None
    if impersonate and impersonate not in SUPPORTED_IMPERSONATE:
        problems.append(
            f"{ENV_IMPERSONATE}={impersonate!r} is not a recognised target — "
            f"use one of: {', '.join(SUPPORTED_IMPERSONATE)}")
        impersonate = None

    extra, extra_problems = parse_extra_args(env.get(ENV_EXTRA_ARGS))
    problems.extend(extra_problems)

    return AuthConfig(cookies_from_browser=browser, browser_profile=profile,
                      force_ipv4=_parse_bool(env.get(ENV_FORCE_IPV4)),
                      impersonate=impersonate, extra_args=extra,
                      problems=problems)


# ----------------------------------------------------------------- redaction
# A googlevideo URL is a signed, time-limited credential: anyone holding it
# can pull the media until it expires. It must never reach a log file, a
# committed report, or the browser.
_SIGNED_HOST_RE = re.compile(
    r"https?://[^\s'\"]*\b(googlevideo\.com|youtube\.com/videoplayback)[^\s'\"]*",
    re.I)
_SENSITIVE_QUERY_KEYS = ("sig", "signature", "lsig", "pot", "token",
                         "access_token", "id_token", "key", "ip", "ipbits",
                         "cpn", "sparams", "lsparams", "n", "c", "expire")
# Long opaque blobs that look like credentials even outside a URL.
_TOKENISH_RE = re.compile(r"\b(?:po_?token|potoken|visitor_data|sapisid|"
                          r"__Secure-[A-Za-z0-9_-]+|SID|HSID|SSID)\s*[=:]\s*"
                          r"[A-Za-z0-9_\-\.%]{8,}", re.I)
_COOKIE_FLAG_RE = re.compile(
    r"(--cookies-from-browser|--cookies)(\s+|=)(\S+)", re.I)
# Windows/macOS/Linux browser profile paths.
_PROFILE_PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\s'\"]*(?:User Data|Profiles)[^\s'\"]*"
    r"|/[^\s'\"]*/(?:User Data|Profiles)/[^\s'\"]*", re.I)


def _redact_url(url: str) -> str:
    """Keep a signed media URL identifiable (host + itag) but unusable."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return REDACTED
    keep = []
    for key, value in urllib.parse.parse_qsl(parts.query,
                                             keep_blank_values=True):
        if key.lower() in _SENSITIVE_QUERY_KEYS:
            keep.append((key, REDACTED))
        elif key.lower() in ("itag", "mime", "source", "clen", "dur"):
            keep.append((key, value))
        else:
            keep.append((key, REDACTED))
    query = urllib.parse.urlencode(keep, safe="<>")
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (
        f"?{query}" if query else "") + " [signed-url redacted]"


def redact_text(text: str | None) -> str:
    """Strip every credential-shaped thing from arbitrary yt-dlp/ffmpeg
    output before it is logged, stored on a job, or shown in a browser.

    Order matters: URLs first (their query strings contain the token-shaped
    values the later patterns would otherwise mangle into unreadability).
    """
    if not text:
        return ""
    out = _SIGNED_HOST_RE.sub(lambda m: _redact_url(m.group(0)), text)
    out = _COOKIE_FLAG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}",
                              out)
    out = _PROFILE_PATH_RE.sub(REDACTED, out)
    out = _TOKENISH_RE.sub(lambda m: m.group(0).split("=")[0].split(":")[0]
                           + "=" + REDACTED, out)
    return out


def redact_argv(argv: list[str] | None) -> list[str]:
    """Sanitized copy of a command line, safe to log or export verbatim.

    The flag names survive (an operator must be able to see that cookies or
    impersonation were used on this attempt); the values do not.
    """
    out: list[str] = []
    skip_next = False
    for tok in (argv or []):
        if skip_next:
            out.append(REDACTED)
            skip_next = False
            continue
        flag = tok.split("=", 1)[0]
        if flag in SENSITIVE_VALUE_FLAGS:
            if "=" in tok:
                out.append(f"{flag}={REDACTED}")
            else:
                out.append(tok)
                skip_next = True
            continue
        out.append(redact_text(tok) if ("http" in tok or "\\" in tok
                                        or "/" in tok) else tok)
    return out


def format_argv(argv: list[str] | None) -> str:
    return " ".join(redact_argv(argv))


# ------------------------------------------------------- error classification
# Stable, retryable codes. The whole point of `youtube_media_forbidden` is
# that "403 on the media URL" has a specific remedy ladder (refresh the
# signed URL, force IPv4, supply browser cookies, impersonate) that a
# generic "download_failed" hides.
ERR_FORBIDDEN = "youtube_media_forbidden"
ERR_RATE_LIMITED = "youtube_rate_limited"
ERR_BOT_CHECK = "youtube_bot_check"
ERR_UNAVAILABLE = "youtube_video_unavailable"
ERR_GEO = "youtube_geo_blocked"
ERR_AGE = "youtube_age_restricted"
ERR_PRIVATE = "youtube_private_or_members_only"
ERR_NO_FORMAT = "youtube_no_matching_format"
ERR_JS_RUNTIME = "youtube_js_runtime_missing"
ERR_NETWORK = "network_error"
ERR_PROXY = "proxy_blocked"

# Ordered most-specific-first: a "sign in to confirm you're not a bot"
# message also contains "403" on some yt-dlp versions, and the bot check
# needs the cookie remedy, not the signed-URL-refresh remedy.
_ERROR_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (ERR_BOT_CHECK,
     r"sign in to confirm|not a bot|confirm your age|captcha",
     "YouTube demanded a signed-in human. Configure browser cookies: "
     f"set {ENV_COOKIES_FROM_BROWSER}=chrome (or edge/firefox) and retry."),
    (ERR_PRIVATE,
     r"private video|members[- ]only|join this channel|requires payment",
     "The broadcast is private/members-only for this account — nothing to "
     "retry; use a public official VOD."),
    (ERR_AGE,
     r"age[- ]restricted|confirm your age|inappropriate for some users",
     "Age-restricted. Configure browser cookies from a signed-in profile: "
     f"{ENV_COOKIES_FROM_BROWSER}=chrome."),
    (ERR_GEO,
     r"not available in your country|geo[- ]?restricted|blocked it in your",
     "Geo-blocked from this host's region."),
    (ERR_UNAVAILABLE,
     r"video unavailable|has been removed|no longer available|"
     r"this video is unavailable",
     "The video is unavailable/removed — confirm the URL."),
    (ERR_RATE_LIMITED,
     r"http error 429|too many requests|rate[- ]?limit",
     "Rate limited. Slow down: "
     f"{ENV_EXTRA_ARGS}=\"--sleep-requests 2 --limit-rate 4M\"."),
    (ERR_PROXY,
     r"unable to connect to proxy|tunnel connection failed|proxyerror",
     "The network path to YouTube is blocked by a proxy — this host cannot "
     "reach YouTube media at all (not a yt-dlp problem)."),
    (ERR_FORBIDDEN,
     r"http error 403|403 forbidden|fragment .* not found, unable to "
     r"continue|unable to download video data: http error 403",
     "The signed media URL was refused. The download ladder will refresh "
     "the URL, force IPv4, then use configured browser cookies and "
     "impersonation — configure "
     f"{ENV_COOKIES_FROM_BROWSER} to enable the last two rungs."),
    (ERR_NO_FORMAT,
     r"requested format is not available|no video formats found",
     "No format matched the selector — the ladder will try a lower rung."),
    (ERR_JS_RUNTIME,
     r"no supported javascript runtime|js runtime",
     "Install Deno (recommended) or Node.js so yt-dlp can unscramble "
     "YouTube formats: https://github.com/yt-dlp/yt-dlp/wiki/EJS"),
    (ERR_NETWORK,
     r"temporary failure in name resolution|connection reset|timed out|"
     r"ssl:|certificate verify failed|network is unreachable",
     "Transient network failure — retry."),
)


def classify_ytdlp_error(text: str | None) -> tuple[str, str]:
    """(code, remedy) for yt-dlp output. ("", "") when nothing matched.

    Matches against the REDACTED text so a signed URL in the error can
    never leak through the classifier into a stored error message.
    """
    low = redact_text(text or "").lower()
    if not low:
        return "", ""
    for code, pattern, remedy in _ERROR_PATTERNS:
        if re.search(pattern, low):
            return code, remedy
    return "", ""


# Codes worth retrying with a DIFFERENT rung. A private/unavailable video is
# not one of them — retrying identical work is how a queue wedges itself.
RETRYABLE_CODES = frozenset({ERR_FORBIDDEN, ERR_RATE_LIMITED, ERR_BOT_CHECK,
                             ERR_NO_FORMAT, ERR_NETWORK, ERR_JS_RUNTIME})


def is_retryable(code: str | None) -> bool:
    return bool(code) and code in RETRYABLE_CODES


# --------------------------------------------------------- dependency checks
def _run(cmd: list[str], *, runner=subprocess, timeout: float = 20
         ) -> tuple[int, str]:
    try:
        # Inlined rather than `proc_text.PIPE_TEXT`: this module promises to
        # import nothing but the stdlib, so callers that have no pipeline
        # directory on sys.path can still read the dependency plan.
        res = runner.run(cmd, capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace")
        return res.returncode, ((res.stdout or "") + (res.stderr or "")).strip()
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def ytdlp_version(*, runner=subprocess) -> str | None:
    code, out = _run(["yt-dlp", "--version"], runner=runner)
    if code != 0:
        return None
    return (out.splitlines() or [""])[0].strip() or None


def ytdlp_module_version(*, runner=subprocess) -> str | None:
    """The version of the yt-dlp importable by THIS interpreter.

    Compared against the `yt-dlp` on PATH so an operator who pip-installed a
    nightly into one Python but runs a different `yt-dlp.exe` sees the
    mismatch — the single most confusing yt-dlp failure mode on Windows.
    """
    code, out = _run([sys.executable, "-c",
                      "import yt_dlp,sys; sys.stdout.write(yt_dlp.version.__version__)"],
                     runner=runner)
    return out.strip() if code == 0 and out.strip() else None


def python_module_present(module: str, *, runner=subprocess) -> str | None:
    """Version string of an importable module, or None. Runs in a
    subprocess so a broken/half-installed package can't take us down."""
    code, out = _run(
        [sys.executable, "-c",
         f"import {module} as m,sys; "
         f"sys.stdout.write(getattr(m,'__version__','present'))"],
        runner=runner)
    return out.strip() if code == 0 and out.strip() else None


def detect_js_runtime(which=shutil.which) -> tuple[str | None, str | None]:
    """(name, path) of a yt-dlp-usable JS runtime, else (None, None).

    Deno first: modern yt-dlp enables only Deno by default, and an installed
    Node is ignored unless `--js-runtimes node` is passed (which
    `js_runtime_args` does)."""
    for name in ("deno", "node"):
        path = which(name)
        if path:
            return name, path
    return None, None


def js_runtime_args(which=shutil.which) -> list[str]:
    name, _ = detect_js_runtime(which)
    return ["--js-runtimes", "node"] if name == "node" else []


# Exact remediation, PowerShell-first (this project's operator is on Windows).
REMEDIES: dict[str, str] = {
    "yt-dlp": "python -m pip install -U --pre yt-dlp",
    "yt-dlp-path-mismatch":
        "where.exe yt-dlp   # then either use that install, or run yt-dlp "
        "through this interpreter: python -m yt_dlp --version",
    "ffmpeg": "winget install --id Gyan.FFmpeg -e   "
              "(or: choco install ffmpeg-full)",
    "ffprobe": "winget install --id Gyan.FFmpeg -e   "
               "(ffprobe ships with ffmpeg)",
    "js-runtime": "winget install --id DenoLand.Deno -e   "
                  "(or install Node.js; yt-dlp then needs --js-runtimes node, "
                  "which this pipeline adds automatically)",
    "yt-dlp-ejs": "python -m pip install -U yt-dlp-ejs",
    "curl_cffi": "python -m pip install -U \"curl_cffi>=0.5.10\"",
}


def dependency_report(*, which=shutil.which, runner=subprocess,
                      env: dict | None = None) -> dict:
    """Everything `worker-doctor` needs to say about the download stack.

    Read-only and non-mutating by contract: this NEVER installs or upgrades
    anything — it reports and hands over the exact command. Each entry is
    {name, present, version, detail, remedy, required}.
    """
    auth = load_auth_config(env)
    entries: list[dict] = []

    def add(name: str, present: bool, version: str | None, detail: str,
            required: bool, remedy_key: str | None = None) -> None:
        entries.append({
            "name": name, "present": bool(present), "version": version,
            "detail": detail, "required": required,
            "remedy": (REMEDIES.get(remedy_key or name, "") if not present
                       else ""),
        })

    path_version = ytdlp_version(runner=runner)
    module_version = ytdlp_module_version(runner=runner)
    ytdlp_path = which("yt-dlp")
    add("yt-dlp", bool(path_version), path_version,
        (f"{ytdlp_path or 'not on PATH'}" if path_version
         else "yt-dlp is not runnable from PATH"), True)
    # The PATH binary and this interpreter's module must agree, or an
    # operator "updates yt-dlp" and nothing changes.
    if path_version and module_version and path_version != module_version:
        entries.append({
            "name": "yt-dlp-install-match", "present": False,
            "version": f"PATH {path_version} != python {module_version}",
            "detail": (f"the `yt-dlp` on PATH ({ytdlp_path}) is version "
                       f"{path_version}, but {sys.executable} imports "
                       f"yt_dlp {module_version} — updating one does not "
                       f"update the other"),
            "required": False,
            "remedy": REMEDIES["yt-dlp-path-mismatch"],
        })
    elif path_version:
        entries.append({
            "name": "yt-dlp-install-match", "present": True,
            "version": path_version,
            "detail": ("PATH binary and this interpreter agree"
                       if module_version else
                       "yt-dlp resolved from PATH (no importable module — "
                       "fine for a standalone binary install)"),
            "required": False, "remedy": "",
        })

    for tool in ("ffmpeg", "ffprobe"):
        code, out = _run([tool, "-version"], runner=runner)
        ok = code == 0
        add(tool, ok, (out.splitlines() or [""])[0][:120] if ok else None,
            which(tool) or "not on PATH", True)

    # STRONGLY recommended, not hard-required: yt-dlp still serves many
    # formats without a JS runtime, so a missing one must not declare the
    # whole worker unusable. It IS the single most common cause of a
    # download that stalls at 0 bytes, so it is reported loudly with its
    # remedy and tracked in `recommendedMissing`. (This mirrors
    # preflight.check_js_runtime, which has always graded it a warning.)
    js_name, js_path = detect_js_runtime(which)
    entries.append({
        "name": "js-runtime", "present": bool(js_name), "version": js_name,
        "detail": (f"{js_name} at {js_path}"
                   + (" (opted in with --js-runtimes node)"
                      if js_name == "node" else "")
                   if js_name else
                   "no Deno/Node — some YouTube formats cannot be unscrambled "
                   "and downloads stall at 0 bytes"),
        "required": False, "recommended": True,
        "remedy": "" if js_name else REMEDIES["js-runtime"],
    })

    ejs = python_module_present("yt_dlp_ejs", runner=runner)
    add("yt-dlp-ejs", bool(ejs), ejs,
        "EJS solver available to yt-dlp" if ejs else
        "optional: improves format unscrambling alongside a JS runtime",
        False, "yt-dlp-ejs")

    cffi = python_module_present("curl_cffi", runner=runner)
    add("curl_cffi", bool(cffi), cffi,
        "browser impersonation available (--impersonate)" if cffi else
        "optional: required only for the impersonation rung of the "
        "download ladder", False, "curl_cffi")

    required_missing = [e["name"] for e in entries
                        if e["required"] and not e["present"]]
    recommended_missing = [e["name"] for e in entries
                           if e.get("recommended") and not e["present"]]
    optional_missing = [e["name"] for e in entries
                        if not e["required"] and not e.get("recommended")
                        and not e["present"]]
    # Impersonation configured without curl_cffi is a real, silent trap:
    # the rung would fail every time with a confusing yt-dlp usage error.
    if auth.impersonate_configured and not cffi:
        auth.problems.append(
            f"{ENV_IMPERSONATE}={auth.impersonate} is configured but "
            f"curl_cffi is not installed — the impersonation rung would "
            f"fail. Install it: {REMEDIES['curl_cffi']}")
    return {
        "entries": entries,
        "requiredMissing": required_missing,
        "recommendedMissing": recommended_missing,
        "optionalMissing": optional_missing,
        "ok": not required_missing,
        "auth": auth.describe(),
    }


def format_dependency_report(report: dict) -> str:
    lines = []
    for e in report["entries"]:
        mark = ("OK  " if e["present"] else
                "MISS" if e["required"] else
                "WARN" if e.get("recommended") else "opt ")
        ver = f" {e['version']}" if e["version"] else ""
        lines.append(f"  [{mark}] {e['name']:<20}{ver}")
        if e["detail"]:
            lines.append(f"           {e['detail']}")
        if e["remedy"]:
            lines.append(f"           -> {e['remedy']}")
    auth = report["auth"]
    lines.append("  download auth:")
    cookie_state = auth["cookiesFromBrowser"] or (
        "NOT configured (no browser cookie access — the default)")
    lines.append(f"    cookies-from-browser : {cookie_state}")
    lines.append(f"    browser profile      : "
                 f"{'configured (value never shown)' if auth['browserProfileConfigured'] else 'not set'}")
    lines.append(f"    force IPv4           : {auth['forceIpv4']}")
    lines.append(f"    impersonate          : {auth['impersonate'] or 'not set'}")
    lines.append(f"    extra args           : {auth['extraArgCount']}")
    for p in auth["problems"]:
        lines.append(f"    PROBLEM              : {p}")
    return "\n".join(lines)


# ------------------------------------------------------------- the ladder
# The bounded fallback sequence. Order is deliberate: cheapest and least
# privileged first, browser credentials only after the network-level fixes
# have failed, and the quality downgrade last so we never silently give up
# 720p (which hero detection needs) before trying everything at 720p.
RUNG_NORMAL = "normal"
RUNG_REFRESH = "refresh-signed-url"
RUNG_IPV4 = "force-ipv4"
RUNG_COOKIES = "browser-cookies"
RUNG_COOKIES_IMPERSONATE = "browser-cookies+impersonate"
RUNG_ALT_FORMAT = "alternate-format"

LADDER_ORDER = (RUNG_NORMAL, RUNG_REFRESH, RUNG_IPV4, RUNG_COOKIES,
                RUNG_COOKIES_IMPERSONATE, RUNG_ALT_FORMAT)


class Rung:
    """One attempt in the ladder: what to add to argv, and why.

    `skip_reason` is set (rather than the rung being dropped) so the
    operator log shows every rung that was CONSIDERED, including the ones
    that could not run because they were never configured. A silent absence
    would look identical to a rung that ran and failed.
    """

    def __init__(self, name: str, args: list[str], why: str, *,
                 skip_reason: str | None = None,
                 fresh_url: bool = False,
                 format_override: str | None = None,
                 downgrade: bool = False):
        self.name = name
        self.args = list(args)
        self.why = why
        self.skip_reason = skip_reason
        self.fresh_url = fresh_url
        self.format_override = format_override
        self.downgrade = downgrade

    @property
    def runnable(self) -> bool:
        return self.skip_reason is None

    def describe(self) -> dict:
        return {"rung": self.name, "why": self.why,
                "args": redact_argv(self.args),
                "runnable": self.runnable, "skipReason": self.skip_reason,
                "formatOverride": self.format_override,
                "qualityDowngrade": self.downgrade}


def build_ladder(auth: AuthConfig, *, height: int = 720,
                 alt_format: str | None = None,
                 have_curl_cffi: bool | None = None) -> list[Rung]:
    """The bounded fallback sequence for one download, in order.

    Never unbounded: exactly six rungs, each attempted at most once. Rungs
    4 and 5 are inert unless the operator explicitly configured browser
    cookies — this is the whole "default to no browser-cookie access" rule,
    expressed as data rather than as a branch buried in the downloader.
    """
    base = auth.base_args()
    alt = alt_format or (f"best[height<={min(height, 720)}]/"
                         f"bestvideo[height<={min(height, 720)}]/"
                         f"best[height<=480]/best")
    cookie_args = auth.cookie_args()
    cookie_skip = (None if cookie_args else
                   f"no browser cookies configured — set "
                   f"{ENV_COOKIES_FROM_BROWSER}=chrome|edge|firefox to "
                   f"enable this rung")
    if have_curl_cffi is None:
        have_curl_cffi = bool(python_module_present("curl_cffi"))
    if not cookie_args:
        imp_skip = cookie_skip
    elif not auth.impersonate_configured:
        imp_skip = (f"no impersonation target configured — set "
                    f"{ENV_IMPERSONATE}=chrome to enable this rung")
    elif not have_curl_cffi:
        imp_skip = (f"curl_cffi is not installed, so --impersonate cannot "
                    f"work. Install it: {REMEDIES['curl_cffi']}")
    else:
        imp_skip = None

    return [
        Rung(RUNG_NORMAL, base,
             "the configured baseline download"),
        Rung(RUNG_REFRESH, [*base, "--no-cache-dir"],
             "re-extract the player and get a FRESH signed media URL "
             "(the previous one may simply have expired)",
             fresh_url=True),
        Rung(RUNG_IPV4, [*base, "--force-ipv4"],
             "force IPv4 — YouTube 403s IPv6 ranges on some ISPs",
             fresh_url=True),
        Rung(RUNG_COOKIES, [*base, "--force-ipv4", *cookie_args],
             "use the operator's configured browser cookies (read directly "
             "from the browser; nothing is ever written to disk)",
             skip_reason=cookie_skip, fresh_url=True),
        Rung(RUNG_COOKIES_IMPERSONATE,
             [*base, "--force-ipv4", *cookie_args, *auth.impersonate_args()],
             "configured cookies plus TLS impersonation of a real browser",
             skip_reason=imp_skip, fresh_url=True),
        Rung(RUNG_ALT_FORMAT, [*base, *cookie_args],
             f"last resort: a plainer <={min(height, 720)}p progressive "
             f"format/protocol that is usually served without a fight",
             fresh_url=True, format_override=alt, downgrade=True),
    ]


def main(argv=None) -> int:
    """`python pipeline/ytdlp_opts.py` — print the resolved download-auth
    configuration and dependency status. Never mutates anything."""
    report = dependency_report()
    print("[ytdlp] download stack:")
    print(format_dependency_report(report))
    print("\n[ytdlp] fallback ladder:")
    for i, rung in enumerate(build_ladder(load_auth_config()), start=1):
        state = "ready" if rung.runnable else f"SKIP — {rung.skip_reason}"
        print(f"  {i}. {rung.name:<28} {state}")
        print(f"     {rung.why}")
    print("\nOVERALL: " + ("READY" if report["ok"] else
                           "NOT READY — " + ", ".join(report["requiredMissing"])))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
