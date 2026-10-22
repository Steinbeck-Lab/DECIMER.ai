#!/bin/sh
# Entry point of the edge image: turns the settings the API and the edge must agree on into an nginx
# include, then starts nginx. One source of truth for each, taken from environment variables of the
# same names compose gives the api container (compose.yaml says which values the edge gets):
#
#   TRUSTED_PROXY_CIDR          the proxies whose X-Forwarded-For the EDGE believes (comma or space
#                               separated): compose gives it the public network's subnet plus the campus
#                               proxies from deploy/.env -> set_real_ip_from lines
#   PROXY_READ_TIMEOUT_SECONDS  the API refuses to start unless INGEST_WAIT_SECONDS + the ingest
#                               child's deadline + 5 s is below this, so nginx must not wait less
#                               than the API assumes -> proxy_read_timeout
#   MAX_UPLOAD_MB               the API's upload limit (default 100). nginx's limit is one MB more, so that
#                               multipart framing never trips nginx first and the API makes the call on the
#                               file sizes -> client_max_body_size, and $max_upload_mb for the 413 message
#
# The output goes to /tmp/edge-generated.conf (a tmpfs under read_only), included by edge.conf.
# First, whatever an earlier run left in nginx's temp directory is deleted: buffered upload bodies of
# requests that were cut off (a crash, a restart) are user data and must not outlive the process.
set -eu

find "${EDGE_CACHE_DIR:-/var/cache/nginx}" -mindepth 1 -delete 2> /dev/null || true

out=${EDGE_GENERATED_CONF:-/tmp/edge-generated.conf}
: > "$out"

n=0
set -f   # no globbing of the entries below
# shellcheck disable=SC2046
for cidr in $(printf '%s' "${TRUSTED_PROXY_CIDR:-}" | tr ',' ' '); do
    case "$cidr" in
        *[!0-9a-fA-F:./]*)
            echo "edge: TRUSTED_PROXY_CIDR entry '$cidr' contains characters that are not part of an address" >&2
            exit 1 ;;
        */0)
            echo "edge: TRUSTED_PROXY_CIDR entry '$cidr' would trust every client; list proxy addresses, not client networks" >&2
            exit 1 ;;
    esac
    echo "set_real_ip_from $cidr;" >> "$out"
    n=$((n + 1))
done
set +f
if [ "$n" -eq 0 ]; then
    echo "edge: WARNING: TRUSTED_PROXY_CIDR is empty, so X-Forwarded-For is ignored and every client looks like the docker gateway to the per-IP limits" >&2
fi

seconds=${PROXY_READ_TIMEOUT_SECONDS:-60}
case "$seconds" in
    ''|*[!0-9.]*|.*|*.|*.*.*)
        echo "edge: PROXY_READ_TIMEOUT_SECONDS='$seconds' is not a number of seconds" >&2
        exit 1 ;;
esac
ms=$(awk -v s="$seconds" 'BEGIN { printf "%d", s * 1000 }')
if [ "$ms" -le 0 ]; then
    echo "edge: PROXY_READ_TIMEOUT_SECONDS must be positive" >&2
    exit 1
fi
echo "proxy_read_timeout ${ms}ms;" >> "$out"

upload_mb=${MAX_UPLOAD_MB:-100}
case "$upload_mb" in
    '' | *[!0-9]* | 0)
        echo "edge: MAX_UPLOAD_MB='$upload_mb' is not a positive whole number of MB" >&2
        exit 1 ;;
esac
{
    echo "client_max_body_size $((upload_mb + 1))m;"
    echo "set \$max_upload_mb $upload_mb;"
} >> "$out"

exec "$@"
