#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

if [ -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE already exists. Remove it first if you want to regenerate."
    exit 1
fi

random_token() {
    python3 -c "import secrets; print(secrets.token_hex(${1:-24}))"
}

cat > "$ENV_FILE" <<EOF
# Auto-generated test environment file.
# These are RANDOM PLACEHOLDER VALUES for local testing only.
# Replace them with real values from the respective services before production use.

REPO=owner/name

# GitHub Personal Access Token
# Create one at: https://github.com/settings/tokens
GITHUB_TOKEN=ghp_$(random_token 20)

# Slack Incoming Webhook URL
# Create one at: https://api.slack.com/messaging/webhooks
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T$(random_token 4)/B$(random_token 4)/$(random_token 24)

# Polling interval in seconds (used by Makefile / systemd timer)
INTERVAL=900

# Email alerts (optional — all four must be set to enable)
SMTP_USER=test-$(random_token 8)@example.com
SMTP_PASS=$(random_token 16)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
EMAIL_TO=test-$(random_token 8)@example.com
EOF

chmod 600 "$ENV_FILE"
echo "==> Generated $ENV_FILE with random placeholder values."
echo "==> IMPORTANT: Edit this file and replace placeholders with real credentials before use."
