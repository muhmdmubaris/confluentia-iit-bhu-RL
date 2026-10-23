#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: scripts/build_timing.sh --yes | --dry-run" >&2
    exit 3
}

# ---------------------------------------------------------------------------
# Argument parsing: exactly one of --yes or --dry-run, nothing else
# ---------------------------------------------------------------------------
if [ "$#" -ne 1 ]; then
    usage
fi

case "$1" in
    --yes)     MODE=yes ;;
    --dry-run) MODE=dry ;;
    *)         usage ;;
esac

# ---------------------------------------------------------------------------
# Limit validation: must be a positive integer
# ---------------------------------------------------------------------------
LIMIT="${BUILD_LIMIT_SECONDS:-180}"
if ! [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "BUILD_LIMIT_SECONDS must be a positive integer, got: ${LIMIT}" >&2
    usage
fi

# ---------------------------------------------------------------------------
# Dry-run: print the two commands and the limit, then exit
# ---------------------------------------------------------------------------
if [ "$MODE" = "dry" ]; then
    echo "docker builder prune -af"
    echo "docker compose build --no-cache"
    echo "limit_seconds=${LIMIT}"
    exit 0
fi

# ---------------------------------------------------------------------------
# --yes path
# ---------------------------------------------------------------------------

# Warning: this deletes the ENTIRE local Docker build cache, not only this project's.
echo "WARNING: docker builder prune -af deletes your entire local Docker build cache, not only this project's." >&2

# Step 1: prune
rc=0
docker builder prune -af >&2 || rc=$?
if [ "$rc" -ne 0 ]; then
    echo "build_failed (exit ${rc})"
    echo "FAIL"
    exit 1
fi

# Step 2: build (timed with $SECONDS — bash wall-clock counter, no python, no bc)
START=$SECONDS
rc=0
docker compose build --no-cache >&2 || rc=$?
ELAPSED=$(( SECONDS - START ))

if [ "$rc" -ne 0 ]; then
    echo "build_failed (exit ${rc})"
    echo "FAIL"
    exit 1
fi

# Step 3: report
echo "build_seconds=${ELAPSED}"
echo "limit_seconds=${LIMIT}"
if [ "$ELAPSED" -lt "$LIMIT" ]; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
