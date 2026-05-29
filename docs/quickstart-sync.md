# Sync quickstart

The async API is canonical, but a sync facade lives at
:class:`dtollib.sync.Dtol` for scripts, notebooks, and REPL use.

## Read two thermocouples

```python
from dtollib import (
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.sync import Dtol


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


with Dtol.open_device(spec) as session:
    reading = session.poll()
    print(reading.values)
    # → {"surface_tc_K": 23.41, "back_tc_K": 22.97}
```

## What the sync facade is

`Dtol.open_device(...)` returns a :class:`SyncDtolSession`, which is a
blocking wrapper around the async :class:`DtolSession`.  Internally,
the wrapper spins up an :class:`anyio.from_thread.BlockingPortal` so
every synchronous call dispatches through the same async code path —
no parallel implementation.

That means:

- The sync API has the same behaviour as the async API.  If
  `await session.poll()` returns a sentinel-filled `DaqReading`, so
  does `session.poll()` in the sync facade.
- The sync session is **not reusable** — exiting its `with` block
  closes the portal.  Open a fresh session for each run.

## When to use it

Use the sync facade when:

- You're writing a notebook or REPL session and don't want to wrap
  every call in `anyio.run(...)`.
- You're integrating into a sync codebase and async-all-the-way isn't
  available.

Use the async API when:

- You're composing acquisition with other async I/O (Alicat MFCs,
  Sartorius balances, network sinks).
- You want per-task concurrency via `DtolManager.poll(names)` — the
  async manager parallelises cross-board polls.
