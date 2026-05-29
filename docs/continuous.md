# Continuous acquisition

Single-value `poll()` reads one sample per channel on demand. For sustained,
high-rate acquisition you want the hardware sample clock driving a buffer ring
— that is what `record()` provides. `dtollib` offers two recorders:

| Recorder | Timing | Yields | Use when |
|----------|--------|--------|----------|
| [`record()`](#record-hardware-clocked) | **Hardware** sample clock | `DaqBlock`s (`(n_channels, n_samples)`) | You need jitter-free, gap-free sampling at the board's clock rate. |
| [`record_polled()`](#record_polled-software-timed) | **Software** loop | `DaqReading` per tick | A low rate is fine and you want the simplicity of repeated `poll()`s (or to poll a whole `DtolManager`). |

Both are async context managers that yield a handle and run a producer task in
the background. Design reference: [design.md §14](design.md).

## `record()` — hardware-clocked

`record()` drives the [§12.3.2 callback bridge](design.md): it owns the buffer
pool and the bench-proven `commit → register → queue → arm → start` ordering,
and yields `(stream, summary)`.

The task **must** be opened with `autostart=False` — the bridge has to register
the notification window and queue buffers *before* the second `olDaConfig`, so
the recorder calls `commit()` itself. `spec.data_flow` must be `CONTINUOUS` or
`FINITE`, and `spec.buffers` must be set.

```python
import anyio

from dtollib import (
    AnalogInputVoltage, BufferPlan, DataFlow, TaskSpec, Timing, open_device, record,
)


async def main() -> None:
    spec = TaskSpec(
        name="heat_flux_run",
        channels=[AnalogInputVoltage(physical_channel=0, name="heat_flux")],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=10_000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
    )
    async with (
        await open_device(spec, autostart=False) as session,
        record(session) as recording,
    ):
        async for block in recording.stream:
            # block.data is (n_channels, n_samples) in engineering units
            print(block.block_index, block.data.mean(axis=1))
            if recording.summary.payloads_emitted >= 50:
                break
    summary = recording.summary
    print("dropped:", summary.payloads_dropped, "overruns:", summary.overruns_observed)


anyio.run(main)
```

### Parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `stream_buffer_size` | `16` | AnyIO stream depth — the **consumer-side** back-pressure window, distinct from `spec.buffers.buffers` (the SDK's HBUF ring). |
| `error_policy` | `ErrorPolicy.RAISE` | How SDK errors reaching the producer loop are surfaced (see [below](#error-and-overflow-policies)). |
| `overflow` | `OverflowPolicy.DROP_OLDEST` | What to do when the consumer stream is full. The default keeps the SDK queue moving even with a slow consumer. |
| `timeout` | `10.0` | Reserved for a future shutdown-timeout revision; currently informational. |

!!! tip "Loss-proof captures override the default overflow"
    `record()` defaults to `DROP_OLDEST` so a slow consumer can't stall the SDK
    queue into an overrun. For an archival capture you want the opposite —
    `OverflowPolicy.BLOCK` plus a [`RawCountsSink`](raw-logging.md) fed at the
    head of the loop, so back-pressure protects the durable file.

## `record_polled()` — software-timed

`record_polled()` calls `poll()` on a fixed cadence using absolute-target
scheduling, so per-cycle drift never accumulates. It accepts either a single
`DtolSession` (yields one `DaqReading` per tick) **or** a `DtolManager` (yields
`Mapping[str, DeviceResult[DaqReading]]` per tick — every task polled together).

```python
from dtollib import record_polled

async with record_polled(session, rate_hz=10.0) as rec:
    async for reading in rec.stream:
        print(reading.values)
```

It defaults to `OverflowPolicy.BLOCK` (a software poller can pause without
risking an SDK overrun) and `ErrorPolicy.RAISE`.

## The `Recording` handle and `AcquisitionSummary`

`record_polled()` yields a `Recording` (`.stream`, `.summary`, `.rate_hz`);
`record()` yields the `(stream, summary)` tuple directly. The
`AcquisitionSummary` is **mutable and updated in place** during the run, so you
can read progress live:

| Field | Meaning |
|-------|---------|
| `payloads_emitted` | Payloads handed to the consumer. |
| `payloads_dropped` | Consumer-side losses under a `DROP_*` overflow policy (or missed ticks in `record_polled`). |
| `errors_observed` | Backend errors seen by the producer loop. |
| `overruns_observed` / `underruns_observed` | **SDK-level** input overruns / output underruns — distinct from `payloads_dropped`. |
| `started_at` / `finished_at` | UTC timestamps; `finished_at` is set on context exit. |

## Error and overflow policies

`ErrorPolicy` governs what happens when a backend error reaches the producer:

- `RAISE` — cancel the recorder; the exception propagates out of the `async with`.
- `RETURN` — emit a payload with `.error` set and keep streaming (good for long unattended runs).
- `SKIP` — drop the failed payload silently, but still count it in `errors_observed`.

`OverflowPolicy` governs a full consumer buffer: `BLOCK` (back-pressure, lossless),
`DROP_OLDEST` (evict the head), `DROP_NEWEST` (drop the incoming payload). Both
enums live in `dtollib.streaming`.

## Where blocks go next

A `DaqBlock` carries `data`, `channels`, `units`, `block_index`, timestamps, and
an optional `error`. Feed blocks to a [sink](sinks.md) for durable logging, or
reshape one into tidy per-sample rows with `block_to_long_rows`. The
[`dtol-capture`](cli.md#dtol-capture) CLI wires `record()` to a file sink for you.
