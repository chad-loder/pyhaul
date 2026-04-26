# Security policy

## Reporting a vulnerability

If you discover a security issue in `pyhaul`, please report it privately
via GitHub's security advisory workflow:

**<https://github.com/chad-loder/pyhaul/security/advisories/new>**

Do **not** open a public GitHub issue, pull request, or discussion for a
vulnerability. We aim to acknowledge reports within 72 hours.

## Supported versions

`pyhaul` is pre-1.0, so only the latest published minor version is
actively maintained. Once a stable 1.x line ships, this policy will be
updated to cover at least the current and previous minor releases.

## Scope

In scope:

- Remote code execution via crafted HTTP responses or redirects.
- Path traversal or arbitrary file writes via `Content-Disposition`
  or URL-derived filenames.
- Memory exhaustion via hostile servers (unbounded `Content-Length`,
  pathological compression ratios, etc.).
- TLS downgrade, certificate validation bypass, or hostname confusion.

Out of scope:

- Issues in dependencies (report to `bitarray`, `niquests`, etc. directly).
- Attacks that require local filesystem access or a compromised host.
- Self-DoS from passing absurd inputs to the API.
