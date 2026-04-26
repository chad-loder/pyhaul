#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# shellcheck source=_shared.sh
source "$ROOT/.githooks/_shared.sh"

_signing="$(git config --bool --get commit.gpgsign 2>/dev/null || true)"
if [[ "$_signing" != "true" ]]; then
  if is_agent; then
    agent_refusal \
      "This repository requires cryptographically signed commits" \
      "Do not use --no-gpg-sign; if signing failed, re-authenticate (e.g. ssh-add) and ask the user for help"
  else
    echo "Error: commit.gpgsign must be true in this repository"
    echo "Run 'just setup' to repair your local configuration"
  fi
  exit 1
fi
