# Patterns from hynek's structlog/attrs CI

Reference workflows studied:
- [structlog CI](https://github.com/hynek/structlog/blob/main/.github/workflows/ci.yml)
- [structlog pypi-package](https://github.com/hynek/structlog/blob/main/.github/workflows/pypi-package.yml)
- [attrs CI](https://github.com/python-attrs/attrs/blob/main/.github/workflows/ci.yml)
- [attrs pypi-package](https://github.com/python-attrs/attrs/blob/main/.github/workflows/pypi-package.yml)
- [build-and-inspect-python-package](https://github.com/hynek/build-and-inspect-python-package) (BAIPP)

---

## 1. Build-first architecture ("Build Once, Test Against Wheel")

**What:** The first CI job builds the wheel and sdist (e.g. via BAIPP). The test
job downloads the `Packages` artifact, **extracts the sdist** into the workspace,
then **`rm -rf src`**. The package under test is **only** the one installed
from `dist/*.whl` (structlog does that with tox `--installpkg`; the same
constraint can be done with `uv sync --no-install-project` + `uv pip install
...whl`). You still keep whatever the git checkout and/or sdist provide for
`tests/`, `pyproject.toml`, and `uv.lock` (if not in the sdist, the checked-out
lockfile remains).

**Why `tar xf` and not only `rm -rf src`:** Extracting the sdist snapshots the
packaged tree (tests, metadata) so it matches what the built artifacts contain;
`rm -rf src` is the part that **guarantees** Python cannot import the in-repo
source tree, so `import pyhaul` is resolved from **site-packages only**. That
is stronger than "install the wheel but leave `src/` in place" (pytest,
`PYTHONPATH`, or `pythonpath` can still point at the tree by mistake). Structlog’s
`tests` job is **single-OS (Ubuntu only)**; on Windows you can run the same
`tar` + `rm` steps under **`shell: bash`**, which GitHub-hosted runners provide.

**How structlog does it (wheel install via tox):**
```yaml
- run: |
    tar xf dist/*.tar.gz --strip-components=1
    rm -rf src
- run: >
    uvx --with tox-uv tox run
    --installpkg dist/*.whl
    -f ${PYTHON}-tests
```

`--installpkg` is a **tox** flag: it installs the pre-built wheel into the tox
environment as the test dependency. The tests themselves do not point at
`./src/`.

**Coverage with this layout:** Traced file paths are under `site-packages/…` (or
the venv’s `lib/…`), not `./src/…`. In `pyproject`, set **`[tool.coverage.run]
source = ["<package>"]` using the import name** (e.g. `pyhaul`), not
`source = ["src/..."]`, so `coverage report` and `coverage combine` match what
CI executes. structlog’s **separate** `coverage` job downloads `.coverage.*`
from each matrix cell (`coverage-data-*`), runs `coverage combine`, writes a
markdown report to `$GITHUB_STEP_SUMMARY`, and fails with
`coverage report --fail-under=…` (or `fail_under` in config).

**Why it matters:**
- Catches forgotten files: if a module isn't in the wheel, tests fail.
- Catches packaging bugs: if `pyproject.toml` metadata is wrong, tests fail.
- Ensures test fidelity: users install wheels, not source trees.
- **Separates** “editable / tree-based” dev smoke tests (e.g. `install-dev` with
  `-e .`) from “wheel-only” tests.

**Without tox (same invariants):** `uv sync --frozen --all-groups
--no-install-project` to install test deps, then
`uv pip install --force-reinstall --no-deps dist/<pkg>-*.whl`, then run pytest
after `src/` is gone. With **uv**, plain `uv run pytest` will try to **re-sync /
build** the workspace from `pyproject.toml` and fail with “Expected … at
`src/<pkg>/__init__.py`”. Use **`uv run --no-sync pytest …`** so the tool
command uses the existing venv (with the wheel already installed) without
rebuilding the tree.

**Wheel contents vs tests:** If the build backend **excludes** modules from the
wheel (e.g. optional `source-exclude` for `cli.py`), any test that imports those
modules will fail in wheel-only runs. Either ship those modules in the wheel or
constrain / skip those tests in the wheel job.

**Status:** **Done** in our `ci.yml` (sdist extract + `rm -rf src`, wheel install
via `uv`, `uv run --no-sync pytest`, combined coverage job).

**Effort (historical):** Medium for the first wiring; maintenance is low.

---

## 2. Replace manual build job with `build-and-inspect-python-package`

**What:** A single GitHub Action that:
- Builds wheel + sdist (via `uv build`)
- Sets `SOURCE_DATE_EPOCH` for reproducible builds
- Runs `twine check` (validates metadata + README rendering)
- Runs `check-wheel-contents` (lints wheel structure)
- Uploads built artifacts as `Packages`
- Generates job summaries with sdist/wheel tree views
- Extracts `supported_python_classifiers_json_array` for matrix generation

**Our current build job (10 steps):**
```yaml
- uses: actions/checkout@...
- uses: astral-sh/setup-uv@...
- run: uv build
- run: uvx twine check dist/*
- uses: actions/upload-artifact@...
```

**BAIPP replacement (2 steps):**
```yaml
- uses: actions/checkout@...
- uses: hynek/build-and-inspect-python-package@v2
```

**Why it matters:**
- Less code to maintain.
- `check-wheel-contents` catches things `twine check` doesn't (duplicate files,
  `__pycache__`, test directories leaking into wheel).
- Reproducible builds via `SOURCE_DATE_EPOCH`.
- Job summary shows wheel/sdist contents in the GitHub UI without downloading.

**Effort:** Low. Drop-in replacement for our `build` job in both `ci.yml` and
`release.yml`.

---

## 3. Dynamic Python version matrix from package metadata

**What:** Instead of hardcoding `["3.12", "3.13", "3.14"]` in the CI matrix,
BAIPP reads trove classifiers from your package metadata and outputs the
supported versions as a JSON array.

**How structlog does it:**
```yaml
build-package:
  steps:
    - uses: hynek/build-and-inspect-python-package@v2
      id: baipp
  outputs:
    python-versions: ${{ steps.baipp.outputs.supported_python_classifiers_json_array }}

tests:
  strategy:
    matrix:
      python-version: ${{ fromJson(needs.build-package.outputs.python-versions) }}
```

**Why it matters:**
- Single source of truth: update `pyproject.toml` classifiers and CI
  automatically adjusts.
- Eliminates drift between "versions we claim to support" and "versions we
  test."

**Prerequisite:** Our `pyproject.toml` must have accurate
`Programming Language :: Python :: 3.X` classifiers.

**Effort:** Low once BAIPP is adopted.

---

## 4. `re-actors/alls-green` as a merge gate

**What:** A single `required-checks-pass` job that depends on every other job
and uses `alls-green` to succeed only if all dependencies succeeded. Branch
protection requires only this one job name.

**How structlog does it:**
```yaml
required-checks-pass:
  name: Ensure everything required is passing for branch protection
  if: always()
  needs:
    - coverage
    - install-dev
    - typing
    - docs
  runs-on: ubuntu-latest
  steps:
    - uses: re-actors/alls-green@v1.2.2
      with:
        jobs: ${{ toJSON(needs) }}
```

**Why it matters:**
- Adding, removing, or renaming CI jobs doesn't require updating branch
  protection rules.
- Handles the "skipped job counts as passing" gotcha correctly.
- The `if: always()` ensures this job runs even when dependencies fail, so it
  can report the failure to branch protection.

**Effort:** Very low. Add one job, update branch protection to require only
`required-checks-pass`.

---

## 5. `permissions: {}` at workflow level

**What:** Set empty permissions at the workflow level, then grant specific
permissions per-job.

**Our current approach:**
```yaml
permissions:
  contents: read
```

**hynek's approach:**
```yaml
permissions: {}
```

**Why it matters:**
- Principle of least privilege: no job gets any permission unless explicitly
  declared.
- Prevents accidental token leakage if a new job is added without thinking
  about permissions.
- Our `release.yml` already does this correctly. Our `ci.yml` should match.

**Effort:** Trivial. Change one line, verify no job breaks (they shouldn't since
`actions/checkout` doesn't need `contents: read` with
`persist-credentials: false`).

---

## 6. `persist-credentials: false` on every checkout

**What:** Prevent the GITHUB_TOKEN from lingering in the `.git/config` after
checkout.

**Why it matters:**
- The token could be exfiltrated by a compromised dependency during
  `npm install`, `pip install`, or any build step that reads the git config.
- We already do this in both workflows.

**Status:** Already implemented. No action needed.

---

## 7. Separate CI and publish workflows

**What:** Two distinct workflow files:
- `ci.yml` — triggers on push/PR, handles lint + test + coverage
- `pypi-package.yml` — triggers on push-to-main (Test PyPI) and
  release-published (real PyPI)

**Why it matters:**
- `id-token: write` (needed for publishing) is never granted in the CI workflow.
- The publish workflow is a minimal blast radius: build + publish, nothing else.
- Reduces the attack surface if someone compromises a test dependency.

**Our current state:** We have `ci.yml` and `release.yml` which is close. The
main difference is that structlog uploads every main-branch commit to Test PyPI,
giving continuous visibility into what the package looks like on a real index.

**Effort:** Low. We'd add a `release-test-pypi` job to our release workflow
gated on `push to main`.

---

## 8. Coverage aggregation with fail-under threshold

**What:** Each test matrix cell uploads `.coverage.*` files as separate
artifacts. A dedicated `coverage` job downloads all of them, combines them,
writes a markdown report to `$GITHUB_STEP_SUMMARY`, and fails if coverage is
below the threshold. On failure, the HTML report is uploaded for debugging.

**How structlog does it:**
```yaml
coverage:
  name: Ensure 100% test coverage
  needs: tests
  if: always()
  steps:
    - uses: actions/download-artifact@...
      with:
        pattern: coverage-data-*
        merge-multiple: true
    - run: |
        coverage combine
        coverage html --skip-covered --skip-empty
        coverage report --format=markdown >> $GITHUB_STEP_SUMMARY
        coverage report --fail-under=100
    - uses: actions/upload-artifact@...
      with:
        name: html-report
        path: htmlcov
      if: ${{ failure() }}
```

**Why it matters:**
- Multi-platform coverage is combined correctly (Ubuntu + Windows).
- The markdown summary renders directly in the GitHub Actions UI.
- Failed coverage uploads the HTML report so you can click through to see
  exactly what's missing.
- `if: always()` ensures coverage is reported even when some matrix cells fail.

**Our current approach:** Each `test-stable` cell uploads
`.coverage.*` to `coverage-data-<os>-<python>`. A `coverage` job downloads all
`coverage-data-*` artifacts, runs `coverage combine` + `coverage report` (with
`fail_under` in `pyproject`), writes markdown to `$GITHUB_STEP_SUMMARY`, and
emits a single `coverage.xml` for Codecov. This matches structlog; we use a
**combined** Codecov upload instead of per-cell XML only.

**Status:** **Done.**

---

## 9. `install-dev` smoke test

**What:** A dedicated job that runs `uv sync` + `python -Ic 'import pyhaul'`
on both Ubuntu and Windows.

**Why it matters:**
- Catches "the dev environment doesn't even install" regressions.
- Runs on Windows too, where path handling and optional C extensions may differ.
- Very fast (< 30s), minimal CI cost.

**Effort:** Trivial. Add a 10-line job.

---

## 10. `hynek/setup-cached-uv`

**What:** A lightweight composite action for installing uv with caching.

**Why it matters:**
- Slightly simpler than `astral-sh/setup-uv` with its many options.
- Hynek uses it consistently; it's battle-tested in his projects.

**Our current approach:** We use `astral-sh/setup-uv@v8.1.0` which works fine.

**Verdict:** Lateral move. Not worth switching unless we want to reduce our
action dependency surface. `astral-sh/setup-uv` is the official action from the
uv authors and is likely to track uv features more closely.

---

## 11. Dual attestation strategy (GitHub + PyPI)

This is the most nuanced pattern and directly relates to our existing
attestation setup.

### Background: two types of attestations

There are **two distinct attestation systems**, both using Sigstore under the
hood but serving different purposes:

| | GitHub Artifact Attestations | PyPI Publish Attestations |
|---|---|---|
| **Action** | `actions/attest-build-provenance` | `pypa/gh-action-pypi-publish` (with `attestations: true`) |
| **When** | After build, before publish | During upload to PyPI |
| **What it proves** | "This artifact was built by this GitHub Actions run" | "This artifact was published to PyPI from this workflow identity" |
| **Standard** | SLSA build provenance | PEP 740 digital attestations |
| **Permissions** | `attestations: write` + `id-token: write` | `id-token: write` |
| **Where it's visible** | GitHub repo's attestation tab | PyPI project page |
| **Verification** | `gh attestation verify` | `pip` (experimental), `pypi-attestations` CLI |

### What we currently do

Our `release.yml` already does both:

1. **Build provenance** via `actions/attest-build-provenance@v4.1.0` in the
   `build` job (generates GitHub artifact attestations).
2. **Publish attestation** implicitly via `pypa/gh-action-pypi-publish@v1.14.0`
   in the `publish` job (since v1.11.0, publish attestations are generated by
   default when using Trusted Publishing).

This is actually the correct dual-attestation pattern.

### What hynek does

In `pypi-package.yml`:
```yaml
build-package:
  permissions:
    attestations: write
    id-token: write
  steps:
    - uses: hynek/build-and-inspect-python-package@v2
      with:
        attest-build-provenance-github: 'true'  # GitHub attestations

release-pypi:
  steps:
    - uses: pypa/gh-action-pypi-publish@v1.13.0
      with:
        attestations: true  # PyPI attestations
```

BAIPP wraps `actions/attest-build-provenance` internally when
`attest-build-provenance-github: 'true'` is set, so the build provenance
generation is bundled into the build step rather than being a separate step.

### How this integrates with our setup

If we switch to BAIPP for the build step in `release.yml`:

**Before (our current release.yml):**
```yaml
build:
  permissions:
    contents: read
    id-token: write
    attestations: write
  steps:
    - uses: actions/checkout@...
    - uses: astral-sh/setup-uv@...
    - run: uv build
    - uses: actions/attest-build-provenance@v4.1.0  # separate step
      with:
        subject-path: dist/*
    - uses: actions/upload-artifact@...
```

**After (with BAIPP):**
```yaml
build:
  permissions:
    attestations: write
    id-token: write
  steps:
    - uses: actions/checkout@...
    - uses: hynek/build-and-inspect-python-package@v2
      with:
        attest-build-provenance-github: 'true'  # replaces separate step
```

The `publish` job stays unchanged — `pypa/gh-action-pypi-publish` already
generates PEP 740 publish attestations by default.

**Result:** Both attestation layers remain intact, with less code.

### The attestation flow

```
Build job:
  uv build → wheel + sdist
  ├─ actions/attest-build-provenance (via BAIPP)
  │  └─ "This wheel was built by GitHub Actions run #N in repo X"
  │     → stored in GitHub's attestation store
  └─ upload-artifact → "Packages"

Publish job:
  download-artifact → dist/
  └─ pypa/gh-action-pypi-publish
     ├─ Trusted Publishing (OIDC identity)
     └─ PEP 740 publish attestation (via sigstore)
        └─ "This wheel was published to PyPI by workflow identity Y"
           → stored on PyPI alongside the package
```

Users can verify both:
- `gh attestation verify dist/pyhaul-*.whl --repo lodgeit-labs/pyhaul`
- PyPI's web UI shows a "provenance" badge for attested packages

---

## 12. `FORCE_COLOR` and `PIP_DISABLE_PIP_VERSION_CHECK`

**What:** Global env vars set at the workflow level:
```yaml
env:
  FORCE_COLOR: "1"
  PIP_DISABLE_PIP_VERSION_CHECK: "1"
  PIP_NO_PYTHON_VERSION_WARNING: "1"
```

**Why it matters:**
- `FORCE_COLOR: "1"` enables colored output in CI logs even though there's no
  TTY. Makes pytest, ruff, and uv output much more readable.
- `PIP_DISABLE_PIP_VERSION_CHECK` and `PIP_NO_PYTHON_VERSION_WARNING` suppress
  noisy warnings that clutter CI logs.

**Effort:** Trivial. Three lines at the top of the workflow.

---

## 13. `concurrency` with PR number for better cancellation

**What:**
```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

**Why it matters:**
- Using `pull_request.number` means each PR gets its own concurrency group.
- Pushing a new commit to a PR cancels the previous run for that PR.
- Push-to-main runs don't cancel each other (different `github.ref`).
- Our current `ci-${{ github.ref }}` is close but doesn't distinguish between
  workflow names, so if we had multiple workflows triggered by the same ref
  they'd interfere.

**Effort:** Trivial. Change one line.

---

## 14. `merge_group` trigger

**What:** attrs CI includes `merge_group:` in the `on:` triggers.

**Why it matters:**
- Supports GitHub's merge queue feature, which batches PRs and runs CI on the
  merged result before actually merging.
- We already have this in our `ci.yml`.

**Status:** Already implemented.

---

## 15. `interrogate` — docstring coverage enforcement

**What:** A pre-commit hook and CLI that measures docstring coverage across
your codebase and fails if it drops below a threshold.

**How structlog uses it:**
```toml
# pyproject.toml
[tool.interrogate]
omit-covered-files = true
verbose = 2
fail-under = 100
whitelist-regex = ["test_.*"]
```
```yaml
# .pre-commit-config.yaml
- repo: https://github.com/econchick/interrogate
  rev: 1.7.0
  hooks:
    - id: interrogate
      args: [tests]
```

Note: structlog runs `interrogate` only on `tests` in pre-commit — the
`fail-under = 100` in `pyproject.toml` applies when run against the full
codebase via tox/CI. The `whitelist-regex = ["test_.*"]` means test functions
are included in coverage counting (they need docstrings too, in structlog's
convention).

**Why it matters:**
- Prevents docstring rot: new public API surfaces that lack documentation are
  caught before merge.
- Complements type checking: types say *what*, docstrings say *why*.
- Configurable: you can exclude private methods, `__init__`, magic methods, etc.

**Applicability to pyhaul:** Moderate. We follow a "docstrings on public API"
convention. We could set `fail-under` to something reasonable (e.g., 80%) for
`src/` and exclude tests. Would catch regressions when adding new public
functions without docs.

**Effort:** Low. Add pre-commit hook + pyproject.toml config.

---

## 16. `codespell` — typo detection in source code

**What:** Scans source code, docs, and comments for common misspellings.
Dictionary-based (not a full spellchecker), so false positive rate is very low.

**How structlog uses it:**
```yaml
- repo: https://github.com/codespell-project/codespell
  rev: v2.4.2
  hooks:
    - id: codespell
      args: [-L, alog, -L, abl, --skip=*.svg]
```

The `-L` flags whitelist project-specific terms that look like typos but aren't
(e.g., `alog` is a valid structlog abbreviation).

**Why it matters:**
- Catches embarrassing typos in error messages, docstrings, and variable names.
- Near-zero false positives — it only flags known misspellings.
- Very fast (< 1s for a typical codebase).

**Applicability to pyhaul:** High. We have user-facing error messages,
docstrings, and CLI help text where typos would be embarrassing. We need to
whitelist `te` (`-L te`) which is a false positive from three legitimate uses:
the `except TransportError as te:` variable name, the HTTP `TE` header in
`headers.py`, and "Chunked TE" prose in `WHY.md`. With that whitelist, the
repo is clean.

**Configuration:**
```yaml
# .pre-commit-config.yaml
- repo: https://github.com/codespell-project/codespell
  rev: v2.4.2
  hooks:
    - id: codespell
      args: [-L, te]
```

**Effort:** Trivial. Add pre-commit hook.

---

## 17. `validate-pyproject` — schema validation for pyproject.toml

**What:** Validates `pyproject.toml` against JSON Schema definitions for
PEP 517/518/621/639/735 and optionally against SchemaStore schemas for tools
like ruff, mypy, pytest, etc.

**How structlog uses it:**
```yaml
- repo: https://github.com/abravalheri/validate-pyproject
  rev: v0.25
  hooks:
    - id: validate-pyproject
      additional_dependencies: ["validate-pyproject-schema-store[all]"]
```

The `schema-store[all]` extra validates not just the `[project]` and
`[build-system]` tables, but also `[tool.ruff]`, `[tool.mypy]`,
`[tool.pytest.ini_options]`, etc. against their respective schemas.

**Why it matters:**
- Catches typos in tool configuration keys (e.g., `[tool.ruff.lint.selct]`
  instead of `select`).
- Catches invalid values (e.g., wrong types for configuration options).
- Validates PEP compliance of the `[project]` table.
- Especially valuable when editing `pyproject.toml` by hand or via AI — the
  schema catches structural errors that would otherwise only surface at
  build/runtime.

**Applicability to pyhaul:** High. Our `pyproject.toml` is large (350+ lines)
with ruff, mypy, pyright, and pytest config. Schema validation catches silent
misconfigurations.

**Effort:** Trivial. Add pre-commit hook.

---

## 18. Multiple type checkers in CI (`mypy` + `pyright` + `ty` + `pyrefly`)

**What:** Structlog runs four type checkers:
- `mypy` — the standard, mature type checker
- `pyright` — Microsoft's type checker (used by Pylance/VS Code)
- `ty` — Astral's new Rust-based type checker (from the ruff team)
- `pyrefly` — Meta's experimental type checker

All four are run in the `typing` tox environment:
```ini
[testenv:typing-{pyright,ty,mypy,pyrefly}]
commands =
  mypy: mypy src
  pyrefly: pyrefly check
  pyright: pyright tests/typing
  ty: ty check
```

**Why structlog does this:** As a foundational logging library used by
thousands of projects, structlog needs to be correct under every type checker
its users might run. Each checker has different inference engines, different
strictness defaults, and catches different classes of bugs.

**Applicability to pyhaul:** We already run mypy + pyright. Adding `ty` is
interesting because it's from the ruff team (Astral) and will likely become
the dominant type checker for the Python ecosystem.

**Suppression comment gotcha (ty >= 0.0.25):** Since v0.0.25, `ty` no longer
treats `# type: ignore[mypy-code]` as blanket suppression. It now requires
either bare `# type: ignore`, or `# type: ignore[ty:ty-rule]`, or
`# ty: ignore[ty-rule]`. Our codebase uses mypy-specific codes like
`# type: ignore[arg-type]` throughout, which ty silently ignores, producing
false positives.

**Recommended approach: scope ty to `src/` only (like structlog).** Structlog
configures `[tool.ty.src] include = ["tests/typing"]` to run ty on a narrow
subset. We should scope ty to `src/pyhaul/` (skipping tests, which are
full of deliberate type mismatches and mocks) and add a small number of
`# ty: ignore[...]` comments to the ~3 places in `src/` that legitimately
need suppression (e.g., `**kw` unpacking in `cli.py`, the `_urllib3_exc = None`
fallback). This keeps ty useful for catching real type errors in the library
code without drowning in test noise.

**Configuration:**
```toml
[tool.ty.src]
include = ["src"]

[tool.ty.rules]
invalid-argument-type = "warn"  # downgrade to warn while stabilizing
```

**Effort:** Low. Add config + ~3 `ty: ignore` comments in `src/`.

---

## 19. `pytest` strict configuration

**What:** Structlog's pytest config is notably strict:
```toml
[tool.pytest.ini_options]
addopts = ["--strict-markers", "--strict-config", "--import-mode=importlib"]
xfail_strict = true
filterwarnings = ["once::Warning"]
```

- `--strict-markers`: Unknown markers cause errors (catches typos in
  `@pytest.mark.foo`).
- `--strict-config`: Invalid config keys in `pyproject.toml`'s pytest section
  cause errors.
- `--import-mode=importlib`: Uses importlib rather than path-based imports,
  which avoids conftest.py shadowing issues and is more correct for
  `src/`-layout packages.
- `xfail_strict = true`: Tests marked `@pytest.mark.xfail` that unexpectedly
  pass are treated as failures. Prevents xfail from hiding fixed bugs.
- `filterwarnings = ["once::Warning"]`: Shows each unique warning once.
  Prevents warnings from being silently swallowed.

**Applicability to pyhaul:** High. We should adopt all of these. They're
zero-cost quality gates.

**Effort:** Trivial. Add/update a few lines in `pyproject.toml`.

---

## 20. Ruff `select = ["ALL"]` with explicit ignores

**What:** Instead of selecting specific rule sets, structlog enables *every*
ruff rule and then explicitly ignores the ones that don't fit:

```toml
[tool.ruff.lint]
select = ["ALL"]
ignore = [
  "A",    # shadowing is fine
  "ANN",  # Mypy is better at this
  "ARG",  # unused arguments are common w/ interfaces
  # ... 20+ rules with comments explaining why each is ignored
]
```

**Why this approach is better:**
- New ruff rules are automatically enabled when you update ruff. You benefit
  from new checks without having to discover and add them.
- The ignore list serves as documentation: each disabled rule has a comment
  explaining *why* it's disabled, making it a deliberate policy decision rather
  than an oversight.
- Contrast with our approach of selecting specific rule sets (e.g.,
  `select = ["E", "F", "W", ...]`) — new rules are silently ignored until
  someone manually adds them.

**Applicability to pyhaul:** High, but requires an initial investment to audit
and configure the ignore list for our codebase.

**Effort:** Medium. Enable `ALL`, fix or ignore the resulting violations,
document each ignore.

---

## Summary: adoption priority

### CI architecture

| # | Pattern | Effort | Impact | Priority |
|---|---------|--------|--------|----------|
| 2 | BAIPP for build | Low | High | **Done** |
| 4 | `alls-green` merge gate | Very low | High | **Done** |
| 5 | `permissions: {}` | Trivial | Medium | **Done** |
| 12 | `FORCE_COLOR` env vars | Trivial | Low | **Done** |
| 13 | Better concurrency group | Trivial | Low | **Done** |
| 11 | Attestation via BAIPP | Low | Medium | With #2 |
| 9 | `install-dev` smoke test | Trivial | Medium | **Done** |
| 3 | Dynamic Python matrix | Low | Medium | **Done** |
| 7 | Test PyPI on main push | Low | Medium | After #2 |
| 8 | Coverage aggregation | Medium | High | **Done** |
| 1 | Test against wheel | Medium | High | **Done** |
| 10 | `setup-cached-uv` | Trivial | None | Skip |

### Quality tools

| # | Tool | Effort | Impact | Priority |
|---|------|--------|--------|----------|
| 16 | `codespell` (typo detection) | Trivial | Medium | **Done** |
| 17 | `validate-pyproject` (schema validation) | Trivial | Medium | **Done** |
| 19 | Strict pytest config | Trivial | Medium | **Done** |
| 15 | `interrogate` (docstring coverage) | Low | Medium | **Done** |
| 18 | Add `ty` type checker (scoped to `src/`) | Low | Medium | **Done** |
| 20 | Ruff `select = ["ALL"]` | Medium | High | **Done** |
