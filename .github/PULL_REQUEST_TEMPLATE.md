<!--
Your PR title must follow Conventional Commits:
  feat:     new feature               -> minor bump
  fix:      bug fix                   -> patch bump
  feat!: … or BREAKING CHANGE footer  -> major bump
  perf | refactor | docs | test | build | ci | chore | style | revert

Pre-1.0: `feat` also produces a patch bump (major_on_zero = false in pyproject.toml).
-->

## Summary

<!-- One to three sentences on what this PR changes and why. -->

## Changes

<!-- Bullet list of the user-visible changes. -->

## Testing

<!-- How did you verify this? `uv run pytest`, manual steps, etc. -->

## Checklist

- [ ] Tests added or updated
- [ ] Docs / docstrings updated if needed
- [ ] `just check` passes (lint + test)
