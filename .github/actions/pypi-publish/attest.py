"""Write a PEP 740 publish attestation next to each distribution in a directory.

Signs with the job's ambient GitHub Actions OIDC identity through Sigstore,
the same calls pypa/gh-action-pypi-publish makes. ``uv publish`` uploads each
``<dist>.publish.attestation`` it finds beside the file.

Usage: ``attest.py <dist-dir>``
"""

from __future__ import annotations

import sys
from pathlib import Path

from pypi_attestations import Attestation, Distribution
from sigstore.models import ClientTrustConfig
from sigstore.oidc import IdentityToken, detect_credential
from sigstore.sign import SigningContext


def main(dist_dir: Path) -> None:
    dists = sorted(p for p in dist_dir.iterdir() if p.name.endswith((".whl", ".tar.gz")))
    if not dists:
        raise SystemExit(f"no distributions in {dist_dir}")
    existing = [p for p in dist_dir.iterdir() if p.name.endswith(".publish.attestation")]
    if existing:
        raise SystemExit(f"attestations already present: {existing}")
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
