# Exceptions

All pyhaul exceptions inherit from `HaulError`. Transport errors from the
underlying HTTP library (e.g. `httpx.ReadTimeout`, `requests.ConnectionError`)
pass through **unwrapped** — you catch the types you already know.

## Exception hierarchy

```text
HaulError
├── PartialHaulError
├── UnexpectedStatusError
├── ServerMisconfiguredError
├── ContentRangeError
├── ControlFileError
└── DestinationError
```

## Reference

| Exception | When | Retryable? |
| --- | --- | --- |
| `PartialHaulError` | Stream ended before all bytes arrived. | Yes — call `haul()` again; progress is saved |
| `UnexpectedStatusError` | Server returned a non-download status (408, 429, 5xx, 404, …). | Lets caller decide — check `exc.is_transient` / `exc.is_server_error` |
| `ServerMisconfiguredError` | Server violated HTTP in a way that prevents safe resume. | No |
| `ContentRangeError` | 206 `Content-Range` doesn't match the requested range. | Often yes |
| `ControlFileError` | `.part.ctrl` is corrupt or version-mismatched. | Auto-recovers — corrupt checkpoint is discarded on next attempt |
| `DestinationError` | Destination path can't accommodate sidecar files. | No — fix the path |
| *(native)* | Transport errors (`httpx.ReadTimeout`, `requests.ConnectionError`, etc.) pass through unwrapped. | Usually yes |

## Details

::: pyhaul._types.HaulError
    options:
      show_source: false

::: pyhaul._types.PartialHaulError
    options:
      show_source: false

::: pyhaul._types.UnexpectedStatusError
    options:
      show_source: false

The `.headers` attribute is a [`TransportHeaders`](headers.md) — an immutable,
case-insensitive mapping with multi-value support and automatic redaction of
sensitive values in logs.

::: pyhaul._types.ServerMisconfiguredError
    options:
      show_source: false

::: pyhaul._types.ContentRangeError
    options:
      show_source: false

::: pyhaul._types.ControlFileError
    options:
      show_source: false

::: pyhaul._types.DestinationError
    options:
      show_source: false
