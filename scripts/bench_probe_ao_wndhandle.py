"""Bench probe — continuous ANALOG OUTPUT via olDaSetWndHandle.

Maintainer-only, run on a real DT9806. This is the WS-AO bench spike from
docs/plan-hardware-functional.md §S0: before trusting the output bridge's
event routing, confirm on hardware **how the DA subsystem behaves** under the
proven window-handle mechanism.

Open questions it answers (record the answers in docs/decisions.md):

1. Does the DA subsystem post per-buffer ``OLDA_WM_BUFFER_DONE`` in
   ``WrapMode.SINGLE`` (so the SINGLE drainer in _output_callback_bridge.py can
   refill), or only ``OLDA_WM_QUEUE_DONE`` / ``OLDA_WM_IO_COMPLETE``?
2. In ``WrapMode.MULTIPLE``, does it post per-buffer ``BUFFER_DONE`` at the
   refill cadence the input subsystem does?
3. Does a starved queue post ``OLDA_WM_UNDERRUN_ERROR`` (vs the AD subsystem's
   ``OVERRUN_ERROR``)?
4. Do the two-``olDaConfig`` + ``olDaSetDmaUsage(min(1,N))`` prerequisites apply
   to DA exactly as to AD?

It mirrors scripts/bench_probe_wndhandle.py (the proven AD reference) — same
hidden HWND_MESSAGE window + PeekMessage pump — but seeds the buffers with a
sine via olDmCopyToBuffer (output) instead of draining them (input).

This script does NOT import the dtollib backend/bridge on purpose: it binds the
raw SDK so the probe is independent of the code it is meant to validate.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import time

from dtollib.capi.constants import (
    OL_DF_CONTINUOUS,
    OL_QUE_DONE,
    OL_QUE_INPROCESS,
    OL_QUE_READY,
    OL_WRP_MULTIPLE,
    OL_WRP_SINGLE,
    OLDA_WM_BUFFER_DONE,
    OLDA_WM_BUFFER_REUSED,
    OLDA_WM_IO_COMPLETE,
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_QUEUE_DONE,
    OLDA_WM_QUEUE_STOPPED,
    OLDA_WM_TRIGGER_ERROR,
    OLDA_WM_UNDERRUN_ERROR,
    OLSS_DA,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HBUF, HDASS, HDRVR

# --- Bench configuration (edit for your board) -----------------------------
DEVICE_NAME = b"DT9806(00)"  # targets the DT9806 DAC
SAMPLES_PER_BUF = 1000  # 1 s per buffer at 1 kHz
SAMPLE_BYTES = 2  # DT9806 16-bit DAC
SINE_AMPLITUDE_V = 2.0  # peak amplitude of the test sine
SINE_HZ = 10.0  # tone frequency within one buffer period
AO_RANGE_V = 10.0  # ±10 V device range
AO_CODE_MIDSCALE = 0x8000  # 16-bit offset-binary: 0 V == 0x8000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(wt.LPARAM, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)
HWND_MESSAGE = wt.HWND(-3)
PM_REMOVE = 0x0001


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


def proto(lib, fn, args, ret=ctypes.c_ulong):
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def ec_str(ec):
    return f"ec={ec}"


# Map OLDA_WM_* -> short label, so the probe logs every message by name.
_MSG_LABELS = {
    OLDA_WM_BUFFER_DONE: "BUFFER_DONE",
    OLDA_WM_BUFFER_REUSED: "BUFFER_REUSED",
    OLDA_WM_QUEUE_DONE: "QUEUE_DONE",
    OLDA_WM_QUEUE_STOPPED: "QUEUE_STOPPED",
    OLDA_WM_IO_COMPLETE: "IO_COMPLETE",
    OLDA_WM_TRIGGER_ERROR: "TRIGGER_ERROR",
    OLDA_WM_OVERRUN_ERROR: "OVERRUN_ERROR",
    OLDA_WM_UNDERRUN_ERROR: "UNDERRUN_ERROR",
}


def _volts_to_code(v: float) -> int:
    """16-bit offset-binary code for ``v`` volts on a ±AO_RANGE_V DAC."""
    code = AO_CODE_MIDSCALE + round(v / AO_RANGE_V * AO_CODE_MIDSCALE)
    return max(0, min(0xFFFF, code))


def _make_sine_buffer(n_samples: int) -> ctypes.Array:
    """One period of a sine as offset-binary uint16 codes (ctypes array)."""
    arr = (ctypes.c_uint16 * n_samples)()
    for i in range(n_samples):
        phase = 2.0 * math.pi * SINE_HZ * i / n_samples
        arr[i] = _volts_to_code(SINE_AMPLITUDE_V * math.sin(phase))
    return arr


def run_probe(da, dm, hdrvr, *, wrap_mode: int, n_bufs: int, refill: bool, duration_s: float):
    """One DA continuous run; logs every OLDA_WM_* with a timestamp.

    Args:
        wrap_mode: OL_WRP_SINGLE or OL_WRP_MULTIPLE.
        n_bufs: buffers to allocate + seed (1 with refill=False provokes underrun).
        refill: on BUFFER_DONE, get + re-fill + re-queue the buffer (MULTIPLE).
        duration_s: how long to pump messages.
    """
    wrap_label = "SINGLE" if wrap_mode == OL_WRP_SINGLE else "MULTIPLE"
    print(f"\n=== DA probe: wrap={wrap_label} n_bufs={n_bufs} refill={refill} ===")

    hdass_ref = {"h": HDASS()}
    ec = da.olDaGetDASS(hdrvr, OLSS_DA, 0, ctypes.byref(hdass_ref["h"]))
    print(f"  olDaGetDASS(OLSS_DA, 0): {ec_str(ec)}")
    if ec:
        return
    hdass = hdass_ref["h"]

    t0 = time.monotonic()
    log: list[tuple[float, str, int, int]] = []  # (t, label, wparam, lparam)
    counts: dict[str, int] = dict.fromkeys(_MSG_LABELS.values(), 0)
    counts["other"] = 0
    sine = _make_sine_buffer(SAMPLES_PER_BUF)

    def _wndproc(hwnd, msg, wparam, lparam):
        label = _MSG_LABELS.get(msg)
        if label is None:
            return user32.DefWindowProcA(hwnd, msg, wparam, lparam)
        log.append((time.monotonic() - t0, label, int(wparam), int(lparam)))
        counts[label] += 1
        if msg == OLDA_WM_BUFFER_DONE and refill:
            hbuf_out = HBUF()
            gec = da.olDaGetBuffer(hdass, ctypes.byref(hbuf_out))
            if gec == 0 and hbuf_out.value:
                # Re-fill the emptied buffer with the same sine, then re-queue.
                dm.olDmCopyToBuffer(hbuf_out, ctypes.cast(sine, ctypes.c_void_p), SAMPLES_PER_BUF)
                da.olDaPutBuffer(hdass, hbuf_out)
        return 0

    wndproc_cb = WNDPROC(_wndproc)
    h_inst = kernel32.GetModuleHandleA(None)
    class_name = f"DtolAoBench{wrap_label}{n_bufs}".encode()
    wndclass = WNDCLASS()
    wndclass.lpfnWndProc = wndproc_cb
    wndclass.hInstance = h_inst
    wndclass.lpszClassName = class_name
    atom = user32.RegisterClassA(ctypes.byref(wndclass))
    if not atom:
        print("  RegisterClassA failed:", ctypes.get_last_error())
        da.olDaReleaseDASS(hdass)
        return
    hwnd = user32.CreateWindowExA(
        0, class_name, b"DtolAoBench", 0, 0, 0, 0, 0, HWND_MESSAGE, None, h_inst, None
    )
    if not hwnd:
        print("  CreateWindowExA failed:", ctypes.get_last_error())
        da.olDaReleaseDASS(hdass)
        return

    # --- Configure DA continuous (canonical AD sequence, mirrored for DA) ---
    print(f"  SetDataFlow(CONTINUOUS): {ec_str(da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS))}")
    print(f"  SetWrapMode({wrap_label}): {ec_str(da.olDaSetWrapMode(hdass, wrap_mode))}")
    print(f"  SetClockFrequency(1000): {ec_str(da.olDaSetClockFrequency(hdass, 1000.0))}")
    n_dma_caps = ctypes.c_ulong(0)
    da.olDaGetSSCaps(hdass, 6, ctypes.byref(n_dma_caps))  # 6 == OLSSC_NUMDMACHANS
    dma_to_use = min(1, n_dma_caps.value)
    print(
        f"  SetDmaUsage({dma_to_use}) [NUMDMACHANS={n_dma_caps.value}]:"
        f" {ec_str(da.olDaSetDmaUsage(hdass, dma_to_use))}"
    )
    print(f"  SetChannelListSize(1): {ec_str(da.olDaSetChannelListSize(hdass, 1))}")
    print(f"  SetChannelListEntry(0,0): {ec_str(da.olDaSetChannelListEntry(hdass, 0, 0))}")
    print(f"  SetGainListEntry(0,0,1.0): {ec_str(da.olDaSetGainListEntry(hdass, 0, 0, 1.0))}")
    print(f"  olDaConfig (#1, pre-WndHandle): {ec_str(da.olDaConfig(hdass))}")

    # --- Seed + queue buffers (Fill-before-Queue) ---------------------------
    hbufs: list[HBUF] = []
    for i in range(n_bufs):
        hb = HBUF()
        ec = dm.olDmCallocBuffer(0, 0, SAMPLES_PER_BUF, SAMPLE_BYTES, ctypes.byref(hb))
        if ec:
            print(f"  CallocBuffer #{i}: {ec_str(ec)}")
            break
        cec = dm.olDmCopyToBuffer(hb, ctypes.cast(sine, ctypes.c_void_p), SAMPLES_PER_BUF)
        if cec:
            print(f"  CopyToBuffer #{i}: {ec_str(cec)}")
        hbufs.append(hb)
        da.olDaPutBuffer(hdass, hb)
    print(f"  seeded + queued {len(hbufs)} buffers")

    # --- SetWndHandle then Config #2 (wires HWND into buffer rotation) ------
    print(f"  SetWndHandle({hwnd:#x}): {ec_str(da.olDaSetWndHandle(hdass, hwnd, 0))}")
    print(f"  olDaConfig (#2, post-WndHandle): {ec_str(da.olDaConfig(hdass))}")
    print(f"  olDaStart: {ec_str(da.olDaStart(hdass))}")

    # --- Pump messages ------------------------------------------------------
    deadline = time.monotonic() + duration_s
    last_print = 0.0
    msg = wt.MSG()
    while time.monotonic() < deadline:
        while user32.PeekMessageA(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))
        now = time.monotonic()
        if now - last_print > 1.0:
            sz_r = ctypes.c_ulong(0)
            sz_d = ctypes.c_ulong(0)
            sz_i = ctypes.c_ulong(0)
            da.olDaGetQueueSize(hdass, OL_QUE_READY, ctypes.byref(sz_r))
            da.olDaGetQueueSize(hdass, OL_QUE_DONE, ctypes.byref(sz_d))
            da.olDaGetQueueSize(hdass, OL_QUE_INPROCESS, ctypes.byref(sz_i))
            print(
                f"  t={now - t0:4.1f}s R={sz_r.value} D={sz_d.value} IP={sz_i.value} "
                f"counts={ {k: v for k, v in counts.items() if v} }"
            )
            last_print = now
        time.sleep(0.02)

    # --- Report -------------------------------------------------------------
    print(f"  --- message log (first 20 of {len(log)}) ---")
    for t, label, wp, lp in log[:20]:
        print(f"    t={t:6.3f}s  {label:<14} wparam={wp:#x} lparam={lp:#x}")
    print(f"  --- final counts: { {k: v for k, v in counts.items() if v} } ---")
    print("  takeaways to record in decisions.md:")
    print(f"    - BUFFER_DONE in {wrap_label}? {'YES' if counts['BUFFER_DONE'] else 'NO'}")
    print(
        f"    - QUEUE_DONE/IO_COMPLETE seen? "
        f"{'YES' if counts['QUEUE_DONE'] or counts['IO_COMPLETE'] else 'NO'}"
    )
    print(f"    - UNDERRUN_ERROR seen? {'YES' if counts['UNDERRUN_ERROR'] else 'NO'}")

    # --- Tear down ----------------------------------------------------------
    da.olDaAbort(hdass)
    da.olDaFlushBuffers(hdass)
    da.olDaReleaseDASS(hdass)
    for hb in hbufs:
        dm.olDmFreeBuffer(hb)
    user32.DestroyWindow(hwnd)


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
    proto(da, "olDaSetChannelListSize", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint])
    proto(da, "olDaSetGainListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint, ctypes.c_double])
    proto(da, "olDaSetDmaUsage", [HDASS, ctypes.c_uint])
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
    proto(dm, "olDmCopyToBuffer", [HBUF, ctypes.c_void_p, ctypes.c_ulong])

    hdrvr = HDRVR()
    ec = da.olDaInitialize(DEVICE_NAME, ctypes.byref(hdrvr))
    print(f"olDaInitialize({DEVICE_NAME!r}): {ec_str(ec)}")
    if ec:
        return
    try:
        # Run 1 — SINGLE wrap: does DA post per-buffer BUFFER_DONE, or only
        # QUEUE_DONE/IO_COMPLETE? (the open question that drives the SINGLE drainer)
        run_probe(da, dm, hdrvr, wrap_mode=OL_WRP_SINGLE, n_bufs=4, refill=False, duration_s=5.0)
        # Run 2 — MULTIPLE wrap with refill: confirm refill cadence matches AD.
        run_probe(da, dm, hdrvr, wrap_mode=OL_WRP_MULTIPLE, n_bufs=4, refill=True, duration_s=5.0)
        # Run 3 — starve the queue (1 buffer, no refill) to provoke UNDERRUN.
        run_probe(da, dm, hdrvr, wrap_mode=OL_WRP_MULTIPLE, n_bufs=1, refill=False, duration_s=5.0)
    finally:
        da.olDaTerminate(hdrvr)
        print("\nDone. Record the per-run takeaways in docs/decisions.md.")


if __name__ == "__main__":
    main()
