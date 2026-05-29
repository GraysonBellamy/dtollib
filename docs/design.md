# `dtollib` Architecture Plan

**Status:** Initial design (v0) — pre-implementation
**Target package name:** `dtollib`
**Proposed role:** Experiment-facing DT-Open Layers acquisition layer for the
existing `alicatlib` / `sartoriuslib` / `watlowlib` / `nidaqlib` ecosystem
**Primary dependency:** the Data Translation **DataAcq SDK** (`oldaapi32.dll` /
`oldaapi64.dll`), wrapped via `ctypes` — there is no upstream Python binding
**Reference design:** [`nidaqlib`](https://github.com/GraysonBellamy/nidaqlib)
(closest sibling — same task/session/recorder shape, but `dtollib` owns the C
binding layer that `nidaqlib` inherits from `nidaqmx-python`)
**Hardware in scope:** DT9805 (multi-sensor USB module — AI only) and DT9806
(adds D/A, DIO, counter/timer)
**Platform support:** Windows only (the DataAcq SDK is Windows-only)
**SDK reference:** `dasdk_digest.md` (1100-line technical digest of
`dasdk.md`, the DataAcq SDK User's Manual UM-18326-AC)

---

## Executive Summary

`dtollib` should be a typed, lifecycle-managed acquisition layer over a
hand-rolled `ctypes` binding to the DT-Open Layers C API. It fits the same
scientific-instrumentation ecosystem as `alicatlib`, `sartoriuslib`,
`watlowlib`, and `nidaqlib`.

Unlike `nidaqlib`, **we own the C-binding layer itself**. There is no
`nidaqmx-python` equivalent in the Data Translation world; the only path
between Python and a DT device is `oldaapi64.dll` through `ctypes`. That
extra responsibility doubles the careful-code surface: in addition to the
typed task-spec / session / recorder / sink stack that the rest of the
ecosystem has, we have to maintain a faithful low-level binding (`capi`)
and the §11.3.2-style driver-thread → asyncio bridge for SDK notification
callbacks.

The design principle, inherited from `nidaqlib`:

> Wrap workflow, not capability. Where the SDK is already clean, re-export.
> Where the workflow benefits from structure (lifecycle, backpressure,
> typed channels, sinks), add it.

But with one addition specific to `dtollib`:

> Treat the C binding as an internal seam, not as a public surface. Users
> live in the typed Python layer; the C layer is documented, tested, and
> reachable via an escape hatch but is not the path of first resort.

v0.1 covers DT9805's primary use case: analog-input voltage and thermocouple
channels, single-value and continuous acquisition, the §11.3.2 callback
bridge, a fake backend, ecosystem-compatible sinks, and a "raw-counts to
disk" fast sink that fills the role TDMS plays in `nidaqlib`. v0.2 adds AO,
DIO, and counter/timer for the DT9806.

---

## 1. Motivation

The existing ecosystem covers:

- `alicatlib` — Alicat mass flow controllers (serial ASCII)
- `sartoriuslib` — Sartorius balances (xBPI + SBI serial protocols)
- `watlowlib` — Watlow temperature controllers (Standard Bus + Modbus RTU)
- `nidaqlib` — NI-DAQmx DAQ acquisition (built on `nidaqmx-python`)

A common experimental rig in this lab combines:

- Thermocouple acquisition on a DT9805 (currently has no Python option that
  fits the ecosystem)
- Mass flow controllers (Alicat)
- Sample-mass logging (Sartorius)
- Optional temperature controller setpoint sweeps (Watlow)
- Optional secondary DAQ on NI hardware (NI 9214, NI 9234, etc.)

Adding DT-Open Layers support closes the most painful gap. Today, anyone
using a DT9805 has two options:

1. Use Data Translation's Windows-only QuickDAQ GUI (no automation, no
   integration with the rest of the rig)
2. Write `ctypes` boilerplate per experiment, with no shared types, no
   shared error handling, and no shared logging

Neither composes with `alicatlib`-style `record() + pipe() + SqliteSink`
pipelines. `dtollib` should close that gap so a DT9805 thermocouple row
lands in the same `run.sqlite` table as an Alicat flow row and a Sartorius
mass row, joined on `(device, t_mono_ns)`.

---

## 2. Core Recommendation

Build `dtollib`, scoped narrowly for v0.1 (analog input only), and grow
deliberately.

### What it should be

- A typed, lifecycle-managed acquisition layer over a `ctypes` binding to
  the DataAcq SDK.
- A driver-thread → asyncio bridge that turns SDK notification callbacks
  into AnyIO memory-object streams of `DaqBlock`s.
- An ecosystem-compatible scalar polling path (`DaqReading`) that joins
  cleanly against `alicatlib.Sample` / `sartoriuslib.Sample` /
  `watlowlib.Sample` on `(device, t_mono_ns)`.
- A fake backend that exercises the entire stack without a real DT device
  installed — enables CI on Linux/macOS GitHub runners despite the SDK
  being Windows-only.
- A "raw counts" fast sink that fills the role NI TDMS plays in
  `nidaqlib` — durable, high-rate logging that does not depend on
  consumer back-pressure.

### What it should not be

- A general-purpose binding generator. We transcribe the headers we need,
  by hand, into the `capi/` package. New SDK functions arrive when a real
  experiment needs them.
- A GUI. QuickDAQ exists; we are not competing with it.
- A re-implementation of the SDK's queue model. The Ready/Inprocess/Done
  queue is the SDK's truth — we expose it through a typed `BufferPlan`
  on the `TaskSpec` and a `BufferPool` helper, not by hiding it.
- Cross-platform. The SDK is Windows-only. Marking the package
  `Operating System :: Microsoft :: Windows` and skipping non-Windows
  install of the real backend is correct.
- A wrapper around every `olDa*` function. v0.1 covers what DT9805 AI
  needs; everything else is justified by a real experiment.

---

## 3. Ecosystem Comparison

### 3.1 Where `dtollib` sits relative to the four siblings

| Pattern | `alicatlib` | `sartoriuslib` | `watlowlib` | `nidaqlib` | `dtollib` |
|---|:-:|:-:|:-:|:-:|:-:|
| Async-first API (`anyio`) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sync facade (`SyncPortal`) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Frozen-dataclass models | ✓ | ✓ | ✓ | ✓ | ✓ |
| `<Root>Error` hierarchy + `ErrorContext` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Manager for many devices | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pluggable sinks (CSV/JSONL/SQLite/Parquet/Postgres) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fake backend for tests | ✓ (FakeTransport) | ✓ (FakeTransport) | ✓ (FakeTransport) | ✓ (FakeDaqBackend) | ✓ (FakeDtolBackend) |
| Hardware tests gated by markers/env vars | ✓ | ✓ | ✓ | ✓ | ✓ |
| Safety gates on destructive ops | ✓ | ✓ | ✓ | ✓ | ✓ |
| `transport/` byte layer | ✓ | ✓ | ✓ | — | — |
| `protocol/` framing layer | ✓ | ✓ | ✓ | — | — |
| `commands/` semantic dispatch | ✓ | ✓ | ✓ | — | — |
| `backend/` (substitutes for transport/protocol/commands) | — | — | — | ✓ | ✓ |
| Owns the low-level C binding | — | — | — | — | **✓ (NEW)** |
| TDMS-equivalent driver-side logging | — | — | — | ✓ (NI driver) | ✓ (RawCountsSink writes from callback thread) |
| Buffer queue exposed to user | — | — | — | hidden | exposed (`BufferPlan` on `TaskSpec`) |

The two new things, relative to `nidaqlib`:

1. **We own `capi`** — the typed `ctypes` binding. `nidaqlib` could
   `import nidaqmx`; we cannot.
2. **We expose `BufferPlan`** — the DT-Open Layers queue model is
   user-visible (Ready / Inprocess / Done), and hiding it leads to
   confusing OVERRUN errors when the consumer is slow. `TaskSpec.buffers`
   carries a typed `BufferPlan` (count, samples-per-buffer, wrap mode,
   queue strategy); the backend implements it as an internal
   `BufferPool` helper around `olDmAllocBuffer` / `olDaPutBuffer` /
   `olDaGetBuffer`.

### 3.2 Architectural seam — where `dtollib` differs from siblings

Serial siblings own the wire-level bytes:

```text
transport ─→ protocol ─→ command ─→ session ─→ device facade ─→ recorder/sinks
```

`nidaqlib` collapses the first three into `nidaqmx-python` and replaces
them with a single backend Protocol:

```text
TaskSpec ─→ TaskBuilder ─→ DtolSession ─→ DaqBackend ─→ nidaqmx ─→ NI driver
                                              │
                                              └─→ FakeDaqBackend (tests)
```

`dtollib` follows `nidaqlib`'s shape but the backend is implemented on top
of *our own* C binding, not a vendor-supplied Python package:

```text
TaskSpec ─→ TaskBuilder ─→ DtolSession ─→ DtolBackend ─→ capi (ctypes) ─→ oldaapi64.dll
                                              │
                                              └─→ FakeDtolBackend (tests)
```

The split between `DtolBackend` (the typed, error-wrapping seam) and
`capi` (the raw `ctypes` surface) is intentional. `capi` is
pure ctypes/Win32 — no error wrapping, no async, no policy. `DtolBackend`
adds error wrapping, opaque-handle ownership, and the per-call
GIL-release discipline. Both are testable in isolation.

---

## 4. Design Goals

1. **Preserve DT-Open Layers correctness.** Do not silently reinterpret
   queue ordering, callback timing, or capability rules. When in doubt,
   surface the SDK's own behaviour through a typed wrapper.
2. **Stable Python API.** `TaskSpec` / `DtolSession` / `record()` should
   read like `nidaqlib`'s API. A user who knows one knows the other.
3. **Ecosystem-aligned logging.** `DaqReading` joins on the same
   `(device, t_mono_ns)` tuple as `alicatlib.Sample` /
   `sartoriuslib.Sample` / `watlowlib.Sample`.
4. **Support both low-rate and high-rate acquisition.** Software polling
   (`record_polled`) for ≤100 Hz, hardware-clocked continuous
   (`record`) for the rest. Different correctness models — different
   recorders.
5. **Explicit lifecycle.** Subsystem allocation, configuration commit,
   start, stop, release, and terminate are all session-managed. Failed
   configuration tears down cleanly.
6. **Typed models at boundaries.** Public inputs and outputs use frozen
   dataclasses with `kw_only=True`, enums, and Protocols.
7. **High-rate efficiency.** `DaqBlock` is `(n_channels, n_samples)`
   `np.ndarray`, not a stream of dataclass instances. Avoid premature
   scalarisation.
8. **Hardware-free tests.** Most behaviour is tested against
   `FakeDtolBackend` — including the §11.3.2 callback bridge, the
   buffer pool, and the recorder shutdown ordering. Real-hardware tests
   are opt-in.
9. **Honest async.** The SDK is synchronous and partially blocking. We
   wrap calls at coarse boundaries with `anyio.to_thread.run_sync`. We
   do **not** pretend the SDK is async-native.
10. **Escape hatches.** Users can always reach `session.raw_hdass` /
    `backend.dll` for advanced operations the wrapper does not cover.
11. **Windows-only is fine.** The SDK is Windows-only and that is
    inherent. Mark the wheel `Operating System :: Microsoft :: Windows`,
    skip non-Windows install of the real backend, run CI tests on Linux
    against `FakeDtolBackend` only.
12. **Runtime capability query is the only authority.** Per-device
    capability matrices (DT9805/DT9806 in Appendix D, vendor datasheets,
    user manuals) are planning context only. The wrapper validates every
    configuration against the live `CapabilitySet` populated from
    `olDaGetDevCaps` / `olDaGetSSCaps` / `olDaGetSSCapsEx` /
    `olDaEnumSSCaps` / `olDaEnumChannelCaps` at session construction.
    Firmware revisions are known to flip capability flags within the
    same model number; trusting the matrix over the runtime query is a
    class of bug.

---

## 5. Non-Goals

- No support for non-DT-Open-Layers hardware.
- No re-implementation of QuickDAQ's GUI features.
- No replacement for the SDK itself (we don't reimplement queue logic in
  Python).
- No support for the legacy continuous pre-trigger / about-trigger data
  flow modes in v0.1 (they are flagged "Legacy Devices" in the manual;
  add only when a real DT9805/DT9806 supports them).
- No automatic re-detection of devices unplugged mid-acquisition (NI
  doesn't either; surfacing the error is enough).
- No ORM, no RPC server, no networked devices.
- No forced dependency on pandas.
- No "device pool" abstraction in v0.1 — `DtolManager` handles many
  named tasks but does not pretend to be a hardware orchestrator.

---

## 6. Proposed Package Layout

```text
src/
  dtollib/
    __init__.py
    py.typed
    version.py
    _version.py             # hatch-vcs generated

    config.py               # DtolConfig + config_from_env
    errors.py               # DtolError hierarchy + ErrorContext
    units.py                # to_pint helper
    _logging.py             # ROOT = "dtollib"
    _runtime.py             # eager_task_factory installer (port from alicatlib)

    capi/                   # LOW-LEVEL ctypes binding (internal) — three layers
      __init__.py
      loader.py             # DLL discovery and load — TWO DLLs: oldaapi + olmem
      types.py              # HDRVR, HDASS, HBUF, HSSLIST, ECODE — opaque ctypes types
      prototypes.py         # argtypes/restype declarations only (raw ctypes surface)
      constants.py          # OL_DF_*, OL_TRG_*, OL_CLK_*, OLSS_*, OLSSC_*, OLDA_WM_*
      errors.py             # ECODE checker; olDaGetErrorString / olDmGetErrorString
      callbacks.py          # WINFUNCTYPE definitions for notification procedures
      conversion.py         # code↔volts; CJC deinterleave; sentinel detection
      api.py                # OpenLayersApi — direct ctypes calls + output-pointer
                            #   extraction + source-aware ECODE → typed exceptions.
                            #   Raises DtolCapiError subclasses; never returns ECODE.

    backend/                # TYPED SEAM (analogue of nidaqlib.backend)
      __init__.py
      base.py               # DtolBackend Protocol
      dataacq.py            # DataAcqBackend (real, wraps capi) — named after the SDK
      fake.py               # FakeDtolBackend (re-exported from testing.py) — named after the Protocol
      _buffer_pool.py       # BufferPool helper (internal) — HBUF alloc/recycle + output fill mode
      _message_window.py    # Win32 home — hidden HWND_MESSAGE window + pump thread (the ONLY user32/kernel32 caller)
      _bridge_common.py     # shared Sentinel/DrainStop/event-map for both bridges
      _callback_bridge.py   # input (AI) bridge — pump-thread → queue.SimpleQueue plumbing (mechanism-agnostic)
      _output_callback_bridge.py  # output (AO) bridge — refill-and-requeue drainer for play()

    system/
      __init__.py
      discovery.py          # find_devices() → list[BoardInfo]
      capabilities.py       # CapabilitySet, query_capabilities
      models.py             # BoardInfo, SubsystemInfo, DeviceInfo

    channels/
      __init__.py
      base.py               # ChannelSpec (kw_only=True, frozen, slots)
      analog_input.py       # AnalogInputVoltage, AnalogInputCurrent,
                            #   ThermocoupleInput, RtdInput, ThermistorInput,
                            #   StrainInput, BridgeInput, IepeInput
      analog_output.py      # AnalogOutputVoltage         (v0.2)
      digital.py            # DigitalInputPort, DigitalOutputPort, DigitalLine (port+bitmask)
      counter_input.py      # CounterEdgeCount, CounterFrequency,
                            #   CounterEdgeToEdge, QuadratureDecoder (v0.2)
      counter_output.py     # PulseTrainOutput, OneShotOutput      (v0.2)

    tasks/
      __init__.py
      spec.py               # TaskSpec, Timing, RawLogging
      builder.py            # TaskBuilder — drives backend.create_subsystem / add_channel / ...
      session.py            # DtolSession — lifecycle, lock, escape hatch
      models.py             # DaqReading, DaqBlock, DaqSample, DataFlow, Edge
      triggers.py           # TriggerSpec hierarchy: SoftwareStart, ExternalDigitalStart,
                            #   AnalogThresholdStart, ReferenceTrigger, RetriggerSpec
      metadata.py           # RunMetadata + sidecar serialisation

    streaming/
      __init__.py
      block.py              # record() — hardware-clocked block path
      poll_source.py        # PollSource / PollSourceAdapter
      recorder.py           # record_polled() — software-timed scalar path
      _types.py             # ErrorPolicy, OverflowPolicy, AcquisitionSummary

    sinks/
      __init__.py
      base.py               # ReadingSink, SampleSink, BlockSink Protocols + block_to_rows, reading_to_row
      _schema.py            # row/column helpers
      memory.py             # InMemorySink
      csv.py                # CsvSink
      jsonl.py              # JsonlSink
      sqlite.py             # SqliteSink
      parquet.py            # ParquetSink
      postgres.py           # PostgresSink
      raw_counts.py         # RawCountsSink — TDMS-equivalent fast path
                            #   (writes from the callback thread, no consumer back-pressure)

    manager.py              # DtolManager — many tasks, ErrorPolicy, DeviceResult

    sync/
      __init__.py
      portal.py             # SyncPortal (direct port from siblings)
      daq.py                # Dtol — sync facade
      session.py            # sync DtolSession wrapper
      recording.py          # sync recording context

    cli/
      __init__.py
      list.py               # dtol-list   (v0.1)
      capture.py            # dtol-capture (v0.1)
      read.py               # dtol-read   (v0.2)
      info.py               # dtol-info   (v0.2)

    constants.py            # re-exports of OL_TRG_*, OL_DF_*, DataFlow, Edge, ...
    testing.py              # FakeDtolBackend convenience builders
    utils.py                # ConvertTempToVolts / ConvertVoltsToTemp /
                            #   GetThermocoupleRange / ComputeRectangular- /
                            #   ComputeDeltaRosette — port from .NET Utility class
    teds.py                 # StrainGageTeds / BridgeSensorTeds — TEDS read helpers (v0.3+)
```

### Modules intentionally omitted

```text
transport/        # no bytes on the wire; replaced by backend/ (same as nidaqlib)
protocol/         # capi is the protocol layer; nothing to re-implement
commands/         # olDa* functions are already typed via _signatures
registry/         # constants live in capi/constants.py; no parallel codes table
```

### Key naming deviation from `nidaqlib`

Where `nidaqlib` uses `nidaq-*` CLI prefixes and `NIDaq*` class prefixes,
`dtollib` uses `dtol-*` and `Dtol*` respectively. `Dtol` is short for
"DT-Open Layers" and matches the package name.

---

## 7. Public API Shape

### 7.1 Basic single-value thermocouple read

```python
import anyio

from dtollib import TaskSpec, ThermocoupleInput, ThermocoupleType, open_device


spec = TaskSpec(
    name="surface_temperatures",
    channels=[
        ThermocoupleInput(
            physical_channel=0,
            name="surface_tc_K",
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        ),
        ThermocoupleInput(
            physical_channel=1,
            name="back_tc_K",
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        ),
    ],
)


async def main() -> None:
    async with await open_device(spec) as session:
        reading = await session.poll()
        print(reading.values)  # {"surface_tc_K": 23.41, "back_tc_K": 22.97}


anyio.run(main)
```

### 7.2 Continuous hardware-clocked voltage acquisition to Parquet

```python
import anyio

from dtollib import (
    AnalogInputVoltage, BufferPlan, DataFlow, TaskSpec, Timing, open_device,
)
from dtollib.streaming import record
from dtollib.sinks import ParquetSink


spec = TaskSpec(
    name="heat_flux_run",
    data_flow=DataFlow.CONTINUOUS,
    channels=[
        AnalogInputVoltage(physical_channel=0, name="heat_flux",
                           min_val=-10.0, max_val=10.0),
        AnalogInputVoltage(physical_channel=1, name="surface_tc",
                           min_val=-0.1, max_val=0.1),
    ],
    timing=Timing(rate_hz=1000.0),
    buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
)


async def main() -> None:
    async with (
        await open_device(spec, autostart=False) as session,
        record(session) as recording,
        ParquetSink("run.parquet") as sink,
    ):
        async for block in recording.stream:
            await sink.write(block)


anyio.run(main)
```

`autostart=False` is required for `record()` because the SDK requires
notification callbacks to be registered **before** `olDaStart()` — same
constraint as NI's `register_every_n_samples_acquired_into_buffer_event`.
See §11.3.2.

### 7.3 Sync facade

```python
from dtollib.sync import Dtol


with Dtol.open_device(spec) as session:
    block = session.read_block(samples_per_channel=1000)
    print(block.data.shape)  # (2, 1000)
```

### 7.4 Escape hatch — direct ctypes access

```python
async with await open_device(spec) as session:
    hdass = session.raw_hdass               # raw HDASS ctypes handle
    dll = session.backend.dll               # raw oldaapi64 ctypes.WinDLL
    status = dll.olDaPause(hdass)           # call any olDa* function directly
```

The escape hatch is documented and supported. The wrapper does not aspire
to cover every SDK function; users with advanced needs reach through.

### 7.5 Unified experiment (DAQ + Alicat + Sartorius)

```python
async with (
    AlicatManager(error_policy=ErrorPolicy.RETURN) as mfc_mgr,
    SartoriusManager(error_policy=ErrorPolicy.RETURN) as bal_mgr,
    DtolManager(error_policy=ErrorPolicy.RETURN) as daq_mgr,
):
    await mfc_mgr.add("fuel_mfc", "/dev/ttyUSB0")
    await bal_mgr.add("sample_mass", "/dev/ttyUSB1")
    await daq_mgr.add("thermal_signals", daq_spec)

    async with (
        record_polled(mfc_mgr, rate_hz=2.0) as mfc_rec,
        record_polled(bal_mgr, rate_hz=2.0) as bal_rec,
        record(daq_mgr.get("thermal_signals")) as daq_rec,
        SqliteSink("run.sqlite") as scalar_sink,
        ParquetSink("daq.parquet") as daq_sink,
    ):
        ...
```

Rows from all three sources join on `(device, t_mono_ns)`.

---

## 8. Core Data Models

All spec dataclasses use `@dataclass(frozen=True, slots=True, kw_only=True)`.
Construction is keyword-only across the public API. See `nidaqlib` design
doc §8 for the rationale (avoids the dataclass-inheritance trap when
defaulted parent fields precede required subclass fields).

### 8.1 TaskSpec

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class TaskSpec:
    name: str
    board: str | None = None                 # e.g. "DT9805(00)" — None = first found
                                             #   matches DT-Open Layers "board name" terminology
    subsystem_type: SubsystemType | None = None  # explicit when channels alone can't disambiguate
    element: int = 0                         # subsystem element index (default 0 = first AI subsys)
    channels: Sequence[ChannelSpec]
    data_flow: DataFlow = DataFlow.SINGLE_VALUE  # SINGLE_VALUE / CONTINUOUS / *_PRETRIG / *_ABOUTTRIG
    timing: Timing | None = None
    trigger: TriggerSpec | None = None
    buffers: BufferPlan | None = None        # required for CONTINUOUS modes
    logging: RawLogging | None = None        # write raw counts to disk from callback thread
    stop_on_error: bool = True               # SDK-level: stop subsystem on OVERRUN / UNDERRUN
                                             #   maps to olDaSetStopOnError; orthogonal to ErrorPolicy (§14.3)
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
```

A `TaskSpec` declares one DT-Open Layers subsystem (HDASS) configured for
one data-flow mode. `subsystem_type` is usually inferred from the channel
kinds (all-AI → `OLSS_AD`, all-AO → `OLSS_DA`, etc.); mixing kinds in a
single `TaskSpec` is a validation error (matches the SDK's own rule that
each HDASS is one subsystem of one type). `element` distinguishes
multiple subsystems of the same type on one board (rare in v0.1 hardware
but valid SDK-wise).

**Why `BufferPlan` lives on `TaskSpec`, not on `record()`:** the SDK
treats buffer count and per-buffer sample width as part of the configured
subsystem state, not as a recorder concern. Two recorders against the
same task would share the same buffer pool. Putting it on the spec means
the pool is created and torn down with the session.

### 8.2 ChannelSpec (base)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelSpec:
    physical_channel: int                    # SDK uses 0-based int, not "Dev1/ai0"
    name: str | None = None                  # display name; falls back to f"ch{physical_channel}"
    unit: str | None = None                  # display unit; informational
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    # Class-level discriminator — used for to_dict / from_dict round-tripping.
    kind: ClassVar[str] = ""
```

Concrete subclasses (`AnalogInputVoltage`, `ThermocoupleInput`,
`RtdInput`, …) each declare a `kind: ClassVar[str]` discriminator.

### 8.3 AnalogInputBase — common AI knobs

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputBase(ChannelSpec):
    channel_type: ChannelType = ChannelType.SINGLE_ENDED   # SE / DIFF / PSEUDODIFF
    gain: float = 1.0
    filter: FilterType | None = None
    encoding: Encoding | None = None         # BINARY / TWOS_COMP / OFFSET_BIN
    coupling: CouplingType | None = None     # AC / DC
```

These map to `olDaSetChannelType` / `olDaSetGainListEntry` /
`olDaSetChannelFilter` / `olDaSetEncoding` / `olDaSetCouplingType` calls
the backend issues after the channel is added to the list.

### 8.4 AnalogInputVoltage

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(AnalogInputBase):
    kind: ClassVar[str] = "ai_voltage"
    min_val: float = -10.0                   # volts
    max_val: float = 10.0                    # volts
```

### 8.5 ThermocoupleInput

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ThermocoupleInput(AnalogInputBase):
    kind: ClassVar[str] = "thermocouple"
    thermocouple_type: ThermocoupleType      # J / K / T / E / R / S / B / N
    min_val_degc: float
    max_val_degc: float
    cjc_source: CjcSource = CjcSource.INTERNAL
    units: TemperatureUnit = TemperatureUnit.DEG_C
```

When the channel emits thermocouple values, the backend reads CJC via
`olDaGetCjcTemperature` (single-value path) or via the in-stream
interleaving enabled by `olDaSetReturnCjcTemperatureInStream` (continuous
path). The wrapper applies linearisation through `olDaGetSingleFloat`
or the SDK's continuous-data CJC compensation as appropriate.

### 8.6 RtdInput, ThermistorInput, StrainInput, BridgeInput, IepeInput

Defined per SDK chapter 3:

- `RtdInput` — `RtdType` (PT100/PT1000/custom), `r0`, `a`, `b`, `c`,
  excitation current source/value.
- `ThermistorInput` — Steinhart–Hart `a`, `b`, `c` coefficients.
- `StrainInput` — `BridgeConfiguration` (FULL/HALF/QUARTER), excitation
  voltage, gauge factor, shunt-cal flag.
- `BridgeInput` — bridge configuration + transducer type (LOAD_CELL,
  PRESSURE, DISPLACEMENT, …) + excitation voltage.
- `IepeInput` — coupling = AC (mandatory for IEPE), excitation current
  enabled, source/value.

All five share `AnalogInputBase` and reuse its knobs.

### 8.7 Timing

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    rate_hz: float                           # internal clock; via olDaSetClockFrequency
    clock_source: ClockSource = ClockSource.INTERNAL
    external_divider: int | None = None      # only for EXTERNAL clock
    retrigger: RetriggerSpec | None = None   # triggered scan mode (post-v0.1)
```

`Timing` is omitted for `DataFlow.SINGLE_VALUE` tasks. After
`olDaConfig`, the wrapper reads the *actual* clock frequency back via
`olDaGetClockFrequency` and records it on the session — the SDK may
quantise to the nearest achievable rate and the user needs the truth,
not the request.

### 8.7a BufferPlan — required for continuous tasks

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BufferPlan:
    buffers: int = 4                         # how many HBUFs in the Ready/Inprocess/Done cycle
                                             #   minimum 3; default 4 (matches QuickDAQ default)
    samples_per_buffer: int                  # samples-per-channel per buffer
    sample_width_bytes: int | None = None    # None = auto-detect from
                                             #   OLSSC_RETURNS_FLOATS + resolution + data width
    wrap_mode: WrapMode = WrapMode.MULTIPLE  # NONE (finite) / SINGLE (DAC waveform) / MULTIPLE
    queue_strategy: QueueStrategy = QueueStrategy.REQUEUE  # REQUEUE / KEEP / FREE_ON_DONE
```

Defaults are conservative — 4 buffers absorb ~4× consumer-latency
hiccups before OLDA_WM_OVERRUN_ERROR. Sizing rule of thumb documented
in `streaming.md`:
`buffers >= ceil(max_consumer_latency_s * rate_hz / samples_per_buffer) + 1`
with a hard minimum of 3 and a recommended floor of 4 in production.

The `+1` is the extra buffer the SDK keeps filling while the drainer is
copying and recycling a just-completed buffer; without it, a consumer
running at exactly the average production rate still produces sporadic
overruns. The minimum-3 floor matches the SDK's own continuous-AI
sample (which allocates `HBUF[3]` — see `dasdk_digest.md:906-918`):
two buffers leave no headroom for "one in the driver, one being
recycled, one queued."

`AcquisitionSummary.overruns_observed > 0` after a run is a signal to
increase `buffers` or `samples_per_buffer`.

### 8.8 TriggerSpec

```python
class TriggerSpec: ...                       # marker base

@dataclass(frozen=True, slots=True, kw_only=True)
class SoftwareStart(TriggerSpec): pass       # OL_TRG_SOFT (default)

@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalDigitalStart(TriggerSpec):
    edge: Edge = Edge.RISING                 # OL_TRG_EXTERN

@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogThresholdStart(TriggerSpec):
    channel: int                             # threshold-monitor channel
    level: float                             # volts
    slope: Edge                              # RISING → OL_TRG_THRESHPOS, FALLING → OL_TRG_THRESHNEG

@dataclass(frozen=True, slots=True, kw_only=True)
class SyncBusStart(TriggerSpec): pass        # OL_TRG_SYNCBUS

@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceTrigger:                      # composed onto TriggerSpec via TaskSpec.reference
    source: TriggerSpec
    post_scan_count: int                     # samples after reference event

@dataclass(frozen=True, slots=True, kw_only=True)
class RetriggerSpec:
    mode: RetriggerMode                      # SCAN_PER_TRIGGER / INTERNAL / EXTRA
    frequency_hz: float | None = None        # required for INTERNAL
    source: TriggerSpec | None = None        # required for EXTRA
```

### 8.9 DaqReading — scalar / cross-instrument bridge

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DaqReading:
    device: str                              # manager-add name; join key with siblings
    task: str | None = None                  # underlying TaskSpec.name
    values: Mapping[str, float | int | bool]
    units: Mapping[str, str | None]
    requested_at: datetime
    received_at: datetime
    t_utc: datetime                          # wall-clock acquisition midpoint
    t_mono_ns: int                           # canonical monotonic join key
    t_midpoint_mono_ns: int | None = None    # integration-window midpoint in monotonic ns
    latency_s: float
    sensor_status: Mapping[str, SensorStatus] = field(default_factory=dict)
                                             # see §13.1 — TC sentinel preservation
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    error: DtolError | None = None           # populated only under ErrorPolicy.RETURN
```

Field shape matches current `nidaqlib.DaqReading` / `alicatlib.Sample` /
`watlowlib.Sample` for join compatibility — the canonical contract is
`t_mono_ns` (int, monotonic ns) plus `t_utc` (datetime, wall-clock at
midpoint) plus `t_midpoint_mono_ns` (int | None, integration-window
midpoint) plus `requested_at` / `received_at` for I/O provenance. The
`latency_s` field name matches alicatlib/watlowlib (sartoriuslib's
`elapsed_s` is a known divergence — same call as in `nidaqlib` §8.6).
The `sensor_status` overlay is the dtollib-specific addition: TC
channels can produce `SENSOR_IS_OPEN` / `TEMP_OUT_OF_RANGE_LOW` /
`TEMP_OUT_OF_RANGE_HIGH` sentinel values that must NOT be coerced into
plausible floats — see §13.1.

### 8.10 DaqBlock — hardware-clocked, rectangular

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DaqBlock:
    device: str
    task: str | None = None
    channels: tuple[str, ...]                # in array-row order
    data: np.ndarray                         # shape == (len(channels), samples_per_channel)
                                             # dtype float64 (post conversion)
    raw_codes: np.ndarray | None             # shape == data.shape, dtype int16 / int32
                                             #   — populated if RawLogging requested
    block_index: int                         # 0-based monotonic per task
    first_sample_index: int                  # cumulative offset since task_started_at
    samples_per_channel: int
    sample_rate_hz: float | None
    block_period_ns: int | None              # ns per sample; derived from sample_rate_hz
    task_started_at: datetime                # wall-clock anchor for sample-time reconstruction
    t0: datetime                             # wall-clock at first sample of THIS block
    t_mono_ns: int                           # canonical monotonic key — at callback receipt
    t_utc: datetime                          # wall-clock at block midpoint
    t_midpoint_mono_ns: int | None = None    # block-midpoint in monotonic ns
    read_started_at: datetime                # provenance
    read_finished_at: datetime               # provenance
    elapsed_s: float
    units: Mapping[str, str | None]
    sensor_status: Mapping[str, np.ndarray] = field(default_factory=dict)
                                             # per-channel sentinel mask (see §13.1)
    error: DtolError | None = None
```

**Sample-time reconstruction** matches current `nidaqlib` §8.7 — derive
each sample's `t_mono_ns` from `block.t_mono_ns + k * block.block_period_ns`
and `t_utc` analogously; do not interpolate off `t0` (which has
scheduler jitter). Use `first_sample_index + k` as the absolute sample
index.

**Why `raw_codes` is a top-level field, not metadata:** users who want the
RawCountsSink path always have raw counts in hand. Storing them as an
optional second array keeps the conversion `olDaCodeToVolts` close to the
data — replay tools can re-derive `data` if scaling changes.

### 8.11 DaqSample

Optional per-sample scalarisation, produced explicitly via
`block_to_long_rows(block)`. Same shape and use cases as `nidaqlib` §8.9.

### 8.12 DataFlow + SubsystemType

Two enums name what SDK calls take. Both are `StrEnum` for JSON-friendly
round-trip via the discriminated serialisation in §19.3.

```python
class DataFlow(StrEnum):
    SINGLE_VALUE             = "single_value"             # OL_DF_SINGLEVALUE
    CONTINUOUS               = "continuous"               # OL_DF_CONTINUOUS (post-trigger)
    FINITE                   = "finite"                   # CONTINUOUS + WrapMode.NONE + sample ceiling
    CONTINUOUS_PRETRIGGER    = "continuous_pretrigger"    # OL_DF_CONTINUOUS_PRETRIG  (legacy, deferred)
    CONTINUOUS_ABOUT_TRIGGER = "continuous_about_trigger" # OL_DF_CONTINUOUS_ABOUTTRIG (legacy, deferred)


class SubsystemType(StrEnum):
    ANALOG_INPUT   = "analog_input"     # OLSS_AD
    ANALOG_OUTPUT  = "analog_output"    # OLSS_DA
    DIGITAL_INPUT  = "digital_input"    # OLSS_DIN
    DIGITAL_OUTPUT = "digital_output"   # OLSS_DOUT
    COUNTER_TIMER  = "counter_timer"    # OLSS_CT
    QUADRATURE     = "quadrature"       # OLSS_QUAD
    TACHOMETER     = "tachometer"       # OLSS_TACH  — first-class, separate from C/T
```

`FINITE` is implemented on top of `CONTINUOUS` + `WrapMode.NONE` + a
`samples_per_channel` ceiling on the configured task. The recorder
stops when the cumulative sample count is reached.

`TACHOMETER` is broken out from `COUNTER_TIMER` despite both flowing
through C/T-like configuration calls — the SDK treats them as distinct
subsystem types (`OLSS_TACH` ≠ `OLSS_CT`), and lumping them would force
runtime branching on per-channel kind.

### 8.13 SubsystemState — explicit lifecycle phase

Borrowed from the .NET API (`SubsystemBase.State`, UMOpenLayers.md:932).
The SDK already tracks this; we expose it instead of synthesising it
from `is_running()` plus implicit flags.

```python
class SubsystemState(StrEnum):
    INITIALIZED                  = "initialized"
    CONFIGURED_FOR_SINGLE_VALUE  = "configured_for_single_value"
    CONFIGURED_FOR_CONTINUOUS    = "configured_for_continuous"
    PRESTARTED                   = "prestarted"          # simultaneous-start pool
    RUNNING                      = "running"
    STOPPING                     = "stopping"
    ABORTING                     = "aborting"
    IO_COMPLETE                  = "io_complete"         # finite/reference-trigger end
```

`DtolSession.state -> SubsystemState` is the canonical state query.
`is_running()` becomes a derived convenience (`self.state ==
SubsystemState.RUNNING`). Tests assert exact transitions instead of
"did we end up running?", and error messages can say "task is in
`STOPPING` — `poll()` is invalid mid-shutdown" instead of guessing.

### 8.14 BufferState — per-HBUF lifecycle

Borrowed from `OIBuffer.State` (UMOpenLayers.md:5958-5963). Tracked on
the internal `RawBuffer` returned by `BufferPool`:

```python
class BufferState(StrEnum):
    IDLE       = "idle"          # allocated, not yet queued
    QUEUED     = "queued"        # on the Ready queue (olDaPutBuffer)
    INPROCESS  = "inprocess"     # SDK is filling it
    COMPLETED  = "completed"     # on Done queue / handed to drainer
    RELEASED   = "released"      # olDmFreeBuffer called; ndarray view is invalid
```

The pool maintains `buffer.state` on every transition. Two payoffs:

1. **Use-after-free becomes an explicit error.** Reading data from a
   `RELEASED` buffer raises `DtolTaskStateError` immediately instead
   of producing silent garbage from freed C memory.
2. **The §12.3.2 shutdown invariant ("drain-wait BEFORE close") is
   asserted, not assumed.** `BufferPool.free_all()` refuses to run
   while any buffer is `INPROCESS`; the fake backend enforces the same.

### 8.15 IOType — channel measurement-kind discriminator

Borrowed from `SupportedChannelInfo.IOType` (UMOpenLayers.md:3924-3946).
Carried on `CapabilitySet.channel_caps[ch]["IOType"]` so the wrapper
can reject "configure channel 3 as RTD" when that channel reports
`IOType.VOLTAGE_IN` only.

```python
class IOType(StrEnum):
    VOLTAGE_IN          = "voltage_in"
    VOLTAGE_OUT         = "voltage_out"
    CURRENT             = "current"
    THERMOCOUPLE        = "thermocouple"
    RTD                 = "rtd"
    THERMISTOR          = "thermistor"
    RESISTANCE          = "resistance"
    STRAIN_GAGE         = "strain_gage"
    BRIDGE              = "bridge"
    ACCELEROMETER       = "accelerometer"          # IEPE
    DIGITAL_INPUT       = "digital_input"
    DIGITAL_OUTPUT      = "digital_output"
    COUNTER_TIMER       = "counter_timer"
    TACHOMETER          = "tachometer"
    QUADRATURE_DECODER  = "quadrature_decoder"
    MULTI_SENSOR        = "multi_sensor"           # see §8.5a
```

`MULTI_SENSOR` is the DT9805 case: one physical channel that the SDK
re-types at configure time based on what's wired to it.

### 8.5a MultiSensor channels — explicit configure-time discriminator

The DT9805 multi-sensor analog input subsystem reports
`IOType.MULTI_SENSOR` on every channel, meaning the same physical
channel can be voltage / current / thermocouple / RTD / strain / bridge
depending on what is wired and how the channel is configured.

The .NET API exposes this through
`SupportedChannelInfo.MultiSensorType` (UMOpenLayers.md:1724,
UMOpenLayers.md:4036, etc.) — a per-channel runtime setter that **must
be called before any other per-channel configuration**, because it
re-types the channel and invalidates earlier per-type settings.

In `dtollib` this is implicit in the channel-spec subclass (you write
`ThermocoupleInput(physical_channel=3, ...)` and the backend issues
the right SDK call), but the *ordering* must be explicit in the backend:

```python
# Inside DataAcqBackend.add_channel(hdass, list_index, spec)
caps = self._capabilities_cache[id(hdass)]
io_type_for_ch = caps.io_type_for_channel(spec.physical_channel)
if io_type_for_ch == IOType.MULTI_SENSOR:
    # FIRST — re-type the channel to the spec's kind.
    self._api.set_multi_sensor_type(
        hdass, spec.physical_channel, spec.kind_to_multi_sensor_type(),
    )
# THEN — all per-type setters (range, gain, TC type, RTD coefficients, ...).
```

Skipping the multi-sensor set on a multi-sensor channel is a
**silent-wrong-data bug**, not an SDK error: the SDK will happily read
voltage off a channel you intended as thermocouple. The fake backend
enforces the ordering for tests; the real backend issues it
**gated on `capabilities.supports_multisensor`** — TC-only modules
(DT9806 reports `OLSSC_SUP_MULTISENSOR=0`) reject
`olDaSetMultiSensorType` with `OLNOTSUPPORTED` and must not have it
called.

`CapabilitySet.io_type_for_channel(ch) -> IOType` and
`spec.kind_to_multi_sensor_type() -> IOType` are the seams.

#### Thermocouples on the DT9805/DT9806 (SDK V7.0.0.7) — application-side

**These boards do not linearise thermocouples in firmware.** UM9800.md
Table 26 lists *Voltage Converted to Temperature*
(`SupportsTemperatureDataInStream`) and `SupportsCjcSourceInternal` as
**unsupported**; only `SupportsCjcSourceChannel` (CJC on channel 0) is
true. So the AD subsystem reports `OLSSC_SUP_THERMOCOUPLES=1`,
`OLSSC_SUP_MULTISENSOR=0`, **`OLSSC_RETURNS_FLOATS=0`** (bench-verified
2026-05-28). The firmware-linearising SDK calls therefore correctly
return `OLNOTSUPPORTED` (ec=36): `olDaSetThermocoupleType`,
`olDaSetReturnCjcTemperatureInStream`, `olDaGetCjcTemperature`, and
`olDaGetSingleValueEx`. They target *intelligent* DT temperature modules,
not these dumb differential front-ends. (The earlier "TC mapping is
hardwired by the connector" note was wrong — the ec=36 is firmware-feature
absence, not a runtime-vs-stored typing distinction.)

The board provides only a **CJC sensor on channel 0 at 10 mV/°C** and
high-impedance **differential** inputs on channels 1–7. The read path is
therefore application-side (see §15 and docs/decisions.md):

1. Configure the AD subsystem in **differential** mode
   (`olDaSetChannelType(OL_CHNT_DIFFERENTIAL=101)`; subsystem-wide, no
   channel argument). Single-ended wiring reads TCs as rail-to-rail noise.
2. Read the **CJC voltage on channel 0 at gain 1** →
   `cjc_°C = V / 0.010`. (At gain 100 the ~0.25 V CJC saturates the ±10 V
   ADC.)
3. Read each TC channel's differential emf at **high gain** (default 100,
   ≈3 µV/LSB) and convert the raw code to volts ourselves —
   `olDaCodeToVolts` returns ECODE=9 "Invalid Encoding" on this board, so
   `code_to_volts` reads the configured encoding/resolution/range
   (`olDaGetEncoding`/`olDaGetResolution`/`olDaGetRange`) and applies the
   offset-binary formula in `conversion.code_to_input_volts`.
4. Linearise with `utils.convert_volts_to_temperature(tc_type, volts,
   cjc_temperature_c=cjc_°C)` — NIST ITS-90 (Types K and J today).

Open-circuit detection is free: an open differential input is pulled to
the +2.5 V reference and pegs the ADC at +full scale, i.e. ≈ `+V_RAIL /
gain` at the input — flagged `SensorStatus.SENSOR_OPEN`. The path is
selected by `supports_thermocouples and not returns_floats`; the
firmware-linearised float path (`returns_floats`) remains for hypothetical
boards that report it. Gated branches live in
`DtolSession._read_all_channels` / `_read_all_channels_app_side_tc` and
`DataAcqBackend.add_channel` (which skips `olDaSetThermocoupleType` on the
application-side path). `ThermocoupleInput` defaults match this hardware:
`channel_type=DIFFERENTIAL`, `gain=100`, `cjc_channel=0`.

---

## 9. Session and Lifecycle Model

### 9.1 DtolSession

```python
class DtolSession:
    def __init__(
        self,
        spec: TaskSpec,
        backend: DtolBackend,
        *,
        timeout: float = 10.0,
    ) -> None: ...

    async def prepare(self) -> None:
        """Allocate HDASS, add channels, set timing/triggers/wrap mode/logging.

        Stops short of `olDaConfig`. After `prepare()` the SDK is ready to
        accept the notification procedure (`register_notification`) and ready
        buffers (`pool.queue_all()`), both of which must land BEFORE
        `olDaConfig` per the SDK sample code (see §12.3.2). Idempotent.

        For single-value mode the recorder calls `prepare()` immediately
        followed by `commit()` — there is no notification or buffer pool to
        interleave."""

    async def commit(self) -> None:
        """Call `olDaConfig`. Must follow `prepare()` and (for continuous
        mode) the notification + buffer-queue setup. Idempotent."""

    async def configure(self) -> None:
        """Convenience: `prepare()` followed by `commit()` with nothing
        between. Correct for SINGLE_VALUE tasks. Continuous-mode callers
        MUST drive the prepare → register → queue → commit sequence
        themselves (or use `record()`, which does it for you)."""

    async def start(self) -> None:
        """olDaStart. Transitions subsystem to running."""

    async def stop(self) -> None:
        """olDaStop — orderly. Blocks until current buffer fills.
        See §9.2 — do NOT call if trigger may never fire; use abort() instead."""

    async def abort(self) -> None:
        """olDaAbort — immediate. Current buffer may be partial."""

    def is_running(self) -> bool:
        """Cheap state query via olDaIsRunning. Safe to call from any thread.
        Derived convenience — equivalent to `self.state == SubsystemState.RUNNING`."""

    @property
    def state(self) -> SubsystemState:
        """Canonical subsystem state (§8.13). Cheap read of the SDK's own
        state-machine via olDaGetSSState. Useful in error messages and tests:
        an invalid poll() during STOPPING gets a precise diagnostic instead
        of a generic "task busy" error."""

    @property
    def queued_buffer_dones(self) -> int:
        """Number of BUFFER_DONE events currently queued for synchronous
        delivery (via olDaGetQueueSize on the Done queue). Useful for
        runtime monitoring: a steadily growing value means the drainer is
        falling behind and an overrun is impending."""

    async def poll(self, *, timeout: float | None = None) -> DaqReading:
        """One-shot scalar read across all channels. ON_DEMAND mode only.
        Raises DtolTaskStateError if the task is buffering."""

    async def read_block(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,
    ) -> DaqBlock:
        """Pulls one buffer worth of data via the buffer pool.
        FINITE / CONTINUOUS modes only. Allocates if pool not yet primed."""

    async def read_inprocess(self) -> DaqBlock | None:
        """Drains the currently-filling HBUF without waiting for it to
        complete. Returns None if the buffer holds zero valid samples.

        Equivalent to .NET's `AnalogInputSubsystem.MoveFromBufferInprocess`
        (UMOpenLayers.md:5933-5945). Requires
        `CapabilitySet.supports_inprocess_flush()` — raises
        DtolCapabilityError otherwise. Useful for low-latency consumers
        on low-rate continuous tasks (200 Hz TC, 1 kHz strain) where
        waiting for a full buffer is unacceptable.

        Backed by olDaCopyFromBuffer on the current Inprocess HBUF;
        returns a copy into a fresh ndarray. The SDK transfers data in
        device-specific segment sizes (some devices in 64-byte chunks),
        so the returned sample count is not necessarily what the user
        requested. Caller checks `block.samples_per_channel` for the
        actual count."""

    async def write(
        self,
        values: Mapping[str, float | bool],
        *,
        confirm: bool = False,
    ) -> None:
        """Single-value write to AO/DO/CO channels.
        confirm=True is mandatory on channels marked requires_confirm. (v0.2)"""

    async def close(self, *, graceful: bool = False) -> None:
        """Tear the session down. Default: abort-if-running, then flush/free/release.

        Why default abort: olDaStop blocks waiting for the current buffer to
        fill, which can hang forever if the configured trigger never fires.
        `close()` is called from `__aexit__` and must not deadlock when an
        outer exception is propagating.

        Pass `graceful=True` to use olDaStop instead — appropriate when the
        caller knows the trigger has fired and wants the final buffer to be
        committed cleanly. The orderly close raises DtolTimeoutError if stop
        does not complete within `self._timeout`."""

    @property
    def raw_hdass(self) -> Any:
        """Escape hatch — raw HDASS for direct olDa* calls."""

    @property
    def raw_hdrv(self) -> Any:
        """Escape hatch — raw HDRVR for direct olDa* calls."""

    @property
    def backend(self) -> DtolBackend:
        """Escape hatch — backend with .dll attribute for direct ctypes calls."""

    # Named diagnostic escape hatches (advanced; documented but rarely used).
    # Map directly to the .NET Device.Diag* methods (UMOpenLayers.md:860-863).
    # Named methods are more discoverable than telling users "drop to ctypes."
    async def diag_read_reg(self, reg: int) -> int:
        """olDaReadDevReg. Raw device-register read. Documented as advanced."""

    async def diag_write_reg(self, reg: int, value: int) -> None:
        """olDaWriteDevReg. Raw device-register write. Documented as advanced
        and gated behind safety policy — same gate as auto-calibration."""

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
```

### 9.2 Lifecycle invariants

The invariants below are stated in terms of `SubsystemState` (§8.13)
transitions. The fake backend enforces every transition; tests assert
the exact sequence rather than "did we end up running?"

Canonical happy path (single-value):
`INITIALIZED → CONFIGURED_FOR_SINGLE_VALUE → RUNNING → IO_COMPLETE → INITIALIZED`

Canonical happy path (continuous, post-trigger):
`INITIALIZED → CONFIGURED_FOR_CONTINUOUS → RUNNING → STOPPING → IO_COMPLETE → INITIALIZED`

Canonical abort path (continuous):
`INITIALIZED → CONFIGURED_FOR_CONTINUOUS → RUNNING → ABORTING → INITIALIZED`

- HDRVR and HDASS are allocated once per session.
- Configuration runs through `olDaConfig` exactly once before start.
- **For continuous mode, `olDaConfig` must be preceded by notification
  registration and `olDaPutBuffer × N`.** The SDK sample code at
  `dasdk_digest.md:906-918` allocates and queues all buffers before
  calling `olDaConfig`; the textual flowchart at `dasdk_digest.md:420-426`
  contradicts itself and is treated as advisory. The canonical order is:
  `prepare → register notification → queue buffers → commit (olDaConfig)
  → start (olDaStart)`. `FakeDtolBackend` enforces this; registering or
  queueing after `olDaConfig` raises a synthetic
  `DtolTaskStateError`.
- Reads and writes serialise on a per-session `anyio.Lock` (HDASS is not
  thread-safe per the SDK; the lock is the seam).
- `close()` is idempotent. Calls in order:
  abort-if-running (or stop, when `graceful=True`)
  → flush buffers → free pool → release HDASS → release HDRVR (refcount).
  HDRVR `olDaTerminate` runs only when the last session against that
  board closes — `DtolManager` (or the implicit per-board cache in
  `open_device`) owns the refcount.
- `__aexit__` always attempts the full cleanup chain, even if a prior
  call raised.
- Wrapped SDK errors include task name, channel name when known,
  operation, and the raw `OLSTATUS` code.
- **`poll()` is invalid mid-buffered-acquisition.** Raises
  `DtolTaskStateError` if the task is configured `CONTINUOUS` /
  `FINITE` and is running. Matches `nidaqlib` §9.2 — competing consumers
  on the same buffer queue would produce undefined results.
- **`poll()` is also invalid during `STOPPING` / `ABORTING`.** The error
  message names the current `SubsystemState` so the caller can
  distinguish "task is buffering" from "task is in the middle of
  shutdown" without guessing.
- **`stop()` blocks** (it waits for the current buffer to fill). If the
  trigger may never fire, use `abort()`. The wrapper does not silently
  prefer one or the other.
- **Callback-bridge shutdown is ordered.** For sessions using the
  `record()` callback bridge, the ordering is the same five-step shutdown
  as `nidaqlib` §9.2 / §11.3.2 (with one rename — see §13).

### 9.3 Open factory

```python
async def open_device(
    spec: TaskSpec,
    *,
    backend: DtolBackend | None = None,
    timeout: float = 10.0,
    autostart: bool = True,
    confirm_start: bool = False,
) -> DtolSession: ...
```

Mirrors `nidaqlib.open_device`. `autostart=False` is required for any
caller that needs to register a callback before start — namely `record()`.

---

## 10. Backend Abstraction

### 10.1 Why a backend, not a transport

There are no serial bytes to fake. The substitution point is the SDK API —
an `HDASS`-shaped seam. Same call as `nidaqlib`, executed at the same
level.

Two concrete implementations of the Protocol:

| Class                | Module                   | Notes |
|----------------------|--------------------------|-------|
| `DataAcqBackend`     | `backend/dataacq.py`     | Real — wraps `capi` (two DLLs). Windows-only at runtime. |
| `FakeDtolBackend`    | `backend/fake.py`        | In-process simulator. Always available; powers all unit tests and Linux/macOS CI. |

The protocol name (`DtolBackend`) is package-prefixed; the real
implementation (`DataAcqBackend`) is named after the SDK it wraps so
that a hypothetical future replacement (e.g. a DT-Open-Layers v2 SDK
binding) would naturally be a sibling `DataAcqV2Backend`. The fake is
named after the Protocol (matching `nidaqlib`'s `FakeDaqBackend`).

### 10.2 DtolBackend Protocol

```python
class DtolBackend(Protocol):
    # Lifecycle
    def initialize(self, board_name: str) -> Any: ...            # olDaInitialize → HDRVR
    def terminate(self, hdrv: Any) -> None: ...                  # olDaTerminate
    def get_dass(
        self, hdrv: Any, subsys_type: SubsystemType, element: int
    ) -> Any: ...                                                # olDaGetDASS
    def release_dass(self, hdass: Any) -> None: ...              # olDaReleaseDASS
    def is_running(self, hdass: Any) -> bool: ...                # olDaIsRunning
    def get_state(self, hdass: Any) -> SubsystemState: ...       # olDaGetSSState → §8.13

    # Discovery
    def enum_boards(self) -> tuple[BoardInfo, ...]: ...          # olDaEnumBoardsEx
    def enum_subsystems(self, hdrv: Any) -> tuple[SubsystemInfo, ...]: ...
    def get_version(self) -> tuple[str, str]: ...                # (olDaGetVersion, olDmGetVersion)

    # Capability
    def query_capabilities(self, hdass: Any) -> CapabilitySet: ...
        # Composes olDaGetSSCaps / olDaGetSSCapsEx / olDaEnumSSCaps /
        # olDaEnumChannelCaps into one snapshot. See §13.

    # Configuration
    def set_data_flow(self, hdass: Any, mode: int) -> None: ...
    def set_stop_on_error(self, hdass: Any, stop: bool) -> None: ...    # olDaSetStopOnError
    def set_multi_sensor_type(
        self, hdass: Any, physical_channel: int, io_type: IOType,
    ) -> None: ...                                                       # see §8.5a
    def add_channel(self, hdass: Any, list_index: int, spec: ChannelSpec) -> None: ...
        # Drives olDaSetChannelListEntry / olDaSetChannelType / olDaSetGainListEntry / ...
        # Calls set_multi_sensor_type FIRST on MULTI_SENSOR channels (§8.5a).
    def configure_timing(self, hdass: Any, timing: Timing) -> None: ...
    def configure_trigger(self, hdass: Any, trigger: TriggerSpec) -> None: ...
    def configure_logging(self, hdass: Any, logging: RawLogging) -> None: ...
    def commit(self, hdass: Any) -> None: ...                    # olDaConfig

    # Operation
    def start(self, hdass: Any) -> None: ...                     # olDaStart
    def stop(self, hdass: Any) -> None: ...                      # olDaStop
    def abort(self, hdass: Any) -> None: ...                     # olDaAbort
    def reset(self, hdass: Any) -> None: ...                     # olDaReset
    def get_single_value(self, hdass: Any, channel: int, gain: float) -> int: ...
    def get_single_values(self, hdass: Any, gain: float) -> np.ndarray: ...
    def put_single_value(self, hdass: Any, channel: int, value: int, gain: float) -> None: ...
    def code_to_volts(self, hdass: Any, code: int, gain: float) -> float: ...

    # Buffer pool — owned by the backend, lifecycled with the subsystem
    def create_buffer_pool(self, hdass: Any, plan: BufferPlan) -> BufferPool: ...
    def free_buffer_pool(self, pool: BufferPool) -> None: ...
    def read_done_buffer(self, hdass: Any) -> RawBuffer | None: ...
        # Wraps olDaGetBuffer + olDmGetBufferPtr + olDmGetValidSamples.
        # Dispatches on OLSSC_RETURNS_FLOATS: float-returning devices skip
        # code-to-volts; int-returning devices apply per-channel scaling.
    def requeue_buffer(self, hdass: Any, raw: RawBuffer) -> None: ...
    def flush_buffers(self, hdass: Any) -> None: ...
    def get_done_queue_size(self, hdass: Any) -> int: ...               # olDaGetQueueSize
        # Number of completed buffers on the Done queue waiting for drainage.
        # Backs DtolSession.queued_buffer_dones — runtime back-pressure indicator.
    def move_inprocess_buffer(self, hdass: Any) -> RawBuffer | None: ...  # olDaCopyFromBuffer
        # Snapshot of the currently-filling HBUF without waiting for it to
        # complete. Returns None on zero valid samples. Requires
        # CapabilitySet.supports_inprocess_flush(). Powers session.read_inprocess.

    # Diagnostic register access (advanced — gated, rarely used)
    def diag_read_reg(self, hdrv: Any, reg: int) -> int: ...            # olDaReadDevReg
    def diag_write_reg(self, hdrv: Any, reg: int, value: int) -> None: ...  # olDaWriteDevReg

    # Notification
    def register_notification(
        self,
        hdass: Any,
        callback: Callable[[SdkEventKind, int, int], None],
        #                  (kind, wparam, lparam) - runs on DRIVER THREAD
    ) -> NotificationHandle: ...
    def unregister_notification(self, hdass: Any, handle: NotificationHandle) -> None: ...

    # Escape hatch
    @property
    def dll(self) -> Any: ...                                     # raw oldaapi WinDLL
    @property
    def memdll(self) -> Any: ...                                  # raw olmem WinDLL
```

### 10.3 The C-boundary stack — three layers

The hand-rolled C boundary is the new risk surface relative to `nidaqlib`
(which inherits a tested Python binding from `nidaqmx-python`). To make
the boundary testable in isolation, it splits into three layers:

```text
capi/prototypes.py          # raw ctypes signatures only — no error handling,
                            # no output-pointer extraction, no policy
   ↓
capi/api.py :: OpenLayersApi   # one-call-per-method wrapper. Calls the prototype,
                            # extracts output pointers, calls the source-aware
                            # ECODE checker, raises typed DtolCapiError subclasses.
                            # No session state, no caching, no buffer pool.
   ↓
backend/dataacq.py :: DataAcqBackend   # session-level orchestration. Holds
                            # capability cache, notification wrapper dict,
                            # buffer-pool helper. Drives the DtolBackend
                            # Protocol surface against OpenLayersApi.
```

`OpenLayersApi` exists because the prototype layer is too raw to test
(raw ECODE returns, output pointers as `POINTER(HDASS)` references)
and the backend is too concerned with session state. Splitting them
means:

- **`OpenLayersApi` is testable against a stub DLL**, without a backend
  instance. The Windows binding-test lane exercises this layer directly:
  signature shape, output-pointer extraction, ECODE → exception
  classification, error-string DLL-source dispatch.
- **`DataAcqBackend` is testable against a stub `OpenLayersApi`**,
  without ctypes. The unit-test lane (cross-platform) exercises this
  layer: ordering invariants, capability cache, buffer-pool lifecycle,
  notification-wrapper GC pinning.

Example seam:

```python
class OpenLayersApi:
    def __init__(self, dlls: OpenLayersDlls) -> None:
        self._oldaapi = dlls.oldaapi
        self._olmem = dlls.olmem
        # prototypes already declared on dlls at load time
        ...

    def initialize(self, board_name: str) -> int:
        hdrv = HDRVR()
        status = self._oldaapi.olDaInitialize(board_name.encode("ascii"),
                                              ctypes.byref(hdrv))
        self._check(status, op="olDaInitialize", source="oldaapi",
                    board=board_name)
        return hdrv.value

    def get_dass(self, hdrv: int, subsys: int, element: int) -> int:
        hdass = HDASS()
        status = self._oldaapi.olDaGetDASS(hdrv, subsys, element,
                                           ctypes.byref(hdass))
        self._check(status, op="olDaGetDASS", source="oldaapi",
                    subsystem_type=subsys, element=element)
        return hdass.value

    # ... one method per olDa*/olDm* function used by the backend
```

### 10.4 `DataAcqBackend` (real implementation)

Lives in `backend/dataacq.py`. Drives the `OpenLayersApi` from §10.3
to satisfy the `DtolBackend` Protocol. Does NOT call ctypes directly
— every C call goes through `OpenLayersApi`.

Responsibilities:

- Construct via `capi.loader.load_openlayers()` + `OpenLayersApi(dlls)`
  — 32-vs-64-bit detect via `struct.calcsize('P')`. Two `WinDLL`
  instances; two env-var overrides (`DTOLLIB_OLDAAPI_DLL`,
  `DTOLLIB_OLMEM_DLL`).
- Detect `OLSSC_RETURNS_FLOATS` for each subsystem at `query_capabilities`
  time and cache it. The buffer pipeline branches on this — float-returning
  devices skip per-sample voltage conversion; int-returning devices apply
  per-channel scaling derived from range / gain / resolution / encoding.
- Maintain `self._notification_wrappers: dict[int, WINFUNCTYPE]` keyed
  by `id(hdass)` — the SDK stores callbacks as raw C function pointers
  and Python GC will silently break the seam otherwise (same hazard as
  `nidaqlib` §11.3.2).
- Convert `HBUF` data via `OpenLayersApi.get_buffer_ptr` into a
  `numpy.ndarray` with `numpy.ctypeslib.as_array` (zero copy until the
  buffer is freed or recycled).

### 10.5 `FakeDtolBackend` (test backend)

Lives in `backend/fake.py`, re-exported from `testing.py`. Capabilities:

- Scripted boards and subsystems (with custom `CapabilitySet`s including
  `OLSSC_RETURNS_FLOATS` toggling per subsystem so the float/int dispatch
  is testable).
- Scripted scalar reads.
- Scripted block sequences (lists of `np.ndarray` keyed by task name).
- Scripted SDK events of every kind in `SdkEventKind` — tests fire
  `fake.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)` to exercise
  recorder error paths without hardware.
- Simulated timeouts and `ECODE` failures (user injects codes).
- Enforces the same ordering invariants the real SDK enforces:
  - Register BEFORE start (rejects with synthetic error code).
  - Unregister AFTER stop (rejects with synthetic error code).
  - `olDmGetBufferPtr` on freed `HBUF` raises (catches use-after-free).
- Operation log for assertions (`fake.operations: list[tuple[str, object]]`).

---

## 11. The C Binding Layer (`capi`)

This module is the part that `nidaqlib` does not have. It is the single
place in the package where `ctypes.WinDLL` is touched. Internally it
splits into three layers (see §10.3): `prototypes.py` (raw ctypes
signatures), `api.py` (`OpenLayersApi` — output-pointer extraction +
typed exception wrapping), and the supporting `loader.py` / `types.py`
/ `constants.py` / `callbacks.py` / `conversion.py` / `errors.py`
modules described below.

### 11.1 Loader (`capi/loader.py`) — TWO DLLs

**SDK install layout (reference — what the DataAcq SDK installer drops on disk).**
On 64-bit Windows the SDK installer is a 32-bit installer, so the SDK
tree lands under `Program Files (x86)` regardless of OS bitness. The
manual (`dasdk.md`) claims 32-bit libs install to `C:\Program Files\...`
and 64-bit libs to `C:\Program Files (x86)\...` — that's misleading;
on modern 64-bit Windows *both* land under `Program Files (x86)`. The
runtime DLLs themselves land in `System32` / `SysWOW64` per the usual
WOW64 redirection rules (and that is what `capi/loader.py` looks for —
nothing under `Program Files` is touched at load time).

| Location                                                       | Contents                                                           |
|----------------------------------------------------------------|--------------------------------------------------------------------|
| `%SystemRoot%\System32\` (64-bit DLLs) / `%SystemRoot%\SysWOW64\` (32-bit DLLs on 64-bit Windows) | **Runtime DLLs** — `oldaapi64.dll` + `olmem64.dll` (or `*32.dll`). This is what `capi/loader.py` looks for. Verified present on a working DT9805 install. |
| `C:\Program Files (x86)\Data Translation\Win32\SDK\Include\`   | C **headers** — `OLDAAPI.H`, `Olmem.h`, `OLERRORS.H`, plus `OLTYPES.H`, `OLDADEFS.H`, `OLDSPTCH.H`, `Oldacfg.h`, `GRAPHS.H`, `TedsApi.h`, `DtDeviceInterface.h`, `DtIoctl.h`, `DtRegKeys.h`. Used only at dev time when transcribing prototypes/constants (§11.3, §11.4); not needed at runtime. |
| `C:\Program Files (x86)\Data Translation\Win32\SDK\Lib\` (32-bit) and `...\SDK\Lib64\` (64-bit) — both under the same root | C **import libraries** — `oldaapi32.lib` / `oldaapi64.lib` / `olmem32.lib` / `olmem64.lib` plus `.exp` files. Not needed by `dtollib` at all (we use `ctypes`, not link-time binding). |
| `C:\Program Files (x86)\Data Translation\Win32\SDK\Examples\`  | C **example programs** (`SvAdc`, `ContAdc`, `ThermoADC`, `IepContAdc`, `CExample`, etc.). Reference material for the maintainer; cited from `dasdk.md`. |

Three consequences for this project:

1. **`dtollib` ships no DLLs and no headers.** The user installs the
   DataAcq SDK separately; the runtime DLLs land in `System32` and that
   is what we load.
2. **Headers are a maintainer-only convenience.** `capi/prototypes.py`
   and `capi/constants.py` are hand-transcribed from `dasdk_digest.md`
   plus the on-disk headers when available. `scripts/gen_openlayers.py`
   parses them only when a maintainer has the SDK installed locally —
   see Appendix D Q3 for the redistribution question.
3. **`dtol-diag sdk` is the user-facing surface for this layout.** It
   reports the resolved DLL paths and, when DLLs cannot be found, hints
   at the expected SDK install location (§21.3). The `Include\` and
   `Lib\` folders are not probed at runtime — their absence is irrelevant
   to acquisition.

**Runtime split.** The DataAcq SDK splits its API surface across two DLLs:

| DLL                                       | Functions                | Header  |
|-------------------------------------------|--------------------------|---------|
| `oldaapi32.dll` / `oldaapi64.dll`         | `olDa*` (acquisition)    | `OLDAAPI.H` |
| `olmem32.dll`   / `olmem64.dll`           | `olDm*` (buffer/memory)  | `OLMEM.H`   |

Both must be loaded. They are separate `WinDLL` instances and each has
its own error-string function (`olDaGetErrorString` vs
`olDmGetErrorString`) — the classifier in `capi/errors.py` knows which
to call based on the function that returned the error.

```python
@dataclass(frozen=True, slots=True)
class OpenLayersDlls:
    oldaapi: ctypes.WinDLL
    olmem: ctypes.WinDLL
    oldaapi_path: Path
    olmem_path: Path
    bitness: int                             # 32 or 64

def load_openlayers(
    *,
    oldaapi_path: str | os.PathLike | None = None,
    olmem_path: str | os.PathLike | None = None,
) -> OpenLayersDlls:
    """Locate and load both DataAcq SDK DLLs.

    Resolution order (applied independently to each DLL):
        1. Explicit path argument (if provided)
        2. Environment variable:
             DTOLLIB_OLDAAPI_DLL for oldaapi*.dll
             DTOLLIB_OLMEM_DLL   for olmem*.dll
        3. Default install path for the current bitness:
             64-bit: %SystemRoot%\\System32\\oldaapi64.dll
                     %SystemRoot%\\System32\\olmem64.dll
             32-bit (on 64-bit Windows): %SystemRoot%\\SysWOW64\\oldaapi32.dll
                                        %SystemRoot%\\SysWOW64\\olmem32.dll
        4. ctypes.WinDLL("oldaapi64" / "olmem64") — relies on SDK installer
           having added the DLLs to the system PATH.

    Raises:
        DtolDependencyError: a DLL was not found at any candidate location.
                             Message includes ALL candidate paths tried.
        DtolDependencyError: bitness mismatch (e.g. 32-bit Python loading
                             64-bit DLL).
        DtolDependencyError: platform is not Windows.
    """
```

Diagnostic surface: every successful load logs at INFO level with both
DLL paths and detected bitness. Failure messages include the full
candidate list — these are the messages the `dtol-diag sdk` CLI surfaces
(§21).

### 11.2 Opaque types (`capi/types.py`)

```python
HDRVR    = ctypes.c_void_p   # device handle
HDASS    = ctypes.c_void_p   # subsystem handle
HBUF     = ctypes.c_void_p   # buffer handle
HLIST    = ctypes.c_void_p   # buffer list handle
HSSLIST  = ctypes.c_void_p   # simultaneous-start list handle
OLSTATUS = ctypes.c_ulong    # SDK return code
ECODE    = ctypes.c_ulong    # error code (alias for OLSTATUS)
```

### 11.3 Prototypes (`capi/prototypes.py`)

Every `olDa*` / `olDm*` function used by the backend has its `argtypes`
and `restype` declared explicitly. Example:

```python
def declarecapi(dll: ctypes.WinDLL) -> None:
    dll.olDaInitialize.argtypes = [ctypes.c_char_p, ctypes.POINTER(HDRVR)]
    dll.olDaInitialize.restype = ECODE

    dll.olDaGetDASS.argtypes = [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)]
    dll.olDaGetDASS.restype = ECODE

    dll.olDaConfig.argtypes = [HDASS]
    dll.olDaConfig.restype = ECODE

    dll.olDaSetWndHandle.argtypes = [HDASS, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM]
    dll.olDaSetWndHandle.restype = ECODE
    # NOTE: the user-data (lParam) arg is LPARAM (pointer-sized), not c_long. On
    # 64-bit Python with 64-bit oldaapi64.dll, c_long is 32-bit while the SDK
    # expects LONG_PTR — passing c_long delivers a truncated user-data value to
    # the window. olDaSetWndHandle is the buffer-done mechanism we actually bind;
    # olDaSetNotificationProcedure is NOT bound (its OLNOTIFYPROC callback never
    # fires on V7.0.0.7 — see §12.3.2).

    # ... ~60 olDa* functions in v0.1; ~20 more in v0.2

def declare_olmem(dll: ctypes.WinDLL) -> None:
    dll.olDmCallocBuffer.argtypes = [ctypes.c_uint, ctypes.c_uint, ULNG, UINT,
                                     ctypes.POINTER(HBUF)]
    dll.olDmCallocBuffer.restype = ECODE
    # ... ~20 olDm* functions in v0.1
```

Two separate declaration functions because each `WinDLL` only knows its
own exports. The signatures live in functions (not at module-import time)
because they bind to specific `WinDLL` instances — important for
testability (swap in a `ctypes.CDLL("./fake.dll")` for unit tests of the
binding itself, separate from `FakeDtolBackend`).

### 11.4 Constants (`capi/constants.py`)

All SDK enums transcribed by hand. Single source of truth for
`OL_DF_SINGLEVALUE`, `OL_DF_CONTINUOUS`, `OL_TRG_SOFT`,
`OL_TRG_THRESHPOS`, `OL_CLK_INTERNAL`, `OLSS_AD`, `OLSS_DA`,
`OLSSC_SUP_SINGLEVALUE`, `OLSSC_RETURNS_FLOATS`, `OLDA_WM_*`, etc. Each
constant has a docstring naming the header that defines it.

#### Authoritative source: `OLDADEFS.H` (C, NOT `OLDADEFS.bas`)

`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\OLDADEFS.H`
is the authoritative C header — every published example
(`IepContAdc.c`, `ThermoADC.C`, `DtConsole.cpp`) compiles against it
and the `oldaapi64.dll` exposes the same numeric values. The
**older `DTx-EZ\Include\OLDADEFS.bas`** (VB binding, dated 1997) uses
a different — much simpler — numbering (`OL_DF_CONTINUOUS=0`,
`OL_WRP_MULTIPLE=1`, etc.) and **DOES NOT match the live DLL ABI**.
Bench-confirmed on SDK V7.0.0.7 (docs/decisions.md, 2026-05-28):
the C-header values are correct, the VB-binding values are rejected
by the SDK with `OLBADDATAFLOW` / `OLBADWRAPMODE` / `OLBADQUEUE`.

Verified value families (see `capi/constants.py` for the full set):

- `OL_CHNT_*` = 100, 101
- `OL_ENC_*` = 200, 201
- `OL_TRG_*` (legacy) = 300–306
- `OL_CLK_*` = 400, 401, 402
- `OL_DF_*` = 800–805
- `OL_WRP_*` = 1000, 1001, 1002
- `OL_QUE_*` = 1100, 1101, 1102
- `OL_TRG_THRESHPOS / THRESHNEG / SYNCBUS` = 1200, 1201, 1202
- `OL_RETRIG_*` = 1300, 1301, 1302
- `OL_THERMOCOUPLE_TYPE_*` = 1500–1508
- `OLDA_WM_*` = `WM_USER + 100..114` (= 0x464..0x472)

`OLSSC_*` values are zero-based sequential positions in the
`olssc_tag` enum, e.g. `OLSSC_NUMCHANNELS=7`,
`OLSSC_SUP_CONTINUOUS=35`, `OLSSC_SUP_SINGLEVALUE=36`,
`OLSSCE_MAXTHROUGHPUT=61`, `OLSSC_RETURNS_FLOATS=116`,
`OLSSC_SUP_MULTISENSOR=143` — count from the OLDADEFS.H enum
declaration, never guess.

Verification status (per-row in [docs/decisions.md](decisions.md)) bench-confirms
each value family against `olDaGetSSCaps` / `olDaSetDataFlow` /
`olDaSetWrapMode` readbacks. Header sources:

- `OLDADEFS.H` — the bulk of the constants, including `OL_DF_*`,
  `OL_TRG_*`, `OL_CLK_*`, `OLSS_*`, `OLSSC_*`, and the `OLDA_WM_*`
  notification window-message codes.
- `Oldacfg.h` — defines `SS_STATES` (1000–1009) plus the
  `DEVICE_CFG`/`DEVICE_CFG_EX` structs used by olDaConfig internally.
- `OLERRORS.H` — the error code constants (named `OL*` — e.g.,
  `OLNOERROR`, `OLBADCAP`, `OLBADELEMENT`).
- `OLMEM.H` — `olDm*` prototypes and memory-manager constants.
- `OLDAAPI.H` — `olDa*` prototypes and `OL_ENUM_*` enumeration IDs
  (100–106). The `OLNOTIFYPROC` typedef lives here too, but we do **not**
  bind `olDaSetNotificationProcedure` — buffer-done events arrive via
  `olDaSetWndHandle` + a hidden message window instead (§12.3.2).

### 11.5 Callbacks (`capi/callbacks.py` + `backend/_message_window.py`)

`capi/callbacks.py` is mechanism-agnostic: it exports `SdkEventKind`
(the eleven `OLDA_WM_*` message kinds) and `event_kind_from_message`,
which the bridge uses to demultiplex an incoming `message_id`. It does
**not** define a SDK-callback function pointer — buffer-done events
arrive as Win32 window messages, not as a registered `OLNOTIFYPROC`.

The function pointer that matters is the **`WNDPROC`**, and it lives in
`backend/_message_window.py` (the single home of all `user32` /
`kernel32` calls — see §12.3.2):

```python
_LRESULT = ctypes.c_ssize_t          # LRESULT — pointer-sized signed
_WNDPROCTYPE = ctypes.WINFUNCTYPE(
    _LRESULT,                        # return: LRESULT (DefWindowProcA result)
    wintypes.HWND,                   # hWnd — which message window
    ctypes.c_uint,                   # uMsg — OLDA_WM_BUFFER_DONE etc.
    wintypes.WPARAM,                 # wParam — HDASS for buffer-done; pointer-sized
    wintypes.LPARAM,                 # lParam — message-specific payload; pointer-sized
)
```

`WINFUNCTYPE` (not `CFUNCTYPE`) because the Win32 calling convention is
stdcall. A single process-wide `_WNDPROC` is pinned and shared across
all message windows; it dispatches by `hWnd` to the registered Python
callback, then returns `DefWindowProcA` for everything else.

`wintypes.WPARAM` / `wintypes.LPARAM` (not `c_uint` / `c_long`) because
on 64-bit Windows these types are `UINT_PTR` / `LONG_PTR` — both
pointer-sized (64-bit), whereas `c_uint` / `c_long` are 32-bit. The
practical failure mode on x64 is that the WNDPROC receives truncated
values with garbage in the high 32 bits — a `switch (uMsg)` over
`OLDA_WM_*` constants will branch unpredictably when wParam holds an
HDASS-typed value that's been truncated to 32 bits. Binding-test
assertion: "WPARAM/LPARAM round-trip correct values on 64-bit Python."
The x86 stdcall case (which we don't target) would additionally cause
caller/callee stack-cleanup mismatch; on x64 the caller cleans the
stack, so the failure is bad-data, not corrupted stack.

### 11.5a Optional SDK functions (absent in some builds)

The DataAcq SDK ships slightly different function sets across
versions. Bench-confirmed missing on V7.0.0.7 (DT9805/DT9806):

- `olDaSetStopOnError(HDASS, BOOL)` — informational only at the
  dtollib layer; the recorder's `ErrorPolicy` is the authoritative
  source of stop-on-overrun behaviour. `OpenLayersApi.set_stop_on_error`
  is a no-op when the symbol is absent.
- `olDaGetSSState(HDASS, ULNG*)` — would return one of the
  `SS_STATES` enum codes (`OL_STATE_DORMANT=1000`,
  `OL_STATE_RUNNING=1005`, ...). `DataAcqBackend.get_state`
  derives state from `olDaIsRunning` + locally-tracked transitions
  when the SDK symbol is absent. The fake backend models the same
  derivation so the seam is covered by the §22 regression suite.

`prototypes.py` carries an `OPTIONAL_OLDAAPI_FUNCTIONS` tuple
documenting which symbols are bind-on-presence; new SDK revs that add
or remove functions update this tuple in the same commit that updates
[docs/decisions.md](decisions.md).

### 11.6 Conversion (`capi/conversion.py`)

At the C boundary, samples come in two shapes depending on the device:

- **Int devices** (most older boards): raw counts; needs range / gain /
  resolution / encoding to convert to volts. The wrapper uses a
  vectorised NumPy formula on whole blocks (`olDaCodeToVolts` is correct
  but per-sample — too slow at 50 kS/s).
- **Float devices** (declared via `OLSSC_RETURNS_FLOATS`): samples are
  already engineering units (volts for AI voltage, °C for TC, etc.).
  Skip conversion entirely.

```python
def detect_returns_floats(dll: ctypes.WinDLL, hdass: HDASS) -> bool:
    """Branch the buffer pipeline at backend construction, not per-block."""

def codes_to_volts_vectorised(
    codes: np.ndarray,        # shape (n_channels, n_samples), dtype int16/int32
    *,
    ranges: Sequence[tuple[float, float]],   # per channel
    gains: Sequence[float],                  # per channel
    resolution_bits: int,
    encoding: Encoding,
) -> np.ndarray:                              # shape (n_channels, n_samples), float64
    """Pure-NumPy whole-block conversion. olDaCodeToVolts is the oracle
    used in unit tests to validate the formula on representative codes."""


def deinterleave_cjc(
    raw: np.ndarray,          # shape (n_channels * 2 * n_samples,) — flat HBUF view
    *,
    n_channels: int,
    n_samples: int,
) -> tuple[np.ndarray, np.ndarray]:           # (measurement, cjc); each (n_channels, n_samples)
    """Split a CJC-interleaved buffer into measurement + CJC streams.

    When olDaSetReturnCjcTemperatureInStream is enabled, every scan
    contains TWO values per channel: [ch0_value, ch0_cjc, ch1_value,
    ch1_cjc, ...]. The raw HBUF therefore has n_channels * 2 * n_samples
    elements; the deinterleaver reshapes it into (n_samples, n_channels, 2),
    then splits axis -1 into measurement and CJC, and transposes back to
    (n_channels, n_samples). See §13.2 for capability gating."""


def detect_thermocouple_sentinel(
    values: np.ndarray,       # float64 from olDaGetSingleFloat / float buffer
    *,
    tc_type: ThermocoupleType,
) -> np.ndarray:                              # int8 mask of SensorStatus ordinals
    """Recognise SDK sentinel floats and map them to SensorStatus values.

    The SDK reports SENSOR_IS_OPEN, TEMP_OUT_OF_RANGE_LOW, and
    TEMP_OUT_OF_RANGE_HIGH as documented magic floats. The backend
    calls this at the C boundary so sentinels never reach the public
    DaqReading/DaqBlock as plausible-looking temperatures (see §13.1)."""
```

### 11.7 Why hand-transcribe instead of auto-generating

`cffi` API mode would parse `OLDAAPI.H` / `OLMEM.H` automatically, but it
would force every user to have the SDK headers installed at install time.
With `ctypes` we only need the DLLs at runtime; the binding is plain
`.py` files that ship in the wheel. The tradeoff is real but small: ~80
function signatures and ~200 constants are easy to maintain and changes
to the SDK are extremely rare (the 25th edition of the manual is from
2015).

**Hand-curated is the source of truth.** A contributor-facing helper
script — `scripts/gen_openlayers.py` — parses installed headers (when
available) and prints diffs against the transcribed prototypes and
constants. CI runs the diff as a *non-blocking* check when SDK headers
are mounted on a self-hosted Windows runner; the output is informational
and lands in the action log so we notice when the SDK adds a function we
might want to bind. We do **not** auto-overwrite the hand-curated files
— curation buys us docstrings, header attribution, and the ability to
ignore deprecated symbols, all of which a generator would erase.

---

## 12. Async Strategy

The SDK is synchronous. The async API does not pretend otherwise.

### 12.0 Threading vocabulary

Three threads matter in this design. The rest of §12 (and the
ordering-invariants table in §12.3.2, and the RawCountsSink discussion
in §15.2) uses these names consistently:

- **Asyncio thread.** Runs the event loop. All user `await` points, all
  AnyIO memory-object-stream sends, all `record() async for` iteration
  happen here. Never makes blocking SDK calls directly — always via
  `anyio.to_thread.run_sync`.
- **Pump thread.** A dedicated `threading.Thread` (one per HDASS) that
  *creates and owns* the hidden `HWND_MESSAGE` window and runs
  `GetMessageA` / `DispatchMessageA`. The SDK posts `OLDA_WM_*` window
  messages to that window; `DispatchMessageA` invokes the pinned
  `WNDPROC` *on this thread*. (This replaces the old "driver thread" that
  a registered `OLNOTIFYPROC` would have run on — that callback never
  fires on V7.0.0.7, §12.3.2.) No event loop, no AnyIO context, no
  asyncio primitives. The only safe operation here is a
  `queue.SimpleQueue.put_nowait` of a tiny event tuple (message ID +
  monotonic timestamp). Allocating arrays, calling `olDaGetBuffer`, or
  writing files in this thread is a bug.
- **Drainer thread (a.k.a. worker thread).** A long-lived `anyio`
  worker spun up by `record()`. Pulls events from the pump-thread
  queue, calls `olDaGetBuffer` / `olDmGetBufferPtr`, copies data into a
  `numpy.ndarray`, requeues the `HBUF` via `olDaPutBuffer`, optionally
  writes raw counts to disk via `RawCountsSink`, and forwards the
  `DaqBlock` into the user-facing async stream. This is where every
  unit of work between "buffer-done callback fired" and "DaqBlock is in
  the consumer's `async for`" lives.

The cardinal rule: **the pump-thread WNDPROC only signals; the
drainer thread does the work.** Any time §12, §14, or §15.2 says
"writes from the drainer thread" or "from the callback," it means
*those* threads specifically — not the asyncio thread.

### 12.1 Worker threads at coarse boundaries

Good:

```python
block_ndarray = await anyio.to_thread.run_sync(
    backend.read_block,        # internally: pulls one HBUF from done queue, copies
    hdass,
    samples_per_channel,
    timeout,
)
```

Bad:

```python
for i in range(samples_per_channel):
    sample = await anyio.to_thread.run_sync(backend.get_single_value, hdass, ch, gain)
```

### 12.2 Session locking

All HDASS operations serialise on a per-session `anyio.Lock`. The SDK is
thread-safe per-HDRVR but **not** per-HDASS. The lock is the seam.

### 12.3 Two paths for continuous acquisition

Same dispatch as `nidaqlib`:

| `TaskSpec.timing`                                | Recorder         | Emits         |
|--------------------------------------------------|------------------|---------------|
| `None` / `DataFlow.ON_DEMAND`             | `record_polled`  | `DaqReading`  |
| `DataFlow.CONTINUOUS` / `FINITE`          | `record`         | `DaqBlock`    |

#### 12.3.1 Software-timed (low-rate)

For thermocouple acquisition ≤100 Hz typical of the DT9805 multi-sensor
inputs, software polling against the SDK's single-value path is fine.
Same absolute-target scheduling pattern as
`alicatlib.streaming.recorder` — `compute target[n] = start + n * dt;
anyio.sleep_until(target[n]); to_thread.run_sync(session.poll); emit`.

#### 12.3.2 Hardware-timed (high-rate) — the bridge

This is the §11.3.2 analogue. The DataAcq SDK only delivers buffer-done
events via:

1. **Window messages** (`olDaSetWndHandle`) — needs a Win32 message
   pump.
2. **Notification procedure** (`olDaSetNotificationProcedure`) — C
   callback invoked on a driver-managed thread.

**We use option 1 (window-handle + message pump) — implemented in
`src/`.** Bench-verified against SDK V7.0.0.7 on DT9805/DT9806
(2026-05-28, [docs/decisions.md](decisions.md)): the SDK accepts
`olDaSetNotificationProcedure` with `ec=0` but the ``OLNOTIFYPROC``
callback **never fires** — neither when the calling thread runs a
`GetMessage` pump nor when it blocks idle. Option 2 is therefore not
bound at all. Every SDK sample under
`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Examples` uses
`olDaSetWndHandle` even in console apps (`DtConsole.cpp` creates a
hidden `HWND_MESSAGE` window specifically for this).

The mechanism also has two companion requirements, both bench-proven and
implemented: `olDaConfig` is called **twice** (once after channel/timing
setup, then again after `olDaSetWndHandle` — the second config wires the
HWND into the SDK's buffer-rotation state machine; modeled as the
backend's `commit()` then `arm()`), and `olDaSetDmaUsage(min(1, N))` is
called **even when `NUMDMACHANS == 0`**.

The bridge:

```text
SDK driver ── PostMessage(OLDA_WM_*) ──> hidden HWND_MESSAGE window
                                                  │
                                  dedicated message-pump thread:
                                  while GetMessage / DispatchMessage:
                                      WNDPROC(uiMsg) → queue.SimpleQueue
                                                  │
                                       anyio.to_thread.run_sync(q.get)
                                                  │
                                       anyio.MemoryObjectStream
                                                  │
                                       async for block in stream:
```

The dtollib API surface (`register_notification` /
`unregister_notification` on `DataAcqBackend`, the `SdkEventKind`
enum) stays unchanged, and so does the `_callback_bridge.py` contract
(`callback(msg_id, wparam, lparam) -> int`). All Win32 machinery lives
in **`backend/_message_window.py`** (the sole home of `user32` /
`kernel32` calls) and is driven from `DataAcqBackend.register_notification`
— `_callback_bridge.py` stays **mechanism-agnostic** and `FakeDtolBackend`
is untouched (it already matches the seam). A per-HDASS `MessageWindow`
owns the hidden window + pump thread, spawned at register time and
joined (`PostThreadMessageA` `WM_QUIT` → join → `DestroyWindow`) at
unregister.

Same shape as the NI bridge, with three differences:

1. **The WNDPROC only signals "buffer done"** — it does NOT carry the
   data. We must call `olDaGetBuffer` from a worker thread to pull the
   HBUF, then `olDmGetBufferPtr` to get the data, then `olDaPutBuffer`
   to recycle it. The WNDPROC hands the message kind to the queue; the
   drainer does the buffer-pool work on a worker thread.
2. **The pool must be primed** — at least 3 buffers, ideally 4–8. With
   only 1 or 2, an OVERRUN is guaranteed because the SDK has nothing to
   fill while the consumer is draining. `BufferPlan.buffers` (§8.7a)
   defaults to 4 and enforces a minimum of 3 (matches the SDK's own
   continuous-AI sample at `dasdk_digest.md:906-918`, which allocates 3).
3. **Ten distinct event types** can arrive on the same callback — see the
   full `SdkEventKind` enum below. Only `BUFFER_DONE` is the happy path;
   `OVERRUN_ERROR` / `UNDERRUN_ERROR` / `TRIGGER_ERROR` raise (or, under
   `ErrorPolicy.RETURN`, emit error blocks); `BUFFER_REUSED` is logged
   loudly because it means data was overwritten in `WrapMode.MULTIPLE`;
   `QUEUE_DONE` / `QUEUE_STOPPED` / `IO_COMPLETE` signal end-of-run;
   `PRETRIGGER_BUFFER_DONE` / `EVENT_DONE` / `MEASURE_DONE` are
   subsystem-specific and routed to dedicated handlers.

```python
class SdkEventKind(StrEnum):
    BUFFER_DONE             = "buffer_done"             # OLDA_WM_BUFFER_DONE
    PRETRIGGER_BUFFER_DONE  = "pretrigger_buffer_done"  # OLDA_WM_PRETRIGGER_BUFFER_DONE
    BUFFER_REUSED           = "buffer_reused"           # OLDA_WM_BUFFER_REUSED
    QUEUE_DONE              = "queue_done"              # OLDA_WM_QUEUE_DONE
    QUEUE_STOPPED           = "queue_stopped"           # OLDA_WM_QUEUE_STOPPED
    IO_COMPLETE             = "io_complete"             # OLDA_WM_IO_COMPLETE
    EVENT_DONE              = "event_done"              # OLDA_WM_EVENT_DONE
    MEASURE_DONE            = "measure_done"            # OLDA_WM_MEASURE_DONE
    TRIGGER_ERROR           = "trigger_error"           # OLDA_WM_TRIGGER_ERROR
    OVERRUN_ERROR           = "overrun_error"           # OLDA_WM_OVERRUN_ERROR
    UNDERRUN_ERROR          = "underrun_error"          # OLDA_WM_UNDERRUN_ERROR (D/A path)
```

#### Startup ordering (mandatory)

Per the SDK sample code (`ThermoADC.C`, `IepContAdc.c`,
`DtConsole.cpp`), the canonical startup sequence has TWO calls to
`olDaConfig` and requires `olDaSetDmaUsage` even when DMA is
unsupported. Bench-verified 2026-05-28: omitting either trips the
"buffers stuck in Inprocess forever" failure. The `_callback_bridge`
enforces this six-step prepare/commit split:

```python
# 1. Allocate HDASS. Configure channels/clock/trigger/wrap mode/etc.
#    set_dma_usage(min(1, NUMDMACHANS)) is MANDATORY even when
#    NUMDMACHANS == 0. Stops short of olDaConfig.
#    Also creates the BufferPool from spec.buffers.
await session.prepare()

# 2. First olDaConfig — commits the channel/clock/trigger state.
await session.commit_initial()

# 3. Queue all pool buffers to the SDK Ready queue.
session.pool.queue_all()                                      # olDaPutBuffer × N

# 4. Create the hidden HWND_MESSAGE window and bind it via
#    olDaSetWndHandle.  ``register_notification`` does both, plus
#    starts the message-pump thread.
handle = backend.register_notification(hdass, _on_notify)

# 5. Second olDaConfig — re-arms the SDK's internal buffer-rotation
#    state machine with the window-handle in place. Without this
#    second call, OLDA_WM_BUFFER_DONE never fires.
await session.commit()

# 6. olDaStart. First message lands shortly after.
await session.start()
```

`record(session)` validates `session.is_prepared and not session.is_committed`,
then runs steps 2–5 internally. The `BufferPlan` on `TaskSpec.buffers` is
the only place buffer-pool sizing is configured — recorder calls do not
take a buffer-count parameter.

Why this ordering matters: with notification + Ready buffers in place
before `olDaConfig`, the SDK transitions cleanly from configured → ready
→ running on `olDaStart`, and the first `OLDA_WM_BUFFER_DONE` lands on a
fully-initialised callback bridge. Inverting the order (config first,
then register/queue) is permissive on some devices but races on others
— the digest text walks through it that way, but the sample code does
not, and SDK sample code is the more reliable signal.

#### Shutdown ordering (mandatory)

`anyio.to_thread.run_sync` does **not** propagate cancellation into the
worker thread. The drainer awaiting `queue.get()` is not interrupted by
recorder exit — same hazard as `nidaqlib`. The recorder's `__aexit__`
runs (shielded from cancellation):

```python
# 1. Stop the SDK subsystem. After this, in-flight callbacks have completed
#    and no new ones fire. (olDaStop is the orderly path; for emergency
#    shutdown, olDaAbort is the same place in the ordering.)
await session.stop()

# 2. Unregister the notification. SDK accepts this once stopped.
backend.unregister_notification(hdass, handle)

# 3. Wake the drainer with a sentinel.
chunk_q.put_nowait(_SENTINEL)

# 4. Wait for the drainer to exit cleanly.
await drain_done.wait()

# 5. Flush buffers, free pool, release HDASS, terminate HDRVR.
#    Runs in DtolSession.close() inside open_device's __aexit__.
```

Ordering invariants:

| Ordering requirement       | Why |
|----------------------------|-----|
| Register BEFORE commit     | SDK sample code at `dasdk_digest.md:906-918` registers notification (and queues buffers) before `olDaConfig`. Registering after `olDaConfig` races the first `OLDA_WM_BUFFER_DONE` against an uninitialised callback bridge. The fake backend enforces this. |
| Queue BEFORE commit        | Same SDK sample code shows `olDaPutBuffer × N` before `olDaConfig`. Without buffers on the Ready queue at commit time, `olDaStart` has nothing to fill and the first callback either never fires or fires with a sentinel HBUF. |
| Commit BEFORE start        | `olDaStart` requires `olDaConfig` to have run; SDK returns a state-machine error otherwise. |
| Stop BEFORE unregister     | Otherwise an in-flight callback can race with the unregister and fire after the wrapper is gone — segfault risk because the CFUNCTYPE reference may be GC'd. |
| Unregister BEFORE sentinel | After unregister the SDK cannot fire a callback that races with the sentinel and orphans an HBUF behind it in the queue. |
| Sentinel BEFORE drain-wait | The drainer is parked in `chunk_q.get()`; only the sentinel wakes it. |
| Drain-wait BEFORE close    | The drainer holds the strong reference to the HBUF; freeing while it's mid-iteration is unsafe. |

#### Rules for the callback body

- **No `anyio.*` calls from the callback.** It runs on the pump thread
  (which dispatches the WNDPROC) with no event loop context. Use
  `queue.SimpleQueue` (thread-safe, no asyncio dependency).
- **The callback must be short.** Don't call `olDaGetBuffer` from inside
  the callback — defer to the drainer on a worker thread. The callback
  just posts the message ID.
- **Keep a strong reference to the CFUNCTYPE wrapper** for the lifetime
  of the subsystem. The SDK stores a raw C function pointer; Python GC
  will silently break the seam otherwise. The backend keeps it in
  `self._notification_wrappers` keyed by `id(hdass)`.

---

## 13. Acquisition Modes

### 13.1 Single-value (`DataFlow.SINGLE_VALUE`)

Setup:
```text
olDaSetDataFlow(hdass, OL_DF_SINGLEVALUE)
olDaSetChannelType(...)  # per channel
olDaSetChannelRange(...)
olDaConfig(hdass)
```

Per call — the backend branches on `OLSSC_RETURNS_FLOATS` cached at
backend construction:

```text
# Float-returning device (DT9805/DT9806 multi-sensor inputs):
olDaGetSingleFloat(hdass, &value, channel, gain)
# → value is already in engineering units (volts, °C, etc.); no conversion.

# Int-returning device (older voltage-only boards):
olDaGetSingleValue(hdass, &code, channel, gain)
# → convert via capi.conversion.codes_to_volts_vectorised on the scalar.
```

For simultaneous-sampling subsystems (DT9805 declares
`OLSSC_SUP_SIMULTANEOUS_SH`), `olDaGetSingleValues` /
`olDaGetSingleFloats` reads all channels in one call. The backend
prefers these when the cap is present.

#### Thermocouple linearisation: hardware-side vs application-side

We honour two linearisation paths, selected by capability:

- **Application-linearised** (DT9805/DT9806 — the v0.1 happy path,
  bench-verified 2026-05-28): the device returns raw codes
  (`OLSSC_RETURNS_FLOATS = 0`) and exposes only a CJC channel
  (`OLSSC_SUP_THERMOCOUPLES = 1`). The wrapper reads the differential
  thermo-emf plus the CJC sensor on channel 0 and applies NIST ITS-90
  polynomials (`utils.convert_volts_to_temperature`, Types K and J today).
  See §8.5a for the full call sequence. There is **no**
  `OLSSC_SUP_LINEARIZE_TC` capability in this SDK build; the path is gated
  on `supports_thermocouples and not returns_floats`.
- **Hardware-linearised** (hypothetical firmware-linearising boards): the
  device reports temperature directly via `olDaGetSingleFloat`, gated on
  `OLSSC_RETURNS_FLOATS = 1`. No currently-owned board uses this path
  (the DT9805/06 do not), but it is kept for generality and covered in
  tests via a synthetic capability set (`make_firmware_tc_capabilities`).

A `ThermocoupleInput` on a subsystem that is neither
(`not returns_floats and not supports_thermocouples`) raises
`DtolCapabilityError` at configuration time, as does an unimplemented TC
type (anything but K/J today) on the application-side path.

#### Thermocouple sentinel values — preserve, never coerce

DT-Open Layers thermocouple readings can report three sentinel
conditions in place of a temperature value:

- `SENSOR_IS_OPEN` — TC wire is broken / not connected.
- `TEMP_OUT_OF_RANGE_LOW` — sensor below the TC type's valid range.
- `TEMP_OUT_OF_RANGE_HIGH` — sensor above the TC type's valid range.

These are **data conditions, not numeric measurements.** The wrapper
must NOT coerce them into plausible-looking temperatures (e.g.
`SENSOR_IS_OPEN` rendered as `0.0 °C`). Instead:

```python
class SensorStatus(StrEnum):
    OK                    = "ok"
    SENSOR_OPEN           = "sensor_open"
    TEMP_OUT_OF_RANGE_LOW  = "temp_out_of_range_low"
    TEMP_OUT_OF_RANGE_HIGH = "temp_out_of_range_high"
```

Single-value path (`DaqReading`): the `sensor_status` overlay
(`Mapping[str, SensorStatus]`, keyed by channel display name) records
the condition; the corresponding entry in `values` is `float("nan")`
so a downstream consumer that ignores `sensor_status` still sees the
gap rather than a fake number.

Continuous path (`DaqBlock`): `sensor_status` is `Mapping[str, np.ndarray]`
— one `int8` mask per channel, same length as `samples_per_channel`,
encoded with `SensorStatus` ordinals. The matching positions in `data`
are NaN-filled.

Sinks (CSV, JSONL, SQLite, Parquet, Postgres) propagate the sentinel
mask alongside the value column. `RawCountsSink` writes the raw int16
codes regardless; the sentinel decoding is part of replay-time
conversion against the channel's TC type.

Detected via `olDaGetSingleFloat` returning the SDK's documented
sentinel float values; the backend recognises them at the boundary and
populates `sensor_status` rather than passing the magic float through.

### 13.2 Continuous (`DataFlow.CONTINUOUS`)

Setup:
```text
olDaSetDataFlow(hdass, OL_DF_CONTINUOUS)
olDaSetChannelListSize / olDaSetChannelListEntry  # one per channel
olDaSetClockSource(OL_CLK_INTERNAL)
olDaSetClockFrequency(rate_hz)
olDaSetTrigger(OL_TRG_SOFT)                        # or external/threshold per spec
olDaSetWrapMode(OL_WRP_MULTIPLE)                   # continuous reuse
olDaSetDmaUsage(min(1, NUMDMACHANS))               # MANDATORY even when NUMDMACHANS=0
olDaConfig(hdass)                                  # first config
# ... allocate + queue buffers, register notification (sets WndHandle) ...
olDaConfig(hdass)                                  # second config — re-arms with WndHandle
```

Then the §12.3.2 bridge runs (with the prepare/commit split — first
config commits channel/timing state; buffers + window-handle land;
second config re-arms with the WndHandle so OLDA_WM_BUFFER_DONE
actually fires). Both config calls AND `olDaSetDmaUsage` are
bench-verified mandatory on SDK V7.0.0.7 (DT9805/DT9806) — see
`ThermoADC.C` and `IepContAdc.c` for the canonical order.

#### CJC-interleaved buffer shape (continuous TC path)

When `olDaSetReturnCjcTemperatureInStream(hdass, TRUE)` is enabled on a
subsystem that supports `OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM`, every TC
channel in the channel/gain list produces TWO samples per scan: the
measurement value followed by the CJC value. The SDK buffer therefore
has shape:

```text
HBUF data layout (interleaved CJC):
  n_channels * 2 * samples_per_buffer  (in elements)

Per-scan ordering (n_channels=2, both TC with CJC interleave):
  [ch0_value, ch0_cjc, ch1_value, ch1_cjc, ch0_value, ch0_cjc, ...]
```

The deinterleaver in `capi/conversion.py` must split the measurement
stream from the CJC stream BEFORE constructing the public `DaqBlock`.
The resulting `DaqBlock.data` has shape `(n_channels, samples_per_buffer)`
in measurement values; CJC values are attached as a sibling field
(`DaqBlock.cjc_data: np.ndarray | None`, shape `(n_channels,
samples_per_buffer)`) for diagnostics and replay-time validation.

Forgetting the ×2 width is a class of bug that produces alternating
"value, cjc, value, cjc" stripes in the recorded data — sample
ordering looks plausible per-channel but every reading is wrong by the
CJC delta. The deinterleaver has a unit test that asserts a synthetic
buffer with known interleave pattern round-trips correctly.

Capability gate: the wrapper enables interleaved CJC only when both
`OLSSC_SUP_TEMPERATURE_DATA_IN_STREAM` and
`OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM` are present in the subsystem's
`CapabilitySet`. Without the latter, CJC is read out-of-band via
`olDaGetCjcTemperature` and applied at conversion time.

### 13.3 Finite

`FINITE` is `CONTINUOUS` with `OL_WRP_NONE` (linear, no reuse) and a
`samples_per_channel` ceiling on the `TaskSpec.timing`. The recorder
counts samples emitted and calls `session.stop()` when the ceiling is
reached, then drains the done queue.

### 13.4 Triggered scan (post-v0.1)

`RetriggerSpec` enables `olDaSetTriggeredScanUsage(1)` and configures
mode + frequency + source. The SDK doc recommends `OL_RETRIG_EXTRA` over
`OL_RETRIG_SCANPERTRIG` for jitter-free retrigger — the wrapper defaults
to EXTRA when both are supported.

### 13.5 Simultaneous start (post-v0.1)

For multi-subsystem coordination (e.g., AI + counter), `DtolManager`
exposes `start_synchronized(names)` which runs the SDK's three-step
hardware-coordinated start:

```text
olDaGetSSList(hdrv, &hsslist)
for hdass in subsystems:
    olDaPutDassToSSList(hsslist, hdass)
olDaSimultaneousPreStart(hsslist)         # arms all subsystems, releases on next trigger
olDaSimultaneousStart(hsslist)            # fires the shared start trigger
# ... acquisition runs in each session normally ...
olDaReleaseSSList(hsslist)                # in __aexit__
```

The pre-start step is mandatory and easy to miss — it arms every
subsystem on the list to wait for the simultaneous trigger; without it,
each `olDaStart` would happen independently. Defers to v0.2 / v0.3 since
it requires the DT9806 D/A or C/T subsystem.

### 13.6 CapabilitySet — runtime capability snapshot

Capabilities are queried at session construction (after `olDaGetDASS`,
before any configuration) and cached on the session. The model is
deliberately dict-shaped so new SDK caps don't require model changes:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilitySet:
    int_caps: Mapping[str, int]               # OLSSC_* boolean / count caps
    float_caps: Mapping[str, float]           # OLSSCE_* throughput / frequency caps
    enum_values: Mapping[str, tuple[int | float, ...]]
                                              # discrete values per cap (ranges, gains, ...)
    channel_caps: Mapping[int, Mapping[str, int]]
                                              # per-channel cap overrides (some channels
                                              #   support TC, others don't, on some boards)

    # Well-known accessor helpers — single source of truth for cap name spelling.
    def supports_continuous(self) -> bool:        return bool(self.int_caps.get("OLSSC_SUP_CONTINUOUS", 0))
    def supports_simultaneous(self) -> bool:      return bool(self.int_caps.get("OLSSC_SUP_SIMULTANEOUS_SH", 0))
    def returns_floats(self) -> bool:             return bool(self.int_caps.get("OLSSC_RETURNS_FLOATS", 0))
    def hardware_linearises_tc(self) -> bool:     return bool(self.int_caps.get("OLSSC_SUP_LINEARIZE_TC", 0))
    def supports_inprocess_flush(self) -> bool:   return bool(self.int_caps.get("OLSSC_SUP_INPROCESSFLUSH", 0))
    def supports_multi_sensor(self) -> bool:      return bool(self.int_caps.get("OLSSC_SUP_MULTI_SENSOR", 0))
    def supports_synchronous_buffer_done(self) -> bool:
        return bool(self.int_caps.get("OLSSC_SUP_SYNCBUFFERDONE", 0))
    def max_throughput_hz(self) -> float:         return self.float_caps.get("OLSSCE_MAX_THROUGHPUT", float("inf"))
    def available_ranges(self) -> tuple[tuple[float, float], ...]:
        return tuple(self.enum_values.get("OL_ENUM_RANGES", ()))    # type: ignore
    def channel_supports_tc(self, ch: int) -> bool:
        return bool(self.channel_caps.get(ch, {}).get("OLSSC_SUP_LINEARIZE_TC", 0))
    def io_type_for_channel(self, ch: int) -> IOType:
        """Per-channel IOType (§8.15). VOLTAGE_IN by default; MULTI_SENSOR
        on DT9805-style multi-sensor channels. Drives the channel-spec ↔
        IOType validation (cannot configure a VOLTAGE_IN-only channel as
        a thermocouple)."""
        raw = self.channel_caps.get(ch, {}).get("OLSSC_IO_TYPE", 0)
        return _IO_TYPE_FROM_OLSSC.get(raw, IOType.VOLTAGE_IN)
```

Population is via four SDK calls, all wrapped by `query_capabilities`:

| SDK call                  | Populates              |
|---------------------------|------------------------|
| `olDaGetSSCaps`           | `int_caps`             |
| `olDaGetSSCapsEx`         | `float_caps`           |
| `olDaEnumSSCaps`          | `enum_values`          |
| `olDaEnumChannelCaps`     | `channel_caps`         |

Validation (during `session.configure()`) reads the cached snapshot —
zero extra SDK calls. If the user requests a config combination not
supported by the cap snapshot, the wrapper raises
`DtolCapabilityError` *before* `olDaConfig` runs. The SDK remains the
final authority — if `olDaConfig` rejects despite a passing preflight,
the SDK error is wrapped and surfaced with full context.

---

## 14. Recorder Design

### 14.1 Recorder dispatch

```python
@asynccontextmanager
async def record_polled(
    source: DtolSession | DtolManager,
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
) -> AsyncIterator[tuple[AsyncIterator[DaqReading], AcquisitionSummary]]:
    """Software-timed scalar polling. Mirrors alicatlib's record() exactly."""

@asynccontextmanager
async def record(
    source: DtolSession,
    *,
    timeout: float = 10.0,
    stream_buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
) -> AsyncIterator[tuple[AsyncIterator[DaqBlock], AcquisitionSummary]]:
    """Hardware-clocked block acquisition. Drives the §12.3.2 bridge.

    Chunk size and pool size come from `source.spec.buffers` (BufferPlan).
    `stream_buffer_size` is the AnyIO MemoryObjectStream size — distinct
    from the SDK buffer pool; this is the async-side back-pressure window.
    """
```

`AcquisitionSummary` mirrors `nidaqlib.AcquisitionSummary`:

```python
@dataclass(frozen=True, slots=True)
class AcquisitionSummary:
    blocks_emitted: int               # readings_emitted for record_polled
    blocks_dropped: int               # > 0 only under DROP_* overflow
    errors_observed: int              # bumped on every wrapped SDK error
    overruns_observed: int            # OLDA_WM_OVERRUN_ERROR count
    started_at: datetime
    finished_at: datetime
```

The new field over `nidaqlib` is `overruns_observed` — the SDK has a
hard distinction between "consumer dropped a block" (`DROP_OLDEST` policy
applied) and "the SDK overran its own queue" (OLDA_WM_OVERRUN_ERROR).
Different root causes, so we count them separately.

### 14.2 Recorder invariants

Same as `nidaqlib` §13.2:

- Task started on recorder entry if not already running.
- Task stopped on exit if (and only if) the recorder started it.
- A producer task reads blocks (or, for the §12.3.2 path, drains the
  notification queue).
- Backpressure policy is explicit.
- Each block/reading includes timing metadata.
- The producer never silently drops blocks unless configured to do so.

Plus one dtollib-specific invariant:

- **`RawLogging` is detected at recorder entry.** If `RawLogging` is
  configured, the `RawCountsSink` is attached automatically as a passive
  observer; the recorder still emits `DaqBlock`s for the user-facing
  stream. The raw-counts path runs from the drainer thread without
  consumer back-pressure, so even if the async consumer blocks, the
  raw-counts file keeps growing.

### 14.3 Error policy semantics

There are **two independent error knobs** that govern continuous-mode
error handling. They sit at different layers and answer different
questions:

| Knob                       | Layer                | Question it answers                                  |
|----------------------------|----------------------|------------------------------------------------------|
| `TaskSpec.stop_on_error`   | SDK (`olDaSetStopOnError`) | On `OVERRUN_ERROR` / `UNDERRUN_ERROR`, does the **subsystem itself** halt, or keep filling buffers? |
| `record(... error_policy)` | Wrapper recorder loop      | When a wrapped `DtolError` reaches the **Python producer loop**, does the wrapper raise, emit an error-record, or log-and-continue?           |

Borrowed from .NET's `AnalogInputSubsystem.StopOnError` (UMOpenLayers.md:917)
vs. the wrapper-side `ErrorPolicy`. Conflating them is the classic
"my recorder caught the OVERRUN but the SDK kept producing buffers anyway,
so I missed *which* buffer was bad" footgun.

Recommended pairings:

| Use case | `stop_on_error` | `error_policy` |
|---|---|---|
| Unattended automation, errors are run-fatal | `True` (default) | `RAISE` (default) |
| Long-running soak, isolated OVERRUNs are tolerable | `False` | `LOG_AND_CONTINUE` |
| Downstream analysis pipeline wants gap markers in the data | `False` | `RETURN` |

The recorder's `error_policy` parameter governs what happens when a
wrapped SDK error reaches the producer loop. Three values, all from the
shared ecosystem enum (`ErrorPolicy.LOG_AND_CONTINUE` is the dtollib
addition):

```python
class ErrorPolicy(StrEnum):
    RAISE              = "raise"               # cancel task group, propagate
    RETURN             = "return"              # emit error record (.error set, .data zero-filled)
    LOG_AND_CONTINUE   = "log_and_continue"    # log at WARNING, drop the offending block, keep running
```

- **`RAISE`** (default) — cancel the recorder task group and propagate
  the typed `DtolError`. Correct for unattended scripts where any error
  is run-fatal.
- **`RETURN`** — emit a `DaqReading` / `DaqBlock` with `.error` set;
  `.data` is a zero-filled array of the expected shape; consumers MUST
  gate on `error is None` before using `data`. Correct for downstream
  pipelines that want to record gaps in the data stream alongside the
  data.
- **`LOG_AND_CONTINUE`** — log the error at WARNING and skip the
  offending block; no record is emitted for that interval. Correct for
  long-running unattended acquisition services where occasional
  transient errors (OVERRUN under brief consumer-side hiccups) should
  not bring the run down but also shouldn't pollute the recorded data
  with zero blocks.

Under `RAISE` and `LOG_AND_CONTINUE`, the `.error` field on emitted
records is always `None`. The `AcquisitionSummary.errors_observed`
counter increments under all three policies — silent loss is never the
answer; the summary is the record.

### 14.4 Overflow policies — consumer-side back-pressure

Separate concern from `ErrorPolicy`. Where `ErrorPolicy` governs what
to do when the **SDK** reports an error, `OverflowPolicy` governs what
to do when the **async send-stream** to the consumer is full:

```python
class OverflowPolicy(StrEnum):
    BLOCK        = "block"                # producer awaits consumer; preserves every block
    DROP_NEWEST  = "drop_newest"          # drop about-to-be-enqueued block; bounds latency
    DROP_OLDEST  = "drop_oldest"          # evict oldest queued block; keeps newest data
```

Defaults:

| Recorder        | Default       | Rationale |
|-----------------|---------------|-----------|
| `record_polled` | `BLOCK`       | Same as siblings. |
| `record`        | `DROP_OLDEST` | Same reason as `nidaqlib`: a blocked producer leaks into a buffer-pool exhaustion, which surfaces as OLDA_WM_OVERRUN_ERROR minutes later. `DROP_OLDEST` keeps the SDK queue moving. |

For high-rate durable logging, attach `RawCountsSink` in addition to the
streaming sink — raw-counts writes from the drainer thread and are not
subject to consumer back-pressure (this is the `RawLogging`-equivalent of
NI's TDMS).

---

## 15. Sink Design

### 15.1 Four Sink Protocols, two pipe drivers

Three Protocols port directly from `nidaqlib` §14.1 — `ReadingSink`,
`SampleSink`, `BlockSink` — driven by `pipe()` (for readings/samples)
and `pipe_blocks()` (for blocks). The fourth Protocol, `RawBlockSink`,
is dtollib-specific and exists because the raw-counts path carries a
fundamentally different payload (a `RawBuffer` of bytes from
`olDmGetBufferPtr` plus sidecar metadata) than the converted
`DaqBlock`. Typing them as distinct Protocols prevents accidental
miswiring — a `RawCountsSink` cannot satisfy a `BlockSink` parameter
slot, and a `ParquetSink` cannot satisfy a `RawBlockSink` slot.

```python
class BlockSink(Protocol):
    async def write(self, block: DaqBlock) -> None: ...

class RawBlockSink(Protocol):
    async def write_raw(self, raw: RawBuffer) -> None: ...
        # raw.payload is bytes from olDmGetBufferPtr — not unit-converted.
        # raw.header is the JSON header written once at sink-open time.
```

The fan-out inside `record()` checks each attached sink's Protocol at
attach time and routes accordingly. A sink may implement both
`BlockSink` and `RawBlockSink` (rare but legal — e.g., a debug sink
that records both the converted block and the raw bytes for offline
comparison).

Matrix of concrete sinks:

| Sink            | Reading | Sample | Block                                       | RawBlock |
|-----------------|:-:|:-:|:------:|:-:|
| `InMemorySink`  | ✓ | ✓ | ✓                                           | ✗ |
| `CsvSink`       | ✓ | ✓ | ✗ (opt-in via `accept_blocks=True`)         | ✗ |
| `JsonlSink`     | ✓ | ✓ | ✗ (same)                                    | ✗ |
| `SqliteSink`    | ✓ | ✓ | summary rows only                           | ✗ |
| `ParquetSink`   | ✓ | ✓ | ✓ (preferred; row groups per block)         | ✗ |
| `PostgresSink`  | ✓ | ✓ | summary rows only                           | ✗ |
| `RawCountsSink` | ✗ | ✗ | ✗ (deliberately — see §15.2)                | ✓ (DT-only) |

Refuse-blocks-by-default on row-oriented sinks is the same rule:
prevents accidental 1-GB CSVs at 10 kHz × 8 channels. The reason
`RawCountsSink` is `RawBlock`-only and not `Block`-also is symmetry:
forcing the user to think about which durable format they want
(volt-converted `DaqBlock` → `ParquetSink`, raw bytes →
`RawCountsSink`) at sink-construction time is the right place for that
decision.

### 15.2 RawCountsSink — the TDMS-equivalent

This is the one sink unique to `dtollib`. It exists because the SDK has
no driver-side logging (no analogue of NI's `task.in_stream.configure_logging`).

`RawCountsSink` writes the **raw int16/int32 buffer data** (not the
volt-converted floats) plus a one-time file header AND a per-chunk
record header to a `.dt-raw` file format defined by the package:

```text
file = file_header_len:uint32
     + file_header_json:bytes
     + (chunk_record)*

file_header_json = {
    "format_version": 2,                   # bumped from v1 — per-chunk framing
    "task_name": "...",
    "device": "DT9805(00)",
    "channels": [
        {"name": "...", "physical_channel": 0, "range": [-10.0, 10.0],
         "gain": 1.0, "encoding": "binary", "unit": "V",
         "tc_type": null}, ...
    ],
    "sample_rate_hz": 1000.0,
    "block_period_ns": 1000000,
    "resolution_bits": 16,
    "dtype": "int16",
    "interleaved_cjc": false,              # if true, every scan is n_channels*2 wide
    "task_started_at": "2026-05-27T12:34:56.789Z",
    "task_started_mono_ns": 123456789000,
    "sdk_version": "...",
    "dll_paths": {"oldaapi": "...", "olmem": "..."},
    "dtollib_version": "0.1.0",
    "metadata": {...},
}

chunk_record =
    chunk_header_len:uint32
  + chunk_header_json:bytes
  + chunk_payload:bytes

chunk_header_json = {
    "seq": 0,                              # 0-based monotonic per task
    "event_kind": "buffer_done",           # see SdkEventKind; usually buffer_done
    "first_sample_index": 0,               # cumulative offset since task_started_*
    "valid_samples": 1000,                 # may be < buffer capacity on final/partial
    "buffer_capacity": 1000,               # samples_per_channel the buffer was sized for
    "t_mono_ns": 123456789000,             # at callback receipt (drainer thread)
    "t_utc": "2026-05-27T12:34:56.789Z",
    "flags": ["final"]                     # subset of: final, partial, reused, overrun_marker
}

chunk_payload = (n_channels * valid_samples * sizeof(dtype)) bytes
              # if interleaved_cjc is true: n_channels * 2 * valid_samples * sizeof(dtype)
```

Why per-chunk framing matters: the v1 "header + appended bytes" format
round-trips a clean run but cannot represent partial final buffers,
`OLDA_WM_BUFFER_REUSED` events (overwritten in `WrapMode.MULTIPLE`),
overrun gaps (`OLDA_WM_OVERRUN_ERROR` is recorded as a zero-payload
chunk with `flags=["overrun_marker"]`), or out-of-order sequence
numbers from a multi-threaded drainer. The replay utility uses `seq` +
`flags` to reconstruct the exact event timeline of the original run —
not just the data, but what the SDK reported about the data.

A replay utility (`dtollib.tools.replay_raw`) re-opens the file as a
stream of `DaqBlock`s for post-hoc analysis, applying the conversion at
read time using the file-header's channel metadata and the per-chunk
metadata for sample-time reconstruction.

The sink registers itself as a passive observer in `record()`'s internal
fan-out — buffer data flows into it directly from the drainer thread,
**before** the async stream. This means raw-counts logging keeps working
even if the async consumer is slow / has stopped. The file is the
durable record of the run; the async stream is the live view.

Why a custom format instead of TDMS:
- We'd have to bring in a TDMS writer dependency (`npTDMS`) and ship a
  format we don't fully control.
- The raw-counts format is dead-simple to read in NumPy, MATLAB, or any
  other tool — `np.fromfile(f, dtype=...)` is enough.
- It's faithful: the SDK gives us raw counts; we write raw counts. No
  loss of information.

Users who want TDMS specifically can pipe `DaqBlock`s into `nptdms` from
a normal async consumer; we don't make it the default.

### 15.3 CSV / JSONL / SQLite / Parquet / Postgres

Direct ports of the sibling sinks. `reading_to_row(reading)`,
`sample_to_row(sample)`, `block_to_long_rows(block)` work the same way.

### 15.4 Utility helpers (`dtollib.utils`)

Port of the .NET `Utility` class (UMOpenLayers.md:2273-2294) — pure
Python, no SDK calls, no hardware needed. Lives in `dtollib/utils.py`
and re-exported from the package root for ergonomic access.

```python
def convert_temperature_to_volts(
    tc_type: ThermocoupleType,
    temperature_c: float,
) -> float:
    """ITS-90 polynomial: temperature → thermocouple voltage (volts).
    Useful for: simulating a TC source against a voltage AI channel,
    sanity-checking hardware-linearised reads, replay-time conversion
    when the .dt-raw file stored voltage rather than temperature."""

def convert_volts_to_temperature(
    tc_type: ThermocoupleType,
    volts: float,
    cjc_temperature_c: float = 0.0,
) -> float:
    """ITS-90 inverse polynomial: TC voltage + CJC → temperature (°C).
    The fallback path for boards that report VOLTAGE_IN on a TC channel
    when SupportsTemperatureDataInStream is False (which is the
    application-side TC linearisation case from §31.9)."""

def get_thermocouple_range(tc_type: ThermocoupleType) -> tuple[float, float]:
    """Documented operating range (°C, low, high) for a TC type.
    Used by ThermocoupleInput.__post_init__ to validate that
    (min_val_degc, max_val_degc) fits inside the physically meaningful
    range — saves an SDK round-trip and gives a precise error."""

def compute_rectangular_rosette(
    epsilon_0: float, epsilon_45: float, epsilon_90: float,
) -> tuple[float, float, float]:
    """Rectangular strain-gage rosette: returns (eps_max, eps_min, angle_deg).
    Three quarter-bridge SGs at 0°/45°/90°."""

def compute_delta_rosette(
    epsilon_0: float, epsilon_60: float, epsilon_120: float,
) -> tuple[float, float, float]:
    """Delta strain-gage rosette: returns (eps_max, eps_min, angle_deg).
    Three quarter-bridge SGs at 0°/60°/120°."""
```

These are utilities the .NET API exposes off the static `Utility`
class, not on any subsystem. Same shape in Python: free functions that
don't touch the SDK, so they work in tests, replay tooling, and CI
on Linux/macOS without the real backend present.

The TC polynomial coefficients are the NIST ITS-90 reference tables;
the rosette formulas are textbook ASTM E1561 / E132. Both are
test-against-hand-calculated-vectors, no hardware needed.

---

## 16. Manager Design

### 16.1 DtolManager

```python
class DtolManager:
    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None: ...

    async def add(
        self,
        name: str,
        spec: TaskSpec,
        *,
        backend: DtolBackend | None = None,
    ) -> DtolSession: ...

    async def remove(self, name: str) -> None: ...
    def get(self, name: str) -> DtolSession: ...

    async def start(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]: ...
    async def stop(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]: ...
    async def poll(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[DaqReading]]: ...
    async def read_block(
        self,
        samples_per_channel: int,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, DeviceResult[DaqBlock]]: ...

    async def start_synchronized(self, names: Sequence[str]) -> None:  # v0.2
        """olDaSimultaneousStart all named tasks."""
```

### 16.2 Manager differences from `nidaqlib`

DT-Open Layers' subsystem-reservation rules:

- **One HDASS per (board, subsystem-type, element).** Two `TaskSpec`s
  targeting `(board="DT9805(00)", subsystem=A/D, element=0)` cannot
  coexist. The manager's preflight catches this at `add()` time.
- **HDRVR is shared, ref-counted.** The manager keeps a
  `dict[str, _HdrvrRef]` keyed by board name; first `add()` per board
  calls `olDaInitialize`, last `remove()` per board calls
  `olDaTerminate`. Same ref-count shape as siblings.
- **Simultaneous start is hardware-coordinated**, not software-coordinated.
  `start_synchronized` runs the four-step SDK sequence
  (`olDaGetSSList` → `olDaPutDassToSSList` × N → `olDaSimultaneousPreStart`
  → `olDaSimultaneousStart`).

### 16.3 Thread-safety stance — start conservative, relax with evidence

Per the SDK manual, HDRVR is thread-safe and HDASS is not. In practice
the DataAcq SDK has not been thoroughly audited for cross-subsystem
reentrancy on a single board (some subsystems share hardware resources
internally — e.g. clock domains, DMA channels). We start conservative:

- **v0.1:** one `anyio.Lock` per *board* (HDRVR), shared by every
  session that targets that board. Concurrent sessions across different
  boards run in parallel.
- **v0.2+ (if needed):** measure under realistic load. If profiling
  shows lock contention on multi-subsystem boards (DT9806 AI + C/T
  running concurrently) and bench tests confirm cross-subsystem safety,
  relax to per-HDASS locks for the relevant subsystem pairs.

The relaxation should be earned by a documented bench experiment, not
inferred from the SDK manual. Cost of over-locking is throughput;
cost of under-locking is hours of debugging an SDK that returns
generic ECODEs from a corrupted internal state.

### 16.4 Resource model

```python
@dataclass(frozen=True, slots=True)
class SubsystemReservation:
    board: str
    subsystem_type: SubsystemType            # A/D, D/A, DIN, DOUT, C/T, QUAD, TACH
    element: int                             # usually 0; some boards have multiple AI subsystems
```

The manager rejects an `add()` that conflicts with an existing
reservation. Conflicts that the manager cannot detect (e.g. shared
clock or trigger lines) surface at `olDaConfig` / `olDaStart` time as
typed errors — same shape as `nidaqlib` §15.3.

### 16.5 `DtolCollection` — forward-looking (v0.3+, not committed)

The .NET API splits its surface into two namespaces: `OpenLayers.Base`
for individual boards and `OpenLayers.DeviceCollection` for
*device collections* — VIBbox-style chassis or user-defined collections
created via the DT Device Collection Manager. A collection is N
physical devices wired together over the Sync Bus, configured and
started as one logical device with a master/slave relationship
(UMOpenLayers.md:2655-2776).

`DtolManager` (§16.1) is **not** the right abstraction for collections.
The manager handles "N independent named tasks I want to coordinate";
a collection is "N devices the SDK already considers one." Different
lifecycle:

| Concern | `DtolManager` | `DtolCollection` (proposed) |
|---|---|---|
| What it manages | N independent sessions | One logical device with N elements |
| Discovery | `find_devices()` per board | `find_collections()` returns one entity |
| `olDaInitialize` calls | N (one per board) | 1 (the collection driver) |
| Synchronisation | software loop or `start_synchronized` | hardware Sync Bus, intrinsic |
| Subsystem set | full (AI/AO/DIN/DOUT/CT/QUAD/TACH) | AI + AO only (per the .NET spec) |
| Master/slave roles | none | first-class (`master_index`, slave roles) |

Proposed shape, deferred to v0.3+ pending a real VIBbox-style rig in
the lab:

```python
class DtolCollection:
    """One logical DT device made of N Sync-Bus-coordinated boards.
    Use this for VIBbox or DT Device Collection Manager-defined
    collections. For ad-hoc multi-board coordination on independent
    boards, use DtolManager instead."""

    collection_name: str
    collection_devices: tuple[BoardInfo, ...]
    master_index: int

    async def analog_input_subsystem(self, element: int = 0) -> DtolSession: ...
    async def analog_output_subsystem(self, element: int = 0) -> DtolSession: ...
    # No DIN/DOUT/CT/QUAD/TACH — collections only expose AI + AO.

async def find_collections() -> tuple[CollectionInfo, ...]: ...
```

For v0.1/v0.2, the plain `DtolManager.start_synchronized(names)` path
covers the case of "I have two independent DT9806s and want them to
start together via the SDK's simultaneous-start primitives." A
`DtolCollection` would be added only after a real VIBbox-style rig
shows up in the lab and exercises the master/slave semantics in
practice. Open Question 11 (added to §31) tracks this.

---

## 17. Error Model

### 17.1 Root error

```python
class DtolError(Exception):
    def __init__(self, message: str, *, context: ErrorContext | None = None) -> None: ...
```

Naming matches sibling root pattern: `AlicatError`, `SartoriusError`,
`WatlowError`, `NIDaqError`, `DtolError`.

### 17.2 ErrorContext

```python
@dataclass(frozen=True, slots=True)
class ErrorContext:
    task_name: str | None = None
    board: str | None = None                     # DT-Open Layers board name
    subsystem_type: SubsystemType | None = None
    element: int | None = None
    channel_name: str | None = None
    channel: int | None = None                   # physical channel number (SDK int)
    operation: str | None = None                 # e.g. "olDaConfig", "read_block"
    ecode: int | None = None                     # raw ECODE / OLSTATUS value
    ecode_source: Literal["oldaapi", "olmem"] | None = None
    ecode_message: str | None = None             # from olDaGetErrorString / olDmGetErrorString
    sdk_event_kind: SdkEventKind | None = None   # set on async-error wraps
    extra: Mapping[str, object] = field(default_factory=dict)
```

### 17.3 Subclasses

```text
DtolError
  DtolConfigurationError              # invalid spec / config commit rejected
  DtolValidationError                 # client-side validation before SDK call
  DtolTaskStateError                  # poll() during continuous, start-before-config, etc.
  DtolReadError                       # olDaGetSingleValue / olDaGetBuffer failed
  DtolWriteError                      # olDaPutSingleValue / put-buffer-to-list failed
  DtolTimeoutError                    # operation exceeded explicit timeout
  DtolResourceError                   # HDASS conflict, simultaneous-start mismatch
  DtolCapabilityError                 # requested feature not supported per CapabilitySet
  DtolCapiError                       # raw ECODE from oldaapi / olmem; parent for ↓ buffer/trigger
    DtolBufferOverrunError            # OLDA_WM_OVERRUN_ERROR (continuous AI)
    DtolBufferUnderrunError           # OLDA_WM_UNDERRUN_ERROR (continuous AO)
    DtolTriggerError                  # OLDA_WM_TRIGGER_ERROR
  DtolBackendError                    # generic SDK failure not in another bucket
  DtolDependencyError                 # DLL not found, bitness mismatch, SDK not installed
  DtolConfirmationRequiredError       # safety-gate refusal
  DtolSinkError
    DtolSinkSchemaError
    DtolSinkWriteError
    DtolSinkDependencyError
```

### 17.4 Wrapping SDK errors

Single wrapping point at `DataAcqBackend`. The classifier knows which
DLL's error-string function to call based on the source function:

```python
def _check(
    self,
    status: int,
    *,
    op: str,
    source: Literal["oldaapi", "olmem"] = "oldaapi",
    **ctx: object,
) -> None:
    if status == OLNOERROR:
        return
    msg = (self._capi.errors.olda_error_string(status)
           if source == "oldaapi"
           else self._capi.errors.olmem_error_string(status))
    raise self._classify(status)(
        f"{op} failed: {msg}",
        context=ErrorContext(
            operation=op,
            ecode=status,
            ecode_source=source,
            ecode_message=msg,
            **ctx,
        ),
    )
```

Interim classification table maps `OLSTATUS` ranges to `DtolError`
subclasses:
- `0x0001`–`0x00FF` (device errors) → `DtolBackendError`
- `0x0100`–`0x01FF` (init errors) → `DtolConfigurationError`
- `0x0200`–`0x02FF` (configuration) → `DtolConfigurationError`
- `0x0300`–`0x03FF` (operation) → `DtolTaskStateError`
- `0x0400`–`0x04FF` (buffer) → `DtolReadError` / `DtolWriteError`
- `0x0500`–`0x05FF` (memory) → `DtolBackendError`

Bench-frequent codes encountered during V7.0.0.7 verification
(`OLERRORS.H` / docs/decisions.md) — surface every one as a typed
exception, not a generic `DtolBackendError`:

| ECODE | Symbol | Meaning | Typical cause |
|-------|--------|---------|---------------|
| 8 | `OLBADCHANNELTYPE` | Invalid channel-type selector | wrong-rev `OL_CHNT_*` (use 100/101, not 0/1) |
| 10 | `OLBADTRIGGER` | Invalid trigger selector | wrong-rev `OL_TRG_*` (use 300-series) |
| 12 | `OLBADCLOCKSOURCE` | Invalid clock-source selector | wrong-rev `OL_CLK_*` (use 400-series) |
| 18 | `OLBADDATAFLOW` | Invalid data-flow selector | wrong-rev `OL_DF_*` (use 800-series) |
| 20 | `OLSUBSYSINUSE` | Subsystem already acquired | prior session didn't release HDASS |
| 27 | `OLDATAFLOWMISMATCH` | Op not valid in current data-flow | called single-value API on a continuous-mode subsystem |
| 35 | `OLBADWRAPMODE` | Invalid wrap-mode selector | wrong-rev `OL_WRP_*` (use 1000-series) |
| 36 | `OLNOTSUPPORTED` | Feature not supported by this subsystem | called `olDaSetMultiSensorType` on TC-only board |
| 89 | `OLBADQUEUE` | Invalid queue selector | wrong-rev `OL_QUE_*` (use 1100-series) |

ECODE 8/12/18/35/89 are almost always **caller-side bugs** in the
constant family the dtollib was built against — the SDK is reporting
"I don't recognise this value at all." If you see one of these from
production code, the first hypothesis should be "the constant table
slipped against the installed SDK rev."

**Range-based mapping is interim.** Ranges are not a stable taxonomy
across SDK revisions — the manual lists specific named codes, and a
single range routinely contains codes that should classify into
different subclasses (e.g., a "buffer" code that's really a
state-machine violation belongs in `DtolTaskStateError`, not
`DtolReadError`). The Phase 1 deliverable replaces the range table
with a per-code table built from `OLERRORS.H`: one row per named
`OL*` error constant (e.g. `OLNOERROR`, `OLBADCAP`, `OLBADELEMENT` —
~160 codes), with the DataAcq SDK version it was sourced from
recorded next to it (so a future SDK upgrade is a diff against a known
baseline, not a guess). The range table remains as a fallback for
unknown codes the per-code table doesn't cover, and as a safety net if
the SDK adds new codes between releases.

The classification table lives in `capi/errors.py` and is exposed
to the backend as a single `classify(status: int) -> type[DtolError]`
function. Tests assert each documented `OLSTATUS` lands in the right
bucket — a range-based parametrised test in v0.0, replaced by a
per-code parametrised test once Phase 1 lands.

---

## 18. Safety Model

DT-Open Layers can drive AO, DO, and CO outputs. Same seriousness as
sibling-library actuators.

### 18.1 Operations requiring confirmation (v0.2+)

- AO writes above a configured `safe_max` / below `safe_min`.
- DO writes on lines marked `requires_confirm`.
- C/O pulse-train generation.
- Auto-calibration (`olDaAutoCalibrate`).
- Anything that mutates persistent device state.

### 18.2 Example

```python
await session.write({"heater_command": 4.5}, confirm=True)
```

### 18.3 Channel-level safety metadata

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogOutputVoltage(ChannelSpec):
    kind: ClassVar[str] = "ao_voltage"
    min_val: float = -10.0
    max_val: float = 10.0
    safe_min: float | None = None
    safe_max: float | None = None
    requires_confirm: bool = True
```

Validation runs **before** the SDK call — backends never silently clamp.

---

## 19. Configuration and Metadata

### 19.1 DtolConfig

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DtolConfig:
    default_timeout_s: float = 10.0
    default_sample_rate_hz: float = 1000.0
    default_chunk_size: int = 1000
    default_buffers: int = 4                # BufferPlan.buffers default
    default_stream_buffer: int = 16         # AnyIO send-stream buffer
    eager_tasks: bool = False
    oldaapi_dll_path: str | None = None     # explicit oldaapi*.dll override
    olmem_dll_path: str | None = None       # explicit olmem*.dll override

    def replace(self, **updates: object) -> "DtolConfig":
        return dataclasses.replace(self, **updates)


def config_from_env(prefix: str = "DTOLLIB_") -> DtolConfig:
    """Reads:
        DTOLLIB_DEFAULT_TIMEOUT_S, DTOLLIB_DEFAULT_SAMPLE_RATE_HZ,
        DTOLLIB_DEFAULT_CHUNK_SIZE, DTOLLIB_DEFAULT_BUFFERS,
        DTOLLIB_DEFAULT_STREAM_BUFFER, DTOLLIB_EAGER_TASKS,
        DTOLLIB_OLDAAPI_DLL, DTOLLIB_OLMEM_DLL.
    Mirrors nidaqlib.config_from_env."""
```

### 19.2 RunMetadata

Same shape as `nidaqlib.RunMetadata`, adding SDK-specific fields:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RunMetadata:
    run_id: str
    started_at: datetime
    dtollib_version: str
    sdk_version: str | None                  # from olDmGetVersion
    dll_path: str                            # which DLL was loaded
    python_version: str
    platform: str                            # always "win32" but recorded
    bitness: int                             # 32 or 64
    task_specs: Mapping[str, TaskSpec]
    user_metadata: Mapping[str, object] = field(default_factory=dict)
```

### 19.3 Serialisation

Same discriminated-union pattern as `nidaqlib` §18.3. Each
`ChannelSpec` subclass declares `kind: ClassVar[str]` and exposes
`to_dict()` / `from_dict()`. The base `ChannelSpec.from_dict` dispatches
through `_CHANNEL_REGISTRY` keyed by `kind`.

### 19.4 Sidecar metadata

For `RawCountsSink` paths, write a sidecar `<file>.metadata.json`
alongside the `.dt-raw` file. The JSON is `RunMetadata.to_dict()`.

---

## 20. System Discovery

### 20.1 API

```python
from dtollib.system import find_devices, list_subsystems

devices: list[DeviceInfo] = find_devices()           # uses olDaEnumBoardsEx
subsystems: list[SubsystemInfo] = list_subsystems("DT9805(00)")
```

### 20.2 Models

```python
@dataclass(frozen=True, slots=True)
class DeviceInfo:
    name: str                                # e.g. "DT9805(00)"
    model: str                               # "DT9805", "DT9806", ...
    driver_name: str                         # from olDaEnumBoardsEx
    instance: int                            # registry instance number
    sdk_version: str | None                  # from olDmGetVersion
    subsystems: tuple[SubsystemInfo, ...]


@dataclass(frozen=True, slots=True)
class SubsystemInfo:
    type: SubsystemType                      # A/D / D/A / DIN / DOUT / C/T / QUAD
    element: int                             # 0-based
    num_channels: int                        # OLSSC_NUMCHANNELS
    supports_continuous: bool                # OLSSC_SUP_CONTINUOUS
    supports_singlevalue: bool               # OLSSC_SUP_SINGLEVALUE
    supports_simultaneous: bool              # OLSSC_SUP_SIMULTANEOUS_SH
    supports_multisensor: bool               # OLSSC_SUP_MULTISENSOR
    supports_dma: bool                       # OLSSC_SUP_DMA
    max_throughput_hz: float                 # OLSSCE_MAX_THROUGHPUT (olDaGetSSCapsEx)
    max_channel_list_size: int               # OLSSC_CGLDEPTH
    available_ranges: tuple[tuple[float, float], ...]  # OL_ENUM_RANGES
    available_gains: tuple[float, ...]                 # OL_ENUM_GAINS
```

### 20.3 Discovery without hardware

`find_devices()` on a machine without the SDK installed raises
`DtolDependencyError`. On a machine with the SDK but no DT hardware, it
returns `[]`. Both behaviours are tested with `FakeDtolBackend` plus a
mock loader.

---

## 21. CLI Tools

| CLI             | Purpose                                              | Version |
|-----------------|------------------------------------------------------|---------|
| `dtol-discover` | List boards and subsystems with capabilities.        | v0.1    |
| `dtol-capture`  | Short acquisition to file (Parquet or `.dt-raw`).    | v0.1    |
| `dtol-diag`     | Diagnose SDK / DLL / driver install issues.          | v0.1    |
| `dtol-read`     | One-shot scalar read.                                | v0.2    |
| `dtol-info`     | Print detailed per-board info.                       | v0.2    |

### 21.1 `dtol-discover` (v0.1)

```bash
dtol-discover                              # all boards, summary table
dtol-discover --board DT9805\(00\)         # filter to one board
dtol-discover --json                       # machine-readable output
```

Lists boards and their subsystems with key capability flags
(`OLSSC_RETURNS_FLOATS`, `OLSSC_SUP_CONTINUOUS`,
`OLSSC_SUP_SIMULTANEOUS_SH`, max throughput, channel count).

### 21.2 `dtol-capture` (v0.1)

```bash
dtol-capture --board DT9805\(00\) --channels 0,1 --rate 1000 \
             --duration 10 --out run.parquet
dtol-capture --board DT9805\(00\) --channels 0,1 --rate 10000 \
             --duration 60 --out run.dt-raw
```

Output format selected by `.parquet` vs `.dt-raw` extension. The `.dt-raw`
path uses `RawCountsSink` and is the recommended choice for high-rate
acquisition.

### 21.3 `dtol-diag` (v0.1)

The install-troubleshooting tool. Surfaces every piece of context a
support call would otherwise have to extract:

```bash
dtol-diag                                  # all checks
dtol-diag sdk                              # just the SDK / DLL section
dtol-diag boards                           # just enumeration
```

Reports:

- Python interpreter path, architecture (32/64-bit), version
- Operating system version
- `oldaapi*.dll` discovered path + bitness + load success/failure
- `olmem*.dll` discovered path + bitness + load success/failure
- Expected SDK install root probed for diagnostic context (presence /
  absence only — never required for load). All under
  `C:\Program Files (x86)\Data Translation\Win32\SDK\` on 64-bit Windows:
  - `...\SDK\Include\` (headers)
  - `...\SDK\Lib\` (32-bit import libs)
  - `...\SDK\Lib64\` (64-bit import libs)
  - `...\SDK\Examples\` (C reference examples)
- `olDaGetVersion` (acquisition library)
- `olDmGetVersion` (memory library)
- Number of boards enumerated; for each: name, driver name, model,
  instance number
- For each subsystem on each board: type, element, num_channels,
  key capability flags
- Common failure hints when checks fail (DLL not found → install SDK
  from Data Translation, expected at
  `C:\Program Files (x86)\Data Translation\Win32\SDK\` on 64-bit Windows;
  bitness mismatch → install matching SDK; no boards → check Open Layers
  Control Panel)

Exit code is nonzero if any check fails — usable in CI as a precondition
gate.

### 21.4 `dtol-read` / `dtol-info` (v0.2)

```bash
dtol-read    --board DT9805\(00\) --channel 0 --range -10,10
dtol-info    --board DT9805\(00\)               # full per-board CapabilitySet dump
```

Same shape as `nidaq-read` / `nidaq-info`.

---

## 22. Testing Strategy

### 22.1 Unit tests (no hardware, no SDK)

Use `FakeDtolBackend`. Cover:

- TaskSpec validation (channel-kind mixing rejected, kw_only enforced).
- ChannelSpec validation (range bounds, gain enum, sensor-specific knobs).
- Timing / TriggerSpec validation.
- Backend call ordering (Set* before Config, Config before Start).
- §12.3.2 callback-bridge ordering (Register before Start, Stop before
  Unregister, Sentinel before Drain-wait).
- BufferPlan sizing minima (`buffers ≥ 2`, recycling correctness).
- Error wrapping (every documented `ECODE` → right `DtolError` subclass,
  via the classification table in `capi/errors.py`).
- CapabilitySet population — `OLSSC_RETURNS_FLOATS` toggling switches
  the buffer pipeline branch.
- SdkEventKind dispatch (all 10 events routed correctly).
- Session lifecycle (idempotent close, configure-failure tears down).
- Recorder backpressure / OverflowPolicy semantics.
- Sink schema behaviour.
- Sync facade parity.
- RawCountsSink round-trip (write → replay → assert array equality).
- Discriminated channel-spec serialisation round-trip.

### 22.2 Binding tests (no DT hardware, real ctypes)

Cover the `capi` layer against a hand-rolled `tests/_fake_dll.c`
compiled to a stub DLL on Windows CI. Asserts:

- Signatures match (argtypes / restype produce the expected ctypes
  call shape).
- Constants match the values in `oldaapi.h` (the stub DLL re-exports
  the header constants for read-back).
- Notification callback fires under the right `WINFUNCTYPE` shape.

This catches `argtypes` typos without needing a DT device.

### 22.3 Hardware tests (opt-in)

Same marker / env-var pattern as `nidaqlib`:

```toml
markers = [
    "hardware: requires connected DT DAQ hardware",
    "hardware_stateful: changes subsystem/device state",
    "hardware_output: writes AO / DO / CO",
    "hardware_destructive: calibration or potentially unsafe operations",
    "slow: excluded from fast CI",
]
```

Environment gates:

```text
DTOLLIB_ENABLE_HARDWARE_TESTS=1
DTOLLIB_ENABLE_STATEFUL_TESTS=1
DTOLLIB_ENABLE_OUTPUT_TESTS=1
DTOLLIB_ENABLE_DESTRUCTIVE_TESTS=1
```

Hardware test configuration:

```text
DTOLLIB_TEST_DEVICE=DT9805(00)
DTOLLIB_TEST_AI_CHANNEL_PRIMARY=0
DTOLLIB_TEST_AI_CHANNEL_SECONDARY=1                  # optional
DTOLLIB_TEST_TC_TYPE=K
DTOLLIB_TEST_RATE_HZ=10
DTOLLIB_TEST_TC_MIN_DEGC=-50
DTOLLIB_TEST_TC_MAX_DEGC=200
```

### 22.4 CI matrix

- **Lint** (Ubuntu): ruff check + ruff format check.
- **Typecheck** (Ubuntu): mypy strict + pyright strict.
- **Test** (Windows + Ubuntu + macOS, py3.13 + py3.14): unit + binding
  tests against `FakeDtolBackend`. Real backend tests run only on
  Windows; non-Windows platforms test the fake backend exclusively
  (the real backend module is import-guarded by
  `sys.platform == "win32"`).
- **Build** (Ubuntu): sdist + wheel; twine check strict.

Workflows mirror `nidaqlib/.github/workflows/{ci,docs,release}.yml`
verbatim, with the addition of a Windows-only `hardware` job that runs
manually on a self-hosted runner with a DT9805 attached (opt-in via
`workflow_dispatch`).

---

## 23. Documentation Plan

```text
docs/
  index.md
  quickstart-async.md
  quickstart-sync.md
  installation.md            # how to install the SDK runtime, troubleshooting DLL discovery
  task-specs.md
  channels.md                # voltage / thermocouple / RTD / strain / IEPE
  timing.md
  triggers.md
  streaming.md
  raw-logging.md             # RawCountsSink and the .dt-raw format
  safety.md
  testing.md
  architecture.md            # short — pointer to design.md
  design.md                  # this document (renamed at first commit)
  troubleshooting.md         # common SDK errors decoded
  binding.md                 # capi internals (two DLLs) — for contributors
  api/                       # mkdocstrings auto-generated
```

Most-important docs at launch:

1. **Quickstart: thermocouple input** (the DT9805 primary use case).
2. **Quickstart: continuous voltage acquisition to Parquet**.
3. **Installation + DLL discovery** (this is the most likely source of
   first-time install failures).
4. **High-rate acquisition via RawCountsSink**.
5. **Unified experiment** — DAQ + Alicat + Sartorius.
6. **How this differs from raw ctypes / QuickDAQ**.
7. **When to use the escape hatch**.

Build with Zensical + mkdocstrings-python (same as `nidaqlib` /
`watlowlib`).

---

## 24. Packaging

### 24.1 pyproject.toml sketch

```toml
[build-system]
requires = ["hatchling>=1.29.0", "hatch-vcs>=0.5.0"]
build-backend = "hatchling.build"

[project]
name = "dtollib"
dynamic = ["version"]
description = "Experiment-facing DT-Open Layers acquisition layer for scientific instrumentation."
readme = "README.md"
requires-python = ">=3.13"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Grayson Bellamy", email = "gbellamy@umd.edu" }]
keywords = ["data-translation", "dt-open-layers", "daq", "data-acquisition",
            "thermocouple", "instrument", "laboratory"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: AnyIO",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Programming Language :: Python :: Implementation :: CPython",
    "Topic :: Scientific/Engineering",
    "Topic :: System :: Hardware",
    "Typing :: Typed",
]
dependencies = [
    "anyio>=4.13",
    "numpy>=2",
]

[project.optional-dependencies]
postgres = ["asyncpg>=0.30"]
parquet = ["pyarrow>=16"]
docs = [
    "zensical>=0.0.37",
    "mkdocstrings-python>=1.12",
]

[project.scripts]
dtol-discover = "dtollib.cli.discover:main"
dtol-capture  = "dtollib.cli.capture:main"
dtol-diag     = "dtollib.cli.diag:main"
dtol-read     = "dtollib.cli.read:main"
dtol-info     = "dtollib.cli.info:main"

[project.urls]
Homepage = "https://github.com/GraysonBellamy/dtollib"
Repository = "https://github.com/GraysonBellamy/dtollib"
Documentation = "https://GraysonBellamy.github.io/dtollib/"
Issues = "https://github.com/GraysonBellamy/dtollib/issues"
Changelog = "https://github.com/GraysonBellamy/dtollib/blob/main/CHANGELOG.md"
```

Dev / lint / type / test / docs groups match `nidaqlib` verbatim.

### 24.2 Dependency note

Smallest core dependency footprint of the lab packages — only `anyio`
and `numpy`. Notably:

- **No vendor Python SDK** (we are the binding).
- **No driver Python package** (the SDK is a C DLL).
- **No serial library** (USB enumeration goes through the SDK).

The runtime dependency is the DataAcq SDK installer itself, which the
user must install separately from Data Translation. The package's
`installation.md` walks through this.

### 24.3 Wheel distribution strategy

Pure-Python wheel, classified `Operating System :: Microsoft :: Windows`.
The `capi/loader.py` raises `DtolDependencyError` at first use on
non-Windows platforms, with a clear message. Imports of the public
package at module-load time do not touch the DLL — discovery and load
are deferred to `find_devices()` / `open_device()`.

This means the wheel installs cleanly on Linux/macOS for ecosystem
composition (e.g., a multi-instrument experiment script that imports
both `dtollib` and `alicatlib` can be type-checked on a Linux CI
runner), but the real backend only works on Windows.

---

## 25. Migration Map from `nidaqlib`

| `nidaqlib` module        | `dtollib` equivalent                              | Decision |
|--------------------------|---------------------------------------------------|----------|
| `_logging.py`            | `_logging.py` with `ROOT = "dtollib"`             | **Direct port** |
| `_runtime.py`            | `_runtime.py`                                     | **Direct port** |
| `config.py`              | `config.py` with `DtolConfig`                     | **Port the shape**, change fields (add `default_buffers`, `oldaapi_dll_path`, `olmem_dll_path`) |
| `errors.py`              | `errors.py` with `DtolError`                      | **Port the shape**, add `DtolBufferOverrunError` / `DtolBufferUnderrunError` / `DtolTriggerError` / `DtolDependencyError` |
| `backend/base.py` (DaqBackend Protocol) | `backend/base.py` (DtolBackend Protocol) | **Port the role**, expand methods to cover buffer-pool + notification surface |
| `backend/nidaqmx_backend.py` | `backend/dtol_backend.py`                     | **Rewrite** — different SDK |
| `backend/fake.py`        | `backend/fake.py` (FakeDtolBackend)               | **Port the shape**, add buffer-pool + notification simulation |
| (none)                   | `capi/`                                       | **NEW** — the C binding |
| (none)                   | `backend/_buffer_pool.py`                         | **NEW** — internal BufferPool helper backing `BufferPlan` |
| (none)                   | `backend/_callback_bridge.py`                     | **NEW** — driver-thread bridge |
| `system/discovery.py`    | `system/discovery.py`                             | **Port the role**, use `olDaEnumBoardsEx` |
| `channels/`              | `channels/`                                       | **Port the shape**, add DT-specific sensors (RTD, thermistor, strain, bridge, IEPE) |
| `tasks/spec.py`          | `tasks/spec.py`                                   | **Direct port** with field renames |
| `tasks/triggers.py`      | `tasks/triggers.py`                               | **Port the shape**, DT-specific trigger types |
| `tasks/session.py`       | `tasks/session.py`                                | **Port the shape**, lifecycle includes HDRVR + HDASS |
| `tasks/models.py`        | `tasks/models.py`                                 | **Direct port** (DaqReading, DaqBlock identical shape) |
| `streaming/recorder.py`  | `streaming/recorder.py` (`record_polled`)         | **Direct port** |
| `streaming/block.py`     | `streaming/block.py` (`record`)                   | **Port the shape**, drive the §12.3.2 bridge instead of §11.3.2 |
| `sinks/`                 | `sinks/`                                          | **Direct port**, add `RawCountsSink` |
| `manager.py`             | `manager.py` (DtolManager)                        | **Direct port** with renames |
| `sync/`                  | `sync/` (Dtol facade)                             | **Direct port** |
| `cli/`                   | `cli/` (`dtol-*`)                                 | **Direct port** with `dtol-` prefix |
| `testing.py`             | `testing.py`                                      | **Heavy port** — add notification-firing helpers |

### The two non-obvious deviations from `nidaqlib`

1. **`capi/` exists.** It is the lowest layer of `dtollib`; `nidaqlib`
   delegates this entirely to `nidaqmx-python`. Two DLLs (`oldaapi`,
   `olmem`); both loaded by `capi/loader.py`.
2. **`BufferPlan` is user-visible.** `nidaqmx` hides buffer management;
   the DataAcq SDK does not. Pretending otherwise leads to surprising
   `OLDA_WM_OVERRUN_ERROR`. We expose `buffers` / `samples_per_buffer`
   on `TaskSpec.buffers` and document the tradeoffs.

Everything else is a port.

---

## 26. Phased Roadmap

The roadmap is feature-sliced. Each phase lists exactly the SDK
functions that get bound — bind small, test small. Functions not listed
in a phase are not bound yet; the user-facing surface is bounded by what
the current phase covers.

> **Hardware reconciliation.** Several original acceptance targets below
> have been revised against bench findings on the owned DT9805/DT9806
> (SDK V7.0.0.7) — continuous analog output (Phase 4), quadrature/
> tachometer and AI+C/T simultaneous-start (Phase 5), and the
> `diag_read_reg`/`diag_write_reg` escape hatches (Phase 2). See
> [plan-hardware-functional.md](plan-hardware-functional.md) for the
> authoritative hardware envelope; the entries below are annotated where
> they are superseded.

### Phase 0 — Scaffold

**Scope:**
- Package skeleton matching `nidaqlib`.
- `pyproject.toml`, `zensical.toml`, docs scaffolding, CI workflows
  (`ci.yml`, `docs.yml`, `release.yml`).
- Port `_logging`, `_runtime`, `sync/portal.py`, the sink Protocols and
  in-memory sink, test config.
- `errors.py` with the full `DtolError` hierarchy.
- `config.py` with `DtolConfig` + `config_from_env`.
- Import-smoke tests on all CI OSes (no SDK, no hardware).

**No SDK functions bound yet.** Goal is import-clean on Linux/macOS.

### Phase 1 — C boundary, discovery, diagnostics

**SDK functions bound:**
- `olDaGetVersion`, `olDmGetVersion`
- `olDaGetErrorString`, `olDmGetErrorString`
- `olDaEnumBoards`, `olDaEnumBoardsEx`, `olDaGetBoardInfo`
- `olDaInitialize`, `olDaTerminate`
- `olDaEnumSubSystems`, `olDaGetDevCaps`
- `olDaGetDASS`, `olDaReleaseDASS`
- `olDaGetSSCaps`, `olDaGetSSCapsEx`, `olDaEnumSSCaps`,
  `olDaEnumChannelCaps`

**Public surface:**
- `find_devices()`, `find_subsystems(board)`, `inspect_device(board)`
- `CapabilitySet`, `BoardInfo`, `SubsystemInfo`, `DeviceInfo`
- `IOType` enum (§8.15) populated from `OLSSC_IO_TYPE` per channel
- `dtollib.utils` — pure-Python helpers (`convert_temperature_to_volts`,
  `convert_volts_to_temperature`, `get_thermocouple_range`,
  `compute_rectangular_rosette`, `compute_delta_rosette`). No SDK
  required; ports of the .NET `Utility` class. Tested against
  hand-calculated NIST ITS-90 reference vectors and textbook rosette
  examples — same test suite runs on Linux/macOS CI.
- `dtol-discover`, `dtol-diag` CLIs
- `FakeDtolBackend` parity for everything above

**Header verification (prerequisite for entering Phase 1):**
- All `capi/types.py` aliases (`HDRVR`, `HDASS`, `HBUF`, `HLIST`,
  `HSSLIST`, `ECODE`, `OLSTATUS`) cross-checked against installed
  `OLTYPES.H` on the first maintainer machine that has the SDK
  available. The opaque-pointer assumption (`c_void_p`) is committed
  only after that check; if any handle is actually `HANDLE` /
  `HINSTANCE` / `c_uint32` per the header, the alias is corrected
  before binding any function that uses it.
- All `argtypes` / `restype` declarations in `capi/prototypes.py`
  cross-checked against `OLDAAPI.H` / `OLMEM.H` for the functions
  bound in this phase. Notably: `olDaSetWndHandle`'s third (lParam)
  arg confirmed as `LPARAM` (not `LONG`) and its second arg as `HWND`;
  every output parameter confirmed as a pointer type; every callback
  typedef confirmed as stdcall (`WINFUNCTYPE`, not `CFUNCTYPE`).
- Findings recorded in `docs/decisions.md` with the SDK version they
  were verified against. A future SDK upgrade then becomes a diff
  against a known baseline, not a guess.

**Acceptance:** `dtol-diag` reports cleanly on a fresh SDK install;
`find_devices()` returns a populated list against a connected DT9805 or
DT9806; full diagnostic table renders on Windows; CI on Linux/macOS
runs the discovery suite against `FakeDtolBackend`; header
verification entries exist in `docs/decisions.md` for every type
alias and every prototype in this phase.

### Phase 2 — Single-value analog input (DT9805 happy path)

**SDK functions bound:**
- `olDaSetDataFlow`
- `olDaSetChannelType`, `olDaSetChannelRange`, `olDaSetRange`
- `olDaSetGainListEntry` (single-channel form for spec'd gain)
- `olDaSetMultiSensorType` (must be issued BEFORE per-type setters on
  MULTI_SENSOR channels — see §8.5a)
- `olDaSetThermocoupleType`, `olDaSetReturnCjcTemperatureInStream`
- `olDaConfig`, `olDaGetSSState` (powers `DtolSession.state` — §8.13)
- `olDaGetSingleValue`, `olDaGetSingleFloat`
- `olDaGetSingleValueEx` (autoranging path)
- `olDaGetSingleValues`, `olDaGetSingleFloats` (simultaneous SH)
- `olDaGetCjcTemperature`, `olDaGetCjcTemperatures`
- `olDaCodeToVolts`, `olDaVoltsToCode` (oracles for vectorised path)

**Public surface:**
- `TaskSpec`, `ChannelSpec`, `AnalogInputBase`, `AnalogInputVoltage`,
  `ThermocoupleInput`, `Timing` (rate/clock only)
- `SubsystemState` enum + `DtolSession.state` property (§8.13)
- `SoftwareStart` only (full `TriggerSpec` hierarchy lands in Phase 3)
- `DtolSession.configure / start / stop / abort / poll / close /
  is_running / state`
- ~~`DtolSession.diag_read_reg / diag_write_reg`~~ — **descoped from
  v0.1** (B2 in [plan-hardware-functional.md](plan-hardware-functional.md)):
  `olDaReadDevReg`/`olDaWriteDevReg` are unbound and have no
  bench-verified DLL export. Raw access goes through the `raw_hdass` /
  `raw_hdrv` / `backend` escape hatches instead.
- `open_device()` factory
- `DtolManager.add / remove / get / poll` (single-device focus,
  per-board lock)
- Sync facade `Dtol.open_device(spec)`
- DT9805/DT9806 read-only hardware smoke tests
- MULTI_SENSOR ordering test: the fake backend enforces that
  `set_multi_sensor_type` precedes `set_thermocouple_type` /
  `set_range` / etc. on `IOType.MULTI_SENSOR` channels.

**Acceptance:** a script reads K-type thermocouples on a DT9805 and
prints temperatures; the same script passes type-check on Linux CI;
hardware smoke tests pass on the bench.

### Phase 3 — Continuous analog input + the §12.3.2 bridge

**SDK functions bound:**
- `olDaSetChannelListSize`, `olDaSetChannelListEntry`,
  `olDaSetChannelListEntryInhibit`
- `olDaSetGainListEntry` (list form)
- `olDaSetClockSource`, `olDaSetClockFrequency`, `olDaGetClockFrequency`,
  `olDaSetExternalClockDivider`
- `olDaSetTrigger`, `olDaSetTriggerThresholdChannel`,
  `olDaSetTriggerThresholdLevel`
- `olDaSetWrapMode`, `olDaSetDmaUsage`, `olDaSetStopOnError`
- `olDaSetWndHandle` (buffer-done mechanism — `olDaSetNotificationProcedure`
  is **not** bound; its callback never fires on V7.0.0.7, §12.3.2)
- `olDaStart`, `olDaStop`, `olDaAbort`, `olDaIsRunning`,
  `olDaGetQueueSize`
- `olDmCallocBuffer`, `olDmMallocBuffer`, `olDmReAllocBuffer`,
  `olDmFreeBuffer`
- `olDmGetBufferPtr`, `olDmGetBufferSize`, `olDmGetMaxSamples`,
  `olDmGetValidSamples`, `olDmGetDataWidth`, `olDmGetDataBits`
- `olDaPutBuffer`, `olDaGetBuffer`, `olDaFlushBuffers`
- `olDaCopyFromBuffer` (powers `DtolSession.read_inprocess` — gated on
  `OLSSC_SUP_INPROCESSFLUSH`; see §9.1 and the .NET
  `MoveFromBufferInprocess` analogue at UMOpenLayers.md:5933-5945)

**Public surface:**
- `DataFlow` enum (full), `BufferPlan`, `WrapMode`, `QueueStrategy`
- `BufferState` enum (§8.14) tracked per `RawBuffer` in the pool;
  use-after-free raises `DtolTaskStateError` immediately
- `TaskSpec.stop_on_error` (SDK-level, distinct from
  recorder-level `ErrorPolicy` — see §14.3 pairing table)
- `DtolSession.read_inprocess()` for low-latency partial-buffer
  drainage on devices where `CapabilitySet.supports_inprocess_flush()`
- `DtolSession.queued_buffer_dones` for runtime back-pressure
  monitoring (number of completed buffers waiting for the drainer)
- `ExternalDigitalStart`, `AnalogThresholdStart` triggers
- `record()` recorder driving the §12.3.2 callback bridge
- `record_polled()` recorder
- `DaqBlock`, `DaqReading`, `DaqSample`, `AcquisitionSummary`,
  `ErrorPolicy` (RAISE / RETURN / LOG_AND_CONTINUE),
  `OverflowPolicy`
- All six sinks (`InMemorySink`, `CsvSink`, `JsonlSink`, `SqliteSink`,
  `ParquetSink`, `PostgresSink`)
- `RawCountsSink` + replay tool (`dtollib.tools.replay_raw`)
- `dtol-capture` CLI
- Buffer-event ordering test suite (all 5 invariants enforced by
  `FakeDtolBackend`)
- Overrun / trigger-error / buffer-reused tests
- `stop_on_error=False` + `ErrorPolicy.LOG_AND_CONTINUE` pairing test:
  inject OVERRUN, assert the SDK keeps producing and the recorder
  logs without raising

**Acceptance:** 60-minute continuous 1 kHz acquisition on DT9805 drops
zero blocks with `BufferPlan(buffers=4)`; `RawCountsSink` round-trips losslessly;
deliberate consumer-pause test triggers `OLDA_WM_OVERRUN_ERROR` and the
recorder surfaces it correctly under each `ErrorPolicy`.

### Phase 4 — Outputs and digital I/O (DT9806)

**SDK functions bound:**
- `olDaPutSingleValue`, `olDaPutSingleValues`
- `olDmCopyToBuffer`, `olDmCopyFromBuffer`, `olDmCopyBuffer` (waveform output)
- `olDaSetSynchronousDigitalIOUsage`, `olDaSetDigitalIOListEntry`
- `olDaMute`, `olDaUnMute`

**Public surface:**
- `AnalogOutputVoltage`, `DigitalInputPort`, `DigitalOutputPort` (+ `DigitalLine`
  bit-views) specs
- `DtolSession.write` with `confirm=True` safety gate; `safe_min` /
  `safe_max` validation before SDK call
- Continuous AO waveform output path (`play()`) — software-complete and
  unit-tested against the fake. **Withdrawn on owned hardware** (B1 in
  [plan-hardware-functional.md](plan-hardware-functional.md)): the
  DT9806 D/A is single-value only (`SUP_CONTINUOUS=0`), so `play()`
  fails loud with `DtolCapabilityError`. Retargeted to a future
  continuous-DAC board.
- Hardware output tests with explicit `DTOLLIB_ENABLE_OUTPUT_TESTS=1`
  gate
- `dtol-read`, `dtol-info` CLIs

**Acceptance:** single-value AO/DO writes honour the `confirm=True`
safety gate on a DT9806; out-of-range values are rejected before any SDK
call. (The original "60 s continuous waveform, zero underruns" target is
withdrawn — see B1.)

### Phase 5 — Counter/timer, tachometer, simultaneous start

**SDK functions bound:**
- `olDaSetCTMode`, `olDaSetCTClockSource`, `olDaSetCTClockFrequency`
- `olDaSetGateType`, `olDaSetPulseType`, `olDaSetPulseWidth`
- `olDaSetMeasureStartEdge`, `olDaSetMeasureStopEdge`
- `olDaSetCascadeMode`
- `olDaReadEvents`, `olDaMeasureFrequency`
- `olDaSetTriggeredScanUsage`, `olDaSetMultiscanCount`,
  `olDaSetRetriggerMode`, `olDaSetRetrigger`,
  `olDaSetRetriggerFrequency`
- `olDaGetSSList`, `olDaPutDassToSSList`,
  `olDaSimultaneousPreStart`, `olDaSimultaneousStart`,
  `olDaReleaseSSList`

**Public surface:**
- `CounterEdgeCount`, `CounterFrequency`, `CounterEdgeToEdge`,
  `QuadratureDecoder`, `Tachometer` channel specs
- `PulseTrainOutput`, `OneShotOutput`, `RepetitiveOneShotOutput` specs
- `SubsystemType.TACHOMETER` (first-class)
- `RetriggerSpec` (triggered scan mode — `EXTRA` preferred)
- `DtolManager.start_synchronized` (full four-step pre-start + start
  sequence)
- Multi-subsystem composition tests (AI + counter on one DT9806)

**Hardware reconciliation (B4/B5 in
[plan-hardware-functional.md](plan-hardware-functional.md)):** the owned
DT9805/DT9806 expose **no** `OLSS_QUAD` / `OLSS_TACH` subsystem and the
C/T reports `SUP_SIMULTANEOUS_START=0`. `QuadratureDecoder` / `Tachometer`
and `CounterMode.MEASURE` are gated off via the runtime capability query
(raise `DtolCapabilityError`); all paths stay unit-tested against the
idealised fake. Real quadrature/tachometer and tight AI+C/T alignment
need hardware that reports those capabilities.

**Acceptance:** event-count (COUNT) and pulse-train (RATE) run on the
DT9806 C/T; `start_synchronized` builds and starts an SS-list (AI-only
where the AI subsystem reports `SUP_SIMULTANEOUS_START`). Quadrature/
tachometer/measure and AI+C/T one-sample-period alignment are out of
scope on owned hardware (B4/B5).

**Out of scope (deferred to v0.3+):** `DtolCollection` — the .NET
`OpenLayers.DeviceCollection` equivalent (§16.5). Phase 5's
`start_synchronized` covers the "two independent DT boards I want to
start together" case via the SDK simultaneous-start primitives.
`DtolCollection` is the separate "the SDK considers these one logical
device" abstraction (VIBbox-style chassis, DT Device Collection
Manager-defined collections), which adds master/slave semantics and a
distinct discovery surface. Deferred until a real collection-style rig
is in the lab.

### Phase 6 — Full multi-sensor coverage

**SDK functions bound:**
- `olDaSetRtdType`, `olDaSetRtdR0`, `olDaSetRtdA/B/C`
- `olDaSetThermistorA/B/C`
- `olDaSetIEPE`, `olDaSetExcitationCurrentSource`,
  `olDaSetExcitationCurrentValue`
- `olDaSetCouplingType`
- `olDaSetStrainExcitationVoltageSource`,
  `olDaSetStrainExcitationVoltage`, `olDaSetStrainShuntResistor`,
  `olDaSetStrainBridgeConfiguration`
- `olDaSetBridgeConfiguration`
- `olDaSetTransducerType`
- `olDaVoltsToStrain`, `olDaVoltsToBridgeBasedSensor`
- `olDaReadBridgeSensorHardwareTeds`,
  `olDaReadBridgeSensorVirtualTeds`,
  `olDaReadStrainGageHardwareTeds`,
  `olDaReadStrainGageVirtualTeds`

**Public surface:**
- `CurrentInput`, `ResistanceInput`, `RtdInput`, `ThermistorInput`,
  `IepeInput`, `StrainInput`, `BridgeInput` specs
- TEDS read helpers
- Application-side TC linearisation **only if** a real owned device
  needs it (deferred until then; `DtolCapabilityError` if missing)

### Phase 7 — Hardening

- Pre-trigger / about-trigger data flow modes (only if any owned
  hardware supports them — these are flagged "Legacy Devices" in the
  manual)
- Multi-board simultaneous start across two DT devices via Sync Bus
  (`olDaSetSyncMode`)
- `olDaAutoCalibrate` with destructive-test gate
- Long-duration stress tests (24-hour continuous AI runs)
- Lock-relaxation experiments: profile per-board vs per-HDASS lock
  contention with real load
- Performance work on the §12.3.2 path (lock-free SPSC variant if
  measurement shows queue contention)
- Public API freeze for v1.0 beta

### Future / not committed

- `DtolCollection` for VIBbox-style chassis and DT Device Collection
  Manager-defined collections (§16.5). Adds a separate
  `find_collections()` discovery path and a CollectionDevices /
  master/slave model that `DtolManager` is intentionally not.
- TimescaleDB sink variant for high-rate Postgres logging.
- HDF5 sink as an alternative to `.dt-raw`.
- Cross-instrument synchronisation primitives (DT external trigger
  shared with NI device on a common back-plane).
- Web dashboard (probably belongs in a separate package).

---

## 27. Risks and Mitigations

### Risk: The `capi` binding diverges from the SDK silently

**Mitigation:** Binding tests exercise every declared signature against
a stub DLL that re-exports the SDK header values. Adding a new function
without a corresponding signature test fails CI.

### Risk: The §12.3.2 callback bridge has the same hazards as §11.3.2

**Mitigation:** Port the `nidaqlib` bridge invariants table verbatim into
the `FakeDtolBackend`. Tests cover all five ordering invariants
(Register-before-Start, Stop-before-Unregister, etc.). Failing any one
ordering is a unit-test failure, not a hardware-day surprise.

### Risk: GC of the CFUNCTYPE callback wrapper crashes the driver

**Mitigation:** Same fix as `nidaqlib` — keep a strong reference on the
backend instance, keyed by `id(hdass)`, dropped only at
`unregister_notification`. A unit test exercises a register / GC-pressure
/ callback-fire sequence to catch regression.

### Risk: Buffer-pool sizing is hard to choose

**Mitigation:** `BufferPlan.buffers` defaults to 4 (matches QuickDAQ's
default). Document the relationship `buffers ≈ ceil(max_consumer_latency_s
* sample_rate_hz / samples_per_buffer)` in `streaming.md`. The recorder
reports `overruns_observed` in `AcquisitionSummary` — a single overrun
in a run is a signal to increase `buffers` or `samples_per_buffer`.

### Risk: Windows-only constrains the user base

**Mitigation:** Acknowledged. The SDK is Windows-only; there is no
remediation. The wheel installs on all platforms for type-checking and
ecosystem composition; the real backend is gated to Windows.

### Risk: Bitness mismatch (32-bit Python + 64-bit DLL) fails confusingly

**Mitigation:** `_loader.py` detects bitness with
`struct.calcsize('P')` and raises `DtolDependencyError` with the
expected DLL filename in the message, before `ctypes.WinDLL` raises a
less-friendly OSError.

### Risk: SDK errors are documented but classification is wrong

**Mitigation:** Classification table is a unit-tested mapping with one
test per documented error code range. PRs adding new codes must add a
test.

### Risk: `RawCountsSink` writes from the callback thread cause GIL
contention

**Mitigation:** Bench it. The write path is `np.ndarray.tofile(f)` on a
pre-opened file handle; this releases the GIL during the actual I/O.
If contention shows up, switch to a background writer thread with its
own `queue.SimpleQueue` (same shape as the drainer).

---

## 28. Public API Surface

```python
from dtollib import (
    # Discovery + capability
    BoardInfo,
    CapabilitySet,
    DeviceInfo,
    SubsystemInfo,
    SubsystemType,
    find_devices,

    # Spec primitives
    BufferPlan,
    ChannelType,
    ClockSource,
    CouplingType,
    DataFlow,
    Edge,
    Encoding,
    FilterType,
    QueueStrategy,
    RawLogging,
    TaskSpec,
    Timing,
    WrapMode,

    # Channels — v0.1
    AnalogInputVoltage,
    BridgeInput,
    IepeInput,
    RtdInput,
    StrainInput,
    ThermistorInput,
    ThermocoupleInput,
    ThermocoupleType,
    TemperatureUnit,
    CjcSource,

    # Channels — v0.2
    AnalogOutputVoltage,
    DigitalInputPort,
    DigitalOutputPort,
    DigitalLine,
    CounterEdgeCount,
    CounterFrequency,
    CounterEdgeToEdge,
    QuadratureDecoder,
    Tachometer,
    PulseTrainOutput,
    OneShotOutput,

    # Triggers
    AnalogThresholdStart,
    ExternalDigitalStart,
    ReferenceTrigger,
    RetriggerMode,
    RetriggerSpec,
    SoftwareStart,
    SyncBusStart,
    TriggerSpec,

    # Data models
    DaqBlock,
    DaqReading,
    DaqSample,

    # Session + manager
    DtolConfig,
    DtolManager,
    DtolSession,
    DeviceResult,
    config_from_env,
    open_device,

    # Metadata
    Recording,
    RunMetadata,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,

    # Errors
    ErrorContext,
    ErrorPolicy,
    DtolError,
    DtolBackendError,
    DtolBufferOverrunError,
    DtolBufferUnderrunError,
    DtolCapabilityError,
    DtolCapiError,
    DtolConfigurationError,
    DtolConfirmationRequiredError,
    DtolDependencyError,
    DtolReadError,
    DtolResourceError,
    DtolSinkError,
    DtolSinkDependencyError,
    DtolSinkSchemaError,
    DtolSinkWriteError,
    DtolTaskStateError,
    DtolTimeoutError,
    DtolTriggerError,
    DtolValidationError,
    DtolWriteError,

    __version__,
)

from dtollib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    PollSourceAdapter,
    SdkEventKind,
    record,
    record_polled,
)

from dtollib.sinks import (
    BlockSink,
    CsvSink,
    InMemorySink,
    JsonlSink,
    ParquetSink,
    PostgresSink,
    RawCountsSink,
    ReadingSink,
    SampleSink,
    SqliteSink,
    block_to_rows,
    pipe,
    pipe_blocks,
    reading_to_row,
)

from dtollib.testing import (
    FakeDtolBackend,
)

from dtollib.sync import (
    Dtol,
)

from dtollib.units import to_pint
```

---

## 29. README Positioning

Suggested README opening:

```markdown
# dtollib

Experiment-facing DT-Open Layers acquisition tools for Python.

`dtollib` is the missing Python binding for Data Translation USB DAQ
modules (DT9805, DT9806, and other DT-Open Layers devices). It wraps
the DataAcq SDK C DLL via `ctypes` and exposes a typed, async,
lifecycle-managed acquisition layer that fits the same scientific-
instrumentation ecosystem as [`alicatlib`], [`sartoriuslib`],
[`watlowlib`], and [`nidaqlib`].

Use `dtollib` when you want:

- declarative task specifications,
- consistent async/sync APIs,
- structured errors,
- block-oriented acquisition,
- raw-counts / Parquet / SQLite / Postgres / CSV / JSONL logging,
- hardware-free tests,
- and unified experiment workflows across DT DAQ, NI DAQ, flow
  controllers, balances, and temperature controllers.

## Platform

Windows only. The DataAcq SDK is Windows-only. The package installs on
Linux/macOS for type-checking and ecosystem composition, but the real
backend gates to Windows.

## Hardware tested

DT9805 (multi-sensor AI), DT9806 (AI + AO + DIO + C/T). Other
DT-Open-Layers devices should work but have not been verified.
```

---

## 30. Final Recommendation

Build `dtollib`.

Build the smallest thing that closes the most painful gap first:

1. ctypes binding for the AI subset of the SDK.
2. Typed `TaskSpec` / `DtolSession` / `record()` / `record_polled()`.
3. `BufferPlan` on `TaskSpec` + internal `BufferPool` + `_callback_bridge` (the §12.3.2 analogue).
4. Fake backend that enforces the same ordering invariants the real
   SDK does.
5. Six concrete sinks plus `RawCountsSink`.
6. Sync facade.
7. One excellent example combining DT9805 thermocouples, an Alicat
   MFC, and a Sartorius balance — all logging into one SQLite table.

That package adds a Python-shaped Data Translation story to the
ecosystem without fighting the SDK and without writing a clone of
QuickDAQ.

The core design principle:

> Wrap workflow, not capability. Treat the C binding as an internal
> seam, not as a public surface.

---

## 31. Open Questions

These are not blockers for the architecture, but they should be
answered before Phase 0 scaffolding begins. Most have a default the
plan currently assumes (called out in parentheses); the value of
asking is to confirm the default or replace it. Track answers in
`docs/decisions.md` as they are made — each answer either confirms the
parenthesised default or generates a single follow-up edit in this
document.

1. **Package name.** Is `dtollib` definitely the final published name,
   or would `datatranslationlib` (more discoverable to someone
   searching PyPI for "Data Translation") be preferred? (Default:
   `dtollib` — matches the `*lib` family convention.)
2. **Bitness for first bench.** Will the initial DT9805/DT9806
   bring-up run on 64-bit Python with 64-bit `oldaapi64.dll` /
   `olmem64.dll`? (Default: yes — 32-bit is supported by the loader
   but is not the primary CI or hardware target.)
3. **SDK headers as repo fixtures.** Are `OLDAAPI.H`, `OLMEM.H`,
   `OLERRORS.H`, `OLDADEFS.H`, and `Oldacfg.h` redistributable under the DT-Open
   Layers SDK license? If yes, commit them under
   `tests/fixtures/headers/` so the `scripts/gen_openlayers.py` diff
   CI check can run on every PR rather than only on maintainer
   machines. (Default: assume *no* until license confirmed; headers
   live on maintainer machines and the CI diff check is a manual
   pre-release step.)
4. **Actual board names.** What strings do your DT9805 and DT9806
   report in the Open Layers Control Panel? (`DT9805(00)` and
   `DT9806(00)` are placeholders in this doc — the real names go into
   the smoke-test env vars in §22.3 and the README examples in §7.)
5. **Primary v0.1 use case.** Is the first real experiment
   thermocouple temperature acquisition (TC channels via
   `olDaGetSingleFloat` on a hardware-linearising subsystem), plain
   voltage AI (`olDaGetSingleValue`), or both equally? (Default: TC
   priority — drives the Phase 2 happy-path scope, the §13.1
   thermocouple section, and the Quickstart example ordering in §23.)
6. **DT9806 AO in v0.1?** The plan defers AO to Phase 4 (v0.2). Is
   that right, or does a real near-term experiment need AO writes
   before continuous AI is stable? (Default: defer to v0.2 — keeps
   v0.1 free of safety-gate machinery while the input path stabilises.)
7. **Loopback wiring for output tests.** Will the bench have safe
   loopback wiring (AO0 → AI0, DO0 → DI0, CTR_OUT → CTR_IN) so that
   output tests can verify the written value without operator
   inspection, or do output tests run open-loop? (Affects Phase 4
   hardware-test design — closed-loop tests are much faster to add and
   far more reliable than open-loop visual checks.)
8. **`.dt-raw` replay ergonomics.** Which replay shape matters most
   for post-hoc analysis: iterator of `DaqBlock`s (matches
   `record()`'s shape so analysis code is portable between live and
   replay), bulk NumPy load (`np.ndarray` per channel for one-shot
   slicing), direct conversion to Parquet (interop with
   pandas/polars/duckdb), or all three? (Default: iterator first,
   NumPy bulk-load second in v0.1, Parquet converter as a CLI in v0.2.)
9. **Application-side TC linearisation.** ~~All currently-owned hardware
   linearises thermocouples on-board.~~ **Resolved 2026-05-28:** the
   DT9805/DT9806 do *not* linearise in firmware (UM9800 Table 26;
   `OLSSC_RETURNS_FLOATS = 0`, no `OLSSC_SUP_LINEARIZE_TC` in the SDK).
   The application-side NIST ITS-90 path is the **default and only** path
   for owned hardware, implemented in `tasks/session.py`
   (`_read_all_channels_app_side_tc`), `capi/conversion.py`
   (`code_to_input_volts`), and `utils.py` (Types K/J; T/E/R/S/B/N pending).
   See §8.5a and docs/decisions.md.
10. **TimescaleDB / HDF5 sinks.** The "future / not committed" list at
    the end of §26 includes TimescaleDB and HDF5 sinks. Are either of
    these on the actual roadmap for your lab, or are they speculative?
    (Default: speculative; reconsider after v0.2 ships and there is
    real usage data on which durable formats are reached for in
    practice.)
11. **`DtolCollection` priority.** §16.5 sketches a separate abstraction
    for DT-Open Layers device collections (VIBbox-style chassis, DT
    Device Collection Manager-defined collections), distinct from
    `DtolManager`. The .NET API puts collections in their own namespace
    (`OpenLayers.DeviceCollection`), but adding this requires a real
    collection-style rig to validate the master/slave semantics. Is
    there a near-term plan for such hardware in the lab? (Default:
    *no* — `DtolManager.start_synchronized` handles ad-hoc multi-board
    sync for now; defer `DtolCollection` to v0.3+ when a real rig
    arrives.)

---

## Appendix A: Comparison to `nidaqlib`

| Concern                          | `nidaqlib`                                  | `dtollib`                                            |
|----------------------------------|---------------------------------------------|------------------------------------------------------|
| Low-level binding owner          | NI (via `nidaqmx-python`)                   | **us** (`capi/` package via ctypes, two DLLs)        |
| Cross-platform                   | Linux + macOS + Windows                     | **Windows only**                                     |
| Buffer model                     | hidden by NI                                | **user-visible** via `BufferPlan` on `TaskSpec` (`buffers` knob) |
| Driver-side logging              | TDMS (`task.in_stream.configure_logging`)   | **RawCountsSink** (custom `.dt-raw` format)          |
| Callback bridge                  | §11.3.2 every-N-samples callback            | §12.3.2 notification procedure (`OLDA_WM_*`)         |
| Subsystem identity               | `nidaqmx.Task`                              | `HDASS` (DT-Open Layers subsystem)                   |
| Device identity                  | `Dev1`                                      | `DT9805(00)` (DT registry instance)                  |
| Discovery                        | `nidaqmx.system.System.local()`             | `olDaEnumBoardsEx`                                   |
| Error wrapping point             | one place (every backend method)            | one place (every backend method)                     |
| Escape hatch                     | `session.raw_task` → `nidaqmx.Task`         | `session.raw_hdass` + `session.backend.dll`          |

The two libraries are designed to be drop-in substitutes from the
ecosystem's perspective. A `record()`-based recorder script that uses
either one differs only in the import statement and the `TaskSpec`
construction.

---

## Appendix B: Example FakeDtolBackend

```python
class FakeDtolBackend:
    def __init__(
        self,
        *,
        block_sequences: Mapping[str, Sequence[np.ndarray]] | None = None,
        device_info: DeviceInfo | None = None,
        force_overrun_at_block: int | None = None,
    ) -> None:
        self._block_sequences = {
            name: list(values) for name, values in (block_sequences or {}).items()
        }
        self._device_info = device_info or _default_dt9805_info()
        self._force_overrun_at = force_overrun_at_block
        self.operations: list[tuple[str, object]] = []
        self._notify_callbacks: dict[int, Callable[[int], None]] = {}
        self._started: set[int] = set()
        self._configured: set[int] = set()
        self._buffer_pool: dict[int, list[np.ndarray]] = {}

    def initialize(self, device_name: str) -> int:
        self.operations.append(("initialize", device_name))
        return id(device_name)  # synthetic HDRVR

    def get_dass(self, hdrv: int, subsys_type: int, element: int) -> int:
        hdass = object()                                  # opaque
        self.operations.append(("get_dass", (subsys_type, element)))
        return id(hdass)

    def set_data_flow(self, hdass: int, mode: int) -> None:
        if hdass in self._started:
            raise self._sdk_error(0x0301, "cannot reconfigure running task")
        self.operations.append(("set_data_flow", mode))

    def commit(self, hdass: int) -> None:
        self.operations.append(("commit", hdass))
        self._configured.add(hdass)

    def register_notification(self, hdass: int, callback) -> int:
        if hdass in self._started:
            # SDK enforces register-before-start; the fake does too.
            raise self._sdk_error(200960,
                "register all events prior to starting the task")
        self._notify_callbacks[hdass] = callback
        self.operations.append(("register_notification", hdass))
        return hdass  # handle == hdass for the fake

    def unregister_notification(self, hdass: int, handle: int) -> None:
        if hdass in self._started:
            raise self._sdk_error(200986,
                "cannot unregister event on running task")
        self._notify_callbacks.pop(hdass, None)

    def start(self, hdass: int) -> None:
        if hdass not in self._configured:
            raise self._sdk_error(0x0301, "start before commit")
        self._started.add(hdass)
        self.operations.append(("start", hdass))

    def stop(self, hdass: int) -> None:
        self._started.discard(hdass)
        self.operations.append(("stop", hdass))

    # Test helper — synthesise a buffer-done callback.
    def fire_buffer_done(self, hdass: int) -> None:
        cb = self._notify_callbacks.get(hdass)
        if cb is None:
            raise RuntimeError("no notification registered")
        cb(_OLDA_WM_BUFFER_DONE)
```

---

## Appendix C: Example callback-bridge skeleton

```python
import queue
import ctypes
import anyio
import numpy as np
from anyio.from_thread import BlockingPortal

from dtollib.capi.constants import (
    OLDA_WM_BUFFER_DONE,
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_TRIGGER_ERROR,
)

_SENTINEL: object = object()


@asynccontextmanager
async def _callback_bridge(
    backend: DtolBackend,
    hdass: int,
    pool: BufferPool,
    *,
    chunk_size: int,
):
    """Owns the lifetime of the notification registration + drain task.

    Yields (rx, summary) — rx is the AsyncIterator[DaqBlock] for consumers.
    """
    tx_send, tx_recv = anyio.create_memory_object_stream[DaqBlock](max_buffer_size=16)
    chunk_q: queue.SimpleQueue = queue.SimpleQueue()
    drain_done = anyio.Event()
    overrun_count = 0
    block_index = 0

    def _on_notify(msg_id: int, _wparam: int, _lparam: int) -> int:
        # DRIVER THREAD. Minimal work, no asyncio.
        chunk_q.put_nowait((msg_id, time.monotonic_ns()))
        return 0

    handle = backend.register_notification(hdass, _on_notify)

    async def _drain() -> None:
        nonlocal overrun_count, block_index
        try:
            while True:
                item = await anyio.to_thread.run_sync(chunk_q.get)
                if item is _SENTINEL:
                    return
                msg_id, mono_ns = item
                if msg_id == OLDA_WM_BUFFER_DONE:
                    # Worker-thread pop from done queue + recycle to ready.
                    hbuf = await anyio.to_thread.run_sync(backend.get_buffer, hdass)
                    if hbuf is None:
                        continue
                    raw = await anyio.to_thread.run_sync(backend.get_buffer_ptr, hbuf)
                    block = _to_daq_block(raw, block_index, mono_ns)
                    await tx_send.send(block)
                    block_index += 1
                    await anyio.to_thread.run_sync(backend.put_buffer, hdass, hbuf)
                elif msg_id == OLDA_WM_OVERRUN_ERROR:
                    overrun_count += 1
                    # ErrorPolicy.RAISE: cancel the task group, propagate.
                    # ErrorPolicy.RETURN: emit an error block.
                    ...
                elif msg_id == OLDA_WM_TRIGGER_ERROR:
                    ...
        finally:
            drain_done.set()
            await tx_send.aclose()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            yield tx_recv, _summary_view(...)
    finally:
        # Shutdown ordering: stop → unregister → sentinel → drain-wait → close.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(backend.stop, hdass)
            backend.unregister_notification(hdass, handle)
            chunk_q.put_nowait(_SENTINEL)
            await drain_done.wait()
```

---

## Appendix D: DT9805 / DT9806 capability matrix (informational only)

**This table is planning context, not a runtime authority.** Per
Design Goal §4.12, the wrapper validates configuration against the
live `CapabilitySet` populated from `olDaGetDevCaps` /
`olDaGetSSCaps` / `olDaGetSSCapsEx` / `olDaEnumSSCaps` /
`olDaEnumChannelCaps`. The matrix below shapes scope decisions
(which channel specs to bind in v0.1, which CLIs to ship); it is
NOT consulted at session-construction time and is NOT used to
short-circuit the runtime capability query.

| Capability                            | DT9805 | DT9806 |
|---------------------------------------|:-:|:-:|
| A/D subsystem                         | ✓ | ✓ |
| Multi-sensor inputs (V / I / TC / RTD / Thermistor / Strain / Bridge / IEPE) | ✓ | ✓ |
| Simultaneous single-value AI          | ✓ | ✓ |
| Continuous AI                         | ✓ | ✓ |
| DMA                                   | ✓ | ✓ |
| D/A subsystem                         | ✗ | ✓ |
| DIN / DOUT subsystems                 | ✗ | ✓ |
| C/T subsystem                         | ✗ | ✓ |
| Quadrature decoder                    | ✗ | ✓ |

Firmware revisions are known to flip capability flags within the same
model number — for example, an `OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM`
flag that's true on one DT9805 revision and false on another. Trusting
this matrix over the runtime query is a class of bug.

---

## References

- `dasdk.md` — the DataAcq SDK User's Manual (full text)
- `dasdk_digest.md` — the technical reference digest used to scope this plan
- `nidaqlib/docs/design.md` — the sibling-package design document this
  plan inherits from
- `alicatlib`, `sartoriuslib`, `watlowlib` — sibling packages whose
  architecture this plan adheres to
- DataAcq SDK online help (`oldaapi.h`, `olmem.h`, `olerrors.h`,
  `olwin.h`) — authoritative source for signatures and constants
