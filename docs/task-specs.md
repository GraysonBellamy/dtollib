# Task specifications

`TaskSpec` is the typed declaration of one DT-Open Layers subsystem
configured for one data-flow mode.  This page walks through every
field, the validation rules, and the common construction patterns.

Design reference: [design.md §8.1][8_1].

## Minimal example

```python
from dtollib import TaskSpec, ThermocoupleInput, ThermocoupleType

spec = TaskSpec(
    name="surface_temperatures",
    channels=[
        ThermocoupleInput(
            physical_channel=0,
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        ),
    ],
)
```

That's enough for a single-value read.  Everything else defaults
sensibly.

## All fields

| Field             | Default                | Notes                                                     |
|-------------------|------------------------|-----------------------------------------------------------|
| `name`            | (required)             | Human-readable task identifier; appears in errors / logs. |
| `channels`        | (required, non-empty)  | Ordered list of `ChannelSpec`s.  Must share subsystem kind. |
| `board`           | `None`                 | Board name (e.g. `"DT9805(00)"`).  `None` = first found.   |
| `subsystem_type`  | inferred from channels | Explicit override.  Conflict with inference is an error.   |
| `element`         | `0`                    | Subsystem element index — non-zero on boards with multiple subsystems of one type. |
| `data_flow`       | `DataFlow.SINGLE_VALUE`| Single-value or continuous (`record`/`record_polled`).      |
| `timing`          | `None`                 | Required for non-`SINGLE_VALUE`; forbidden for it.          |
| `trigger`         | `SoftwareStart()`      | Start/reference/retrigger config — see [Triggers](triggers.md). |
| `buffers`         | `None`                 | Required for non-`SINGLE_VALUE`; forbidden for it.          |
| `logging`         | `None`                 | Optional driver-side raw-counts logging — see [Raw logging](raw-logging.md). |
| `stop_on_error`   | `True`                 | SDK-level `olDaSetStopOnError` — orthogonal to recorder `ErrorPolicy`. |
| `metadata`        | `{}`                   | Free-form task metadata propagated to readings + sinks.     |

## Validation matrix

`__post_init__` enforces the following:

| `data_flow`                                                | `timing`  | `buffers` |
|------------------------------------------------------------|-----------|-----------|
| `SINGLE_VALUE`                                             | forbidden | forbidden |
| `CONTINUOUS` / `FINITE` / `*_PRETRIGGER` / `*_ABOUT_TRIGGER`| required | required |

Other invariants:

- `name` must be non-empty.
- `channels` must be non-empty.
- All `channels` must share an inferred `SubsystemType`.
- `subsystem_type`, if given explicitly, must match the inferred kind.

## Subsystem-kind inference

The inferred `SubsystemType` follows the channel kinds: analog-input
specs resolve to `ANALOG_INPUT`, `AnalogOutputVoltage` to
`ANALOG_OUTPUT`, digital ports to `DIGITAL_INPUT` / `DIGITAL_OUTPUT`,
and counter/timer specs to `COUNTER_TIMER` (or `QUADRATURE` /
`TACHOMETER`). A
`TaskSpec` may not mix kinds — every channel must share one inferred
subsystem.

## Continuous-mode example

```python
from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    Timing,
)

spec = TaskSpec(
    name="heat_flux_run",
    data_flow=DataFlow.CONTINUOUS,
    channels=[
        AnalogInputVoltage(
            physical_channel=0,
            name="heat_flux",
            min_val=-10.0,
            max_val=10.0,
        ),
    ],
    timing=Timing(rate_hz=1000.0),
    buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
)
```

Feed this spec to [`record()` / `record_polled()`](continuous.md) to
stream blocks under the hardware clock.

[8_1]: design.md#81-taskspec
