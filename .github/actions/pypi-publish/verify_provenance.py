"""Fail unless the index serves PEP 740 provenance for every uploaded distribution.

Queries ``https://<index>/integrity/<project>/<version>/<file>/provenance``
and requires each bundle's publisher to be this repository and workflow, so a
successful upload that silently dropped its attestations fails the job.

Usage: ``verify_provenance.py <index-host> <dist-dir>`` with the GitHub Actions
environment (``GITHUB_REPOSITORY``, ``GITHUB_WORKFLOW_REF``) present.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

ATTEMPTS = 10
DELAY_SECONDS = 15


def release_of(filename: str) -> tuple[str, str]:
    if filename.endswith(".whl"):
        name, version, *_ = parse_wheel_filename(filename)
    else:
        name, version = parse_sdist_filename(filename)
    return canonicalize_name(name), str(version)


def fetch(url: str) -> dict[str, Any] | None:
    for _ in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — fixed https host
                body: dict[str, Any] = json.load(resp)
                return body
        except urllib.error.HTTPError as e:
            if e.code != HTTPStatus.NOT_FOUND:
                raise
        time.sleep(DELAY_SECONDS)
    return None


def main(index_host: str, dist_dir: Path) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    workflow = os.environ["GITHUB_WORKFLOW_REF"].split("@", 1)[0].removeprefix(f"{repository}/")
    for dist in sorted(p for p in dist_dir.iterdir() if p.name.endswith((".whl", ".tar.gz"))):
        project, version = release_of(dist.name)
        url = f"https://{index_host}/integrity/{project}/{version}/{dist.name}/provenance"
        provenance = fetch(url)
        if provenance is None:
            raise SystemExit(f"no provenance on {index_host} for {dist.name}")
        publishers = [b["publisher"] for b in provenance["attestation_bundles"]]
        if not any(
            p.get("kind") == "GitHub" and p.get("repository") == repository and p.get("workflow") == Path(workflow).name
            for p in publishers
        ):
            raise SystemExit(f"{dist.name}: provenance publisher {publishers} is not {repository} {workflow}")
        print(f"provenance verified: {dist.name}")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
