"""Bench probe — configure DT9806 channels 3-7 as K-type thermocouples and
identify which two have TCs connected. Uses olDaSetThermocoupleType per
channel before olDaConfig.

DT9806 caps confirm: SUP_THERMOCOUPLES=1, SUP_CJC_SOURCE_CHANNEL=1,
SUP_MULTISENSOR=0, RETURNS_FLOATS=0. The 9806 returns raw codes, not
float temperatures, so we read raw codes and look for *stability*
(low variance + a believable mean) to identify connected TCs.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from dtollib.capi.constants import (
    OL_DF_CONTINUOUS,
    OL_THERMOCOUPLE_TYPE_K,
    OL_WRP_MULTIPLE,
    OLDA_WM_BUFFER_DONE,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HBUF, HDASS, HDRVR

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(wt.LPARAM, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)
HWND_MESSAGE = wt.HWND(-3)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HANDLE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCSTR),
        ("lpszClassName", wt.LPCSTR),
    ]


user32.DefWindowProcA.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcA.restype = wt.LPARAM
user32.RegisterClassA.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassA.restype = ctypes.c_uint16
user32.CreateWindowExA.argtypes = [
    wt.DWORD,
    wt.LPCSTR,
    wt.LPCSTR,
    wt.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.HWND,
    wt.HANDLE,
    wt.HANDLE,
    ctypes.c_void_p,
]
user32.CreateWindowExA.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
user32.DestroyWindow.restype = ctypes.c_int
user32.PeekMessageA.argtypes = [
    ctypes.POINTER(wt.MSG),
    wt.HWND,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.PeekMessageA.restype = ctypes.c_int
user32.DispatchMessageA.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageA.restype = wt.LPARAM

PM_REMOVE = 0x0001


def proto(lib, fn, args, ret=ctypes.c_ulong):
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def ec_str(ec):
    names = {
        0: "OK",
        8: "BAD_CHAN_TYPE",
        10: "BAD_TRIG",
        18: "BAD_DF",
        20: "IN_USE",
        35: "BAD_WRAP",
        36: "NOT_SUPPORTED",
        89: "BAD_QUEUE",
    }
    return f"ec={ec} ({names.get(ec, '?')})"


def main():
    dlls = load_openlayers()
    da, dm = dlls.oldaapi, dlls.olmem

    proto(da, "olDaInitialize", [ctypes.c_char_p, ctypes.POINTER(HDRVR)])
    proto(da, "olDaTerminate", [HDRVR])
    proto(da, "olDaGetDASS", [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)])
    proto(da, "olDaReleaseDASS", [HDASS])
    proto(da, "olDaConfig", [HDASS])
    proto(da, "olDaStart", [HDASS])
    proto(da, "olDaAbort", [HDASS])
    proto(da, "olDaSetDataFlow", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetWrapMode", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetClockFrequency", [HDASS, ctypes.c_double])
    proto(da, "olDaSetChannelListSize", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint])
    proto(da, "olDaSetGainListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint, ctypes.c_double])
    proto(da, "olDaSetDmaUsage", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetThermocoupleType", [HDASS, ctypes.c_uint, ctypes.c_uint])
    proto(da, "olDaSetReturnCjcTemperatureInStream", [HDASS, ctypes.c_uint])
    proto(da, "olDaGetCjcTemperature", [HDASS, ctypes.POINTER(ctypes.c_float), ctypes.c_uint])
    proto(da, "olDaPutBuffer", [HDASS, HBUF])
    proto(da, "olDaGetBuffer", [HDASS, ctypes.POINTER(HBUF)])
    proto(da, "olDaFlushBuffers", [HDASS])
    proto(da, "olDaSetWndHandle", [HDASS, wt.HWND, wt.LPARAM])

    proto(
        dm,
        "olDmCallocBuffer",
        [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_uint, ctypes.POINTER(HBUF)],
    )
    proto(dm, "olDmFreeBuffer", [HBUF])
    proto(dm, "olDmGetValidSamples", [HBUF, ctypes.POINTER(ctypes.c_ulong)])
    proto(dm, "olDmGetBufferPtr", [HBUF, ctypes.POINTER(ctypes.c_char_p)])

    h_inst = kernel32.GetModuleHandleA(None)
    captured: list[int] = []
    hdass_ref = {"h": HDASS()}

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == OLDA_WM_BUFFER_DONE:
            hb = HBUF()
            ec = da.olDaGetBuffer(hdass_ref["h"], ctypes.byref(hb))
            if ec == 0 and hb.value:
                captured.append(int(hb.value))
                da.olDaPutBuffer(hdass_ref["h"], hb)
            return 0
        return user32.DefWindowProcA(hwnd, msg, wparam, lparam)

    wndproc_cb = WNDPROC(_wndproc)
    wc = WNDCLASS()
    wc.lpfnWndProc = wndproc_cb
    wc.hInstance = h_inst
    wc.lpszClassName = b"DtolBenchTc9806"
    user32.RegisterClassA(ctypes.byref(wc))
    hwnd = user32.CreateWindowExA(
        0, b"DtolBenchTc9806", b"", 0, 0, 0, 0, 0, HWND_MESSAGE, None, h_inst, None
    )

    hdrvr = HDRVR()
    ec = da.olDaInitialize(b"DT9806(00)", ctypes.byref(hdrvr))
    print(f"olDaInitialize(DT9806): {ec_str(ec)}")
    if ec:
        user32.DestroyWindow(hwnd)
        return

    ec = da.olDaGetDASS(hdrvr, 0, 0, ctypes.byref(hdass_ref["h"]))
    print(f"olDaGetDASS(AD,0): {ec_str(ec)}")
    if ec:
        da.olDaTerminate(hdrvr)
        user32.DestroyWindow(hwnd)
        return

    hdass = hdass_ref["h"]

    # --- Configure each candidate TC channel as K-type ----------------------
    print("--- Setting K-type thermocouple type on candidate channels 3-7 ---")
    for ch in range(3, 8):
        ec = da.olDaSetThermocoupleType(hdass, ch, OL_THERMOCOUPLE_TYPE_K)
        print(f"  SetThermocoupleType(ch={ch}, K=1502): {ec_str(ec)}")

    # Also try to enable interleaved CJC reading
    ec = da.olDaSetReturnCjcTemperatureInStream(hdass, 1)
    print(f"  SetReturnCjcTemperatureInStream(1):  {ec_str(ec)}")

    # --- Configure continuous mode -----------------------------------------
    print(f"  SetDataFlow(CONTINUOUS=800): {ec_str(da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS))}")
    print(f"  SetWrapMode(MULTIPLE=1001):  {ec_str(da.olDaSetWrapMode(hdass, OL_WRP_MULTIPLE))}")
    print(f"  SetClockFrequency(100):      {ec_str(da.olDaSetClockFrequency(hdass, 100.0))}")
    print(f"  SetDmaUsage(0):              {ec_str(da.olDaSetDmaUsage(hdass, 0))}")

    # Scan all 8 channels (0-7) — channels 3-7 configured as TC, 0-2 left default
    n_list = 8
    print(f"  SetChannelListSize({n_list}):     {ec_str(da.olDaSetChannelListSize(hdass, n_list))}")
    for i in range(n_list):
        da.olDaSetChannelListEntry(hdass, i, i)

    print(f"  olDaConfig (pre-WndHandle):  {ec_str(da.olDaConfig(hdass))}")

    # --- Buffers ------------------------------------------------------------
    samples_per_buf = 800  # 8 channels * 100 Hz = 800 samples/s -> 1 buf/s
    n_bufs = 4
    hbufs: list[HBUF] = []
    for _ in range(n_bufs):
        hb = HBUF()
        if dm.olDmCallocBuffer(0, 0, samples_per_buf, 2, ctypes.byref(hb)) == 0 and hb.value:
            hbufs.append(hb)
            da.olDaPutBuffer(hdass, hb)
    print(f"  queued {len(hbufs)} buffers")

    # --- Wnd handle + second Config ----------------------------------------
    da.olDaSetWndHandle(hdass, hwnd, 0)
    ec = da.olDaConfig(hdass)
    print(f"  olDaConfig (post-WndHandle): {ec_str(ec)}")

    # --- Try to read CJC once before start ---------------------------------
    cjc = ctypes.c_float(0.0)
    ec = da.olDaGetCjcTemperature(hdass, ctypes.byref(cjc), 0)
    print(f"  GetCjcTemperature(ch=0): {ec_str(ec)}, cjc={cjc.value:.2f} C")

    # --- Start --------------------------------------------------------------
    ec = da.olDaStart(hdass)
    print(f"olDaStart: {ec_str(ec)}")

    # Pump messages and accumulate buffers
    deadline = time.monotonic() + 4.0
    msg = wt.MSG()
    while time.monotonic() < deadline and len(captured) < 2:
        while user32.PeekMessageA(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
            user32.DispatchMessageA(ctypes.byref(msg))
        time.sleep(0.05)

    print(f"--- {len(captured)} buffers captured ---")

    if captured:
        hb = HBUF(captured[0])
        valid = ctypes.c_ulong(0)
        dm.olDmGetValidSamples(hb, ctypes.byref(valid))
        ptr = ctypes.c_char_p()
        dm.olDmGetBufferPtr(hb, ctypes.byref(ptr))
        ptr_int = ctypes.cast(ptr, ctypes.c_void_p).value or 0
        arr = (ctypes.c_int16 * valid.value).from_address(ptr_int)
        samples = list(arr)
        scans = valid.value // n_list

        print(f"Per-channel stats across {scans} scans (TC mode, K-type on ch3-7):")
        for ch in range(n_list):
            vals = [samples[s * n_list + ch] for s in range(scans)]
            mn = min(vals)
            mx = max(vals)
            mean = sum(vals) / len(vals)
            v = mean * 10.0 / 32768.0
            note = ""
            tc_note = ""
            if ch in (3, 4, 5, 6, 7):
                tc_note = " [TC mode]"
                if abs(mean) > 31000:
                    note = "  RAIL (likely open TC)"
                elif (mx - mn) < 500:
                    # Very stable, likely a TC at room temp
                    # For K-type at 25°C, expect ~1 mV which is very near 0 code
                    note = "  STABLE -- likely connected TC"
                elif (mx - mn) < 5000:
                    note = "  semi-stable"
            print(
                f"  ch{ch}: mean={mean:8.1f} ({v:+.4f}V), "
                f"min={mn:6d}, max={mx:6d}, range={mx - mn:5d}{tc_note}{note}"
            )

        # Also read CJC after acquisition
        ec = da.olDaGetCjcTemperature(hdass, ctypes.byref(cjc), 0)
        print(f"  Post-acquisition CJC reading: {ec_str(ec)}, cjc={cjc.value:.2f} C")

    # --- Tear down ---------------------------------------------------------
    da.olDaAbort(hdass)
    da.olDaFlushBuffers(hdass)
    da.olDaReleaseDASS(hdass)
    for hb in hbufs:
        dm.olDmFreeBuffer(hb)
    da.olDaTerminate(hdrvr)
    user32.DestroyWindow(hwnd)
    print("Done.")


if __name__ == "__main__":
    main()
