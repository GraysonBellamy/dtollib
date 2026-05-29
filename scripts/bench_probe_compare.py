"""Bench probe — run an 8-channel continuous-mode scan against both
DT9805(00) and DT9806(00) and report channel means side-by-side.

The board with the connected thermocouples should show channels with
stable, non-railed readings (TCs at room temp produce mV signals).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from dtollib.capi.constants import (
    OL_DF_CONTINUOUS,
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


def acquire_one_buffer(
    board_name: bytes, da, dm, hwnd_class_atom: int
) -> tuple[list[int], int, int]:
    """Acquire one 1000-sample 8-channel buffer from the given board.

    Returns (samples, n_channels, ec).  ``ec`` is non-zero if anything failed.
    """
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
    # Register a unique class per call to keep WNDPROC reference local
    class_name = b"DtolBenchCompare" + board_name
    wc = WNDCLASS()
    wc.lpfnWndProc = wndproc_cb
    wc.hInstance = h_inst
    wc.lpszClassName = class_name
    user32.RegisterClassA(ctypes.byref(wc))
    hwnd = user32.CreateWindowExA(
        0, class_name, b"", 0, 0, 0, 0, 0, HWND_MESSAGE, None, h_inst, None
    )

    hdrvr = HDRVR()
    ec = da.olDaInitialize(board_name, ctypes.byref(hdrvr))
    if ec:
        user32.DestroyWindow(hwnd)
        return [], 8, ec

    ec = da.olDaGetDASS(hdrvr, 0, 0, ctypes.byref(hdass_ref["h"]))
    if ec:
        da.olDaTerminate(hdrvr)
        user32.DestroyWindow(hwnd)
        return [], 8, ec

    hdass = hdass_ref["h"]
    n_chan_cap = ctypes.c_ulong(0)
    da.olDaGetSSCaps(hdass, 0, ctypes.byref(n_chan_cap))  # MAXSECHANS
    n_list = 8

    da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS)
    da.olDaSetWrapMode(hdass, OL_WRP_MULTIPLE)
    da.olDaSetClockFrequency(hdass, 1000.0)
    da.olDaSetDmaUsage(hdass, 0)
    da.olDaSetChannelListSize(hdass, n_list)
    for i in range(n_list):
        da.olDaSetChannelListEntry(hdass, i, i)
    da.olDaConfig(hdass)

    samples_per_buf = 1000
    hbufs: list[HBUF] = []
    for _ in range(4):
        hb = HBUF()
        if dm.olDmCallocBuffer(0, 0, samples_per_buf, 2, ctypes.byref(hb)) == 0 and hb.value:
            hbufs.append(hb)
            da.olDaPutBuffer(hdass, hb)

    da.olDaSetWndHandle(hdass, hwnd, 0)
    da.olDaConfig(hdass)
    da.olDaStart(hdass)

    deadline = time.monotonic() + 4.0
    msg = wt.MSG()
    while time.monotonic() < deadline and not captured:
        while user32.PeekMessageA(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
            user32.DispatchMessageA(ctypes.byref(msg))
        time.sleep(0.05)

    samples: list[int] = []
    if captured:
        hb = HBUF(captured[0])
        valid = ctypes.c_ulong(0)
        dm.olDmGetValidSamples(hb, ctypes.byref(valid))
        ptr = ctypes.c_char_p()
        dm.olDmGetBufferPtr(hb, ctypes.byref(ptr))
        ptr_int = ctypes.cast(ptr, ctypes.c_void_p).value or 0
        arr = (ctypes.c_int16 * valid.value).from_address(ptr_int)
        samples = list(arr)

    da.olDaAbort(hdass)
    da.olDaFlushBuffers(hdass)
    da.olDaReleaseDASS(hdass)
    for hb in hbufs:
        dm.olDmFreeBuffer(hb)
    da.olDaTerminate(hdrvr)
    user32.DestroyWindow(hwnd)
    return samples, n_list, 0


def proto_all(da, dm):
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
    proto(da, "olDaSetDmaUsage", [HDASS, ctypes.c_uint])
    proto(da, "olDaPutBuffer", [HDASS, HBUF])
    proto(da, "olDaGetBuffer", [HDASS, ctypes.POINTER(HBUF)])
    proto(da, "olDaFlushBuffers", [HDASS])
    proto(da, "olDaGetSSCaps", [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)])
    proto(da, "olDaSetWndHandle", [HDASS, wt.HWND, wt.LPARAM])
    proto(
        dm,
        "olDmCallocBuffer",
        [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_uint, ctypes.POINTER(HBUF)],
    )
    proto(dm, "olDmFreeBuffer", [HBUF])
    proto(dm, "olDmGetValidSamples", [HBUF, ctypes.POINTER(ctypes.c_ulong)])
    proto(dm, "olDmGetBufferPtr", [HBUF, ctypes.POINTER(ctypes.c_char_p)])


def main():
    dlls = load_openlayers()
    da, dm = dlls.oldaapi, dlls.olmem
    proto_all(da, dm)

    def code_to_volts(c):
        return c * 10.0 / 32768.0

    rows: list[tuple[str, list[int], int]] = []
    for board in (b"DT9805(00)", b"DT9806(00)"):
        print(f"--- Acquiring from {board.decode()} ---")
        samples, n_list, ec = acquire_one_buffer(board, da, dm, 0)
        if ec or not samples:
            print(f"  Acquisition failed (ec={ec}, samples={len(samples)})")
            continue
        rows.append((board.decode(), samples, n_list))
        scans = len(samples) // n_list
        print(f"  {scans} scans captured ({len(samples)} samples)")

    print()
    print(f"{'channel':10s} | " + " | ".join(f"{r[0]:20s}" for r in rows))
    print("-" * (12 + 23 * len(rows)))
    for ch in range(8):
        cells = []
        for _name, samples, n_list in rows:
            scans = len(samples) // n_list
            vals = [samples[s * n_list + ch] for s in range(scans)]
            mean = sum(vals) / len(vals)
            mn = min(vals)
            mx = max(vals)
            v = code_to_volts(int(mean))
            note = ""
            if abs(mean) > 31000:
                note = " RAIL"
            elif (mx - mn) < 2000:
                note = " STABLE!"
            elif abs(mean) < 2000:
                note = " ~0V"
            cells.append(f"{v:+.3f}V (range {mx - mn:5d}){note:8s}")
        print(f"  ch{ch}     | " + " | ".join(cells))

    print()
    print("Interpretation:")
    print("  RAIL    = signal stuck at +/-10V (open input or sensor)")
    print("  STABLE! = small range across scans -> something connected")
    print("  ~0V     = mean near zero -> could be TC at room temp OR floating-zero-bias")


if __name__ == "__main__":
    main()
