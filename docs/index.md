# dtollib

Experiment-facing DT-Open Layers acquisition tools for Python.

`dtollib` is a typed, lifecycle-managed acquisition layer over a
hand-rolled `ctypes` binding to the [Data Translation DataAcq SDK](https://www.mccdaq.com/data-translation).
It fits the same scientific-instrumentation ecosystem as
[`alicatlib`](https://github.com/GraysonBellamy/alicatlib),
[`sartoriuslib`](https://github.com/GraysonBellamy/sartoriuslib),
[`watlowlib`](https://github.com/GraysonBellamy/watlowlib), and
[`nidaqlib`](https://github.com/GraysonBellamy/nidaqlib).

Use `dtollib` when you want:

- declarative task specifications for DT9805 / DT9806 acquisition,
- consistent async/sync APIs,
- structured errors with rich context,
- block-oriented continuous acquisition with hardware clocks,
- TC sentinel preservation (no silent "23.4 °C" for an open thermocouple),
- Parquet / SQLite / Postgres / CSV / JSONL logging,
- a "raw counts to disk" fast path equivalent to NI's TDMS,
- hardware-free tests against a faithful `FakeDtolBackend`,
- and unified experiment workflows across DT-Open Layers, NI, Alicat,
  Sartorius, and Watlow.

## Status

The Phase 0–5 **software** surface has landed and is exercised
end-to-end against `FakeDtolBackend`. The package installs and imports
cleanly on Windows, Linux, and macOS; on Windows with the DataAcq SDK
installed it drives real DT9805 / DT9806 hardware. Bound and working:

- **Discovery + diagnostics** — `find_devices`, `find_subsystems`,
  `CapabilitySet`, `dtol-discover`, `dtol-diag`.
- **Single-value analog input** — `TaskSpec`, `AnalogInputVoltage`,
  `ThermocoupleInput`, `open_device`, `session.poll()`, with NIST
  thermocouple math and sentinel preservation.
- **Continuous acquisition** — `record()`, `record_polled()`, the
  §12.3.2 driver-thread callback bridge, `DaqBlock`, and the CSV /
  JSONL / RawCounts sinks plus the `.dt-raw` replay tool.
- **Single-value output + safety gates** — `session.write()`,
  `dtol-read`, `dtol-info`, analog/digital output channel specs.
- **Counters, triggers/retriggering, and** `DtolManager.start_synchronized`.

Hardware acceptance on the maintainer bench is ongoing, and some
original roadmap targets have been revised to match DT9805 / DT9806
capability findings (e.g. continuous AO waveform output is unavailable
on these boards) — see
[plan-hardware-functional.md](plan-hardware-functional.md). For the
phased roadmap and architecture see [design.md](design.md) and
[implementation-plan.md](implementation-plan.md).

## Platform support

DT-Open Layers is Windows-only. `pip install dtollib` works on
Linux/macOS for type-checking and ecosystem composition, but the
real backend raises `DtolDependencyError` at first SDK touch.
