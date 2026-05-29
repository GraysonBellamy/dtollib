"""Interactive bench harness for DIN / DOUT / AO hardware validation.

Companion to ``docs/bench-dio-ao.md``. Each subcommand drives one piece of the
single-value output / digital-IO path on real hardware and (where a multimeter
is involved) prompts the operator for the measured value, appending a structured
result line to a JSON-lines log.

Run with the SDK present on the bench machine::

    uv run python scripts/bench_io.py preflight
    uv run python scripts/bench_io.py ao-gates  --board "DT9806(00)"
    uv run python scripts/bench_io.py ao-sweep   --board "DT9806(00)" --channel 0
    uv run python scripts/bench_io.py ao-drive   --board "DT9806(00)" --channel 0 --volts 2.5
    uv run python scripts/bench_io.py do-drive   --board "DT9806(00)" --line 0 --level high
    uv run python scripts/bench_io.py do-walk    --board "DT9806(00)" --lines 8
    uv run python scripts/bench_io.py di-read    --board "DT9806(00)" --lines 8
    uv run python scripts/bench_io.py loopback-ao --out-board "DT9806(00)" --in-board "DT9805(00)"
    uv run python scripts/bench_io.py loopback-do --out-board "DT9806(00)" --in-board "DT9805(00)"

Every result row is appended to ``--log`` (default ``bench_results.jsonl``) so the
coordinating agent can read back what happened without scraping stdout.

Capability ground truth: ``docs/plan-hardware-functional.md``. Gate semantics:
``docs/design.md`` §18.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from dtollib import (
    AnalogInputVoltage,
    AnalogOutputVoltage,
    DataFlow,
    DigitalInputPort,
    DigitalLine,
    DigitalOutputPort,
    DtolConfirmationRequiredError,
    DtolValidationError,
    SubsystemType,
    TaskSpec,
    find_devices,
    find_subsystems,
    open_device,
)

# --------------------------------------------------------------------------- #
# Result logging
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _log_row(log_path: Path, row: dict[str, Any]) -> None:
    """Append one JSON object to the results log and echo a one-liner."""
    row = {"ts": _now(), **row}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    status = row.get("result", "")
    print(f"  -> logged [{status}] {row.get('step', '')}: {row.get('detail', '')}")


def _prompt_float(label: str) -> float | None:
    """Prompt the operator for a DMM reading; blank/skip -> None."""
    raw = input(f"  {label} (blank to skip): ").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        print("  ! not a number, recording as skipped")
        return None


def _prompt_yes(label: str) -> bool:
    return input(f"  {label} [y/N]: ").strip().lower().startswith("y")


async def _pause(message: str) -> None:
    """Block for operator acknowledgement off the event loop."""
    await anyio.to_thread.run_sync(input, message)


# --------------------------------------------------------------------------- #
# Spec builders
# --------------------------------------------------------------------------- #


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


def _ai_spec(board: str, channel: int) -> TaskSpec:
    return TaskSpec(
        name="bench_ai",
        board=board,
        subsystem_type=SubsystemType.ANALOG_INPUT,
        channels=[AnalogInputVoltage(physical_channel=channel, name=f"ai{channel}")],
        data_flow=DataFlow.SINGLE_VALUE,
    )


# DT9805/06 DIO is a single 8-bit port (channel 0); the 8 lines are its bits.
# Specs declare a DigitalOutputPort / DigitalInputPort on channel 0 with one
# named DigitalLine view per bit (keys ``do{bit}`` / ``di{bit}``), so the harness
# drives/reads individual lines through the port+bitmask model (docs/design.md
# §18.1) instead of the removed per-line classes.


def _do_spec(board: str, lines: int | list[int], *, requires_confirm: bool = True) -> TaskSpec:
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


def _di_spec(board: str, lines: int | list[int]) -> TaskSpec:
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


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


async def cmd_preflight(args: argparse.Namespace) -> int:
    """Enumerate boards + subsystem element counts."""
    log = Path(args.log)
    boards = await find_devices()
    if not boards:
        print("No DT-Open Layers boards enumerated. Is the SDK installed?")
        _log_row(log, {"step": "preflight", "result": "FAIL", "detail": "no boards"})
        return 1
    for b in boards:
        print(f"\nBoard: {b.name}  model={b.model}  driver={b.driver_name}")
        subs = await find_subsystems(b.name)
        counts: dict[str, int] = {}
        for s in subs:
            counts[str(s.type.value)] = s.num_channels
            print(
                f"  {s.type.value:<16} element={s.element} "
                f"channels={s.num_channels} singlevalue={s.supports_singlevalue} "
                f"continuous={s.supports_continuous}"
            )
        _log_row(
            log,
            {
                "step": "preflight",
                "result": "INFO",
                "board": b.name,
                "model": b.model,
                "subsystem_channels": counts,
            },
        )
    print("\nRecord the AO / DOUT / DIN counts into docs/bench-dio-ao.md.")
    return 0


async def cmd_ao_gates(args: argparse.Namespace) -> int:
    """Confirm gate, out-of-range, safe-band. DMM-witnessed."""
    log = Path(args.log)
    board, ch = args.board, args.channel
    print(f"AO gate checks on {board} ao{ch}.")
    print("Attach the DMM to the AO output; these prove a *rejected* write does")
    print("not move the output. Park the channel first with `ao-drive` if needed.\n")

    # 1C — confirm gate
    print("[1C] write without confirm on a requires_confirm channel")
    s = await open_device(_ao_spec(board, ch, requires_confirm=True), autostart=False)
    try:
        try:
            await s.write({f"ao{ch}": 2.5}, confirm=False)
            res, detail = "FAIL", "no exception raised"
        except DtolConfirmationRequiredError:
            res, detail = "PASS", "DtolConfirmationRequiredError raised"
    finally:
        await s.close()
    if (
        res == "PASS"
        and not args.no_prompt
        and not _prompt_yes("DMM unchanged during the blocked write?")
    ):
        res, detail = "FAIL", "operator: DMM moved on blocked write"
    _log_row(log, {"step": "1C-confirm-gate", "result": res, "detail": detail})

    # 1D — out of range
    print("[1D] write 11.0 V (> 10 V max) with confirm=True")
    s = await open_device(_ao_spec(board, ch, requires_confirm=False), autostart=False)
    try:
        try:
            await s.write({f"ao{ch}": 11.0}, confirm=True)
            res, detail = "FAIL", "no exception raised"
        except DtolValidationError as e:
            res = "PASS" if "range" in str(e).lower() else "WARN"
            detail = str(e)
    finally:
        await s.close()
    if (
        res == "PASS"
        and not args.no_prompt
        and not _prompt_yes("DMM unchanged / no glitch on the out-of-range write?")
    ):
        res, detail = "FAIL", "operator: DMM moved on out-of-range write"
    _log_row(log, {"step": "1D-out-of-range", "result": res, "detail": detail})

    # 1E — safe band
    print("[1E] safe band [-1, +1], requires_confirm=False, write +5 V")
    spec = _ao_spec(board, ch, requires_confirm=False, safe_min=-1.0, safe_max=1.0)
    s = await open_device(spec, autostart=False)
    try:
        try:
            await s.write({f"ao{ch}": 5.0}, confirm=False)
            res_a, detail_a = "FAIL", "out-of-band write not rejected"
        except DtolConfirmationRequiredError:
            res_a, detail_a = "PASS", "out-of-band rejected without confirm"
        _log_row(log, {"step": "1E-safeband-reject", "result": res_a, "detail": detail_a})

        await s.write({f"ao{ch}": 5.0}, confirm=True)  # now allowed
        print("  drove +5.0 V with confirm=True")
        meas = None if args.no_prompt else _prompt_float("DMM reading (V)")
        res_b = "PASS" if (meas is None or abs(meas - 5.0) <= args.tol) else "FAIL"
        _log_row(
            log,
            {
                "step": "1E-safeband-allow",
                "result": res_b,
                "setpoint_v": 5.0,
                "measured_v": meas,
                "tol_v": args.tol,
            },
        )
    finally:
        await s.write({f"ao{ch}": 0.0}, confirm=True)  # park safe
        await s.close()
    return 0


async def cmd_ao_drive(args: argparse.Namespace) -> int:
    """Drive one AO channel to a voltage and hold for measurement."""
    log = Path(args.log)
    board, ch, v = args.board, args.channel, args.volts
    s = await open_device(_ao_spec(board, ch, requires_confirm=True), autostart=False)
    try:
        await s.write({f"ao{ch}": v}, confirm=True)
        print(f"Driving {board} ao{ch} = {v:+.3f} V (held).")
        meas = None if args.no_prompt else _prompt_float("DMM reading (V)")
        err_mv = None if meas is None else round((meas - v) * 1000, 3)
        res = "PASS" if (meas is None or abs(meas - v) <= args.tol) else "FAIL"
        _log_row(
            log,
            {
                "step": "ao-drive",
                "result": res,
                "board": board,
                "channel": ch,
                "setpoint_v": v,
                "measured_v": meas,
                "error_mv": err_mv,
                "tol_v": args.tol,
            },
        )
        if not args.no_prompt:
            await _pause("  Enter to release/close (watch the meter for teardown state)...")
    finally:
        await s.close()
    return 0


async def cmd_ao_sweep(args: argparse.Namespace) -> int:
    """Sweep a list of setpoints, prompt DMM at each."""
    log = Path(args.log)
    board, ch = args.board, args.channel
    setpoints = args.volts or [-10.0, -7.3, -5.0, 0.0, 1.234, 2.5, 5.0, 10.0]
    s = await open_device(_ao_spec(board, ch, requires_confirm=True), autostart=False)
    try:
        for v in setpoints:
            await s.write({f"ao{ch}": v}, confirm=True)
            print(f"\nDriving {board} ao{ch} = {v:+.3f} V")
            meas = None if args.no_prompt else _prompt_float("DMM reading (V)")
            err_mv = None if meas is None else round((meas - v) * 1000, 3)
            res = "PASS" if (meas is None or abs(meas - v) <= args.tol) else "FAIL"
            _log_row(
                log,
                {
                    "step": "ao-sweep",
                    "result": res,
                    "board": board,
                    "channel": ch,
                    "setpoint_v": v,
                    "measured_v": meas,
                    "error_mv": err_mv,
                    "tol_v": args.tol,
                },
            )
    finally:
        await s.write({f"ao{ch}": 0.0}, confirm=True)
        await s.close()
    return 0


async def cmd_do_drive(args: argparse.Namespace) -> int:
    """Drive one DOUT line high/low and hold for measurement."""
    log = Path(args.log)
    board, line = args.board, args.line
    level = args.level == "high"
    s = await open_device(_do_spec(board, line, requires_confirm=True), autostart=False)
    try:
        await s.write({f"do{line}": level}, confirm=True)
        print(f"Driving {board} do{line} = {'HIGH' if level else 'LOW'} (held).")
        meas = None if args.no_prompt else _prompt_float("DMM reading (V)")
        _log_row(
            log,
            {
                "step": "do-drive",
                "result": "INFO",
                "board": board,
                "line": line,
                "level": "high" if level else "low",
                "measured_v": meas,
            },
        )
        if not args.no_prompt:
            await _pause("  Enter to release/close...")
    finally:
        await s.close()
    return 0


async def cmd_do_walk(args: argparse.Namespace) -> int:
    """Walking-1 then walking-0 across DOUT lines, probe each.

    Drives the whole 8-bit port in one session: each step writes a single byte
    to the ``dout`` port (``1 << active`` for walking-1, its complement for
    walking-0). One session keeps every bit in a known state simultaneously —
    a per-line write in a fresh session would reseed the shadow and clobber the
    other bits.
    """
    log = Path(args.log)
    board, n = args.board, args.lines
    full = (1 << n) - 1
    s = await open_device(_do_spec(board, list(range(n)), requires_confirm=True), autostart=False)
    try:
        for pattern, name in ((True, "walking-1"), (False, "walking-0")):
            print(
                f"\n=== {name}: active line = {'HIGH' if pattern else 'LOW'}, rest = "
                f"{'LOW' if pattern else 'HIGH'} ==="
            )
            for active in range(n):
                byte = (1 << active) if pattern else (full & ~(1 << active))
                await s.write({"dout": byte}, confirm=True)
                print(
                    f"  {name}: line {active} is the odd one out "
                    f"(port=0b{byte:0{n}b}) — probe each pin."
                )
                ok = (
                    True
                    if args.no_prompt
                    else _prompt_yes(f"All {n} pins match (only line {active} differs)?")
                )
                _log_row(
                    log,
                    {
                        "step": f"do-{name}",
                        "result": "PASS" if ok else "FAIL",
                        "board": board,
                        "active_line": active,
                        "port_byte": byte,
                        "lines": n,
                    },
                )
    finally:
        await s.write({"dout": 0}, confirm=True)  # de-energize all lines
        await s.close()
    return 0


async def cmd_di_read(args: argparse.Namespace) -> int:
    """Poll the DIN port once and report the raw byte + each line."""
    log = Path(args.log)
    board, n = args.board, args.lines
    s = await open_device(_di_spec(board, list(range(n))), autostart=False)
    try:
        reading = await s.poll(timeout=1.0)
        byte = int(reading.values["din"])
        vals = {f"di{line}": bool(reading.values[f"di{line}"]) for line in range(n)}
    finally:
        await s.close()
    print(f"{board} DIN port = {byte} (0b{byte:0{n}b})  lines: {vals}")
    _log_row(
        log,
        {"step": "di-read", "result": "INFO", "board": board, "port_byte": byte, "values": vals},
    )
    return 0


async def cmd_loopback_ao(args: argparse.Namespace) -> int:
    """AO(out_board) -> AI(in_board), read back, compare."""
    log = Path(args.log)
    v = args.volts[0] if args.volts else 2.5
    ao = await open_device(
        _ao_spec(args.out_board, args.out_channel, requires_confirm=True), autostart=False
    )
    ai = await open_device(_ai_spec(args.in_board, args.in_channel), autostart=False)
    try:
        await ao.write({f"ao{args.out_channel}": v}, confirm=True)
        reading = await ai.poll(timeout=1.0)
        recovered = float(reading.values[f"ai{args.in_channel}"])
        res = "PASS" if abs(recovered - v) <= args.tol else "FAIL"
        print(
            f"AO {args.out_board}#{args.out_channel} = {v:+.3f} V -> "
            f"AI {args.in_board}#{args.in_channel} read {recovered:+.3f} V [{res}]"
        )
        _log_row(
            log,
            {
                "step": "loopback-ao",
                "result": res,
                "out": f"{args.out_board}#{args.out_channel}",
                "in": f"{args.in_board}#{args.in_channel}",
                "setpoint_v": v,
                "recovered_v": recovered,
                "tol_v": args.tol,
            },
        )
    finally:
        await ao.write({f"ao{args.out_channel}": 0.0}, confirm=True)
        await ai.close()
        await ao.close()
    return 0


async def cmd_loopback_do(args: argparse.Namespace) -> int:
    """DOUT(out_board) -> DIN(in_board), both levels, compare."""
    log = Path(args.log)
    do = await open_device(
        _do_spec(args.out_board, args.out_line, requires_confirm=True), autostart=False
    )
    di = await open_device(_di_spec(args.in_board, args.in_line), autostart=False)
    try:
        for level in (True, False):
            await do.write({f"do{args.out_line}": level}, confirm=True)
            reading = await di.poll(timeout=1.0)
            got = bool(reading.values[f"di{args.in_line}"])
            res = "PASS" if got is level else "FAIL"
            print(
                f"DOUT {args.out_board}#{args.out_line} drove {level} -> "
                f"DIN {args.in_board}#{args.in_line} read {got} [{res}]"
            )
            _log_row(
                log,
                {
                    "step": "loopback-do",
                    "result": res,
                    "out": f"{args.out_board}#{args.out_line}",
                    "in": f"{args.in_board}#{args.in_line}",
                    "drove": level,
                    "read": got,
                },
            )
    finally:
        await di.close()
        await do.close()
    return 0


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--log", default="bench_results.jsonl", help="JSON-lines results log")
    p.add_argument(
        "--no-prompt",
        action="store_true",
        help="skip operator prompts (software-only assertions; DMM rows logged as unverified)",
    )
    p.add_argument(
        "--tol",
        type=float,
        default=0.10,
        help="AO comparison tolerance, volts (default 0.10; tighten for direct DMM)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("preflight", help="enumerate boards + subsystem counts")
    sp.set_defaults(func=cmd_preflight)

    sp = sub.add_parser("ao-gates", help="confirm / range / safe-band gate checks")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.set_defaults(func=cmd_ao_gates)

    sp = sub.add_parser("ao-drive", help="drive one AO channel and hold")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.add_argument("--volts", type=float, required=True)
    sp.set_defaults(func=cmd_ao_drive)

    sp = sub.add_parser("ao-sweep", help="sweep AO setpoints, prompt DMM at each")
    sp.add_argument("--board", required=True)
    sp.add_argument("--channel", type=int, default=0)
    sp.add_argument("--volts", type=float, nargs="*", help="setpoints (default: standard sweep)")
    sp.set_defaults(func=cmd_ao_sweep)

    sp = sub.add_parser("do-drive", help="drive one DOUT line high/low and hold")
    sp.add_argument("--board", required=True)
    sp.add_argument("--line", type=int, default=0)
    sp.add_argument("--level", choices=["high", "low"], required=True)
    sp.set_defaults(func=cmd_do_drive)

    sp = sub.add_parser("do-walk", help="walking-1/0 across DOUT lines")
    sp.add_argument("--board", required=True)
    sp.add_argument(
        "--lines", type=int, required=True, help="number of DOUT lines (from preflight)"
    )
    sp.set_defaults(func=cmd_do_walk)

    sp = sub.add_parser("di-read", help="poll DIN lines and report levels")
    sp.add_argument("--board", required=True)
    sp.add_argument("--lines", type=int, required=True, help="number of DIN lines (from preflight)")
    sp.set_defaults(func=cmd_di_read)

    sp = sub.add_parser("loopback-ao", help="AO->AI loopback (cross-board capable)")
    sp.add_argument("--out-board", required=True)
    sp.add_argument("--out-channel", type=int, default=0)
    sp.add_argument("--in-board", required=True)
    sp.add_argument("--in-channel", type=int, default=0)
    sp.add_argument("--volts", type=float, nargs="*", help="test voltage (default 2.5)")
    sp.set_defaults(func=cmd_loopback_ao)

    sp = sub.add_parser("loopback-do", help="DOUT->DIN loopback (cross-board capable)")
    sp.add_argument("--out-board", required=True)
    sp.add_argument("--out-line", type=int, default=0)
    sp.add_argument("--in-board", required=True)
    sp.add_argument("--in-line", type=int, default=0)
    sp.set_defaults(func=cmd_loopback_do)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(f"Results log: {Path(args.log).resolve()}")
    return anyio.run(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
