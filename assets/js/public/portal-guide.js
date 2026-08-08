/* =====================================================================
   portal-guide.js — the part of the portal that answers "…and where do I
   type that?"

   The portal has always been able to build the right command. What it never
   did was tell anyone where the command goes, what has to exist first, or
   what a working result looks like — so a first-time operator got a line of
   shell in a grey box and stopped there.

   This file owns four things, all of them presentational; it starts no
   jobs, approves nothing, and sends nothing anywhere:

     1. THE MODE BANNER — one sentence saying whether this copy of the
        portal can do the work (a control room is answering) or can only
        explain it (static hosting). No jargon, no guessing.
     2. THE WALKTHROUGH — per-operating-system, one command per card, each
        with the exact keystrokes to open a terminal, a copy button, what
        the output should look like, and the specific fix for the way that
        step usually fails. Progress is remembered, so the second visit is
        two steps rather than seven.
     3. COPYABLE COMMANDS — every `code.ik-cmd` on the page, including the
        ones the pipeline panel renders per job, becomes click-to-copy.
     4. THE HANDOFF — the pasted link is carried into the local control room
        and into the calibration wizard, so a link is typed exactly once.

   Honesty rules kept from the rest of the project: the walkthrough never
   claims a step succeeded that it cannot observe (a tick is the operator's
   own claim, and says so), and the live control-room probe only runs where
   a browser is actually allowed to make it — a page served over HTTPS
   cannot reach http://localhost, so instead of a spinner that can never
   resolve, that case gets a link and a plain explanation.
   ===================================================================== */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const REPO = "https://github.com/cvree/OWCSComp.Tracker.git";
  const FOLDER = "OWCSComp.Tracker";
  const LOCAL = "http://localhost:8000/portal.html";
  const OS_KEY = "owcs.portal.os";
  const DONE_KEY = "owcs.portal.setupDone";

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  /* ================================================================== */
  /*  1 · copy-to-clipboard, for every command on the page              */
  /* ================================================================== */

  let toastEl = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "copy-toast";
      toastEl.setAttribute("role", "status");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toastEl.classList.remove("on"), 1800);
  }

  function copyText(text) {
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
    }
    return Promise.resolve(fallbackCopy(text));
  }

  function fallbackCopy(text) {
    /* Clipboard API needs a secure context; a control room on plain
       http://localhost qualifies, a plain-http LAN address does not. */
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function flash(el, text) {
    copyText(text).then((ok) => {
      if (!ok) { toast("Could not reach the clipboard — select the text and copy it"); return; }
      toast("Copied — paste it into your terminal with Ctrl+V (⌘V on a Mac)");
      if (el.classList.contains("term-copy")) {
        el.classList.add("copied");
        const was = el.textContent;
        el.textContent = "copied";
        setTimeout(() => { el.textContent = was; el.classList.remove("copied"); }, 1600);
      } else {
        el.dataset.copied = "1";
        setTimeout(() => { delete el.dataset.copied; }, 1600);
      }
    });
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest && ev.target.closest(".term-copy");
    if (btn) {
      ev.preventDefault();
      const card = btn.closest(".term");
      const code = card && card.querySelector("code");
      flash(btn, btn.dataset.copy || (code ? code.textContent : ""));
      return;
    }
    const cmd = ev.target.closest && ev.target.closest("code.ik-cmd");
    if (cmd) { ev.preventDefault(); flash(cmd, cmd.textContent.trim()); }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const cmd = ev.target.closest && ev.target.closest("code.ik-cmd");
    if (!cmd) return;
    ev.preventDefault();
    flash(cmd, cmd.textContent.trim());
  });

  /* The pipeline panel renders commands long after this file runs, and
     re-renders them after every action, so the affordance is applied by an
     observer rather than a one-off pass. */
  function markCommands(root) {
    (root || document).querySelectorAll("code.ik-cmd:not([data-copyable])")
      .forEach((el) => {
        el.dataset.copyable = "1";
        el.tabIndex = 0;
        el.setAttribute("role", "button");
        el.setAttribute("title", "Click to copy this command");
      });
  }
  markCommands();
  if (window.MutationObserver) {
    new MutationObserver(() => markCommands()).observe(document.body,
      { childList: true, subtree: true });
  }

  /* ================================================================== */
  /*  2 · the per-OS walkthrough                                        */
  /* ================================================================== */

  /* Command text is deliberately literal — no placeholders to fill in, no
     "<your path here>". A step that needs a decision is a step people get
     wrong, so every one of these can be pasted exactly as written. */
  const RECIPES = {
    windows: {
      label: "Windows",
      app: "PowerShell",
      prompt: "PS>",
      py: "python",
      pip: "pip",
      /* Everything that has to exist BEFORE the first command, with the
         download in reach. A brand-new Windows machine fails step 1, and
         burying that behind an "it didn't do that" drawer meant the guide
         only worked for people who did not need it. */
      prereq: {
        title: "Before you start: three things Windows does not come with",
        lead: "All free, all official, all safe to accept every default — "
          + "except one checkbox, which is called out below because missing it "
          + "is the single most common way this goes wrong.",
        items: [
          {
            name: "Python 3.12 or newer",
            why: "The tracker is a Python program.",
            href: "https://www.python.org/downloads/windows/",
            label: "Get Python for Windows ↗",
            warn: "On the installer's <b>first</b> screen, tick "
              + "<b>Add python.exe to PATH</b> before pressing Install. Miss it and "
              + "every command below answers <code>'python' is not recognized</code>. "
              + "Pick the plain <b>3.12</b> or <b>3.13</b> installer — the newest "
              + "release is usually a month or two ahead of the ready-built image "
              + "libraries this uses, and installing it means step 4 tries to compile "
              + "them and fails asking for Visual C++.",
            alt: "<b>Do not install Python from the Microsoft Store.</b> Windows ships "
              + "a decoy <code>python</code> that opens the Store instead of running "
              + "anything, and the Store copy installs packages somewhere the rest of "
              + "this cannot see. If typing <code>python</code> opens the Store: "
              + "<b>Settings → Apps → Advanced app settings → App execution aliases</b>, "
              + "and switch <b>python.exe</b> and <b>python3.exe</b> off.",
          },
          {
            name: "Git",
            why: "How you fetch the project. Accept every default the installer offers.",
            href: "https://git-scm.com/download/win",
            label: "Get Git for Windows ↗",
            alt: "Would rather not? <a href=\"https://github.com/cvree/OWCSComp.Tracker/"
              + "archive/refs/heads/main.zip\" target=\"_blank\" rel=\"noopener noreferrer\">"
              + "Download the project as a ZIP</a>, right-click it → <b>Extract All</b>, "
              + "then open the extracted folder in File Explorer, click the address bar, "
              + "type <code>powershell</code> and press Enter. That skips steps 2 and 3 "
              + "entirely — you are already in the folder.",
          },
          {
            name: "ffmpeg",
            why: "Reads the video frames. Nothing to download by hand — step 5 below "
              + "installs it for you.",
          },
        ],
        after: "After installing any of these, <b>close PowerShell and open it "
          + "again</b>. A window that was already open cannot see a program installed "
          + "after it started — this is why a command can still say “not recognized” "
          + "immediately after you installed it.",
      },
      open: {
        keys: [
          "Press the <kbd>Windows</kbd> key.",
          "Type <b>powershell</b> — it will be the first result.",
          "Press <kbd>Enter</kbd>.",
        ],
        looks: "A window with a line that ends in <code>&gt;</code>. That line is "
          + "the prompt. Every command below gets pasted there and run with "
          + "<kbd>Enter</kbd> — one at a time, waiting for each to finish.",
      },
      steps: [
        {
          id: "py",
          title: "Check you have Python",
          why: "The tracker is a Python program. Windows does not come with Python, "
            + "so this either prints a version or tells you it is missing.",
          cmd: "python --version",
          expect: "<b>Python 3.12.0</b> or any higher number.",
          trouble: [
            ["Nothing happens, or the Microsoft Store opens",
              "Python is not installed. Get it from <a href=\"https://www.python.org/downloads/\" "
              + "target=\"_blank\" rel=\"noopener noreferrer\">python.org/downloads</a> and — this "
              + "part matters — tick <b>Add python.exe to PATH</b> on the first screen of the "
              + "installer. Then close PowerShell, open it again, and run this command once more."],
            ["It says 3.11 or lower",
              "Install 3.12 from python.org over the top; both versions can coexist."],
          ],
        },
        {
          id: "clone",
          title: "Download the tracker",
          why: "This copies the whole project into a folder called <code>"
            + FOLDER + "</code> inside wherever PowerShell currently is — which, in a "
            + "window you just opened, is your user folder: <code>C:\\Users\\you\\"
            + FOLDER + "</code>. That is the right place. <b>Do not put it in OneDrive, "
            + "Documents or Desktop</b> — those are synced folders, and OneDrive will "
            + "try to upload every multi-gigabyte broadcast you download, locking the "
            + "files while it does.",
          cmd: "git clone " + REPO,
          expect: "A few lines ending in <code>Resolving deltas: 100% … done.</code>",
          trouble: [
            ["<code>git</code> is not recognised",
              "Either install <a href=\"https://git-scm.com/download/win\" target=\"_blank\" "
              + "rel=\"noopener noreferrer\">Git for Windows</a> (accept every default, then "
              + "reopen PowerShell), or skip git entirely: download the "
              + "<a href=\"https://github.com/cvree/OWCSComp.Tracker/archive/refs/heads/main.zip\" "
              + "target=\"_blank\" rel=\"noopener noreferrer\">project ZIP</a>, right-click → "
              + "<b>Extract All</b>, and remember where you put it."],
            ["It says the folder already exists",
              "You already have it. Skip to the next step."],
          ],
        },
        {
          id: "cd",
          title: "Move into the folder",
          why: "<b>The step everyone skips.</b> Every command after this one has to run "
            + "from inside the project folder, or it will say the file cannot be found.",
          cmd: "cd " + FOLDER,
          expect: "The prompt itself changes — it now ends with "
            + "<code>\\" + FOLDER + "&gt;</code>. If it does not, you are not in the folder.",
          trouble: [
            ["Cannot find path",
              "PowerShell is somewhere else. Run <code>cd ~</code> to go to your user folder "
              + "and try again — or, if you extracted a ZIP, open that folder in File Explorer, "
              + "click the address bar, type <code>powershell</code> and press Enter."],
          ],
        },
        {
          id: "deps",
          title: "Install what it needs",
          why: "Reads <code>requirements.txt</code> and fetches the image and video "
            + "libraries the detector uses, including yt-dlp. A few minutes the first "
            + "time; instant afterwards. <code>python -m pip</code> rather than plain "
            + "<code>pip</code> on purpose — a machine with two Pythons has two pips, "
            + "and the wrong one installs where nothing can find it.",
          cmd: "python -m pip install -r requirements.txt",
          expect: "A wall of scrolling text ending in <code>Successfully installed …</code>.",
          trouble: [
            ["<b>Microsoft Visual C++ 14.0 or greater is required</b>",
              "Your Python is newer than the ready-built libraries. Nothing needs "
              + "compiling here — install Python <b>3.12</b> or <b>3.13</b> from "
              + "python.org instead, reopen PowerShell, and run this again."],
            ["No matching distribution found",
              "Same cause as above: too new a Python. Use 3.12 or 3.13."],
            ["Permission denied, or Access is denied",
              "Add <code>--user</code> to the end of the command."],
            ["It hangs, or SSL errors",
              "Usually a corporate network or a VPN intercepting the connection. Try "
              + "off the VPN."],
          ],
        },
        {
          id: "ffmpeg",
          title: "Install ffmpeg",
          why: "The tool that actually reads video frames. Without it the portal still "
            + "runs, but it cannot open a downloaded broadcast.",
          cmd: "winget install --id Gyan.FFmpeg -e --accept-source-agreements "
            + "--accept-package-agreements",
          expect: "<code>Successfully installed</code>. <b>Then close PowerShell and "
            + "open a new one</b>, and <code>cd " + FOLDER + "</code> again — a window "
            + "that was already open cannot see ffmpeg yet, and this is the single most "
            + "common reason the next step says it is still missing.",
          trouble: [
            ["<code>winget</code> is not recognised",
              "An older Windows 10. Install <b>App Installer</b> from the Microsoft "
              + "Store, or download a build from "
              + "<a href=\"https://www.gyan.dev/ffmpeg/builds/\" target=\"_blank\" "
              + "rel=\"noopener noreferrer\">gyan.dev/ffmpeg/builds</a> "
              + "(<i>ffmpeg-release-essentials.zip</i>), unzip it to "
              + "<code>C:\\ffmpeg</code>, and add <code>C:\\ffmpeg\\bin</code> to your "
              + "PATH under <b>Settings → System → About → Advanced system settings → "
              + "Environment Variables</b>."],
            ["It asks you to agree to terms and stops",
              "The command above already accepts them; if you typed a shorter version, "
              + "copy this one instead."],
            ["Installed, but the next step still says ffmpeg is missing",
              "The terminal is stale. Close it, open a new PowerShell, "
              + "<code>cd " + FOLDER + "</code>, and re-run the check."],
          ],
        },
        {
          id: "check",
          title: "Check everything at once",
          why: "<b>The step that saves the afternoon.</b> This inspects every tool the "
            + "pipeline needs and prints one verdict per line. Finding every problem now "
            + "beats discovering them one at a time, each of them several minutes into a "
            + "job. Anything it marks FAIL comes with the exact command that fixes it.",
          cmd: "python pipeline/preflight.py",
          expect: "A list of lines ending in <code>READY for capture</code>. If it ends "
            + "in <code>NOT READY — fix: …</code> instead, run the command shown under "
            + "each FAIL line, then run this again until it says READY.",
          trouble: [
            ["It says ffmpeg is missing, but you just installed it",
              "This terminal started before ffmpeg existed and cannot see it. Close it, "
              + "open a new PowerShell, <code>cd " + FOLDER + "</code>, and run this again."],
            ["<code>can't open file … preflight.py</code>",
              "You are not inside the project folder — go back to step 3."],
          ],
        },
        {
          id: "serve",
          title: "Start your control room",
          why: "This is the one that turns this page into a working portal. It starts a "
            + "small server on your own machine — nothing is exposed to the internet.",
          cmd: "python pipeline/serve.py",
          expect: "<code>serving at http://localhost:8000/</code> and then the window "
            + "just sits there. <b>That is correct.</b> Leave it open — closing it stops "
            + "the control room.",
          keepOpen: true,
          trouble: [
            ["Address already in use",
              "Something else has port 8000. Run <code>python pipeline/serve.py --port 8010</code> "
              + "and use <code>http://localhost:8010/portal.html</code> below instead."],
            ["No such file or directory",
              "You are not inside the project folder — go back to step 3."],
          ],
        },
      ],
    },

    mac: {
      label: "macOS",
      app: "Terminal",
      prompt: "$",
      py: "python3",
      pip: "pip3",
      prereq: {
        title: "Before you start: what macOS does not already have",
        lead: "macOS brings git along with the developer tools, and will offer to "
          + "install them the first time you need them. Python is the one to get "
          + "ahead of — the version Apple ships is too old.",
        items: [
          {
            name: "Python 3.12 or newer",
            why: "macOS ships 3.9, which the tracker cannot run on.",
            href: "https://www.python.org/downloads/macos/",
            label: "Get Python for macOS ↗",
            warn: "The python.org installer needs no options — accept the defaults. "
              + "It installs alongside Apple's copy rather than replacing it, and adds "
              + "the <code>python3</code> the commands below use.",
          },
          {
            name: "Git",
            why: "Already there in spirit: the first time you run a git command macOS "
              + "offers to install the Command Line Tools. Click Install, wait, and run "
              + "the command again. Nothing to download in advance.",
          },
          {
            name: "Homebrew",
            why: "Only needed for ffmpeg in step 5. If you do not have it, that step "
              + "links a direct download instead.",
            href: "https://brew.sh",
            label: "brew.sh ↗",
          },
        ],
        after: "After installing Python, <b>close Terminal and open it again</b> so the "
          + "new version is the one it finds.",
      },
      open: {
        keys: [
          "Press <kbd>⌘</kbd> + <kbd>Space</kbd>.",
          "Type <b>Terminal</b>.",
          "Press <kbd>Return</kbd>.",
        ],
        looks: "A window with a line that ends in <code>%</code> or <code>$</code>. "
          + "That line is the prompt. Every command below gets pasted there and run "
          + "with <kbd>Return</kbd> — one at a time, waiting for each to finish.",
      },
      steps: [
        {
          id: "py",
          title: "Check you have Python 3.12",
          why: "macOS ships an older Python, so this usually prints a version that is "
            + "too low rather than nothing at all.",
          cmd: "python3 --version",
          expect: "<b>Python 3.12.0</b> or higher.",
          trouble: [
            ["It says 3.9, or command not found",
              "Install a current one from <a href=\"https://www.python.org/downloads/macos/\" "
              + "target=\"_blank\" rel=\"noopener noreferrer\">python.org</a>, or with Homebrew: "
              + "<code>brew install python@3.12</code>. Then close Terminal and reopen it."],
          ],
        },
        {
          id: "clone",
          title: "Download the tracker",
          why: "This copies the project into a folder called <code>" + FOLDER
            + "</code> in your home directory.",
          cmd: "git clone " + REPO,
          expect: "A few lines ending in <code>Resolving deltas: 100% … done.</code>",
          trouble: [
            ["A box appears asking to install developer tools",
              "Click <b>Install</b> and wait — that is macOS fetching git. Then run the "
              + "command again."],
            ["It says the folder already exists",
              "You already have it. Skip to the next step."],
          ],
        },
        {
          id: "cd",
          title: "Move into the folder",
          why: "<b>The step everyone skips.</b> Every command after this one has to run "
            + "from inside the project folder.",
          cmd: "cd " + FOLDER,
          expect: "The prompt changes to show <code>" + FOLDER + "</code>. "
            + "Run <code>pwd</code> if you want it spelled out.",
          trouble: [
            ["No such file or directory",
              "Run <code>cd ~</code> first, then try again."],
          ],
        },
        {
          id: "deps",
          title: "Install what it needs",
          why: "Fetches the image and video libraries the detector uses. A few minutes "
            + "the first time; instant afterwards.",
          cmd: "pip3 install -r requirements.txt",
          expect: "Scrolling text ending in <code>Successfully installed …</code>.",
          trouble: [
            ["externally-managed-environment",
              "Homebrew's Python is protecting itself. Make a private environment first: "
              + "<code>python3 -m venv .venv &amp;&amp; source .venv/bin/activate</code>, then "
              + "run the install again. Re-run the <code>source</code> line in any new "
              + "Terminal window."],
          ],
        },
        {
          id: "ffmpeg",
          title: "Install ffmpeg",
          why: "The tool that actually reads video frames.",
          cmd: "brew install ffmpeg",
          expect: "Several minutes of output, ending without an error.",
          trouble: [
            ["<code>brew</code> is not found",
              "Install Homebrew first from <a href=\"https://brew.sh\" target=\"_blank\" "
              + "rel=\"noopener noreferrer\">brew.sh</a>, or download a static build from "
              + "<a href=\"https://evermeet.cx/ffmpeg/\" target=\"_blank\" "
              + "rel=\"noopener noreferrer\">evermeet.cx/ffmpeg</a>."],
          ],
        },
        {
          id: "serve",
          title: "Start your control room",
          why: "Starts a small server on your own Mac. Nothing is exposed to the internet.",
          cmd: "python3 pipeline/serve.py",
          expect: "<code>serving at http://localhost:8000/</code> and then the window "
            + "sits there. <b>That is correct.</b> Leave it open.",
          keepOpen: true,
          trouble: [
            ["Address already in use",
              "Run <code>python3 pipeline/serve.py --port 8010</code> and use "
              + "<code>http://localhost:8010/portal.html</code> below."],
          ],
        },
      ],
    },

    linux: {
      label: "Linux",
      app: "Terminal",
      prompt: "$",
      py: "python3",
      pip: "pip3",
      prereq: {
        title: "Before you start",
        lead: "Nothing to download from a website. Current distributions already carry "
          + "everything, and where they do not, the step that needs it installs it.",
        items: [
          {
            name: "Python 3.12 or newer",
            why: "Almost certainly already installed — step 1 checks, and names the "
              + "package to install if it is too old.",
          },
          {
            name: "git",
            why: "Usually present. If not: <code>sudo apt install git</code>, or your "
              + "distribution's equivalent.",
          },
          {
            name: "ffmpeg",
            why: "Installed by step 5's command.",
          },
        ],
        after: "If you install Python from your package manager, open a fresh terminal "
          + "afterwards so it picks the new version up.",
      },
      open: {
        keys: [
          "Press <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>T</kbd>.",
          "(Or find <b>Terminal</b> in your applications menu.)",
        ],
        looks: "A window with a line ending in <code>$</code>. Every command below "
          + "gets pasted there and run with <kbd>Enter</kbd> — note that many "
          + "terminals need <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> to paste.",
      },
      steps: [
        {
          id: "py",
          title: "Check you have Python 3.12",
          why: "Most current distributions ship it; older ones do not.",
          cmd: "python3 --version",
          expect: "<b>Python 3.12.0</b> or higher.",
          trouble: [
            ["It is older than 3.12",
              "On Debian/Ubuntu: <code>sudo apt install python3.12 python3.12-venv</code>. "
              + "On Fedora: <code>sudo dnf install python3.12</code>."],
          ],
        },
        {
          id: "clone",
          title: "Download the tracker",
          why: "Copies the project into <code>" + FOLDER + "</code> in your home directory.",
          cmd: "git clone " + REPO,
          expect: "Lines ending in <code>Resolving deltas: 100% … done.</code>",
          trouble: [
            ["<code>git</code> not found",
              "<code>sudo apt install git</code> (or your distribution's equivalent)."],
          ],
        },
        {
          id: "cd",
          title: "Move into the folder",
          why: "<b>The step everyone skips.</b> Everything after this runs from inside "
            + "the project folder.",
          cmd: "cd " + FOLDER,
          expect: "The prompt changes to show <code>" + FOLDER + "</code>.",
          trouble: [],
        },
        {
          id: "deps",
          title: "Install what it needs",
          why: "Fetches the image and video libraries the detector uses.",
          cmd: "pip3 install -r requirements.txt",
          expect: "Scrolling text ending in <code>Successfully installed …</code>.",
          trouble: [
            ["externally-managed-environment",
              "Your distribution protects the system Python. Make a private environment: "
              + "<code>python3 -m venv .venv &amp;&amp; source .venv/bin/activate</code>, then "
              + "run the install again."],
          ],
        },
        {
          id: "ffmpeg",
          title: "Install ffmpeg",
          why: "The tool that actually reads video frames.",
          cmd: "sudo apt install ffmpeg",
          expect: "It asks for your password, then installs. Fedora: "
            + "<code>sudo dnf install ffmpeg</code>. Arch: <code>sudo pacman -S ffmpeg</code>.",
          trouble: [],
        },
        {
          id: "serve",
          title: "Start your control room",
          why: "Starts a small server bound to your own machine only.",
          cmd: "python3 pipeline/serve.py",
          expect: "<code>serving at http://localhost:8000/</code>, then the window sits "
            + "there. <b>That is correct.</b> Leave it open.",
          keepOpen: true,
          trouble: [
            ["Address already in use",
              "Run <code>python3 pipeline/serve.py --port 8010</code> and use "
              + "<code>http://localhost:8010/portal.html</code> below."],
          ],
        },
      ],
    },
  };

  function detectOS() {
    const saved = store.get(OS_KEY);
    if (saved && RECIPES[saved]) return saved;
    const ua = (navigator.userAgent || "") + " " + (navigator.platform || "");
    /* Windows is matched positively, and is also the fallback. Everyone
       operating this is on Windows, so an unrecognised browser landing on
       the Windows walkthrough is right far more often than it is wrong —
       and it is the only walkthrough that assumes nothing is installed. */
    if (/Windows|Win32|Win64|WOW64/i.test(ua)) return "windows";
    if (/Mac|iPhone|iPad|iPod/i.test(ua)) return "mac";
    if (/Linux|X11|CrOS/i.test(ua) && !/Android/i.test(ua)) return "linux";
    return "windows";
  }

  let currentOS = detectOS();
  const autoOS = !store.get(OS_KEY);

  function doneSet() {
    try { return new Set(JSON.parse(store.get(DONE_KEY + "." + currentOS) || "[]")); }
    catch (e) { return new Set(); }
  }
  function saveDone(set) {
    store.set(DONE_KEY + "." + currentOS, JSON.stringify(Array.from(set)));
  }

  function termCard(recipe, cmd) {
    return `<div class="term">
      <div class="term-bar">
        <span class="lights"><i></i><i></i><i></i></span>
        <span>type this into ${esc(recipe.app)}</span>
        <span class="grow"></span>
        <button type="button" class="term-copy">copy</button>
      </div>
      <div class="term-body"><span class="prompt">${esc(recipe.prompt)}</span>
        <code>${esc(cmd)}</code></div>
    </div>`;
  }

  /* The downloads, before the terminal. Collapsed once ticked, because the
     second time round you already have them. */
  function prereqCard(recipe, done) {
    const p = recipe.prereq;
    if (!p) return "";
    const items = p.items.map((it) => `<li>
      <div class="dl-head">
        <b>${esc(it.name)}</b>
        ${it.href ? `<a class="pbtn dl-get" href="${esc(it.href)}" target="_blank"
          rel="noopener noreferrer">${esc(it.label)}</a>` : ""}
      </div>
      <p>${it.why}</p>
      ${it.warn ? `<p class="dl-warn"><b>Watch out:</b> ${it.warn}</p>` : ""}
      ${it.alt ? `<p class="dl-alt">${it.alt}</p>` : ""}
    </li>`).join("");
    return `<div class="g-prereq" data-done="${done ? 1 : 0}">
      <h3>${esc(p.title)}</h3>
      <p class="dl-lead">${esc(p.lead)}</p>
      <ol class="dl-list">${items}</ol>
      <p class="dl-after">${p.after}</p>
      <label class="g-tick"><input type="checkbox" data-tick="prereq"
        ${done ? "checked" : ""} /> I have these — collapse this</label>
    </div>`;
  }

  function stepCard(recipe, step, done) {
    const trouble = (step.trouble || []).length
      ? `<details class="g-trouble"><summary>It didn't do that</summary><dl>${
        step.trouble.map(([q, a]) => `<dt>${q}</dt><dd>${a}</dd>`).join("")}</dl></details>`
      : "";
    return `<li class="gstep" data-step="${esc(step.id)}" data-done="${done ? 1 : 0}">
      <h4>${esc(step.title)}</h4>
      <p class="g-why">${step.why}</p>
      ${termCard(recipe, step.cmd)}
      <p class="expect"><b>You should see:</b> ${step.expect}</p>
      ${trouble}
      <label class="g-tick"><input type="checkbox" data-tick="${esc(step.id)}"
        ${done ? "checked" : ""} /> Done — hide the detail</label>
    </li>`;
  }

  function localUrl() {
    const u = (($("ik-url") || {}).value || "").trim();
    return u ? LOCAL + "?url=" + encodeURIComponent(u) : LOCAL;
  }

  function renderGuide() {
    const host = $("portal-guide");
    if (!host) return;
    const r = RECIPES[currentOS];
    const done = doneSet();
    const allDone = r.steps.every((s) => done.has(s.id));

    host.innerHTML = `
      <div class="os-tabs" role="tablist" aria-label="Operating system">
        ${Object.keys(RECIPES).map((k) => `<button type="button" role="tab"
          data-os="${k}" aria-selected="${k === currentOS}">${esc(RECIPES[k].label)}</button>`).join("")}
      </div>
      <p class="os-detected">${autoOS
        ? "Picked to match the computer you are reading this on — change it if that is wrong."
        : "Remembered from last time."}</p>

      <div class="g-shortcuts">
        <span>Been here before?</span>
        <button type="button" id="g-skip">I already have the project — skip to the last two steps</button>
        ${done.size ? `<button type="button" id="g-reset">Start the walkthrough over</button>` : ""}
      </div>

      ${done.has("clone") ? `<div class="g-update">
        <b>Coming back after a while?</b> Fetch the latest version first — fixes
        land here often, and an old copy runs into problems that no longer exist.
        Run this from inside the project folder.
        ${termCard(r, "git pull")}
      </div>` : ""}

      ${prereqCard(r, done.has("prereq"))}

      <div class="term-open">
        <h3>Then: open ${esc(r.app)}</h3>
        <ol>${r.open.keys.map((k) => `<li>${k}</li>`).join("")}</ol>
        <p class="looks">${r.open.looks}</p>
      </div>

      <ol class="gsteps">
        ${r.steps.map((s) => stepCard(r, s, done.has(s.id))).join("")}
      </ol>

      <div class="g-finish">
        <h3>${allDone ? "That's everything — open your control room" : "Last thing: come back here, locally"}</h3>
        <p>Once <code>serve.py</code> is running and left open, your own copy of this
          portal lives at <b>localhost:8000</b>. Open it and this page turns into a
          working control room: the Convert button runs the pipeline, the log streams
          live, and you never type another command.</p>
        <a class="pbtn pbtn--gold" id="g-open" href="${esc(localUrl())}"
           target="_blank" rel="noopener noreferrer">Open my control room ↗</a>
        <p class="g-fine muted" id="g-fine">Nothing will be there until
          <code>serve.py</code> is running — a browser cannot start it for you. If the tab
          says it can't connect, the server isn't up yet.</p>
      </div>`;

    const openBtn = $("g-open");
    if (openBtn) {
      /* Keep the pasted link attached to the handoff even if it is typed
         after the guide rendered. */
      openBtn.addEventListener("mousedown", () => { openBtn.href = localUrl(); });
      openBtn.addEventListener("focus", () => { openBtn.href = localUrl(); });
    }
  }

  document.addEventListener("click", (ev) => {
    const tab = ev.target.closest && ev.target.closest(".os-tabs button[data-os]");
    if (tab) {
      currentOS = tab.dataset.os;
      store.set(OS_KEY, currentOS);
      renderGuide();
      return;
    }
    if (ev.target.id === "g-skip") {
      const set = doneSet();
      ["prereq", "py", "clone", "cd", "deps", "ffmpeg"].forEach((id) => set.add(id));
      saveDone(set);
      renderGuide();
      return;
    }
    if (ev.target.id === "g-reset") {
      saveDone(new Set());
      renderGuide();
    }
  });

  document.addEventListener("change", (ev) => {
    const tick = ev.target.closest && ev.target.closest("[data-tick]");
    if (!tick) return;
    const set = doneSet();
    if (tick.checked) set.add(tick.dataset.tick); else set.delete(tick.dataset.tick);
    saveDone(set);
    const li = tick.closest(".gstep, .g-prereq");
    if (li) li.dataset.done = tick.checked ? "1" : "0";
  });

  /* ================================================================== */
  /*  3 · the mode banner + live control-room watch                     */
  /* ================================================================== */

  /* A page served over HTTPS is not permitted to fetch http://localhost, so
     on the hosted site there is no honest way to watch for a control room
     appearing. Say so once, rather than spin forever. */
  const canProbe = location.protocol !== "https:"
    || /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);

  function setBanner(kind, title, html, actions) {
    const el = $("portal-mode");
    if (!el) return;
    el.className = "mode-banner is-" + kind;
    el.innerHTML = `<span class="mb-icon" aria-hidden="true">${
      kind === "live" ? "●" : kind === "busy" ? "◐" : "○"}</span>
      <div class="mb-body">
        <h3>${esc(title)}</h3>
        ${html}
        ${actions ? `<div class="mb-actions">${actions}</div>` : ""}
      </div>`;
  }

  function showStatic() {
    setBanner("static",
      "This copy of the portal can explain the work, but not do it",
      `<p>You are reading the hosted site. There is no computer behind a static page,
        so nothing here can download a broadcast or read a frame — and pretending
        otherwise would just waste your time.</p>
       <p><b>Step 2 below fixes that in about five minutes, once.</b> After that,
        pasting a link is the entire job.</p>`,
      `<a class="pbtn pbtn--gold" href="#setup">Show me how, step by step</a>
       <a class="pbtn pbtn--quiet" href="#matchfinder">Just browse the broadcasts it found</a>`);
    const step = document.querySelector('.pstep[data-step="2"]');
    if (step) step.hidden = false;
  }

  function showLive(running) {
    setBanner(running ? "busy" : "live",
      running ? "Control room online — a job is already running"
        : "Control room online — this page can do the work",
      running
        ? `<p>One job runs at a time, so the Convert button is held until the current
            one finishes. The live log appears below as it goes.</p>`
        : `<p>Paste a broadcast link above and press <b>Convert</b>. The download,
            the HUD calibration, the map and team proposals and the detection all run
            on this machine, stopping only where a human decision genuinely belongs.</p>
           <p>No commands. No terminal. This is the finished experience.</p>`,
      `<a class="pbtn pbtn--quiet" href="#pipeline">Jump to the pipeline</a>`);
    const step = document.querySelector('.pstep[data-step="2"]');
    if (step) step.hidden = true;
    const rail = document.querySelector('.pj a[href="#setup"]');
    if (rail) rail.hidden = true;
    /* The walkthrough is gone, so a link to it is a dead end — but the
       commands below it are still real, so the copy hint stays. */
    const help = $("cmd-help-link");
    if (help) help.remove();
    renumberRail();
  }

  /* With a control room running there is no "run it" step to do, so it
     leaves the rail — and everything downstream renumbers, rather than
     counting 1, 3, 4 at someone. */
  function renumberRail() {
    let n = 0;
    document.querySelectorAll(".pj a[href^='#']").forEach((a) => {
      if (a.hidden || a.classList.contains("pj-help")) return;
      const badge = a.querySelector(".n");
      if (badge) badge.textContent = String(++n);
    });
    /* The rail's first entry is the paste step, which is a <header> rather
       than a .pstep — so the section badges continue from two. */
    let s = 1;
    document.querySelectorAll(".pstep").forEach((sec) => {
      if (sec.hidden) return;
      const badge = sec.querySelector(".pstep-num");
      if (badge) badge.textContent = String(++s);
    });
  }

  document.addEventListener("owcs:portal-mode", (ev) => {
    const d = (ev && ev.detail) || {};
    if (d.api) { showLive(!!d.running); stopWatch(); loadReadiness(); }
    else { showStatic(); startWatch(); }
  });

  /* If a control room comes up while this page is open (the normal case:
     you follow the walkthrough in one window with the portal in another,
     served from the same local static server), notice it and offer the
     reload rather than making the operator wonder. */
  let watchTimer = null;
  let watchTicks = 0;
  const WATCH_LIMIT = 150;      /* ten minutes at four seconds a tick */
  function stopWatch() { if (watchTimer) { clearInterval(watchTimer); watchTimer = null; } }
  function startWatch() {
    if (!canProbe || watchTimer) return;
    watchTimer = setInterval(() => {
      /* Someone who left the tab open overnight does not need a 404 every
         four seconds until morning; they will reload when they come back. */
      if (++watchTicks > WATCH_LIMIT) { stopWatch(); return; }
      fetch("/api/ping", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          if (!j || !j.ok) return;
          stopWatch();
          setBanner("live", "Your control room just came online",
            `<p>This page loaded before the server did, so it is still in read-only
              mode. Reload and the Convert button goes live.</p>`,
            `<button class="pbtn pbtn--gold" type="button" id="g-reload">Reload the portal</button>`);
        })
        .catch(() => { /* still not up; keep waiting quietly */ });
    }, 4000);
  }

  document.addEventListener("click", (ev) => {
    if (ev.target.id !== "g-reload") return;
    const u = (($("ik-url") || {}).value || "").trim();
    location.href = "portal.html" + (u ? "?url=" + encodeURIComponent(u) : "");
  });

  /* ================================================================== */
  /*  3b · is this machine actually ready?                              */
  /* ================================================================== */

  /* A control room answering /api/ping only proves Python started. It does
     not prove ffmpeg is installed, that cv2 imported, or that yt-dlp is
     there — and each of those fails LATER, one at a time, several minutes
     into a job, which is how a five-minute setup turns into an afternoon.
     /api/preflight already knows all of it; this puts the whole answer on
     the page before the first link is pasted. */

  const READY_LABELS = {
    python: "Python", ffmpeg: "ffmpeg (reads video frames)",
    ffprobe: "ffprobe (checks clips)", "yt-dlp": "yt-dlp (downloads the VOD)",
    "js-runtime": "JavaScript runtime (helps yt-dlp)",
    opencv: "OpenCV (the detector itself)", database: "Local database",
    source: "Source", layout: "Layout", writable: "Folder permissions",
  };

  function readyRow(c) {
    const cls = c.status === "fail" ? "bad" : c.status === "warn" ? "warn" : "ok";
    const mark = c.status === "fail" ? "✕" : c.status === "warn" ? "!" : "✓";
    return `<li class="rk-row is-${cls}">
      <span class="rk-mark" aria-hidden="true">${mark}</span>
      <div class="rk-body">
        <b>${esc(READY_LABELS[c.name] || c.name)}</b>
        <span class="rk-detail">${esc(c.detail || "")}</span>
        ${c.status !== "ok" && (c.remedy || c.note)
          ? `<div class="rk-fix">
              <span class="rk-fix-lbl">Fix it:</span>
              ${c.remedy ? `<code class="ik-cmd">${esc(c.remedy)}</code>` : ""}
              ${c.note ? `<p class="rk-note">${esc(c.note)}</p>` : ""}
             </div>` : ""}
      </div>
    </li>`;
  }

  function renderReady(res) {
    const host = $("portal-ready");
    if (!host) return;
    host.hidden = false;
    const checks = (res && res.checks) || [];
    const bad = checks.filter((c) => c.status === "fail");
    const warn = checks.filter((c) => c.status === "warn");
    const rows = checks.filter((c) => c.status !== "ok" || !bad.length);

    if (!checks.length) {
      host.className = "ready is-warn";
      host.innerHTML = `<div class="rk-head"><b>Could not check this machine</b>
        <button type="button" class="pbtn pbtn--quiet rk-recheck">Try again</button></div>
        <p class="rk-lead">Run <code>python pipeline/preflight.py</code> in the
        terminal for the full answer.</p>`;
      return;
    }
    if (!bad.length && !warn.length) {
      host.className = "ready is-ok";
      host.innerHTML = `<div class="rk-head">
          <b>✓ Everything this needs is installed and working</b>
          <button type="button" class="pbtn pbtn--quiet rk-recheck">Re-check</button>
        </div>
        <details class="rk-more"><summary>Show the ${checks.length} checks</summary>
          <ul class="rk-list">${checks.map(readyRow).join("")}</ul></details>`;
      return;
    }
    host.className = "ready is-" + (bad.length ? "bad" : "warn");
    host.innerHTML = `<div class="rk-head">
        <b>${bad.length
          ? `${bad.length} thing${bad.length === 1 ? "" : "s"} still to install`
          : `Ready, with ${warn.length} thing${warn.length === 1 ? "" : "s"} worth knowing`}</b>
        <button type="button" class="pbtn pbtn--quiet rk-recheck">Re-check</button>
      </div>
      <p class="rk-lead">${bad.length
        ? "The pipeline will stop on these. Every fix below is a whole command — "
          + "click to copy it, run it in the terminal, then press Re-check. "
          + "Some need the terminal closed and reopened first."
        : "None of these stop a job; they make one slower or less reliable."}</p>
      <ul class="rk-list">${rows.map(readyRow).join("")}</ul>`;
  }

  function loadReadiness() {
    const host = $("portal-ready");
    if (!host) return;
    host.hidden = false;
    host.className = "ready is-busy";
    host.innerHTML = `<div class="rk-head"><b>Checking this machine…</b></div>`;
    fetch("/api/preflight", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(renderReady)
      .catch(() => renderReady(null));
  }

  document.addEventListener("click", (ev) => {
    if (ev.target.closest && ev.target.closest(".rk-recheck")) {
      ev.preventDefault();
      loadReadiness();
    }
  });

  /* ================================================================== */
  /*  4 · the link box: instant, plain-language feedback                */
  /* ================================================================== */

  const YT = [
    [/youtube\.com\/watch\?[^#]*v=([\w-]{6,})/i, "YouTube VOD"],
    [/youtu\.be\/([\w-]{6,})/i, "YouTube VOD"],
    [/youtube\.com\/live\/([\w-]{6,})/i, "YouTube live/VOD"],
    [/youtube\.com\/playlist\?[^#]*list=([\w-]{6,})/i, "YouTube playlist"],
    [/faceit\.com\/[^/]+\/[^/]+\/room\/([\w:-]{6,})/i, "FACEIT match room"],
    [/faceit\.com\/[^/]+\/championship\/([\w-]{6,})/i, "FACEIT tournament"],
  ];

  function verdict() {
    const el = $("url-verdict");
    const box = $("ik-url");
    if (!el || !box) return;
    const v = (box.value || "").trim();
    if (!v) {
      el.className = "url-verdict hint";
      el.textContent = "A YouTube VOD link is the normal input — the whole address, "
        + "straight from the browser bar.";
      return;
    }
    for (const [re, label] of YT) {
      const m = v.match(re);
      if (m) {
        el.className = "url-verdict ok";
        el.textContent = `Looks right — ${label} (${m[1]}).`;
        return;
      }
    }
    if (/^https?:\/\//i.test(v)) {
      el.className = "url-verdict hint";
      el.textContent = "That is a link, but not one this pipeline recognises. It takes "
        + "YouTube VODs and playlists, and FACEIT rooms, matches and tournaments.";
      return;
    }
    el.className = "url-verdict hint";
    el.textContent = "Paste the full address, including https:// — or the path to a "
      + "video file on the machine running the control room.";
  }

  /* Carry a link handed over from the hosted site, the match finder, or the
     calibration wizard, so it is typed exactly once. */
  function adoptQueryUrl() {
    const box = $("ik-url");
    if (!box) return;
    let q = "";
    try { q = new URLSearchParams(location.search).get("url") || ""; } catch (e) { q = ""; }
    if (q && !box.value) {
      box.value = q;
      box.dispatchEvent(new Event("input", { bubbles: true }));
      setTimeout(() => { box.focus(); box.select(); }, 120);
    }
  }

  const urlBox = $("ik-url");
  if (urlBox) urlBox.addEventListener("input", verdict);

  /* Keep every downstream handoff pointed at whatever is in the box, so the
     link is typed once and then carried: into the calibration wizard, into
     the advanced one-liner, and into the local control room. */
  function syncHandoffs() {
    const u = ((urlBox || {}).value || "").trim();
    const cal = $("cal-link");
    if (cal) cal.href = "calibrate.html?from=portal" + (u ? "&url=" + encodeURIComponent(u) : "");
    const adv = $("adv-cmd");
    if (adv) {
      adv.textContent = 'python pipeline/automation/cli.py convert-link --url "'
        + (u || "<paste-your-link-above>") + '"';
    }
  }
  if (urlBox) urlBox.addEventListener("input", syncHandoffs);

  /* ================================================================== */
  /*  5 · the journey rail                                              */
  /* ================================================================== */

  function initRail() {
    const links = Array.from(document.querySelectorAll(".pj a[href^='#']"));
    if (!links.length || !("IntersectionObserver" in window)) return;
    const byId = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        links.forEach((a) => a.removeAttribute("aria-current"));
        const a = byId.get(en.target.id);
        if (a) a.setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    byId.forEach((a, id) => { const el = $(id); if (el) io.observe(el); });
  }

  /* ---- boot ---------------------------------------------------------- */
  adoptQueryUrl();
  verdict();
  syncHandoffs();
  renderGuide();
  initRail();
  if (window.OWCSIdentity) window.OWCSIdentity.bindAll("[data-identity]");
})();
