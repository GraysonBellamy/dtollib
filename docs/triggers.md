# Triggers & retriggering

`TaskSpec.trigger` decides *when* a configured task starts acquiring. The
default is `SoftwareStart()` — the SDK begins immediately on `start()`. For
hardware-gated capture, assign one of the other start triggers; for
triggered-scan bursts, add a `RetriggerSpec` to the task's `Timing`.

Design reference: [design.md §8.8](design.md). Whether a given trigger source is
honoured depends on the board — the builder maps the spec onto the SDK selector,
but the hardware must support that source.

## Start triggers

All start-trigger specs subclass `TriggerSpec` and are wired into
`olDaSetTrigger` by the `TaskBuilder`.

| Spec | SDK selector | Starts on |
|------|--------------|-----------|
| `SoftwareStart` | `OL_TRG_SOFT` | `start()` — no wait. The default. |
| `ExternalDigitalStart(edge=Edge.RISING)` | `OL_TRG_EXTERN` | The configured edge of the board's external trigger input. |
| `AnalogThresholdStart(channel, level, slope=Edge.RISING)` | `OL_TRG_THRESHPOS` / `OL_TRG_THRESHNEG` | The monitor `channel` voltage crossing `level` in the `slope` direction. |
| `SyncBusStart` | `OL_TRG_SYNCBUS` | A sync-bus back-plane edge (basis for future multi-board coordination). |

```python
from dtollib import (
    AnalogInputVoltage, BufferPlan, DataFlow, ExternalDigitalStart, Edge,
    TaskSpec, Timing,
)

spec = TaskSpec(
    name="shutter_capture",
    channels=[AnalogInputVoltage(physical_channel=0, name="detector")],
    data_flow=DataFlow.CONTINUOUS,
    timing=Timing(rate_hz=50_000.0),
    buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
    trigger=ExternalDigitalStart(edge=Edge.RISING),
)
```

`AnalogThresholdStart` validates `channel >= 0` at construction; `Edge.RISING`
selects the positive-threshold selector, `Edge.FALLING` the negative.

## Retriggering (triggered scan)

A `RetriggerSpec` on `Timing.retrigger` turns the task into a triggered scan:
the SDK collects `multiscan_count` channel-list scans per trigger event. The
builder wires it into `olDaSetTriggeredScanUsage` + `olDaSetMultiscanCount` +
`olDaSetRetriggerMode` (plus the rate/source call for the chosen mode).

| Field | Default | Notes |
|-------|---------|-------|
| `mode` | `RetriggerMode.EXTRA` | `SCAN_PER_TRIGGER`, `INTERNAL` (timer-paced), or `EXTRA` (external source). |
| `multiscan_count` | `1` | Channel-list scans per trigger (≥ 1). |
| `frequency_hz` | `None` | **Required** for `INTERNAL`, forbidden otherwise. |
| `source` | `None` | A `TriggerSpec` — **required** for `EXTRA`, forbidden otherwise. |

```python
from dtollib import RetriggerSpec, RetriggerMode, ExternalDigitalStart, Timing

timing = Timing(
    rate_hz=100_000.0,
    retrigger=RetriggerSpec(
        mode=RetriggerMode.EXTRA,
        multiscan_count=16,
        source=ExternalDigitalStart(edge=Edge.RISING),
    ),
)
```

These mode-specific rules are enforced in `RetriggerSpec.__post_init__`, so a
mis-specified retrigger raises `DtolValidationError` at construction — before
any SDK call.

## Reference triggers

`ReferenceTrigger(source, post_scan_count)` describes pre-/about-trigger
acquisition (collect N samples *after* a reference event). The dataclass and its
validation ship, but composing it onto a `TaskSpec` is **not yet wired** — it is
included for API stability ahead of the feature landing.
