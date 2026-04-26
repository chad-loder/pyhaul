set shell := ["bash", "-euo", "pipefail", "-c"]
set no-exit-message

[private]
default:
    @just --list

# --- Quality ---

# Lint code, shell, docs, and spelling (contributor-facing)
[group('quality')]
lint: _lint-py _lint-sh _lint-docs _lint-spell
    @printf '\033[32m✓ lint\033[0m\n'

# Lint workflows, actions security, and CI config (maintainer-facing)
[group('quality')]
lint-maintainer: _lint-workflows
    @printf '\033[32m✓ lint-maintainer\033[0m\n'

# Run all linters (contributor + maintainer)
[group('quality')]
lint-all: _lint-py _lint-sh _lint-docs _lint-spell _lint-workflows
    @printf '\033[32m✓ lint-all\033[0m\n'

# Auto-fix everything fixable, then check
[group('quality')]
lint-fix: _lint-py-fix _lint-sh-fix _lint-docs-fix

# Run test suite
[group('quality')]
test:
    uv run pytest

# Lint and test (pre-push sanity check)
[group('quality')]
check: lint test

# --- Build ---

# Generate PyPI README and build sdist + wheel
[group('build')]
build:
    uv run scripts/build/pypi_readme.py
    uv build

# --- Dev ---

# Setup environment and run tests (first-time onboarding)
[group('dev')]
dev: setup test

# Install deps, hooks, and tools
[group('dev')]
setup:
    uv sync --all-groups
    git config commit.gpgsign true
    uv run pre-commit install --install-hooks
    @{{ just_executable() }} _setup-hooks

# Run the pyhaul CLI from source tree
[group('dev')]
run-cli *ARGS:
    uv run python -m pyhaul {{ ARGS }}

# Remove caches and build artifacts
[group('dev')]
clean:
    uvx pyclean . --debris all

# --- CI ---

# Full CI run (setup + pre-commit + pytest with coverage)
[group('ci')]
ci: setup
    uv run pre-commit run --all-files --show-diff-on-failure
    uv run coverage run -m pytest
    uv run coverage xml -o coverage.xml

# Validate renovate.json against official schema
[group('ci')]
renovate-validate:
    @uvx check-jsonschema --schemafile "https://docs.renovatebot.com/renovate-schema.json" renovate.json

# --- Private sub-recipes (callable directly, hidden from --list) ---

[private]
_lint-py:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run ruff check --quiet .
    uv run ruff format --quiet --check .
    uv run mypy --no-error-summary src tests examples scripts
    _out=$(uv run pyright 2>&1) || { echo "$_out"; exit 1; }
    uv run ty check --quiet --quiet
    uv run validate-pyproject pyproject.toml > /dev/null
    uv run interrogate --quiet src/pyhaul/
    uv run semgrep scan --config=auto --quiet --emacs --error src/

[private]
_lint-py-fix:
    uv run ruff check --fix .
    uv run ruff format .

[private]
_lint-sh:
    #!/usr/bin/env bash
    set -euo pipefail
    targets=()
    while IFS= read -r _f; do
        targets+=("$_f")
    done < <(git ls-files '*.sh')
    for hook in .githooks/commit-msg .githooks/pre-commit; do
        [[ -f "$hook" ]] && targets+=("$hook")
    done
    (( ${#targets[@]} == 0 )) && exit 0
    command -v shellcheck >/dev/null || { echo "error: install shellcheck (e.g. brew install shellcheck)" >&2; exit 1; }
    shellcheck -f quiet -x "${targets[@]}"

[private]
_lint-sh-fix:
    #!/usr/bin/env bash
    set +e
    {{ just_executable() }} _lint-sh
    _exit=$?
    set -e
    printf '\033[33mNote: shellcheck has no auto-fix mode — review the output above and fix manually.\033[0m\n' >&2
    exit $_exit

[private]
_lint-docs:
    @uvx rumdl check --quiet .

[private]
_lint-workflows:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v actionlint >/dev/null || { echo "error: install actionlint (e.g. brew install actionlint)" >&2; exit 1; }
    actionlint -no-color
    uvx check-jsonschema --schemafile "https://docs.renovatebot.com/renovate-schema.json" renovate.json
    uvx zizmor -q .

[private]
_lint-docs-fix:
    uvx rumdl fix .

[private]
_lint-spell:
    @uv run codespell src tests docs examples scripts *.md *.toml

# Copies tracked hook scripts into .git/hooks, aborting on local edits.
[private]
_safe-install src dest:
    #!/usr/bin/env bash
    set -euo pipefail
    _src={{ quote(src) }}
    _dest={{ quote(dest) }}
    SOURCE_HASH=$(git hash-object "$_src")
    DEST_HASH=$(git hash-object "$_dest" 2>/dev/null || echo "none")
    if [[ "$DEST_HASH" != "none" && "$SOURCE_HASH" != "$DEST_HASH" ]]; then
      if ! git rev-list --all --objects | grep -q "$DEST_HASH"; then
        echo "ERROR: Local changes in $_dest"
        diff -u --label "REPO" --label "LOCAL" "$_src" "$_dest" || true
        exit 1
      fi
    fi
    install -m 755 "$_src" "$_dest"

[private]
_setup-hooks:
    @{{ just_executable() }} _safe-install .githooks/commit-msg .git/hooks/commit-msg
