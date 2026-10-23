# shellcheck shell=bash
# Sourced by render.sh and by deploy/cutover/lib.sh. Reads single settings of deploy/.env (DECIMER_ENV_FILE) with grep: the
# file holds secrets (IP_HASH_SECRET, REDIS_PASSWORD, ...), so it is never sourced and never printed.
#   env_value NAME DEFAULT   the environment's NAME if set, else the env file's last NAME=..., else DEFAULT.
# Surrounding quotes and a trailing " # comment" are dropped, as docker compose's parser does.
env_value() {
    local name=$1 default=$2 v
    if [ -n "${!name+x}" ]; then printf '%s' "${!name}"; return; fi
    if [ -n "${DECIMER_ENV_FILE:-}" ] && [ -f "$DECIMER_ENV_FILE" ]; then
        v=$(grep -E "^${name}=" "$DECIMER_ENV_FILE" | tail -n1 | cut -d= -f2- || true)
        v=$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')
        if [ -n "$v" ]; then printf '%s' "$v"; return; fi
    fi
    printf '%s' "$default"
}
