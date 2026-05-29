# Discovery & capabilities

Before configuring a task you can enumerate what hardware is present and
introspect what each subsystem can do. `dtollib` exposes two async discovery
helpers plus a per-session capability query.

Design reference: [design.md §20](design.md).

## Enumerating boards

`find_devices()` returns one `BoardInfo` per installed DT-Open Layers board.
It **never raises** — if the SDK is unavailable or no board is installed, you
get an empty list and the failure is logged. (The [`dtol-diag`](cli.md#dtol-diag)
CLI is the tool that distinguishes "SDK missing" from "no boards".)

```python
import anyio
from dtollib import find_devices, find_subsystems


async def main() -> None:
    for board in await find_devices():
        print(board.name, board.model, board.driver_name, board.instance)
        for sub in await find_subsystems(board):
            print(f"  {sub.type.value} element={sub.element} "
                  f"channels={sub.num_channels}")


anyio.run(main)
```

`find_subsystems(board)` accepts a `BoardInfo` or a raw board-name string and
returns the `SubsystemInfo` list for that board (also non-raising).

### What the info models carry

`BoardInfo`: `name` (e.g. `"DT9805(00)"`), `model`, `driver_name`, `instance`.

`SubsystemInfo` exposes the most-used capability flags directly — `type`,
`element`, `num_channels`, `cgl_depth`, and the booleans
`supports_singlevalue`, `supports_continuous`, `supports_simultaneous_sh`,
`supports_multisensor`, `supports_dma`, `returns_floats`, plus
`max_throughput_hz`.

## Capabilities of an open session

`SubsystemInfo` is the enumeration-time snapshot. For the full, richer
`CapabilitySet` (built from `olDaGetSSCaps` + `olDaGetSSCapsEx` +
`olDaEnumSSCaps` + `olDaEnumChannelCaps`), query an open session:

```python
async with await open_device(spec) as session:
    caps = await session.capabilities()
    if not caps.supports_multisensor:
        print("This board can't do RTD / strain / bridge inputs.")
    if not caps.supports_continuous:
        print("D/A is single-value only — use session.write(), not play().")
```

This is the clean way to branch *before* configuring a feature the board may
not have. The capability checks are also enforced at configure time: a spec
that needs an absent capability raises `DtolCapabilityError` before any SDK
call (see [Channels](channels.md) and [Counter/timer](counter-timer.md) for the
DT9805 / DT9806 specifics).

## DT9805 / DT9806 capability reality

| Capability | DT9805 | DT9806 |
|------------|--------|--------|
| Analog input (`±10 V`, gain-selected) | ✅ | ✅ |
| Thermocouples (read emf + CJC, software ITS-90) | ✅ | ✅ |
| `returns_floats` (firmware linearisation) | ❌ | ❌ |
| `supports_multisensor` (RTD/strain/bridge) | ❌ | ❌ |
| Analog output (single-value only) | — | ✅ |
| Continuous AO (`play()`) | — | ❌ (`supports_continuous=False`) |
| Digital I/O (one 8-bit port each way) | — | ✅ |
| Counter/timer (`COUNT`/`RATE`/`ONESHOT`/`ONESHOT_RPT`) | — | ✅ |
| Quadrature / tachometer subsystems | — | board-dependent |

See [plan-hardware-functional.md](plan-hardware-functional.md) for the
bench-verified detail behind this table.
