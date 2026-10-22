#!/bin/sh
# Applies deploy/patches/{seg,ocsr,cls,chem}-pyproject.patch to services/*/pyproject.toml and refreshes
# their uv.lock (network: PyPI and GitHub). Run from anywhere inside the repository; it changes the
# working tree only (no commit). Safe to run twice: a service that already has the change is skipped.
# Review `git diff services/*/uv.lock` before committing: only the opencv packages and a [manifest]
# table (overrides, build-constraints) should differ.
#
# What the patches do and why: see the header of each .patch (opencv-python-headless for seg and ocsr,
# pinned build backends for seg, cls and chem). Run this before building the images.
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
    case $service in
        seg | ocsr)
            if (cd "services/$service" && uv export --locked --no-dev --no-emit-project --no-hashes --no-annotate 2> /dev/null | grep -q '^opencv-python-headless=='); then
                echo "$service: opencv-python-headless is in the runtime set"
            else
                echo "$service: opencv-python-headless is NOT in the runtime set" >&2
                exit 1
            fi ;;
    esac
done
