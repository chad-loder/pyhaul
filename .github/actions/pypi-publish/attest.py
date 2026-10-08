"""Write a PEP 740 publish attestation next to each distribution in a directory.

Signs with the job's ambient GitHub Actions OIDC identity through Sigstore,
the same calls pypa/gh-action-pypi-publish makes. ``uv publish`` uploads each
``<dist>.publish.attestation`` that is among its file arguments.

The directory must hold only regular-file wheels and sdists of one project
version, and no attestations yet; anything else fails before signing.

Usage: ``attest.py <dist-dir>``
"""

from __future__ import annotations

import sys
from pathlib import Path

from packaging.utils import parse_sdist_filename, parse_wheel_filename
from pypi_attestations import Attestation, Distribution
from sigstore.models import ClientTrustConfig
from sigstore.oidc import IdentityToken, detect_credential
from sigstore.sign import SigningContext


def collect(dist_dir: Path) -> list[Path]:
    entries = sorted(dist_dir.iterdir())
    if not entries:
        raise SystemExit(f"no distributions in {dist_dir}")
    releases = set()
    for p in entries:
        if p.is_symlink() or not p.is_file():
            raise SystemExit(f"not a regular file: {p}")
        if p.name.endswith(".whl"):
            name, version, *_ = parse_wheel_filename(p.name)
        elif p.name.endswith(".tar.gz"):
            name, version = parse_sdist_filename(p.name)
        else:
            raise SystemExit(f"unexpected file in {dist_dir}: {p.name}")
        releases.add((name, version))
    if len(releases) != 1:
        raise SystemExit(f"distributions span several releases: {sorted(map(str, releases))}")
    return entries


def main(dist_dir: Path) -> None:
    dists = collect(dist_dir)
    token = detect_credential()
    if token is None:
        raise SystemExit("no ambient OIDC credential; the job needs id-token: write")
    context = SigningContext.from_trust_config(ClientTrustConfig.production())
    with context.signer(IdentityToken(token), cache=True) as signer:
        for dist in dists:
            attestation = Attestation.sign(signer, Distribution.from_file(dist))
            Path(f"{dist}.publish.attestation").write_text(attestation.model_dump_json(), encoding="utf-8")
            print(f"attested {dist.name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
