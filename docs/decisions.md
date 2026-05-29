# Design decisions log

A chronological record of one-line decisions and verification notes —
type-alias confirmations, prototype `argtypes` / `restype` checks
against installed SDK headers, and resolved open questions from
design.md §31.

## Phase 0 (2026-05-28)

- **`ErrorContext.sdk_event_kind`.** Annotated as `object | None` in
  Phase 0 because `SdkEventKind` (Phase 3 enum) does not exist yet.
  Tightens to `SdkEventKind | None` when Phase 3 ships the enum; no
  ecosystem consumer should subclass-test against the field type in
  Phase 0, so the workaround is invisible to users.
- **Forward placeholder dataclasses.** `DaqReading` and `DaqBlock` are
  declared as empty classes in `dtollib.tasks.models` so the sink
  Protocol `TYPE_CHECKING` imports in `dtollib.sinks.base` and
  `dtollib.sinks.memory` resolve under strict type-checking. The
  full frozen-dataclass shapes (design.md §8.9, §8.10) land in
  Phase 2 / Phase 3 and replace these stubs in the same file.
- **CLI stubs in Phase 0.** All five `dtol-*` entry points resolve
  to a stub `main()` that prints "not yet implemented — see Phase N"
  and exits 2. Avoids `pip install dtollib` failing with an
  ImportError on a missing CLI module reference; users get a
  precise diagnostic instead.
- **No `hardware.yml` workflow in Phase 0.** A present-but-skipped
  workflow file clutters the PR-status checks. Phase 1 creates it
  when the bench can actually run it. CONTRIBUTING.md documents
  the deferral.

## Phase 1 (2026-05-28)

### Architectural decisions

- **`capi/` lives separately from `backend/`.** The three-layer C boundary
  (design.md §10.3) keeps raw ctypes (`capi/prototypes.py`), output-pointer
  extraction + error wrapping (`capi/api.py :: OpenLayersApi`), and
  session orchestration (`backend/dataacq.py :: DataAcqBackend`) as
  three independently-testable layers.
- **Single `_check` point.** ECODE → typed-exception classification
  runs once, in `DataAcqBackend._check` (via `capi.errors.check`). No
  other layer raises `DtolCapiError`. AST-level test
  (`tests/unit/test_api_all_methods_check.py`) asserts every public
  `OpenLayersApi` method body contains a `_check(...)` call so the
  gate cannot be silently bypassed.
- **`NOTIFY_PROC` typedef lands in Phase 1 even though
  `olDaSetNotificationProcedure` binds in Phase 3.** Locking the
  ctypes signature shape here (`WINFUNCTYPE`, pointer-sized `WPARAM`/
  `LPARAM` from `ctypes.wintypes`) means the Phase 3 callback bridge
  inherits a verified type instead of inventing one. The Phase 1
  regression test catches the §11.5 truncation hazard *before* it
  manifests as silent data corruption.
- **`FakeDtolBackend` invariant table is a Phase 1 deliverable.**
  The fake enforces the same ordering rules as the real SDK
  (subsystem state transitions, capability gates) so unit tests catch
  the same bugs hardware would. The Phase 1 fake covers only the
  Phase 1 surface (init/terminate, enum, capability query); each
  subsequent phase extends it monotonically.

### Type-alias verifications (capi/types.py)

These are the opaque-handle aliases. All are `c_void_p` on both 32- and
64-bit Windows; `ECODE` / `OLSTATUS` are `c_ulong` (matches
`typedef unsigned long OLSTATUS;` per dasdk_digest.md §8).

| Alias      | ctypes               | Header source           | Bench-verified? |
|------------|----------------------|--------------------------|------------------|
| `HDRVR`    | `c_void_p`           | `OLTYPES.H` / `OLDAAPI.H` | pending          |
| `HDASS`    | `c_void_p`           | `OLDAAPI.H`              | pending          |
| `HBUF`     | `c_void_p`           | `OLMEM.H`                | pending          |
| `HLIST`    | `c_void_p`           | `OLMEM.H`                | pending          |
| `HSSLIST`  | `c_void_p`           | `OLDAAPI.H`              | pending          |
| `ECODE`    | `c_ulong`            | `OLERRORS.H`             | pending          |
| `OLSTATUS` | `c_ulong` (alias)    | `OLERRORS.H`             | pending          |

Each row is *bench-verified* by reading the installed header on a
maintainer Windows machine and confirming the typedef matches.
Verification fills in this table with `verified` + the SDK version,
on the same commit that flips the field from pending to verified.

**Status (2026-05-28, SDK V7.0.0.7):** all seven aliases are
**functionally verified** — every alias is exercised on every
`olDaInitialize` / `olDaEnumSubSystems` / `olDaGetDASS` /
`olDaGetSSCaps` / buffer call on the live DT9805(00) and DT9806(00),
and the binding loads, enumerates, acquires subsystems, reads single
values, and runs continuous AI without a handle-shape error. The
per-row "pending" markers above denote only the remaining formality of
a line-by-line `OLTYPES.H` transcription audit, not a functional gap.

### Phase-1 SDK function verifications (capi/prototypes.py)

The 15 Phase-1 functions per design.md §26. Each row carries the
expected `argtypes` and `restype` derived from `OLDAAPI.H` /
`OLMEM.H`. The "Bench-verified?" column flips to the SDK version
once a maintainer has confirmed it against the installed header.

**Status (2026-05-28, SDK V7.0.0.7):** all 15 functions are
**functionally verified** on the live boards — version/error-string,
board + subsystem enumeration, init/terminate, DASS acquire/release,
and the four capability-query calls all return correct data through the
binding (see the bench-findings sections below, where the callback-proc
signatures for `olDaEnumSSCaps`/`olDaEnumChannelCaps` and the
`olDaEnumBoardsEx` typedef were corrected against `OLDAAPI.H`). The
per-row "pending" markers denote the remaining line-by-line header
transcription audit, not a functional gap.

| Function                  | argtypes                                                         | restype  | Source DLL | Bench-verified? |
|---------------------------|------------------------------------------------------------------|----------|------------|------------------|
| `olDaGetVersion`          | `(c_char_p, c_uint)`                                             | `ECODE`  | oldaapi    | pending          |
| `olDmGetVersion`          | `(c_char_p, c_uint)`                                             | `ECODE`  | olmem      | pending          |
| `olDaGetErrorString`      | `(ECODE, c_char_p, c_uint)`                                      | `ECODE`  | oldaapi    | pending          |
| `olDmGetErrorString`      | `(ECODE, c_char_p, c_uint)`                                      | `ECODE`  | olmem      | pending          |
| `olDaEnumBoards`          | `(BOARD_ENUM_PROC, LPARAM)`                                      | `ECODE`  | oldaapi    | pending          |
| `olDaEnumBoardsEx`        | `(BOARD_ENUM_EX_PROC, LPARAM)`                                   | `ECODE`  | oldaapi    | pending          |
| `olDaGetBoardInfo`        | `(c_char_p, c_char_p, c_uint, c_char_p, c_uint)`                 | `ECODE`  | oldaapi    | pending          |
| `olDaInitialize`          | `(c_char_p, POINTER(HDRVR))`                                     | `ECODE`  | oldaapi    | pending          |
| `olDaTerminate`           | `(HDRVR,)`                                                       | `ECODE`  | oldaapi    | pending          |
| `olDaEnumSubSystems`      | `(HDRVR, SS_ENUM_PROC, LPARAM)`                                  | `ECODE`  | oldaapi    | pending          |
| `olDaGetDevCaps`          | `(HDRVR, c_uint, c_uint, POINTER(c_ulong))`                      | `ECODE`  | oldaapi    | pending          |
| `olDaGetDASS`             | `(HDRVR, c_uint, c_uint, POINTER(HDASS))`                        | `ECODE`  | oldaapi    | pending          |
| `olDaReleaseDASS`         | `(HDASS,)`                                                       | `ECODE`  | oldaapi    | pending          |
| `olDaGetSSCaps`           | `(HDASS, c_uint, POINTER(c_ulong))`                              | `ECODE`  | oldaapi    | pending          |
| `olDaGetSSCapsEx`         | `(HDASS, c_uint, POINTER(c_double))`                             | `ECODE`  | oldaapi    | pending          |
| `olDaEnumSSCaps`          | `(HDASS, c_uint, SS_CAP_ENUM_PROC, LPARAM)`                      | `ECODE`  | oldaapi    | pending          |
| `olDaEnumChannelCaps`     | `(HDASS, c_uint, c_uint, CHAN_CAP_ENUM_PROC, LPARAM)`            | `ECODE`  | oldaapi    | pending          |

The exact callback-proc signatures (`BOARD_ENUM_PROC`,
`SS_ENUM_PROC`, `SS_CAP_ENUM_PROC`, `CHAN_CAP_ENUM_PROC`) are
locked in `capi/callbacks.py`; each is a `WINFUNCTYPE` returning
`c_int` (BOOL — TRUE continues enumeration, FALSE stops) so the
enumeration cannot be silently truncated by a mistakenly-`c_long`
WPARAM-style argument (see "NOTIFY_PROC typedef" note above).

### Constant values (capi/constants.py)

Subsystem-type IDs (`OLSS_*`) and capability-flag IDs
(`OLSSC_*`, `OL_ENUM_*`) are transcribed from `OLDADEFS.H` /
`OLDAAPI.H`. The published DT-Open Layers C++ examples set the
following stable values, and the verification gate confirms each
against the installed header before Phase 1 closes:

```
OLSS_AD   = 0      OLSS_DOUT = 3      OLSS_QUAD = 6
OLSS_DA   = 1      OLSS_SRL  = 4      OLSS_TACH = 7
OLSS_DIN  = 2      OLSS_CT   = 5
```

Capability-flag IDs are header-stable but their absolute integer
values vary across SDK revisions. The Phase 1 binding never hard-codes
a capability *value*; it always queries via `olDaGetSSCaps` /
`olDaGetSSCapsEx` and *interprets* the returned value. The names
`OLSSC_SUP_SINGLEVALUE`, `OLSSC_SUP_CONTINUOUS`,
`OLSSC_SUP_SIMULTANEOUS_SH`, `OLSSC_SUP_MULTISENSOR`,
`OLSSC_RETURNS_FLOATS`, `OLSSC_NUMCHANNELS`, `OLSSC_CGLDEPTH`,
`OLSSCE_MAX_THROUGHPUT` are documented in design.md §11.4.

### Open-question resolutions

- **Open Question 2 (bitness):** confirmed 64-bit. The loader supports
  32-bit but the bench, CI, and docs assume `oldaapi64.dll` /
  `olmem64.dll`.
- **Open Question 3 (SDK headers as fixtures):** *deferred* —
  `scripts/gen_openlayers.py` runs on the maintainer Windows machine
  only. The headers are not redistributed in this repo. CI does not
  run the diff check.
- **Open Question 4 (actual board name strings):** **RESOLVED**
  (bench 2026-05-28, SDK V7.0.0.7) — `BoardInfo.name` is `"DT9805(00)"`
  and `"DT9806(00)"`; the parenthesised instance suffix is part of the
  name. Driver name for both is `"Dt9800"` (shared umbrella driver).
  See the bench-findings section below.

### Bench findings (2026-05-28 — initial Phase 1 smoke against installed SDK)

First smoke against an installed DataAcq SDK + connected DT9805 +
DT9806 produced the following observations.  These do **not** block
Phase 1 acceptance (the binding mechanically works: SDK loads,
versions report, boards enumerate, subsystems acquire) but they
**do** flag specific values in this file that need cross-checking
against `OLDADEFS.H` on the maintainer machine before being
declared `verified`:

- **SDK versions on bench:** oldaapi `V7.0.0.7`, olmem `V2.00.01`.
  These are the versions all subsequent Phase 1 verifications cite.
- **Board names confirmed as `"DT9805(00)"` and `"DT9806(00)"`**
  (resolves Open Question 4 — the parenthesised instance suffix is
  part of the name).  Driver name for both is `"Dt9800"` (umbrella
  driver shared between DT9805 and DT9806).
- **`OLSSC_*` integer IDs need re-verification.**  Probing each
  Phase-1 capability flag against the real subsystems yielded
  several non-sensible results:
  - `OLSSC_NUMCHANNELS` (placeholder `0x0100`) and `OLSSC_CGLDEPTH`
    (`0x0101`) returned `0` for every subsystem — these are clearly
    the wrong integer IDs.  Real values are lower (~`0x0001..0x000F`
    range in published examples).
  - `OLSSC_SUP_MULTISENSOR` (placeholder `0x0007`) returned `True`
    on DIN/DOUT/CT subsystems — these subsystems cannot be
    multi-sensor, so `0x0007` is hitting a different cap flag.
  - Conversely, `OLSSC_RETURNS_FLOATS` (placeholder `0x000E`)
    returned `False` for the DT9805 multi-sensor AI, which is
    contrary to the SDK manual's documentation.
  - Net: every `OLSSC_*` value in `capi/constants.py` must be
    confirmed against the installed `OLDADEFS.H` before Phase 1
    closes.  Until then, the Phase 1 binding is "loads and
    enumerates" but capability-flag *interpretation* is
    provisional.

- **DT9805 reports five subsystems** (not just AD as the manual
  suggests): AD#0, DIN#0, DOUT#0, CT#0, CT#1.  The CT, DIN, DOUT
  subsystems may be stub/non-functional but they exist in the
  enumeration.
- **DT9806 reports six subsystems**: AD#0, AO#0, DIN#0, DOUT#0,
  CT#0, CT#1.
- **`olDaEnumBoardsEx` instance value drifts between calls**
  (`660` then `588`).  Suggests the callback signature or the
  argument *order* in `BOARD_ENUM_EX_PROC` is not quite right —
  the documented order in the SDK header needs to be confirmed.
  The Phase 1 fallback through `olDaEnumBoards` +
  `olDaGetBoardInfo` is unaffected.

These findings turn into action items for the verification pass:

1. Open `OLDADEFS.H` and confirm every `OLSSC_*` integer.  Update
   `capi/constants.py`; re-run `dtol-discover` to re-verify.
2. Open `OLDAAPI.H` and confirm the exact argument order +
   types of `BOARD_ENUM_EX_PROC` /  the equivalent SDK typedef
   (likely `BOARDPROCEX`).  Update `capi/types.py`.
3. Flip each row in the type-alias / Phase-1-prototype tables
   above from "pending" to `verified — SDK V7.0.0.7 2026-MM-DD`
   on the same commit that lands the corrected values.

### Bench-verified constants (2026-05-28, SDK V7.0.0.7)

Action item 1 resolved.  Headers consulted on the maintainer
machine at
``%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\OLDADEFS.H``
(C, the file the actual ``oldaapi64.dll`` was built against) and
``%ProgramFiles(x86)%\Data Translation\Win32\DTx-EZ\Include\OLDADEFS.bas``
(VB, OLDER, sometimes diverges).

The earlier dtollib revisions transcribed from the VB binding
and from the design.md's hand-curated table, both of which use
the older 0/1/2 family for ``OL_DF_*``, ``OL_WRP_*``,
``OL_QUE_*``, ``OL_TRG_*``, ``OL_CLK_*``.  The actual SDK uses an
offset-by-100 family for these enums; passing 0/1/2 yields
``OLBADDATAFLOW`` / ``OLBADWRAPMODE`` / ``OLBADQUEUE`` / etc.

Confirmed by bench probe (``scripts/bench_probe_continuous.py``):

| Enum            | Value range in installed SDK   | Old (wrong) dtollib value |
|-----------------|---------------------------------|----------------------------|
| ``OL_CHNT_*``   | 100, 101                       | (was correct: 0, 1)        |
| ``OL_ENC_*``    | 200, 201                       | (was correct: 0, 1)        |
| ``OL_TRG_SOFT`` family       | 300–306             | 0–7                        |
| ``OL_CLK_*``    | 400, 401, 402                   | 0, 1, 2                    |
| ``OL_DF_*``     | 800–805                         | 0, 1, 2, 3                 |
| ``OL_WRP_*``    | 1000, 1001, 1002                | 0, 2, 1 *(also swapped)*   |
| ``OL_QUE_*``    | 1100, 1101, 1102                | 0, 2, 1 *(also swapped)*   |
| ``OL_TRG_THRESH*`` / SYNCBUS | 1200, 1201, 1202    | 2, 3, 4                    |
| ``OL_RETRIG_*`` | 1300–1302                       | 0–2                        |
| ``OL_THERMOCOUPLE_TYPE_*``   | 1500–1508          | (deferred to Phase 4)      |

OLSSC_* indices: every value in OLDADEFS.H ``olssc_tag`` enum is
its zero-based position in the enum declaration order, **not** a
bitfield.  Bench-confirmed for the DT9805 AD subsystem:

| Index | Cap                       | DT9805 AD value           |
|-------|---------------------------|---------------------------|
| 0     | ``OLSSC_MAXSECHANS``      | 16 (single-ended chans)   |
| 1     | ``OLSSC_MAXDICHANS``      | 8  (differential chans)   |
| 2     | ``OLSSC_CGLDEPTH``        | 32                        |
| 4     | ``OLSSC_NUMGAINS``        | 4                         |
| 6     | ``OLSSC_NUMDMACHANS``     | 0                         |
| 7     | ``OLSSC_NUMCHANNELS``     | 17                        |
| 16    | ``OLSSC_SUP_SOFTTRIG``    | 1 (yes)                   |
| 24    | ``OLSSC_SUP_INTCLOCK``    | 1 (yes)                   |
| 35    | ``OLSSC_SUP_CONTINUOUS``  | 1 (yes)                   |
| 36    | ``OLSSC_SUP_SINGLEVALUE`` | 1 (yes)                   |
| 38    | ``OLSSC_SUP_WRPMULTIPLE`` | 1 (yes)                   |
| 39    | ``OLSSC_SUP_WRPSINGLE``   | 1 (yes)                   |
| 61    | ``OLSSCE_MAXTHROUGHPUT``  | 50000.0 Hz (DBL)          |
| 111   | ``OLSSC_SUP_THERMOCOUPLES`` | 1 (yes)                 |
| 116   | ``OLSSC_RETURNS_FLOATS``  | 0 (codes, not floats)     |
| 143   | ``OLSSC_SUP_MULTISENSOR`` | 0 (NO — this is a TC-only module) |

**Implication**: the DT9805 AD subsystem on this hardware is a
**thermocouple-input module**, not a generic multi-sensor module.
Earlier dtollib revisions probing ``OLSSC_SUP_MULTISENSOR`` at the
wrong index (0x0007) got ``17`` back (which is ``OLSSC_NUMCHANNELS``)
and read it as ``True``, leading the builder to call
``olDaSetMultiSensorType`` per channel — which the SDK rejects
with ``OLNOTSUPPORTED`` on this hardware.  With the corrected
index ``143``, the value is ``0`` and the multi-sensor codepath is
correctly skipped.

### Bench-verified continuous-mode setup (2026-05-28)

The Phase-3 ``configure_continuous`` sequence has been validated
end-to-end on the DT9805(00) bench, with these prerequisites
beyond what design.md §12.3 originally listed:

1. ``olDaSetDmaUsage(min(1, NUMDMACHANS))`` MUST be called even
   when ``NUMDMACHANS == 0`` — see ``IepContAdc.c`` / ``ThermoADC.C``.
2. ``olDaConfig`` is called TWICE in the canonical sequence — once
   after channel-list setup, then **again** after
   ``olDaSetWndHandle``.  ``ThermoADC.C`` does exactly this; the
   second config wires the window-handle into the SDK's internal
   buffer-rotation state machine.
3. **``olDaSetWndHandle`` + Win32 message pump is the only
   reliable buffer-done notification path on this SDK**.
   ``olDaSetNotificationProcedure`` returns ``OLNOERROR`` but the
   ``OLNOTIFYPROC`` callback never fires on the DT9805 — even when
   ``GetMessage`` / ``PeekMessage`` is running on the same thread.
   The Phase-3 callback bridge needs to be rewritten to create a
   message-only window (HWND_MESSAGE parent), install a
   ``WNDPROC`` that catches ``OLDA_WM_BUFFER_DONE`` /
   ``OLDA_WM_BUFFER_REUSED`` / ``OLDA_WM_QUEUE_DONE`` /
   ``OLDA_WM_OVERRUN_ERROR``, and pump messages from the
   acquisition thread.

With (1) + (2) + (3) in place, the DT9805 AD subsystem produces
``OLDA_WM_BUFFER_DONE`` messages at the expected cadence
(1000 Hz × 8 channels → 1 buffer/sec for 1000-sample buffers).
Verified at ``t=1.5s`` and ``t=2.0s`` in the
``scripts/bench_probe_wndhandle.py`` output dated 2026-05-28.

#### Implemented in `src/` (WS-A0, 2026-05-28)

The production code now uses this mechanism; ``olDaSetNotificationProcedure``
and the ``NOTIFY_PROC`` typedef were **deleted** (no fallback path).

- ``olDaSetWndHandle`` — bound in ``capi/prototypes.py`` with
  ``argtypes = [HDASS, HWND, LPARAM]``, ``restype = ECODE``; verified against
  ``OLDAAPI.H`` rev **SDK V7.0.0.7** (2026-05-28). Wrapped as
  ``OpenLayersApi.set_wnd_handle(hdass, hwnd, context=0)``. A new ``HWND``
  ctypes alias (``wintypes.HWND``) lives in ``capi/types.py``.
- ``backend/_message_window.py`` owns all ``user32`` / ``kernel32`` calls: a
  process-wide cached window class (atom cached so repeats don't leak), one
  ``MessageWindow`` per HDASS whose dedicated pump thread creates+owns a
  hidden ``HWND_MESSAGE`` window and runs ``GetMessage``/``DispatchMessage``,
  and a pinned shared ``WNDPROC`` routing each ``OLDA_WM_*`` to the bridge
  callback. ``unregister`` posts ``WM_QUIT`` (``PostThreadMessageA``), joins
  the thread, and destroys the window.
- The two ``olDaConfig`` calls are distinct backend transitions: ``commit``
  (config #1) and ``arm`` (config #2). ``record()`` ordering is
  ``commit → register (olDaSetWndHandle) → queue → arm → start``. The fake
  enforces register/queue-before-``arm`` and ``start``-requires-``arm``.
- ``set_dma_usage(1 if supports_dma else 0)`` is now always called for
  continuous tasks (previously skipped when ``NUMDMACHANS == 0``).

**Bench-verified through the production path (2026-05-28).**
``scripts/bench_record_dt9805.py`` runs `record()` on the live DT9805 (1 kHz,
2 ch, 100 samp/buf) and receives correctly-shaped ``DaqBlock``s at the expected
cadence (first block ~0.35 s in; 25 blocks in 5 s) — the silent-hang
ship-blocker is gone. Captured as a gated regression guard in
``tests/hardware/test_dt9805_continuous.py`` (``hardware`` marker +
``DTOLLIB_ENABLE_HARDWARE_TESTS=1``); passes on asyncio and trio.

**Still pending the bench:** the full 60-min 1 kHz zero-drop soak (the §5.16
acceptance) — a longer maintainer run; the fast guard above confirms the
mechanism itself works.

### Verification protocol

To flip any "pending" entry above to "verified":

1. On a maintainer Windows machine with the DataAcq SDK installed,
   open `%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\`.
2. For each type alias, find the `typedef` in `OLTYPES.H` /
   `OLDAAPI.H` / `OLMEM.H` / `OLERRORS.H` and confirm the ctypes
   shape matches.
3. For each function, find the prototype and confirm `argtypes`
   element-by-element. Pay particular attention to:
   - `LPARAM` / `WPARAM` arguments — must be `wintypes.LPARAM` /
     `wintypes.WPARAM` (pointer-sized), never `c_long` / `c_uint`.
   - Output-pointer types — `HDRVR *` is `POINTER(HDRVR)`, not
     `POINTER(c_void_p)`; the alias matters for typecheck.
4. For each constant, find the `#define` and confirm the value.
5. Update this file in a single commit, change "pending" to the SDK
   version string (e.g. `verified — SDK 7.8.5 2026-05-30`).

If a row reveals a divergence from the assumed shape, that is a
*blocking* finding — the binding cannot land until reconciled.

---

Subsequent phases append entries here. The intended shape for binding
verifications is:

```
2026-MM-DD — <type alias or function name>:
    argtypes/restype verified against OLDAAPI.H rev <SDK version>.
    Notes: <e.g. "matches manual §11.2; ECODE return type confirmed">.
```

See implementation-plan.md §1.4 for the binding verification gate.

## Phase 2 — Thermocouple read path (2026-05-28, bench-verified on DT9806(00), SDK V7.0.0.7)

Bench session that wired thermocouple reads end-to-end through the public
API (`open_device` → `poll`) on a live DT9806. Two TCs on differential
channels 4 and 6 read room temperature (~17–18 °C) and respond correctly to
a fingertip; an open channel (1) reports `SENSOR_OPEN`. Driver/probe:
`scripts/bench_probe_tc_diff.py`.

### The core finding — DT9805/06 do NOT linearise thermocouples in firmware

UM9800.md Table 26 lists *Voltage Converted to Temperature*
(`SupportsTemperatureDataInStream`) and `SupportsCjcSourceInternal` as
**unsupported**; only `SupportsCjcSourceChannel` (CJC on channel 0) is true.
The live AD subsystem reports `OLSSC_RETURNS_FLOATS=0`,
`OLSSC_SUP_MULTISENSOR=0`, `OLSSC_SUP_THERMOCOUPLES=1`. So the
firmware-linearising SDK calls correctly return `OLNOTSUPPORTED` (ec=36) —
they target intelligent DT temperature modules, not these dumb differential
front-ends:

- `olDaSetThermocoupleType` → ec=36
- `olDaSetReturnCjcTemperatureInStream` → ec=36
- `olDaGetCjcTemperature` → ec=36
- `olDaGetSingleValueEx` (autorange) → ec=36

The earlier handoff's "TC mapping is hardwired by the connector" theory was
wrong; the ec=36 is firmware-feature absence. There is **no**
`OLSSC_SUP_LINEARIZE_TC` constant in this SDK at all.

### The working sequence (application-side linearisation)

1. `olDaSetDataFlow(OL_DF_SINGLEVALUE)`,
   `olDaSetChannelType(OL_CHNT_DIFFERENTIAL=101)` — subsystem-wide, no
   channel arg; differential is mandatory for TCs (UM9800 p.36).
2. `olDaConfig`. Do **not** call `olDaStart` — single-value mode has no run
   state and `olDaStart` returns ECODE=27 "Dataflow mismatch"
   (`DataAcqBackend.start` skips it for single-value subsystems).
3. CJC: `olDaGetSingleValue(ch0, gain=1)` → `cjc_°C = V / 0.010`
   (10 mV/°C; ~0.25 V at 25 °C — saturates at gain 100, so read at gain 1).
4. TC: `olDaGetSingleValue(ch, gain=100)` for µV resolution.
5. Convert code→volts ourselves, then
   `utils.convert_volts_to_temperature(tc_type, volts, cjc_temperature_c)`
   (NIST ITS-90; Types K and J implemented).

### Bench-verified constants / behaviours

- **`OL_CHNT_SINGLEENDED = 100`, `OL_CHNT_DIFFERENTIAL = 101`**
  (OLDADEFS.H). The previous `_CHANNEL_TYPE_VALUE_TO_OL` map (0/1/2) was
  wrong — `olDaSetChannelType(1)` returns ECODE=8 "Invalid Channel Type".
  This SDK build has no pseudo-differential channel-type constant.
- **`olDaCodeToVolts` is unusable** — returns ECODE=9 "Invalid Encoding" on
  this board. `DataAcqBackend.code_to_volts` instead reads
  `olDaGetEncoding` / `olDaGetResolution` / `olDaGetRange` (newly bound) and
  applies the offset-binary formula in
  `conversion.code_to_input_volts`.
- **Encoding is `OL_ENC_BINARY` (200) with offset-binary semantics** on the
  bipolar ±10 V / 16-bit range: code 0 = −10 V, 32768 = 0 V, 65535 = +10 V.
- **Open-circuit detection:** an open differential input is pulled to the
  +2.5 V reference and pegs the ADC at +full scale (≈ +V_RAIL/gain at the
  input) → `SensorStatus.SENSOR_OPEN` (UM9800 spec note d).
- **CJC on channel 0** at 10 mV/°C; thermocouples on channels 1–7.

### New SDK bindings verified

```
2026-05-28 — olDaGetEncoding:    (HDASS, PUINT) → ECODE.   Returns 200 (OL_ENC_BINARY).
2026-05-28 — olDaGetResolution:  (HDASS, PUINT) → ECODE.   Returns 16.
2026-05-28 — olDaGetRange:       (HDASS, PDBL max, PDBL min) → ECODE.  Returns (10.0, -10.0).
```

### Hardware-day findings (DT9805 + DT9806, SDK V7.0.0.7, 2026-05-28)

Bench rig: DT9806 with 2× Type-K thermocouples on channels 4 & 6, CJC on
channel 0. Live reads ≈ 17–18 °C ambient; sentinels confirmed
(`SENSOR_OPEN` on open inputs, `TEMP_OUT_OF_RANGE_LOW` on a faulted TC).

- **`OL_NOT_SUPPORTED = 36`** (OLERRORS.H `OLNOTSUPPORTED`). Bench-confirmed:
  the live DLL returns it (error string "Not supported") for
  `olDaSetChannelRange` on the fixed-range A/D and for
  `olDaSetThermocoupleType` on the application-linearised path.
- **Per-channel `olDaSetChannelRange` is unsupported** on the DT9805/06 A/D
  (ECODE 36). The range is **fixed ±10 V, gain-selected per channel**.
  `DataAcqBackend._set_voltage_range` now tries per-channel range, falls
  back to subsystem-wide `olDaSetRange(max, min)` on ECODE 36, and falls
  back to the native range + gain if neither is honoured. This unblocked
  `AnalogInputVoltage` single-value reads and continuous `dtol-capture`.
- **`olDaEnumSSCaps` callback is `CAPSPROC`**, not a 2-arg value callback:
  `BOOL CALLBACK(UINT uiEnumCap, DBL dParam1, DBL dParam2, LPARAM)`
  (OLDAAPI.H lines 65–66). The old 2-arg typedef appended `uiEnumCap`
  (the cap ID) instead of the value — gains read back as `[102,…]`, ranges
  as `[101]`. `olDaEnumChannelCaps` uses `CHANNELCAPSPROC`
  `(UINT uiEnumCap, UINT uParam, DBL dParam, LPARAM)`.
- **DT9806 A/D enumerated capabilities** (post-fix readback):
  `OL_ENUM_RANGES → (10.0, -10.0)`; `OL_ENUM_GAINS → 1, 10, 100, 500`.
  Now wired into `CapabilitySet.ranges` / `.gains` (were silently empty).
- **A/D capability flags** (DT9805 & DT9806): `supports_thermocouples=True`,
  `returns_floats=False`, `supports_multisensor=False`. The TC path keys off
  *thermocouples*, not multisensor; `multisensor=False` is correct.
- **Clock frequency is exact** — `olDaSetClockFrequency`/`olDaGetClockFrequency`
  readback matched the request at 100/1000/5000 Hz (no quantisation).

### New SDK bindings verified

```
2026-05-28 — olDaSetRange:           (HDASS, DBL max, DBL min) → ECODE.  Accepted (10, -10).
2026-05-28 — olDaEnumSSCaps cb:      CAPSPROC(UINT, DBL, DBL, LPARAM).   Gains 1/10/100/500.
2026-05-28 — olDaEnumChannelCaps cb: CHANNELCAPSPROC(UINT, UINT, DBL, LPARAM).
```

### Callback-bridge teardown fixes (DT9806, 2026-05-28)

Found during hardware-day continuous-mode stress (50 kHz + slow consumer):

- **`ErrorPolicy.RAISE` segfault/deadlock under sustained overrun.** The
  drainer raised the SDK error directly inside the anyio task group, which
  aborted the group and raced the shielded SDK/pool teardown — a hard
  process crash (exit 139), or a hang with faulthandler. Fixed: the drainer
  now captures the exception, unwinds cleanly via an internal `_DrainStop`
  signal (same path as a normal end-of-run), and the bridge re-raises the
  captured error only AFTER the ordered shutdown (stop -> unregister -> drain
  -> free). RAISE now surfaces a clean `DtolBufferOverrunError` (not a
  `BaseExceptionGroup`). See `backend/_callback_bridge.py`.
- **Cancelled session leaked the subsystem.** `DtolSession.close()` was not
  shielded against cancellation, so a `move_on_after`/timeout around
  `record()` cancelled the awaited `release_dass`/`terminate` mid-flight,
  leaving the A/D reserved ("Subsystem in use", ECODE 20) until the OS
  reclaimed the process handle. Fixed: `close()` wraps its teardown in a
  shielded `CancelScope`. See `tasks/session.py`.
- **Release latency.** After a *clean* close (incl. overrun via RETURN) a
  fresh process re-acquires the subsystem in ~0.01 s — no leak. Abnormal
  exits (the pre-fix crash) held the reservation for a few seconds until the
  OS reclaimed the handle.

### Known gaps

- Durable sinks: only `InMemorySink`, `CsvSink`, `JsonlSink`, and
  `RawCountsSink` are implemented. `SqliteSink` / `ParquetSink` /
  `PostgresSink` (design.md §15.1 "six sinks") are NOT yet present.
- Continuous `DaqBlock`s carry raw offset-binary codes, not linearised
  temperatures (block-level TC linearisation is a Phase 6 item).

## Phase 4 (2026-05-28) — output surface (DT9806 AO / DO / DIO)

### Prototype `argtypes` / `restype` verifications

Verified by direct grep of the installed C headers at
`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\` (SDK V7.0.0.7,
2026-05-28). The authoritative C header — never `OLDADEFS.bas`.

- **`olDaPutSingleValue`** — `OLDAAPI.H`:
  `ECODE olDaPutSingleValue(HDASS, LNG lValue, UINT uiChannel, DBL dGain)`.
  argtypes `[HDASS, c_long, c_uint, c_double]`, restype `ECODE`. Note the
  value is the **second** argument (code, not volts) — mirror-but-not-symmetric
  with `olDaGetSingleValue(HDASS, PLNG, UINT, DBL)`.
- **`olDaPutSingleValues`** — `OLDAAPI.H`:
  `ECODE olDaPutSingleValues(HDASS, PLNG plValues, DBL dGain)`.
  argtypes `[HDASS, POINTER(c_long), c_double]`, restype `ECODE`.
- **`olDaSetSynchronousDigitalIOUsage`** — `OLDAAPI.H`:
  `ECODE olDaSetSynchronousDigitalIOUsage(HDASS, BOOL fUse)`.
  argtypes `[HDASS, c_int]`, restype `ECODE`.
- **`olDaSetDigitalIOListEntry`** — `OLDAAPI.H`:
  `ECODE olDaSetDigitalIOListEntry(HDASS, UINT uiEntry, UINT uiValue)`.
  argtypes `[HDASS, c_uint, c_uint]`, restype `ECODE`. The third arg is a
  port *value*, not a channel index.
- **`olDaMute` / `olDaUnMute`** — `OLDAAPI.H`:
  `ECODE olDaMute(HDASS)` / `ECODE olDaUnMute(HDASS)`.
  argtypes `[HDASS]`, restype `ECODE`.
- **`olDmCopyToBuffer`** — `Olmem.h`:
  `ECODE olDmCopyToBuffer(HBUF hBuf, LPVOID lpAppBuffer, ULNG ulNumSamples)`.
  argtypes `[HBUF, c_void_p, c_ulong]`, restype `ECODE`.
- **`olDmCopyBuffer`** — `Olmem.h`:
  `ECODE olDmCopyBuffer(HBUF, LPVOID)`.
  argtypes `[HBUF, c_void_p]`, restype `ECODE`.

### Output capability positions (WS-B) — bench-verified (SDK V7.0.0.7, 2026-05-28)

Transcribed into `capi/constants.py` (WS-B) and wired into `CapabilitySet` /
`query_capabilities`. Positions located in `OLDADEFS.H`'s `olssc_tag` enum by
**counting from the enum head**, cross-checked against every already-verified
neighbour, then **confirmed by a live `olDaGetSSCaps` read-back** on the DT9806
via `scripts/bench_probe_da_caps.py`. Read-back values below are `(AD, DA)`:

| Position | Constant | AD | DA | Verified |
|----------|----------|----|----|----------|
| 46  | `OLSSC_MAX_DIGITALIOLIST_VALUE`  | 1 | 0 | verified — SDK V7.0.0.7 |
| 50  | `OLSSC_SUP_SYNCHRONOUS_DIGITALIO` | 1 | 0 | verified — SDK V7.0.0.7 |
| 97  | `OLSSC_SUP_WRPWAVEFORM`           | 0 | 0 | verified — SDK V7.0.0.7 |
| 117 | `OLSSC_CURRENT_OUTPUTS`          | 0 | 0 | verified — SDK V7.0.0.7 |
| 118 | `OLSSC_SUP_PUT_SINGLE_VALUES`    | 0 | 0 | verified — SDK V7.0.0.7 |
| 142 | `OLSSC_SUP_MUTE`                 | 0 | 0 | verified — SDK V7.0.0.7 |

The read-back returned clean booleans with no `olDaGetSSCaps` errors and a
**coherent** AD/DA pattern (synchronous-DIO + DIO-list value present on AD,
absent on DA), which it could not be if the enum count were off — closing the
§1.4a gate. `supports_simultaneous_da` keeps its documented fallback alias to
`OLSSC_SUP_SIMULTANEOUS_SH`.

### ⛔ The DT9806 D/A is single-value only — continuous AO is unsupported (2026-05-28)

Bench-confirmed via `scripts/bench_probe_ao_wndhandle.py` and
`scripts/bench_probe_da_caps.py`. The DT9806 D/A subsystem reports:

| `OLSSC_SUP_CONTINUOUS` | `WRPMULTIPLE` | `WRPSINGLE` | `WRPWAVEFORM` | `SUP_FIFO` | `SUP_SINGLEVALUE` |
|---|---|---|---|---|---|
| **0** | 0 | 0 | 0 | 0 | **1** |

(The A/D subsystem reports `CONTINUOUS=1`, `WRPMULTIPLE=1`, `WRPSINGLE=1` — so
`record()` works on AI.) Every continuous setter on the D/A returns
`OLNOTSUPPORTED` (ec=36): `olDaSetDataFlow(CONTINUOUS)`, `olDaSetWrapMode`,
the channel-list setters, and `olDaSetWndHandle`; `olDaStart` then fails ec=27.

**Consequence:** continuous analog output (`play()`) **cannot run on the
DT9806** — there is no streaming DAC. Per the §1 Definition of Done, `play()`
now **fails loud**: it checks `capabilities.supports_continuous` after
`configure()` and raises `DtolCapabilityError` (pointing at
`DtolSession.write()`) instead of dying mid-startup at `olDaConfig`. The
`play()` software path (buffer-pool fill, output bridge, refill loop) stays
fully unit-tested against `FakeDtolBackend`, whose `make_dt9806_ao_capabilities`
models an idealised streaming DAC (`supports_continuous=True`) — the same
fake-models-the-ideal pattern as the QUAD/TACH/MEASURE counter caps. `play()`
remains correct for a future DT-Open Layers board whose D/A *does* report
continuous support (e.g. the waveform-DAC boards).

### Single-value AO write — gain-list NOT_SUPPORTED on the D/A (fixed 2026-05-28)

`add_channel` issued `olDaSetGainListEntry` for analog-output channels, but the
DT9806 D/A has no programmable gain and rejects it with `OLNOTSUPPORTED`
(ec=36), so even single-value AO `write()` failed at `configure()`. Fixed in
`backend/dataacq.py`: AI still sets the gain-list entry (it selects the
per-channel range on the DT9805/06), but for `AnalogOutputVoltage` the call now
tolerates `OL_NOT_SUPPORTED` — mirroring the existing `_set_voltage_range`
fallback. The single-value put writes the code directly; no gain list is needed.
Bench-confirmed: after the fix the AO confirm-gate hardware tests pass on the
DT9806.

## Phase 5 (2026-05-28) — counter/timer, tachometer, quadrature, simultaneous start

### Prototype `argtypes` / `restype` verifications

Signatures were initially transcribed from `dasdk_digest.md` (the technical
digest of the DataAcq SDK User's Manual); the direct `OLDADEFS.H` / `OLDAAPI.H`
grep and real-DLL export probe **have since been done on the bench** (SDK
V7.0.0.7, 2026-05-28 — see OQ-5a below), which corrected the selector constant
families, dropped the non-existent `olDaSetCTClock*` exports in favour of the
generic clock setters, and confirmed `olDaSimultaneousPrestart` spelling. The
binding gate is **closed** for every selector this hardware exposes.
Counter/sync functions return `OLSTATUS` (== `ECODE`).

- **`olDaSetCTMode`** — `(HDASS, UINT mode)`; argtypes `[HDASS, c_uint]`.
- **`olDaSetCTClockSource`** — `(HDASS, UINT source)`; `[HDASS, c_uint]`.
- **`olDaSetCTClockFrequency`** — `(HDASS, DBL freq_hz)`; `[HDASS, c_double]`.
- **`olDaSetGateType`** — `(HDASS, UINT gate)`; `[HDASS, c_uint]`.
- **`olDaSetPulseType`** — `(HDASS, UINT polarity)`; `[HDASS, c_uint]`.
- **`olDaSetPulseWidth`** — `(HDASS, DBL duty_or_width)`; `[HDASS, c_double]`.
- **`olDaSetMeasureStartEdge` / `olDaSetMeasureStopEdge`** — `(HDASS, UINT edge)`;
  `[HDASS, c_uint]`.
- **`olDaSetCascadeMode`** — `(HDASS, BOOL cascade)`; `[HDASS, c_int]`.
- **`olDaReadEvents`** — `(HDASS, UINT channel, ULNG *pcount)`;
  argtypes `[HDASS, c_uint, POINTER(c_ulong)]`, out-pointer count.
- **`olDaMeasureFrequency`** — `(HDASS, UINT channel, DBL *pfreq_hz)`;
  argtypes `[HDASS, c_uint, POINTER(c_double)]`, out-pointer Hz.
- **`olDaSetTriggeredScanUsage`** — `(HDASS, UINT enable)`; `[HDASS, c_uint]`.
- **`olDaSetMultiscanCount`** — `(HDASS, ULNG count)`; `[HDASS, c_ulong]`.
- **`olDaSetRetriggerMode`** — `(HDASS, UINT mode)`; `[HDASS, c_uint]`.
- **`olDaSetRetrigger`** — `(HDASS, UINT source)`; `[HDASS, c_uint]`.
- **`olDaSetRetriggerFrequency`** — `(HDASS, DBL freq_hz)`; `[HDASS, c_double]`.
- **`olDaGetSSList`** — `(HDRVR, HSSLIST *phsslist)`;
  argtypes `[HDRVR, POINTER(HSSLIST)]`, out-pointer list handle.
- **`olDaPutDassToSSList`** — `(HSSLIST, HDASS)`; `[HSSLIST, HDASS]`.
- **`olDaSimultaneousPreStart`** — `(HSSLIST)`; `[HSSLIST]`.
- **`olDaSimultaneousStart`** — `(HSSLIST)`; `[HSSLIST]`.
- **`olDaReleaseSSList`** — `(HSSLIST)`; `[HSSLIST]`.

### OQ-5a — C/T selector constants RESOLVED (bench 2026-05-28, SDK V7.0.0.7)

The provisional 1400-family values were **wrong in both value and name**.
Transcribed directly from `OLDADEFS.H` at
`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\` and pinned in
`capi/constants.py` (line citations per symbol):

| Symbol | Provisional | Real | OLDADEFS.H |
|--------|------------:|-----:|-----------|
| `OL_CTMODE_COUNT` | 1400 | **700** | :297 |
| `OL_CTMODE_RATE` | 1402 | **701** | :298 |
| `OL_CTMODE_ONESHOT` | 1403 | **702** | :299 |
| `OL_CTMODE_ONESHOT_RPT` | 1404 | **703** | :300 |
| `OL_CTMODE_UP_DOWN` (new) | — | **704** | :301 |
| `OL_CTMODE_MEASURE` | 1401 | **705** | :302 |
| `OL_CTMODE_CONT_MEASURE` (new) | — | **706** | :303 |
| `OL_PLS_HIGH2LOW` (was `OL_PULSETYPE_HITOLOW`) | 1421 | **600** | :293 |
| `OL_PLS_LOW2HIGH` (was `OL_PULSETYPE_LOWTOHI`) | 1420 | **601** | :294 |
| `OL_GATE_NONE` (was `OL_GATE_SWGATE`) | 1410 | **500** | :279 |
| `OL_GATE_HIGH_LEVEL` | 1412 | **501** | :280 |
| `OL_GATE_LOW_LEVEL` | 1411 | **502** | :281 |
| `OL_GATE_HIGH_EDGE` | 1414 | **503** | :282 |
| `OL_GATE_LOW_EDGE` | 1413 | **504** | :283 |
| `OL_EDGE_FALLING` | 1431 | **600** | :565 |
| `OL_EDGE_RISING` | 1430 | **601** | :566 |
| `OL_CT_CASCADE` (new) | — | **900** | :345 |
| `OL_CT_SINGLE` (new) | — | **901** | :346 |

Pre-bench dtollib names are kept as back-compat aliases. **There is no
`OL_CTMODE_QUAD` / `OL_CTMODE_TACH`** in the header — quadrature/tachometer are
not counter modes (see OQ-5b). `olDaSetCascadeMode` takes a **UINT selector**
(`OL_CT_CASCADE`/`OL_CT_SINGLE`), not the BOOL the wrapper passed.

Binding corrections found the same day (real DLL export probe):
- `olDaSetCTClockSource` / `olDaSetCTClockFrequency` **do not exist** — the C/T
  clock shares the generic `olDaSetClockSource` / `olDaSetClockFrequency`.
- `olDaMute` / `olDaUnMute` are header-declared (OLDAAPI.H:331-332) but **not
  exported** by `oldaapi64.dll` V7.0.0.7 → now bound optionally.
- `olDaSimultaneousPreStart` → real export is `olDaSimultaneousPrestart`
  (OLDAAPI.H:243).

**Read-back (bench 2026-05-28, `scripts/bench_probe_counter.py`)** — identical
on DT9805(00) and DT9806(00), both C/T elements:

- `olDaSetCTMode`: COUNT/RATE/ONESHOT/ONESHOT_RPT → **ec 0**; UP_DOWN/MEASURE/
  CONT_MEASURE → **ec 36** (Not supported) — confirms the values are right
  *and* the SDK rejects the modes this hardware lacks.
- `olDaSetGateType`: NONE/HIGH_LEVEL/LOW_LEVEL/HIGH_EDGE/LOW_EDGE → **ec 0**.
- `olDaSetCascadeMode`: SINGLE(901)/CASCADE(900) → **ec 0** (UINT selector
  confirmed; the old BOOL was wrong).
- `olDaSetPulseType` (under RATE): HIGH2LOW(600)/LOW2HIGH(601) → **ec 0**.
- `olDaSetMeasureStartEdge`: RISING(601)/FALLING(600) → **ec 128** ("Invalid
  counter edge specified") because MEASURE mode is unsupported on this
  hardware — the edge values are transcribed verbatim from OLDADEFS.H:565-566
  and cannot be exercised here. Gated off with the MEASURE mode.

OQ-5a is **closed** for every selector this hardware exposes.

### OQ-5b — quadrature/tachometer hardware RESOLVED: ABSENT (bench 2026-05-28)

Direct probe of both physical boards (DLL loaded standalone, bypassing the
package):

- `olDaGetDASS(OLSS_QUAD)` and `olDaGetDASS(OLSS_TACH)` → **ECODE 3** (bad
  subsystem) on **both** DT9805(00) and DT9806(00). Neither board exposes a
  quadrature or tachometer subsystem.
- The C/T subsystem (2 elements × 1 channel on each board) reports caps:
  - **Supported:** `CTMODE_COUNT`, `CTMODE_RATE`, `CTMODE_ONESHOT`,
    `CTMODE_ONESHOT_RPT`, `CASCADING`, `PLS_HIGH2LOW`, `PLS_LOW2HIGH`,
    `GATE_NONE/HIGH_LEVEL/LOW_LEVEL/HIGH_EDGE/LOW_EDGE`.
  - **NOT supported:** `CTMODE_MEASURE`, `CTMODE_UP_DOWN`,
    `CTMODE_CONT_MEASURE`, `FIXED_PULSE_WIDTH`, `QUADRATURE_DECODER`,
    `SIMULTANEOUS_START`.

Authoritative OLSSC enum positions (hand-counted from OLDADEFS.H, cross-checked
against the already-bench-confirmed `OLSSC_*` values): `SUP_FIXED_PULSE_WIDTH`
= 100, `SUP_QUADRATURE_DECODER` = 101, `SUP_CTMODE_COUNT..ONESHOT_RPT` = 42-45,
`SUP_CTMODE_UP_DOWN` = 95, `SUP_CTMODE_MEASURE` = 96, `SUP_CTMODE_CONT_MEASURE`
= 102, `SUP_CASCADING` = 41, `SUP_SIMULTANEOUS_START` = 51.

**Consequences (decided 2026-05-28):**
- `CounterMode.QUADRATURE` / `.TACHOMETER` / `.MEASURE` are **gated off** via
  the runtime capability query (the project's "cap query is the only
  authority" rule) — they raise `DtolCapabilityError` before any SDK call,
  rather than being rebound to a subsystem that does not exist here. The
  superseded plan to "rebind to the OLSS_QUAD/OLSS_TACH subsystem path" is
  moot on this hardware.
- Procedure C frequency-measurement and edge-to-edge cases (which need
  `CTMODE_MEASURE`) are out of scope on these boards; event-count (COUNT) and
  pulse-train (RATE) acceptance remain in scope.
- `SIMULTANEOUS_START` unsupported on the C/T subsystem flags risk for the
  Procedure E `start_synchronized` AI+C/T claim — to be confirmed when WS-A0
  unblocks bench runs.

---

## 2026-05-29 — Phase 6 §A.2: continuous-mode application-side TC linearisation

The continuous/block drainer previously emitted **raw offset-binary codes cast
to float** (the Phase 3 known gap; see project memory). Phase 6 §A.2 adds
application-side code→engineering-units conversion to the §12.3.2 drainer so
`DaqBlock.data` carries volts / °C, mirroring the single-value
`DtolSession._read_all_channels_app_side_tc` path.

**Design decisions (software complete 2026-05-29; bench validation pending WS-A0):**

- **CJC is sourced from a channel in the scan list, NOT the interleaved
  stream.** `olDaSetReturnCjcTemperatureInStream` returns ECODE 36 on the
  DT9805/06 (bench-confirmed 2026-05-28), so a continuous TC task must include
  its cold-junction channel (ch0, 10 mV/°C, gain 1) as a scan-list row. The
  drainer reads that row per scan and CJC-corrects the TC rows. `record()`
  raises `DtolTaskStateError` if the CJC channel is omitted (fail-loud, never
  silent-wrong-data), and rejects a TC sitting on its own CJC channel. The
  `deinterleave_cjc` helper is retained for true intelligent modules but is not
  the owned-hardware path.
- **There is no `OLSSC_SUP_LINEARIZE_TC`** in this SDK (design.md §8.4 was
  wrong). The app-side path is gated on `supports_thermocouples and not
  returns_floats`, the established single-value rule.
- New pure kernel `capi/conversion.py::linearise_block` + `BlockConversion`
  plan (built by `streaming/record()` after `olDaConfig`, from the
  `CapabilitySet` + per-channel gains/TC types + `backend.get_scaling`).
  Fully unit-tested on the fake; the per-sample NIST inverse is a Python loop
  (acceptable at bench scan rates — vectorisation is a Phase 7 perf item).
- New `DtolBackend.get_scaling(hdass) -> (vmin, vmax, resolution_bits,
  twos_complement)` exposes the same scaling `code_to_volts` already cached.
- `DaqBlock.is_linearised: bool` discriminates engineering-units data from the
  raw-codes fallback so sinks/replay don't guess.

**Still owed:** NIST ITS-90 polynomials for Types T/E/R/S/B/N (only K & J ship
today) — blocked 2026-05-29 on the NIST ITS-90 site returning HTTP 503; come
back when it is reachable. The bench rig has only K-type TCs, so this does not
block §A.4 hardware validation.

---

## 2026-05-28 — Phase 6 Track B multi-sensor bindings (header-verified)

The Phase 6 multi-sensor SDK surface was bound and signature-verified against
the installed C headers at
`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\` (per the §1.4 gate):

- **`olDaSetRtdType`** — `(HDASS, UINT chan, UINT rtd_type)` — OLDAAPI.H:292.
  `OL_RTD_TYPE_*` selectors are `#define`s 1608–1614 in OLDADEFS.H:384–390
  (PT3750/PT3850/PT3911/PT3916/PT3920/PT3928/CUSTOM).
- **`olDaSetRtdR0/A/B/C`** — `(HDASS, UINT chan, DBL)` — OLDAAPI.H:338–344.
- **`olDaSetThermistorA/B/C`** — `(HDASS, UINT chan, DBL)` — OLDAAPI.H:346–350.
- **`olDaSetCouplingType`** — `(HDASS, UINT chan, COUPLING_TYPE)` —
  OLDAAPI.H:275; `COUPLING_TYPE` enum `{DC=0, AC=1}` OLDADEFS.H:492.
- **`olDaSetExcitationCurrentSource`** — `(HDASS, UINT chan,
  EXCITATION_CURRENT_SRC)` — OLDAAPI.H:277; enum `{INTERNAL=0, EXTERNAL=1,
  DISABLED=2}` OLDADEFS.H:498.
- **`olDaSetExcitationCurrentValue`** — `(HDASS, UINT chan, DBL)` — OLDAAPI.H:279.
- **`olDaSetStrainExcitationVoltageSource`** — `(HDASS,
  STRAIN_EXCITATION_VOLTAGE_SRC)` — OLDAAPI.H:318. **No channel arg** —
  excitation is a subsystem-wide property; `{INTERNAL=0, EXTERNAL=1}`
  OLDADEFS.H:506.
- **`olDaSetStrainExcitationVoltage`** — `(HDASS, DBL)` — OLDAAPI.H:320. No channel arg.
- **`olDaSetStrainBridgeConfiguration`** — `(HDASS, UINT chan,
  STRAIN_GAGE_CONFIGURATION)` — OLDAAPI.H:322; 7-value enum OLDADEFS.H:512.
- **`olDaSetStrainShuntResistor`** — `(HDASS, UINT chan, BOOL)` — OLDAAPI.H:324.
- **`olDaSetBridgeConfiguration`** — `(HDASS, UINT chan, BRIDGE_CONFIGURATION)`
  — OLDAAPI.H:358; `{FULL, HALF, QUARTER}` OLDADEFS.H:523.
- **`olDaVoltsToStrain`** — `(STRAIN_GAGE_CONFIGURATION, DBL Vu, DBL Vs, DBL
  Vex, DBL GF, DBL Rg, DBL Rl, DBL Pr, DBL ShuntCorrection, PDBL out)` —
  OLDAAPI.H:397. Pure function, no HDASS.
- **`olDaVoltsToBridgeBasedSensor`** — `(DBL Vu, DBL Vs, DBL Vex, DBL Tc, DBL
  Rg, DBL Rl, DBL RoInmV_V, DBL ShuntCorrection, PDBL out)` — OLDAAPI.H:409.
- **TEDS readers** (`olDaReadStrainGage{Hardware,Virtual}Teds`,
  `olDaReadBridgeSensor{Hardware,Virtual}Teds`) — OLDAAPI.H:326–329; structs
  `STRAIN_GAGE_TEDS` / `BRIDGE_SENSOR_TEDS` in TedsApi.h:245/278. Bound in §8.B5.

**`olDaSetTransducerType` does NOT exist** in this SDK build (design.md §26
Phase 6 listed it speculatively) — dropped, per the "bind when present" rule.
**`olDaSetIEPE` does NOT exist** — IEPE is configured via
`olDaSetCouplingType(AC)` + the excitation-current setters instead.

All 18 setter/conversion symbols + 4 TEDS symbols confirmed present on the live
`oldaapi64.dll` (2026-05-28). **None are bench-verified against a sensor** — the
owned DT9805/06 report `supports_multisensor=False` and reject every setter with
ECODE 36. Real-sensor verification is deferred until a multi-sensor DT module
(DT9828/9829/9837) is acquired; the acceptance bar for Track B is the
fake-proven configure path + clean `DtolCapabilityError` on owned hardware.

The `IO_TYPE` enum (OLDADEFS.H:576) was re-confirmed: `VOLTAGEIN=0 … CURRENT=7,
THERMOCOUPLE=8, RTD=9, STRAINGAGE=10, ACCELEROMETER=11, BRIDGE=12,
THERMISTOR=13, RESISTANCE=14, MULTISENSOR=15` — matching `capi/constants.py`
`IOTYPE_*`. The stale `_IO_TYPE_TO_OLSS_MULTI_SENSOR` table in `dataacq.py`
(values 0–8) is corrected to these verified ordinals in §8.B3.

### Phase 6 Track B — software complete (2026-05-28)

Specs (`RtdInput`/`ThermistorInput`/`ResistanceInput`/`CurrentInput`/`IepeInput`/
`StrainInput`/`BridgeInput` + `RtdType`/`ExcitationSource`/`StrainExcitationSource`/
`StrainGageConfiguration`/`BridgeConfiguration` enums), the 18 setters/conversions +
4 TEDS readers, backend dispatch (`_configure_multi_sensor` + `_tolerate_unsupported`),
the builder capability gate (`_require_io_type_supported`), `teds.py`
(`StrainGageTeds`/`BridgeSensorTeds`), and `strain.py`
(`strain_from_volts`/`bridge_value_from_volts`) all landed and CI-green on the fake.
`STRAIN_GAGE_TEDS`/`BRIDGE_SENSOR_TEDS` ctypes structs transcribed from `TedsApi.h`;
live-DLL ABI confirmed (conversions return values, virtual-TEDS read raises cleanly,
no memory corruption). Corrected the stale `_IO_TYPE_TO_OLSS_MULTI_SENSOR` table in
`dataacq.py` to the verified `IOTYPE_*` ordinals (it had pre-bench placeholders 0–8;
unexercised on owned hw because `supports_multisensor=False`, but wrong). Real-sensor
verification deferred until a DT9828/9829/9837-class module is on the bench.

### DIO port + bitmask model (2026-05-29)

Bench (`docs/bench-dio-ao.md` §2D/§2E) proved the per-line digital model was
wrong against real hardware. The DT9805/06 expose **one 8-bit port per
direction** (`num_channels=1`, `OLSSC_RESOLUTION=8`); the 8 relays are the 8
bits of channel 0. The shipped `DigitalOutputLine(physical_channel=N)` passed
`N` as the SDK channel, so any line ≥1 raised `ECODE 7 (Invalid Channel)` and
the bool→`{0,1}` encoding could only drive relay 0.

**Decision (clean break, no compat shim):** removed the `*Line` classes and
replaced them with a port + bitmask model in `channels/digital.py`:

- `DigitalOutputPort` / `DigitalInputPort` — `physical_channel` is the port
  index; a port is the SDK channel. One `olDaPutSingleValue` /
  `olDaGetSingleValue` moves the whole byte.
- `DigitalLine(bit=N, name=...)` — per-bit view; not an SDK channel.
- `DtolSession.write` (`_plan_write`) groups touched keys by port, packs the
  bits into one byte, and issues one put per port. Partial per-line writes
  merge into a per-port **shadow register** (seeded from `safe_value` at
  commit), so untouched lines hold their last value. DOUT has no reliable
  read-back, so the shadow is authoritative.
- Reads decompose the port byte into the raw int (under the port name) plus a
  bool per declared line.
- `CapabilitySet.resolution` (`olDaGetResolution`) now carries the port width;
  `builder._validate_digital_port` rejects an out-of-range port index or a line
  bit ≥ width at configure-time.
- `FakeDtolBackend` now models the single 8-bit port (`num_channels=1`,
  `resolution=8`) and raises ECODE 7 past it, so the unit suite exercises the
  real shape — the bug was previously invisible because the fake modelled
  idealised per-line channels.
