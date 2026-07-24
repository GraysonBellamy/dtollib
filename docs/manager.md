# Manager — many tasks at once

`DtolManager` owns a named collection of tasks across one or more DT boards. It
gives you a registry, concurrent fan-out polling, bulk lifecycle control, and
[coordinated simultaneous starts](synchronized.md). Use it when an experiment
spans several subsystems (a couple of AI tasks plus a counter, say) or when you
want the manager-level join key for the [ecosystem](quickstart-async.md#ecosystem-integration).

Continuous block streaming is **not** aggregated by the manager — that is owned
by [`record()`](continuous.md), which takes a single `DtolSession`. The manager
does fan-out *polling* (and `record_polled()` accepts a manager directly).

Design reference: [design.md §16](design.md).

## Lifecycle and registry

```python
import anyio

from dtollib import AnalogInputVoltage, DtolManager, TaskSpec


async def main() -> None:
    ai = TaskSpec(name="ai", channels=[AnalogInputVoltage(physical_channel=0, name="v0")])

    async with DtolManager() as mgr:
        await mgr.add("ai", ai)  # registers, reserves the subsystem, commits
        results = await mgr.poll(["ai"])  # fan-out poll
        print(results["ai"].value.values)
        # mgr closes every managed session on exit


anyio.run(main)
```

- `add(name, spec, *, backend=None)` — register a task, reserve its
  `(board, subsystem_type, element)` triple, and return its committed
  `DtolSession`. `None` backend creates a fresh `DataAcqBackend` owned by the
  manager. Raises `DtolValidationError` on a duplicate name and
  `DtolResourceError` if the subsystem is already reserved.
- `remove(name)` — close and unregister.
- `get(name)` — the registered `DtolSession`.
- `names` — registered task names in insertion order.

## Fan-out polling

`poll(names=None)`, `start(names=None)`, and `stop(names=None)` each return a
`Mapping[str, DeviceResult[...]]`. Pass `None` to address every task.

`poll()` parallelises across boards: **same-board** polls serialise on a
per-board lock (one HDRVR can't be re-entered concurrently), while
**cross-board** polls run together in a task group. So adding a second board
roughly halves wall-clock for a combined poll.

## `DeviceResult` and error policy

Each entry in an aggregated result is a `DeviceResult[T]` with exactly one of
`.value` / `.error` set, plus an `.ok` property. The manager's `error_policy`
(set at construction) decides how failures are handled:

| `ErrorPolicy` | Behaviour |
|---------------|-----------|
| `RAISE` (default) | First failure cancels and propagates. Every returned `DeviceResult.ok` is `True`. |
| `RETURN` | Each `DeviceResult` carries its own outcome — inspect `.ok` / `.error` per task. |
| `LOG_AND_CONTINUE` | Failures are dropped from the result map with a WARNING log line. |

```python
from dtollib.manager import ErrorPolicy

async with DtolManager(error_policy=ErrorPolicy.RETURN) as mgr:
    await mgr.add("ai", ai)
    for name, res in (await mgr.poll()).items():
        if res.ok:
            print(name, res.value.values)
        else:
            print(name, "failed:", res.error)
```

## Polling on a cadence

`record_polled()` accepts a manager and polls every task on each tick, yielding
`Mapping[str, DeviceResult[DaqReading]]`:

```python
from dtollib import record_polled

async with record_polled(mgr, rate_hz=5.0) as rec:
    async for tick in rec.stream:
        print({name: r.value.values for name, r in tick.items() if r.ok})
```

## Coordinated starts

`start_synchronized(names)` begins several single-board subsystems together via
the SDK simultaneous-start primitives. Every named task must target one board
and share one backend instance. See [Synchronized start](synchronized.md) for
the full sequence and constraints.
