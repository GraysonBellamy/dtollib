# Hardware-functional plan & spec reconciliation

**Status:** Canonical hardware-reality reference for `dtollib`.
**Created:** 2026-05-28 · **Bench:** DT9805(00) + DT9806(00), DataAcq SDK
`oldaapi64.dll` V7.0.0.7 / `olmem64.dll` V2.00.01, driver `Dt9800`.

This document is the single source of truth for *what the owned hardware can
actually do*, and how the original `design.md` / `implementation-plan.md`
acceptance criteria are reconciled against bench findings. Where this document
and the phase specs disagree about hardware behaviour, **this document wins**;
the phase specs describe the intended software contract and now link here for
the hardware envelope.

It supersedes the earlier per-topic handoff drafts
(`handoff-phase5-hardware.md`, `plan-continuous-ao.md`,
`handoff-continuous-ao.md`) — their surviving decisions are folded in below.
The line-by-line bench evidence lives in [decisions.md](decisions.md); this
page is the decision summary.

---

## 1. Hardware capability envelope (bench-confirmed)

| Subsystem | DT9805 | DT9806 | Notes |
|-----------|--------|--------|-------|
| A/D (`OLSS_AD`) | yes | yes | Fixed ±10 V, **gain-selected** per channel (no per-channel range — `olDaSetChannelRange` → ec 36). `returns_floats=0`, `supports_multisensor=0`, `supports_thermocouples=1`. Offset-binary 16-bit. Continuous = yes. |
| D/A (`OLSS_DA`) | — | yes | **Single-value only.** `SUP_CONTINUOUS=0`, all wrap modes 0, no FIFO. |
| DIN / DOUT | yes | yes | Single-value. **One 8-bit port = channel 0** (`num_channels=1`, `OLSSC_RESOLUTION=8`); the 8 lines are its bits (8 relays). Use `DigitalOutputPort`/`DigitalInputPort` + `DigitalLine` (port+bitmask), not per-line SDK channels — see §4. DT9806 A/D has synchronous-DIO + DIO-list value caps; D/A does not. |
| C/T (`OLSS_CT`) | 2×1 | 2×1 | COUNT / RATE / ONESHOT / ONESHOT_RPT + cascade + gate/pulse types. **No** MEASURE, UP_DOWN, CONT_MEASURE, FIXED_PULSE_WIDTH, QUADRATURE_DECODER, **SIMULTANEOUS_START**. |
| Quadrature (`OLSS_QUAD`) | **absent** | **absent** | `olDaGetDASS(OLSS_QUAD)` → ECODE 3. |
| Tachometer (`OLSS_TACH`) | **absent** | **absent** | `olDaGetDASS(OLSS_TACH)` → ECODE 3. |

The runtime **capability query is the only authority** for these flags. The
`FakeDtolBackend` models an *idealised* device (continuous DAC, quadrature,
tachometer, measure) so the software paths stay fully unit-tested even where
the owned hardware cannot exercise them.

---

## 2. Spec reconciliation (B1–B6)

### B1 — Continuous analog output: **withdrawn on owned hardware**

`design.md` §26 Phase 4 originally required "DT9806 waveform output runs for
60 s with zero underruns." The DT9806 D/A is single-value only (table above),
so continuous AO **cannot run on this board**. Decision:

- `play()` **fails loud** — after `configure()` it checks
  `capabilities.supports_continuous` and raises `DtolCapabilityError` pointing
  the caller at `DtolSession.write()`, rather than dying mid-startup at
  `olDaConfig`.
- The full `play()` software path stays unit-tested against `FakeDtolBackend`
  (`make_dt9806_ao_capabilities` models a streaming DAC) and remains correct
  for a future DT-Open Layers waveform-DAC board.
- The 60-s zero-underrun **hardware** DoD is withdrawn for DT9805/DT9806 and
  re-targeted to a future continuous-DAC board.

### B2 — `diag_read_reg` / `diag_write_reg`: **descoped from v0.1**

These map to `olDaReadDevReg` / `olDaWriteDevReg` (advanced raw-register
escape hatches). They are not bound, have no bench-verified DLL export on
V7.0.0.7, and require a device-specific register map to be useful or safe.
Decision: **descoped from the v0.1 session contract.** Users needing raw
register access use the documented `raw_hdass` / `raw_hdrv` / `backend`
escape hatches. If a concrete diagnostic need appears, they can be bound
on-presence (the `olDaMute` pattern) in a later phase.

### B3 — Direct buffer reads: `record()` is the contract

`record()` / `record_polled()` are the supported continuous-consumption API.

- `queued_buffer_dones` — **implemented** (`olDaGetQueueSize(OL_QUE_DONE)`,
  synchronous monitoring probe).
- `read_block` / `read_inprocess` — feasible (the buffer SDK functions are
  bound for `record()`) and **retained in the contract** as low-level
  conveniences; `read_inprocess` is gated on
  `CapabilitySet.supports_inprocess_flush()` and raises `DtolCapabilityError`
  otherwise. They are thin pulls over the same buffer-pool machinery `record()`
  drives — `record()` remains the recommended path.

### B4 — Quadrature / tachometer: **gated off (absent hardware)**

Neither board exposes `OLSS_QUAD` / `OLSS_TACH`. `CounterMode.QUADRATURE` /
`.TACHOMETER` / `.MEASURE` raise `DtolCapabilityError` from the runtime
capability query before any SDK call. The superseded "rebind to the
OLSS_QUAD/OLSS_TACH subsystem" plan is moot here. Fake path stays tested; real
acceptance needs different hardware.

### B5 — `start_synchronized` (AI + C/T): **alignment claim revised**

The C/T subsystem reports `OLSSC_SUP_SIMULTANEOUS_START=0` on both boards, so
the original "AI and C/T start within one sample period" hardware claim is
**not achievable on this hardware**. Decision:

- `DtolManager.start_synchronized` keeps its software contract (SS-list build,
  pre-start, start) and stays unit-tested against the fake.
- Hardware acceptance is revised to: AI-only multi-subsystem simultaneous start
  where the AI subsystem reports the capability; AI+C/T tight alignment is
  re-targeted to hardware that reports `SUP_SIMULTANEOUS_START=1`.

### B6 — Decisions-log verification status

The Phase-1 type-alias and prototype tables in [decisions.md](decisions.md)
predate the bench session that confirmed SDK **V7.0.0.7**, board names
`DT9805(00)`/`DT9806(00)`, and the corrected constant families. Those "pending"
rows are flipped to verified (or marked superseded) against V7.0.0.7 in the same
pass as this document.

---

## 3. Hardware acceptance — status and what is still owed

| Acceptance | Status |
|------------|--------|
| Phase 1 — discover / diag / `find_devices` on DT9805 + DT9806 | bench-confirmed (board names, 5/6 subsystems); to be encoded as gated tests |
| Phase 2 — single-value TC + voltage read | bench-confirmed on DT9806 (TC ch4/6, CJC ch0); to be encoded as a gated test |
| Phase 3 — continuous AI mechanism | bench-confirmed; 5-s fast guard in `tests/hardware/test_dt9805_continuous.py` |
| Phase 3 — **full 60-min 1 kHz zero-drop soak** | **owed** (longer maintainer run) |
| Phase 3 — deliberate-overrun + RawCountsSink soak | owed |
| Phase 4 — single-value AO / DO confirm-gate | **bench-confirmed + DMM-witnessed** (AO on DT9806; DO on both boards), 2026-05-29 — see §4 |
| Phase 4 — DOUT→DIN loopback | **PASS** (same-board shipped pytest + cross-board harness), 2026-05-29 — see §4 |
| Phase 4 — AO→AI loopback | **PASS** — accurate to ~2 mV in differential; single-ended carries a common-mode ground offset (see §4) |
| Phase 4 — continuous AO 60 s | **withdrawn** (B1) |
| Phase 5 — event-count (COUNT) with known TTL burst | owed |
| Phase 5 — pulse-train (RATE) frequency verify | owed |
| Phase 5 — AI+C/T simultaneous start | revised (B5); AI-only path owed |
| Phase 5 — quadrature / tachometer | out of scope on owned hardware (B4) |

---

## 4. DIN / DOUT / AO bench session (2026-05-29)

Full DMM/jumper validation of the single-value AO and digital-I/O paths on the
real DT9805(00) + DT9806(00). (Supersedes the now-removed working docs
`bench-dio-ao.md` operator checklist and `handoff-bench-dio-ao.md`; the
machine-readable trail is `bench_results.jsonl`.)

### Confirmed on hardware

- **AO (DT9806):** confirm-gate / out-of-range / safe-band all reject in software
  **and** DMM-witnessed as zero output movement on the rejected write (parked the
  latching DAC at a distinctive value, attempted the rejected write, confirmed no
  change). Accuracy/linearity across the full ±10 V range with **≤1 mV** true
  error (no gain tilt, no zero offset, no sign-encoding bug). Two AO channels are
  independent (no mux/reversal).
- **AO teardown — safe-state is "hold," not "zero":** the D/A **latches its last
  written value through `close()`** (no auto-zero). Callers must explicitly park
  AO at 0 V before disconnecting. `DtolSession.write` teardown / `ao-sweep` already
  park; document this guarantee for any AO consumer.
- **DOUT (both boards):** logic-high ≈ 4.5 V (DT9806 ~4.53, DT9805 ~4.48–4.57),
  low ≈ 0 V; full bit→relay mapping verified for all 8 lines (walking + alternating
  patterns); confirm-gate DMM-witnessed (rejected write leaves the line unchanged).
- **DIN:** DT9806 DIN0 reads both applied levels with correct bit mapping; DT9805
  DIN0 validated via the cross-board loopback. **Floating input reads HIGH**
  (DT9806 DIN0 fully disconnected → bit 0 = 1, stable across reads → internal
  pull-up). AI/DIN reads default to **single-ended**.
- **Loopback:** DOUT0→DIN0 **PASS** same-board (shipped gated pytest:
  `test_dt9806_do.py -m hardware_output`, 8 passed) and cross-board
  (9806 DOUT0→9805 DIN0). AO0→AI loopback reads accurately — **~2 mV error in
  differential mode**; a single-ended read carries a common-mode ground-reference
  offset (≈ −0.19 V cross-board, ≈ −2.12 V same-board) that differential rejects.

### Library findings

- **DIO is one 8-bit port (channel 0), not per-line SDK channels.** The 8 relays
  are the 8 bits of a single port. The original `DigitalOutputLine` /
  `DigitalInputLine` per-line classes modelled each line as its own SDK channel —
  broken on this hardware (only line 0 reachable; lines ≥1 → ECODE 7 Invalid
  Channel). **Resolved**: replaced by the `DigitalOutputPort` / `DigitalInputPort`
  + `DigitalLine` port+bitmask model (whole-byte write with a per-port shadow
  register that merges partial per-line writes; reads surface the raw byte plus
  one bool per declared line). Bench harness + `test_dt9806_do.py` migrated.
- **Single-value `AnalogInputVoltage.poll()` returned the raw code, not volts.**
  `code_to_volts` was applied only on the thermocouple path. **Resolved** via a
  shared `_code_to_volts` helper in `DtolSession._read_all_channels` (converts at
  the gain the code was read at); bench-verified (`poll` matches the SDK oracle).

### Outstanding / notes

- `test_ao0_to_ai0_loopback_recovers_value` defaults its AI channel to
  single-ended, so on a bare jumper it carries the common-mode offset and can
  exceed its 0.10 V tolerance. Pass it reliably with a **differential** AI config
  or by commoning AO-return ↔ AI-LLGND. Not a code bug.
- **Not run:** per-line DIN input mapping for lines 1–7 (only DIN0 proven; the
  other input pins were occupied — close it with a DOUT0–7→DIN0–7 bus + pattern),
  the DT9806 same-board AO→AI single-ended offset diagnosis.
