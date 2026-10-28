#!/usr/bin/env bash
# shellcheck disable=SC2034
# EXAMPLE alert hook for uptime-check.sh: copy it, keep ONE branch, point ALERT_CMD at the copy. Nothing here is in use.
# Called as:  alert-hook.sh <event> "<subject>"   (event: down | reminder | recovered), the message on stdin.
# A server often has no mail command, so mail needs an SMTP relay (option 2).
set -euo pipefail
event=$1 subject=$2
text=$(cat)

# --- 1. a file anyone can tail (always works; a start, not a channel)
# printf '%s %s\n%s\n\n' "$(date -u +%FT%TZ)" "$subject" "$text" >> "$HOME/.local/state/decimer-uptime/alerts.log"

# --- 2. e-mail through an SMTP relay, with curl (no mail command needed). Put the password in a file readable only by you.
# printf 'From: decimer-uptime <noreply@example.org>\nTo: you@example.org\nSubject: %s\n\n%s\n' "$subject" "$text" |
#   curl -sS --ssl-reqd --url 'smtp://smtp.example.org:587' --user "uptime:$(cat "$HOME/.config/decimer-uptime/smtp-password")" \
#        --mail-from noreply@example.org --mail-rcpt you@example.org -T -

# --- 3. ntfy (a push notification to a phone; self-hosted or ntfy.sh)
# curl -sS -H "Title: $subject" -H "Priority: $([ "$event" = recovered ] && echo default || echo high)" -d "$text" https://ntfy.example.org/decimer-uptime

# --- 4. a chat webhook (Slack-compatible JSON; Mattermost, Matrix bridges and others accept it)
# curl -sS -H 'Content-Type: application/json' -d "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1] + "\n" + sys.argv[2]}))' "$subject" "$text")" https://chat.example.org/hooks/XXXX
