r"""Binding-internal SDK constants — values from ``OLDADEFS.H`` / ``OLDAAPI.H``.

This module is the **binding-internal** namespace. It deliberately
mirrors the SDK header names (``OLSS_AD``, ``OL_DF_SINGLEVALUE``,
``OLSSC_SUP_CONTINUOUS``) so call-sites read like the SDK manual.

The user-facing public namespace lives in :mod:`dtollib.constants`
(``DataFlow.CONTINUOUS``, ``SubsystemType.ANALOG_INPUT``, ...) and
never shares a name with this module.

**Verification status (2026-05-28, SDK V7.0.0.7):** every value below
is transcribed from ``OLDADEFS.H`` / ``OLDAAPI.H`` at
``%ProgramFiles(x86)%\\Data Translation\\Win32\\SDK\\Include\\``,
cross-checked against the older ``OLDADEFS.bas`` (VB) where it
agrees and against ``olDaGetSSCaps`` bench readback for the
``OLSSC_*`` family.  Earlier dtollib revisions used the OLDER VB
values (DF=0/SV=1, QUE=0/1/2, etc.) which the OLDAAPI.DLL rejects
with ECODE 18/35/89 — every continuous-mode setter call failed
because the API expects the offsetted-by-100s values defined here.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "OLDA_WM_BUFFER_DONE",
    "OLDA_WM_BUFFER_REUSED",
    "OLDA_WM_EVENT_DONE",
    "OLDA_WM_IO_COMPLETE",
    "OLDA_WM_MEASURE_DONE",
    "OLDA_WM_OVERRUN_ERROR",
    "OLDA_WM_PRETRIGGER_BUFFER_DONE",
    "OLDA_WM_QUEUE_DONE",
    "OLDA_WM_QUEUE_STOPPED",
    "OLDA_WM_TRIGGER_ERROR",
    "OLDA_WM_UNDERRUN_ERROR",
    "OLNOERROR",
    "OLSSCE_MAX_THROUGHPUT",
    "OLSSC_CGLDEPTH",
    "OLSSC_CURRENT_OUTPUTS",
    "OLSSC_MAX_DIGITALIOLIST_VALUE",
    "OLSSC_NUMCHANNELS",
    "OLSSC_RETURNS_FLOATS",
    "OLSSC_SUP_AUTOCAL",
    "OLSSC_SUP_CONTINUOUS",
    "OLSSC_SUP_CONTINUOUS_ABOUTTRIG",
    "OLSSC_SUP_CONTINUOUS_PRETRIG",
    "OLSSC_SUP_CTMODE_CONT_MEASURE",
    "OLSSC_SUP_CTMODE_MEASURE",
    "OLSSC_SUP_CTMODE_UP_DOWN",
    "OLSSC_SUP_DMA",
    "OLSSC_SUP_FIXED_PULSE_WIDTH",
    "OLSSC_SUP_INPROCESSFLUSH",
    "OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM",
    "OLSSC_SUP_MULTISENSOR",
    "OLSSC_SUP_MUTE",
    "OLSSC_SUP_PUT_SINGLE_VALUES",
    "OLSSC_SUP_QUADRATURE_DECODER",
    "OLSSC_SUP_SIMULTANEOUS_DA",
    "OLSSC_SUP_SIMULTANEOUS_SH",
    "OLSSC_SUP_SINGLEENDED",
    "OLSSC_SUP_SINGLEVALUE",
    "OLSSC_SUP_SINGLEVALUE_AUTORANGE",
    "OLSSC_SUP_SYNCHRONOUS_DIGITALIO",
    "OLSSC_SUP_WRPWAVEFORM",
    "OLSS_AD",
    "OLSS_CT",
    "OLSS_DA",
    "OLSS_DIN",
    "OLSS_DOUT",
    "OLSS_QUAD",
    "OLSS_SRL",
    "OLSS_TACH",
    "OL_BRIDGE_FULL",
    "OL_BRIDGE_HALF",
    "OL_BRIDGE_QUARTER",
    "OL_CLK_EXTERNAL",
    "OL_CLK_EXTRA",
    "OL_CLK_INTERNAL",
    "OL_COUPLING_AC",
    "OL_COUPLING_DC",
    "OL_CTMODE_CONT_MEASURE",
    "OL_CTMODE_COUNT",
    "OL_CTMODE_MEASURE",
    "OL_CTMODE_ONESHOT",
    "OL_CTMODE_ONESHOT_RPT",
    "OL_CTMODE_QUAD",
    "OL_CTMODE_RATE",
    "OL_CTMODE_TACH",
    "OL_CTMODE_UP_DOWN",
    "OL_CT_CASCADE",
    "OL_CT_SINGLE",
    "OL_DF_CONTINUOUS",
    "OL_DF_CONTINUOUS_ABOUTTRIG",
    "OL_DF_CONTINUOUS_PRETRIG",
    "OL_DF_SINGLEVALUE",
    "OL_EDGE_FALLING",
    "OL_EDGE_RISING",
    "OL_ENUM_GAINS",
    "OL_ENUM_RANGES",
    "OL_EXCITATION_CURRENT_SRC_DISABLED",
    "OL_EXCITATION_CURRENT_SRC_EXTERNAL",
    "OL_EXCITATION_CURRENT_SRC_INTERNAL",
    "OL_FALSE",
    "OL_GATE_HIGHEDGE",
    "OL_GATE_HIGHLEVEL",
    "OL_GATE_HIGH_EDGE",
    "OL_GATE_HIGH_EDGE_DEBOUNCE",
    "OL_GATE_HIGH_LEVEL",
    "OL_GATE_HIGH_LEVEL_DEBOUNCE",
    "OL_GATE_LEVEL",
    "OL_GATE_LEVEL_DEBOUNCE",
    "OL_GATE_LOWEDGE",
    "OL_GATE_LOWLEVEL",
    "OL_GATE_LOW_EDGE",
    "OL_GATE_LOW_EDGE_DEBOUNCE",
    "OL_GATE_LOW_LEVEL",
    "OL_GATE_LOW_LEVEL_DEBOUNCE",
    "OL_GATE_NONE",
    "OL_GATE_SWGATE",
    "OL_NOT_SUPPORTED",
    "OL_PLS_HIGH2LOW",
    "OL_PLS_LOW2HIGH",
    "OL_PULSETYPE_HITOLOW",
    "OL_PULSETYPE_LOWTOHI",
    "OL_QUE_DONE",
    "OL_QUE_INPROCESS",
    "OL_QUE_READY",
    "OL_RETRIG_EXTRA",
    "OL_RETRIG_INTERNAL",
    "OL_RETRIG_SCANPERTRIG",
    "OL_RTD_TYPE_CUSTOM",
    "OL_RTD_TYPE_PT3750",
    "OL_RTD_TYPE_PT3850",
    "OL_RTD_TYPE_PT3911",
    "OL_RTD_TYPE_PT3916",
    "OL_RTD_TYPE_PT3920",
    "OL_RTD_TYPE_PT3928",
    "OL_STRAIN_EXCITATION_VOLTAGE_SRC_EXTERNAL",
    "OL_STRAIN_EXCITATION_VOLTAGE_SRC_INTERNAL",
    "OL_STRAIN_FULL_BRIDGE_AXIAL",
    "OL_STRAIN_FULL_BRIDGE_BENDING",
    "OL_STRAIN_FULL_BRIDGE_BENDING_POISSON",
    "OL_STRAIN_HALF_BRIDGE_BENDING",
    "OL_STRAIN_HALF_BRIDGE_POISSON",
    "OL_STRAIN_QUARTER_BRIDGE",
    "OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION",
    "OL_TACHOMETER_INPUT_FALLING",
    "OL_TACHOMETER_INPUT_RISING",
    "OL_TRG_EXTERN",
    "OL_TRG_SOFT",
    "OL_TRG_SYNCBUS",
    "OL_TRG_THRESHNEG",
    "OL_TRG_THRESHPOS",
    "OL_TRUE",
    "OL_WRP_MULTIPLE",
    "OL_WRP_NONE",
    "OL_WRP_SINGLE",
]


# --- Subsystem types (OLDADEFS.H ``olss_tag`` enum) ------------------------
#
# Sequential enum positions.  ``OLSS_QUAD`` is a #define alias for
# ``OLSS_SRL`` (both are value 4); ``OLSS_TACH`` is the trailing
# enum entry (6).

OLSS_AD: Final[int] = 0
OLSS_DA: Final[int] = 1
OLSS_DIN: Final[int] = 2
OLSS_DOUT: Final[int] = 3
OLSS_SRL: Final[int] = 4
OLSS_QUAD: Final[int] = 4  # OLDADEFS.H: ``#define OLSS_QUAD OLSS_SRL``
OLSS_CT: Final[int] = 5
OLSS_TACH: Final[int] = 6


# --- Data-flow modes (OLDADEFS.H ``OLDRV_SETDATAFLOW`` block) --------------
#
# These are the 800-series offsets from the actual C header, NOT the
# 0-5 values in the older OLDADEFS.bas VB binding.  The DT9805
# multi-sensor module reports current data-flow as 800 (CONTINUOUS)
# via ``olDaGetDataFlow``, confirming this offset family.

OL_DF_CONTINUOUS: Final[int] = 800
OL_DF_SINGLEVALUE: Final[int] = 801
OL_DF_CONTINUOUS_PRETRIG: Final[int] = 804
OL_DF_CONTINUOUS_ABOUTTRIG: Final[int] = 805


# --- Boolean ----------------------------------------------------------------
#
# ``OL_TRUE`` / ``OL_FALSE`` are documented as the SDK's preferred
# integer truth values; the SDK accepts any non-zero value for TRUE in
# practice but we normalise on these for round-trip consistency.

OL_FALSE: Final[int] = 0
OL_TRUE: Final[int] = 1


# --- Error sentinel (OLERRORS.H) -------------------------------------------

OLNOERROR: Final[int] = 0

# OLERRORS.H ``OLNOTSUPPORTED``. Bench-confirmed on DT9806 SDK V7.0.0.7
# (2026-05-28): the live DLL returns this for unsupported per-channel
# setters such as ``olDaSetChannelRange`` on the fixed-range DT9805/06 A/D
# and ``olDaSetThermocoupleType`` on the application-linearised path. The
# error string decodes to "Not supported".
OL_NOT_SUPPORTED: Final[int] = 36


# --- Capability flags (OLDADEFS.H ``olssc_tag`` enum) ----------------------
#
# Sequential enum positions in the ``OLSSC`` enum, **not** bitfield
# values.  Old dtollib revisions used arbitrary hex placeholders
# (0x0001, 0x0007, 0x0100, ...) which the SDK either rejects with
# OLBADSSCAP or — worse — silently maps to a different cap.

OLSSC_MAXSECHANS: Final[int] = 0
OLSSC_MAXDICHANS: Final[int] = 1
OLSSC_CGLDEPTH: Final[int] = 2
OLSSC_NUMFILTERS: Final[int] = 3
OLSSC_NUMGAINS: Final[int] = 4
OLSSC_NUMRANGES: Final[int] = 5
OLSSC_NUMDMACHANS: Final[int] = 6
OLSSC_NUMCHANNELS: Final[int] = 7

OLSSC_SUP_SOFTTRIG: Final[int] = 16
OLSSC_SUP_EXTERNTRIG: Final[int] = 17
OLSSC_SUP_INTCLOCK: Final[int] = 24
OLSSC_SUP_EXTCLOCK: Final[int] = 25

OLSSC_SUP_CONTINUOUS: Final[int] = 35
OLSSC_SUP_SINGLEVALUE: Final[int] = 36
OLSSC_SUP_WRPMULTIPLE: Final[int] = 38
OLSSC_SUP_WRPSINGLE: Final[int] = 39

OLSSC_SUP_INPROCESSFLUSH: Final[int] = 52
OLSSC_SUP_SIMULTANEOUS_SH: Final[int] = 54

# --- Output / synchronous-DIO capability positions (WS-B) ------------------
#
# olssc_tag positions counted from OLDADEFS.H (SDK V7.0.0.7).  Each is
# header-verified by counting the enum and cross-checking that every
# already-bench-verified neighbour in this file lands on the same number
# (OLSSC_SUP_WRPSINGLE=39, OLSSC_SUP_INPROCESSFLUSH=52,
# OLSSC_SUP_SIMULTANEOUS_SH=54, OLSSC_SUP_CTMODE_MEASURE=96,
# OLSSC_RETURNS_FLOATS=116, OLSSC_SUP_MULTISENSOR=143 all match).
#
# ⚠️ HEADER-VERIFIED, BENCH READ-BACK PENDING (§1.4a gate): these have NOT
# yet been confirmed against an olDaGetSSCaps read-back on a live DA
# subsystem.  The continuous-AO path degrades gracefully where it consults
# them (skips mute, defaults WrapMode.MULTIPLE) so a wrong position cannot
# silently drive the DAC; flip the decisions.md rows to "verified" once the
# WS-B bench read-back lands.
OLSSC_MAX_DIGITALIOLIST_VALUE: Final[int] = 46  # OLDADEFS.H olssc_tag
OLSSC_SUP_SYNCHRONOUS_DIGITALIO: Final[int] = 50  # OLDADEFS.H olssc_tag
OLSSC_SUP_WRPWAVEFORM: Final[int] = 97  # OLDADEFS.H olssc_tag (neighbour of CTMODE_MEASURE=96)
OLSSC_CURRENT_OUTPUTS: Final[int] = 117  # OLDADEFS.H olssc_tag (neighbour of RETURNS_FLOATS=116)
OLSSC_SUP_PUT_SINGLE_VALUES: Final[int] = 118  # OLDADEFS.H olssc_tag
OLSSC_SUP_MUTE: Final[int] = 142  # OLDADEFS.H olssc_tag (neighbour of MULTISENSOR=143)

# Float capability (queried via ``olDaGetSSCapsEx`` -> DBL).
OLSSCE_MAX_THROUGHPUT: Final[int] = 61

# Range/throughput/clock float caps (queried via olDaGetSSCapsEx).
OLSSCE_MIN_THROUGHPUT: Final[int] = 62
OLSSCE_MAX_RETRIGGER: Final[int] = 63
OLSSCE_MIN_RETRIGGER: Final[int] = 64
OLSSCE_MAX_CLOCK_DIVIDER: Final[int] = 65
OLSSCE_MIN_CLOCK_DIVIDER: Final[int] = 66
OLSSCE_BASE_CLOCK: Final[int] = 67

OLSSC_SUP_CONTINUOUS_PRETRIG: Final[int] = 89
OLSSC_SUP_CONTINUOUS_ABOUTTRIG: Final[int] = 90

OLSSC_SUP_SINGLEVALUE_AUTORANGE: Final[int] = 94

# Counter/timer mode-support caps (olssc_tag enum positions, OLDADEFS.H).
# Used to gate counter modes the hardware does not expose: the DT9805/06
# C/T reports MEASURE/UP_DOWN/CONT_MEASURE and QUADRATURE_DECODER as 0
# (bench 2026-05-28).  COUNT/RATE/ONESHOT/ONESHOT_RPT are always present
# on a C/T subsystem, so no gate cap is needed for them.
OLSSC_SUP_CTMODE_UP_DOWN: Final[int] = 95
OLSSC_SUP_CTMODE_MEASURE: Final[int] = 96
OLSSC_SUP_FIXED_PULSE_WIDTH: Final[int] = 100
OLSSC_SUP_QUADRATURE_DECODER: Final[int] = 101
OLSSC_SUP_CTMODE_CONT_MEASURE: Final[int] = 102

OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM: Final[int] = 110
OLSSC_SUP_THERMOCOUPLES: Final[int] = 111
OLSSC_SUP_CJC_SOURCE_CHANNEL: Final[int] = 113
OLSSC_SUP_CJC_SOURCE_INTERNAL: Final[int] = 114
OLSSC_RETURNS_FLOATS: Final[int] = 116
OLSSC_SUP_AUTO_CALIBRATE: Final[int] = 121

# Multi-sensor + related (new in newer SDK revs; sequential
# extensions of the OLSSC enum past the original published surface).
OLSSC_SUP_MULTISENSOR: Final[int] = 143
OLSSC_SUP_THERMISTOR: Final[int] = 144
OLSSC_SUP_CURRENT: Final[int] = 145
OLSSC_SUP_BRIDGEBASEDSENSORS: Final[int] = 146
OLSSC_SUP_RESISTANCE: Final[int] = 147
OLSSC_SUP_IEPE: Final[int] = 148

# Aliases for source code that uses dtollib's earlier nomenclature.
OLSSC_SUP_DMA: Final[int] = OLSSC_NUMDMACHANS  # treat >0 as supports-DMA
OLSSC_SUP_AUTOCAL: Final[int] = OLSSC_SUP_AUTO_CALIBRATE
# DA simultaneous output is reported by a different cap on each board —
# the live binding queries by enum position; this constant exists for
# the old name surface and falls back to SIMULTANEOUS_SH.
OLSSC_SUP_SIMULTANEOUS_DA: Final[int] = OLSSC_SUP_SIMULTANEOUS_SH
OLSSC_SUP_SINGLEENDED: Final[int] = 12


# --- Enumerable capability IDs (OLDAAPI.H ``OL_ENUM_*`` family) ------------
#
# Passed to ``olDaEnumSSCaps`` / ``olDaEnumChannelCaps``.

OL_ENUM_FILTERS: Final[int] = 100
OL_ENUM_RANGES: Final[int] = 101
OL_ENUM_GAINS: Final[int] = 102
OL_ENUM_RESOLUTIONS: Final[int] = 103


# --- OLDA_WM_* notification messages (OLDADEFS.H) --------------------------
#
# Windows messages posted by the SDK when ``olDaSetWndHandle`` is used.
# ``OLDA_WM_BUFFER_DONE`` etc. = ``WM_USER + N``.  WM_USER = 0x400.
# The continuous-mode callback bridge dispatches on ``uiMsg`` from the
# ``OLNOTIFYPROC`` callback (which receives the same constants).

_WM_USER: Final[int] = 0x0400

OLDA_WM_TRIGGER_ERROR: Final[int] = _WM_USER + 100
OLDA_WM_UNDERRUN_ERROR: Final[int] = _WM_USER + 101
OLDA_WM_OVERRUN_ERROR: Final[int] = _WM_USER + 102
OLDA_WM_BUFFER_DONE: Final[int] = _WM_USER + 103
OLDA_WM_QUEUE_DONE: Final[int] = _WM_USER + 104
OLDA_WM_BUFFER_REUSED: Final[int] = _WM_USER + 105
OLDA_WM_QUEUE_STOPPED: Final[int] = _WM_USER + 106
OLDA_WM_EVENT_ERROR: Final[int] = _WM_USER + 107
OLDA_WM_MEASURE_DONE: Final[int] = _WM_USER + 108
OLDA_WM_DTCONNECT_DONE: Final[int] = _WM_USER + 109
OLDA_WM_DTCONNECT_STOPPED: Final[int] = _WM_USER + 110
OLDA_WM_EVENT_DONE: Final[int] = _WM_USER + 111
OLDA_WM_PRETRIGGER_BUFFER_DONE: Final[int] = _WM_USER + 112
OLDA_WM_DEVICE_REMOVAL: Final[int] = _WM_USER + 113
OLDA_WM_IO_COMPLETE: Final[int] = _WM_USER + 114


# --- Clock source selectors (OLDADEFS.H ``OLDRV_SETCLOCKSOURCE`` block) ----

OL_CLK_INTERNAL: Final[int] = 400
OL_CLK_EXTERNAL: Final[int] = 401
OL_CLK_EXTRA: Final[int] = 402


# --- Start-trigger selectors (OLDADEFS.H ``OLDRV_SETTRIGGER`` block) -------
#
# Two families coexist in the modern header: the original
# ``OL_TRG_SOFT=300`` family and the newer ``OL_TRG_THRESHPOS=1200``
# pair.  ``OL_TRG_SYNCBUS`` (1202) is the cross-board trigger sync.

OL_TRG_SOFT: Final[int] = 300
OL_TRG_EXTERN: Final[int] = 301
OL_TRG_THRESH: Final[int] = 302  # legacy alias; positive/negative variants below
OL_TRG_ANALOGEVENT: Final[int] = 303
OL_TRG_DIGITALEVENT: Final[int] = 304
OL_TRG_TIMEREVENT: Final[int] = 305
OL_TRG_EXTRA: Final[int] = 306

OL_TRG_THRESHPOS: Final[int] = 1200
OL_TRG_THRESHNEG: Final[int] = 1201
OL_TRG_SYNCBUS: Final[int] = 1202


# --- Wrap-mode selectors (OLDADEFS.H ``OLDRV_SETWRAPMODE`` block) ----------

OL_WRP_NONE: Final[int] = 1000
OL_WRP_MULTIPLE: Final[int] = 1001
OL_WRP_SINGLE: Final[int] = 1002


# --- Queue selectors (OLDADEFS.H ``OLDRV_GETQUEUESIZES`` block) -------------

OL_QUE_READY: Final[int] = 1100
OL_QUE_DONE: Final[int] = 1101
OL_QUE_INPROCESS: Final[int] = 1102


# --- Retrigger-mode selectors (OLDADEFS.H ``OLDRV_GETRETTRIGGERMODE``) -----

OL_RETRIG_INTERNAL: Final[int] = 1300
OL_RETRIG_SCANPERTRIG: Final[int] = 1301
OL_RETRIG_EXTRA: Final[int] = 1302


# --- Counter/timer mode, gate, pulse, edge selectors (OLDADEFS.H) ----------
#
# Bench-verified 2026-05-28 (SDK V7.0.0.7).  Values transcribed directly
# from OLDADEFS.H at %ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\
# (line citations per symbol).  The earlier "set-family offset" guesses
# (1400/1410/1420/1430) were WRONG in both value AND name — the real header
# uses the 500/600/700/900 families and ``OL_PLS_*`` (not ``OL_PULSETYPE_*``)
# / underscored ``OL_GATE_HIGH_LEVEL`` names.  This is the same VB-vs-C
# divergence that caused the 2026-05-28 continuous-mode failure; pinning it
# closes OQ-5a.  See docs/decisions.md counter/timer bench block.

# C/T operation mode (olDaSetCTMode; OLDRV_SETCOUNTERMODE family).
OL_CTMODE_COUNT: Final[int] = 700  # OLDADEFS.H:297
OL_CTMODE_RATE: Final[int] = 701  # OLDADEFS.H:298
OL_CTMODE_ONESHOT: Final[int] = 702  # OLDADEFS.H:299
OL_CTMODE_ONESHOT_RPT: Final[int] = 703  # OLDADEFS.H:300
OL_CTMODE_UP_DOWN: Final[int] = 704  # OLDADEFS.H:301
OL_CTMODE_MEASURE: Final[int] = 705  # OLDADEFS.H:302
OL_CTMODE_CONT_MEASURE: Final[int] = 706  # OLDADEFS.H:303

# Pulse output polarity (olDaSetPulseType; OLDRV_SETPULSETYPE family).
# NOTE: these share the integers 600/601 with the OL_EDGE_* family below —
# different #define families, same values, disambiguated by call site.
OL_PLS_HIGH2LOW: Final[int] = 600  # OLDADEFS.H:293
OL_PLS_LOW2HIGH: Final[int] = 601  # OLDADEFS.H:294

# Gate-enable logic (olDaSetGateType).  OLDADEFS.H:279-289.
OL_GATE_NONE: Final[int] = 500  # software gate == no hardware gate
OL_GATE_HIGH_LEVEL: Final[int] = 501
OL_GATE_LOW_LEVEL: Final[int] = 502
OL_GATE_HIGH_EDGE: Final[int] = 503
OL_GATE_LOW_EDGE: Final[int] = 504
OL_GATE_LEVEL: Final[int] = 505
OL_GATE_HIGH_LEVEL_DEBOUNCE: Final[int] = 506
OL_GATE_LOW_LEVEL_DEBOUNCE: Final[int] = 507
OL_GATE_HIGH_EDGE_DEBOUNCE: Final[int] = 508
OL_GATE_LOW_EDGE_DEBOUNCE: Final[int] = 509
OL_GATE_LEVEL_DEBOUNCE: Final[int] = 510

# Measurement edges (olDaSetMeasureStartEdge / olDaSetMeasureStopEdge).
# OLDADEFS.H:565-566.  (The OLDRV_SETMEASUREMENT 750-776 family — tach /
# clock / CT0 input edges — is only relevant to MEASURE-mode counters,
# which this hardware does not expose; the two we need are below.)
OL_EDGE_FALLING: Final[int] = 600  # OLDADEFS.H:565
OL_EDGE_RISING: Final[int] = 601  # OLDADEFS.H:566

# Tachometer input edges (OLDRV_SETMEASUREMENT family; OLDADEFS.H:311-312).
# Retained for reference — the boards on hand expose no tachometer.
OL_TACHOMETER_INPUT_FALLING: Final[int] = 755
OL_TACHOMETER_INPUT_RISING: Final[int] = 756

# Cascade mode (olDaSetCascadeMode; OLDRV_SETCASCADEMODE family).
# OLDADEFS.H:345-346.  NB: this is a UINT selector, NOT a BOOL — the
# earlier ``1 if cascade else 0`` was wrong.
OL_CT_CASCADE: Final[int] = 900  # OLDADEFS.H:345
OL_CT_SINGLE: Final[int] = 901  # OLDADEFS.H:346

# --- Back-compat aliases for dtollib's earlier (pre-bench) names -----------
# Existing call sites import these; they now resolve to the header-true
# values above.  Prefer the canonical names in new code.
OL_PULSETYPE_LOWTOHI: Final[int] = OL_PLS_LOW2HIGH
OL_PULSETYPE_HITOLOW: Final[int] = OL_PLS_HIGH2LOW
OL_GATE_HIGHLEVEL: Final[int] = OL_GATE_HIGH_LEVEL
OL_GATE_LOWLEVEL: Final[int] = OL_GATE_LOW_LEVEL
OL_GATE_HIGHEDGE: Final[int] = OL_GATE_HIGH_EDGE
OL_GATE_LOWEDGE: Final[int] = OL_GATE_LOW_EDGE
OL_GATE_SWGATE: Final[int] = OL_GATE_NONE

# Quadrature and tachometer are NOT counter modes in OLDADEFS.H, and this
# hardware exposes neither (bench 2026-05-28: get_dass(OLSS_QUAD)/get_dass(
# OLSS_TACH) both return ECODE 3, and the C/T QUADRATURE_DECODER cap reads 0).
# These names are retained only so the typed layer can map
# CounterMode.QUADRATURE / .TACHOMETER to a value the runtime capability gate
# rejects with DtolCapabilityError BEFORE any SDK call.  Never sent to the DLL.
_OL_CTMODE_UNSUPPORTED: Final[int] = -1
OL_CTMODE_QUAD: Final[int] = _OL_CTMODE_UNSUPPORTED
OL_CTMODE_TACH: Final[int] = _OL_CTMODE_UNSUPPORTED


# --- Channel type / Encoding (OLDADEFS.H ``OLDRV_SETCHANNELTYPE`` block) ---

OL_CHNT_SINGLEENDED: Final[int] = 100
OL_CHNT_DIFFERENTIAL: Final[int] = 101

OL_ENC_BINARY: Final[int] = 200
OL_ENC_2SCOMP: Final[int] = 201


# --- Thermocouple types (OLDADEFS.H ``olDaSetThermocoupleType`` block) -----

OL_THERMOCOUPLE_TYPE_NONE: Final[int] = 1500
OL_THERMOCOUPLE_TYPE_J: Final[int] = 1501
OL_THERMOCOUPLE_TYPE_K: Final[int] = 1502
OL_THERMOCOUPLE_TYPE_B: Final[int] = 1503
OL_THERMOCOUPLE_TYPE_E: Final[int] = 1504
OL_THERMOCOUPLE_TYPE_N: Final[int] = 1505
OL_THERMOCOUPLE_TYPE_R: Final[int] = 1506
OL_THERMOCOUPLE_TYPE_S: Final[int] = 1507
OL_THERMOCOUPLE_TYPE_T: Final[int] = 1508


# --- IO_TYPE enum (OLDADEFS.H, used by olDaSetMultiSensorType) -------------
#
# Sequential 0-based enum values.  Passed as the third argument to
# ``olDaSetMultiSensorType`` to re-type a MULTI_SENSOR channel.

IOTYPE_VOLTAGEIN: Final[int] = 0
IOTYPE_VOLTAGEOUT: Final[int] = 1
IOTYPE_DIGITALINPUT: Final[int] = 2
IOTYPE_DIGITALOUTPUT: Final[int] = 3
IOTYPE_QUADRATUREDECODER: Final[int] = 4
IOTYPE_COUNTERTIMER: Final[int] = 5
IOTYPE_TACHOMETER: Final[int] = 6
IOTYPE_CURRENT: Final[int] = 7
IOTYPE_THERMOCOUPLE: Final[int] = 8
IOTYPE_RTD: Final[int] = 9
IOTYPE_STRAINGAGE: Final[int] = 10
IOTYPE_ACCELEROMETER: Final[int] = 11
IOTYPE_BRIDGE: Final[int] = 12
IOTYPE_THERMISTOR: Final[int] = 13
IOTYPE_RESISTANCE: Final[int] = 14
IOTYPE_MULTISENSOR: Final[int] = 15


# --- RTD curve types (OLDADEFS.H ``olDaSetRtdType`` block) -----------------
#
# ``#define OL_RTD_TYPE_*`` values 1608–1614 (OLDADEFS.H:384–390).

OL_RTD_TYPE_PT3750: Final[int] = 1608
OL_RTD_TYPE_PT3850: Final[int] = 1609
OL_RTD_TYPE_PT3911: Final[int] = 1610
OL_RTD_TYPE_PT3916: Final[int] = 1611
OL_RTD_TYPE_PT3920: Final[int] = 1612
OL_RTD_TYPE_PT3928: Final[int] = 1613
OL_RTD_TYPE_CUSTOM: Final[int] = 1614


# --- Coupling type (OLDADEFS.H ``COUPLING_TYPE`` enum:492) -----------------

OL_COUPLING_DC: Final[int] = 0
OL_COUPLING_AC: Final[int] = 1


# --- Excitation-current source (OLDADEFS.H ``EXCITATION_CURRENT_SRC``:498) --

OL_EXCITATION_CURRENT_SRC_INTERNAL: Final[int] = 0
OL_EXCITATION_CURRENT_SRC_EXTERNAL: Final[int] = 1
OL_EXCITATION_CURRENT_SRC_DISABLED: Final[int] = 2


# --- Strain excitation-voltage source (OLDADEFS.H:506) ---------------------

OL_STRAIN_EXCITATION_VOLTAGE_SRC_INTERNAL: Final[int] = 0
OL_STRAIN_EXCITATION_VOLTAGE_SRC_EXTERNAL: Final[int] = 1


# --- Strain-gage configuration (OLDADEFS.H ``STRAIN_GAGE_CONFIGURATION``:512) -

OL_STRAIN_FULL_BRIDGE_BENDING: Final[int] = 0
OL_STRAIN_FULL_BRIDGE_BENDING_POISSON: Final[int] = 1
OL_STRAIN_FULL_BRIDGE_AXIAL: Final[int] = 2
OL_STRAIN_HALF_BRIDGE_POISSON: Final[int] = 3
OL_STRAIN_HALF_BRIDGE_BENDING: Final[int] = 4
OL_STRAIN_QUARTER_BRIDGE: Final[int] = 5
OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION: Final[int] = 6


# --- Bridge configuration (OLDADEFS.H ``BRIDGE_CONFIGURATION``:523) --------
#
# Aliases into the strain-gage enum: FULL=FULL_BRIDGE_BENDING(0),
# HALF=HALF_BRIDGE_POISSON(3), QUARTER=QUARTER_BRIDGE(5).

OL_BRIDGE_FULL: Final[int] = 0
OL_BRIDGE_HALF: Final[int] = 3
OL_BRIDGE_QUARTER: Final[int] = 5


# --- Multi-sensor sentinel values for out-of-range / open sensor ----------
#
# Per OLDADEFS.H — when a multi-sensor channel reads back as one of
# these float values, the SDK is telling us the sensor is open / out
# of range, NOT that the temperature is actually 99999 °C.

SENSOR_IS_OPEN: Final[float] = 99999.0
TEMP_OUT_OF_RANGE_LOW: Final[float] = -88888.0
TEMP_OUT_OF_RANGE_HIGH: Final[float] = 88888.0
