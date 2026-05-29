"""Bench probe — read DT9806 thermocouples the way the hardware actually works.

Background (see UM9800.md and docs/handoff-bench-2026-05-28b.md):

The DT9805/DT9806 do **not** linearise thermocouples in firmware.  Table 26
of UM9800.md lists "Voltage Converted to Temperature" (SupportsTemperature-
DataInStream) as *unsupported*, and there is no SupportsCjcSourceInternal.
That is why ``olDaSetThermocoupleType`` / ``olDaGetCjcTemperature`` /
``olDaGetSingleValueEx`` return OLNOTSUPPORTED (ec=36) on these boards —
those calls target the *intelligent* temperature modules, not these.  The
board only gives you:

  * a cold-junction sensor on **channel 0** at **10 mV/degC**, and
  * high-impedance **differential** front-ends on channels 1-7.

So the real read path is application-side:

  1. Put the A/D subsystem in **DIFFERENTIAL** mode (mandatory for TCs —
     UM9800.md p.36; single-ended is why earlier probes saw rail-to-rail
     codes on the TC channels).
  2. Read the CJC voltage on channel 0 **at gain 1** -> cjc_degC = V / 0.010.
     (The CJC sits near +0.25 V; at gain 100 it saturates the +/-10 V ADC.)
  3. Read each TC channel's differential emf at **high gain** (100) for
     micro-volt resolution.
  4. Linearise in software with the NIST ITS-90 polynomials already shipped
     in ``dtollib.utils.convert_volts_to_temperature`` (CJC-compensated).

Encoding gotcha: this subsystem reports **offset binary** (OL_ENC_BINARY),
so code 0 = -FS, code 32768 = 0 V, code 65535 = +FS.  An *open* differential
input is pulled to the +2.5 V reference through 10 MOhm, so at gain 1 it
reads ~+2.5 V and at gain >=10 it pegs at +full scale (UM9800.md spec note d:
"Broken thermocouples in differential mode will output plus full scale").  A
connected thermocouple at room temperature reads ~0 V (hot junction ~= cold
junction), i.e. code ~= 32768.  That is how we tell them apart.

Run:
    uv run --no-sync python scripts/bench_probe_tc_diff.py             # one-shot, DT9806(00)
    uv run --no-sync python scripts/bench_probe_tc_diff.py DT9805(00)  # other board
    uv run --no-sync python scripts/bench_probe_tc_diff.py DT9806(00) J  # type-J TCs
    uv run --no-sync python scripts/bench_probe_tc_diff.py watch       # live loop (heat test)
"""

from __future__ import annotations

import ctypes
import sys
import time

from dtollib.capi.constants import (
    OL_DF_SINGLEVALUE,
    OL_ENC_BINARY,
    OLSS_AD,
    OLSSC_NUMCHANNELS,
    OLSSC_SUP_SINGLEVALUE_AUTORANGE,
    OLSSC_SUP_THERMOCOUPLES,
)
from dtollib.capi.loader import load_openlayers
from dtollib.capi.types import HDASS, HDRVR
from dtollib.errors import DtolError
from dtollib.utils import convert_volts_to_temperature

# olDaSetChannelType selects the wiring mode for the WHOLE subsystem (the call
# takes no channel argument).  Values from OLDADEFS.H.
OL_CHNT_SINGLEENDED = 100
OL_CHNT_DIFFERENTIAL = 101

# UM9800.md p.52: cold-junction sensor outputs 10 mV per degree C on ch0.
CJC_VOLTS_PER_DEG_C = 0.010

# Gains the DT9805/06 support are 1, 10, 100, 500.  Gain 1 keeps the CJC
# (~0.25 V) and an open input (~2.5 V) on-scale so we can classify channels;
# gain 100 gives ~3 uV/LSB for resolving room-temperature TC emf.
GAIN_CLASSIFY = 1.0
GAIN_TC = 100.0
N_SAMPLES = 16  # repeated reads per (channel, gain) for a stable median

# Classification thresholds on the gain-1 input voltage.
OPEN_VOLTS = 1.5  # open input is pulled toward the +2.5 V reference
CJC_LO, CJC_HI = 0.08, 0.55  # 8-55 degC at 10 mV/degC
ZERO_VOLTS = 0.08  # a TC at room temp sits within a few mV of 0


def proto(lib, fn, args, ret=ctypes.c_ulong):
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = ret


def proto_all(da):
    proto(da, "olDaInitialize", [ctypes.c_char_p, ctypes.POINTER(HDRVR)])
    proto(da, "olDaTerminate", [HDRVR])
    proto(da, "olDaGetDASS", [HDRVR, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(HDASS)])
    proto(da, "olDaReleaseDASS", [HDASS])
    proto(da, "olDaConfig", [HDASS])
    proto(da, "olDaSetDataFlow", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetChannelType", [HDASS, ctypes.c_uint])
    proto(da, "olDaSetRange", [HDASS, ctypes.c_double, ctypes.c_double])
    proto(
        da,
        "olDaGetRange",
        [HDASS, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)],
    )
    proto(da, "olDaGetResolution", [HDASS, ctypes.POINTER(ctypes.c_uint)])
    proto(da, "olDaGetEncoding", [HDASS, ctypes.POINTER(ctypes.c_uint)])
    proto(da, "olDaGetSSCaps", [HDASS, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)])
    proto(
        da,
        "olDaGetSingleValue",
        [HDASS, ctypes.POINTER(ctypes.c_long), ctypes.c_uint, ctypes.c_double],
    )


def cap(da, hdass, idx) -> int:
    val = ctypes.c_ulong(0)
    da.olDaGetSSCaps(hdass, idx, ctypes.byref(val))
    return val.value


def code_to_volts(code: int, enc: int, res: int, vmax: float, vmin: float, gain: float) -> float:
    """Convert a raw single-value count to the real-world input voltage.

    Offset-binaries the code when the encoding is two's-complement, maps it
    linearly onto the ADC range, then divides by the gain to refer the reading
    back to the input.  For OL_ENC_BINARY (this board) no XOR is applied:
    code 0 -> vmin, code 2^res-1 -> vmax.
    """
    v = code
    if enc != OL_ENC_BINARY:
        v ^= 1 << (res - 1)
        v &= (1 << res) - 1
    adc_volts = (vmax - vmin) / (1 << res) * v + vmin
    return adc_volts / gain


def median(xs: list[int]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def read_codes(da, hdass, ch: int, gain: float, n: int) -> list[int]:
    """n repeated fixed-gain single-value reads -> raw codes."""
    val = ctypes.c_long(0)
    return [
        int(val.value)
        for _ in range(n)
        if da.olDaGetSingleValue(hdass, ctypes.byref(val), ch, gain) == 0
    ]


def configure(da, hdass, channel_type: int) -> tuple[int, int, int, float, float]:
    """Set single-value + wiring mode + range, config, return (ec, enc, res, vmax, vmin)."""
    da.olDaSetDataFlow(hdass, OL_DF_SINGLEVALUE)
    ec_type = da.olDaSetChannelType(hdass, channel_type)
    da.olDaSetRange(hdass, 10.0, -10.0)
    ec = da.olDaConfig(hdass) or ec_type
    enc = ctypes.c_uint(0)
    res = ctypes.c_uint(0)
    vmax = ctypes.c_double(0.0)
    vmin = ctypes.c_double(0.0)
    da.olDaGetEncoding(hdass, ctypes.byref(enc))
    da.olDaGetResolution(hdass, ctypes.byref(res))
    da.olDaGetRange(hdass, ctypes.byref(vmax), ctypes.byref(vmin))
    return ec, enc.value, res.value, vmax.value, vmin.value


def read_rows(
    da, hdass, n_chan: int, enc: int, res: int, vmax: float, vmin: float, n: int
) -> list[dict]:
    """Per channel, read gain-1 (classify) and gain-100 (resolve)."""
    rows = []
    for ch in range(n_chan):
        c1 = read_codes(da, hdass, ch, GAIN_CLASSIFY, n)
        c100 = read_codes(da, hdass, ch, GAIN_TC, n)
        v1 = code_to_volts(int(median(c1)), enc, res, vmax, vmin, GAIN_CLASSIFY)
        v100 = code_to_volts(int(median(c100)), enc, res, vmax, vmin, GAIN_TC)
        rng100 = (max(c100) - min(c100)) if c100 else 0
        rows.append({"ch": ch, "v1": v1, "v100": v100, "rng100": rng100})
    return rows


def scan(da, hdass, n_chan: int) -> dict | None:
    """Differential-mode one-shot scan."""
    ec, enc, res, vmax, vmin = configure(da, hdass, OL_CHNT_DIFFERENTIAL)
    if ec:
        print(f"  olDaConfig (differential) failed (ec={ec})")
        return None
    rows = read_rows(da, hdass, n_chan, enc, res, vmax, vmin, N_SAMPLES)
    return {"enc": enc, "res": res, "vmax": vmax, "vmin": vmin, "rows": rows}


def row_temp(r: dict, tc_type: str, cjc_degc: float) -> float | None:
    """Temperature for a non-open channel, or None if open / invalid."""
    if classify(r["v1"]) == "OPEN":
        return None
    emf = r["v100"] if abs(r["v100"]) < 0.1 else r["v1"]
    try:
        return convert_volts_to_temperature(tc_type, emf, cjc_temperature_c=cjc_degc)
    except DtolError:
        return None


def watch(da, hdass, n_chan: int, tc_type: str) -> None:
    """Live loop: re-read every ~1 s and print each channel's current temperature
    plus its running MAX (so a brief touch any time leaves a visible peak).
    Heavier averaging than the one-shot to cut front-end noise.  Ctrl-C to stop."""
    ec, enc, res, vmax, vmin = configure(da, hdass, OL_CHNT_DIFFERENTIAL)
    if ec:
        print(f"  olDaConfig (differential) failed (ec={ec})")
        return
    print(
        f"\n  WATCH mode (type-{tc_type}) — touch/heat ONE thermocouple bead and hold.\n"
        f"  'cur' is live, 'MAX' is the peak seen so far. A real TC + finger -> MAX climbs past ~28 C.\n"
        f"  Ctrl-C to stop.\n"
    )
    t0 = time.monotonic()
    peak: dict[int, float] = {}
    i = 0
    try:
        while True:
            rows = read_rows(da, hdass, n_chan, enc, res, vmax, vmin, 16)
            cjc_degc = rows[0]["v1"] / CJC_VOLTS_PER_DEG_C
            if i % 20 == 0:
                cols = "  ".join(f"{'ch' + str(r['ch']):>13}" for r in rows[1:])
                print(f"  {'t':>4} {'CJC':>5}  {cols}", flush=True)
            cells = []
            for r in rows[1:]:
                t = row_temp(r, tc_type, cjc_degc)
                if t is None:
                    cells.append(f"{'OPEN':>13}")
                    continue
                peak[r["ch"]] = max(peak.get(r["ch"], t), t)
                cells.append(f"{t:5.1f}/{peak[r['ch']]:5.1f}".rjust(13))
            print(
                f"  {time.monotonic() - t0:4.0f} {cjc_degc:5.1f}  " + "  ".join(cells), flush=True
            )
            i += 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n  stopped.")


def classify(v1: float) -> str:
    if v1 > OPEN_VOLTS:
        return "OPEN"
    if CJC_LO <= v1 <= CJC_HI:
        return "CJC?"
    if abs(v1) <= ZERO_VOLTS:
        return "TC~0V"
    return "SIGNAL"


def print_table(scan_: dict) -> None:
    print(
        f"\n  DIFFERENTIAL  encoding={scan_['enc']}(200=offset-binary) "
        f"res={scan_['res']}b range=[{scan_['vmin']:+.1f},{scan_['vmax']:+.1f}]V"
    )
    print(f"  {'ch':>2} | {'gain1 V':>10} | {'gain100 V':>12} | {'g100 rng':>8} | class")
    print("  " + "-" * 56)
    for r in scan_["rows"]:
        print(
            f"  {r['ch']:>2} | {r['v1']:>+10.4f} | {r['v100']:>+12.6f} | "
            f"{r['rng100']:>8d} | {classify(r['v1'])}"
        )


def interpret(scan_: dict, tc_type: str) -> None:
    rows = scan_["rows"]
    cjc_v = rows[0]["v1"]
    cjc_degc = cjc_v / CJC_VOLTS_PER_DEG_C
    print("\n  === INTERPRETATION ===")
    cls0 = classify(cjc_v)
    note = "" if cls0 == "CJC?" else f"  (WARNING: ch0 reads {cls0}, not a CJC-like voltage!)"
    print(f"  ch0 = CJC sensor: {cjc_v:+.4f} V -> cold junction {cjc_degc:.2f} degC{note}")

    found = 0
    for r in rows[1:]:
        ch, cls = r["ch"], classify(r["v1"])
        if cls == "OPEN":
            print(f"  ch{ch}: OPEN (no thermocouple - pulled to +2.5 V reference)")
            continue
        # Use the high-gain reading for the emf; fall back to gain-1 if it railed.
        emf = r["v100"] if abs(r["v100"]) < 0.1 else r["v1"]
        try:
            temp = convert_volts_to_temperature(tc_type, emf, cjc_temperature_c=cjc_degc)
            print(
                f"  ch{ch}: TYPE-{tc_type} emf={emf * 1e3:+.4f} mV -> {temp:.2f} degC"
                f"   <-- thermocouple"
            )
            found += 1
        except DtolError as exc:
            print(f"  ch{ch}: emf={emf * 1e3:+.4f} mV  (not a valid {tc_type} reading: {exc})")
    print(f"\n  Found {found} channel(s) reading a thermocouple temperature.")
    print("  A TC sitting on the bench should read ~room temperature (~22 degC).")


def probe_board(da, board: bytes, tc_type: str, watch_mode: bool) -> None:
    print(f"\n########## {board.decode()} (type-{tc_type} assumed) ##########")
    hdrvr = HDRVR()
    if da.olDaInitialize(board, ctypes.byref(hdrvr)):
        print("  olDaInitialize failed - board not present?")
        return
    hdass = HDASS()
    if da.olDaGetDASS(hdrvr, OLSS_AD, 0, ctypes.byref(hdass)):
        print("  olDaGetDASS(OLSS_AD,0) failed")
        da.olDaTerminate(hdrvr)
        return

    n_chan = cap(da, hdass, OLSSC_NUMCHANNELS)
    sup_tc = cap(da, hdass, OLSSC_SUP_THERMOCOUPLES)
    sup_ar = cap(da, hdass, OLSSC_SUP_SINGLEVALUE_AUTORANGE)
    n_diff = min(8, n_chan)  # differential mode exposes 8 channels (0-7)
    print(f"  caps: NUMCHANNELS={n_chan} SUP_THERMOCOUPLES={sup_tc} SUP_SV_AUTORANGE={sup_ar}")
    print(f"  reading channels 0-{n_diff - 1} (ch0=CJC, ch1-7=TC inputs)")

    if watch_mode:
        watch(da, hdass, n_diff, tc_type)
    else:
        s = scan(da, hdass, n_diff)
        if s is not None:
            print_table(s)
            interpret(s, tc_type)

    da.olDaReleaseDASS(hdass)
    da.olDaTerminate(hdrvr)


def main() -> None:
    args = list(sys.argv[1:])
    watch_mode = False
    for flag in ("watch", "--watch", "-w"):
        if flag in args:
            args.remove(flag)
            watch_mode = True
    board = (args[0] if args else "DT9806(00)").encode()
    tc_type = (args[1] if len(args) > 1 else "K").upper()
    dlls = load_openlayers()
    da = dlls.oldaapi
    proto_all(da)
    probe_board(da, board, tc_type, watch_mode)


if __name__ == "__main__":
    main()
