# GitHub Repo OSINT Monitor

A single-file Python tool that watches a GitHub repository — public or
private-with-a-token — and reports:

- **Activity** — new commits, new releases, new contributors
- **Security** — scans the *added* lines of each new commit for likely leaked
  credentials (AWS keys, API keys, GitHub/Slack tokens, private key blocks,
  JWTs, generic password assignments). This is a defensive scan (same idea as
  gitleaks/trufflehog) so a maintainer can rotate/remove a secret quickly.
- **Popularity** — stars, forks, watchers, open-issue count, and the delta
  since the last check

It only calls GitHub's public REST API — the same data you'd see browsing the
repo yourself — so it works with anonymous access on public repos, or a
personal access token for higher rate limits / private repos you already have
access to.

## Quick start (standalone binary)

The repository includes pre-built Linux executables in `dist/`:

- `dist/github-osint-monitor` — CLI-only binary (~12 MB)
- `dist/github-osint-monitor-webgui` — CLI + web dashboard binary (~14 MB)

No Python, pip, or venv required on the target machine:

```bash
# CLI mode
./dist/github-osint-monitor --repo owner/name --once
./dist/github-osint-monitor --repo owner/name --interval 900 --slack-webhook URL

# Web GUI mode
./dist/github-osint-monitor-webgui --port 8080 --repos-file repos.txt
# Then open http://localhost:8080
```

The binary accepts the same CLI flags as `monitor.py`. It reads secrets from
environment variables or an external `.env` file, and writes state/dashboards
to the paths you pass via `--state-file` and `--dashboard`.

**Monitor multiple repos** by passing `--repos-file` with one `owner/name` per line:

```bash
./dist/github-osint-monitor --repos-file repos.txt --once
./dist/github-osint-monitor --repos-file repos.txt --interval 900
```

## GitHub authentication

To avoid passing `--token` or `GITHUB_TOKEN` every time, you can save a token
interactively:

```bash
# Source mode
python monitor.py --login

# Standalone binary
./dist/github-osint-monitor --login
```

You will be prompted for your GitHub Personal Access Token (PAT). It is saved
locally at `~/.github-osint-monitor.json` with restricted permissions. After
logging in, all subsequent runs will use the saved token automatically.

To remove the saved token:

```bash
python monitor.py --logout
./dist/github-osint-monitor --logout
```

You can still override the saved token with `--token` or `GITHUB_TOKEN` if
needed.

## Web GUI dashboard

A browser-based dashboard is included for visual monitoring:

```bash
make webgui
# or directly:
python webgui.py --port 8080 --repos-file repos.txt
```

Then open http://localhost:8080 in your browser. The web GUI provides:

- **Dashboard** — cards for each repo showing stars, forks, watchers, issues
- **Run Now** — trigger a single repo check from the browser
- **Run All** — check all repos at once
- **Fetch All My Repos** — automatically import all repositories you have access to (requires login)
- **Login/Logout** — save or remove GitHub PAT from the web UI
- **Events feed** — see new commits, releases, contributors, and secret alerts
- **Dashboard links** — open per-repo HTML dashboards

The web GUI reads repos from `repos.txt` (one `owner/name` per line) and uses
the same state and dashboard directories as the CLI. After logging in, click
**Fetch All My Repos** to import all repositories you have access to, then
select which ones to monitor.

## Developer setup (source)

If you want to run from source or rebuild the binary:

```bash
make setup
make test
make build
```

For quick local testing without real credentials, generate a `.env` with random placeholders:

```bash
./scripts/generate-test-env.sh
# Then edit .env and replace placeholders with real values before production use.
```

Optional: create a GitHub [personal access token](https://github.com/settings/tokens)
(no scopes needed for public repos, just raises your rate limit from 60 to
5,000 requests/hour) and export it:

```bash
export GITHUB_TOKEN=ghp_xxxxxxxx
```

## Makefile targets

```bash
make help                      Show all available targets
make setup                     Create venv and install dependencies
make generate-env              Generate .env with random placeholder values
make login                     Interactively save GitHub PAT
make logout                    Remove saved GitHub PAT
make build                     Build standalone Linux executable (PyInstaller)
make run REPO=owner/name       One-off check (prints to console)
make watch REPO=owner/name     Continuous monitoring (Ctrl+C to stop)
make dashboard REPO=owner/name Generate dashboard.html
make run-all REPOS_FILE=file   Run all repos from file (one owner/name per line)
make watch-all REPOS_FILE=file Continuous monitoring for all repos
make dashboard-all REPOS_FILE=file Generate dashboards for all repos
make webgui [PORT=8080]        Start web GUI dashboard
make test                      Functional test with public repo
make clean                     Remove venv, logs, state, dashboards, dist
make install REPO=owner/name   Full systemd timer installation
make uninstall REPO=owner/name Remove systemd unit and timer
```

## Usage

**One-off check** (prints events, writes `dashboard.html`, updates state):

```bash
./dist/github-osint-monitor --repo anthropics/anthropic-sdk-python --once
# or from source:
python monitor.py --repo anthropics/anthropic-sdk-python --once
```

**Continuous monitoring** (checks every 15 minutes):

```bash
./dist/github-osint-monitor --repo anthropics/anthropic-sdk-python --interval 900
```

**With Slack alerts:**

```bash
./dist/github-osint-monitor --repo owner/name --interval 900 \
  --slack-webhook https://hooks.slack.com/services/XXX/YYY/ZZZ
```

**With email alerts:**

```bash
./dist/github-osint-monitor --repo owner/name --interval 900 \
  --email-to you@example.com \
  --smtp-host smtp.gmail.com --smtp-port 587 \
  --smtp-user you@gmail.com --smtp-pass "app-password"
```

**Disable secret scanning** (activity + popularity only, fewer API calls):

```bash
./dist/github-osint-monitor --repo owner/name --once --no-secret-scan
```

**With traffic analytics** (views/clones/referrers — only works for repos
*you* have push access to, since GitHub doesn't expose this for repos you
don't maintain):

```bash
./dist/github-osint-monitor --repo owner/name --once --token $GITHUB_TOKEN --track-traffic
```

**Monitor multiple repos** (batch mode):

Create a `repos.txt` file with one `owner/name` per line:

```
octocat/Hello-World
torvalds/linux
kubernetes/kubernetes
```

Then run:

```bash
# One-off check for all repos
./dist/github-osint-monitor --repos-file repos.txt --once

# Continuous monitoring for all repos (every 15 minutes)
./dist/github-osint-monitor --repos-file repos.txt --interval 900 \
  --slack-webhook https://hooks.slack.com/services/XXX/YYY/ZZZ
```

In multi-repo mode, state and dashboard files are auto-derived per repo:
- State: `state/owner-repo.json`
- Dashboard: `dashboards/owner-repo.html`

Or use the Makefile shortcuts:

```bash
make run-all REPOS_FILE=repos.txt
make watch-all REPOS_FILE=repos.txt
make dashboard-all REPOS_FILE=repos.txt
```

## Scheduling it for real

Rather than leaving a `--interval` loop running in a terminal, use cron or
systemd so it survives reboots.

### Option A — cron (simplest)

```cron
# crontab -e — run every 15 minutes. GITHUB_TOKEN / SLACK_WEBHOOK_URL can be
# set in crontab itself (crontab -e lets you add VAR=value lines above the
# job) or exported in a wrapper script — the binary picks them up from the
# environment automatically, no need to repeat them as flags.
*/15 * * * * cd /path/to/github-osint-monitor && ./dist/github-osint-monitor --repo owner/name --once >> monitor.log 2>&1
```

### Option B — systemd timer (recommended for servers)

The `deploy/` folder has a ready-to-use systemd unit, timer, `.env` template,
and installer:

```bash
cd deploy
cp .env.example .env      # then edit REPO / GITHUB_TOKEN / SLACK_WEBHOOK_URL
sudo ./install.sh owner/name
```

This installs the monitor to `/opt/github-osint-monitor`, sets up a timer
that runs a check every 15 minutes, and writes the dashboard to
`/opt/github-osint-monitor/dashboards/owner-name.html`. You can monitor
multiple repos by re-running `install.sh` with a different `owner/name` —
each gets its own systemd instance, state file, and dashboard.

Useful commands after install:

```bash
systemctl status github-osint-monitor@owner-name.timer   # is it scheduled?
journalctl -u github-osint-monitor@owner-name.service -f  # live logs
systemctl start github-osint-monitor@owner-name.service   # run a check now
```

To change the check frequency, edit `OnUnitActiveSec=15min` in
`/etc/systemd/system/github-osint-monitor@.timer`, then
`sudo systemctl daemon-reload && sudo systemctl restart github-osint-monitor@owner-name.timer`.

## Output

- **Console** — a timestamped list of new events each run. On the very first
  run for a repo, no "new commit/release/contributor" events are printed —
  the tool just records a baseline silently (you'd otherwise get a flood of
  everything as "new" the first time you point it at an active repo). Secret
  scanning still runs on that first pass, since an existing leaked credential
  is worth knowing about immediately.
- **`dashboard.html`** — a small self-contained HTML report with current
  star/fork/watcher/issue counts and the latest event feed; open it in a
  browser or serve it with `python -m http.server`
- **`monitor_state.json`** — the persisted "last seen" state (commit SHA,
  release IDs, contributor list, metric snapshot) so every run only reports
  what's *new*

Bad repo names, network errors, or GitHub API errors are caught and printed
as a single `[error] ...` line — they won't crash a `--interval` loop or a
scheduled run; it just tries again next cycle.

## Project layout

```
monitor.py               main script (all logic lives here)
requirements.txt
Makefile                  build/test/install automation
README.md
scripts/
  generate-test-env.sh    generate .env with random placeholders for testing
deploy/
  github-osint-monitor@.service   systemd oneshot unit (templated by instance)
  github-osint-monitor@.timer     systemd timer, default every 15 min
  .env.example                    copy to .env and fill in
  install.sh                      sudo ./install.sh owner/name
webgui/
  webgui.py               Flask web dashboard server
  templates/
    index.html            dashboard page
  static/
    css/style.css         styling
    js/app.js             frontend logic
dist/                     generated executables (gitignored)
  github-osint-monitor            standalone Linux CLI executable (~12 MB)
  github-osint-monitor-webgui     standalone Linux web GUI executable (~14 MB)
tests/
  test_basic.py           basic smoke tests
state/                    runtime state files (gitignored)
dashboards/               HTML dashboard outputs (gitignored)
logs/                     log files (gitignored)
```

Runtime directories (`state/`, `dashboards/`, `logs/`) and `.env` are gitignored
and created automatically on first run. Generated executables in `dist/` are
also gitignored; rebuild with `make build` and `make build-webgui`.

For a clean GitHub upload, a compressed archive is available:
`GITHUBMONITOR.tar.gz` at the project root. It excludes runtime artifacts like
`dist/`, `venv/`, `state/`, `dashboards/`, `logs/`, and build artifacts.

## Notes & limits

- Traffic analytics (`--track-traffic`) require a token with push access to
  the repo — GitHub only exposes clone/view counts to maintainers, so this
  only works for repos you own. It's excluded by default.
- Secret scanning is pattern/heuristic-based and will have false positives
  and false negatives — treat hits as "go look," not certainty.
- GitHub's unauthenticated rate limit is 60 requests/hour; a token raises
  this to 5,000/hour. Frequent polling of very active repos should use a
  token.
- Commit checks fetch the most recent 30 commits per run. If a repo receives
  more than 30 commits between two checks (e.g. you check daily on a very
  high-velocity repo), the oldest ones in that burst won't be individually
  reported — check more frequently (`--interval`/timer) on very active repos
  to avoid this.
