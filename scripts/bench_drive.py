"""Bench driver helpers that LEAVE THE OUTPUT LATCHED (no auto-park).

Companion to ``docs/bench-dio-ao.md``. The shipped ``bench_io.py ao-gates``
command parks the AO to 0 V in its teardown, which erases the DMM evidence
before a human operator can read it. The DT9806 D/A latches its last value
through ``close()`` (bench-confirmed 2026-05-29), so the reliable witness for
"a rejected write never reaches hardware" is:

  1. park the channel at a DISTINCTIVE held value (e.g. +3.000 V),
  2. attempt each rejected write WITHOUT touching the output afterwards,
  3. read the DMM at leisure — it must still show the parked value; a leak
     would have latched the *attempted* value (2.5 / a rail / +5) instead.

Subcommands::

    uv run python scripts/bench_drive.py park    --board "DT9806(00)" --channel 0 --volts 3.0
    uv run python scripts/bench_drive.py witness  --board "DT9806(00)" --channel 0
"""

from __future__ import annotations

import argparse
import sys

import anyio

from dtollib import (
    AnalogInputVoltage,
    AnalogOutputVoltage,
    ChannelType,
    DataFlow,
    DigitalInputPort,
    DigitalLine,
    DigitalOutputPort,
    DtolConfirmationRequiredError,
    DtolValidationError,
    SubsystemType,
    TaskSpec,
    open_device,
)


def _ao_spec(
    board: str,
    channel: int,
    *,
    requires_confirm: bool = True,
    safe_min: float | None = None,
    safe_max: float | None = None,
) -> TaskSpec:
    return TaskSpec(
        name="bench_ao",
        board=board,
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[
            AnalogOutputVoltage(
                physical_channel=channel,
                name=f"ao{channel}",
                min_val=-10.0,
                max_val=10.0,
                requires_confirm=requires_confirm,
                safe_min=safe_min,
                safe_max=safe_max,
            )
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def cmd_park(args: argparse.Namespace) -> int:
    """Drive the channel to a held value and close (DAC latches it)."""
    ch = args.channel
    s = await open_device(_ao_spec(args.board, ch, requires_confirm=True), autostart=False)
    try:
        await s.write({f"ao{ch}": args.volts}, confirm=True)
        print(f"Parked {args.board} ao{ch} = {args.volts:+.3f} V (latched on close).")
    finally:
        await s.close()
    return 0


async def cmd_witness(args: argparse.Namespace) -> int:
    """Attempt the three rejected writes; never touch the output on success.

    Each attempt uses a value DIFFERENT from a sensible parked value so a leak
    is unambiguous on the meter. Output is left exactly as found.
    """
    board, ch = args.board, args.channel
    results: list[tuple[str, str, str]] = []

    # 1C — confirm gate: confirm=False on a requires_confirm channel.
    s = await open_device(_ao_spec(board, ch, requires_confirm=True), autostart=False)
    try:
        try:
            await s.write({f"ao{ch}": 2.5}, confirm=False)
            results.append(("1C", "FAIL", "no exception raised — 2.5 V may have latched"))
        except DtolConfirmationRequiredError:
            results.append(("1C", "PASS", "DtolConfirmationRequiredError; output untouched"))
    finally:
        await s.close()

    # 1D — out of range: 11 V with confirm=True.
    s = await open_device(_ao_spec(board, ch, requires_confirm=False), autostart=False)
    try:
        try:
            await s.write({f"ao{ch}": 11.0}, confirm=True)
            results.append(("1D", "FAIL", "no exception raised — a rail may have latched"))
        except DtolValidationError as e:
            tag = "PASS" if "range" in str(e).lower() else "WARN"
            results.append(("1D", tag, str(e)))
    finally:
        await s.close()

    # 1E-a — safe band [-1, +1], confirm=False, attempt +5.
    s = await open_device(
        _ao_spec(board, ch, requires_confirm=False, safe_min=-1.0, safe_max=1.0),
        autostart=False,
    )
    try:
        try:
            await s.write({f"ao{ch}": 5.0}, confirm=False)
            results.append(("1E-a", "FAIL", "no exception raised — 5 V may have latched"))
        except DtolConfirmationRequiredError:
            results.append(("1E-a", "PASS", "out-of-band rejected; output untouched"))
    finally:
        await s.close()

    for step, res, detail in results:
        print(f"  [{res}] {step}: {detail}")
    print(
        "\nOutput was NOT modified by any passing gate. Read the DMM now: it must "
        "still show the parked value. A jump to 2.5 / a rail / +5 V is a leak."
    )
    return 0 if all(r != "FAIL" for _, r, _ in results) else 1


def _ai_spec(board: str, channel: int, *, differential: bool = False) -> TaskSpec:
    ctype = ChannelType.DIFFERENTIAL if differential else ChannelType.SINGLE_ENDED
    return TaskSpec(
        name="bench_ai",
        board=board,
        subsystem_type=SubsystemType.ANALOG_INPUT,
        channels=[
            AnalogInputVoltage(physical_channel=channel, name=f"ai{channel}", channel_type=ctype)
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def cmd_ai_read(args: argparse.Namespace) -> int:
    """Read an AI channel (single-ended or --differential): code + oracle volts.

    Reports both the public ``poll()`` value and the SDK ``code_to_volts`` oracle.
    Use ``--differential`` to read channel N as a differential pair (N=HI,
    N+8=LO on the DT9806) — rejects the common-mode offset a single-ended
    ground-referenced read picks up.
    """
    board, channel = args.board, args.channel
    mode = "differential" if args.differential else "single-ended"
    s = await open_device(_ai_spec(board, channel, differential=args.differential), autostart=False)
    try:
        reading = await s.poll(timeout=1.0)
        poll_val = reading.values[f"ai{channel}"]
        code = await anyio.to_thread.run_sync(s.backend.get_single_value, s.hdass, channel, 1.0)
        volts = await anyio.to_thread.run_sync(s.backend.code_to_volts, s.hdass, code, 1.0)
    finally:
        await s.close()
    print(
        f"{board} ai{channel} [{mode}]: poll={poll_val}  code={code}  oracle_volts={volts:+.4f} V"
    )
    return 0


def _do_spec(board: str, lines: int | list[int], *, requires_confirm: bool = True) -> TaskSpec:
    """DOUT port (channel 0) with named ``do{bit}`` line views for ``lines``.

    Pass an empty list for whole-port byte writes via the ``"dout"`` key.
    """
    bits = [lines] if isinstance(lines, int) else list(lines)
    return TaskSpec(
        name="bench_do",
        board=board,
        subsystem_type=SubsystemType.DIGITAL_OUTPUT,
        channels=[
            DigitalOutputPort(
                physical_channel=0,
                name="dout",
                lines=tuple(DigitalLine(bit=b, name=f"do{b}") for b in bits),
                requires_confirm=requires_confirm,
            )
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def cmd_do_witness(args: argparse.Namespace) -> int:
    """Attempt a confirm=False DOUT write; leave the line untouched on reject.

    Park the line at a known level first (e.g. `bench_io do-drive ... high`),
    then run this: the confirm gate must raise before the SDK write, so the DMM
    must still show the parked level. A flip to the other level is a leak.
    """
    board, line = args.board, args.line
    s = await open_device(_do_spec(board, line, requires_confirm=True), autostart=False)
    try:
        try:
            # Attempt to drive the OPPOSITE of a high park (low) without confirm.
            await s.write({f"do{line}": False}, confirm=False)
            print(f"  [FAIL] do{line}: no exception raised — LOW may have latched")
            rc = 1
        except DtolConfirmationRequiredError:
            print(f"  [PASS] do{line}: DtolConfirmationRequiredError; line untouched")
            rc = 0
    finally:
        await s.close()
    print("\nRead the DMM: the line must still show its parked level. A flip is a leak.")
    return rc


async def cmd_do_port(args: argparse.Namespace) -> int:
    """Write an 8-bit port value to the DOUT port via the public port API.

    Proves the 8 relays are the 8 bits of one port: value=1 fires relay 0,
    value=2 fires relay 1, value=4 relay 2, value=8 relay 3, ... value=255 all.
    Uses ``write({"dout": value})`` — the real ``DigitalOutputPort`` whole-byte
    path (docs/design.md §18.1), not a backend escape hatch.
    """
    board, value = args.board, args.value
    s = await open_device(_do_spec(board, [], requires_confirm=True), autostart=False)
    try:
        await s.write({"dout": value}, confirm=True)
        bits = format(value & 0xFF, "08b")
        print(f"Wrote DOUT port = {value} (0b{bits}) on {board} (latched).")
        print("Relays high = bits set, counting from bit0 = relay0 (rightmost).")
    finally:
        await s.close()
    return 0


def _di_spec(board: str, lines: int | list[int]) -> TaskSpec:
    """DIN port (channel 0) with named ``di{bit}`` line views for ``lines``.

    Pass an empty list to read just the raw ``"din"`` port byte.
    """
    bits = [lines] if isinstance(lines, int) else list(lines)
    return TaskSpec(
        name="bench_di",
        board=board,
        subsystem_type=SubsystemType.DIGITAL_INPUT,
        channels=[
            DigitalInputPort(
                physical_channel=0,
                name="din",
                lines=tuple(DigitalLine(bit=b, name=f"di{b}") for b in bits),
            )
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def cmd_di_port(args: argparse.Namespace) -> int:
    """Read the 8-bit input port via the public port API and show every bit.

    ``poll()`` surfaces the whole port byte under the ``"din"`` key; each bit is
    one DIN line. Reading the port reveals which line a jumper landed on and
    validates all 8 input bits at once.
    """
    board = args.board
    s = await open_device(_di_spec(board, []), autostart=False)
    try:
        reading = await s.poll(timeout=1.0)
        value = int(reading.values["din"]) & 0xFF
    finally:
        await s.close()
    bits = format(value, "08b")
    high = [i for i in range(8) if value & (1 << i)]
    print(f"{board} DIN port = {value} (0b{bits})")
    print(f"  high lines (bit set): {high or 'none'}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("park", help="drive a held value and close (latches)")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.add_argument("--volts", type=float, required=True)
    sp.set_defaults(func=cmd_park)

    sp = sub.add_parser("witness", help="attempt rejected writes, leave output untouched")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.set_defaults(func=cmd_witness)

    sp = sub.add_parser("do-witness", help="attempt confirm=False DOUT write, leave line untouched")
    sp.add_argument("--board", required=True)
    sp.add_argument("--line", type=int, default=0)
    sp.set_defaults(func=cmd_do_witness)

    sp = sub.add_parser("do-port", help="write a raw 0-255 byte to DOUT channel 0")
    sp.add_argument("--board", required=True)
    sp.add_argument("--value", type=int, required=True, help="0-255 port byte")
    sp.set_defaults(func=cmd_do_port)

    sp = sub.add_parser("di-port", help="read the raw 8-bit DIN port (channel 0)")
    sp.add_argument("--board", required=True)
    sp.set_defaults(func=cmd_di_port)

    sp = sub.add_parser("ai-read", help="read AI channel: poll volts + oracle code_to_volts")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.add_argument("--differential", action="store_true", help="read as a differential pair")
    sp.set_defaults(func=cmd_ai_read)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return anyio.run(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
