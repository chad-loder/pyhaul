# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0](https://github.com/chad-loder/pyhaul/compare/v0.2.1...v0.3.0) (2026-04-26)


### Features

* initial release of pyhaul ([e661cbe](https://github.com/chad-loder/pyhaul/commit/e661cbed13166549c2a3b403036386383fdd596b))


### Documentation

* v0.2.1 aiohttp readme, pypi keyword, and merge dev ([#3](https://github.com/chad-loder/pyhaul/issues/3)) ([526c973](https://github.com/chad-loder/pyhaul/commit/526c973281accb16db94f867589f2e54e809df87))

## [0.2.1](https://github.com/chad-loder/pyhaul/compare/v0.2.0...v0.2.1) (2026-04-26)

### Documentation

* Document **aiohttp** as a supported async client; add the `aiohttp` PyPI keyword.

## [0.2.0](https://github.com/chad-loder/pyhaul/compare/v0.1.0...v0.2.0) (2026-04-26)


### Features

* initial release of pyhaul ([e661cbe](https://github.com/chad-loder/pyhaul/commit/e661cbed13166549c2a3b403036386383fdd596b))

## [Unreleased]

### Added

- Cursor-based single-range resume engine (sync and async).
- Crash-safe persistence via `.part` + `.part.ctrl` (JSON checkpoint) files.
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
