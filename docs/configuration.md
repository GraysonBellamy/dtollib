# Configuration

`DtolConfig` holds **process-wide defaults** — the fallbacks used when a call
site or `TaskSpec` doesn't specify a value. Anything that varies *per task*
(channel ranges, trigger source, buffer plan) belongs on the
[`TaskSpec`](task-specs.md), not here.

It is a frozen, slotted, keyword-only dataclass with no validation-library
dependency, so importing it costs nothing. Design reference:
[design.md §19.1](design.md).

## Fields

| Field | Default | Meaning |
|-------|---------|---------|
| `default_timeout_s` | `10.0` | Fallback SDK read/write timeout (seconds). |
| `default_sample_rate_hz` | `1000.0` | Fallback `Timing.rate_hz` when unset. |
| `default_chunk_size` | `1000` | Samples/channel per emitted `DaqBlock` for `record()`. |
| `default_buffers` | `4` | `BufferPlan.buffers` default (HBUF ring; hard minimum 3). |
| `default_stream_buffer` | `16` | AnyIO send-stream capacity for `record()`, in `DaqBlock` slots. |
| `eager_tasks` | `False` | Opt in to `asyncio.eager_task_factory` (no-op on trio). |
| `oldaapi_dll_path` | `None` | Explicit `oldaapi*.dll` path; `None` uses the loader's resolution chain. |
| `olmem_dll_path` | `None` | Explicit `olmem*.dll` path. |

```python
from dtollib import DtolConfig

cfg = DtolConfig(default_sample_rate_hz=50_000.0, default_buffers=8)
faster = cfg.replace(default_buffers=16)   # immutable copy with one change
```

## From the environment

`config_from_env(prefix="DTOLLIB_")` builds a `DtolConfig` from environment
variables. It is **best-effort and never raises** — missing or unparseable
values fall back to the dataclass default.

| Env var | Type |
|---------|------|
| `DTOLLIB_DEFAULT_TIMEOUT_S` | float |
| `DTOLLIB_DEFAULT_SAMPLE_RATE_HZ` | float |
| `DTOLLIB_DEFAULT_CHUNK_SIZE` | int |
| `DTOLLIB_DEFAULT_BUFFERS` | int (clamped at 3 by `BufferPlan`) |
| `DTOLLIB_DEFAULT_STREAM_BUFFER` | int |
| `DTOLLIB_EAGER_TASKS` | bool (`1`/`true`/`yes`/`on`) |
| `DTOLLIB_OLDAAPI_DLL` | path |
| `DTOLLIB_OLMEM_DLL` | path |

```python
from dtollib import config_from_env

cfg = config_from_env()   # reads DTOLLIB_* from os.environ
```

## DLL-path overrides

`oldaapi_dll_path` / `olmem_dll_path` (and their `DTOLLIB_OLDAAPI_DLL` /
`DTOLLIB_OLMEM_DLL` env equivalents) feed the loader's resolution chain — useful
when the DataAcq SDK is installed in a non-default location. See
[Installation](installation.md#dataacq-sdk-windows-only) for the full chain and
[Troubleshooting](troubleshooting.md) for diagnosing load failures.
