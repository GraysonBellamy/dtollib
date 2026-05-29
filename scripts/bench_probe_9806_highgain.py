"""DT9806 channels 3-7 with high gain to identify connected TCs.

TCs produce mV signals at room temp. We scan multiple gains and look
for channels that show stable (low-noise) readings — those are the
ones with TCs physically connected.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from dtollib.capi.constants import OL_DF_CONTINUOUS, OL_WRP_MULTIPLE, OLDA_WM_BUFFER_DONE
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

    # Module-level state for the WNDPROC
    state = {"hdass": HDASS(), "captured": []}

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == OLDA_WM_BUFFER_DONE:
            hb = HBUF()
            ec = da.olDaGetBuffer(state["hdass"], ctypes.byref(hb))
            if ec == 0 and hb.value:
                state["captured"].append(int(hb.value))
                da.olDaPutBuffer(state["hdass"], hb)
            return 0
        return user32.DefWindowProcA(hwnd, msg, wparam, lparam)

    wndproc_cb = WNDPROC(_wndproc)
    h_inst = kernel32.GetModuleHandleA(None)
    wc = WNDCLASS()
    wc.lpfnWndProc = wndproc_cb
    wc.hInstance = h_inst
    wc.lpszClassName = b"DtolBenchHG9806"
    user32.RegisterClassA(ctypes.byref(wc))
    hwnd = user32.CreateWindowExA(
        0, b"DtolBenchHG9806", b"", 0, 0, 0, 0, 0, HWND_MESSAGE, None, h_inst, None
    )

    for gain in (1.0, 10.0, 100.0, 1000.0):
        print(f"--- gain = {gain} ---")
        state["captured"].clear()

        hdrvr = HDRVR()
        ec = da.olDaInitialize(b"DT9806(00)", ctypes.byref(hdrvr))
        if ec:
            print(f"  init failed ec={ec}")
            continue
        ec = da.olDaGetDASS(hdrvr, 0, 0, ctypes.byref(state["hdass"]))
        if ec:
            print(f"  getDASS failed ec={ec}")
            da.olDaTerminate(hdrvr)
            continue
        hdass = state["hdass"]

        n_list = 8
        da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS)
        da.olDaSetWrapMode(hdass, OL_WRP_MULTIPLE)
        da.olDaSetClockFrequency(hdass, 1000.0)
        da.olDaSetDmaUsage(hdass, 0)
        da.olDaSetChannelListSize(hdass, n_list)
        gain_ecs = []
        for i in range(n_list):
            da.olDaSetChannelListEntry(hdass, i, i)
            gec = da.olDaSetGainListEntry(hdass, i, i, gain)
            if gec:
                gain_ecs.append((i, gec))
        if gain_ecs:
            print(f"  gain setter errors: {gain_ecs}")
        ec_conf = da.olDaConfig(hdass)
        if ec_conf:
            print(f"  olDaConfig ec={ec_conf} -- gain rejected, skip")
            da.olDaReleaseDASS(hdass)
            da.olDaTerminate(hdrvr)
            continue

        hbufs: list[HBUF] = []
        for _ in range(4):
            hb = HBUF()
            if dm.olDmCallocBuffer(0, 0, 1000, 2, ctypes.byref(hb)) == 0 and hb.value:
                hbufs.append(hb)
                da.olDaPutBuffer(hdass, hb)

        da.olDaSetWndHandle(hdass, hwnd, 0)
        da.olDaConfig(hdass)
        da.olDaStart(hdass)

        deadline = time.monotonic() + 3.0
        msg = wt.MSG()
        while time.monotonic() < deadline and not state["captured"]:
            while user32.PeekMessageA(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
                user32.DispatchMessageA(ctypes.byref(msg))
            time.sleep(0.05)

        if not state["captured"]:
            print(f"  no buffers captured at gain={gain}")
        else:
            hb = HBUF(state["captured"][0])
            valid = ctypes.c_ulong(0)
            dm.olDmGetValidSamples(hb, ctypes.byref(valid))
            ptr = ctypes.c_char_p()
            dm.olDmGetBufferPtr(hb, ctypes.byref(ptr))
            ptr_int = ctypes.cast(ptr, ctypes.c_void_p).value or 0
            arr = (ctypes.c_int16 * valid.value).from_address(ptr_int)
            samples = list(arr)
            scans = valid.value // n_list
            print(f"  {scans} scans captured")
            for ch in range(n_list):
                vals = [samples[s * n_list + ch] for s in range(scans)]
                mn = min(vals)
                mx = max(vals)
                mean = sum(vals) / len(vals)
                input_v = mean * 10.0 / 32768.0 / gain  # divide by gain
                input_mv = input_v * 1000.0
                note = ""
                if abs(mean) > 31500:
                    note = " RAIL"
                elif (mx - mn) < 300:
                    note = " STABLE"
                elif (mx - mn) < 3000:
                    note = " semi"
                print(
                    f"    ch{ch}: code mean={mean:8.1f} (~{input_mv:+8.3f} mV input), "
                    f"range={mx - mn:5d}{note}"
                )

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
