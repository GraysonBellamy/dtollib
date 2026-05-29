# Synchronized start

`DtolManager.start_synchronized(names)` starts several subsystems together via
the DT-Open Layers simultaneous-start primitives, so their first samples begin
within one sample period of each other — tighter than a software start loop can
achieve.

## What it does

It runs the four-step SDK sequence (and always releases the list afterward):

```text
olDaGetSSList            # obtain a simultaneous-start list for the board
olDaPutDassToSSList × N  # add each task's subsystem
olDaSimultaneousPreStart # arm every subsystem
olDaSimultaneousStart    # start them all on one trigger
olDaReleaseSSList        # always, even if a step raised
```

## Usage

```python
import anyio

from dtollib import AnalogInputVoltage, CounterEdgeCount, DtolManager, TaskSpec
from dtollib.backend.dataacq import DataAcqBackend


ai_spec = TaskSpec(name="ai", board="DT9806(00)",
                   channels=[AnalogInputVoltage(physical_channel=0)])
ct_spec = TaskSpec(name="ct", board="DT9806(00)",
                   channels=[CounterEdgeCount(physical_channel=0)])


async def main() -> None:
    backend = DataAcqBackend()  # one shared backend → one HDRVR namespace
    async with DtolManager() as mgr:
        await mgr.add("ai", ai_spec, backend=backend)
        await mgr.add("ct", ct_spec, backend=backend)
        await mgr.start_synchronized(["ai", "ct"])
        # both subsystems are now RUNNING, started together.


anyio.run(main)
```

## Scope and constraints

- **Single board.** Every named task must target the same board. Cross-board
  coordination over the Sync Bus (`olDaSetSyncMode`) is not yet implemented.
- **One shared backend.** Pass the *same* `backend=` to each `add()`. The
  SS-list is built from one HDRVR's subsystem handles; tasks on separate
  backend instances live in separate handle namespaces and cannot be
  coordinated. The manager raises `DtolValidationError` if the named tasks
  don't share a backend.
- **Tasks must be committed, not started.** `DtolManager.add()` commits during
  registration, so single-value and counter tasks are ready immediately.
- **Continuous-AI tasks** must complete their `record()` register → queue →
  commit sequence first. Open the recorder with `autostart=False`, let it wire
  the §12.3.2 callback bridge and commit, then call `start_synchronized` to
  begin acquisition in lockstep with the other subsystems. A first-class
  `record_synchronized()` helper is deferred (OQ-5c).

## Verifying alignment

On the bench, capture both subsystems through a `RawCountsSink` and compare the
first-sample timestamps — they should fall within one sample period. The fake
backend models the state transitions (`PRESTARTED` → `RUNNING`) and the
ordering invariants (`put` before pre-start, pre-start before start) so the
sequence is unit-tested without hardware.
