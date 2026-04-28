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
    build             # Regenerate docs/PYPI_README.md then build sdist + wheel

    [ci]
    ci                # Full CI run (setup + pre-commit + pytest with coverage)
    renovate-validate # Validate renovate.json against official schema

    [dev]
    clean             # Remove caches and build artifacts
    dev               # Setup environment and run tests (first-time onboarding)
    run-cli *ARGS     # Run the pyhaul CLI from source tree
    setup             # Install deps, hooks, and tools

    [quality]
    check             # Lint and test (pre-push sanity check)
    lint              # Lint code, shell, and docs (contributor-facing)
    lint-all          # Run all linters (contributor + maintainer)
    lint-fix          # Auto-fix everything fixable, then check
    lint-maintainer   # Lint workflows, actions security, and CI config (maintainer-facing)
    maintain          # uv sync then maintainer lints (workflows, schemas, zizmor)
    test              # Run test suite
```

The most common workflow:

| Command | When to use it |
|---|---|
| `just dev` | First-time setup, or after pulling new deps |
| `just check` | Before pushing (runs lint + test) |
| `just lint` | Quick lint-only pass (code, shell, docs) |
| `just lint-maintainer` | Lint workflows and CI config (actionlint, zizmor, schemas) |
| `just maintain` | `uv sync` then same as `lint-maintainer` (refresh env first) |
| `just lint-all` | Both `lint` + `lint-maintainer` |
| `just lint-fix` | Auto-fix lint issues, then check |
| `just test` | Run pytest |
| `just build` | Build sdist + wheel |
| `just clean` | Remove caches and build artifacts |

<details>
<summary>Without just (raw uv commands)</summary>

The justfile is a convenience layer, not a gatekeeper. Every recipe wraps
standard tools you can run directly:

```bash
uv sync --all-groups              # install deps
uv run pytest                     # tests
uv run pytest --cov=pyhaul         # tests + coverage
uv run ruff check .               # lint
uv run ruff format .              # format
uv run mypy src tests examples scripts  # type-check (mypy)
uv run pyright                    # type-check (pyright, strict)
uv build                          # build sdist + wheel
```

</details>

<details>
<summary>Running individual linters</summary>

The `lint` recipe runs Python, shell, and markdown linters together.
`lint-maintainer` runs workflow-specific tools. Sub-recipes are hidden
from `just --list` but still directly callable:

```bash
just _lint-py         # ruff + mypy + pyright
just _lint-sh         # shellcheck
just _lint-docs       # rumdl (markdown)
just _lint-workflows  # actionlint + check-jsonschema + zizmor
```

</details>

## Git hooks

Hooks are installed automatically by `just dev`. To re-install manually:

```bash
uv run prek install
```

Bulk linting runs as a **pre-push** hook (not pre-commit), so WIP commits
stay fast and you won't be tempted to `--no-verify`. The hook runs ruff
(lint + format), actionlint, zizmor (workflow security), schema
validation, codespell, and structural checks (trailing whitespace, merge
conflicts, large files) before code is pushed. Commit signing is enforced
at commit time via a separate `commit-msg` hook.

## Commit messages

This repository uses [Conventional Commits](https://www.conventionalcommits.org/).
The PR title is what matters most -- it's validated on every pull request
and is what `release-please` reads when deciding the next version.

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

## Dependency updates

Dependency bumps are usually handled by a scheduled [Renovate](https://docs.renovatebot.com/)
workflow; its configuration is in `renovate.json`. If you edit that file,
run `just renovate-validate` to check it against the official schema before
you push.

## Reporting security issues

See [`SECURITY.md`](SECURITY.md) for scope and how to report (including
**public PRs or issues** if you prefer an open fix).
