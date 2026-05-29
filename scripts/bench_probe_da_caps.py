"""Bench probe (WS-B / S0 follow-up) — read back DA-subsystem capabilities.

Confirms, against a live board, (a) whether the DA subsystem supports
continuous / waveform output at all, and (b) the six WS-B output-capability
enum positions transcribed into capi/constants.py.

Prints AD and DA side by side so a "supported on AD, not on DA" answer is
obvious.
"""

from __future__ import annotations

import ctypes

from dtollib.capi.constants import (
    OLSS_AD,
    OLSS_DA,
    OLSSC_CURRENT_OUTPUTS,
    OLSSC_MAX_DIGITALIOLIST_VALUE,
    OLSSC_NUMCHANNELS,
    OLSSC_NUMDMACHANS,
    OLSSC_SUP_CONTINUOUS,
    OLSSC_SUP_MUTE,
    OLSSC_SUP_PUT_SINGLE_VALUES,
    OLSSC_SUP_SIMULTANEOUS_SH,
    OLSSC_SUP_SINGLEVALUE,
    OLSSC_SUP_SYNCHRONOUS_DIGITALIO,
    OLSSC_SUP_WRPMULTIPLE,
    OLSSC_SUP_WRPSINGLE,
    OLSSC_SUP_WRPWAVEFORM,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HDASS, HDRVR

DEVICE_NAME = b"DT9806(00)"

# Positions not transcribed into constants.py; query by literal.
OLSSC_SUP_FIFO = 31
OLSSC_SUP_WRPWAVEFORM_ONLY = 109

_CAPS = [
    ("NUMCHANNELS", OLSSC_NUMCHANNELS),
    ("NUMDMACHANS", OLSSC_NUMDMACHANS),
    ("SUP_FIFO", OLSSC_SUP_FIFO),
    ("SUP_SINGLEVALUE", OLSSC_SUP_SINGLEVALUE),
    ("SUP_CONTINUOUS", OLSSC_SUP_CONTINUOUS),
    ("SUP_WRPMULTIPLE", OLSSC_SUP_WRPMULTIPLE),
    ("SUP_WRPSINGLE", OLSSC_SUP_WRPSINGLE),
    ("SUP_WRPWAVEFORM (97)", OLSSC_SUP_WRPWAVEFORM),
    ("SUP_WRPWAVEFORM_ONLY (109)", OLSSC_SUP_WRPWAVEFORM_ONLY),
    ("SUP_PUT_SINGLE_VALUES (118)", OLSSC_SUP_PUT_SINGLE_VALUES),
    ("SUP_MUTE (142)", OLSSC_SUP_MUTE),
    ("CURRENT_OUTPUTS (117)", OLSSC_CURRENT_OUTPUTS),
    ("SUP_SYNCHRONOUS_DIGITALIO (50)", OLSSC_SUP_SYNCHRONOUS_DIGITALIO),
    ("MAX_DIGITALIOLIST_VALUE (46)", OLSSC_MAX_DIGITALIOLIST_VALUE),
    ("SUP_SIMULTANEOUS_SH (54)", OLSSC_SUP_SIMULTANEOUS_SH),
]


def main() -> None:
    dlls = load_openlayers()
    da = dlls.oldaapi
    da.olDaInitialize.argtypes = [ctypes.c_char_p, ctypes.POINTER(HDRVR)]
    da.olDaInitialize.restype = ctypes.c_ulong
    da.olDaTerminate.argtypes = [HDRVR]
    da.olDaGetDASS.argtypes = [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)]
    da.olDaGetDASS.restype = ctypes.c_ulong
    da.olDaReleaseDASS.argtypes = [HDASS]
    da.olDaGetSSCaps.argtypes = [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)]
    da.olDaGetSSCaps.restype = ctypes.c_ulong

    hdrvr = HDRVR()
    ec = da.olDaInitialize(DEVICE_NAME, ctypes.byref(hdrvr))
    print(f"olDaInitialize({DEVICE_NAME!r}): ec={ec}")
    if ec:
        return

    def caps_for(subsystem_type: int) -> dict[int, int | str]:
        hdass = HDASS()
        gec = da.olDaGetDASS(hdrvr, subsystem_type, 0, ctypes.byref(hdass))
        if gec:
            return {}
        out: dict[int, int | str] = {}
        for _, cap_id in _CAPS:
            val = ctypes.c_ulong(0)
            rec = da.olDaGetSSCaps(hdass, cap_id, ctypes.byref(val))
            out[cap_id] = val.value if rec == 0 else f"ec={rec}"
        da.olDaReleaseDASS(hdass)
        return out

    ad = caps_for(OLSS_AD)
    da_caps = caps_for(OLSS_DA)

    print(f"\n{'capability':<34} {'AD (OLSS_AD)':>14} {'DA (OLSS_DA)':>14}")
    print("-" * 64)
    for label, cap_id in _CAPS:
        print(f"{label:<34} {ad.get(cap_id, '-')!s:>14} {da_caps.get(cap_id, '-')!s:>14}")

    da.olDaTerminate(hdrvr)
    print("\nVerdict:")
    cont = da_caps.get(OLSSC_SUP_CONTINUOUS)
    sv = da_caps.get(OLSSC_SUP_SINGLEVALUE)
    print(
        f"  DA SUP_CONTINUOUS = {cont}  -> continuous AO (play()) {'POSSIBLE' if cont == 1 else 'NOT SUPPORTED'}"
    )
    print(
        f"  DA SUP_SINGLEVALUE = {sv}  -> single-value write {'OK' if sv == 1 else 'NOT SUPPORTED'}"
    )


if __name__ == "__main__":
    main()
