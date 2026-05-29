# Async quickstart

`dtollib` is async-first.  The example below reads two K-type
thermocouples on a DT9805 and prints the temperatures.

## Read two thermocouples

```python
import anyio

from dtollib import (
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
    open_device,
)


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
        ThermocoupleInput(
            physical_channel=1,
            name="back_tc_K",
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        ),
    ],
)


async def main() -> None:
    async with await open_device(spec) as session:
        reading = await session.poll()
        print(reading.values)
        # → {"surface_tc_K": 23.41, "back_tc_K": 22.97}


anyio.run(main)
```

## Sensor sentinels — never coerced to plausible values

DT-Open Layers thermocouple inputs can report three sentinel
conditions in place of a temperature:

- `SENSOR_OPEN` — the TC wire is broken / disconnected.
- `TEMP_OUT_OF_RANGE_LOW` — sensor below the type's NIST envelope.
- `TEMP_OUT_OF_RANGE_HIGH` — sensor above the type's NIST envelope.

The wrapper preserves them on the `sensor_status` overlay and
NaN-fills the corresponding `values` entry — see
[design.md §13.1][13_1].

```python
import math

reading = await session.poll()
for name, value in reading.values.items():
    status = reading.sensor_status.get(name)
    if status is not None or math.isnan(value):
        print(f"{name}: {status.value if status else 'unknown'}")
    else:
        print(f"{name}: {value:.2f}")
```

## Running tests against a fake backend

There is no DT-Open Layers SDK on non-Windows platforms, so unit tests
exercise the same code paths via :class:`FakeDtolBackend`:

```python
from dtollib import open_device, TaskSpec, ThermocoupleInput, ThermocoupleType
from dtollib.testing import make_fake_backend


backend = make_fake_backend(include_dt9805=True)
async with await open_device(spec, backend=backend) as session:
    # Inject scripted values per (HDASS, channel) tuple:
    hdass = session.raw_hdass
    backend.scalar_values[(hdass, 0)] = 25.5
    backend.thermocouple_sentinels[(hdass, 1)] = "sensor_open"
    reading = await session.poll()
```

The fake enforces the same ordering rules as the real SDK — for
instance the [§8.5a][8_5a] MULTI_SENSOR ordering invariant — so a unit
test against the fake catches the same bugs hardware would.

## Ecosystem integration

`DaqReading` joins on `(device, t_mono_ns)` against
`alicatlib.Sample` / `sartoriuslib.Sample` / `watlowlib.Sample`.  A
unified experiment combining a DT9805, an Alicat MFC, and a Sartorius
balance is one `async with` block away:

```python
import anyio

from alicatlib import AlicatManager
from sartoriuslib import SartoriusManager
from dtollib import DtolManager, TaskSpec, ThermocoupleInput, ThermocoupleType


tc_spec = TaskSpec(
    name="thermal_signals",
    channels=[
        ThermocoupleInput(
            physical_channel=0,
            name="surface_K",
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        ),
    ],
)


async def main() -> None:
    async with (
        AlicatManager() as mfc_mgr,
        SartoriusManager() as bal_mgr,
        DtolManager() as daq_mgr,
    ):
        await mfc_mgr.add("fuel_mfc", "/dev/ttyUSB0")
        await bal_mgr.add("sample_mass", "/dev/ttyUSB1")
        await daq_mgr.add("thermal_signals", tc_spec)

        results = await daq_mgr.poll(["thermal_signals"])
        print(results["thermal_signals"].value.values)


anyio.run(main)
```

For streaming acquisition under the hardware clock see
[Continuous acquisition](continuous.md); for durable Parquet / SQLite /
Postgres / CSV / JSONL logging see [Sinks](sinks.md).

[13_1]: design.md#131-single-value-dataflowsingle_value
[8_5a]: design.md#85a-multisensor-channels-explicit-configure-time-discriminator
