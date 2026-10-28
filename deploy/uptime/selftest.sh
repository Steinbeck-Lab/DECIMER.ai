#!/usr/bin/env bash
# Self-test of uptime-check.sh against a stub server on 127.0.0.1 (nothing real is probed, nothing is sent anywhere):
# healthy -> silent; failing -> one alert after FAILS_BEFORE_ALERT runs, a reminder, a recovery message; unreachable; no channel;
# a hook that fails must not break the check. Run: deploy/uptime/selftest.sh
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d); trap 'kill $srv 2>/dev/null; rm -rf "$tmp"' EXIT
PASS=0 FAIL=0
t() { local d=$1; shift; if "$@" > /dev/null 2>&1; then PASS=$((PASS + 1)); echo "  PASS  $d"; else FAIL=$((FAIL + 1)); echo "  FAIL  $d"; fi; }

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
cat > "$tmp/stub.py" <<PY
import http.server, os
MODE = "$tmp/mode"
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        mode = open(MODE).read().strip() if os.path.exists(MODE) else "ok"
        if mode.startswith("legacy"):
            ok = {"/": b"<html>DECIMER Web Application</html>", "/about": b"about", "/impressum": b"impressum"}
            if mode == "legacy-broken": ok.pop("/about")
            body = ok.get(self.path, b"not found"); code = 200 if self.path in ok else 404
            self.send_response(code)
            if mode == "legacy-proxied": self.send_header("Set-Cookie", "decimer_session=x; path=/")   # a reverse proxy may strip X-Powered-By
            else: self.send_header("X-Powered-By", "PHP/8.3.36")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path == "/ping": body, code = b"pong", 200
        elif mode == "down": body, code = b'{"detail":"unavailable"}', 503
        elif mode == "html-only" and self.path == "/api/engines": body, code = b"{}", 502
        elif self.path == "/readyz": body, code = b'{"status":"ready"}', 200
        elif self.path == "/": body, code = b'<html><body><div id="root"></div></body></html>', 200
        elif self.path == "/api/engines": body, code = b'{"engines":[]}', 200
        else: body, code = b"", 404
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
http.server.ThreadingHTTPServer(("127.0.0.1", $port), H).serve_forever()
PY
python3 "$tmp/stub.py" & srv=$!
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$port/ping" > /dev/null 2>&1 && break; sleep 0.2; done

cat > "$tmp/hook.sh" <<EOF2
#!/usr/bin/env bash
{ echo "\$1|\$2"; cat; } >> "$tmp/alerts"
EOF2
chmod +x "$tmp/hook.sh"
export STATE_DIR=$tmp/state ALERT_CMD=$tmp/hook.sh HEARTBEAT_URL=http://127.0.0.1:$port/ping ERROR_DASHBOARD_URL=https://glitchtip.example/org/decimer FAILS_BEFORE_ALERT=2 REMIND_MINUTES=60
run() { "$here/uptime-check.sh" "http://127.0.0.1:$port" > "$tmp/out" 2>&1; }
alerts() { local n; n=$(grep -c "^$1|" "$tmp/alerts" 2> /dev/null); echo "${n:-0}"; }

echo "healthy"
t "all probes pass: exit 0, no alert, summary logged" bash -c "'$here/uptime-check.sh' http://127.0.0.1:$port 2>&1 | grep -q ' OK http://127.0.0.1:$port mode=new readyz=200.*home=200.*engines=200'"
t "no alert file yet" bash -c "[ ! -e $tmp/alerts ]"

echo "failing"
echo down > "$tmp/mode"
run; rc1=$?
t "first failed run: exit 1, NO alert yet" bash -c "[ $rc1 = 1 ] && [ \$(cat $tmp/state/fails) = 1 ] && [ ! -e $tmp/alerts ]"
run
t "second failed run: exactly one 'down' alert, with the summary and the dashboard pointer" bash -c "[ $(alerts down) = 1 ] && grep -q 'readyz=FAIL(503)' $tmp/alerts && grep -q 'Errors: https://glitchtip.example' $tmp/alerts"
run; run
t "further failed runs stay silent (still one alert)" bash -c "[ $(alerts down) = 1 ] && [ $(alerts reminder) = 0 ]"
REMIND_MINUTES=0 run
t "with REMIND_MINUTES=0 the next failed run sends a reminder" bash -c "[ $(alerts reminder) = 1 ]"

echo "recovery"
echo ok > "$tmp/mode"
run; rc=$?
t "first healthy run: exit 0, one 'recovered' alert, state cleared" bash -c "[ $rc = 0 ] && [ $(alerts recovered) = 1 ] && [ ! -e $tmp/state/fails ] && [ ! -e $tmp/state/alerted ]"
run
t "healthy again: no second recovery message" bash -c "[ $(alerts recovered) = 1 ]"
echo html-only > "$tmp/mode"; run; run
t "one failing probe out of three is an outage too (engines=FAIL(502))" bash -c "[ $(alerts down) = 2 ] && grep -q 'engines=FAIL(502)' $tmp/alerts && grep -q 'readyz=200' $tmp/alerts"
echo ok > "$tmp/mode"; run

echo "legacy mode (the earlier Laravel version of decimer.ai: no /readyz, X-Powered-By: PHP)"
echo legacy > "$tmp/mode"; rm -rf "$tmp/state" "$tmp/alerts"
t "auto-detects legacy and passes: mode=legacy home=200 about=200 impressum=200, no alert" bash -c "'$here/uptime-check.sh' http://127.0.0.1:$port 2>&1 | grep -q ' OK .* mode=legacy home=200.*about=200.*impressum=200' && [ ! -e $tmp/alerts ]"
echo legacy-proxied > "$tmp/mode"; rm -rf "$tmp/state"
t "legacy behind the proxy (no X-Powered-By, only the session cookie and title) is still detected" bash -c "'$here/uptime-check.sh' http://127.0.0.1:$port 2>&1 | grep -q ' OK .* mode=legacy'"
echo legacy-broken > "$tmp/mode"; run; run
t "a legacy page that fails is an outage too (about=FAIL(404)): one alert" bash -c "[ $(alerts down) = 1 ] && grep -q 'about=FAIL(404)' $tmp/alerts && grep -q 'mode=legacy' $tmp/alerts"
echo ok > "$tmp/mode"; run
t "the new site is auto-detected again after the cutover (mode=new), recovery sent" bash -c "grep -q 'mode=new' $tmp/out && [ $(alerts recovered) = 1 ]"
t "UPTIME_MODE=legacy against the new site fails (forced mode is honoured)" bash -c "UPTIME_MODE=legacy '$here/uptime-check.sh' http://127.0.0.1:$port 2>&1 | grep -q ' FAIL .* mode=legacy'"
rm -rf "$tmp/state" "$tmp/alerts"

echo "unreachable, and no channel"
kill $srv; wait $srv 2>/dev/null
rm -rf "$tmp/state" "$tmp/alerts"
HEARTBEAT_URL="" run; HEARTBEAT_URL="" run
t "connection refused: FAIL with curl's reason, alert sent" bash -c "[ $(alerts down) = 1 ] && grep -qi 'readyz=FAIL(000, curl' $tmp/alerts"
rm -rf "$tmp/state"
ALERT_CMD="" ALERT_EMAIL="" HEARTBEAT_URL="" run; ALERT_CMD="" ALERT_EMAIL="" HEARTBEAT_URL="" run
t "no channel configured: says so, exit 1, does not crash" bash -c "grep -q 'no alert channel configured' $tmp/out"
rm -rf "$tmp/state"
printf '#!/usr/bin/env bash\nexit 7\n' > "$tmp/bad-hook.sh"; chmod +x "$tmp/bad-hook.sh"
ALERT_CMD=$tmp/bad-hook.sh HEARTBEAT_URL="" run; ALERT_CMD=$tmp/bad-hook.sh HEARTBEAT_URL="" run
t "a hook that fails is logged and the check still exits 1 normally" bash -c "grep -q 'alert hook failed' $tmp/out"

echo "== uptime-check selftest: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
