# Waveform output (continuous analog output)

`play()` drives a *continuous* (streaming) D/A subsystem (`OLSS_DA`) as a
waveform generator — the output mirror of [`record()`](task-specs.md). Where
`record()` *drains* an input buffer ring and yields `DaqBlock`s, `play()`
*fills* an output ring from a waveform source and streams it to the DAC under
the hardware sample clock.

For one-off DC levels use [`DtolSession.write()`](safety.md) instead; `play()`
is for clocked, repeating, or streamed waveforms.

!!! danger "The DT9805/DT9806 D/A cannot do continuous output"
    Bench-confirmed (2026-05-28): the DT9806 D/A is **single-value only** — it
    reports `OLSSC_SUP_CONTINUOUS=0`, has no FIFO, and supports no wrap modes.
    `play()` raises `DtolCapabilityError` on it and points you at
    [`DtolSession.write()`](safety.md). The `play()` machinery is verified
    against the fake backend and is ready for a future DT-Open Layers board
    whose D/A *does* report continuous support (a waveform-DAC board); it is
    **not** usable on the DT9805/06. See
    [the hardware plan](plan-hardware-functional.md) and `docs/decisions.md`.

## `play()`

```python
async with play(session, source, *, confirm=False, error_policy=ErrorPolicy.RAISE) as summary:
    ...
```

- `session` — an analog-output continuous task (every channel an
  `AnalogOutputVoltage`, `data_flow=CONTINUOUS`, `buffers.wrap_mode` either
  `SINGLE` or `MULTIPLE`). Open it with `autostart=False` — `play()` drives the
  `commit → register → seed → queue → arm → start` lifecycle itself.
- `source` — see [Wrap modes and source shapes](#wrap-modes-and-source-shapes).
- `confirm` — operator confirmation for the [§18 safety gate](safety.md),
  exactly as `DtolSession.write`.
- `error_policy` — how SDK errors reaching the producer loop are surfaced
  (`RAISE` cancels playback after a shielded teardown; `RETURN` / `SKIP` log +
  count and keep streaming).

The context yields the mutable `AcquisitionSummary`; read
`summary.underruns_observed` / `summary.payloads_emitted` during or after the run.

## Wrap modes and source shapes

The `BufferPlan.wrap_mode` selects the playback model, which in turn dictates
the `source` shape.

| `wrap_mode` | Behaviour | `source` |
|-------------|-----------|----------|
| `SINGLE` | The SDK loops the pre-filled buffer ring as one continuous waveform. No refill. | A single-period `np.ndarray` shaped `(n_channels, ring_capacity)` (or `(ring_capacity,)` for one channel), where `ring_capacity == buffers × samples_per_buffer`. |
| `MULTIPLE` | Each `BUFFER_DONE` pulls the next chunk, fills the emptied buffer, and re-queues it. | An async iterator, or a `() -> np.ndarray | None` callable, yielding `(n_channels, samples_per_buffer)` chunks. `None` ends finite playback cleanly. |

### `SINGLE` — loop one period

```python
import numpy as np

from dtollib import (
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    SubsystemType,
    TaskSpec,
    Timing,
    WrapMode,
    open_device,
    play,
)

spec = TaskSpec(
    name="sine_1khz",
    channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", safe_min=-5.0, safe_max=5.0)],
    data_flow=DataFlow.CONTINUOUS,
    timing=Timing(rate_hz=100_000.0),
    buffers=BufferPlan(buffers=4, samples_per_buffer=250, wrap_mode=WrapMode.SINGLE),
)

# One period spanning the whole ring (4 × 250 = 1000 samples).
t = np.linspace(0, 2 * np.pi, 1000, endpoint=False)
period = 5.0 * np.sin(t)

session = await open_device(spec, autostart=False)
async with play(session, period, confirm=True) as summary:
    await anyio.sleep(2.0)  # the SDK loops the period for 2 s
```

### `MULTIPLE` — stream successive chunks

```python
spec = TaskSpec(
    name="stream_out",
    channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", safe_min=-5.0, safe_max=5.0)],
    data_flow=DataFlow.CONTINUOUS,
    timing=Timing(rate_hz=100_000.0),
    buffers=BufferPlan(buffers=4, samples_per_buffer=250, wrap_mode=WrapMode.MULTIPLE),
)


async def chunks():
    phase = 0.0
    for _ in range(400):  # 400 chunks then exhaust
        t = phase + np.linspace(0, 0.0157, 250, endpoint=False)
        phase = t[-1]
        yield 5.0 * np.sin(t)  # (250,) volts


session = await open_device(spec, autostart=False)
async with play(session, chunks(), confirm=True) as summary:
    while session.is_running():
        await anyio.sleep(0.1)
    print("underruns:", summary.underruns_observed)
```

The source's first `buffers` chunks seed the ring before `start`; each
subsequent `BUFFER_DONE` pulls one more. When the source yields `None` the
run stops cleanly.

## Safety gate

`play()` enforces the same [§18 gate](safety.md) as `DtolSession.write`, applied
to **every** sample:

- A sample outside the channel's device range `[min_val, max_val]` → always a
  `DtolValidationError`, raised **before any waveform reaches the DAC**
  (`confirm` does not override an electrically-impossible value).
- A sample outside the safe band `[safe_min, safe_max]`, or any channel with
  `requires_confirm=True`, requires `confirm=True`, else a
  `DtolConfirmationRequiredError`.

For a `SINGLE` ndarray the whole period is gated up front. For a `MULTIPLE`
stream, each chunk is gated as it is pulled — a bad chunk mid-stream raises
after the shielded teardown, regardless of `error_policy`.

## Underruns

If the DAC drains the ring faster than refills arrive (a slow `MULTIPLE`
source), the SDK posts `OLDA_WM_UNDERRUN_ERROR`, surfaced as
`DtolBufferUnderrunError` and routed by `error_policy`:

- `RAISE` — playback stops and the error propagates out of the `async with`
  block (after the shielded mute → stop → unregister → drain teardown).
- `RETURN` / `SKIP` — logged and counted in `summary.underruns_observed`;
  playback continues.

Size `BufferPlan.buffers` / `samples_per_buffer` so a refill always completes
within one buffer's playout time at `Timing.rate_hz`.

## Encoding

Volts are converted to 16-bit offset-binary device codes in software
(`tasks/_output_gate.ao_volts_to_codes`), matching the read path — the
DT9805/06 family's `olDaVoltsToCode` is unreliable on these boards (see
[Decisions](decisions.md)). Multi-channel buffers are interleaved sample-major.
