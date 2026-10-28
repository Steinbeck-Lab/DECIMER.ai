#!/bin/sh
# The edge's error log and how long it lives.
#
# nginx's error log is the only place where the edge writes a client's IP address. A line holds the
# address, the request line (so a job id), the host and the referrer: "connect() failed ... client:
# 203.0.113.7, request: "GET /api/jobs/<id> HTTP/1.1", host: "...", referrer: "..."". The privacy page
# promises it is kept seven days at most. A docker log driver cannot promise that (json-file rotates by
# size, not by age), so nginx writes the error log to a FILE on its own volume (nginx.conf: error_log
# /var/log/nginx/error.log) and this script rotates and deletes it:
#
#   rotate   once: if the file holds lines of an earlier UTC day, rename it error.log.<that day>
#            (error.log.2026-10-22) and tell nginx to open a fresh one (USR1); then delete every
#            rotated file that is, or is about to be, seven days old. Safe to repeat. It takes a lock
#            (the directory .error.log.lock) so that two runs never rotate at once; a run that finds the
#            lock taken does nothing (the holder is doing the work), and a lock a minute old is broken.
#   loop     rotate now, then every EDGE_LOG_INTERVAL_SECONDS (10 minutes) for ever. The entrypoint
#            (edge-entrypoint.sh) runs `rotate` and `check` before nginx starts and `loop` beside it.
#   check    exit 1 and say why unless the retention is being kept: the error log is not a plain file
#            (a symlink to stderr would put the addresses into docker's log), the day marker is not
#            today's (or yesterday's within the grace after midnight), a rotated file is past its time, or
#            the clock is behind a date in the logs.
#   enforce  `rotate`, then `check`: what the image's HEALTHCHECK runs every 15 seconds. The retention
#            does not depend on the loop being alive: the health check does the work itself, and is
#            unhealthy (in `docker ps`) only for what rotating cannot mend.
#   Docker runs the health check as long as the container runs. If healthchecks are ever switched off
#   (compose `healthcheck: disable`), the loop is the only enforcer.
#
# The guarantee, and why it holds:
#   * A file covers one UTC day. `.error.log.began` holds the day the current error.log was started;
#     the first run after midnight renames the file to that day and starts the next one. So the oldest
#     line in error.log.D is from D 00:00 UTC.
#   * It is deleted when D 00:00 + 7 days <= now + one interval, i.e. at the last run before its oldest
#     line would be seven days old. So no line outlives seven days (while the container runs), and
#     none is deleted before it is about six days old: the file of a day goes when it is a week behind.
#   * A stopped container prunes nothing: the first thing a start does is `rotate`, before nginx opens
#     the file, so what is past its week goes before anything is served. For a long stop, prune from the
#     host (deploy/README.md, "Error log").
#   * A clock that goes back: a rotated file dated after today (the marker's day, once the file has been
#     renamed, or one from before the step) is dated today, so it is gone a week from today and not a
#     week from a date that has not come.
# Only files named error.log.<YYYY-MM-DD>[.n] are ever deleted. Nothing is read from a log line.
#
# POSIX sh and BusyBox (the nginx image's `date` knows `-d @seconds` and `-r file`); deploy/nginx/ has
# no other dependency. Tested by api/tests/test_edge_logs.py (dash and BusyBox).
#
# Settings (environment; the defaults are the deployment's, the others exist for the test):
#   EDGE_LOG_DIR                 /var/log/nginx
#   EDGE_LOG_KEEP_DAYS           7        what the privacy page says: change both together
#   EDGE_LOG_INTERVAL_SECONDS    600
#   EDGE_LOG_GRACE_SECONDS       3600     how late `check` lets a run be before it fails
#   EDGE_LOG_PID_FILE            /tmp/nginx.pid
#   EDGE_LOG_NOW                 (test only) the time, in seconds since the epoch
set -eu

dir=${EDGE_LOG_DIR:-/var/log/nginx}
name=error.log
keep=${EDGE_LOG_KEEP_DAYS:-7}
interval=${EDGE_LOG_INTERVAL_SECONDS:-600}
grace=${EDGE_LOG_GRACE_SECONDS:-3600}
pidfile=${EDGE_LOG_PID_FILE:-/tmp/nginx.pid}
active=$dir/$name
began=$dir/.$name.began
lock=$dir/.$name.lock
lock_stale_seconds=60

for number in "$keep" "$interval" "$grace"; do
    case "$number" in
        '' | *[!0-9]* | 0)
            echo "edge-logs: '$number' is not a positive whole number" >&2
            exit 2 ;;
    esac
done

# Helpers set a variable instead of printing, so that the usual run starts few processes.
# $t: the time, in seconds since the epoch.
now() { if [ -n "${EDGE_LOG_NOW:-}" ]; then t=$EDGE_LOG_NOW; else t=$(date +%s); fi; }
# $d: the UTC day of the time $1, as YYYY-MM-DD, and $dn: the number YYYYMMDD (which compares like the date).
day() { d=$(date -u -d "@$1" +%F); daynumber "$d"; }
daynumber() { rest=${1#*-}; dn=${1%%-*}${rest%%-*}${rest#*-}; }

# $fday: the day a rotated file is for, from its name (error.log.2026-10-22 or error.log.2026-10-22.1), as
# YYYYMMDD; empty for any other name.
file_day() {
    fday=
    suffix=${1##*/}
    suffix=${suffix#"$name".}
    candidate=${suffix%%.*}
    case "$candidate" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) daynumber "$candidate"; fday=$dn ;;
    esac
}

# $target: a name for a rotated file of the day $1 that nothing has yet (error.log.2026-10-22, then .1, .2).
free_name() {
    target=$dir/$name.$1
    n=0
    while [ -e "$target" ]; do
        n=$((n + 1))
        target=$dir/$name.$1.$n
    done
}

reopen() {
    # Tell nginx to open the log file again (USR1). Nothing to do when it is not running yet (the
    # entrypoint rotates before it starts it): nginx then creates the file itself.
    pid=$(cat "$pidfile" 2> /dev/null || true)
    case "$pid" in '' | *[!0-9]*) return 0 ;; esac
    kill -USR1 "$pid" 2> /dev/null || true
}

# --- the lock -------------------------------------------------------------------------------------

# 0: taken. 1: someone else holds it. 2: it cannot be made (no such directory, read-only volume).
take_lock() {
    tries=0
    while [ "$tries" -lt 3 ]; do
        tries=$((tries + 1))
        if mkdir "$lock" 2> /dev/null; then
            return 0
        fi
        if [ -d "$lock" ]; then
            # Held, or left by a run that was killed in the middle (the real clock: the lock's own time).
            if held_since=$(date -r "$lock" +%s 2> /dev/null); then
                if [ $(($(date +%s) - held_since)) -le "$lock_stale_seconds" ]; then
                    return 1
                fi
                rmdir "$lock" 2> /dev/null || true
            fi # (no time: it was given back just now; try again)
        fi
        # Not there and not made: the holder gave it back between the two calls (try again), or it cannot
        # be made at all (after three tries).
    done
    return 2
}

# --- rotating and deleting --------------------------------------------------------------------------

rotate_active() {
    today=$1
    started=$(cat "$began" 2> /dev/null || true)
    case "$started" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
        *) started= ;;
    esac
    if [ "$started" = "$today" ]; then
        return 0
    fi
    if [ -s "$active" ]; then
        # The day it is for: the day it was started; without that note, the day of its last line. (A day
        # after today, because the clock went back, is mended by `prune`, which runs next.)
        [ -n "$started" ] || started=$(date -u -r "$active" +%F)
        free_name "$started"
        mv "$active" "$target" || return 1
        reopen
    fi
    # Written whole and moved into place: a `check` at the same moment never finds it empty.
    printf '%s\n' "$today" > "$began.new" && mv "$began.new" "$began" || return 1
}

prune() {
    # Delete a file when its day began a full `keep` days before the next run: the cutoff is the day of
    # (now + interval - keep days). 8 digits compare like dates. A file dated after today (the clock went
    # back since it was rotated) is dated today instead, so that it goes a week from today.
    day "$1"
    today_date=$d
    today_number=$dn
    day $(($1 + interval - keep * 86400))
    cutoff=$dn
    status=0
    for file in "$dir/$name".*; do
        [ -f "$file" ] || continue
        file_day "$file"
        [ -n "$fday" ] || continue
        if [ "$fday" -gt "$today_number" ]; then
            free_name "$today_date"
            mv "$file" "$target" || status=1
        elif [ "$fday" -le "$cutoff" ]; then
            rm -f "$file" || status=1
        fi
    done
    return "$status"
}

busy=0 # 1 when `rotate` found the lock taken and left the work to the run that holds it

rotate() {
    take_lock && locked=0 || locked=$?
    case "$locked" in
        0) ;;
        1)
            busy=1 # another run is at it
            return 0 ;;
        *)
            echo "edge-logs: cannot take the lock $lock (is $dir there, and writable?)" >&2
            return 1 ;;
    esac
    trap 'rmdir "$lock" 2> /dev/null || true' EXIT
    now
    day "$t"
    outcome=0
    rotate_active "$d" || outcome=1
    prune "$t" || outcome=1
    trap - EXIT
    rmdir "$lock" 2> /dev/null || true
    return "$outcome"
}

# --- checking ---------------------------------------------------------------------------------------

check() {
    now
    failed=0
    day "$t"
    today_number=$dn
    # error.log must be a plain file nginx writes to: a symlink to /dev/stderr (a volume seeded by the
    # stock nginx image) would send the addresses to docker's log, which keeps them by size, not by age.
    if [ -L "$active" ] || { [ -e "$active" ] && [ ! -f "$active" ]; }; then
        echo "edge-logs: $active is not a regular file: its lines would not be deleted after $keep days" >&2
        failed=1
    fi
    # A rotated file that should have been deleted more than `grace` ago, or is dated after today.
    day $((t - keep * 86400 - grace))
    late=$dn
    for file in "$dir/$name".*; do
        [ -f "$file" ] || continue
        file_day "$file"
        [ -n "$fday" ] || continue
        if [ "$fday" -le "$late" ]; then
            echo "edge-logs: ${file##*/} is older than $keep days and was not deleted" >&2
            failed=1
        elif [ "$fday" -gt "$today_number" ]; then
            echo "edge-logs: ${file##*/} is dated after today: the clock went back" >&2
            failed=1
        fi
    done
    # The day marker must be today's, or the day before within `grace` of midnight: otherwise nobody
    # has rotated for a day and error.log holds more than one day of lines.
    started=$(cat "$began" 2> /dev/null || true)
    case "$started" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
            daynumber "$started"
            marked=$dn
            day $((t - grace))
            if [ "$marked" -gt "$today_number" ]; then
                echo "edge-logs: error.log has been open since $started, which is after today: the clock went back" >&2
                failed=1
            elif [ "$marked" -lt "$dn" ]; then
                echo "edge-logs: error.log has been open since $started and was not rotated" >&2
                failed=1
            fi ;;
        *)
            echo "edge-logs: no record of when error.log was started: the rotation has not run" >&2
            failed=1 ;;
    esac
    return "$failed"
}

loop() {
    while :; do
        rotate || echo "edge-logs: rotation failed; trying again in ${interval}s" >&2
        sleep "$interval"
    done
}

case "${1:-}" in
    rotate) rotate ;;
    loop) loop ;;
    check) check ;;
    enforce)
        rotate
        # A run that holds the lock is rotating right now: its result is not in yet, so there is nothing
        # fair to check this time (Docker retries the health check).
        if [ "$busy" = 1 ]; then
            exit 0
        fi
        check ;;
    *)
        echo "usage: edge-logs.sh rotate|loop|check|enforce" >&2
        exit 2 ;;
esac
