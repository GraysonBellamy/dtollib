"""ctypes prototype declarations for the DataAcq SDK surface.

Two declaration functions, applied once per :class:`OpenLayersDlls`
instance:

- :func:`declare_oldaapi` — binds the acquisition/discovery functions
  on the ``oldaapi*.dll`` handle: 15 discovery/lifecycle/capability
  functions plus ~17 single-value configuration + read functions.
- :func:`declare_olmem` — binds the buffer/version/error helpers on
  the ``olmem*.dll`` handle.

Each prototype sets ``argtypes`` + ``restype`` explicitly.  Skipping
``argtypes`` lets ctypes infer types from Python values at call time,
which means a ``long`` Python int silently truncates an ``LPARAM``
without warning — exactly the §11.5 hazard this module exists to
prevent.

The core ``olmem`` surface is only ``olDmGetVersion`` and
``olDmGetErrorString``; the buffer-management functions accompany the
continuous buffer pool.

Header-verification status: see docs/decisions.md.
"""

from __future__ import annotations

from ctypes import POINTER, c_char_p, c_double, c_float, c_int, c_long, c_uint, c_ulong, c_void_p
from typing import TYPE_CHECKING, Any, Protocol, cast

from dtollib.capi.types import (
    BOARD_ENUM_EX_PROC,
    BOARD_ENUM_PROC,
    BRIDGE_SENSOR_TEDS,
    CHAN_CAP_ENUM_PROC,
    ECODE,
    HBUF,
    HDASS,
    HDRVR,
    HSSLIST,
    HWND,
    LPARAM,
    SS_CAP_ENUM_PROC,
    SS_ENUM_PROC,
    STRAIN_GAGE_TEDS,
)

if TYPE_CHECKING:
    from ctypes import CDLL


__all__ = [
    "BUFFER_OLMEM_FUNCTIONS",
    "CONTINUOUS_OLDAAPI_FUNCTIONS",
    "CORE_OLMEM_FUNCTIONS",
    "COUNTER_OLDAAPI_FUNCTIONS",
    "DISCOVERY_OLDAAPI_FUNCTIONS",
    "MULTI_SENSOR_OLDAAPI_FUNCTIONS",
    "OPTIONAL_OLDAAPI_FUNCTIONS",
    "OUTPUT_OLDAAPI_FUNCTIONS",
    "SINGLE_VALUE_OLDAAPI_FUNCTIONS",
    "TEDS_OLDAAPI_FUNCTIONS",
    "WAVEFORM_OLMEM_FUNCTIONS",
    "declare_oldaapi",
    "declare_olmem",
]


# Names of the discovery / lifecycle / capability functions.  The
# ``test_declare_oldaapi_sets_argtypes_and_restype_on_every_function``
# regression test imports these tuples and asserts that every name
# resolves to a callable with ``argtypes`` set on a freshly-declared
# DLL handle.

DISCOVERY_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    "olDaGetVersion",
    "olDaGetErrorString",
    "olDaEnumBoards",
    "olDaEnumBoardsEx",
    "olDaGetBoardInfo",
    "olDaInitialize",
    "olDaTerminate",
    "olDaEnumSubSystems",
    "olDaGetDevCaps",
    "olDaGetDASS",
    "olDaReleaseDASS",
    "olDaGetSSCaps",
    "olDaGetSSCapsEx",
    "olDaEnumSSCaps",
    "olDaEnumChannelCaps",
)


CORE_OLMEM_FUNCTIONS: tuple[str, ...] = (
    "olDmGetVersion",
    "olDmGetErrorString",
)


# Names of the single-value configuration + read-path functions for the
# DT9805 happy path.  Header-verification status lives in
# docs/decisions.md.

SINGLE_VALUE_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    "olDaSetDataFlow",
    "olDaSetChannelType",
    "olDaSetChannelRange",
    "olDaSetRange",
    "olDaSetGainListEntry",
    "olDaSetMultiSensorType",
    "olDaSetThermocoupleType",
    "olDaSetReturnCjcTemperatureInStream",
    "olDaConfig",
    "olDaStart",
    "olDaStop",
    "olDaAbort",
    "olDaIsRunning",
    "olDaGetSingleValue",
    "olDaGetSingleFloat",
    "olDaGetSingleValueEx",
    "olDaGetSingleValues",
    "olDaGetSingleFloats",
    "olDaGetCjcTemperature",
    "olDaCodeToVolts",
    "olDaVoltsToCode",
    "olDaGetEncoding",
    "olDaGetResolution",
    "olDaGetRange",
)


# Optional ``oldaapi*.dll`` functions — absent from some SDK builds
# (confirmed missing in V7.0.0.7). Wrappers in
# :class:`~dtollib.capi.api.OpenLayersApi` use ``getattr`` to detect
# absence and degrade gracefully.
OPTIONAL_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    "olDaSetStopOnError",
    "olDaGetSSState",
)


# Names of the continuous-AI functions + §12.3.2 callback bridge + buffer
# pool.  Each prototype is verified against OLDAAPI.H / OLMEM.H rev
# recorded in docs/decisions.md.

CONTINUOUS_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    # Continuous-mode configuration.
    "olDaSetChannelListSize",
    "olDaSetChannelListEntry",
    "olDaSetChannelListEntryInhibit",
    "olDaSetClockSource",
    "olDaSetClockFrequency",
    "olDaGetClockFrequency",
    "olDaSetExternalClockDivider",
    "olDaSetTrigger",
    "olDaSetTriggerThresholdChannel",
    "olDaSetTriggerThresholdLevel",
    "olDaSetWrapMode",
    "olDaSetDmaUsage",
    # Notification + runtime.
    "olDaSetWndHandle",
    "olDaGetQueueSize",
    # Buffer enqueue / dequeue.
    "olDaPutBuffer",
    "olDaGetBuffer",
    "olDaFlushBuffers",
)


BUFFER_OLMEM_FUNCTIONS: tuple[str, ...] = (
    "olDmCallocBuffer",
    "olDmMallocBuffer",
    "olDmReAllocBuffer",
    "olDmFreeBuffer",
    "olDmGetBufferPtr",
    "olDmGetBufferSize",
    "olDmGetMaxSamples",
    "olDmGetValidSamples",
    "olDmGetDataWidth",
    "olDmGetDataBits",
    "olDmCopyFromBuffer",
)


# Names of the DT9806 output-surface functions: AO/DO single-value writes,
# digital-I/O list configuration, AO mute/unmute, and the host→buffer copy
# used to pre-fill continuous-AO waveform buffers.
# Signatures verified against OLDAAPI.H / Olmem.h (SDK V7.0.0.7,
# 2026-05-28); see docs/decisions.md.

OUTPUT_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    "olDaPutSingleValue",
    "olDaPutSingleValues",
    "olDaSetSynchronousDigitalIOUsage",
    "olDaSetDigitalIOListEntry",
    "olDaMute",
    "olDaUnMute",
)


WAVEFORM_OLMEM_FUNCTIONS: tuple[str, ...] = (
    "olDmCopyToBuffer",
    "olDmCopyBuffer",
)


# Names of the counter/timer, tachometer, quadrature, triggered-scan
# retrigger, and simultaneous-start (HSSLIST) functions.  Signatures from
# dasdk_digest.md pending the OLDAAPI.H bench grep (see
# docs/decisions.md).

COUNTER_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    # Counter/timer configuration.  The C/T clock reuses the generic
    # olDaSetClockSource / olDaSetClockFrequency single-value setters — this
    # DLL (V7.0.0.7) exports no olDaSetCTClock* variants (bench-confirmed
    # 2026-05-28).
    "olDaSetCTMode",
    "olDaSetGateType",
    "olDaSetPulseType",
    "olDaSetPulseWidth",
    "olDaSetMeasureStartEdge",
    "olDaSetMeasureStopEdge",
    "olDaSetCascadeMode",
    # Counter/timer read.
    "olDaReadEvents",
    "olDaMeasureFrequency",
    # Triggered-scan retrigger.
    "olDaSetTriggeredScanUsage",
    "olDaSetMultiscanCount",
    "olDaSetRetriggerMode",
    "olDaSetRetrigger",
    "olDaSetRetriggerFrequency",
    # Simultaneous start (HSSLIST).
    "olDaGetSSList",
    "olDaPutDassToSSList",
    "olDaSimultaneousPrestart",
    "olDaSimultaneousStart",
    "olDaReleaseSSList",
)


# Names of the multi-sensor configuration functions
# (RTD / thermistor / IEPE / strain / bridge / resistance) plus the
# volts→engineering conversions.  Signatures verified against OLDAAPI.H
# / OLDADEFS.H (SDK install, 2026-05-28); see docs/decisions.md.
#
# Every owned DT9805/DT9806 rejects these with ECODE 36 (they target
# intelligent modules DT9828/9829/9837).  The TaskBuilder capability gate
# (§8.B4) raises DtolCapabilityError before they are ever called on owned
# hardware; the OpenLayersApi wrappers also tolerate ec=36 so a probe
# does not crash.

MULTI_SENSOR_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    # RTD configuration.
    "olDaSetRtdType",
    "olDaSetRtdR0",
    "olDaSetRtdA",
    "olDaSetRtdB",
    "olDaSetRtdC",
    # Thermistor configuration (Steinhart–Hart).
    "olDaSetThermistorA",
    "olDaSetThermistorB",
    "olDaSetThermistorC",
    # Coupling + excitation current (IEPE / resistance / RTD / thermistor).
    "olDaSetCouplingType",
    "olDaSetExcitationCurrentSource",
    "olDaSetExcitationCurrentValue",
    # Strain-gage + bridge configuration.
    "olDaSetStrainExcitationVoltageSource",
    "olDaSetStrainExcitationVoltage",
    "olDaSetStrainBridgeConfiguration",
    "olDaSetStrainShuntResistor",
    "olDaSetBridgeConfiguration",
    # Volts → engineering-unit conversions (pure functions, no HDASS state).
    "olDaVoltsToStrain",
    "olDaVoltsToBridgeBasedSensor",
)


# TEDS readers are bound separately (Track B §8.B5) because they need the
# ``STRAIN_GAGE_TEDS`` / ``BRIDGE_SENSOR_TEDS`` ctypes structs in
# :mod:`dtollib.capi.types`.  They are declared in :func:`declare_oldaapi`
# once those structs land (§8.B5).
TEDS_OLDAAPI_FUNCTIONS: tuple[str, ...] = (
    "olDaReadStrainGageHardwareTeds",
    "olDaReadStrainGageVirtualTeds",
    "olDaReadBridgeSensorHardwareTeds",
    "olDaReadBridgeSensorVirtualTeds",
)


class _CFunc(Protocol):
    """ctypes function object with mutable prototype metadata."""

    argtypes: list[Any]
    restype: Any


def _try_bind(dll: CDLL, name: str) -> _CFunc | None:
    """Return the DLL function by name, or ``None`` if not present.

    The DataAcq SDK ships slightly different function sets across
    versions. Optional bindings (``set_stop_on_error``, ``get_ss_state``,
    ...) use this helper so a missing function degrades gracefully
    instead of breaking import.
    """
    if hasattr(dll, name):
        return cast("_CFunc", getattr(dll, name))
    return None


def declare_oldaapi(dll: CDLL) -> None:  # noqa: PLR0915
    """Bind the discovery + single-value ``olDa*`` function prototypes on ``dll``.

    Mutates ``dll`` in place, setting ``argtypes`` + ``restype`` on
    every function in :data:`DISCOVERY_OLDAAPI_FUNCTIONS` +
    :data:`SINGLE_VALUE_OLDAAPI_FUNCTIONS`.  Idempotent: calling twice on
    the same handle re-applies the same bindings.

    Args:
        dll: Handle returned by :func:`ctypes.WinDLL` for
            ``oldaapi*.dll``.

    Raises:
        AttributeError: If a function in the listed sets is missing
            from the DLL.  Indicates a wrong-version SDK or a
            corrupted install; the caller usually wraps this in a
            :class:`~dtollib.errors.DtolDependencyError` with
            installation guidance.
    """
    # ===== Discovery / lifecycle / capability =============================

    # --- Version / error string ---
    f = dll.olDaGetVersion
    f.argtypes = [c_char_p, c_uint]
    f.restype = ECODE

    f = dll.olDaGetErrorString
    f.argtypes = [ECODE, c_char_p, c_uint]
    f.restype = ECODE

    # --- Board enumeration ---
    f = dll.olDaEnumBoards
    f.argtypes = [BOARD_ENUM_PROC, LPARAM]
    f.restype = ECODE

    f = dll.olDaEnumBoardsEx
    f.argtypes = [BOARD_ENUM_EX_PROC, LPARAM]
    f.restype = ECODE

    f = dll.olDaGetBoardInfo
    # (LPSTR lpszBoardName, LPSTR lpszModel, UINT model_size,
    #  LPSTR lpszDriver, UINT driver_size)
    f.argtypes = [c_char_p, c_char_p, c_uint, c_char_p, c_uint]
    f.restype = ECODE

    # --- Device lifecycle ---
    f = dll.olDaInitialize
    f.argtypes = [c_char_p, POINTER(HDRVR)]
    f.restype = ECODE

    f = dll.olDaTerminate
    f.argtypes = [HDRVR]
    f.restype = ECODE

    # --- Subsystem enumeration ---
    f = dll.olDaEnumSubSystems
    f.argtypes = [HDRVR, SS_ENUM_PROC, LPARAM]
    f.restype = ECODE

    # --- Capability queries ---
    f = dll.olDaGetDevCaps
    f.argtypes = [HDRVR, c_uint, c_uint, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDaGetDASS
    f.argtypes = [HDRVR, c_uint, c_uint, POINTER(HDASS)]
    f.restype = ECODE

    f = dll.olDaReleaseDASS
    f.argtypes = [HDASS]
    f.restype = ECODE

    f = dll.olDaGetSSCaps
    f.argtypes = [HDASS, c_uint, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDaGetSSCapsEx
    f.argtypes = [HDASS, c_uint, POINTER(c_double)]
    f.restype = ECODE

    f = dll.olDaEnumSSCaps
    f.argtypes = [HDASS, c_uint, SS_CAP_ENUM_PROC, LPARAM]
    f.restype = ECODE

    f = dll.olDaEnumChannelCaps
    f.argtypes = [HDASS, c_uint, c_uint, CHAN_CAP_ENUM_PROC, LPARAM]
    f.restype = ECODE

    # ===== Single-value configuration + read ==============================

    # --- Subsystem-level configuration ---

    f = dll.olDaSetDataFlow
    # (HDASS, UINT mode)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    # olDaSetStopOnError — absent in SDK V7.0.0.7; bind when present.
    optional_f = _try_bind(dll, "olDaSetStopOnError")
    if optional_f is not None:
        optional_f.argtypes = [HDASS, c_int]
        optional_f.restype = ECODE

    # --- Per-channel configuration ---

    f = dll.olDaSetChannelType
    # (HDASS, UINT channel_type)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetChannelRange
    # (HDASS, DBL gain, UINT channel)
    # NOTE: SDK manual signature varies by version; we use the
    # (DBL min, DBL max, UINT channel) variant per the C examples.
    f.argtypes = [HDASS, c_double, c_double, c_uint]
    f.restype = ECODE

    f = dll.olDaSetRange
    # (HDASS, DBL max, DBL min)
    f.argtypes = [HDASS, c_double, c_double]
    f.restype = ECODE

    f = dll.olDaSetGainListEntry
    # (HDASS, UINT list_index, UINT channel, DBL gain)
    f.argtypes = [HDASS, c_uint, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetMultiSensorType
    # (HDASS, UINT channel, UINT sensor_type)
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetThermocoupleType
    # (HDASS, UINT channel, UINT tc_type)
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetReturnCjcTemperatureInStream
    # (HDASS, BOOL enable)
    f.argtypes = [HDASS, c_int]
    f.restype = ECODE

    f = dll.olDaConfig
    # (HDASS)
    f.argtypes = [HDASS]
    f.restype = ECODE

    # --- State / lifecycle ---

    # olDaGetSSState — absent in SDK V7.0.0.7; bind when present.
    # DataAcqBackend.get_state derives state from is_running() instead.
    optional_f = _try_bind(dll, "olDaGetSSState")
    if optional_f is not None:
        optional_f.argtypes = [HDASS, POINTER(c_ulong)]
        optional_f.restype = ECODE

    f = dll.olDaStart
    f.argtypes = [HDASS]
    f.restype = ECODE

    f = dll.olDaStop
    f.argtypes = [HDASS]
    f.restype = ECODE

    f = dll.olDaAbort
    f.argtypes = [HDASS]
    f.restype = ECODE

    f = dll.olDaIsRunning
    # (HDASS, PBOOL out)
    f.argtypes = [HDASS, POINTER(c_int)]
    f.restype = ECODE

    # --- Single-value reads ---

    f = dll.olDaGetSingleValue
    # (HDASS, PLNG out, UINT channel, DBL gain)
    f.argtypes = [HDASS, POINTER(c_ulong), c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaGetSingleFloat
    # (HDASS, PFLT out, UINT channel, DBL gain)
    f.argtypes = [HDASS, POINTER(c_float), c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaGetSingleValueEx
    # Autoranging variant — same signature as olDaGetSingleValue.
    f.argtypes = [HDASS, POINTER(c_ulong), c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaGetSingleValues
    # (HDASS, PLNG out_array, DBL gain) — simultaneous SH
    f.argtypes = [HDASS, POINTER(c_ulong), c_double]
    f.restype = ECODE

    f = dll.olDaGetSingleFloats
    # (HDASS, PFLT out_array, DBL gain) — simultaneous SH + engineering
    f.argtypes = [HDASS, POINTER(c_float), c_double]
    f.restype = ECODE

    f = dll.olDaGetCjcTemperature
    # (HDASS, PFLT out, UINT channel)
    f.argtypes = [HDASS, POINTER(c_float), c_uint]
    f.restype = ECODE

    f = dll.olDaCodeToVolts
    # (HDASS, LNG code, PDBL out, DBL gain)
    f.argtypes = [HDASS, c_ulong, POINTER(c_double), c_double]
    f.restype = ECODE

    f = dll.olDaVoltsToCode
    # (HDASS, DBL volts, PLNG out, DBL gain)
    f.argtypes = [HDASS, c_double, POINTER(c_ulong), c_double]
    f.restype = ECODE

    # --- Input scaling (needed because olDaCodeToVolts returns ECODE=9
    #     "Invalid Encoding" on the DT9805/DT9806 AD; we convert ourselves). ---

    f = dll.olDaGetEncoding
    # (HDASS, PUINT out)
    f.argtypes = [HDASS, POINTER(c_uint)]
    f.restype = ECODE

    f = dll.olDaGetResolution
    # (HDASS, PUINT out_bits)
    f.argtypes = [HDASS, POINTER(c_uint)]
    f.restype = ECODE

    f = dll.olDaGetRange
    # (HDASS, PDBL out_max, PDBL out_min)
    f.argtypes = [HDASS, POINTER(c_double), POINTER(c_double)]
    f.restype = ECODE

    # ===== Continuous AI ===================================================

    # --- Continuous-mode channel-list configuration ---

    f = dll.olDaSetChannelListSize
    # (HDASS, UINT size)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetChannelListEntry
    # (HDASS, UINT list_index, UINT channel)
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetChannelListEntryInhibit
    # (HDASS, UINT list_index, BOOL inhibit)
    f.argtypes = [HDASS, c_uint, c_int]
    f.restype = ECODE

    # --- Clock configuration ---

    f = dll.olDaSetClockSource
    # (HDASS, UINT source)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetClockFrequency
    # (HDASS, DBL frequency_hz)
    f.argtypes = [HDASS, c_double]
    f.restype = ECODE

    f = dll.olDaGetClockFrequency
    # (HDASS, PDBL out_hz)
    f.argtypes = [HDASS, POINTER(c_double)]
    f.restype = ECODE

    f = dll.olDaSetExternalClockDivider
    # (HDASS, ULNG divider)
    f.argtypes = [HDASS, c_ulong]
    f.restype = ECODE

    # --- Trigger configuration ---

    f = dll.olDaSetTrigger
    # (HDASS, UINT trigger_kind)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetTriggerThresholdChannel
    # (HDASS, UINT channel)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetTriggerThresholdLevel
    # (HDASS, DBL level_volts)
    f.argtypes = [HDASS, c_double]
    f.restype = ECODE

    # --- Buffer / DMA configuration ---

    f = dll.olDaSetWrapMode
    # (HDASS, UINT mode)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetDmaUsage
    # (HDASS, UINT n_channels)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    # --- Notification ---
    #
    # Buffer-done events are delivered via olDaSetWndHandle + a Win32 message
    # pump (the olDaSetNotificationProcedure callback never fires on the
    # DT9805/06, SDK V7.0.0.7 — see docs/decisions.md).
    f = dll.olDaSetWndHandle
    # (HDASS, HWND hWnd, LPARAM lParam) — verified against OLDAAPI.H rev
    # SDK V7.0.0.7 (2026-05-28).
    f.argtypes = [HDASS, HWND, LPARAM]
    f.restype = ECODE

    f = dll.olDaGetQueueSize
    # (HDASS, UINT queue, PULNG out_size) — queue selector is the
    # OLx_QUE_* enum (0=Ready, 1=Inprocess, 2=Done).
    f.argtypes = [HDASS, c_uint, POINTER(c_ulong)]
    f.restype = ECODE

    # --- Buffer enqueue / dequeue ---

    f = dll.olDaPutBuffer
    # (HDASS, HBUF hbuf) — push onto the Ready queue.
    f.argtypes = [HDASS, HBUF]
    f.restype = ECODE

    f = dll.olDaGetBuffer
    # (HDASS, PHBUF out_hbuf) — pop from the Done queue.
    f.argtypes = [HDASS, POINTER(HBUF)]
    f.restype = ECODE

    f = dll.olDaFlushBuffers
    # (HDASS) — empty Ready + Done queues.
    f.argtypes = [HDASS]
    f.restype = ECODE

    # olDmCopyFromBuffer lives on the olmem DLL — declared in declare_olmem.

    # ===== Output surface (DT9806 AO / DO / DIO) ==========================

    f = dll.olDaPutSingleValue
    # (HDASS, LNG lValue, UINT uiChannel, DBL dGain) — one-shot code write.
    # OLDAAPI.H: ECODE olDaPutSingleValue(HDASS, LNG, UINT, DBL).
    f.argtypes = [HDASS, c_long, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaPutSingleValues
    # (HDASS, PLNG plValues, DBL dGain) — simultaneous code write across the
    # configured channel list.  OLDAAPI.H: ECODE olDaPutSingleValues(HDASS,
    # PLNG, DBL).
    f.argtypes = [HDASS, POINTER(c_long), c_double]
    f.restype = ECODE

    f = dll.olDaSetSynchronousDigitalIOUsage
    # (HDASS, BOOL fUse) — enable scan-synchronised digital I/O.
    f.argtypes = [HDASS, c_int]
    f.restype = ECODE

    f = dll.olDaSetDigitalIOListEntry
    # (HDASS, UINT uiEntry, UINT uiValue) — bind a digital port at a list slot.
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    # olDaMute / olDaUnMute are declared in OLDAAPI.H but are NOT exported
    # by oldaapi64.dll V7.0.0.7 (bench-confirmed 2026-05-28).  Bind them
    # optionally so a DLL build lacking them does not break backend init;
    # the AO mute path raises a clean capability error at call time.
    mute = _try_bind(dll, "olDaMute")
    if mute is not None:
        mute.argtypes = [HDASS]
        mute.restype = ECODE
    unmute = _try_bind(dll, "olDaUnMute")
    if unmute is not None:
        unmute.argtypes = [HDASS]
        unmute.restype = ECODE

    # ===== Counter/timer, tachometer, quadrature, sim-start ===============

    # --- Counter/timer configuration ---

    f = dll.olDaSetCTMode
    # (HDASS, UINT mode) — OL_CTMODE_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    # The C/T clock is configured through the generic olDaSetClockSource /
    # olDaSetClockFrequency (declared with the single-value setters): this
    # DLL exports no olDaSetCTClock* variants.  See
    # OpenLayersApi.set_ct_clock_*.

    f = dll.olDaSetGateType
    # (HDASS, UINT gate) — OL_GATE_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetPulseType
    # (HDASS, UINT polarity) — OL_PULSETYPE_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetPulseWidth
    # (HDASS, DBL duty_or_width) — duty cycle (0..1) for rate, width for one-shot.
    f.argtypes = [HDASS, c_double]
    f.restype = ECODE

    f = dll.olDaSetMeasureStartEdge
    # (HDASS, UINT edge) — OL_EDGE_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetMeasureStopEdge
    # (HDASS, UINT edge) — OL_EDGE_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetCascadeMode
    # (HDASS, BOOL cascade) — cascade two counters into a 32-bit counter.
    f.argtypes = [HDASS, c_int]
    f.restype = ECODE

    # --- Counter/timer read ---

    f = dll.olDaReadEvents
    # (HDASS, UINT channel, PULNG out_count)
    f.argtypes = [HDASS, c_uint, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDaMeasureFrequency
    # (HDASS, UINT channel, PDBL out_freq_hz)
    f.argtypes = [HDASS, c_uint, POINTER(c_double)]
    f.restype = ECODE

    # --- Triggered-scan retrigger ---

    f = dll.olDaSetTriggeredScanUsage
    # (HDASS, UINT enable)
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetMultiscanCount
    # (HDASS, ULNG count) — scans per trigger.
    f.argtypes = [HDASS, c_ulong]
    f.restype = ECODE

    f = dll.olDaSetRetriggerMode
    # (HDASS, UINT mode) — OL_RETRIG_* selector.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetRetrigger
    # (HDASS, UINT source) — retrigger source for EXTRA mode (OL_TRG_* selector).
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetRetriggerFrequency
    # (HDASS, DBL freq_hz) — internal retrigger rate for INTERNAL mode.
    f.argtypes = [HDASS, c_double]
    f.restype = ECODE

    # --- Simultaneous start (HSSLIST) ---

    f = dll.olDaGetSSList
    # (HDRVR, PHSSLIST out_list)
    f.argtypes = [HDRVR, POINTER(HSSLIST)]
    f.restype = ECODE

    f = dll.olDaPutDassToSSList
    # (HSSLIST, HDASS)
    f.argtypes = [HSSLIST, HDASS]
    f.restype = ECODE

    f = dll.olDaSimultaneousPrestart
    # (HSSLIST) — arm every subsystem in the list.  Header spelling is
    # "Prestart" (OLDAAPI.H:243), not "PreStart".
    f.argtypes = [HSSLIST]
    f.restype = ECODE

    f = dll.olDaSimultaneousStart
    # (HSSLIST) — start every subsystem in the list on one trigger.
    f.argtypes = [HSSLIST]
    f.restype = ECODE

    f = dll.olDaReleaseSSList
    # (HSSLIST) — release the simultaneous-start list handle.
    f.argtypes = [HSSLIST]
    f.restype = ECODE

    # ===== Multi-sensor configuration (DT9828/9829/9837) ==================
    #
    # OLDADEFS.H enum args (COUPLING_TYPE, EXCITATION_CURRENT_SRC,
    # STRAIN_GAGE_CONFIGURATION, BRIDGE_CONFIGURATION, OL_RTD_TYPE_*) are
    # plain C ints → c_uint here.

    # --- RTD ---

    f = dll.olDaSetRtdType
    # (HDASS, UINT channel, UINT rtd_type) — OL_RTD_TYPE_* selector.
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetRtdR0
    # (HDASS, UINT channel, DBL r0_ohms)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetRtdA
    # (HDASS, UINT channel, DBL a) — Callendar-Van Dusen A.
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetRtdB
    # (HDASS, UINT channel, DBL b)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetRtdC
    # (HDASS, UINT channel, DBL c)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    # --- Thermistor (Steinhart-Hart) ---

    f = dll.olDaSetThermistorA
    # (HDASS, UINT channel, DBL a)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetThermistorB
    # (HDASS, UINT channel, DBL b)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    f = dll.olDaSetThermistorC
    # (HDASS, UINT channel, DBL c)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    # --- Coupling + excitation current ---

    f = dll.olDaSetCouplingType
    # (HDASS, UINT channel, COUPLING_TYPE coupling) — DC=0, AC=1.
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetExcitationCurrentSource
    # (HDASS, UINT channel, EXCITATION_CURRENT_SRC src) — INTERNAL=0/EXTERNAL=1/DISABLED=2.
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetExcitationCurrentValue
    # (HDASS, UINT channel, DBL amps)
    f.argtypes = [HDASS, c_uint, c_double]
    f.restype = ECODE

    # --- Strain gage + bridge ---
    #
    # NOTE: the two strain *excitation* setters take NO channel argument —
    # excitation voltage is a subsystem-wide property (OLDAAPI.H:318/320).

    f = dll.olDaSetStrainExcitationVoltageSource
    # (HDASS, STRAIN_EXCITATION_VOLTAGE_SRC src) — INTERNAL=0/EXTERNAL=1.
    f.argtypes = [HDASS, c_uint]
    f.restype = ECODE

    f = dll.olDaSetStrainExcitationVoltage
    # (HDASS, DBL volts)
    f.argtypes = [HDASS, c_double]
    f.restype = ECODE

    f = dll.olDaSetStrainBridgeConfiguration
    # (HDASS, UINT channel, STRAIN_GAGE_CONFIGURATION cfg)
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    f = dll.olDaSetStrainShuntResistor
    # (HDASS, UINT channel, BOOL enabled)
    f.argtypes = [HDASS, c_uint, c_int]
    f.restype = ECODE

    f = dll.olDaSetBridgeConfiguration
    # (HDASS, UINT channel, BRIDGE_CONFIGURATION cfg)
    f.argtypes = [HDASS, c_uint, c_uint]
    f.restype = ECODE

    # --- Volts → engineering-unit conversions (pure, no HDASS) ---

    f = dll.olDaVoltsToStrain
    # (STRAIN_GAGE_CONFIGURATION cfg, DBL Vu, DBL Vs, DBL Vex, DBL GF,
    #  DBL Rg, DBL Rl, DBL Pr, DBL ShuntCorrection, PDBL out_strain)
    f.argtypes = [
        c_uint,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        POINTER(c_double),
    ]
    f.restype = ECODE

    f = dll.olDaVoltsToBridgeBasedSensor
    # (DBL Vu, DBL Vs, DBL Vex, DBL Tc, DBL Rg, DBL Rl, DBL RoInmV_V,
    #  DBL ShuntCorrection, PDBL out_value)
    f.argtypes = [
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        c_double,
        POINTER(c_double),
    ]
    f.restype = ECODE

    # --- TEDS readers (§8.B5) — output struct pointers (TedsApi.h) ---

    f = dll.olDaReadStrainGageHardwareTeds
    # (HDASS, UINT channel, PSTRAIN_GAGE_TEDS out)
    f.argtypes = [HDASS, c_uint, POINTER(STRAIN_GAGE_TEDS)]
    f.restype = ECODE

    f = dll.olDaReadStrainGageVirtualTeds
    # (PCHAR virtual_teds_filename, PSTRAIN_GAGE_TEDS out)
    f.argtypes = [c_char_p, POINTER(STRAIN_GAGE_TEDS)]
    f.restype = ECODE

    f = dll.olDaReadBridgeSensorHardwareTeds
    # (HDASS, UINT channel, PBRIDGE_SENSOR_TEDS out)
    f.argtypes = [HDASS, c_uint, POINTER(BRIDGE_SENSOR_TEDS)]
    f.restype = ECODE

    f = dll.olDaReadBridgeSensorVirtualTeds
    # (PCHAR virtual_teds_filename, PBRIDGE_SENSOR_TEDS out)
    f.argtypes = [c_char_p, POINTER(BRIDGE_SENSOR_TEDS)]
    f.restype = ECODE

    # olDmCopyToBuffer / olDmCopyBuffer live on the olmem DLL.


def declare_olmem(dll: CDLL) -> None:
    """Bind the ``olDm*`` function prototypes on ``dll``.

    The core olmem surface is the version + error-string helpers; the
    buffer-management functions (``olDmAllocBuffer``,
    ``olDmGetBufferPtr``, etc.) accompany the continuous buffer pool.

    Args:
        dll: Handle returned by :func:`ctypes.WinDLL` for
            ``olmem*.dll``.
    """
    f = dll.olDmGetVersion
    f.argtypes = [c_char_p, c_uint]
    f.restype = ECODE

    f = dll.olDmGetErrorString
    f.argtypes = [ECODE, c_char_p, c_uint]
    f.restype = ECODE

    # ===== Buffer allocation + introspection ==============================

    f = dll.olDmCallocBuffer
    # (HGLOBAL hmem, HGLOBAL extra, ULNG n_samples, UINT bytes_per_sample,
    #  PHBUF out_hbuf) — zero-fills.
    f.argtypes = [c_ulong, c_ulong, c_ulong, c_uint, POINTER(HBUF)]
    f.restype = ECODE

    f = dll.olDmMallocBuffer
    # Same shape as Calloc but uninitialised.
    f.argtypes = [c_ulong, c_ulong, c_ulong, c_uint, POINTER(HBUF)]
    f.restype = ECODE

    f = dll.olDmReAllocBuffer
    # (HBUF hbuf, ULNG n_samples)
    f.argtypes = [HBUF, c_ulong]
    f.restype = ECODE

    f = dll.olDmFreeBuffer
    f.argtypes = [HBUF]
    f.restype = ECODE

    f = dll.olDmGetBufferPtr
    # (HBUF hbuf, PVOID *out_ptr) — returns the data pointer.
    f.argtypes = [HBUF, POINTER(c_char_p)]
    f.restype = ECODE

    f = dll.olDmGetBufferSize
    # (HBUF hbuf, PULNG out_n_samples)
    f.argtypes = [HBUF, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDmGetMaxSamples
    # (HBUF hbuf, PULNG out_max_samples)
    f.argtypes = [HBUF, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDmGetValidSamples
    # (HBUF hbuf, PULNG out_valid_samples)
    f.argtypes = [HBUF, POINTER(c_ulong)]
    f.restype = ECODE

    f = dll.olDmGetDataWidth
    # (HBUF hbuf, PUINT out_bytes_per_sample)
    f.argtypes = [HBUF, POINTER(c_uint)]
    f.restype = ECODE

    f = dll.olDmGetDataBits
    # (HBUF hbuf, PUINT out_resolution_bits)
    f.argtypes = [HBUF, POINTER(c_uint)]
    f.restype = ECODE

    f = dll.olDmCopyFromBuffer
    # (HBUF hbuf, PVOID dst, ULNG count, PULNG actual) — drain an
    # Inprocess HBUF without waiting.
    f.argtypes = [HBUF, c_char_p, c_ulong, POINTER(c_ulong)]
    f.restype = ECODE

    # ===== Host→buffer copy for continuous-AO waveform fill ===============

    f = dll.olDmCopyToBuffer
    # (HBUF hBuf, LPVOID lpAppBuffer, ULNG ulNumSamples) — fill an HBUF from
    # an application array.  Olmem.h: ECODE olDmCopyToBuffer(HBUF, LPVOID, ULNG).
    f.argtypes = [HBUF, c_void_p, c_ulong]
    f.restype = ECODE

    f = dll.olDmCopyBuffer
    # (HBUF, LPVOID) — copy an HBUF's valid samples into an application array.
    # Olmem.h: ECODE olDmCopyBuffer(HBUF, LPVOID).
    f.argtypes = [HBUF, c_void_p]
    f.restype = ECODE
