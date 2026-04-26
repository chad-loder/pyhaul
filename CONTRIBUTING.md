# Contributing to pyhaul

Thanks for your interest in contributing. This document covers the dev
environment, the commit/PR conventions, and the release workflow.

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

## Common commands

Run `just` with no arguments to see all available recipes, organized by
group:

```text
$ just
Available recipes:
    [build]
    build             # Generate PyPI README and build sdist + wheel

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
    test              # Run test suite
```

The most common workflow:

| Command | When to use it |
|---|---|
| `just dev` | First-time setup, or after pulling new deps |
| `just check` | Before pushing (runs lint + test) |
| `just lint` | Quick lint-only pass (code, shell, docs) |
| `just lint-maintainer` | Lint workflows and CI config (actionlint, zizmor, schemas) |
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

## Pre-commit hooks

Hooks are installed automatically by `just dev`. To re-install manually:

```bash
uv run pre-commit install
```

The hook runs ruff (lint + format), rumdl (markdown), actionlint, zizmor
(workflow security), schema validation, and structural checks (trailing
whitespace, merge conflicts, large files) on every commit.

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

## Release workflow

We don't cut releases manually. The flow is:

1. Merge PRs to `main`. Each PR title is a conventional commit.
2. On every push to `main`, the **Release** workflow runs
   [`release-please`](https://github.com/googleapis/release-please),
   which opens or updates a **"Release PR"** with the next version,
   updated `CHANGELOG.md`, and bumped `pyproject.toml`.
3. When ready to release, merge the Release PR. That triggers:
   - A git tag (e.g. `v0.2.0`) and a GitHub Release.
   - The `publish` job, which builds via `uv build` and publishes to
     PyPI using **Trusted Publishing** (OIDC; no API tokens).

### Nightly builds

A scheduled workflow (`nightly.yml`) runs once a day. It stamps a
`.devYYYYMMDD` suffix on the current `pyproject.toml` version, builds
sdist + wheel, and publishes to **TestPyPI**. To install a nightly:

```bash
uv pip install --index-url https://test.pypi.org/simple/ \
               --extra-index-url https://pypi.org/simple/ \
               pyhaul
```

## Dependency updates

Dependencies are kept current automatically by
[Renovate](https://docs.renovatebot.com/), running as a self-hosted
GitHub Actions workflow (no third-party app install). The configuration
lives in `renovate.json` and applies these policies:

- **7-day cooldown** on all new releases (Python deps, pre-commit hooks,
  GitHub Actions) to let the community catch malicious or broken packages
  before we adopt them.
- **Vulnerability fixes skip the cooldown** and are labeled `security`.
- **Dev/lint tools** (ruff, mypy, pyright, etc.) and **test deps** (pytest,
  hypothesis, etc.) are grouped into single PRs and automerged via branch
  merge on patch/minor bumps if CI passes. Pre-1.0 packages are excluded
  from automerge.
- **Lock file maintenance** runs weekly.
- **OSV vulnerability summary** appears on the Dependency Dashboard issue.

If you edit `renovate.json`, run `just renovate-validate` to check it
against the official schema before pushing.

<details>
<summary>Maintainer setup (GitHub App token)</summary>

The Renovate workflow authenticates via a GitHub App to get short-lived
tokens (no long-lived PATs). One-time setup:

1. [Create a GitHub App](https://github.com/settings/apps/new) with these
   repository permissions:
   - **Read+write:** Contents, Pull requests, Checks, Commit statuses,
     Issues, Workflows
   - **Read-only:** Administration, Vulnerability alerts, Members
2. Install the app on the `chad-loder/pyhaul` repository.
3. Create a GitHub **environment** named `renovate` in the repository
   settings (Settings > Environments). Optionally restrict it to the
   `main` branch.
4. Add the app's numeric ID as an environment secret named
   `RENOVATE_APP_ID`.
5. Generate a private key and add it as an environment secret named
   `RENOVATE_APP_PRIVATE_KEY`.

The workflow runs weekly on Monday mornings and can be triggered manually
from the Actions tab (with an optional dry-run mode).

</details>

## Reporting security issues

See [`SECURITY.md`](SECURITY.md). Please do **not** file public issues
for vulnerabilities.
