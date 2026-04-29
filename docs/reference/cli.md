# CLI Reference

pyhaul includes a command-line downloader invoked via `python -m pyhaul`.
It requires at least one HTTP client extra to be installed.

!!! warning "Not a stable interface"
    The CLI exists for demonstration and quick smoke-testing. It is **not**
    intended for scripting or automation. Options, output format, and exit
    code semantics may change at any time without notice. For programmatic
    use, depend on the [`haul()`][pyhaul.engine.haul] /
    [`haul_async()`][pyhaul.async_engine.haul_async] Python API instead (including the optional
    `headers=` keyword, which mirrors `-H` / `-A` here).

## Usage

```text
pyhaul [-o FILE] [-x PROXY] [-H HEADER] [-A NAME] [--http-backend NAME] URL
```

## Examples

```bash
# Basic download (filename derived from URL)
python -m pyhaul https://example.com/file.iso

# Specify output file
python -m pyhaul -o file.iso https://example.com/file.iso

# Use a specific HTTP backend
python -m pyhaul --http-backend httpx -o out.bin https://example.com/file.bin

# Download through a SOCKS proxy
python -m pyhaul -x socks5h://127.0.0.1:9050 http://abc.onion/blob.bin

# Custom headers and user agent
python -m pyhaul -H 'Cookie: x=1' -A 'my-bot/1.0' https://host/f.zip
```

## Options

### Output

| Flag | Description |
| --- | --- |
| `-o FILE`, `--output FILE` | Write output to FILE (default: derived from URL) |
| `-O`, `--remote-name` | Use URL basename as output filename |
| `--output-dir DIR` | Directory to save file in (created if missing) |

### Network

| Flag | Description |
| --- | --- |
| `-x URL`, `--proxy URL` | Proxy URL (e.g. `socks5h://127.0.0.1:9050`, `http://host:3128`) |
| `-H HEADER`, `--header HEADER` | Add custom header `Name: Value` (repeatable) |
| `-A NAME`, `--user-agent NAME` | User-Agent string |
| `--http-backend NAME` | HTTP client library: `niquests` (default), `requests`, `httpx`, `urllib3` |
| `-k`, `--insecure` | Skip TLS certificate verification |
| `--connect-timeout SECS` | Maximum seconds to wait for connection |
| `--read-timeout SECS` | Maximum seconds between response chunks (default: 4x connect-timeout) |

### Logging

| Flag | Description |
| --- | --- |
| `-q`, `--quiet`, `-s`, `--silent` | Suppress progress output |
| `-v`, `--verbose` | Verbose logging (repeat for debug: `-vv`) |
| `-V`, `--version` | Print version and exit |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Download or HTTP error |
| 2 | Usage error (bad arguments) |
| 130 | Interrupted (SIGINT / SIGTERM) |

## Resume behavior

The CLI automatically retries up to 20 times with exponential backoff. If the
process is killed, re-run the same command — it resumes from the checkpoint.

Progress is written to `<dest>.part` and `<dest>.part.ctrl`. The destination
file only appears after a successful download.

## Signal handling

- **First SIGINT/SIGTERM:** saves resume state and exits with code 130.
- **Second signal:** hard-exits immediately via `os._exit`.
