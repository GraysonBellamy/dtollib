# Output safety model

`dtollib` treats AO / DO writes with the same seriousness as sibling-library
actuators. Every write through [`DtolSession.write`](#the-gate-model) is
validated **before** any SDK call reaches the device — the wrapper never
silently clamps a value.

Design reference: [design.md §18][18].

## The gate model

Two range layers and a per-channel confirm flag combine into one rule set
(decided 2026-05-28; confirm-gate semantics per design §18.1):

| Condition | Result |
|-----------|--------|
| Unknown channel name | `DtolValidationError` |
| Value outside the device `[min_val, max_val]` | `DtolValidationError` — **always**; `confirm` does not override (electrically impossible) |
| Value outside `[safe_min, safe_max]` (when set), **or** channel has `requires_confirm=True`, without `confirm=True` | `DtolConfirmationRequiredError` |
| Otherwise | write proceeds |

- **Device range** (`min_val`/`max_val`) is a hard electrical boundary.
- **Safe band** (`safe_min`/`safe_max`) is an operator-defined subset of the
  device range. Stepping outside it is a *confirmation gate*, not a hard
  error — pass `confirm=True` to proceed.
- **`requires_confirm`** (default `True`) forces `confirm=True` on every write
  to the channel, irrespective of the safe band.

## Atomic validation

`write()` validates **every** value in the mapping before issuing **any**
SDK write. A single bad value raises and leaves the device completely
untouched — there are no partial writes.

```python
# One bad value ⇒ zero writes reach the device.
await session.write({"a": 1.0, "b": 999.0})  # raises; neither a nor b is written
```

## Examples

```python
from dtollib import AnalogOutputVoltage, TaskSpec, SubsystemType, open_device

spec = TaskSpec(
    name="heater",
    board="DT9806(00)",
    subsystem_type=SubsystemType.ANALOG_OUTPUT,
    channels=[
        AnalogOutputVoltage(
            physical_channel=0,
            name="heater_command",
            safe_min=0.0,
            safe_max=5.0,
            requires_confirm=True,
        )
    ],
)

async with await open_device(spec, autostart=False) as session:
    # In-band but requires_confirm=True → still needs confirm:
    await session.write({"heater_command": 3.0}, confirm=True)

    # Out of safe band → confirm gate (raises without confirm):
    await session.write({"heater_command": 8.0})            # DtolConfirmationRequiredError
    await session.write({"heater_command": 8.0}, confirm=True)  # OK (still inside device range)

    # Outside device range → hard error even with confirm:
    await session.write({"heater_command": 12.0}, confirm=True)  # DtolValidationError
```

## Autostart and `confirm_start`

Autostarting a task that drives a `requires_confirm` output channel is itself
a gated operation. `open_device(..., autostart=True)` raises
`DtolConfirmationRequiredError` unless `confirm_start=True` is passed; or open
with `autostart=False` and write explicitly:

```python
# Either opt in to autostart:
session = await open_device(spec, autostart=True, confirm_start=True)

# ...or open un-started and gate at write time (preferred):
async with await open_device(spec, autostart=False) as session:
    await session.write({"heater_command": 3.0}, confirm=True)
```

[18]: design.md#18-safety-model
