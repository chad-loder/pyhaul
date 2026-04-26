# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
