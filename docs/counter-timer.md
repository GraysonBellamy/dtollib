# Counter/timer, quadrature, and tachometer

`dtollib` exposes the DT9806's counter/timer (`OLSS_CT`), quadrature decoder
(`OLSS_QUAD`), and tachometer (`OLSS_TACH`) subsystems through typed channel
specs. Each is read on demand after [`start()`](task-specs.md): configure →
commit → start → read.

!!! warning "Hardware support varies — modes are capability-gated"
    The selector constants are **bench-verified** (OQ-5a, 2026-05-28, SDK
    V7.0.0.7 — see [Decisions](decisions.md)). But the **DT9805/DT9806 expose
    no quadrature or tachometer subsystem**, and their counter/timer supports
    only `COUNT` / `RATE` / `ONESHOT` / `ONESHOT_RPT` (not `MEASURE`). On those
    boards `CounterFrequency`, `CounterEdgeToEdge`, `QuadratureDecoder`, and
    `Tachometer` raise `DtolCapabilityError` at configure time, driven by the
    runtime capability query. The fake backend models the full feature set, so
    the software path for every spec stays unit-tested regardless.

## Channel specs

| Spec | Subsystem | Mode | Read with |
|------|-----------|------|-----------|
| `CounterEdgeCount` | `OLSS_CT` | event counting | `read_events()` |
| `CounterFrequency` | `OLSS_CT` | gated frequency | `measure_frequency()` |
| `CounterEdgeToEdge` | `OLSS_CT` | interval / pulse-width | `read_events()` |
| `QuadratureDecoder` | `OLSS_QUAD` | quadrature decode | `read_events()` |
| `Tachometer` | `OLSS_TACH` | tachometer | `measure_frequency()` |
| `PulseTrainOutput` | `OLSS_CT` | rate generation | — (output) |
| `OneShotOutput` / `RepetitiveOneShotOutput` | `OLSS_CT` | one-shot | — (output) |

## Event counting

```python
import anyio

from dtollib import CounterEdgeCount, Edge, GateType, TaskSpec, open_device


spec = TaskSpec(
    name="encoder_ticks",
    channels=[
        CounterEdgeCount(
            physical_channel=0,
            count_edge=Edge.RISING,
            gate_type=GateType.SOFTWARE,
        ),
    ],
)


async def main() -> None:
    async with await open_device(spec) as session:
        reading = await session.read_events()
        print(reading.values)  # {"ch0": 4096}


anyio.run(main)
```

`read_events()` returns a `DaqReading` — the same model `poll()` returns — so
counter rows join an Alicat flow row or a thermocouple row on
`(device, t_mono_ns)`.

## Frequency measurement

```python
from dtollib import CounterFrequency, TaskSpec

spec = TaskSpec(name="tach_in", channels=[CounterFrequency(physical_channel=0)])
# ... open_device(spec) ... await session.measure_frequency()
# reading.values == {"ch0": 10000.0}  (Hz)
```

## Pulse-train output

```python
from dtollib import PulseTrainOutput, PulseType, TaskSpec

spec = TaskSpec(
    name="clock_out",
    channels=[
        PulseTrainOutput(
            physical_channel=0,
            frequency_hz=1000.0,
            duty_cycle=0.5,
            pulse_type=PulseType.HIGH_TO_LOW,
        ),
    ],
)
# open_device(spec) → session.start() begins generation; session.stop() ends it.
```

`duty_cycle` must lie in `(0, 1)` and `frequency_hz` must be positive — both
validated client-side at construction. `OneShotOutput` / `RepetitiveOneShotOutput`
take a `pulse_width_s` instead.

## Quadrature and tachometer

`QuadratureDecoder` carries a `decode_mode` (X1/X2/X4) and `index_reset`;
`Tachometer` carries the measurement edges. Both route through their own
subsystems (`OLSS_QUAD` / `OLSS_TACH`), distinct from `OLSS_CT`.

!!! note "Hardware availability"
    Whether a given physical DT9806 exposes `OLSS_QUAD` / `OLSS_TACH` is
    board-dependent (OQ-5b). The fake backend models both so the software path
    is fully testable; the hardware-acceptance tests are gated on a confirmed
    encoder + tach signal source.

## Configuration ordering

The builder always issues `olDaSetCTMode` **first** on a counter channel — the
C/T mode re-types the counter, and gate / pulse / measure-edge setters issued
before it would target the wrong configuration. The fake backend rejects
out-of-order calls, so unit tests catch the bug a real board would only reveal
as silently wrong data.
