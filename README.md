# dtollib

Experiment-facing DT-Open Layers acquisition layer for Python.

`dtollib` is a typed, lifecycle-managed acquisition layer over a
hand-rolled `ctypes` binding to the Data Translation DataAcq SDK
(`oldaapi64.dll` + `olmem64.dll`). It fits the same
scientific-instrumentation ecosystem as
[`alicatlib`](https://github.com/GraysonBellamy/alicatlib),
[`sartoriuslib`](https://github.com/GraysonBellamy/sartoriuslib),
[`watlowlib`](https://github.com/GraysonBellamy/watlowlib), and
[`nidaqlib`](https://github.com/GraysonBellamy/nidaqlib).

**Hardware in scope:** DT9805 (multi-sensor USB module — AI only) and
DT9806 (adds D/A, DIO, counter/timer).

**Platform support:** Windows only (the DataAcq SDK is Windows-only).
The package installs on Linux/macOS for type-checking and ecosystem
composition, but the real backend raises `DtolDependencyError` at first
SDK touch.

## Status

The Phase 0–5 **software** surface has landed and is exercised end-to-end
against `FakeDtolBackend`. Bound and working:

- Discovery + diagnostics — `find_devices`, `dtol-discover`, `dtol-diag`.
- Single-value analog input — `TaskSpec`, `AnalogInputVoltage`,
  `ThermocoupleInput`, `open_device`, `session.poll`.
- Continuous acquisition — `record`, `record_polled`, the driver-thread
  callback bridge, `DaqBlock`, and the CSV / JSONL / RawCounts sinks plus
  the `.dt-raw` replay tool.
- Single-value output + safety gates — `session.write`, `dtol-read`,
  `dtol-info`, analog/digital output channel specs.
- Counters, trigger/retrigger config, and
  `DtolManager.start_synchronized`.

Hardware acceptance on the maintainer bench is ongoing and some original
roadmap targets have been revised to match DT9805/DT9806 capability
findings — see [docs/plan-hardware-functional.md](docs/plan-hardware-functional.md).
For the phased roadmap and architecture see
[docs/design.md](docs/design.md) and
[docs/implementation-plan.md](docs/implementation-plan.md).

## Install

```bash
pip install dtollib
# or
uv pip install dtollib
```

Optional extras:

```bash
pip install "dtollib[parquet]"    # pyarrow for the Parquet sink
pip install "dtollib[postgres]"   # asyncpg for the Postgres sink
```

## Quickstart

A single-value thermocouple read (see also
[docs/quickstart-async.md](docs/quickstart-async.md)):

```python
import anyio
from dtollib import TaskSpec, ThermocoupleInput, ThermocoupleType, open_device


async def main() -> None:
    spec = TaskSpec(
        name="surface_temperatures",
        channels=[
            ThermocoupleInput(
                physical_channel=0,
                name="surface_tc_K",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            ),
        ],
    )
    async with await open_device(spec) as session:
        reading = await session.poll()
        print(reading.values)


anyio.run(main)
```

On Linux/macOS (no SDK) this type-checks and runs against
`FakeDtolBackend`; on Windows with the DataAcq SDK and a DT9805/DT9806
attached it reads real hardware.

## Development

```bash
uv sync --all-extras --group dev
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pyright
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the binding verification
gate, hardware test markers, and CI lanes.

## License

MIT — see [LICENSE](LICENSE).
