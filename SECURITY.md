# Security policy

## Reporting a vulnerability

We prefer **transparency**: if you find a security problem in `pyhaul`, you are
welcome to open a **pull request** (or a public **issue** if a PR is not
practical) so the fix and any discussion can happen in the open.

If you need **private coordination** first—for example, you believe disclosure
should wait until a patch is ready—use GitHub’s security advisory flow:

**<https://github.com/chad-loder/pyhaul/security/advisories/new>**

In either case, we aim to acknowledge reports within 72 hours.

## Supported versions

Security fixes land in the current and previous minor releases of the
latest major version.

## Scope

In scope:

- Remote code execution via crafted HTTP responses or redirects.
- Path traversal or arbitrary file writes via `Content-Disposition`
  or URL-derived filenames.
- Memory exhaustion via hostile servers (unbounded `Content-Length`,
  pathological compression ratios, etc.).
- TLS downgrade, certificate validation bypass, or hostname confusion.

Out of scope:

- Issues in dependencies (report to `httpx`, `niquests`, etc. directly).
- Attacks that require local filesystem access or a compromised host.
- Self-DoS from passing absurd inputs to the API.
