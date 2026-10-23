#!/usr/bin/env bash
# fetch.sh [--archive DIR | --zenodo] [--force] [service ...]
#
# Fills deploy/weights/<service>/ (the build context directories the Dockerfiles copy; not in git)
# and verifies every file against SHA256SUMS with `verify.sh --strict`. A fetch whose result does not
# verify is removed again, so a directory that exists here has been checked.
#
#   seg    deploy/weights/seg/DECIMER-Segmentation_model/mask_rcnn_molecule.h5
#   ocsr   deploy/weights/ocsr/DECIMER-V2/{DECIMER_model,DECIMER_HandDrawn_model}/
#          (both: import DECIMER loads both, owner decision D15)
#   chem   deploy/weights/chem/{opsin-cli-2.9.0-jar-with-dependencies.jar,centres-cli-1.2.1.jar,
#          cdk-2.8.jar}
#   cls    nothing to fetch: the weights ship inside the pip package; cls.Dockerfile checks the
#          installed files against the cls/ lines of SHA256SUMS at build time
#
# With no service named: seg, ocsr and chem.
#
# Sources
#   archive  the legacy weights archive, default $WEIGHTS_ARCHIVE or
#            /srv/decimer/weights-archive/2026-10-22. It has seg and ocsr's DECIMER_model; it has
#            NOT the hand-drawn model, which therefore always comes from Zenodo.
#   zenodo   https://zenodo.org/records/{10663579,8300489,10781330}, each download also checked
#            against the MD5 Zenodo publishes for the file (below), the zips against the SHA-256 in
#            services/ocsr/README.md, then every file against SHA256SUMS.
#   github   the three chem jars, from the OPSIN, centres and CDK (cdk-2.8) releases.
#   default  the archive where it has the files, the network for the rest. --zenodo forces Zenodo for
#            all of seg and ocsr; --archive DIR picks another archive.
# Every download is https only (also across redirects). --force fetches again even when what is there
# already verifies.
set -euo pipefail
umask 022

here=$(cd "$(dirname "$0")" && pwd)
archive=${WEIGHTS_ARCHIVE:-/srv/decimer/weights-archive/2026-10-22}
use_archive=1
force=0
services=()

# Published by Zenodo for these files (record metadata, read 2026-10-22), and the SHA-256s of the zips
# (services/ocsr/README.md, "Weights").
SEG_URL='https://zenodo.org/record/10663579/files/mask_rcnn_molecule.h5?download=1'
SEG_MD5=edd1e6e469cfff7efa6bf8c38441a529
MODELS_URL='https://zenodo.org/record/8300489/files/models.zip?download=1'           # DECIMER_model/
MODELS_MD5=de78c966b0d63b290cd3f70bb81a91e7
MODELS_SHA256=8ebc7db18fffac7e71a2cf8487708053620d124846bc9502eddac1283b431d2b
HAND_URL='https://zenodo.org/records/10781330/files/DECIMER_HandDrawn_model.zip?download=1'
HAND_MD5=1fece9813549417440f7c45d0c51603a
HAND_SHA256=91c70fee79b7d64c09d433ff82e2216d16070e4d098b83da2c13d34c52439fde
# chem: the file is checked against SHA256SUMS afterwards; the first two URLs are the releases Orthonym
# pins, the third is the CDK 2.8 bundle that legacy uses (the same bytes: see SHA256SUMS).
OPSIN_URL='https://github.com/dan2097/opsin/releases/download/2.9.0/opsin-cli-2.9.0-jar-with-dependencies.jar'
CENTRES_URL='https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar'
CDK_URL='https://github.com/cdk/cdk/releases/download/cdk-2.8/cdk-2.8.jar'

die() { echo "fetch: $*" >&2; exit 1; }
say() { echo "fetch: $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --archive) [ $# -ge 2 ] || die "--archive needs a directory"; archive=$2; shift 2 ;;
        --zenodo) use_archive=0; shift ;;
        --force) force=1; shift ;;
        -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) services+=("$1"); shift ;;
    esac
done
[ ${#services[@]} -gt 0 ] || services=(seg ocsr chem)

tmp=$(mktemp -d "$here/.fetch.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

verified() { "$here/verify.sh" --strict "$1" "$here/$1" > /dev/null 2>&1; }

# https only, also across redirects (Zenodo and GitHub both redirect to a CDN)
download() { # url dest [md5] [sha256]
    say "downloading ${1%%\?*}"
    curl --fail --location --silent --show-error --retry 3 \
        --proto '=https' --proto-redir '=https' --output "$2" "$1"
    local got
    if [ -n "${3:-}" ]; then
        got=$(md5sum "$2" | cut -d' ' -f1)
        [ "$got" = "$3" ] || die "$1: MD5 $got, Zenodo publishes $3"
    fi
    if [ -n "${4:-}" ]; then
        got=$(sha256sum "$2" | cut -d' ' -f1)
        [ "$got" = "$4" ] || die "$1: SHA-256 $got, expected $4"
    fi
}

# Python's zipfile, not unzip: it refuses names that would leave the target directory.
unzip_to() { # zip dir
    python3 -I -m zipfile -e "$1" "$2"
}

have_archive() { [ "$use_archive" = 1 ] && [ -d "$archive" ]; }

# Files copied out of the (read-only) archive get ordinary permissions.
copy_tree() { cp -R --no-preserve=mode,ownership,timestamps "$1" "$2"; }

fetch_seg() {
    local dest="$tmp/seg/DECIMER-Segmentation_model"
    mkdir -p "$dest"
    if have_archive && [ -f "$archive/segmentation/mask_rcnn_molecule.h5" ]; then
        say "seg: from the archive $archive"
        cp --no-preserve=mode,ownership,timestamps "$archive/segmentation/mask_rcnn_molecule.h5" "$dest/"
    else
        download "$SEG_URL" "$dest/mask_rcnn_molecule.h5" "$SEG_MD5"
    fi
}

fetch_ocsr() {
    local dest="$tmp/ocsr/DECIMER-V2"
    mkdir -p "$dest"
    if have_archive && [ -d "$archive/decimer-transformer/DECIMER-V2/DECIMER_model" ]; then
        say "ocsr: DECIMER_model from the archive $archive"
        copy_tree "$archive/decimer-transformer/DECIMER-V2/DECIMER_model" "$dest/"
    else
        download "$MODELS_URL" "$tmp/models.zip" "$MODELS_MD5" "$MODELS_SHA256"
        unzip_to "$tmp/models.zip" "$dest"   # the zip's top level directory is DECIMER_model/
        rm -f "$tmp/models.zip"
    fi
    # `import DECIMER` loads the hand-drawn model too and would try to download it at start-up.
    download "$HAND_URL" "$tmp/hand.zip" "$HAND_MD5" "$HAND_SHA256"
    unzip_to "$tmp/hand.zip" "$dest"          # top level directory DECIMER_HandDrawn_model/
    rm -f "$tmp/hand.zip"
    chmod -R a+rX "$tmp/ocsr"
}

fetch_chem() {
    local dest="$tmp/chem"
    mkdir -p "$dest"
    download "$OPSIN_URL" "$dest/opsin-cli-2.9.0-jar-with-dependencies.jar"
    download "$CENTRES_URL" "$dest/centres-cli-1.2.1.jar"
    download "$CDK_URL" "$dest/cdk-2.8.jar"
    chmod 644 "$dest"/*.jar   # Orthonym's own downloads are 0600; the service user must be able to read them
}

for service in "${services[@]}"; do
    case "$service" in
        seg | ocsr | chem) ;;
        cls)
            say "cls: nothing to fetch (the weights are inside the pip package; cls.Dockerfile verifies them)"
            continue ;;
        *) die "unknown service '$service' (seg, ocsr, cls or chem)" ;;
    esac
    if [ "$force" = 0 ] && [ -d "$here/$service" ] && verified "$service"; then
        say "$service: already present and verified"
        continue
    fi
    "fetch_$service"
    "$here/verify.sh" --strict "$service" "$tmp/$service" > /dev/null || {
        "$here/verify.sh" --strict "$service" "$tmp/$service" || true   # show why
        die "$service: the fetched files do not match SHA256SUMS; nothing installed"
    }
    rm -rf "${here:?}/$service"
    mv "$tmp/$service" "$here/$service"
    say "$service: installed in $here/$service and verified"
done
