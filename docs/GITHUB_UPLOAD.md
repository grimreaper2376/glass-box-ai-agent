# Uploading GlassBox to GitHub

Exact steps to get this into a public repository with a working link, on
Windows (PowerShell) — the same commands work on Linux/macOS with `~` in place
of `$HOME`.

Total time: about 10 minutes, including creating a GitHub account if you don't
have one.

---

## Part 1 — Install Git, if you don't have it

Check first:

```powershell
git --version
```

If that fails:

1. Download from <https://git-scm.com/download/win>
2. Run the installer — default options are fine for everything.
3. Open a **new** PowerShell window (an already-open one won't see the
   updated PATH) and confirm `git --version` works.

Set your identity once, if you haven't already used Git before:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

---

## Part 2 — Create the repository on GitHub

1. Go to <https://github.com/new> (sign in or create a free account first).
2. **Repository name:** `glassbox` (or whatever you prefer).
3. **Description:** something like *"An accountable multi-agent trading
   copilot for Binance Agent OS — a trading agent that can veto itself, and
   proves what it was thinking when it did."*
4. **Public** (required for a hackathon submission judges need to open).
5. **Do NOT tick** "Add a README file," "Add .gitignore," or "Choose a
   license" — the project already has all three, and letting GitHub create
   its own would conflict when you push.
6. Click **Create repository**.
7. GitHub shows you a page with commands. Copy the URL it gives you under
   "…or push an existing repository from the command line" — it looks like
   `https://github.com/<your-username>/glassbox.git`. Keep this page open.

---

## Part 3 — Push your local copy

From the folder containing the project (adjust the path to wherever you
extracted or cloned it):

```powershell
cd $HOME\Desktop\glassbox
```

### Before you do anything else: confirm nothing sensitive will be committed

```powershell
git status
```

If this is the very first time, `git` isn't initialised yet, so this will
error — that's expected, continue to the next step. If you've already been
running GlassBox from this folder, check that `.glassbox` (your ledger,
device key, and any stored Binance session tokens) is **not** listed as
something about to be committed. The repository ships with a `.gitignore`
that already excludes `.glassbox/`, `*.jsonl`, and `device.key` — this is
just a sanity check, not something you should need to fix.

### Initialise and push

```powershell
git init
git add .
git status
```

Read the output of `git status` once before committing. You should see
source files (`.py`, `.js`, `.html`, `.css`, `.md`) and **not** see
`device.key`, anything under `.glassbox`, or any `.jsonl` ledger file. If you
do see one of those, stop and check `.gitignore` before continuing — do not
commit a device key or a ledger file to a public repository.

```powershell
git commit -m "Initial commit: GlassBox — accountable agent trading for Binance Agent OS"
git branch -M main
git remote add origin https://github.com/<your-username>/glassbox.git
git push -u origin main
```

Replace `<your-username>` with your actual GitHub username, and the URL with
whatever GitHub showed you in Part 2 if it differs.

If this is your first push, Git may open a browser window asking you to
authorize — approve it, and the push continues automatically.

---

## Part 4 — Confirm it worked

1. Refresh your repository page: `https://github.com/<your-username>/glassbox`
2. Confirm the file list matches what's on your machine.
3. Confirm the **README renders** below the file list — you should see the
   headline, the screenshot table, and the badges. GitHub renders
   `README.md` automatically; if images look broken, check that
   `docs/screenshots/*.png` were actually committed (`git ls-files
   docs/screenshots` should list all 9).
4. Click into `docs/` and confirm all the markdown files are there:
   `ARCHITECTURE.md`, `NOVELTY.md`, `SECURITY.md`, `TESTING.md`,
   `TESTING_LINUX.md`, `WINDOWS_VERIFICATION.md`, `FEATURE_CHECKLIST.md`,
   `DEMO_SCRIPT.md`, `GITHUB_UPLOAD.md` (this file).

---

## Part 5 — Make it discoverable (optional but recommended for judging)

On your repository's main page:

1. Click the **⚙️ gear icon** next to "About" (top right of the file list).
2. Add **topics**: `binance`, `mcp`, `model-context-protocol`, `trading-agent`,
   `ai-agent`, `agentic-finance`. This helps anyone browsing GitHub by topic
   find it, and signals relevance to anyone reviewing hackathon submissions.
3. Tick **"Use your README"** if prompted, so the description auto-syncs.
4. Optionally add the deployed dashboard URL if you're hosting one, in the
   "Website" field.

### Verify the license is recognised

GitHub auto-detects `LICENSE` and shows an "MIT License" badge near the file
list. If it doesn't appear within a minute of pushing, confirm the file is
named exactly `LICENSE` (no extension) at the repository root.

---

## Part 6 — The link to share

Once pushed, your shareable link is:

```
https://github.com/<your-username>/glassbox
```

For the hackathon's X/Twitter submission requirements (per the rules: a demo
video, a GitHub repo link, and the completed survey), this is the repo URL to
paste. If you also want a direct link to a specific document — for instance
pointing a judge straight at the novelty write-up — GitHub supports linking
straight to a file:

```
https://github.com/<your-username>/glassbox/blob/main/docs/NOVELTY.md
```

---

## Updating the repository later

Any time you make changes locally:

```powershell
cd $HOME\Desktop\glassbox
git add .
git commit -m "Describe what changed"
git push
```

---

## Troubleshooting

**`fatal: not a git repository`** — you're not in the right folder, or
`git init` hasn't been run yet. `cd` to the project root and try again.

**`remote origin already exists`** — you already ran `git remote add origin`
once. Either skip that step, or if the URL was wrong:
```powershell
git remote set-url origin https://github.com/<your-username>/glassbox.git
```

**`Permission denied` / authentication fails on push** — GitHub no longer
accepts a plain password for `git push`. Either let the browser-based
authorization complete (the default with recent Git for Windows), or set up a
[personal access token](https://github.com/settings/tokens) and use that as
the password when prompted.

**Repository looks empty on GitHub after push** — check you pushed the
right branch: `git branch -M main` before `git push -u origin main` ensures
your local branch is actually named `main`, matching what GitHub expects by
default.

**Accidentally committed `device.key` or a ledger file** — remove it from
version control and rotate it:
```powershell
git rm --cached backend\device.key   # adjust path if different
git commit -m "Remove accidentally committed device key"
git push
```
Then delete `%USERPROFILE%\.glassbox\device.key` locally so a fresh one is
generated on next run — the exposed key only ever signed local audit records
and cannot move funds, but treat it as compromised anyway.
