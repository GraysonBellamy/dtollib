"""Opaque type aliases for the DataAcq SDK ctypes binding.

All DataAcq handles are opaque to the application — the SDK manual
documents them as ``HDRVR``, ``HDASS``, ``HBUF``, ``HLIST``, ``HSSLIST``
without exposing their underlying struct. We bind them as
``ctypes.c_void_p`` so they:

- pass through ctypes without truncation on either bitness,
- can be stored in a Python ``int`` (via ``.value``) for ID-style use
  in caches keyed by handle, and
- typecheck distinctly from raw ``c_void_p`` at the binding-Python
  boundary because the aliases below are *type aliases*, not subclasses.

``ECODE`` and ``OLSTATUS`` are interchangeable per ``OLERRORS.H``:
both are ``typedef unsigned long`` (see dasdk_digest.md §8). We
export both names so call-sites read like the SDK manual.

Header verification status lives in docs/decisions.md — every alias
in this module has a row in the type-alias verification table.
"""

from __future__ import annotations

import ctypes
import sys

# ``ctypes.wintypes`` is importable on all platforms (it just contains
# the type definitions).  ``_WINFUNCTYPE`` is defined only on
# Windows; on Linux/macOS we fall back to ``CFUNCTYPE`` so this module
# imports cleanly for type-checking and ``FakeDtolBackend`` testing.
# The real DLLs cannot be loaded on non-Windows anyway (see
# :func:`dtollib.capi.loader.load_openlayers`), so the cdecl-vs-stdcall
# difference is invisible — no real call ever flows through these
# function pointers off Windows.
from ctypes import wintypes

if sys.platform == "win32":
    _WINFUNCTYPE = ctypes.WINFUNCTYPE
else:
    _WINFUNCTYPE = ctypes.CFUNCTYPE

__all__ = [
    "BOARD_ENUM_EX_PROC",
    "BOARD_ENUM_PROC",
    "BRIDGE_SENSOR_TEDS",
    "CHAN_CAP_ENUM_PROC",
    "ECODE",
    "HBUF",
    "HDASS",
    "HDRVR",
    "HLIST",
    "HSSLIST",
    "HWND",
    "LPARAM",
    "OLSTATUS",
    "SS_CAP_ENUM_PROC",
    "SS_ENUM_PROC",
    "STRAIN_GAGE_TEDS",
    "WPARAM",
]


# --- Opaque handle aliases --------------------------------------------------
#
# Each is ``c_void_p`` at the ctypes layer. Distinct aliases mean a
# ``POINTER(HDASS)`` argument typechecks differently from
# ``POINTER(HBUF)`` even though both serialise the same wire bytes.

HDRVR = ctypes.c_void_p
HDASS = ctypes.c_void_p
HBUF = ctypes.c_void_p
HLIST = ctypes.c_void_p
HSSLIST = ctypes.c_void_p

# Win32 window handle — the target of ``olDaSetWndHandle``. The SDK posts
# ``OLDA_WM_*`` buffer-done / error messages to this window. ``wintypes.HWND``
# is ``c_void_p`` and is importable on every platform (the real call only
# ever runs on Windows; see :mod:`dtollib.backend._message_window`).
HWND = wintypes.HWND

# --- Status codes -----------------------------------------------------------
#
# Both names point at the same ctype because the SDK header makes them
# typedef-equivalent. Code paths that wrap raw error codes use
# ``OLSTATUS``; the typed exception ``.context.ecode`` is named after
# the SDK's per-call ``ECODE`` parameter.

ECODE = ctypes.c_ulong
OLSTATUS = ctypes.c_ulong


# --- Win32-flavoured pointer-sized ints ------------------------------------
#
# Pointer-sized on 64-bit Windows — must NOT be ``c_long`` or ``c_uint``,
# which are 32 bits even on x64 and silently truncate ``LPARAM``-sized
# payloads. See docs/design.md §11.5 and the regression test
# in tests/unit/test_capi_callbacks.py.

LPARAM = wintypes.LPARAM
WPARAM = wintypes.WPARAM


# --- Callback prototypes ----------------------------------------------------
#
# Each ``WINFUNCTYPE`` is a stdcall function pointer (DataAcq SDK uses
# the Microsoft Pascal calling convention per dasdk_digest.md §10).
#
# Enumeration callbacks return ``BOOL`` (``c_int``): TRUE continues the
# enumeration, FALSE stops it.
#
# Buffer-done notifications do NOT use a callback typedef: the SDK's
# ``olDaSetNotificationProcedure`` callback never fires on the DT9805/06
# (SDK V7.0.0.7 — see docs/decisions.md). The only working mechanism is
# ``olDaSetWndHandle`` + a Win32 message pump, handled entirely inside
# :mod:`dtollib.backend._message_window`.
#
# Argument lists below follow the SDK manual signatures:
#
# - ``BOARD_ENUM_PROC(lpszBoardName, lParam) -> BOOL``
# - ``BOARD_ENUM_EX_PROC(lpszBoardName, lpszDriverName, lInstance,
#                       lpszRegistryPath, lParam) -> BOOL``
# - ``SS_ENUM_PROC(hdass, lParam) -> BOOL``
#       (the SDK invokes this with the *subsystem handle* the
#       enumeration has implicitly opened; the application sees the
#       enumerated subsystem type via per-callback context)
# - ``SS_CAP_ENUM_PROC(uiEnumCap, dParam1, dParam2, lParam) -> BOOL``
# - ``CHAN_CAP_ENUM_PROC(uiEnumCap, uParam, dParam, lParam) -> BOOL``

BOARD_ENUM_PROC = _WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_char_p,
    LPARAM,
)

BOARD_ENUM_EX_PROC = _WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_long,
    ctypes.c_char_p,
    LPARAM,
)

SS_ENUM_PROC = _WINFUNCTYPE(
    ctypes.c_int,
    HDASS,
    LPARAM,
)

# ``CAPSPROC`` per OLDAAPI.H: BOOL CALLBACK(UINT uiEnumCap, DBL dParam1,
# DBL dParam2, LPARAM lParam).  The first arg is the enum cap ID being
# listed (OL_ENUM_GAINS=102, OL_ENUM_RANGES=101, ...); the value(s) ride
# in dParam1/dParam2.  Declaring it as ``(ulValue, lParam)`` made the
# callback append the cap ID instead of the value (bench: gains came back
# as [102,102,102,102], ranges as [101]) and mismatched the stack.
SS_CAP_ENUM_PROC = _WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_double,
    ctypes.c_double,
    LPARAM,
)

# ``CHANNELCAPSPROC`` per OLDAAPI.H: BOOL CALLBACK(UINT uiEnumCap,
# UINT uParam, DBL dParam, LPARAM lParam).
CHAN_CAP_ENUM_PROC = _WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_double,
    LPARAM,
)


# --- TEDS structs (TedsApi.h) ----------------------------------------------
#
# Output buffers for the four ``olDaRead*Teds`` functions (§8.B5).
# Field order + types transcribed verbatim from TedsApi.h:245 / :278.
# ``GageType`` / ``TedsBridgeType`` / ``PhysicalMeasurandUnits`` are plain
# C enums → ``c_int``.  ``calInitials[3]`` is a fixed 3-byte char array.
# The owned DT9805/06 cannot read hardware TEDS (ec=36); these structs are
# exercised on the fake + via virtual-TEDS files.


class STRAIN_GAGE_TEDS(ctypes.Structure):  # noqa: N801 - mirrors the C struct name.
    """ctypes mirror of ``STRAIN_GAGE_TEDS`` (TedsApi.h:245)."""

    _fields_ = (
        ("manufacturerId", ctypes.c_int),
        ("modelNumber", ctypes.c_int),
        ("versionLetter", ctypes.c_char),
        ("versionNumber", ctypes.c_int),
        ("serialNumber", ctypes.c_uint),
        ("minPhysicalValue", ctypes.c_float),
        ("maxPhysicalValue", ctypes.c_float),
        ("minElecVal", ctypes.c_float),
        ("maxElecVal", ctypes.c_float),
        ("gageType", ctypes.c_int),
        ("gageFactor", ctypes.c_float),
        ("gageTransSens", ctypes.c_float),
        ("gageOffset", ctypes.c_float),
        ("poissonCoef", ctypes.c_float),
        ("youngsMod", ctypes.c_float),
        ("gageArea", ctypes.c_float),
        ("tedsBridgeType", ctypes.c_int),
        ("sensorImped", ctypes.c_float),
        ("respTime", ctypes.c_double),
        ("exciteAmplNom", ctypes.c_float),
        ("exciteAmplMax", ctypes.c_float),
        ("calDaysSince1_1_1998", ctypes.c_uint),
        ("calInitials", ctypes.c_char * 3),
        ("calPeriod", ctypes.c_uint),
        ("measID", ctypes.c_uint),
    )


class BRIDGE_SENSOR_TEDS(ctypes.Structure):  # noqa: N801 - mirrors the C struct name.
    """ctypes mirror of ``BRIDGE_SENSOR_TEDS`` (TedsApi.h:278)."""

    _fields_ = (
        ("manufacturerId", ctypes.c_int),
        ("modelNumber", ctypes.c_int),
        ("versionLetter", ctypes.c_char),
        ("versionNumber", ctypes.c_int),
        ("serialNumber", ctypes.c_uint),
        ("calDaysSince1_1_1998", ctypes.c_uint),
        ("calInitials", ctypes.c_char * 3),
        ("calPeriod", ctypes.c_uint),
        ("exciteAmplMax", ctypes.c_float),
        ("exciteAmplMin", ctypes.c_float),
        ("exciteAmplNom", ctypes.c_float),
        ("maxElecVal", ctypes.c_float),
        ("maxPhysicalValue", ctypes.c_float),
        ("measID", ctypes.c_uint),
        ("minElecVal", ctypes.c_float),
        ("minPhysicalValue", ctypes.c_float),
        ("physicalMeasurand", ctypes.c_int),
        ("respTime", ctypes.c_double),
        ("selector", ctypes.c_uint),
        ("sensorImped", ctypes.c_float),
        ("tedsBridgeType", ctypes.c_int),
    )
