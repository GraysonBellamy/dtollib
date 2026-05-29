# Concepts & glossary

`dtollib` mirrors the DT-Open Layers object model closely, so a handful of SDK
terms recur across the guides. This page defines them once.

## The acquisition model

A **board** (e.g. `DT9805(00)`) hosts one or more **subsystems**. A subsystem is
a functional unit of a fixed kind — analog input, analog output, digital in,
digital out, counter/timer, quadrature, tachometer. You configure **one
subsystem per [`TaskSpec`](task-specs.md)**; a task may not mix kinds.

Each subsystem has a number of **channels**. `physical_channel` on a channel
spec is the zero-based index of the channel *on that subsystem*. A task reads or
writes a **channel list** (CGL) — the ordered set of channels it touches.

A configured task moves through a **lifecycle** — configured → committed →
(armed) → running → stopped — driven by `DtolSession` / the
[`TaskBuilder`](api/tasks.md). Single-value tasks `poll()` / `write()` on
demand; continuous tasks stream under the hardware clock via
[`record()`](continuous.md).

## SDK handles and terms

| Term | Meaning |
|------|---------|
| **HDRVR** | A driver handle for one open board. One per board; ref-counted and shared by every session on that board (the per-board lock guards it). |
| **HDASS** | A subsystem handle (`session.raw_hdass`) — the SDK "device" for one configured subsystem. |
| **OLSS_*** | SDK subsystem-type selectors: `OLSS_AD` (analog in), `OLSS_DA` (analog out), `OLSS_DIN`/`OLSS_DOUT` (digital), `OLSS_CT` (counter/timer), `OLSS_QUAD`, `OLSS_TACH`. |
| **OLSSC_*** | Capability flags queried per subsystem — surfaced on [`CapabilitySet`](discovery.md) (e.g. `OLSSC_SUP_CONTINUOUS`, `OLSSC_RETURNS_FLOATS`). |
| **element** | When a board has more than one subsystem of the same kind, `element` indexes them (`0` = first). |
| **CGL / cgl_depth** | Channel-Gain List and its maximum size. |
| **gain** | Programmable-gain-amplifier setting applied before the A/D. Thermocouples use a high gain to resolve µV-level emf. |

## Data shapes

| Term | Meaning |
|------|---------|
| **`DaqReading`** | One on-demand sample set: `values` (per channel), `units`, `sensor_status`, timestamps, optional `error`. Returned by `poll()` / `read_events()`. |
| **`DaqBlock`** | A continuous-mode chunk: a `(n_channels, n_samples)` array plus `channels`, `units`, `block_index`, timestamps. Yielded by `record()`. |
| **`DaqSample`** | One scalar `(channel, sample)` row — what `block_to_long_rows` yields. |
| **sentinel** | A non-numeric sensor condition (`sensor_open`, out-of-range) preserved on `sensor_status` while the matching `values` cell is NaN-filled — never coerced to a plausible number. See [Channels](channels.md#sensor-sentinel-handling). |

## Continuous-acquisition machinery

| Term | Meaning |
|------|---------|
| **HBUF / buffer pool** | The ring of driver buffers (`BufferPlan.buffers`, min 3) the SDK fills and the consumer drains. Distinct from the AnyIO `stream_buffer_size`. |
| **callback bridge** | The §12.3.2 mechanism translating the driver thread's window messages (`BUFFER_DONE`, overrun, etc.) into the async block stream. |
| **wrap mode** | For output: `SINGLE` loops one pre-filled period; `MULTIPLE` refills each emptied buffer. See [Waveform output](waveform-output.md). |
| **overrun / underrun** | SDK-level loss: input overrun (consumer too slow), output underrun (refill too slow). Counted in `AcquisitionSummary`, distinct from consumer-side `payloads_dropped`. |

## Thermocouples on the DT9805 / DT9806

These boards do **not** linearise thermocouples in firmware
(`returns_floats=False`). The wrapper reads the differential thermo-emf at high
gain plus the **CJC** (cold-junction-compensation) sensor on channel 0
(10 mV/°C), then applies the NIST **ITS-90** polynomials in software. Raw A/D
samples are **offset-binary codes**, converted to volts in software. See the
[TC read-path detail](plan-hardware-functional.md).
