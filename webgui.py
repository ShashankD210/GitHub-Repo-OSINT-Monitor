#!/usr/bin/env python3
"""
Web GUI for GitHub OSINT Monitor
==================================
Serves a browser-based dashboard for monitoring repositories.

Usage:
    python webgui.py [--port 8080] [--repos-file repos.txt] [--token GH_TOKEN]

Then open http://localhost:8080 in your browser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, request, redirect, url_for

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from monitor import (
    GitHubClient,
    GITHUB_API,
    load_state,
    save_state,
    run_check,
    render_dashboard,
    load_saved_token,
    save_token,
    clear_saved_token,
    SECRET_PATTERNS,
)

# PyInstaller compatibility: when bundled, templates/static are extracted to _MEIPATH
if hasattr(sys, '_MEIPASS'):
    BASE_DIR = Path(sys._MEIPASS)
    TEMPLATE_DIR = BASE_DIR / "webgui" / "templates"
    STATIC_DIR = BASE_DIR / "webgui" / "static"
else:
    BASE_DIR = PROJECT_ROOT
    TEMPLATE_DIR = PROJECT_ROOT / "webgui" / "templates"
    STATIC_DIR = PROJECT_ROOT / "webgui" / "static"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
)

# Default state
repos: list[str] = []
token: str | None = None
client: GitHubClient | None = None
state_dir = BASE_DIR / "state"
dashboard_dir = BASE_DIR / "dashboards"
state_dir.mkdir(exist_ok=True)
dashboard_dir.mkdir(exist_ok=True)


def get_client() -> GitHubClient:
    global client
    if client is None:
        client = GitHubClient(token=token)
    return client


def repo_paths(repo_full: str) -> tuple[Path, Path]:
    instance = repo_full.replace("/", "-")
    return state_dir / f"{instance}.json", dashboard_dir / f"{instance}.html"


def run_repo(repo_full: str) -> dict:
    owner, repo = repo_full.split("/", 1)
    state_path, dashboard_path = repo_paths(repo_full)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)

    state = load_state(state_path)
    is_first_run = not state
    try:
        events, findings, state = run_check(
            get_client(), owner, repo, state,
            scan_secrets=True,
            max_commits_scan=15,
            track_traffic=False,
        )
    except (RuntimeError, Exception) as e:
        return {
            "repo": repo_full,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

    save_state(state_path, state)

    render_dashboard(
        dashboard_path, repo_full, state.get("metrics", {}), events,
        traffic_metrics=state.get("traffic_metrics"),
        top_referrers=state.get("top_referrers"),
    )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "repo": repo_full,
        "timestamp": ts,
        "is_first_run": is_first_run,
        "baseline": {
            "stars": state.get("metrics", {}).get("stars"),
            "forks": state.get("metrics", {}).get("forks"),
        } if is_first_run else None,
        "events_count": len(events),
        "events": [str(e) for e in events],
        "secrets_count": len(findings.get("secrets", [])),
        "secrets": findings.get("secrets", []),
        "metrics": state.get("metrics", {}),
        "traffic_metrics": state.get("traffic_metrics"),
        "top_referrers": state.get("top_referrers"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", repos=repos, title="GitHub OSINT Monitor")


@app.route("/api/repos")
def api_repos():
    data = []
    for repo_full in repos:
        state_path, dashboard_path = repo_paths(repo_full)
        state = load_state(state_path)
        data.append({
            "name": repo_full,
            "metrics": state.get("metrics", {}),
            "last_checked": state.get("last_checked"),
            "dashboard_url": f"/dashboards/{repo_full.replace('/', '-')}.html",
        })
    return jsonify(data)


@app.route("/api/run", methods=["POST"])
def api_run():
    payload = request.get_json(silent=True) or {}
    repo_full = payload.get("repo")
    if not repo_full:
        return jsonify({"error": "repo is required"}), 400
    if repo_full not in repos:
        repos.append(repo_full)
    result = run_repo(repo_full)
    return jsonify(result)


@app.route("/api/run-all", methods=["POST"])
def api_run_all():
    results = []
    for repo_full in list(repos):
        results.append(run_repo(repo_full))
    return jsonify(results)


@app.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    token_value = payload.get("token", "").strip()
    if not token_value:
        return jsonify({"error": "token is required"}), 400
    save_token(token_value)
    global client
    client = None
    return jsonify({"status": "ok", "message": "Token saved."})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    clear_saved_token()
    global client
    client = None
    return jsonify({"status": "ok", "message": "Token removed."})


@app.route("/api/status")
def api_status():
    return jsonify({
        "repos_count": len(repos),
        "repos": repos,
        "token_loaded": token is not None,
        "saved_token_loaded": load_saved_token() is not None,
    })


@app.route("/api/user/repos")
def api_user_repos():
    client = get_client()
    if not client:
        return jsonify({"error": "No token loaded. Please login first."}), 401
    
    try:
        resp = client.session.get(
            f"{GITHUB_API}/user/repos",
            params={"per_page": 100, "type": "all"},
            timeout=30,
        )
        if resp.status_code == 401:
            return jsonify({"error": "Invalid or expired token."}), 401
        if resp.status_code != 200:
            return jsonify({"error": f"GitHub API error: {resp.status_code}"}), resp.status_code
        data = resp.json()
        repo_list = [{"name": r["full_name"], "private": r["private"], "updated_at": r["updated_at"]} for r in data]
        return jsonify(repo_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/add", methods=["POST"])
def api_repos_add():
    payload = request.get_json(silent=True) or {}
    repo_list = payload.get("repos", [])
    if not repo_list:
        return jsonify({"error": "repos list is required"}), 400
    
    added = []
    for repo_full in repo_list:
        if repo_full not in repos:
            repos.append(repo_full)
            added.append(repo_full)
    
    return jsonify({"added": added, "total": len(repos)})


@app.route("/dashboards/<path:filename>")
def serve_dashboard(filename):
    return redirect(url_for("static", filename=f"../dashboards/{filename}"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    global repos, token

    parser = argparse.ArgumentParser(description="GitHub OSINT Monitor Web GUI")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--repos-file", help="Path to repos file")
    parser.add_argument("--token", default=None, help="GitHub PAT")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    token = args.token or load_saved_token()

    if args.repos_file:
        repos_path = Path(args.repos_file)
        if repos_path.exists():
            for line in repos_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    repos.append(line)

    if not repos:
        repos = ["octocat/Hello-World"]

    url = f"http://localhost:{args.port}"
    print(f"[info] Starting web GUI on {url}")
    print(f"[info] Monitoring {len(repos)} repo(s): {', '.join(repos)}")

    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
