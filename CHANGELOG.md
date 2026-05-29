# Changelog

All notable changes to `dtollib` are documented here. This project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions.

## [Unreleased]

## [0.1.0] - 2026-05-29

First public release. `dtollib` is an experiment-facing, async-first
DT-Open Layers acquisition layer for Data Translation DT9805/DT9806
hardware. Continuous analog input (`record()`), single-value analog/digital
output, counter/timer measurement, software thermocouple linearisation, and
five CLIs (`dtol-discover` / `dtol-diag` / `dtol-capture` / `dtol-read` /
`dtol-info`) are implemented and bench-verified against SDK V7.0.0.7.

### Changed (DIO port + bitmask model — **breaking**)

- **Digital I/O is now port-shaped, matching the SDK.** `DigitalOutputLine` /
  `DigitalInputLine` are **removed** and replaced by `DigitalOutputPort` /
  `DigitalInputPort` (whose `physical_channel` is the **port index**) plus
  `DigitalLine(bit=N, name=...)` bit-views. The DT9805/06 expose one 8-bit port
  per direction; the old per-line model addressed each line as its own SDK
  channel and failed on hardware with `ECODE 7 (Invalid Channel)` for any line
  ≥1, reaching only relay 0 (docs/bench-dio-ao.md §2D).
- **`DtolSession.write` packs lines into one port byte.** A write groups keys by
  port and issues one `olDaPutSingleValue` per port. Whole-port writes take an
  int byte (`write({"dout": 0b1010})`); per-line writes (`write({"relay0": True})`)
  merge into a per-port **shadow register** so untouched lines hold their last
  value. `poll()` surfaces the raw port byte plus one bool per declared line.
- **`CapabilitySet.resolution`** (`olDaGetResolution`) added — the port width for
  digital subsystems. `builder._validate_digital_port` rejects an out-of-range
  port index or a line bit beyond the port width at configure-time. The fake
  backend now models the single 8-bit port and raises ECODE 7 past it.

### Added (WS-AO — continuous analog output / waveform playback)

- **`play(session, source, *, confirm=False, error_policy=...)`** — the output
  mirror of `record()`. A hardware-clocked continuous-AO context manager that
  fills a buffer ring from a waveform `source` and yields the live
  `AcquisitionSummary`. `WrapMode.SINGLE` lays one period across the ring and
  loops it; `WrapMode.MULTIPLE` refills + re-queues each emptied buffer from an
  async iterator or `() -> ndarray | None` callable (`None` ends finite
  playback). Exported at the top level alongside the `PlaybackSource` alias.
- **§18 safety gate on every sample.** `play()` reuses `DtolSession.write`'s
  confirm gate via a shared validator (`tasks/_output_gate.py`): a sample
  outside the device range is a `DtolValidationError` (pre-seed, always); a
  sample outside the safe band or a `requires_confirm` channel without
  `confirm=True` is a `DtolConfirmationRequiredError`. Streamed chunks are
  validated as they are pulled.
- **Output buffer-pool fill mode.** `BufferPool` gained `direction=OUTPUT` with
  `fill()` / `seed_all()` and a `FILLED` buffer state, enforcing
  **Fill-before-Queue** (an output HBUF must be filled before it is queued or
  re-queued). The fake mirrors this on the D/A subsystem's `put_buffer`.
- **Output callback bridge.** New `backend/_output_callback_bridge.py` refills
  and re-queues completed buffers (vs the input bridge's copy-and-emit) and
  routes `OLDA_WM_UNDERRUN_ERROR` → `DtolBufferUnderrunError` per `ErrorPolicy`.
  The shared shutdown discipline (`Sentinel`, `DrainStop`, the SDK→asyncio
  event-kind map) was extracted to `backend/_bridge_common.py` so neither
  bridge copy-pastes it. Teardown order: mute (when the cap is verified) → stop
  → unregister → drain.
- Docs: new `docs/waveform-output.md`.
- **No current DT9805/06 board supports continuous AO.** Bench-confirmed
  (2026-05-28) that the DT9806 D/A is single-value only
  (`OLSSC_SUP_CONTINUOUS=0`); `play()` raises `DtolCapabilityError` on it (see
  "Fixed (hardware bring-up)"). The `play()` machinery is verified against the
  fake and is ready for a future streaming-DAC board. `play()` reads
  `supports_mute` from the subsystem capabilities (WS-B) and skips the pre-stop
  mute when the cap is false.

### Added (WS-B — output capability constants)

- Six DT9806 output-capability positions transcribed into `capi/constants.py`
  from `OLDADEFS.H`'s `olssc_tag` enum (`OLSSC_MAX_DIGITALIOLIST_VALUE=46`,
  `OLSSC_SUP_SYNCHRONOUS_DIGITALIO=50`, `OLSSC_SUP_WRPWAVEFORM=97`,
  `OLSSC_CURRENT_OUTPUTS=117`, `OLSSC_SUP_PUT_SINGLE_VALUES=118`,
  `OLSSC_SUP_MUTE=142`), each cross-checked against an already-verified
  neighbour. Wired into `CapabilitySet` + `query_capabilities` as
  `supports_mute`, `supports_wrp_waveform`, `supports_put_single_values`,
  `supports_synchronous_digitalio`, `current_outputs`, `max_digitaliolist_value`
  (all default off so the AO path degrades gracefully). `play()` now passes the
  real `supports_mute` through to the output bridge.
- **Header-verified, bench read-back pending (§1.4a).** Positions are confirmed
  by enum counting but not yet by a live `olDaGetSSCaps` read-back; see
  `docs/decisions.md` "Output capability positions (WS-B)".

### Added (WS-TEST / S0 — hardware test scaffolding + DA-event probe)

- `scripts/bench_probe_ao_wndhandle.py` — maintainer-only S0 spike confirming
  the DA subsystem's `OLDA_WM_*` cadence under the window-handle mechanism.
- `tests/hardware/test_dt9806_ao.py`, `test_dt9806_do.py`,
  `test_dt9806_waveform.py` — single-value AO/DO confirm-gate + loopback tests
  and the continuous-`play()` waveform recovery / 60 s zero-underrun soak. Gated
  by `DTOLLIB_ENABLE_OUTPUT_TESTS=1` + the `hardware_output` marker (loopback
  halves additionally by `DTOLLIB_LOOPBACK_AO0_AI0` / `DTOLLIB_LOOPBACK_DOUT_DIN`).

### Fixed (hardware bring-up — DT9806 output path, 2026-05-28)

- **Single-value AO `write()` was broken on hardware.** `add_channel` issued
  `olDaSetGainListEntry` for analog-output channels, but the DT9806 D/A has no
  programmable gain and rejects it with `OLNOTSUPPORTED` (ec=36), failing
  `configure()`. AO now tolerates that error (AI still sets the gain list to
  select its range); the single-value put writes the code directly. The AO
  confirm-gate hardware tests pass on the DT9806 after the fix.
- **`play()` now fails loud on a single-value-only D/A.** Bench-confirmed that
  the DT9806 D/A reports `OLSSC_SUP_CONTINUOUS=0` (no FIFO, no wrap modes) — it
  cannot stream waveforms. `play()` checks `capabilities.supports_continuous`
  after `configure()` and raises `DtolCapabilityError` pointing at `write()`,
  instead of dying mid-startup at `olDaConfig`. The `play()` software path stays
  unit-tested against the (idealised, continuous-capable) fake D/A.
- **WS-A0 confirmed on hardware.** `record()` continuous AI delivers blocks on a
  live DT9805 through the `olDaSetWndHandle` + message-pump bridge.
- **WS-B capability positions bench-verified** against a live `olDaGetSSCaps`
  read-back (`scripts/bench_probe_da_caps.py`); see `docs/decisions.md`.

### Fixed (docs)

- Reconciled `docs/design.md` §11.3 / §11.5 / §12.0 / §12.3.2 and the
  Phase-1/Phase-3 bound-function lists from the dead `NOTIFY_PROC` mechanism to
  the implemented `olDaSetWndHandle` + hidden message-window + pump-thread
  mechanism; the file tree now lists `_message_window.py`, `_bridge_common.py`,
  and `_output_callback_bridge.py`.

### Added (Phase 5 — counter/timer, tachometer, quadrature, simultaneous start)

- **Counter channel specs** — `CounterEdgeCount`, `CounterFrequency`,
  `CounterEdgeToEdge`, `QuadratureDecoder`, `Tachometer` (inputs) and
  `PulseTrainOutput`, `OneShotOutput`, `RepetitiveOneShotOutput` (outputs),
  plus the `CounterMode` / `GateType` / `PulseType` / `QuadratureDecodeMode`
  enums. All exported at the top level and registered with `channel_from_dict`.
- **`DtolSession.read_events()`** and **`DtolSession.measure_frequency()`** —
  on-demand counter reads packaged as `DaqReading` (joining siblings on
  `(device, t_mono_ns)`). Counter/quadrature/tachometer tasks route through a
  new `TaskBuilder.configure_counter` path (C/T mode set first).
- **`RetriggerSpec`** gained real fields (`mode`, `multiscan_count`,
  `frequency_hz`, `source`) and is now wired into the continuous builder via
  `olDaSetTriggeredScanUsage` / `olDaSetMultiscanCount` / `olDaSetRetriggerMode`.
- **`DtolManager.start_synchronized(names)`** — runs the four-step SDK
  simultaneous-start sequence (`olDaGetSSList` → `olDaPutDassToSSList` × N →
  `olDaSimultaneousPreStart` → `olDaSimultaneousStart`), always releasing the
  list. Single-board, single-backend scope; cross-board Sync Bus is Phase 7.
- **SDK bindings** — `olDaSetCTMode`, `olDaSetCTClockSource`,
  `olDaSetCTClockFrequency`, `olDaSetGateType`, `olDaSetPulseType`,
  `olDaSetPulseWidth`, `olDaSetMeasureStartEdge`/`StopEdge`,
  `olDaSetCascadeMode`, `olDaReadEvents`, `olDaMeasureFrequency`,
  `olDaSetTriggeredScanUsage`, `olDaSetMultiscanCount`, `olDaSetRetriggerMode`,
  `olDaSetRetrigger`, `olDaSetRetriggerFrequency`, `olDaGetSSList`,
  `olDaPutDassToSSList`, `olDaSimultaneousPreStart`, `olDaSimultaneousStart`,
  `olDaReleaseSSList`, with matching `OpenLayersApi` methods.
- **Backend** — `DtolBackend` Protocol + `DataAcqBackend` + `FakeDtolBackend`
  gain the counter/retrigger/simultaneous-start surface. The fake enforces
  C/T-mode-first ordering, counter-subsystem-only setters, RUNNING-before-read,
  and the SS-list ordering invariants; `make_fake_dt9806()` now exposes QUAD +
  TACH subsystems.
- Docs: new `docs/counter-timer.md`, `docs/synchronized.md`.
- **C/T selector constants (OQ-5a) — bench-verified** (2026-05-28, SDK
  V7.0.0.7). `OL_CTMODE_*` / `OL_GATE_*` / `OL_PLS_*` / `OL_EDGE_*` /
  `OL_CT_CASCADE`/`OL_CT_SINGLE` are transcribed from `OLDADEFS.H` (line cited
  per symbol) and read back against the live DT9805/06 C/T. The earlier
  provisional 1400-family was wrong in value *and* name (`OL_PULSETYPE_*` →
  `OL_PLS_*`; gates gained underscores). `olDaSetCascadeMode` takes a UINT
  selector, not a BOOL.
- **Quadrature / tachometer / MEASURE gated off (OQ-5b).** The DT9805/06 expose
  no quadrature or tachometer subsystem and the C/T reports `CTMODE_MEASURE` /
  `QUADRATURE_DECODER` as 0; `CounterMode.QUADRATURE` / `.TACHOMETER` /
  `.MEASURE` now raise `DtolCapabilityError` at configure time, driven by the
  runtime capability query. The fake reports support so the software path stays
  unit-tested. See `docs/decisions.md`.
- **Binding fixes:** `olDaSimultaneousPrestart` (header spelling, not
  `…PreStart`); the C/T clock uses the generic `olDaSetClockSource` /
  `olDaSetClockFrequency` (no `olDaSetCTClock*` export exists);
  `olDaMute` / `olDaUnMute` bound optionally (header-declared, not exported by
  V7.0.0.7).

### Added (Phase 4 — outputs and digital I/O, DT9806)

- **Output channel specs** — `AnalogOutputVoltage` (with `safe_min`/`safe_max`
  safe-band + `requires_confirm`), `DigitalOutputLine` (`safe_value` +
  `requires_confirm`), `DigitalInputLine`. All exported at the top level.
- **`channel_from_dict`** + a `kind → class` registry in `dtollib.channels`,
  reversing `ChannelSpec.to_dict` for all five channel kinds. (`to_dict` no
  longer deep-copies, fixing a `mappingproxy` pickling error.)
- **`DtolSession.write(values, *, confirm=False)`** — single-value AO / DO
  writes with the §18 safety gate: out-of-device-range → `DtolValidationError`
  (always); out-of-safe-band or `requires_confirm` without `confirm=True` →
  `DtolConfirmationRequiredError`. Validation is atomic and pre-SDK — one bad
  value writes nothing. Dispatches to simultaneous (`olDaPutSingleValues`) or
  per-channel (`olDaPutSingleValue`) writes on `supports_simultaneous_da`.
- **`open_device(..., confirm_start=...)`** is now wired: autostarting a task
  that drives a `requires_confirm` output raises `DtolConfirmationRequiredError`
  unless `confirm_start=True`.
- **SDK bindings** — `olDaPutSingleValue`, `olDaPutSingleValues`, `olDaMute`,
  `olDaUnMute`, `olDaSetSynchronousDigitalIOUsage`, `olDaSetDigitalIOListEntry`,
  `olDmCopyToBuffer`, `olDmCopyBuffer`, with matching `OpenLayersApi` methods.
  Signatures verified against the installed C headers (SDK V7.0.0.7) — see
  docs/decisions.md.
- **Backend output surface** — `DtolBackend` Protocol + `DataAcqBackend` +
  `FakeDtolBackend` gain `put_single_value`/`put_single_values`/`mute`/`unmute`/
  `set_synchronous_digital_io_usage`/`set_digital_io_list_entry`/`copy_to_buffer`/
  `copy_buffer`. The fake enforces output-subsystem-only writes, the
  simultaneous-D/A capability, and committed-state ordering.
- **`dtol-read`** and **`dtol-info`** CLIs — promoted from stubs.
  `dtol-read --board NAME --channel N --range MIN,MAX [--json]` does a one-shot
  scalar read; `dtol-info [--board NAME] [--json]` dumps the full per-board
  `CapabilitySet`.
- Docs: new `docs/safety.md`; `docs/channels.md` extended with AO / DIN / DOUT.

### Deferred (Phase 4, follow-up commit)

- **Continuous AO waveform output** (`play()` + the output callback bridge).
  Requires a threaded Win32 message-pump analog of the §12.3.2 input bridge and
  bench read-back on real DT9806 hardware to validate underrun/refill semantics
  — see docs/implementation-plan.md §6.5. `DtolSession.write` rejects continuous
  data-flow with a pointer to `play()`.

### Fixed (hardware bring-up — DT9805 / DT9806, SDK V7.0.0.7)

- **Continuous-mode `ErrorPolicy.RAISE` crash.** A sustained SDK overrun
  under `RAISE` segfaulted (or deadlocked) on real hardware: the drainer
  raised inside the anyio task group, racing the shielded SDK/pool
  teardown. The drainer now captures the error, unwinds cleanly, and the
  bridge re-raises it only after the ordered shutdown — surfacing a clean
  `DtolBufferOverrunError` instead of a `BaseExceptionGroup`.
- **Cancelled session leaked the subsystem.** `DtolSession.close()` is now
  shielded against cancellation, so a timed-out / cancelled `record()`
  still releases the HDASS and terminates the HDRVR (was leaving the A/D
  reserved — ECODE 20 "Subsystem in use").
- **Voltage input range on the DT9805/06.** `olDaSetChannelRange` is
  unsupported (ECODE 36) on these fixed-range boards; `add_channel` now
  falls back to subsystem-wide `olDaSetRange` (then native range + gain).
  Unblocks `AnalogInputVoltage` reads and `dtol-capture`.
- **`olDaEnumSSCaps` / `olDaEnumChannelCaps` callbacks.** Corrected the
  ctypes typedefs to the real SDK `CAPSPROC`/`CHANNELCAPSPROC` shapes
  (the value rides in the DBL params, not the first arg); `CapabilitySet`
  now reports real `ranges`/`gains` (e.g. DT9806 gains 1/10/100/500)
  instead of empty tuples.

### Added (Phase 1 — C boundary, discovery, diagnostics)

- `dtollib.capi` package — three-layer C boundary (prototypes →
  `OpenLayersApi` → `DataAcqBackend`) per docs/design.md §10.3.
- `dtollib.capi.loader.load_openlayers()` — two-DLL discovery
  (`oldaapi*.dll` + `olmem*.dll`) with explicit-path > env-var >
  default-path > bare-name resolution.  Pre-checks bitness before
  `WinDLL` is called.
- `dtollib.capi.types` — opaque handle aliases (`HDRVR`, `HDASS`,
  `HBUF`, `HLIST`, `HSSLIST`), pointer-sized `WPARAM` / `LPARAM`
  from `wintypes`, six SDK callback typedefs including the Phase-3
  `NOTIFY_PROC` (declared early so the typedef has exactly one home).
- `dtollib.capi.prototypes` — bound `argtypes` / `restype` on the
  15 Phase-1 SDK functions (per docs/design.md §26 Phase 1):
  versions, error-strings, board enumeration, device lifecycle,
  subsystem enumeration, capability queries.
- `dtollib.capi.constants` — SDK-side numeric IDs
  (`OLSS_*`, `OLSSC_*`, `OL_DF_*`, `OLDA_WM_*`), kept in a
  binding-internal namespace separate from the user-facing
  `dtollib.constants`.
- `dtollib.capi.errors` — ECODE → typed-exception classification via
  per-code table + range fallback (docs/design.md §17.4).  Single
  `check()` seam through which every SDK call routes; AST-level
  regression test asserts the invariant.
- `dtollib.capi.callbacks` — `SdkEventKind` enum and
  `event_kind_from_message` helper for the Phase-3 callback bridge;
  callback typedef re-exports.
- `dtollib.capi.api.OpenLayersApi` — typed wrapper exposing one
  method per Phase-1 SDK function with output-pointer extraction
  and `_check`-routed error wrapping.
- `dtollib.backend` package — `DtolBackend` Protocol,
  `DataAcqBackend` (real SDK, Phase-1 subset),
  `FakeDtolBackend` (cross-platform in-memory fake enforcing the
  same ordering and capability rules).
- `dtollib.system` package — `find_devices()`, `find_subsystems()`,
  immutable `BoardInfo` / `SubsystemInfo` / `DeviceInfo` dataclasses,
  `CapabilitySet` (typed view over the four SDK capability-query
  functions).
- `dtollib.testing` — `make_fake_dt9805()`, `make_fake_dt9806()`,
  `make_fake_backend()` with realistic capability sets.
- `dtollib.utils` — NIST ITS-90 forward + inverse polynomials for
  Type K and Type J thermocouples (others ship operating-range
  helpers only), CJC compensation, rectangular + delta strain
  rosette transforms.
- `dtol-diag` CLI — real implementation: DLL load + version report,
  board enumeration, common-failure diagnostics, `--json` output.
- `dtol-discover` CLI — real implementation: multi-board summary,
  `--board NAME` single-board drill-in, `--json` output.
- `scripts/gen_openlayers.py` — maintainer-only header-diff tool.
- Bench-confirmed on DT9805 + DT9806 against SDK V7.0.0.7 +
  olmem V2.00.01 — see docs/decisions.md for the verified findings.

### Added (Phase 0 scaffold)

- Phase 0 scaffold — package installs and imports cleanly on Windows,
  Linux, and macOS with no DataAcq SDK present.
- `DtolConfig` + `config_from_env` — process-wide settings with full
  env-var coercion (`DTOLLIB_DEFAULT_TIMEOUT_S`, `DTOLLIB_DEFAULT_BUFFERS`,
  `DTOLLIB_OLDAAPI_DLL`, `DTOLLIB_OLMEM_DLL`, ...).
- Full `DtolError` hierarchy + `ErrorContext` — every subclass from
  docs/design.md §17.3 declared, none raised by Phase 0 code.
- Public `StrEnum` surface — `DataFlow`, `SubsystemType`,
  `SubsystemState`, `BufferState`, `IOType`, `SensorStatus`, `Edge`,
  `WrapMode`, `QueueStrategy`, `ClockSource`, `RetriggerMode`.
- `SyncPortal` — blocking portal for the Phase 2 sync facade.
- Sink Protocols (`ReadingSink`, `BlockSink`) + `InMemorySink`.
- Streaming policy types (`ErrorPolicy`, `OverflowPolicy`,
  `AcquisitionSummary`, `Recording[T]`).
- CI lanes (lint, typecheck, test matrix on Win/Ubuntu/macOS × py3.13 +
  py3.14, build, docs, release).
- Five CLI entry-point stubs (`dtol-discover`, `dtol-diag`,
  `dtol-capture`, `dtol-read`, `dtol-info`) that print a
  "not yet implemented — see Phase N" message and exit 2.

### Not yet (deferred to later phases)

- `capi/` package and any SDK function binding. Phase 1.
- `DtolBackend` Protocol, `DataAcqBackend`, `FakeDtolBackend`. Phase 1.
- `find_devices()`, `dtol-discover` real output, `dtol-diag` real
  checks. Phase 1.
- `TaskSpec`, `ChannelSpec`, `ThermocoupleInput`, `open_device`,
  `DtolSession`, `DtolManager`, scalar `poll()`. Phase 2.
- Continuous AI, the §12.3.2 callback bridge, `BufferPlan`, `record()`,
  durable sinks. Phase 3.

[Unreleased]: https://github.com/GraysonBellamy/dtollib/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/GraysonBellamy/dtollib/releases/tag/v0.1.0
