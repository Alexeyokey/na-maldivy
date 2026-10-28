#!/bin/sh
# Run from the unpacked distribution; no downloads, source checkout or .env.
set -eu

DEMO_BUNDLE=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
if [ ! -f "$DEMO_BUNDLE/images.tar" ] || [ ! -f "$DEMO_BUNDLE/compose.yml" ]; then
    printf '%s\n' 'Run start.sh from the unpacked offline distribution.' >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Start an installed Docker Engine/Desktop with Docker Compose first.' >&2
    exit 1
fi
(
    cd "$DEMO_BUNDLE"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 -c SHA256SUMS
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum -c SHA256SUMS
    else
        printf '%s\n' 'shasum or sha256sum is required to verify the bundle.' >&2
        exit 1
    fi
)
docker image load --input "$DEMO_BUNDLE/images.tar"
docker compose --env-file /dev/null --project-directory "$DEMO_BUNDLE" \
    --project-name na-maldivy-offline -f "$DEMO_BUNDLE/compose.yml" \
    up --detach --pull never --wait
printf 'Offline demo API: http://localhost:%s/health\n' "${AGENT_PORT:-8000}"
printf 'Optional HTTP check: python3 check_running_demo.py --base-url http://localhost:%s\n' "${AGENT_PORT:-8000}"
