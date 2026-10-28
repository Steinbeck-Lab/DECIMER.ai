#!/usr/bin/env bash
# Validates the rendered front door with `nginx -t` in throwaway containers (no network, nothing running is touched):
#   1. the pinned nginx of the edge image (deploy/web.Dockerfile NGINX_IMAGE) with its stock nginx.conf, which is what
#      deploy/frontdoor/compose.yaml runs;
#   2. the nginx image and nginx.conf of an nginx container you already run, for the other way to use the file: loaded
#      into that container's conf.d (the option names say "legacy": the earlier Laravel version of decimer.ai was
#      served that way). Skipped, with a note, unless --legacy-nginx-conf (or LEGACY_NGINX_CONF) names the file.
# Each with the proxy list empty (the default) and set, so both branches of the template parse.
#   deploy/frontdoor/check.sh [--legacy-nginx-conf FILE] [--legacy-image IMAGE]
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
legacy_conf=${LEGACY_NGINX_CONF:-}
# The image defaults to the one NGINX_CONTAINER runs (if that is set), else nginx:alpine.
legacy_image=${LEGACY_NGINX_IMAGE:-}
if [ -z "$legacy_image" ] && [ -n "${NGINX_CONTAINER:-}" ]; then
    legacy_image=$(docker inspect -f '{{.Image}}' "$NGINX_CONTAINER" 2>/dev/null || true)
fi
legacy_image=${legacy_image:-nginx:alpine}
while [ $# -gt 0 ]; do
    case $1 in
        --legacy-nginx-conf) legacy_conf=${2:?}; shift 2 ;;
        --legacy-image) legacy_image=${2:?}; shift 2 ;;
        *) echo "check.sh: unknown argument '$1'" >&2; exit 2 ;;
    esac
done
pinned=$(sed -n 's/^ARG NGINX_IMAGE=//p' "$here/../web.Dockerfile")
[ -n "$pinned" ] || { echo "check.sh: cannot read NGINX_IMAGE from deploy/web.Dockerfile" >&2; exit 2; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
"$here/render.sh" --env-file /dev/null > "$tmp/plain.conf"
TRUSTED_PROXY_CIDR="192.0.2.10/32, 2001:db8::10" "$here/render.sh" --env-file /dev/null > "$tmp/proxy.conf"

fail=0
run() { # label image main-conf-or-empty conf
    local label=$1 image=$2 main=$3 conf=$4 out
    local mounts=(-v "$conf":/etc/nginx/conf.d/default.conf:ro)
    [ -n "$main" ] && mounts+=(-v "$main":/etc/nginx/nginx.conf:ro)
    if out=$(docker run --rm --network none "${mounts[@]}" --entrypoint nginx "$image" -t 2>&1); then
        echo "ok    $label: $(printf '%s' "$out" | tail -n1)"
    else
        echo "FAIL  $label"; printf '%s\n' "$out" | sed 's/^/      /'; fail=1
    fi
}
for variant in plain proxy; do
    run "pinned nginx ($variant)" "$pinned" "" "$tmp/$variant.conf"
    if [ -n "$legacy_conf" ] && [ -f "$legacy_conf" ]; then
        run "existing nginx + its nginx.conf ($variant)" "$legacy_image" "$legacy_conf" "$tmp/$variant.conf"
    else
        if [ -n "$legacy_conf" ]; then
            echo "skip  existing nginx ($variant): $legacy_conf not found"
        else
            echo "skip  existing nginx ($variant): no nginx.conf given (--legacy-nginx-conf FILE)"
        fi
    fi
done
# TRUSTED_PROXY_CIDR: networks are refused (a proxy is an address), also when several add up to everyone; addresses and small groups pass.
for entry in "0.0.0.0/0" "0.0.0.0/1,128.0.0.0/1" "10.0.0.0/8" "198.18.0.0/15" "2001:db8::/32" "1.2.3.4/" "1.2.3.4;" "a b"; do
    if TRUSTED_PROXY_CIDR="$entry" "$here/render.sh" --env-file /dev/null > /dev/null 2>&1; then echo "FAIL  render.sh accepted TRUSTED_PROXY_CIDR='$entry'"; fail=1
    else echo "ok    render.sh refuses TRUSTED_PROXY_CIDR='$entry'"; fi
done
for entry in "192.0.2.10" "192.0.2.10/32" "198.18.0.0/16" "192.0.2.0/24, 198.51.100.7" "2001:db8::10/128" "2001:db8::/48"; do
    # (captured first: `grep -q` closing the pipe early would make render.sh die of SIGPIPE, and pipefail would call that a failure)
    rendered=$(TRUSTED_PROXY_CIDR="$entry" "$here/render.sh" --env-file /dev/null 2> /dev/null) || rendered=
    if grep -q '^    set_real_ip_from ' <<< "$rendered"; then echo "ok    render.sh accepts TRUSTED_PROXY_CIDR='$entry'"
    else echo "FAIL  render.sh refused TRUSTED_PROXY_CIDR='$entry'"; fail=1; fi
done
# The only log line the front door writes names no address and no URI: exactly these four fields, and no other access_log.
fmt=$(grep -E '^log_format fd_failures' "$tmp/proxy.conf")
if [ "$fmt" = "log_format fd_failures '\$time_iso8601 \$status \$request_time \$upstream_status';" ] \
   && [ "$(grep -cE '^[[:space:]]*access_log ' "$tmp/proxy.conf")" = 1 ] \
   && grep -qE '^[[:space:]]*access_log /dev/stderr fd_failures if=\$fd_log_failure;' "$tmp/proxy.conf"; then
    echo "ok    the front door's only log line is '<time> <status> <request_time> <upstream_status>', for 429 and 5xx (no address, no URI)"
else
    echo "FAIL  the front door's access_log is not the four-field failure line"; fail=1
fi
# Nothing in the rendered file may add the headers the edge owns (named error locations set only no-store/nosniff/their own CSP).
if grep -nE 'add_header[[:space:]]+(Strict-Transport-Security|Cache-Control "(public|no-cache))' "$tmp/proxy.conf"; then
    echo "FAIL  the front door sets a header that the edge owns"; fail=1
else
    echo "ok    no header of the edge is duplicated (HSTS, public/no-cache Cache-Control)"
fi
exit $fail
