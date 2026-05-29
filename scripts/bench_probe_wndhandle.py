"""Bench probe — continuous mode with olDaSetWndHandle + Win32 message pump.

Mirrors the DtConsole.cpp example: creates a hidden message-only window
and pumps messages to receive OLDA_WM_BUFFER_DONE notifications.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from dtollib.capi.constants import (
    OL_DF_CONTINUOUS,
    OL_QUE_DONE,
    OL_QUE_INPROCESS,
    OL_QUE_READY,
    OL_WRP_MULTIPLE,
    OLDA_WM_BUFFER_DONE,
    OLDA_WM_BUFFER_REUSED,
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_QUEUE_DONE,
    OLDA_WM_QUEUE_STOPPED,
    OLDA_WM_TRIGGER_ERROR,
    OLDA_WM_UNDERRUN_ERROR,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HBUF, HDASS, HDRVR

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 window proc signature
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
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.TranslateMessage.restype = ctypes.c_int

PM_REMOVE = 0x0001


def proto(lib, fn, args, ret=ctypes.c_ulong):
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def ec_str(ec):
    return f"ec={ec}"


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
    proto(da, "olDaSetClockSource", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetClockFrequency", [HDASS, ctypes.c_double])
    proto(da, "olDaSetTrigger", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListSize", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint])
    proto(da, "olDaSetDmaUsage", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetGainListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint, ctypes.c_double])
    proto(da, "olDaIsRunning", [HDASS, ctypes.POINTER(ctypes.c_int)])
    proto(da, "olDaPutBuffer", [HDASS, HBUF])
    proto(da, "olDaGetBuffer", [HDASS, ctypes.POINTER(HBUF)])
    proto(da, "olDaFlushBuffers", [HDASS])
    proto(da, "olDaGetQueueSize", [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)])
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
    proto(dm, "olDmGetDataWidth", [HBUF, ctypes.POINTER(ctypes.c_uint)])

    # --- Set up hidden message-only window ----------------------------------
    h_inst = kernel32.GetModuleHandleA(None)
    msg_count = {
        "done": 0,
        "reused": 0,
        "qdone": 0,
        "qstop": 0,
        "trig_err": 0,
        "ovr": 0,
        "unr": 0,
        "other": 0,
    }
    captured_buffers: list[int] = []

    # We hold hdass globally so the WndProc can drain Done buffers and requeue.
    hdass_ref = {"h": HDASS()}

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == OLDA_WM_BUFFER_DONE:
            msg_count["done"] += 1
            hbuf_out = HBUF()
            ec = da.olDaGetBuffer(hdass_ref["h"], ctypes.byref(hbuf_out))
            if ec == 0 and hbuf_out.value:
                captured_buffers.append(int(hbuf_out.value))
                # Re-queue for continuous flow
                da.olDaPutBuffer(hdass_ref["h"], hbuf_out)
        elif msg == OLDA_WM_BUFFER_REUSED:
            msg_count["reused"] += 1
        elif msg == OLDA_WM_QUEUE_DONE:
            msg_count["qdone"] += 1
        elif msg == OLDA_WM_QUEUE_STOPPED:
            msg_count["qstop"] += 1
        elif msg == OLDA_WM_TRIGGER_ERROR:
            msg_count["trig_err"] += 1
        elif msg == OLDA_WM_OVERRUN_ERROR:
            msg_count["ovr"] += 1
        elif msg == OLDA_WM_UNDERRUN_ERROR:
            msg_count["unr"] += 1
        else:
            return user32.DefWindowProcA(hwnd, msg, wparam, lparam)
        return 0

    wndproc_cb = WNDPROC(_wndproc)
    wndclass = WNDCLASS()
    wndclass.lpfnWndProc = wndproc_cb
    wndclass.hInstance = h_inst
    wndclass.lpszClassName = b"DtolBenchClass"
    atom = user32.RegisterClassA(ctypes.byref(wndclass))
    if not atom:
        print("RegisterClassA failed:", ctypes.get_last_error())
        return

    hwnd = user32.CreateWindowExA(
        0,
        b"DtolBenchClass",
        b"DtolBench",
        0,
        0,
        0,
        0,
        0,
        HWND_MESSAGE,
        None,
        h_inst,
        None,
    )
    if not hwnd:
        print("CreateWindowExA failed:", ctypes.get_last_error())
        return
    print(f"Hidden window created: hwnd={hwnd:#x}")

    # --- Init board ---------------------------------------------------------
    hdrvr = HDRVR()
    ec = da.olDaInitialize(b"DT9805(00)", ctypes.byref(hdrvr))
    print(f"olDaInitialize: {ec_str(ec)}")
    if ec:
        user32.DestroyWindow(hwnd)
        return

    ec = da.olDaGetDASS(hdrvr, 0, 0, ctypes.byref(hdass_ref["h"]))
    print(f"olDaGetDASS: {ec_str(ec)}")
    if ec:
        da.olDaTerminate(hdrvr)
        user32.DestroyWindow(hwnd)
        return

    hdass = hdass_ref["h"]

    # --- Configure (mirror ThermoADC.C closely) -----------------------------
    # Order: SetDataFlow, SetWrapMode, SetClockFrequency, SetDmaUsage,
    #        SetChannelListSize, SetChannelListEntry x N, olDaConfig,
    #        allocate + queue buffers, SetWndHandle, olDaConfig AGAIN, Start.
    print(f"  SetDataFlow(CONTINUOUS=800): {ec_str(da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS))}")
    print(f"  SetWrapMode(MULTIPLE=1001):  {ec_str(da.olDaSetWrapMode(hdass, OL_WRP_MULTIPLE))}")
    print(f"  SetClockFrequency(1000):     {ec_str(da.olDaSetClockFrequency(hdass, 1000.0))}")
    n_dma_caps = ctypes.c_ulong(0)
    da.olDaGetSSCaps(hdass, 6, ctypes.byref(n_dma_caps))
    dma_to_use = min(1, n_dma_caps.value)
    print(
        f"  SetDmaUsage({dma_to_use}):            {ec_str(da.olDaSetDmaUsage(hdass, dma_to_use))}"
    )
    n_list = 8  # scan all 8 multi-sensor channels to find which two have TCs
    print(f"  SetChannelListSize({n_list}):     {ec_str(da.olDaSetChannelListSize(hdass, n_list))}")
    for i in range(n_list):
        ec = da.olDaSetChannelListEntry(hdass, i, i)
        if ec:
            print(f"  SetChannelListEntry({i},{i}): {ec_str(ec)}")
    print(f"  olDaConfig (pre-WndHandle):  {ec_str(da.olDaConfig(hdass))}")

    # --- Buffers -------------------------------------------------------------
    samples_per_buf = 1000  # 1 second at 1000 Hz
    sample_bytes = 2  # DT9805 16-bit ADC
    n_bufs = 4
    hbufs: list[HBUF] = []
    for i in range(n_bufs):
        hb = HBUF()
        ec = dm.olDmCallocBuffer(0, 0, samples_per_buf, sample_bytes, ctypes.byref(hb))
        if ec:
            print(f"  CallocBuffer #{i}: ec={ec}")
            break
        hbufs.append(hb)
        da.olDaPutBuffer(hdass, hb)
    print(f"  queued {len(hbufs)} buffers")

    # --- Set window handle then Config again (ThermoADC.C pattern) ----------
    ec = da.olDaSetWndHandle(hdass, hwnd, 0)
    print(f"  SetWndHandle({hwnd:#x}): {ec_str(ec)}")
    ec = da.olDaConfig(hdass)
    print(f"  olDaConfig (post-WndHandle): {ec_str(ec)}")

    # --- Start ---------------------------------------------------------------
    ec = da.olDaStart(hdass)
    print(f"olDaStart: {ec_str(ec)}")
    running = ctypes.c_int(0)
    da.olDaIsRunning(hdass, ctypes.byref(running))
    print(f"  IsRunning: {bool(running.value)}")

    # --- Pump messages while polling -----------------------------------------
    deadline = time.monotonic() + 5.0
    last_print = 0.0
    msg = wt.MSG()
    while time.monotonic() < deadline:
        # Pump pending messages
        while user32.PeekMessageA(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))
        now = time.monotonic()
        if now - last_print > 0.5:
            sz_r = ctypes.c_ulong(0)
            sz_d = ctypes.c_ulong(0)
            sz_i = ctypes.c_ulong(0)
            da.olDaGetQueueSize(hdass, OL_QUE_READY, ctypes.byref(sz_r))
            da.olDaGetQueueSize(hdass, OL_QUE_DONE, ctypes.byref(sz_d))
            da.olDaGetQueueSize(hdass, OL_QUE_INPROCESS, ctypes.byref(sz_i))
            print(
                f"  t={5.0 - (deadline - now):.1f}s: done={msg_count['done']} "
                f"R={sz_r.value} D={sz_d.value} IP={sz_i.value} "
                f"captured={len(captured_buffers)}"
            )
            last_print = now
        time.sleep(0.05)
        if msg_count["done"] >= 2:
            break

    print(f"--- Final counts: {msg_count} ---")

    # --- Inspect captured buffers (decode as N-channel scans) ---------------
    if captured_buffers:
        hb_val = captured_buffers[0]
        hb = HBUF(hb_val)
        valid = ctypes.c_ulong(0)
        dm.olDmGetValidSamples(hb, ctypes.byref(valid))
        width = ctypes.c_uint(0)
        dm.olDmGetDataWidth(hb, ctypes.byref(width))
        print(f"First captured buffer: valid={valid.value}, width={width.value}")

        ptr = ctypes.c_char_p()
        dm.olDmGetBufferPtr(hb, ctypes.byref(ptr))
        ptr_int = ctypes.cast(ptr, ctypes.c_void_p).value or 0
        # Decode every sample
        if width.value == 2:
            arr = (ctypes.c_int16 * valid.value).from_address(ptr_int)
        else:
            arr = (ctypes.c_int32 * valid.value).from_address(ptr_int)
        samples = list(arr)
        scans = valid.value // n_list
        print(f"  Acquired {scans} full {n_list}-channel scans ({valid.value} samples)")
        print("  First 3 scans:")
        for s in range(min(3, scans)):
            print(f"    Scan {s}: {samples[s * n_list : (s + 1) * n_list]}")

        # 16-bit signed ADC, ±10V range, encoding = 2's complement most likely
        # (Encoding readback was 200 = OL_ENC_BINARY though — so codes are unsigned-shifted?)
        # Convert code to volts assuming 16-bit signed ±10V:
        def code_to_volts(c: int) -> float:
            return c * 10.0 / 32768.0

        print("  Per-channel statistics across all scans:")
        for ch in range(n_list):
            vals = [samples[s * n_list + ch] for s in range(scans)]
            mn = min(vals)
            mx = max(vals)
            mean = sum(vals) / len(vals)
            v_mean = code_to_volts(int(mean))
            v_min = code_to_volts(mn)
            v_max = code_to_volts(mx)
            note = ""
            if abs(mean) > 31000:
                note = "  (railed -- likely open)"
            elif abs(mean) < 2000:
                note = "  <-- near 0 V (likely TC at room temp on high-gain input)"
            print(
                f"    ch{ch}: code mean={mean:8.1f} ({v_mean:+.3f} V), "
                f"min={mn:6d} ({v_min:+.3f}V), max={mx:6d} ({v_max:+.3f}V){note}"
            )

    # --- Tear down ----------------------------------------------------------
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
