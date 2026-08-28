#!/usr/bin/env python3
"""
GitHub Repo OSINT Monitor
==========================
Tracks one or more public (or token-accessible) GitHub repositories for:
  - Activity: new commits, releases, contributors
  - Security: newly-introduced secrets/credentials in commit diffs (defensive scanning)
  - Popularity: stars, forks, watchers, and open-issue trends

All data comes from GitHub's public REST API (github.com/{owner}/{repo}) — nothing
here scrapes private data or bypasses access controls. It only sees what your
token (or anonymous access, for public repos) is already allowed to see.

Usage:
    python monitor.py --repo owner/name [--token GH_TOKEN] --once
    python monitor.py --repos-file repos.txt [--token GH_TOKEN] --once
    python monitor.py --repo owner/name --interval 900          # loop every 15 min
    python monitor.py --repo owner/name --slack-webhook URL
    python monitor.py --repo owner/name --dashboard report.html
    python monitor.py --login                                   # save GitHub token
    python monitor.py --logout                                  # remove saved token

State (last-seen commit SHA, release id, star/fork counts, contributor list) is
persisted to a JSON file so each run only reports *new* changes (a diff, not a
full dump).

Multi-repo mode:
    Pass --repos-file with one owner/name per line. State and dashboard paths
    are auto-derived per repo unless explicitly set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import getpass
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"
CONFIG_PATH = Path.home() / ".github-osint-monitor.json"

# --------------------------------------------------------------------------
# GitHub authentication helpers
# --------------------------------------------------------------------------
def load_saved_token() -> str | None:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return data.get("github_token")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_token(token: str):
    CONFIG_PATH.write_text(json.dumps({"github_token": token}, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def clear_saved_token():
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def prompt_and_save_token() -> str:
    try:
        token = getpass.getpass("Enter GitHub Personal Access Token: ").strip()
    except (EOFError, getpass.GetPassWarning):
        try:
            token = input("Enter GitHub Personal Access Token (input will be visible): ").strip()
        except EOFError:
            print("[error] Cannot read input in this environment. Use --token or GITHUB_TOKEN instead.", file=sys.stderr)
            sys.exit(1)
    if not token:
        print("[error] No token entered.", file=sys.stderr)
        sys.exit(1)
    save_token(token)
    print(f"[info] Token saved to {CONFIG_PATH}")
    return token

# --------------------------------------------------------------------------
# Secret-detection patterns (defensive use only — flags likely-leaked
# credentials in new commit diffs so a maintainer can rotate/remove them).
# --------------------------------------------------------------------------
SECRET_PATTERNS = {
    "AWS Access Key ID": re.compile(r"AKIA[0-9A-Z]{16}"),
    "AWS Secret Key (heuristic)": re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]"),
    "Generic API Key": re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    "GitHub Token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "Slack Token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Private Key Block": re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----"),
    "Generic Password Assignment": re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
    "JWT-looking token": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.session = requests.Session()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)

    def get(self, path: str, params: dict | None = None):
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        resp = self.session.get(url, params=params, timeout=20)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset")
            raise RuntimeError(f"GitHub API rate limit hit. Resets at epoch {reset}. "
                                f"Pass --token to raise your limit.")
        resp.raise_for_status()
        return resp.json()

    def repo_info(self, owner, repo):
        return self.get(f"/repos/{owner}/{repo}")

    def commits(self, owner, repo, since_iso=None, per_page=30):
        params = {"per_page": per_page}
        if since_iso:
            params["since"] = since_iso
        return self.get(f"/repos/{owner}/{repo}/commits", params=params)

    def commit_detail(self, owner, repo, sha):
        return self.get(f"/repos/{owner}/{repo}/commits/{sha}")

    def releases(self, owner, repo, per_page=10):
        return self.get(f"/repos/{owner}/{repo}/releases", params={"per_page": per_page})

    def contributors(self, owner, repo, per_page=100):
        return self.get(f"/repos/{owner}/{repo}/contributors", params={"per_page": per_page, "anon": "false"})

    def traffic_views(self, owner, repo):
        # Requires push access to the repo. 14-day rolling window, daily buckets.
        return self.get(f"/repos/{owner}/{repo}/traffic/views")

    def traffic_clones(self, owner, repo):
        # Requires push access to the repo. 14-day rolling window, daily buckets.
        return self.get(f"/repos/{owner}/{repo}/traffic/clones")

    def traffic_referrers(self, owner, repo):
        # Requires push access. Top 10 referrers over the last 14 days.
        return self.get(f"/repos/{owner}/{repo}/traffic/popular/referrers")


# --------------------------------------------------------------------------
# State persistence
# --------------------------------------------------------------------------
def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict):
    path.write_text(json.dumps(state, indent=2, default=str))


# --------------------------------------------------------------------------
# Core check
# --------------------------------------------------------------------------
def run_check(client: GitHubClient, owner: str, repo: str, state: dict, scan_secrets: bool, max_commits_scan: int, track_traffic: bool = False):
    events = []  # list of human-readable strings for alerting
    findings = {"secrets": []}

    info = client.repo_info(owner, repo)

    # --- Popularity ---
    prev = state.get("metrics", {})
    metrics = {
        "stars": info.get("stargazers_count", 0),
        "forks": info.get("forks_count", 0),
        "watchers": info.get("subscribers_count", info.get("watchers_count", 0)),
        "open_issues": info.get("open_issues_count", 0),
    }
    if prev:
        for key, label in [("stars", "Stars"), ("forks", "Forks"), ("watchers", "Watchers"), ("open_issues", "Open issues")]:
            delta = metrics[key] - prev.get(key, metrics[key])
            if delta != 0:
                sign = "+" if delta > 0 else ""
                events.append(f"[Popularity] {label}: {prev.get(key)} -> {metrics[key]} ({sign}{delta})")
    state["metrics"] = metrics

    # --- Activity: commits ---
    is_first_commit_check = "last_commit_sha" not in state and "last_commit_check" not in state
    since_iso = state.get("last_commit_check")
    commits = client.commits(owner, repo, since_iso=since_iso)
    # commits[] is newest-first. Walk it until we hit the last commit we already
    # reported, rather than just filtering that one sha out — this avoids
    # re-reporting older commits that the `since` timestamp filter (which has
    # second-level precision and can include boundary commits) lets through.
    last_seen_sha = state.get("last_commit_sha")
    new_commits = []
    for c in commits:
        if c["sha"] == last_seen_sha:
            break
        new_commits.append(c)

    for c in reversed(new_commits[:max_commits_scan]):  # oldest-first for readability
        sha = c["sha"]
        msg = c["commit"]["message"].splitlines()[0]
        author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        if not is_first_commit_check:
            events.append(f"[Activity] New commit {sha[:7]} by {author}: {msg}")

        if scan_secrets:
            detail = client.commit_detail(owner, repo, sha)
            for f in detail.get("files", []):
                patch = f.get("patch", "")
                if not patch:
                    continue
                # Only look at added lines
                added_lines = "\n".join(l for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
                for label, pattern in SECRET_PATTERNS.items():
                    m = pattern.search(added_lines)
                    if m:
                        finding = {
                            "commit": sha,
                            "file": f.get("filename"),
                            "type": label,
                            "snippet": m.group(0)[:12] + "…(redacted)",
                        }
                        findings["secrets"].append(finding)
                        events.append(f"[SECURITY ALERT] Possible {label} in {f.get('filename')} @ {sha[:7]}")

    if commits:
        state["last_commit_sha"] = commits[0]["sha"]
    state["last_commit_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Activity: releases ---
    is_first_release_check = "known_release_ids" not in state
    releases = client.releases(owner, repo)
    known_release_ids = set(state.get("known_release_ids", []))
    new_releases = [r for r in releases if r["id"] not in known_release_ids]
    if not is_first_release_check:
        for r in new_releases:
            events.append(f"[Activity] New release: {r.get('tag_name')} — {r.get('name') or '(untitled)'}")
    state["known_release_ids"] = list(known_release_ids | {r["id"] for r in releases})

    # --- Activity: contributors ---
    try:
        is_first_contributor_check = "known_contributors" not in state
        contributors = client.contributors(owner, repo)
        current_logins = {c["login"] for c in contributors if c.get("login")}
        known_logins = set(state.get("known_contributors", []))
        new_contributors = current_logins - known_logins
        if not is_first_contributor_check:
            for login in new_contributors:
                events.append(f"[Activity] New contributor: {login}")
        state["known_contributors"] = list(current_logins | known_logins)
    except requests.HTTPError:
        # Contributors endpoint can 204/403 on empty or huge repos; non-fatal
        pass

    # --- Traffic (owner/push-access only) ---
    if track_traffic:
        try:
            views = client.traffic_views(owner, repo)
            clones = client.traffic_clones(owner, repo)
            traffic_metrics = {
                "views_14d": views.get("count", 0),
                "unique_visitors_14d": views.get("uniques", 0),
                "clones_14d": clones.get("count", 0),
                "unique_cloners_14d": clones.get("uniques", 0),
            }
            prev_traffic = state.get("traffic_metrics", {})
            if prev_traffic:
                for key, label in [
                    ("views_14d", "Views (14d)"), ("unique_visitors_14d", "Unique visitors (14d)"),
                    ("clones_14d", "Clones (14d)"), ("unique_cloners_14d", "Unique cloners (14d)"),
                ]:
                    delta = traffic_metrics[key] - prev_traffic.get(key, traffic_metrics[key])
                    if delta != 0:
                        sign = "+" if delta > 0 else ""
                        events.append(f"[Traffic] {label}: {prev_traffic.get(key)} -> {traffic_metrics[key]} ({sign}{delta})")
            state["traffic_metrics"] = traffic_metrics

            try:
                referrers = client.traffic_referrers(owner, repo)
                top = ", ".join(f"{r['referrer']} ({r['count']})" for r in referrers[:5]) or "none"
                state["top_referrers"] = top
            except requests.HTTPError:
                pass
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                events.append("[Traffic] Skipped: token lacks push access to this repo (traffic stats require it).")
            else:
                events.append(f"[Traffic] Skipped: {e}")

    return events, findings, state


# --------------------------------------------------------------------------
# Alerting
# --------------------------------------------------------------------------
def send_slack_alert(webhook_url: str, repo_full: str, events: list[str]):
    if not events:
        return
    text = f"*GitHub OSINT Monitor — {repo_full}*\n" + "\n".join(f"• {e}" for e in events)
    try:
        requests.post(webhook_url, json={"text": text}, timeout=10)
    except requests.RequestException as e:
        print(f"[warn] Slack alert failed: {e}", file=sys.stderr)


def send_email_alert(smtp_host, smtp_port, smtp_user, smtp_pass, to_addr, repo_full, events: list[str]):
    if not events:
        return
    body = f"GitHub OSINT Monitor — {repo_full}\n\n" + "\n".join(f"- {e}" for e in events)
    msg = MIMEText(body)
    msg["Subject"] = f"[GitHub Monitor] {repo_full}: {len(events)} update(s)"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_addr], msg.as_string())
    except Exception as e:
        print(f"[warn] Email alert failed: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GitHub OSINT Monitor — {repo_full}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
  h1 {{ font-size: 1.5rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 32px; }}
  .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 14px; text-align: center; }}
  .card .num {{ font-size: 1.6rem; font-weight: 700; }}
  .card .label {{ font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: .04em; }}
  ul {{ padding-left: 0; list-style: none; }}
  li {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 0.95rem; }}
  li.security {{ background: #fff3f3; border-left: 3px solid #e11d48; padding-left: 10px; }}
  li.activity {{ border-left: 3px solid #2563eb; padding-left: 10px; }}
  li.popularity {{ border-left: 3px solid #16a34a; padding-left: 10px; }}
  .empty {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
  <h1>GitHub OSINT Monitor</h1>
  <div class="meta">Repository: <strong>{repo_full}</strong> &nbsp;|&nbsp; Generated: {generated_at}</div>

  <div class="grid">
    <div class="card"><div class="num">{stars}</div><div class="label">Stars</div></div>
    <div class="card"><div class="num">{forks}</div><div class="label">Forks</div></div>
    <div class="card"><div class="num">{watchers}</div><div class="label">Watchers</div></div>
    <div class="card"><div class="num">{open_issues}</div><div class="label">Open Issues</div></div>
  </div>

  {traffic_section}

  <h2>Recent Events</h2>
  <ul>
    {events_html}
  </ul>
</body>
</html>
"""


def render_dashboard(path: Path, repo_full: str, metrics: dict, events: list[str], traffic_metrics: dict | None = None, top_referrers: str | None = None):
    if events:
        rows = []
        for e in events:
            cls = "activity"
            if e.startswith("[SECURITY"):
                cls = "security"
            elif e.startswith("[Popularity]") or e.startswith("[Traffic]"):
                cls = "popularity"
            rows.append(f'<li class="{cls}">{e}</li>')
        events_html = "\n    ".join(rows)
    else:
        events_html = '<li class="empty">No new events since last check.</li>'

    if traffic_metrics:
        traffic_section = f"""
  <h2>Traffic (14-day rolling)</h2>
  <div class="grid">
    <div class="card"><div class="num">{traffic_metrics.get('views_14d', '—')}</div><div class="label">Views</div></div>
    <div class="card"><div class="num">{traffic_metrics.get('unique_visitors_14d', '—')}</div><div class="label">Unique Visitors</div></div>
    <div class="card"><div class="num">{traffic_metrics.get('clones_14d', '—')}</div><div class="label">Clones</div></div>
    <div class="card"><div class="num">{traffic_metrics.get('unique_cloners_14d', '—')}</div><div class="label">Unique Cloners</div></div>
  </div>
  <div class="meta">Top referrers: {top_referrers or 'n/a'}</div>
"""
    else:
        traffic_section = ""

    html = DASHBOARD_TEMPLATE.format(
        repo_full=repo_full,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        stars=metrics.get("stars", "—"),
        forks=metrics.get("forks", "—"),
        watchers=metrics.get("watchers", "—"),
        open_issues=metrics.get("open_issues", "—"),
        events_html=events_html,
        traffic_section=traffic_section,
    )
    path.write_text(html)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Monitor a GitHub repo (activity, security, popularity).")
    parser.add_argument("--repo", help="owner/name, e.g. torvalds/linux")
    parser.add_argument("--repos-file", help="Path to a file with one owner/name per line for batch monitoring")
    parser.add_argument("--token", default=None, help="GitHub PAT (or set GITHUB_TOKEN env var). Raises rate limits; not required for public repos.")
    parser.add_argument("--login", action="store_true", help="Interactively save a GitHub PAT for future runs")
    parser.add_argument("--logout", action="store_true", help="Remove saved GitHub PAT")
    parser.add_argument("--state-file", default="monitor_state.json", help="Where to persist last-seen state. In multi-repo mode, paths are auto-derived per repo unless this is explicitly set.")
    parser.add_argument("--interval", type=int, default=0, help="Seconds between checks. 0 = run once and exit.")
    parser.add_argument("--once", action="store_true", help="Force a single run even if --interval is set.")
    parser.add_argument("--no-secret-scan", action="store_true", help="Skip scanning new commit diffs for credentials.")
    parser.add_argument("--max-commits-scan", type=int, default=15, help="Max new commits to fetch+scan per run (API cost control).")
    parser.add_argument("--track-traffic", action="store_true", help="Also fetch views/clones/referrers. Requires a --token with push access to the repo (i.e. you own/maintain it).")
    parser.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL"))
    parser.add_argument("--dashboard", default="dashboard.html", help="Path to write the HTML dashboard each run. In multi-repo mode, paths are auto-derived per repo unless this is explicitly set.")
    # Email (all-or-nothing)
    parser.add_argument("--email-to")
    parser.add_argument("--smtp-host")
    parser.add_argument("--smtp-port", type=int, default=587)
    parser.add_argument("--smtp-user", default=os.environ.get("SMTP_USER"))
    parser.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS"))
    args = parser.parse_args()

    if args.login and args.logout:
        parser.error("Use either --login or --logout, not both")

    if args.login:
        prompt_and_save_token()
        print("[info] Login successful. You can now run monitor commands without --token.")
        sys.exit(0)

    if args.logout:
        clear_saved_token()
        print(f"[info] Saved token removed from {CONFIG_PATH}")
        sys.exit(0)

    if not args.repo and not args.repos_file:
        parser.error("Either --repo or --repos-file must be provided")
    if args.repo and args.repos_file:
        parser.error("Use either --repo or --repos-file, not both")

    repos = []
    if args.repos_file:
        repos_path = Path(args.repos_file)
        if not repos_path.exists():
            print(f"[error] repos file not found: {args.repos_file}", file=sys.stderr)
            sys.exit(1)
        for line in repos_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                repos.append(line)
        if not repos:
            print(f"[error] no repos found in {args.repos_file}", file=sys.stderr)
            sys.exit(1)
    else:
        repos = [args.repo]

    token = args.token or os.environ.get("GITHUB_TOKEN") or load_saved_token()
    client = GitHubClient(token=token)

    def repo_paths(repo_full: str) -> tuple[Path, Path]:
        instance = repo_full.replace("/", "-")
        state = Path(args.state_file) if args.repo else Path(f"state/{instance}.json")
        dashboard = Path(args.dashboard) if args.repo else Path(f"dashboards/{instance}.html")
        return state, dashboard

    def run_for_repo(repo_full: str):
        owner, repo = repo_full.split("/", 1)
        state_path, dashboard_path = repo_paths(repo_full)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_path.parent.mkdir(parents=True, exist_ok=True)

        state = load_state(state_path)
        is_first_run = not state
        try:
            events, findings, state = run_check(
                client, owner, repo, state,
                scan_secrets=not args.no_secret_scan,
                max_commits_scan=args.max_commits_scan,
                track_traffic=args.track_traffic,
            )
        except (RuntimeError, requests.RequestException) as e:
            print(f"[error] {repo_full}: {e}", file=sys.stderr)
            return

        save_state(state_path, state)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if is_first_run:
            print(f"[{ts}] {repo_full}: baseline established (stars={state.get('metrics', {}).get('stars')}, "
                  f"forks={state.get('metrics', {}).get('forks')}). Future runs will report changes from here.")
        elif events:
            print(f"\n=== {ts} — {len(events)} new event(s) for {repo_full} ===")
            for e in events:
                print(" ", e)
        else:
            print(f"[{ts}] {repo_full}: no changes.")

        if findings["secrets"]:
            print(f"  !! {len(findings['secrets'])} possible secret(s) found in {repo_full} — review and rotate immediately.")

        render_dashboard(
            dashboard_path, repo_full, state.get("metrics", {}), events,
            traffic_metrics=state.get("traffic_metrics") if args.track_traffic else None,
            top_referrers=state.get("top_referrers"),
        )

        if args.slack_webhook:
            send_slack_alert(args.slack_webhook, repo_full, events)
        if args.email_to and args.smtp_host and args.smtp_user and args.smtp_pass:
            send_email_alert(args.smtp_host, args.smtp_port, args.smtp_user, args.smtp_pass, args.email_to, repo_full, events)

    if args.interval > 0 and not args.once:
        print(f"Starting continuous monitor for {len(repos)} repo(s) every {args.interval}s. Ctrl+C to stop.")
        while True:
            for repo_full in repos:
                run_for_repo(repo_full)
            time.sleep(args.interval)
    else:
        for repo_full in repos:
            run_for_repo(repo_full)


if __name__ == "__main__":
    main()
