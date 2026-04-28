# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0](https://github.com/chad-loder/pyhaul/compare/v0.3.0...v0.4.0) (2026-04-27)


### Features

* binary checkpoints and incremental tree hashing ([#7](https://github.com/chad-loder/pyhaul/issues/7)) ([10eb5c7](https://github.com/chad-loder/pyhaul/commit/10eb5c7eac7cfa03e28639c0074b0b9d4cebc660))
* tail hash verification and test reliability improvements ([#10](https://github.com/chad-loder/pyhaul/issues/10)) ([f862d43](https://github.com/chad-loder/pyhaul/commit/f862d4368d493cd8a24288ea0bffb6f29bd67d6d))
* upgrade V4 format with framed TLVs and 8-byte alignment ([#13](https://github.com/chad-loder/pyhaul/issues/13)) ([42651ce](https://github.com/chad-loder/pyhaul/commit/42651ce9dc53a1d0852aa7c3cdd4ee51b57eedbd))


### Bug Fixes

* close urllib3 response to prevent socket leaks ([#11](https://github.com/chad-loder/pyhaul/issues/11)) ([de6c6aa](https://github.com/chad-loder/pyhaul/commit/de6c6aa152ef1370f6a582a20aca1d946b48794d))


### Documentation

* add RFC-style `docs/SPEC.md` and fix rumdl command in justfile ([#12](https://github.com/chad-loder/pyhaul/issues/12)) ([183eeda](https://github.com/chad-loder/pyhaul/commit/183eeda5cb1b5202ed953bd4ce40aeb8ae9825e6))
* finalize `docs/SPEC.md` (alignment + strategy) ([#14](https://github.com/chad-loder/pyhaul/issues/14)) ([734f4c7](https://github.com/chad-loder/pyhaul/commit/734f4c78e08a0d78103b516456d0f19ce9fd5f48))
* update checkpoint format description to binary ([#9](https://github.com/chad-loder/pyhaul/issues/9)) ([661de1c](https://github.com/chad-loder/pyhaul/commit/661de1ccd50b8986fa8a201bc44ebb585501d0de))

## [0.3.0](https://github.com/chad-loder/pyhaul/compare/v0.2.1...v0.3.0) (2026-04-26)

### Features

- initial release of pyhaul ([e661cbe](https://github.com/chad-loder/pyhaul/commit/e661cbed13166549c2a3b403036386383fdd596b))

### Documentation

- v0.2.1 aiohttp readme, pypi keyword, and merge dev ([#3](https://github.com/chad-loder/pyhaul/issues/3)) ([526c973](https://github.com/chad-loder/pyhaul/commit/526c973281accb16db94f867589f2e54e809df87))

## [0.2.1](https://github.com/chad-loder/pyhaul/compare/v0.2.0...v0.2.1) (2026-04-26)

### Documentation

- Document **aiohttp** as a supported async client; add the `aiohttp` PyPI keyword.

## [0.2.0](https://github.com/chad-loder/pyhaul/compare/v0.1.0...v0.2.0) (2026-04-26)

### Features

- initial release of pyhaul ([e661cbe](https://github.com/chad-loder/pyhaul/commit/e661cbed13166549c2a3b403036386383fdd596b))

## [Unreleased]

### Added

- Cursor-based single-range resume engine (sync and async).
- Crash-safe persistence via `.part` + `.part.ctrl` (binary checkpoint) files.
- Transport adapters for httpx, niquests, requests, and urllib3.
- Async engine with niquests and httpx async adapters.
- `Content-Range` parser with full RFC 9110 coverage.
- CLI (`pyhaul` command) with proxy, TLS, timeout, and backend selection.
- `posix_fallocate` / `ftruncate` pre-allocation for reduced fragmentation.

### Changed

- Build system switched from hatchling to uv-build.
- Architecture rewritten from multi-piece parallel model to single-range
  cursor model.

## [0.1.0] - 2026-04-18

- Project bootstrapped.
