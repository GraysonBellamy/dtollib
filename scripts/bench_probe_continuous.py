"""Bench probe — continuous mode acquisition on DT9805(00) multi-sensor AI.

Uses the verified OLDADEFS.H constants (offsetted-by-100s family).

Run:
    uv run --no-sync python scripts/bench_probe_continuous.py
"""

from __future__ import annotations

import ctypes
import threading
import time

from dtollib.capi.constants import (
    OL_CLK_INTERNAL,
    OL_DF_CONTINUOUS,
    OL_QUE_DONE,
    OL_QUE_INPROCESS,
    OL_QUE_READY,
    OL_TRG_SOFT,
    OL_WRP_MULTIPLE,
    OL_WRP_NONE,
    OLDA_WM_BUFFER_DONE,
    OLDA_WM_BUFFER_REUSED,
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_QUEUE_DONE,
    OLDA_WM_QUEUE_STOPPED,
    OLDA_WM_TRIGGER_ERROR,
    SENSOR_IS_OPEN,
    TEMP_OUT_OF_RANGE_HIGH,
    TEMP_OUT_OF_RANGE_LOW,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HBUF, HDASS, HDRVR

# OLNOTIFYPROC signature from OLDADEFS.H:
#     typedef void (FAR PASCAL *OLNOTIFYPROC) (UINT uiMsg, WPARAM wParam, LPARAM lParam);
# On Win64 ctypes.wintypes.WPARAM/LPARAM are pointer-sized.
NOTIFY_PROC = ctypes.WINFUNCTYPE(
    None, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)


def proto(lib, fn: str, args, ret=ctypes.c_ulong) -> None:
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def ec_str(ec: int) -> str:
    names = {
        0: "OK",
        8: "BAD_CHANNEL_TYPE",
        10: "BAD_TRIGGER",
        12: "BAD_CLOCK_SOURCE",
        13: "BAD_FREQUENCY",
        18: "BAD_DATA_FLOW",
        20: "SUBSYS_IN_USE",
        23: "NO_CHANNEL_LIST",
        26: "NOT_CONFIGURED",
        27: "DATA_FLOW_MISMATCH",
        35: "BAD_WRAP_MODE",
        36: "NOT_SUPPORTED",
        64: "NO_READY_BUFFERS",
        71: "NO_WINDOW_HANDLE",
        89: "BAD_QUEUE",
        117: "NO_BUFFER_INPROCESS",
    }
    return f"ec={ec} ({names.get(ec, '?')})"


def main() -> None:
    dlls = load_openlayers()
    da, dm = dlls.oldaapi, dlls.olmem

    proto(da, "olDaInitialize", [ctypes.c_char_p, ctypes.POINTER(HDRVR)])
    proto(da, "olDaTerminate", [HDRVR])
    proto(da, "olDaGetDASS", [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)])
    proto(da, "olDaReleaseDASS", [HDASS])
    proto(da, "olDaConfig", [HDASS])
    proto(da, "olDaStart", [HDASS])
    proto(da, "olDaAbort", [HDASS])
    proto(da, "olDaIsRunning", [HDASS, ctypes.POINTER(ctypes.c_int)])
    proto(da, "olDaSetDataFlow", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetWrapMode", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetClockSource", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetClockFrequency", [HDASS, ctypes.c_double])
    proto(da, "olDaSetTrigger", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListSize", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelListEntry", [HDASS, ctypes.c_uint, ctypes.c_uint])
    proto(da, "olDaPutBuffer", [HDASS, HBUF])
    proto(da, "olDaGetBuffer", [HDASS, ctypes.POINTER(HBUF)])
    proto(da, "olDaFlushBuffers", [HDASS])
    proto(da, "olDaGetQueueSize", [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)])
    proto(da, "olDaGetSSCaps", [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)])
    proto(da, "olDaSetNotificationProcedure", [HDASS, NOTIFY_PROC, ctypes.wintypes.LPARAM])

    proto(
        dm,
        "olDmCallocBuffer",
        [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_uint, ctypes.POINTER(HBUF)],
    )
    proto(dm, "olDmFreeBuffer", [HBUF])
    proto(dm, "olDmGetValidSamples", [HBUF, ctypes.POINTER(ctypes.c_ulong)])
    proto(dm, "olDmGetBufferPtr", [HBUF, ctypes.POINTER(ctypes.c_char_p)])
    proto(dm, "olDmGetDataWidth", [HBUF, ctypes.POINTER(ctypes.c_uint)])

    hdrvr = HDRVR()
    hdass = HDASS()
    ec = da.olDaInitialize(b"DT9805(00)", ctypes.byref(hdrvr))
    print(f"olDaInitialize: {ec_str(ec)}")
    if ec:
        return

    ec = da.olDaGetDASS(hdrvr, 0, 0, ctypes.byref(hdass))
    print(f"olDaGetDASS(AD,0): {ec_str(ec)}")
    if ec:
        da.olDaTerminate(hdrvr)
        return

    # --- Cap probe with verified IDs ------------------------------------------
    def cap(cid: int) -> int:
        v = ctypes.c_ulong(0)
        s = da.olDaGetSSCaps(hdass, cid, ctypes.byref(v))
        return -1 if s else int(v.value)

    n_chan = cap(7)  # OLSSC_NUMCHANNELS
    print(f"  NUMCHANNELS = {n_chan}, CGLDEPTH = {cap(2)}, NUMDMACHANS = {cap(6)}")
    print(f"  SUP_MULTISENSOR(143)={cap(143)}, SUP_THERMOCOUPLES(111)={cap(111)}")
    print(f"  RETURNS_FLOATS(116)={cap(116)}")
    returns_floats = bool(cap(116))

    # --- Configure with REAL constants ---------------------------------------
    print("--- Configure continuous mode (real constants) ---")
    ec = da.olDaSetDataFlow(hdass, OL_DF_CONTINUOUS)
    print(f"  SetDataFlow(CONTINUOUS={OL_DF_CONTINUOUS}): {ec_str(ec)}")

    # Try MULTIPLE first; fall back to NONE if rejected
    ec = da.olDaSetWrapMode(hdass, OL_WRP_MULTIPLE)
    print(f"  SetWrapMode(MULTIPLE={OL_WRP_MULTIPLE}):     {ec_str(ec)}")
    if ec == 35:  # OLBADWRAPMODE
        ec = da.olDaSetWrapMode(hdass, OL_WRP_NONE)
        print(f"  SetWrapMode(NONE={OL_WRP_NONE}):           {ec_str(ec)}")

    ec = da.olDaSetClockSource(hdass, OL_CLK_INTERNAL)
    print(f"  SetClockSource(INTERNAL={OL_CLK_INTERNAL}): {ec_str(ec)}")

    ec = da.olDaSetClockFrequency(hdass, 100.0)
    print(f"  SetClockFrequency(100.0):                  {ec_str(ec)}")

    ec = da.olDaSetTrigger(hdass, OL_TRG_SOFT)
    print(f"  SetTrigger(SOFT={OL_TRG_SOFT}):             {ec_str(ec)}")

    n_list = 8  # use 8 multi-sensor channels (0..7)
    ec = da.olDaSetChannelListSize(hdass, n_list)
    print(f"  SetChannelListSize({n_list}):              {ec_str(ec)}")
    for i in range(n_list):
        ec = da.olDaSetChannelListEntry(hdass, i, i)
        if ec:
            print(f"  SetChannelListEntry({i},{i}): {ec_str(ec)}")
            break

    # --- Install notification callback ---------------------------------------
    msg_queue: list[tuple[int, int, int]] = []
    seen_event = threading.Event()

    def _on_event(ui_msg: int, wparam: int, lparam: int) -> None:
        msg_queue.append((ui_msg, int(wparam), int(lparam)))
        seen_event.set()

    notify_cb = NOTIFY_PROC(_on_event)
    ec = da.olDaSetNotificationProcedure(hdass, notify_cb, 0)
    print(f"  SetNotificationProcedure: {ec_str(ec)}")

    ec = da.olDaConfig(hdass)
    print(f"  olDaConfig: {ec_str(ec)}")
    if ec:
        da.olDaSetNotificationProcedure(hdass, NOTIFY_PROC(0), 0)
        da.olDaReleaseDASS(hdass)
        da.olDaTerminate(hdrvr)
        return

    # --- Allocate & queue buffers --------------------------------------------
    # DT9805 multi-sensor returns int16 codes (RETURNS_FLOATS=0). 800 samples/buf.
    sample_bytes = 4 if returns_floats else 2
    samples_per_buf = 800
    n_bufs = 4
    hbufs: list[HBUF] = []
    for i in range(n_bufs):
        hb = HBUF()
        ec = dm.olDmCallocBuffer(0, 0, samples_per_buf, sample_bytes, ctypes.byref(hb))
        if ec or not hb.value:
            print(f"  CallocBuffer #{i}: {ec_str(ec)}")
            break
        hbufs.append(hb)
        ec = da.olDaPutBuffer(hdass, hb)
        if ec:
            print(f"  PutBuffer #{i}: {ec_str(ec)}")
            break
    print(
        f"  allocated + queued {len(hbufs)} buffers of {samples_per_buf} samples x {sample_bytes} bytes"
    )

    # --- Pre-start queue sizes with CORRECT IDs ------------------------------
    for q, name in (
        (OL_QUE_READY, "READY"),
        (OL_QUE_DONE, "DONE"),
        (OL_QUE_INPROCESS, "INPROCESS"),
    ):
        sz = ctypes.c_ulong(0)
        ec = da.olDaGetQueueSize(hdass, q, ctypes.byref(sz))
        print(f"  Queue {q} ({name:9}): {ec_str(ec)}, size={sz.value}")

    # --- Start ---------------------------------------------------------------
    ec = da.olDaStart(hdass)
    print(f"olDaStart: {ec_str(ec)}")
    if ec:
        for hb in hbufs:
            dm.olDmFreeBuffer(hb)
        da.olDaSetNotificationProcedure(hdass, NOTIFY_PROC(0), 0)
        da.olDaReleaseDASS(hdass)
        da.olDaTerminate(hdrvr)
        return

    # --- Wait for buffer-done events (up to 6 s) ----------------------------
    deadline = time.monotonic() + 6.0
    last_print = 0.0
    while time.monotonic() < deadline:
        seen_event.wait(timeout=0.2)
        seen_event.clear()
        now = time.monotonic()
        if now - last_print > 0.5:
            sz_r = ctypes.c_ulong(0)
            sz_d = ctypes.c_ulong(0)
            sz_i = ctypes.c_ulong(0)
            da.olDaGetQueueSize(hdass, OL_QUE_READY, ctypes.byref(sz_r))
            da.olDaGetQueueSize(hdass, OL_QUE_DONE, ctypes.byref(sz_d))
            da.olDaGetQueueSize(hdass, OL_QUE_INPROCESS, ctypes.byref(sz_i))
            print(
                f"  t={6.0 - (deadline - now):.1f}s: msgs={len(msg_queue)} "
                f"R={sz_r.value} D={sz_d.value} IP={sz_i.value}"
            )
            last_print = now
        if any(m[0] == OLDA_WM_BUFFER_DONE for m in msg_queue):
            break

    # --- Report messages ----------------------------------------------------
    name_map = {
        OLDA_WM_BUFFER_DONE: "BUFFER_DONE",
        OLDA_WM_BUFFER_REUSED: "BUFFER_REUSED",
        OLDA_WM_QUEUE_DONE: "QUEUE_DONE",
        OLDA_WM_QUEUE_STOPPED: "QUEUE_STOPPED",
        OLDA_WM_TRIGGER_ERROR: "TRIGGER_ERROR",
        OLDA_WM_OVERRUN_ERROR: "OVERRUN_ERROR",
    }
    print(f"--- Notification messages received: {len(msg_queue)} ---")
    for ui_msg, wparam, lparam in msg_queue[:20]:
        print(f"  msg={ui_msg:#x} ({name_map.get(ui_msg, '?')}) wp={wparam:#x} lp={lparam}")

    # --- Try to fetch a done buffer -----------------------------------------
    done = HBUF()
    ec = da.olDaGetBuffer(hdass, ctypes.byref(done))
    print(f"olDaGetBuffer: {ec_str(ec)}, handle={int(done.value or 0):#x}")

    if done.value:
        valid = ctypes.c_ulong(0)
        dm.olDmGetValidSamples(done, ctypes.byref(valid))
        width = ctypes.c_uint(0)
        dm.olDmGetDataWidth(done, ctypes.byref(width))
        print(f"  ValidSamples={valid.value}, DataWidth={width.value}")
        ptr = ctypes.c_char_p()
        dm.olDmGetBufferPtr(done, ctypes.byref(ptr))
        ptr_int = ctypes.cast(ptr, ctypes.c_void_p).value or 0
        n_show = min(64, valid.value)
        if returns_floats or width.value >= 4:
            arr_t = ctypes.c_float
        elif width.value == 2:
            arr_t = ctypes.c_int16
        else:
            arr_t = ctypes.c_int32
        arr = (arr_t * n_show).from_address(ptr_int)
        samples = list(arr)
        scans = n_show // 8
        print(
            f"  Decoded as {arr_t.__name__} (returns_floats={returns_floats}, width={width.value})"
        )
        for s in range(min(4, scans)):
            scan = samples[s * 8 : s * 8 + 8]
            print(f"    Scan {s}: {scan}")
        # Channel summary — mean of all scans per channel
        means = []
        for ch in range(8):
            vals = [samples[s * 8 + ch] for s in range(scans)]
            means.append(sum(vals) / max(1, len(vals)))
        print(f"  Channel means across {scans} scans:")
        for ch, m in enumerate(means):
            note = ""
            if returns_floats:
                if abs(m - SENSOR_IS_OPEN) < 1.0:
                    note = " (SENSOR_OPEN)"
                elif abs(m - TEMP_OUT_OF_RANGE_HIGH) < 1.0:
                    note = " (OUT_OF_RANGE_HIGH)"
                elif abs(m - TEMP_OUT_OF_RANGE_LOW) < 1.0:
                    note = " (OUT_OF_RANGE_LOW)"
                elif 15.0 < m < 35.0:
                    note = " <-- looks like room-temp TC"
            print(f"    ch{ch}: mean = {m:.3f}{note}")

    # --- Tear down ----------------------------------------------------------
    da.olDaAbort(hdass)
    da.olDaFlushBuffers(hdass)
    da.olDaSetNotificationProcedure(hdass, NOTIFY_PROC(0), 0)
    da.olDaReleaseDASS(hdass)
    for hb in hbufs:
        dm.olDmFreeBuffer(hb)
    da.olDaTerminate(hdrvr)
    print("Done.")


if __name__ == "__main__":
    import ctypes.wintypes  # ensure WPARAM/LPARAM resolved on Windows

    main()
