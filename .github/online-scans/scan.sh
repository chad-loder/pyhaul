#!/usr/bin/env bash
# Runs inside the online-scans container (see ci.yml): /src is the read-only
# checkout, /venv the shared tool environment, /out the findings directory.
#   scan.sh tools   install the hash-locked lint group, then semgrep and uv audit (no token)
#   scan.sh zizmor  zizmor online audits (GH_TOKEN, read-only contents scope)
# Scanner exit codes are ignored here; scan-gate judges the JSON reports.
set -euo pipefail
cd /src
export UV_PROJECT_ENVIRONMENT=/venv UV_CACHE_DIR=/tmp/uv-cache UV_PYTHON_DOWNLOADS=never

case "$1" in
  tools)
    uv sync --locked --only-group lint --only-group maintainer --no-install-project --quiet
    /venv/bin/semgrep scan --config=auto --quiet --json --output /out/semgrep.json src/ || true
    uv audit --locked --preview-features audit,json-output --output-format json > /out/uv-audit.json || true
    ;;
  zizmor)
    /venv/bin/zizmor --persona pedantic --format json . > /out/zizmor.json || true
    ;;
  *)
    echo "usage: scan.sh tools|zizmor" >&2
    exit 2
    ;;
esac
