#!/usr/bin/env bash
# Renders deploy/frontdoor/decimer.ai.conf.template to stdout. Nothing is written anywhere and no container is touched.
#
#   deploy/frontdoor/render.sh [--env-file FILE] [--template FILE] > default.conf
#
# One variable per setting, taken from the environment first, then from the env file (default deploy/.env, read with
# grep: the file holds secrets and is never sourced), then the default:
#   TRUSTED_PROXY_CIDR          ""                          your reverse proxy's address(es), comma or space separated.
#                                                           Empty = trust nobody (safe), no limit of its own.
#   MAX_UPLOAD_MB               100                         the API's upload limit; this file allows 2 MB more
#   PROXY_READ_TIMEOUT_SECONDS  60                          the edge's value; this file waits 5 s longer
#   FRONTDOOR_EDGE_ORIGIN       http://decimer-next-edge:8080   where the edge answers on the docker network
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
template=$here/decimer.ai.conf.template
DECIMER_ENV_FILE=${DECIMER_ENV_FILE:-$here/../.env}

while [ $# -gt 0 ]; do
    case $1 in
        --env-file) DECIMER_ENV_FILE=${2:?--env-file needs a file}; shift 2 ;;
        --template) template=${2:?--template needs a file}; shift 2 ;;
        -h|--help) awk 'NR > 1 && /^# shellcheck/ { next } NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "render.sh: unknown argument '$1'" >&2; exit 2 ;;
    esac
done
# shellcheck source=envfile.sh
. "$here/envfile.sh"
[ -f "$template" ] || { echo "render.sh: template $template not found" >&2; exit 2; }

proxies=$(env_value TRUSTED_PROXY_CIDR "")
upload_mb=$(env_value MAX_UPLOAD_MB 100)
read_s=$(env_value PROXY_READ_TIMEOUT_SECONDS 60)
edge_origin=$(env_value FRONTDOOR_EDGE_ORIGIN http://decimer-next-edge:8080)

case $upload_mb in ''|*[!0-9]*|0) echo "render.sh: MAX_UPLOAD_MB='$upload_mb' is not a positive whole number of MB" >&2; exit 1 ;; esac
case $read_s in ''|*[!0-9.]*|.*|*.|*.*.*) echo "render.sh: PROXY_READ_TIMEOUT_SECONDS='$read_s' is not a number of seconds" >&2; exit 1 ;; esac
case $edge_origin in
    http://*[!A-Za-z0-9.:_-]*|http://) echo "render.sh: FRONTDOOR_EDGE_ORIGIN='$edge_origin' must be http://<host>[:port] with no path" >&2; exit 1 ;;
    http://*) ;;
    *) echo "render.sh: FRONTDOOR_EDGE_ORIGIN='$edge_origin' must start with http://" >&2; exit 1 ;;
esac
read_wait=$(awk -v s="$read_s" 'BEGIN { i = int(s); if (s > i) i++; if (i < 1) i = 1; print i + 5 }')

# The checks the edge's edge-entrypoint.sh applies to the same variable (it refuses only /0), and stricter ones.
set_real_ip=""
n=0
set -f
# shellcheck disable=SC2046
for cidr in $(printf '%s' "$proxies" | tr ',' ' '); do
    case $cidr in
        *[!0-9a-fA-F:./]*) echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' contains characters that are not part of an address" >&2; exit 1 ;;
    esac
    # An address: four dotted decimal octets, or an IPv6 form (at least two colons); an optional /prefix.
    addr=${cidr%%/*}; max=128
    if [[ $addr =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
        max=32
        for octet in "${BASH_REMATCH[@]:1}"; do [ "$octet" -le 255 ] || { echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' is not an address" >&2; exit 1; }; done
    elif [[ $addr =~ ^[0-9a-fA-F:]+$ && $addr == *:*:* ]]; then :
    else echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' is not an IPv4 or IPv6 address" >&2; exit 1; fi
    # A proxy is an address, or at most a small group of them: refuse any network wider than IPv4 /16 or IPv6 /48 (so that
    # 0.0.0.0/1 plus 128.0.0.0/1 cannot add up to "everyone"); warn from there down to /23 (IPv4).
    case $cidr in
        */*)
            prefix=${cidr#*/}
            # (`[` fails on anything but a whole number, and the characters allowed above leave no sign or space.)
            [ "$prefix" -le "$max" ] 2> /dev/null || { echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' has a prefix length above $max" >&2; exit 1; }
            case $cidr in
                *:*) [ "$prefix" -ge 48 ] || { echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' is a network wider than /48: list the proxy's address(es), not a client network" >&2; exit 1; } ;;
                *) [ "$prefix" -ge 16 ] || { echo "render.sh: TRUSTED_PROXY_CIDR entry '$cidr' is a network wider than /16: list the proxy's address(es), not a client network (0.0.0.0/1 and 128.0.0.0/1 would add up to everyone)" >&2; exit 1; }
                   [ "$prefix" -ge 24 ] || echo "render.sh: warning: '$cidr' is a large network; list the proxy's address(es), not its network" >&2 ;;
            esac ;;
    esac
    set_real_ip+="set_real_ip_from $cidr;"$'\n'
    n=$((n + 1))
done
set +f

awk -v with_proxy="$([ "$n" -gt 0 ] && echo 1 || echo 0)" -v ips="$set_real_ip" -v mb="$((upload_mb + 2))" \
    -v upload_mb="$upload_mb" -v read_wait="$read_wait" -v origin="$edge_origin" '
    /^[[:space:]]*# @if proxy@[[:space:]]*$/  { skipping = !with_proxy; next }
    /^[[:space:]]*# @endif@[[:space:]]*$/     { skipping = 0; next }
    skipping { next }
    /@SET_REAL_IP_FROM@/ {
        m = split(ips, lines, "\n")
        for (i = 1; i <= m; i++) if (lines[i] != "") print "    " lines[i]
        next
    }
    {
        gsub(/@MAX_BODY_MB@/, mb); gsub(/@MAX_UPLOAD_MB@/, upload_mb)
        gsub(/@READ_TIMEOUT@/, read_wait); gsub(/@EDGE_ORIGIN@/, origin)
        print
    }' "$template"
