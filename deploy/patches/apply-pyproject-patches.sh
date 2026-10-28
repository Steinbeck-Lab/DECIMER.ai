#!/bin/sh
# Applies deploy/patches/{seg,ocsr,cls,chem}-pyproject.patch to services/*/pyproject.toml and refreshes
# their uv.lock (network: PyPI and GitHub). Run from anywhere inside the repository; it changes the
# working tree only (no commit). Safe to run twice: a service that already has the change is skipped.
# Review `git diff services/*/uv.lock` before committing: only the opencv packages and a [manifest]
# table (excludes, overrides, build-constraints) should differ.
#
# What the patches do and why: see the header of each .patch (opencv-python-headless for seg and ocsr,
# pinned build backends for seg, cls and chem). Run this before building the images. It also checks
# that no service's runtime set (`uv export --no-dev`) contains PyMuPDF (AGPL).
set -eu
cd "$(git rev-parse --show-toplevel)"

for service in seg ocsr cls chem; do
    patch=deploy/patches/$service-pyproject.patch
    if git apply --check --reverse "$patch" > /dev/null 2>&1; then
        echo "$service: already applied"
    elif git apply --check "$patch" > /dev/null 2>&1; then
        git apply "$patch"
        echo "$service: pyproject.toml patched"
    else
        echo "$service: $patch does not apply; read its header and make the edit by hand" >&2
        exit 1
    fi
    (cd "services/$service" && uv lock && uv lock --check)
    # What an image installs (`--no-dev`: seg's dev group has PyMuPDF for the tests, the image does not).
    if ! runtime=$(cd "services/$service" && uv export --locked --no-dev --no-emit-project --no-hashes --no-annotate); then
        echo "$service: uv export failed" >&2
        exit 1
    fi
    # PyMuPDF is AGPL-3.0 and no image ships it: decimer-segmentation
    # needs it only in its `pdf` extra since 1.6.0, which nothing here installs. Also matches
    # pymupdf4llm and pymupdf-layout.
    if printf '%s\n' "$runtime" | grep -qi '^pymupdf'; then
        echo "$service: PyMuPDF (AGPL) is in the runtime set" >&2
        exit 1
    fi
    echo "$service: PyMuPDF is not in the runtime set"
    case $service in
        seg | ocsr)
            if printf '%s\n' "$runtime" | grep -q '^opencv-python-headless=='; then
                echo "$service: opencv-python-headless is in the runtime set"
            else
                echo "$service: opencv-python-headless is NOT in the runtime set" >&2
                exit 1
            fi ;;
    esac
done
