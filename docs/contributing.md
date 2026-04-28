# Contributing

The full contributing guide is maintained at
[CONTRIBUTING.md](https://github.com/chad-loder/pyhaul/blob/main/CONTRIBUTING.md)
in the repository root.

## Quick start

```bash
git clone https://github.com/chad-loder/pyhaul.git && cd pyhaul
uv sync --all-groups
just setup       # installs git hooks and dependencies
just check       # lint + test
```

## Key commands

| Command | What it does |
| --- | --- |
| `just lint` | Ruff, mypy, pyright, ty, rumdl, shellcheck, codespell, semgrep |
| `just test` | Run pytest |
| `just check` | Lint + test |
| `just lint-fix` | Auto-fix linting issues |
| `just build` | Build the package |
| `just clean` | Remove caches and build artifacts |

See the full [CONTRIBUTING.md](https://github.com/chad-loder/pyhaul/blob/main/CONTRIBUTING.md)
for branch conventions, commit style, and CI details.
