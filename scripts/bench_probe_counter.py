"""Bench probe — counter/timer selector-constant read-back on DT9805/DT9806.

Closes the OQ-5a read-back half: drives each pinned ``OL_CTMODE_*`` /
``OL_GATE_*`` / ``OL_PLS_*`` / ``OL_EDGE_*`` / cascade selector at the live
C/T subsystem and records whether the SDK accepts it (ECODE 0 = OLNOERROR)
or rejects it (non-zero — wrong value or unsupported mode).  Read alongside
the capability matrix, this proves the constants in ``capi/constants.py``
configure the mode the header names.

This is a *standalone* ctypes script (the bench convention), importing the
pinned constants from :mod:`dtollib.capi.constants` so it tests the real
values rather than a copy.

Run:
    uv run --no-sync python scripts/bench_probe_counter.py
"""

from __future__ import annotations

import ctypes

from dtollib.capi.constants import (
    OL_CT_CASCADE,
    OL_CT_SINGLE,
    OL_CTMODE_CONT_MEASURE,
    OL_CTMODE_COUNT,
    OL_CTMODE_MEASURE,
    OL_CTMODE_ONESHOT,
    OL_CTMODE_ONESHOT_RPT,
    OL_CTMODE_RATE,
    OL_CTMODE_UP_DOWN,
    OL_EDGE_FALLING,
    OL_EDGE_RISING,
    OL_GATE_HIGH_EDGE,
    OL_GATE_HIGH_LEVEL,
    OL_GATE_LOW_EDGE,
    OL_GATE_LOW_LEVEL,
    OL_GATE_NONE,
    OL_PLS_HIGH2LOW,
    OL_PLS_LOW2HIGH,
    OLSS_CT,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HDASS, HDRVR

_ECODE_NAMES = {
    0: "OK",
    3: "BAD_SUBSYSTEM",
    8: "BAD_CHANNEL_TYPE",
    36: "NOT_SUPPORTED",
}


def ec_str(ec: int) -> str:
    return f"ec={ec} ({_ECODE_NAMES.get(ec, '?')})"


def proto(lib, fn: str, args, ret=ctypes.c_ulong) -> None:
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def main() -> None:
    dlls = load_openlayers()
    da = dlls.oldaapi

    proto(da, "olDaInitialize", [ctypes.c_char_p, ctypes.POINTER(HDRVR)])
    proto(da, "olDaTerminate", [HDRVR])
    proto(da, "olDaGetDASS", [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)])
    proto(da, "olDaReleaseDASS", [HDASS])
    proto(da, "olDaSetCTMode", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetGateType", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetPulseType", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetMeasureStartEdge", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetMeasureStopEdge", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetCascadeMode", [HDASS, ctypes.c_uint])

    modes = [
        ("OL_CTMODE_COUNT", OL_CTMODE_COUNT),
        ("OL_CTMODE_RATE", OL_CTMODE_RATE),
        ("OL_CTMODE_ONESHOT", OL_CTMODE_ONESHOT),
        ("OL_CTMODE_ONESHOT_RPT", OL_CTMODE_ONESHOT_RPT),
        ("OL_CTMODE_UP_DOWN", OL_CTMODE_UP_DOWN),
        ("OL_CTMODE_MEASURE", OL_CTMODE_MEASURE),
        ("OL_CTMODE_CONT_MEASURE", OL_CTMODE_CONT_MEASURE),
    ]
    gates = [
        ("OL_GATE_NONE", OL_GATE_NONE),
        ("OL_GATE_HIGH_LEVEL", OL_GATE_HIGH_LEVEL),
        ("OL_GATE_LOW_LEVEL", OL_GATE_LOW_LEVEL),
        ("OL_GATE_HIGH_EDGE", OL_GATE_HIGH_EDGE),
        ("OL_GATE_LOW_EDGE", OL_GATE_LOW_EDGE),
    ]
    pulses = [
        ("OL_PLS_HIGH2LOW", OL_PLS_HIGH2LOW),
        ("OL_PLS_LOW2HIGH", OL_PLS_LOW2HIGH),
    ]
    edges = [
        ("OL_EDGE_RISING", OL_EDGE_RISING),
        ("OL_EDGE_FALLING", OL_EDGE_FALLING),
    ]
    cascades = [
        ("OL_CT_SINGLE", OL_CT_SINGLE),
        ("OL_CT_CASCADE", OL_CT_CASCADE),
    ]

    for board in (b"DT9805(00)", b"DT9806(00)"):
        hdrvr = HDRVR()
        ec = da.olDaInitialize(board, ctypes.byref(hdrvr))
        print(f"==== {board.decode()}  (init {ec_str(ec)})")
        if ec:
            continue
        for element in (0, 1):
            hdass = HDASS()
            ec = da.olDaGetDASS(hdrvr, OLSS_CT, element, ctypes.byref(hdass))
            if ec:
                print(f"  C/T element {element}: get_dass {ec_str(ec)}")
                continue
            print(f"  C/T element {element}:")
            # A C/T mode must be selected before gate/pulse/edge/cascade are
            # meaningful — set COUNT first, then probe the rest under it.
            print("    -- modes (olDaSetCTMode) --")
            for name, val in modes:
                print(f"      {name:24} {val:4} -> {ec_str(da.olDaSetCTMode(hdass, val))}")
            da.olDaSetCTMode(hdass, OL_CTMODE_COUNT)
            print("    -- gates (olDaSetGateType) --")
            for name, val in gates:
                print(f"      {name:24} {val:4} -> {ec_str(da.olDaSetGateType(hdass, val))}")
            print("    -- cascade (olDaSetCascadeMode) --")
            for name, val in cascades:
                print(f"      {name:24} {val:4} -> {ec_str(da.olDaSetCascadeMode(hdass, val))}")
            # Pulse polarity only applies to RATE / one-shot output modes.
            da.olDaSetCTMode(hdass, OL_CTMODE_RATE)
            print("    -- pulse polarity (olDaSetPulseType, under RATE) --")
            for name, val in pulses:
                print(f"      {name:24} {val:4} -> {ec_str(da.olDaSetPulseType(hdass, val))}")
            # Measure edges only apply to MEASURE mode (unsupported here — expect
            # the mode set itself to fail, recorded above).
            print("    -- measure edges (olDaSetMeasureStartEdge) --")
            for name, val in edges:
                print(
                    f"      {name:24} {val:4} -> {ec_str(da.olDaSetMeasureStartEdge(hdass, val))}"
                )
            da.olDaReleaseDASS(hdass)
        da.olDaTerminate(hdrvr)
    print("Done.")


if __name__ == "__main__":
    main()
