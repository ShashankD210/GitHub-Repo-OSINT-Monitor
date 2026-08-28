.PHONY: help setup generate-env login logout build build-webgui prepare-dirs install run watch dashboard run-all watch-all dashboard-all webgui clean uninstall test

REPO ?= owner/name
REPOS_FILE ?= repos.txt
INSTANCE := $(shell echo $(REPO) | tr '/' '-')
INSTALL_DIR := /opt/github-osint-monitor
STATE_DIR := $(CURDIR)/state
DASHBOARD_DIR := $(CURDIR)/dashboards
LOG_DIR := $(CURDIR)/logs
VENV := $(CURDIR)/venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
MONITOR := $(CURDIR)/monitor.py
WEBGUI := $(CURDIR)/webgui.py

help:
	@echo "GitHub OSINT Monitor — available targets:"
	@echo ""
	@echo "  make setup                     Create venv and install dependencies"
	@echo "  make generate-env              Generate .env with random placeholder values"
	@echo "  make login                     Interactively save GitHub PAT"
	@echo "  make logout                    Remove saved GitHub PAT"
	@echo "  make build                     Build standalone Linux executable (PyInstaller)"
	@echo "  make build-webgui              Build web GUI standalone executable"
	@echo "  make run REPO=owner/name       One-off check (prints to console)"
	@echo "  make watch REPO=owner/name     Continuous monitoring (Ctrl+C to stop)"
	@echo "  make dashboard REPO=owner/name Generate dashboard.html"
	@echo "  make run-all REPOS_FILE=file   Run all repos from file (one owner/name per line)"
	@echo "  make watch-all REPOS_FILE=file Continuous monitoring for all repos"
	@echo "  make dashboard-all REPOS_FILE=file Generate dashboards for all repos"
	@echo "  make webgui [PORT=8080]        Start web GUI dashboard"
	@echo "  make test                      Functional test with public repo"
	@echo "  make clean                     Remove venv, logs, state, dashboards, dist"
	@echo "  make install REPO=owner/name   Full systemd timer installation"
	@echo "  make uninstall REPO=owner/name Remove systemd unit and timer"
	@echo ""
	@echo "Env vars: GITHUB_TOKEN, SLACK_WEBHOOK_URL, SMTP_USER, SMTP_PASS"

setup:
	@echo "==> Setting up Python virtual environment..."
	python3 -m venv "$(VENV)"
	"$(PIP)" install --upgrade pip
	"$(PIP)" install -r "$(CURDIR)/requirements.txt"
	mkdir -p "$(STATE_DIR)" "$(DASHBOARD_DIR)" "$(LOG_DIR)"
	@echo "==> Done. Venv at $(VENV), directories created."

generate-env:
	@echo "==> Generating .env with random placeholder values..."
	@test ! -f .env || (echo "Error: .env already exists. Remove it first." && exit 1)
	bash "$(CURDIR)/scripts/generate-test-env.sh"

login:
	@echo "==> Logging in to GitHub..."
	"$(PYTHON)" "$(MONITOR)" --login

logout:
	@echo "==> Logging out from GitHub..."
	"$(PYTHON)" "$(MONITOR)" --logout

build:
	@echo "==> Building standalone Linux executable..."
	"$(PIP)" install pyinstaller
	"$(VENV)/bin/pyinstaller" --onefile --name github-osint-monitor "$(MONITOR)"
	@echo "==> Done. Binary at dist/github-osint-monitor"
	@echo "==> Note: For web GUI, run from source: make webgui"

build-webgui:
	@echo "==> Building web GUI standalone executable..."
	"$(PIP)" install pyinstaller
	"$(VENV)/bin/pyinstaller" --onefile \
		--name github-osint-monitor-webgui \
		--add-data "webgui/templates:templates" \
		--add-data "webgui/static:static" \
		"$(WEBGUI)"
	@echo "==> Done. Binary at dist/github-osint-monitor-webgui"

webgui:
	@echo "==> Starting web GUI..."
	"$(PYTHON)" "$(WEBGUI)" $(WEBGUI_ARGS)

install: setup
	@echo "==> Installing systemd timer for $(REPO) (instance: $(INSTANCE))"
	@test "$(REPO)" != "owner/name" || (echo "Error: set REPO=owner/name" && exit 1)
	sudo mkdir -p "$(INSTALL_DIR)"/{state,dashboards,venv}
	sudo mkdir -p /var/log/github-osint-monitor
	sudo cp "$(MONITOR)" "$(INSTALL_DIR)/monitor.py"
	sudo cp "$(CURDIR)/requirements.txt" "$(INSTALL_DIR)/requirements.txt"
	sudo cp deploy/github-osint-monitor@.service /etc/systemd/system/
	sudo cp deploy/github-osint-monitor@.timer /etc/systemd/system/
	sudo python3 -m venv "$(INSTALL_DIR)/venv"
	sudo "$(INSTALL_DIR)/venv/bin/pip" install -r "$(INSTALL_DIR)/requirements.txt"
	if [ ! -f "$(INSTALL_DIR)/.env" ]; then \
		sudo cp deploy/.env.example "$(INSTALL_DIR)/.env"; \
		sudo sed -i "s|REPO=owner/name|REPO=$(REPO)|" "$(INSTALL_DIR)/.env"; \
		echo "==> Wrote $(INSTALL_DIR)/.env — EDIT IT to add your GITHUB_TOKEN and SLACK_WEBHOOK_URL."; \
	else \
		echo "==> $(INSTALL_DIR)/.env already exists, leaving it as-is."; \
	fi
	sudo systemctl daemon-reload
	sudo systemctl enable --now "github-osint-monitor@$(INSTANCE).timer"
	@echo "==> Done."
	@echo "    systemctl status github-osint-monitor@$(INSTANCE).timer"
	@echo "    journalctl -u github-osint-monitor@$(INSTANCE).service -f"
	@echo "    tail -f /var/log/github-osint-monitor/$(INSTANCE).log"
	@echo "    open $(INSTALL_DIR)/dashboards/$(INSTANCE).html"

prepare-dirs:
	mkdir -p "$(STATE_DIR)" "$(DASHBOARD_DIR)" "$(LOG_DIR)"

run: prepare-dirs
	@test "$(REPO)" != "owner/name" || (echo "Error: set REPO=owner/name" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repo "$(REPO)" --once \
		--state-file "$(STATE_DIR)/$(INSTANCE).json" \
		--dashboard "$(DASHBOARD_DIR)/$(INSTANCE).html"

watch: prepare-dirs
	@test "$(REPO)" != "owner/name" || (echo "Error: set REPO=owner/name" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repo "$(REPO)" --interval 900 \
		--state-file "$(STATE_DIR)/$(INSTANCE).json" \
		--dashboard "$(DASHBOARD_DIR)/$(INSTANCE).html"

dashboard: prepare-dirs
	@test "$(REPO)" != "owner/name" || (echo "Error: set REPO=owner/name" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repo "$(REPO)" --once \
		--state-file "$(STATE_DIR)/$(INSTANCE).json" \
		--dashboard "$(DASHBOARD_DIR)/$(INSTANCE).html"

run-all: setup prepare-dirs
	@test -f "$(REPOS_FILE)" || (echo "Error: REPOS_FILE=$(REPOS_FILE) not found" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repos-file "$(REPOS_FILE)" --once

watch-all: setup prepare-dirs
	@test -f "$(REPOS_FILE)" || (echo "Error: REPOS_FILE=$(REPOS_FILE) not found" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repos-file "$(REPOS_FILE)" --interval 900

dashboard-all: setup prepare-dirs
	@test -f "$(REPOS_FILE)" || (echo "Error: REPOS_FILE=$(REPOS_FILE) not found" && exit 1)
	"$(PYTHON)" "$(MONITOR)" --repos-file "$(REPOS_FILE)" --once

test: setup
	@echo "==> Running functional test with public repo..."
	"$(PYTHON)" "$(MONITOR)" --repo octocat/Hello-World --once \
		--state-file /tmp/test_monitor_state.json \
		--dashboard /tmp/test_dashboard.html \
		--max-commits-scan 5

clean:
	@echo "==> Cleaning generated files..."
	rm -rf "$(VENV)"
	rm -rf "$(STATE_DIR)"
	rm -rf "$(DASHBOARD_DIR)"
	rm -rf "$(LOG_DIR)"
	rm -f monitor_state.json dashboard.html
	rm -rf __pycache__
	rm -rf dist build *.spec
	rm -f repos.txt
	rm -f "$(MONITOR).spec"
	@echo "==> Done."

uninstall:
	@test "$(REPO)" != "owner/name" || (echo "Error: set REPO=owner/name" && exit 1)
	sudo systemctl disable --now "github-osint-monitor@$(INSTANCE).timer" || true
	sudo rm -f /etc/systemd/system/github-osint-monitor@$(INSTANCE).service
	sudo rm -f /etc/systemd/system/github-osint-monitor@$(INSTANCE).timer
	sudo systemctl daemon-reload
	@echo "==> Removed systemd units for $(REPO)."
