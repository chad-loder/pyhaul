# Contributing to pyhaul

Thanks for your interest in contributing. This document covers the dev
environment and how we use commits and pull requests.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (package manager)
- [just](https://just.systems/) (command runner) --
  `brew install just` or `cargo install just`

## Development setup

```bash
git clone https://github.com/chad-loder/pyhaul.git
cd pyhaul
just dev
```

`just dev` runs `just setup` (install deps, git hooks) then `just test`.
That single command is all you need to go from clone to green test suite.

**Branching:** work on a topic branch and open pull requests to **`main`**. We
do not use a long-lived `dev` branch.

## Common commands

Run `just` with no arguments to see all available recipes, organized by
group:

```text
$ just
Available recipes:
    [build]
    build

    [dev]
    clean
    dev
    renovate-validate
    run-cli *ARGS
    setup

    [docs]
    docs              # Build docs site to site/ directory

    [quality]
    check
    lint              # Lint tracked Python, shell, docs, spelling (contributor-facing)
    lint-all
    lint-fix
    lint-maintainer
    maintain          # `uv sync`, then workflow / security tooling (matches `maintain` PEP 735 deps)
    test
```

The most common workflow:

| Command | When to use it |
|---|---|
| `just dev` | First-time setup, or after pulling new deps |
| `just check` | Before pushing (lint + test) |
| `just lint` | Quick lint-only pass (code, shell, docs, spelling) |
| `just lint-maintainer` | Lint workflows and CI config (actionlint, zizmor, schemas) |
| `just maintain` | `uv sync` then same as `lint-maintainer` (refresh env first) |
| `just lint-all` | Both `lint` + `lint-maintainer` |
| `just lint-fix` | Auto-fix lint issues (ruff, rumdl) |
| `just test` | Run pytest |
| `just docs` | Build docs site (strict mode) |
| `just build` | Build sdist + wheel |
| `just clean` | Remove caches and build artifacts |

<details>
<summary>Without just (raw uv commands)</summary>

The justfile is a convenience layer, not a gatekeeper. Every recipe wraps
standard tools you can run directly:

```bash
uv sync --all-groups              # install deps
uv run pytest                     # tests
uv run coverage run -m pytest     # tests + coverage
uv run ruff check .               # lint
uv run ruff format .              # format
uv run mypy src tests             # type-check (mypy)
uv run pyright                    # type-check (pyright)
uv run ty check                   # type-check (ty)
uv run prek run --all-files       # run all git hooks
uv build                          # build sdist + wheel
```

</details>

<details>
<summary>Running individual linters</summary>

The `lint` recipe runs Python, shell, and markdown linters together.
`lint-maintainer` runs workflow-specific tools. Sub-recipes are hidden
from `just --list` but still directly callable:

```bash
just _lint-py         # ruff + mypy + pyright + ty + validate-pyproject + interrogate + semgrep
just _lint-sh         # shellcheck
just _lint-docs       # rumdl (markdown)
just _lint-spell      # codespell
just _lint-workflows  # actionlint + check-jsonschema + zizmor (maintainer)
```

</details>

## Git hooks

Hooks are installed automatically by `just dev`. To re-install manually:

```bash
uv run prek install --install-hooks
```

Hooks are split into two stages so WIP commits stay fast:

| Stage | Runs when | What it checks | Time |
|---|---|---|---|
| **pre-commit** | every `git commit` | ruff (lint + format with auto-fix), trailing whitespace, line endings, merge conflicts, large files, commit signing | <1 s |
| **pre-push** | every `git push` | YAML / TOML / JSON structure, actionlint, zizmor (workflow security), GitHub workflow schemas, Renovate config | ~5 s |

Heavier project-aware tools (mypy, pyright, codespell, semgrep, etc.)
run only in CI and in `just lint` — they're not in the hooks, so local
iteration stays snappy.

## Commit messages

This repository uses [Conventional Commits](https://www.conventionalcommits.org/).
The PR title is what matters most -- it's validated on every pull request
and is what `python-semantic-release` reads when deciding the next version.

| Type | Meaning | Pre-1.0 bump | Post-1.0 bump |
|---|---|---|---|
| `feat:` | new feature | patch | minor |
| `fix:` | bug fix | patch | patch |
| `perf:` | performance improvement | patch | patch |
| `refactor:` | internal change, no behavior diff | none | none |
| `docs:` | documentation | none | none |
| `test:` | tests only | none | none |
| `build:` | build system changes | none | none |
| `ci:` | CI changes | none | none |
| `chore:` | maintenance | none | none |
| `style:` | formatting, whitespace, etc. | none | none |

A breaking change is either a `!` after the type (`feat!: ...`) or a
`BREAKING CHANGE:` footer in the commit body. Either triggers a **major**
bump (or `0.x.0` bump pre-1.0).

Subject lines should:

- start with a **lowercase** verb (`add ...`, not `Add ...`)
- not end with a period
- be in the imperative mood (`add`, not `adds` / `added`)

## Releases (maintainers)

Releases are cut locally using `python-semantic-release` and published
via tag-triggered CI.

```bash
# 1. Prepare: stamps _version.py, CHANGELOG.md, creates release branch
just release-prepare

# 2. Review the changelog diff, commit, push, open PR
git add -A && git commit -m 'chore(release): vX.Y.Z'
git push -u origin HEAD
gh pr create --title 'chore(release): vX.Y.Z'

# 3. After CI passes and PR merges, tag to trigger release CI
just release-tag X.Y.Z
```

The tag push triggers the release workflow, which builds with
[BAIPP](https://github.com/hynek/build-and-inspect-python-package),
publishes to PyPI via OIDC, and creates an immutable GitHub Release
with SHA256 checksums and verified build attestations.

## Dependency updates

Dependency bumps are usually handled by a scheduled [Renovate](https://docs.renovatebot.com/)
workflow; its configuration is in `renovate.json`. If you edit that file,
run `just renovate-validate` to check it against the official schema before
you push.

## Reporting security issues

See [`SECURITY.md`](SECURITY.md) for scope and how to report (including
**public PRs or issues** if you prefer an open fix).
