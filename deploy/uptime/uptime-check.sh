#!/usr/bin/env bash
# Uptime check for decimer.ai: one run probes the public URL the way a visitor's browser would and alerts through a
# hook when it fails, and again when it recovers. Run it every minute from cron or a systemd timer (see below).
#
#   deploy/uptime/uptime-check.sh [BASE_URL]        BASE_URL defaults to https://decimer.ai: pass your own site's URL
#
# Probes (each must answer within TIMEOUT_SECONDS). UPTIME_MODE picks the set: `new`, `legacy`, or `auto` (the default: the site is
# the earlier Laravel version of decimer.ai ("legacy") when GET / carries its session cookie, `X-Powered-By: PHP` or its page
# title ("DECIMER Web Application"), so the same line in cron can watch a site while it is switched from the earlier version
# to this one, and the summary says which it saw: mode=legacy or mode=new).
#   new:     GET /readyz       200 and {"status":"ready"}: the edge reaches the API, which reaches Redis and can write the job volume.
#                              (The path is /readyz, not /api/readyz: the API's own routes live under /api/, its health routes do not.)
#            GET /             200 and the app's HTML (`id="root"`): the front door and the edge serve the page.
#            GET /api/engines  200: a real route under /api/, so the proxying of /api/ itself is covered, not only the special /readyz.
#   legacy:  GET /            200 and "DECIMER" in the page; GET /about and GET /impressum 200 (the Laravel site has no /readyz).
# It does NOT upload anything: the golden-set smoke (golden/smoke.sh) is the deep check, run by hand.
#
# Alerts: after FAILS_BEFORE_ALERT failed runs in a row (default 2: one blip is not an outage) it sends ONE alert, a reminder every
# REMIND_MINUTES (60) while it stays down, and one "recovered" message. The alert channel differs per site, so it is a hook:
#   ALERT_CMD         a command run as:  ALERT_CMD <event> "<subject>"   with the message body on stdin; event is
#                     down | reminder | recovered. See alert-hook.example.sh for mail, ntfy, Matrix, a chat webhook.
#   ALERT_EMAIL       optional: also mail this address with `mail`, if the machine has it (otherwise use the hook).
#   Always: a line on stderr (the journal or cron's mail) and syslog (`logger -t decimer-uptime`) when logger exists.
#   HEARTBEAT_URL     optional dead-man's switch: GET after every successful run (healthchecks.io style). A check that runs on the same
#                     machine cannot see that machine, the reverse proxy in front of it or the network being down; an external
#                     service that expects this ping can.
#   ERROR_DASHBOARD_URL  optional: added to every alert (the GlitchTip project page; deploy/README.md, "Error reports: GlitchTip").
# Other settings: TIMEOUT_SECONDS (10), UPTIME_MODE (auto), STATE_DIR (~/.local/state/decimer-uptime).
# Exit code: 0 all probes passed, 1 at least one failed (alert logic aside), 2 could not run.
#
# cron (no root needed):   * * * * *  /opt/decimer/deploy/uptime/uptime-check.sh >> $HOME/.local/state/decimer-uptime/cron.log 2>&1
# (/opt/decimer: where you cloned the repository)
# systemd timer: decimer-uptime.service + decimer-uptime.timer in this directory (deploy/README.md, "Uptime check").
set -uo pipefail

BASE_URL=${1:-${BASE_URL:-https://decimer.ai}}
BASE_URL=${BASE_URL%/}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-10}
FAILS_BEFORE_ALERT=${FAILS_BEFORE_ALERT:-2}
REMIND_MINUTES=${REMIND_MINUTES:-60}
STATE_DIR=${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/decimer-uptime}
ALERT_CMD=${ALERT_CMD:-}
ALERT_EMAIL=${ALERT_EMAIL:-}
HEARTBEAT_URL=${HEARTBEAT_URL:-}
ERROR_DASHBOARD_URL=${ERROR_DASHBOARD_URL:-}
command -v curl > /dev/null || { echo "uptime-check: curl not found" >&2; exit 2; }
mkdir -p "$STATE_DIR" || { echo "uptime-check: cannot create $STATE_DIR" >&2; exit 2; }
# Two runs at once (a slow probe, a one-minute timer) must not both alert.
exec 9> "$STATE_DIR/lock"
if command -v flock > /dev/null && ! flock -n 9; then echo "uptime-check: another run is in progress" >&2; exit 0; fi

now=$(date +%s)
body=$(mktemp); trap 'rm -f "$body"' EXIT
results=() failed=0

# probe NAME PATH EXPECT-REGEX-OF-BODY(or empty)
probe() {
    local name=$1 path=$2 want=$3 out code secs
    out=$(curl -sS -m "$TIMEOUT_SECONDS" -o "$body" -w '%{http_code} %{time_total}' "$BASE_URL$path" 2> "$body.err") || out="000 0"
    code=${out%% *}; secs=${out#* }
    if [ "$code" = 200 ] && { [ -z "$want" ] || grep -qE "$want" "$body"; }; then
        results+=("$name=200(${secs}s)")
    else
        failed=$((failed + 1))
        results+=("$name=FAIL($code$([ "$code" = 200 ] && echo ", unexpected body" || { [ -s "$body.err" ] && printf ', %s' "$(head -c 80 "$body.err" | tr '\n' ' ')"; }))")
    fi
    rm -f "$body.err"
}
mode=${UPTIME_MODE:-auto}
if [ "$mode" = auto ]; then
    # A reverse proxy may strip `X-Powered-By`, so the other signs are the earlier version's session cookie and its page title.
    curl -sS -m "$TIMEOUT_SECONDS" -o "$body" -D "$body.hdr" "$BASE_URL/" 2> /dev/null
    if tr -d '\r' < "$body.hdr" 2> /dev/null | grep -qiE '^(x-powered-by:.*PHP|set-cookie: decimer_session)' || grep -q 'DECIMER Web Application' "$body" 2> /dev/null; then mode=legacy; else mode=new; fi
    rm -f "$body.hdr"
fi
case $mode in
    legacy)
        probe home / 'DECIMER'
        probe about /about ''
        probe impressum /impressum '' ;;
    new)
        probe readyz /readyz '"status": *"ready"'
        probe home / 'id="root"'
        probe engines /api/engines '' ;;
    *) echo "uptime-check: UPTIME_MODE='$mode' is not auto, new or legacy" >&2; exit 2 ;;
esac
summary="$(date -u +%Y-%m-%dT%H:%M:%SZ) $([ "$failed" = 0 ] && echo OK || echo FAIL) $BASE_URL mode=$mode ${results[*]}"

log() { printf '%s\n' "$1" >&2; command -v logger > /dev/null && logger -t decimer-uptime -- "$1" || true; }

send_alert() { # EVENT SUBJECT BODY
    local event=$1 subject=$2 text=$3
    [ -n "$ERROR_DASHBOARD_URL" ] && text+=$'\n'"Errors: $ERROR_DASHBOARD_URL"
    log "ALERT $event: $subject"
    if [ -n "$ALERT_CMD" ]; then
        printf '%s\n' "$text" | $ALERT_CMD "$event" "$subject" || log "alert hook failed (exit $?): $ALERT_CMD"
    fi
    if [ -n "$ALERT_EMAIL" ]; then
        if command -v mail > /dev/null; then printf '%s\n' "$text" | mail -s "$subject" "$ALERT_EMAIL" || log "mail to $ALERT_EMAIL failed"
        else log "ALERT_EMAIL is set but this machine has no mail command: use ALERT_CMD"; fi
    fi
    [ -z "$ALERT_CMD$ALERT_EMAIL" ] && log "no alert channel configured (ALERT_CMD / ALERT_EMAIL): the alert above is only logged"
    return 0
}

fails=$(cat "$STATE_DIR/fails" 2> /dev/null || echo 0)
alerted=$(cat "$STATE_DIR/alerted" 2> /dev/null || echo 0)     # epoch of the last alert, 0 when none is open
since=$(cat "$STATE_DIR/down-since" 2> /dev/null || echo "$now")

if [ "$failed" = 0 ]; then
    log "$summary"
    if [ "$alerted" != 0 ]; then
        mins=$(( (now - since) / 60 ))
        send_alert recovered "decimer.ai is back (down about ${mins} min)" "$summary"
    fi
    rm -f "$STATE_DIR/fails" "$STATE_DIR/alerted" "$STATE_DIR/down-since"
    [ -n "$HEARTBEAT_URL" ] && { curl -fsS -m 10 -o /dev/null "$HEARTBEAT_URL" || log "heartbeat $HEARTBEAT_URL failed"; }
    exit 0
fi

fails=$((fails + 1))
[ "$fails" = 1 ] && echo "$now" > "$STATE_DIR/down-since"
echo "$fails" > "$STATE_DIR/fails"
log "$summary (failed run $fails of $FAILS_BEFORE_ALERT before an alert)"
if [ "$fails" -ge "$FAILS_BEFORE_ALERT" ]; then
    if [ "$alerted" = 0 ]; then
        send_alert down "decimer.ai is DOWN: $failed of ${#results[@]} probes failed" "$summary"
        echo "$now" > "$STATE_DIR/alerted"
    elif [ $(( now - alerted )) -ge $(( REMIND_MINUTES * 60 )) ]; then
        send_alert reminder "decimer.ai is STILL DOWN ($(( (now - since) / 60 )) min)" "$summary"
        echo "$now" > "$STATE_DIR/alerted"
    fi
fi
exit 1
