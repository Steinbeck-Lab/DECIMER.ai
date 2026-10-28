#!/bin/sh
# verify.sh [--strict] <service> <dir> [<SHA256SUMS file>]
#
# Checks <dir> against the lines of SHA256SUMS that belong to <service> (paths there are
# "<service>/<path under dir>"). Fails (exit 1) when
#   * a listed file is missing, is not a regular file, or its SHA-256 differs;
#   * the top-level directories those paths live in hold any file that is not listed, or anything
#     that is neither a regular file nor a directory (a symlink could point anywhere).
# With --strict the check covers the WHOLE of <dir> and is exact: `find . -mindepth 1` must list the
# files of SHA256SUMS and the directories above them, and nothing else (no extra file, no extra or
# empty directory, no symlink, no dot-file). Used for seg, ocsr and chem, whose build-context
# directory holds nothing but the baked files. Without it only the top-level directories of the listed
# paths are scanned: cls checks the installed pip package, which holds much else besides model/.
# Exit 2: SHA256SUMS has no lines for the service (the service is not listed).
#
# POSIX sh plus GNU coreutils' sha256sum (Debian images, the host). Used by:
#   fetch.sh                  after it has put files in deploy/weights/<service>/
#   seg/ocsr/chem.Dockerfile  at build time on the copied build context, so a corrupted or swapped
#                             file fails the build
#   cls.Dockerfile            at build time on the installed pip package (the weights ship inside it)
set -eu
export LC_ALL=C

strict=0
if [ "${1:-}" = "--strict" ]; then
    strict=1
    shift
fi
if [ $# -lt 2 ]; then
    echo "usage: $0 [--strict] <service> <dir> [<SHA256SUMS file>]" >&2
    exit 64
fi
service=$1
dir=$2
sums=${3:-$(dirname "$0")/SHA256SUMS}

case "$service" in
    '' | *[!a-z0-9_-]*)
        echo "verify: bad service name '$service'" >&2
        exit 64 ;;
esac
[ -d "$dir" ] || { echo "verify: $dir is not a directory" >&2; exit 1; }
[ -f "$sums" ] || { echo "verify: $sums not found" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# This service's entries, with the "<service>/" prefix taken off the path.
sed -n "s#^\([0-9a-f]\{64\}\)  ${service}/#\1  #p" "$sums" > "$tmp/entries"
if [ ! -s "$tmp/entries" ]; then
    echo "verify: no checksums for '$service' in $sums (is the service listed?)" >&2
    exit 2
fi
sed 's#^[0-9a-f]\{64\}  ##' "$tmp/entries" | sort > "$tmp/listed"

# 1. every listed file is there, is a regular file (sha256sum would follow a symlink), and has the
#    recorded hash
while IFS= read -r path; do
    if [ ! -f "$dir/$path" ] || [ -L "$dir/$path" ]; then
        echo "verify: $service: $dir/$path is missing or not a regular file" >&2
        exit 1
    fi
done < "$tmp/listed"
if ! (cd "$dir" && sha256sum -c "$tmp/entries"); then
    echo "verify: $service: checksum mismatch or missing file in $dir" >&2
    exit 1
fi

if [ "$strict" = 1 ]; then
    # 2. the whole tree is exactly the listed files plus the directories above them
    #    (a directory above a listed path: every proper prefix of the path)
    awk '{ n = split($0, p, "/"); s = ""; for (i = 1; i < n; i++) { s = (s == "" ? p[i] : s "/" p[i]); print s } print $0 }' \
        "$tmp/listed" | sort -u > "$tmp/expected"
    (cd "$dir" && find . -mindepth 1 | sed 's#^\./##' | sort) > "$tmp/found"
    unexpected=$(comm -13 "$tmp/expected" "$tmp/found")
    if [ -n "$unexpected" ]; then
        echo "verify: $service: $dir holds entries that SHA256SUMS does not account for" >&2
        echo "(extra files, directories, symlinks or dot-files):" >&2
        echo "$unexpected" >&2
        exit 1
    fi
    odd=$(cd "$dir" && find . -mindepth 1 ! -type f ! -type d)
    if [ -n "$odd" ]; then
        echo "verify: $service: not a regular file or directory:" >&2
        echo "$odd" >&2
        exit 1
    fi
else
    # 2. nothing else under the top-level directories (or files) the entries live in
    sed 's#/.*##' "$tmp/listed" | sort -u > "$tmp/tops"
    : > "$tmp/found"
    while IFS= read -r top; do
        odd=$(cd "$dir" && find "$top" ! -type f ! -type d)
        if [ -n "$odd" ]; then
            echo "verify: $service: not a regular file or directory:" >&2
            echo "$odd" >&2
            exit 1
        fi
        (cd "$dir" && find "$top" -type f) >> "$tmp/found"
    done < "$tmp/tops"
    sort -o "$tmp/found" "$tmp/found"
    extra=$(comm -13 "$tmp/listed" "$tmp/found")
    if [ -n "$extra" ]; then
        echo "verify: $service: files in $dir that SHA256SUMS does not list:" >&2
        echo "$extra" >&2
        exit 1
    fi
fi
echo "verify: $service OK ($(wc -l < "$tmp/listed") files$([ "$strict" = 1 ] && echo ', strict'))"
