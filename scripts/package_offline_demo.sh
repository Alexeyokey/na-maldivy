#!/bin/sh
# Build once while dependencies are available; export only allowlisted public artifacts.
set -eu

PACKAGE_ROOT=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
PACKAGE_BUNDLE=${1:-"$PACKAGE_ROOT/artifacts/na-maldivy-offline-full-ui-20261028"}
case "$PACKAGE_BUNDLE" in
    /*) ;;
    *) PACKAGE_BUNDLE="$(pwd)/$PACKAGE_BUNDLE" ;;
esac
PACKAGE_ARCHIVE="$PACKAGE_BUNDLE.tar.gz"
PACKAGE_AGENT_IMAGE=na-maldivy-demo-agent:20261028-full-ui
PACKAGE_GATEWAY_IMAGE=na-maldivy-demo-gateway:20261028-full-ui
PACKAGE_WEB_IMAGE=na-maldivy-demo-web:20261028-full-ui

if [ -e "$PACKAGE_BUNDLE" ] || [ -e "$PACKAGE_ARCHIVE" ]; then
    printf '%s\n' 'Choose a new bundle directory; existing contents are never included or overwritten.' >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker with Compose is required on the packaging machine.' >&2
    exit 1
fi
if command -v shasum >/dev/null 2>&1; then
    PACKAGE_HASH_TOOL=shasum
elif command -v sha256sum >/dev/null 2>&1; then
    PACKAGE_HASH_TOOL=sha256sum
else
    printf '%s\n' 'Install shasum or sha256sum before packaging.' >&2
    exit 1
fi

# --env-file /dev/null prevents Compose interpolation from reading a local .env.
# Dockerfile.demo and .dockerignore exclude credentials, runtime DBs and private cache.
docker compose --env-file /dev/null --project-directory "$PACKAGE_ROOT" \
    --project-name na-maldivy-package \
    -f "$PACKAGE_ROOT/docker-compose.yml" -f "$PACKAGE_ROOT/docker-compose.demo.yml" \
    build agent gateway web

docker image tag na-maldivy-package-agent "$PACKAGE_AGENT_IMAGE"
docker image tag na-maldivy-package-gateway "$PACKAGE_GATEWAY_IMAGE"
docker image tag na-maldivy-package-web "$PACKAGE_WEB_IMAGE"
mkdir -p "$PACKAGE_BUNDLE/docs" "$PACKAGE_BUNDLE/attributions/sources" "$PACKAGE_BUNDLE/scripts"
docker image save --output "$PACKAGE_BUNDLE/images.tar" \
    "$PACKAGE_AGENT_IMAGE" "$PACKAGE_GATEWAY_IMAGE" "$PACKAGE_WEB_IMAGE"
docker image inspect --format '{{.Os}}/{{.Architecture}}' \
    "$PACKAGE_AGENT_IMAGE" "$PACKAGE_GATEWAY_IMAGE" "$PACKAGE_WEB_IMAGE" > "$PACKAGE_BUNDLE/architecture.txt"

cp "$PACKAGE_ROOT/docker-compose.release.yml" "$PACKAGE_BUNDLE/compose.yml"
cp "$PACKAGE_ROOT/scripts/start_offline_demo.sh" "$PACKAGE_BUNDLE/start.sh"
cp "$PACKAGE_ROOT/scripts/check_running_demo.py" "$PACKAGE_BUNDLE/check_running_demo.py"
cp "$PACKAGE_ROOT/scripts/check_running_three_destinations.py" "$PACKAGE_BUNDLE/check_running_three_destinations.py"
cp "$PACKAGE_ROOT/scripts/validate_ui_rehearsal.py" "$PACKAGE_BUNDLE/scripts/validate_ui_rehearsal.py"
cp "$PACKAGE_ROOT/docs/offline-package.md" "$PACKAGE_BUNDLE/README.md"
if [ -f "$PACKAGE_ROOT/docs/defense-slides.pptx" ]; then
    cp "$PACKAGE_ROOT/docs/defense-slides.pptx" "$PACKAGE_BUNDLE/defense.pptx"
    cp "$PACKAGE_ROOT/docs/defense-slides.pptx" "$PACKAGE_BUNDLE/docs/defense-slides.pptx"
fi
chmod 755 "$PACKAGE_BUNDLE/start.sh"

# Explicit allowlist: no repository copy, environment, state, volumes or source tree.
for PACKAGE_DOC in \
    submission-readiness.md demo-checkout.md demo.md chat-aviasales.md \
    hotels.md hotel-profiles.md price-calendar.md trains.md data-cache.md \
    frontend-handoff.md defense-brief.md three-destination-demo.md video-recording.md ui-acceptance.md
do
    cp "$PACKAGE_ROOT/docs/$PACKAGE_DOC" "$PACKAGE_BUNDLE/docs/$PACKAGE_DOC"
done
# Curated public evidence and manifest keep exported documentation links usable.
mkdir -p "$PACKAGE_BUNDLE/eval/reports" "$PACKAGE_BUNDLE/data/demo/aviasales"
for PACKAGE_REPORT in u2-chat-offline-2026-10-28.json u2-budget-feasibility.md \
    docker-offline-2026-10-28.json submission-proposed-2026-10-28.json \
    three-destination-live-coverage-2026-10-28.json three-destination-docker-2026-10-28.json \
    three-destination-ui-proxy-2026-10-28.json
do
    cp "$PACKAGE_ROOT/eval/reports/$PACKAGE_REPORT" "$PACKAGE_BUNDLE/eval/reports/$PACKAGE_REPORT"
done
cp -R "$PACKAGE_ROOT/eval/reports/backend-http-2026-10-28" "$PACKAGE_BUNDLE/eval/reports/"
cp -R "$PACKAGE_ROOT/eval/reports/three-destination-backend-2026-10-28" "$PACKAGE_BUNDLE/eval/reports/"
cp -R "$PACKAGE_ROOT/eval/reports/ui-rehearsal-2026-10-28" "$PACKAGE_BUNDLE/eval/reports/"
cp "$PACKAGE_ROOT/eval/demo-runs.json" "$PACKAGE_BUNDLE/eval/demo-runs.json"
cp "$PACKAGE_ROOT/data/demo/aviasales/manifest.json" "$PACKAGE_BUNDLE/data/demo/aviasales/manifest.json"
cp "$PACKAGE_ROOT/data/demo/aviasales/README.md" "$PACKAGE_BUNDLE/data/demo/aviasales/README.md"
cp "$PACKAGE_ROOT/data/ATTRIBUTION.md" "$PACKAGE_BUNDLE/attributions/ATTRIBUTION.md"
cp "$PACKAGE_ROOT/data/CBR-ATTRIBUTION.md" "$PACKAGE_BUNDLE/attributions/CBR-ATTRIBUTION.md"
cp "$PACKAGE_ROOT/data/sources/airports-MIT-LICENSE.txt" "$PACKAGE_BUNDLE/attributions/sources/airports-MIT-LICENSE.txt"
cp "$PACKAGE_ROOT/data/hotels/sources.json" "$PACKAGE_BUNDLE/attributions/hotel-sources.json"
for PACKAGE_DATA_DOC in routes transfers trains hotel_profiles hotel_reviews
do
    cp "$PACKAGE_ROOT/data/$PACKAGE_DATA_DOC/README.md" "$PACKAGE_BUNDLE/attributions/$PACKAGE_DATA_DOC-README.md"
done

(
    cd "$PACKAGE_BUNDLE"
    if [ "$PACKAGE_HASH_TOOL" = shasum ]; then
        find . -type f ! -name SHA256SUMS -exec shasum -a 256 '{}' + > SHA256SUMS
    else
        find . -type f ! -name SHA256SUMS -exec sha256sum '{}' + > SHA256SUMS
    fi
)

PACKAGE_PARENT=$(dirname "$PACKAGE_BUNDLE")
PACKAGE_NAME=$(basename "$PACKAGE_BUNDLE")
COPYFILE_DISABLE=1 tar -czf "$PACKAGE_ARCHIVE" -C "$PACKAGE_PARENT" "$PACKAGE_NAME"
(
    cd "$PACKAGE_PARENT"
    if [ "$PACKAGE_HASH_TOOL" = shasum ]; then
        shasum -a 256 "$PACKAGE_NAME.tar.gz" > "$PACKAGE_NAME.tar.gz.sha256"
    else
        sha256sum "$PACKAGE_NAME.tar.gz" > "$PACKAGE_NAME.tar.gz.sha256"
    fi
)
printf 'Offline archive: %s\nChecksum: %s\n' "$PACKAGE_ARCHIVE" "$PACKAGE_ARCHIVE.sha256"
