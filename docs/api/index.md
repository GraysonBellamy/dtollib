# API reference

This section renders the public `dtollib` surface from source docstrings
(Google-style, via mkdocstrings). Everything below is re-exported from the
top-level `dtollib` package unless noted — `from dtollib import ...`.

## By topic

| Page | Covers |
|------|--------|
| [Sessions](session.md) | `DtolSession`, `open_device` — the central acquisition object and its lifecycle. |
| [Task specs](tasks.md) | `TaskSpec`, `TaskBuilder`, `Timing`, `BufferPlan`, data-flow / subsystem enums. |
| [Channels](channels.md) | Every analog-input, analog-output, digital, and counter/timer channel spec + enums. |
| [Triggers](triggers.md) | `TriggerSpec` hierarchy and `RetriggerSpec`. |
| [Continuous & playback](streaming.md) | `record`, `record_polled`, `play`, `Recording`, `AcquisitionSummary`, policies, `DaqBlock`. |
| [Sinks](sinks.md) | CSV / JSONL / Parquet / SQLite / Postgres / memory / raw-counts sinks + the sink Protocol. |
| [Discovery & capabilities](system.md) | `find_devices`, `find_subsystems`, `CapabilitySet`, board/subsystem info models. |
| [Manager](manager.md) | `DtolManager`, `DeviceResult`. |
| [Configuration](config.md) | `DtolConfig`, `config_from_env`. |
| [TEDS](teds.md) | IEEE-1451.4 strain-gage / bridge-sensor readers. |
| [Strain & rosette math](strain.md) | `strain_from_volts`, `bridge_value_from_volts`, rosette helpers. |
| [Units](units.md) | `to_pint` and the temperature/thermocouple conversions. |
| [Sync facade](sync.md) | `Dtol`, `SyncDtolSession`, the blocking-portal wrappers. |
| [Testing helpers](testing.md) | `FakeDtolBackend` and the `make_fake_*` fixtures. |
| [Errors](errors.md) | The `DtolError` hierarchy and `ErrorContext`. |
| [capi](capi.md) | The hand-rolled `ctypes` binding (contributor-facing). |
| [backend](backend.md) | Session orchestration above `capi` (contributor-facing). |
