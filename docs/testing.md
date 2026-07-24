# Testing without hardware

DT-Open Layers is Windows-only and needs a physical board, but `dtollib` is
designed so the **entire code path** runs on any platform against
`FakeDtolBackend` — a faithful stand-in that enforces the same ordering and
capability rules the real SDK does. A unit test against the fake catches the
same class of bugs hardware would, which is why CI runs green on Linux.

## Injecting a fake backend

Every entry point that touches the SDK accepts a `backend=` argument.
`make_fake_backend()` builds a `FakeDtolBackend` with optionally pre-populated
boards:

```python
from dtollib import open_device, TaskSpec, ThermocoupleInput, ThermocoupleType
from dtollib.testing import make_fake_backend


spec = TaskSpec(
    name="t",
    channels=[
        ThermocoupleInput(
            physical_channel=0,
            name="tc",
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        )
    ],
)

backend = make_fake_backend(include_dt9805=True)
async with await open_device(spec, backend=backend) as session:
    reading = await session.poll()
```

`make_fake_backend(*, boards=None, include_dt9805=False, include_dt9806=False)`
— pass the `include_*` shortcuts for a default board of that model, or an
explicit `boards=[...]` list. There are also model-specific shortcuts
`make_fake_dt9805()` / `make_fake_dt9806()` / `make_fake_multisensor()`.

## Scripting values and sentinels

The fake lets a test drive what `poll()` returns, keyed by `(HDASS, channel)`:

```python
backend = make_fake_backend(include_dt9805=True)
async with await open_device(spec, backend=backend) as session:
    hdass = session.raw_hdass
    backend.scalar_values[(hdass, 0)] = 25.5
    backend.thermocouple_sentinels[(hdass, 1)] = "sensor_open"
    reading = await session.poll()
    # reading.values["tc"] is 25.5; the sentinel channel is NaN-filled
    # with reading.sensor_status carrying "sensor_open".
```

## Why the fake is faithful, not a no-op

The fake models the SDK's **invariants**, so out-of-order or unsupported calls
fail the same way they would on hardware:

- The §8.5a MULTI_SENSOR ordering invariant.
- Counter `olDaSetCTMode`-first ordering (see [Counter/timer](counter-timer.md)).
- Capability gating — multi-sensor / continuous-AO / quadrature specs raise
  `DtolCapabilityError` on a board whose fake capabilities don't advertise them.
- Simultaneous-start state transitions (`PRESTARTED` → `RUNNING`) and the
  put-before-prestart ordering.

## Capability fixtures

`dtollib.testing` ships pre-canned `CapabilitySet` builders so a test can stand
up a board with exactly the capabilities under test:
`make_dt9805_capabilities`, `make_dt9806_capabilities`,
`make_dt9806_ao_capabilities`, `make_counter_capabilities`,
`make_firmware_tc_capabilities`, and `make_multisensor_capabilities`. See the
[Testing API reference](api/testing.md) for the full list.
