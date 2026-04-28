# Exceptions

All pyhaul exceptions inherit from `HaulError`. Transport errors from the
underlying HTTP library (e.g. `httpx.ReadTimeout`, `requests.ConnectionError`)
pass through **unwrapped** — you catch the types you already know.

## Exception hierarchy

```text
HaulError
├── PartialHaulError
├── ServerMisconfiguredError
├── ContentRangeError
├── ControlFileError
└── DestinationError
```

## Reference

| Exception | When | Retryable? |
| --- | --- | --- |
| `PartialHaulError` | Stream ended before all bytes arrived. | Yes — call `haul()` again; progress is saved |
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
