"""Commit a docs deploy to the local pages branch with mike; publishing is a separate step.

Run from the repo root in GitHub Actions: ``uv run python .github/scripts/deploy_docs.py``.
It needs no write access: it fetches the pages branch, commits locally on top
of it, and writes any new commits to a git bundle. Step outputs: ``branch``
(the pages branch) and, when there is something to publish, ``bundle``.

A tag push (``GITHUB_REF_TYPE=tag``) deploys docs version ``X.Y`` of the
installed pyhaul, and fails if the tag is not that version's release tag.
When ``X.Y`` is the newest deployed release it also takes the ``latest`` alias
(the site default) and refreshes the root redirect stubs, so redeploying an
older tag never moves ``latest`` backwards. Any other ref deploys ``dev``,
hidden from the version selector and canonical to itself.

The site URL, remote, and pages branch come from ``properdocs.yml`` (mike reads
the same keys). The root stubs keep pre-versioning URLs (``/<page>/``) alive:
each serves 200 with a canonical link and an immediate refresh to
``/latest/<page>/``. mike itself only writes the root ``index.html``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from importlib.metadata import version as package_version
from pathlib import Path

import properdocs.replacement  # noqa: F401 — maps mkdocs.* to properdocs.* for plugins, as the properdocs CLI does
from packaging.version import Version
from properdocs.config import load_config

BUNDLE = "docs-deploy.bundle"
STUB_MARKER = "pyhaul-redirect-stub"
STUB = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="generator" content="{marker}">
<title>Redirecting…</title>
<link rel="canonical" href="{href}">
<meta http-equiv="refresh" content="0; url={href}">
<script>location.replace("{href}" + location.search + location.hash)</script>
</head>
<body><a href="{href}">{href}</a></body>
</html>
"""


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.run(args, cwd=cwd, env=env, check=True, text=True, capture_output=True).stdout


def deployed_releases() -> list[Version]:
    try:
        listing = json.loads(run("mike", "list", "--json"))
    except subprocess.CalledProcessError:
        return []
    return [Version(v["version"]) for v in listing if v["version"][:1].isdigit()]


def write_stubs(worktree: Path, docs_version: str, latest_url: str) -> None:
    for stale in worktree.rglob("index.html"):
        if STUB_MARKER in stale.read_text(encoding="utf-8", errors="ignore"):
            stale.unlink()
    listing = json.loads((worktree / "versions.json").read_text(encoding="utf-8"))
    reserved = {name for v in listing for name in (v["version"], *v["aliases"])}
    for page in (worktree / docs_version).rglob("index.html"):
        rel = page.parent.relative_to(worktree / docs_version)
        if rel == Path() or rel.parts[0] in reserved:
            continue
        stub = worktree / rel / "index.html"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(STUB.format(marker=STUB_MARKER, href=f"{latest_url}{rel.as_posix()}/"), encoding="utf-8")


def refresh_stubs(branch: str, docs_version: str, latest_url: str) -> None:
    tmp = Path(tempfile.mkdtemp())
    worktree = tmp / branch
    run("git", "worktree", "add", "--quiet", str(worktree), branch)
    try:
        write_stubs(worktree, docs_version, latest_url)
        run("git", "add", "--all", cwd=worktree)
        if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=worktree, check=False).returncode:
            run("git", "commit", "--quiet", "-m", f"Redirect unversioned URLs to latest ({docs_version})", cwd=worktree)
    finally:
        run("git", "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    cfg = load_config("properdocs.yml")
    remote, branch = cfg.remote_name, cfg.remote_branch
    run("git", "fetch", "--quiet", "--depth=1", remote, branch)

    if os.environ["GITHUB_REF_TYPE"] != "tag":
        env = os.environ | {"DOCS_CANONICAL_VERSION": "dev"}
        run("mike", "deploy", "--title", "dev", "--prop-set", "hidden=true", "dev", env=env)
    else:
        release = Version(package_version("pyhaul"))
        if os.environ["GITHUB_REF_NAME"] != f"v{release}":
            msg = f"tag {os.environ['GITHUB_REF_NAME']} does not match pyhaul {release}"
            raise SystemExit(msg)
        docs_version = f"{release.major}.{release.minor}"
        if all(Version(docs_version) >= v for v in deployed_releases()):
            run("mike", "deploy", "--update-aliases", docs_version, "latest")
            run("mike", "set-default", "latest")
            refresh_stubs(branch, docs_version, f"{cfg.site_url}latest/")
        else:
            run("mike", "deploy", docs_version)

    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        out.write(f"branch={branch}\n")
        if run("git", "rev-parse", branch) != run("git", "rev-parse", f"{remote}/{branch}"):
            run("git", "bundle", "create", BUNDLE, f"{remote}/{branch}..{branch}")
            out.write(f"bundle={BUNDLE}\n")


if __name__ == "__main__":
    main()
