set shell := ["bash", "-euo", "pipefail", "-c"]
set no-exit-message

# `LINT_VERBOSE`: per-tool “Running …” lines (dim) before quiet linters (unset ⇒ auto-on in CI).
# CI / verbosity are pure Just (`~/dev/just/tests/conditional.rs`). `quote` + `\` continuation for bundle assembly.

export LINT_VERBOSE := env('LINT_VERBOSE', '')
ok_msg := '''\033[32m  ok\033[0m'''

[private]
_lv := trim(env('LINT_VERBOSE', ''))

# Mirrors the previous Bash CI probe. `\` continuation so `:=` parses (~/dev/just/manual multi-line constructs).
[private]
_is_ci := if env('CI', '') == 'true' { 'yes' } else if env('CI', '') == '1' { 'yes' } else if env('CONTINUOUS_INTEGRATION', '') != '' { 'yes' } else if env('GITHUB_ACTIONS', '') != '' { 'yes' } else if env('GITLAB_CI', '') != '' { 'yes' } else if env('BUILDKITE', '') != '' { 'yes' } else if env('CIRCLECI', '') != '' { 'yes' } else if env('JENKINS_URL', '') != '' { 'yes' } else if env('TRAVIS', '') != '' { 'yes' } else if env('APPVEYOR', '') != '' { 'yes' } else if env('TF_BUILD', '') != '' { 'yes' } else if env('SYSTEM_TEAMFOUNDATIONCOLLECTIONURI', '') != '' { 'yes' } else { '' }

# Empty `LINT_VERBOSE` ⇒ defer to `_is_ci`. Explicit tokens override; unknown nonempty ⇒ never emit “Running…”.
[private]
_show_running := if _lv == '' { if _is_ci == 'yes' { 'yes' } else { '' } } else if _lv == '1' { 'yes' } else if _lv == 'true' { 'yes' } else if _lv == 'TRUE' { 'yes' } else if _lv == 'yes' { 'yes' } else if _lv == 'YES' { 'yes' } else if _lv == 'on' { 'yes' } else if _lv == 'ON' { 'yes' } else if _lv == '0' { '' } else if _lv == 'false' { '' } else if _lv == 'FALSE' { '' } else if _lv == 'no' { '' } else if _lv == 'NO' { '' } else if _lv == 'off' { '' } else if _lv == 'OFF' { '' } else { '' }

[private]
_shell_export := 'export JUST_SHOW_RUNNING=' + quote(_show_running)

[private]
_bash_lint_helpers := '''
  JUST_DIM=$'\033[2m'
  JUST_GREEN=$'\033[32m'
  JUST_RST=$'\033[0m'
  lint_running() {
    [[ "${JUST_SHOW_RUNNING:-}" == "yes" ]] || return 0
    printf '%s  ·  Running %s%s\n' "$JUST_DIM" "$1" "$JUST_RST" >&2
  }
  lint_ok() {
    printf '%s  ok%s  %s\n' "$JUST_GREEN" "$JUST_RST" "$1" >&2
  }
  run_semgrep() {
    lint_running "semgrep"
    local _tmp _code
    _tmp=$(mktemp) || { echo "semgrep: mktemp failed" >&2; return 1; }
    set +e
    uv run semgrep scan --config=auto --quiet --emacs --error --disable-version-check src/ 2>"$_tmp"
    _code=$?
    set -e
    if [ -s "$_tmp" ]; then
      grep -vE '^[┌│└├].*|^[[:space:]]*Semgrep[[:space:]]+CLI|^[[:space:]]*╭' "$_tmp" | sed '/^[[:space:]]*$/d' >&2 || true
    fi
    rm -f "$_tmp"
    if [ "$_code" -eq 0 ]; then
      lint_ok "semgrep"
    fi
    return "$_code"
  }
'''

[private]
_lint_bundle := _shell_export + "\n" + _bash_lint_helpers

[private]
default:
    @just --list

# --- Quality ---
# (`[group]` is for `just --list`; it is unrelated to PEP 735 `[dependency-groups]`.)

[doc('Lint tracked Python, shell, docs, spelling (contributor-facing)')]
[group('quality')]
lint: _lint-py _lint-sh _lint-docs _lint-spell
    @printf '%b  %s\n' "{{ ok_msg }}" "lint" >&2

[group('quality')]
lint-maintainer: _lint-workflows
    @printf '%b  %s\n' "{{ ok_msg }}" "lint-maintainer" >&2

[doc('`uv sync`, then workflow / security tooling (matches `maintain` PEP 735 deps)')]
[group('quality')]
maintain:
    uv sync
    @{{ just_executable() }} _lint-workflows
    @printf '%b  %s\n' "{{ ok_msg }}" "maintain" >&2

[group('quality')]
lint-all: lint lint-maintainer
    @printf '%b  %s\n' "{{ ok_msg }}" "lint-all" >&2

[group('quality')]
lint-fix: _lint-py-fix _lint-sh-fix _lint-docs-fix

[group('quality')]
test:
    uv run pytest

[group('quality')]
check: lint test

# --- Build ---

[group('build')]
build:
    uv run scripts/build/pypi_readme.py
    uv build

# --- Dev ---

[group('dev')]
dev: setup test

[group('dev')]
setup:
    uv sync --all-groups
    git config commit.gpgsign true
    uv run prek install --install-hooks
    @{{ just_executable() }} _setup-hooks

[group('dev')]
run-cli *ARGS:
    uv run python -m pyhaul {{ ARGS }}

[group('dev')]
clean:
    uv run pyclean . --debris all

# --- CI ---

[group('ci')]
ci: setup
    uv run pre-commit run --all-files --show-diff-on-failure
    uv run coverage run -m pytest
    uv run coverage xml -o coverage.xml

[group('ci')]
renovate-validate:
    @uv run check-jsonschema --schemafile "https://docs.renovatebot.com/renovate-schema.json" renovate.json

# --- Private ---

[private]
_lint-py:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ _lint_bundle }}
    lint_running "ruff check"
    uv run ruff check --quiet .
    lint_ok "ruff check"
    lint_running "ruff format"
    uv run ruff format --quiet --check .
    lint_ok "ruff format"
    lint_running "mypy"
    uv run mypy --no-error-summary src tests examples scripts
    lint_ok "mypy"
    lint_running "pyright"
    _out=$(uv run pyright 2>&1) || { echo "$_out"; exit 1; }
    lint_ok "pyright"
    lint_running "ty"
    uv run ty check --quiet --quiet
    lint_ok "ty"
    lint_running "validate-pyproject"
    uv run validate-pyproject pyproject.toml > /dev/null
    lint_ok "validate-pyproject"
    lint_running "interrogate"
    uv run interrogate --quiet src/pyhaul/
    lint_ok "interrogate"
    run_semgrep

[private]
_lint-py-fix:
    uv run ruff check --fix .
    uv run ruff format .

[private]
_lint-sh:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v shellcheck >/dev/null || {
      echo 'error: install shellcheck (e.g. brew install shellcheck)' >&2
      exit 1
    }
    {{ _lint_bundle }}
    targets=()
    while IFS= read -r _f; do
        targets+=("$_f")
    done < <(git ls-files '*.sh')
    for hook in .githooks/commit-msg .githooks/pre-commit; do
        [[ -f "$hook" ]] && targets+=("$hook")
    done
    (( ${#targets[@]} == 0 )) && exit 0
    lint_running "shellcheck"
    shellcheck -f quiet -x "${targets[@]}"
    lint_ok "shellcheck"

[private]
_lint-sh-fix:
    @{{ just_executable() }} _lint-sh || (printf '\033[33mNote: shellcheck has no auto-fix mode — review the output above and fix manually.\033[0m\n' >&2 && exit 1)

[private]
_lint-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ _lint_bundle }}
    lint_running "rumdl"
    uv run rumdl check --quiet .
    lint_ok "rumdl"

[private]
_lint-workflows:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v actionlint >/dev/null || {
      echo 'error: install actionlint (e.g. brew install actionlint)' >&2
      exit 1
    }
    {{ _lint_bundle }}
    lint_running "actionlint"
    actionlint -no-color
    lint_ok "actionlint"
    lint_running "check-jsonschema (renovate.json)"
    uv run check-jsonschema --schemafile "https://docs.renovatebot.com/renovate-schema.json" renovate.json
    lint_ok "check-jsonschema (renovate.json)"
    lint_running "zizmor"
    uv run zizmor -q .
    lint_ok "zizmor"

[private]
_lint-docs-fix:
    uv run rumdl fmt .

[private]
_lint-spell:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ _lint_bundle }}
    lint_running "codespell"
    uv run codespell src tests docs examples scripts *.md *.toml
    lint_ok "codespell"

[private]
_safe-install src dest:
    #!/usr/bin/env bash
    set -euo pipefail
    _src={{ quote(src) }}
    _dest={{ quote(dest) }}
    SOURCE_HASH=$(git hash-object "$_src")
    DEST_HASH=$(git hash-object "$_dest" 2>/dev/null || echo "none")
    if [[ "$DEST_HASH" != "none" ]] && [[ "$SOURCE_HASH" == "$DEST_HASH" ]]; then
      exit 0
    fi
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
