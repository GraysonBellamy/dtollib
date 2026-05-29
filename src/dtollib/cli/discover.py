"""``dtol-discover`` — enumerate DT-Open Layers boards and subsystems.

Default output: one row per board with a flat summary of its
subsystems.  ``--board NAME`` drills into a single board's
capability set.  ``--json`` produces machine-readable output.

Exit codes:

- ``0`` — discovery succeeded (zero boards is not an error; the SDK
  may simply have nothing attached).
- ``1`` — SDK load or enumeration failed.
- ``2`` — invocation problem.

Design reference: docs/design.md §21.1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from dtollib.errors import DtolError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dtollib.system.models import BoardInfo, SubsystemInfo


__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by the ``dtol-discover`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

        backend = DataAcqBackend()
        boards = backend.enum_boards()
    except DtolError as exc:
        print(f"dtol-discover: failed to enumerate boards: {exc}", file=sys.stderr)
        return 1

    if args.board is not None:
        return _emit_single_board(backend, boards, args.board, json_out=args.json)

    return _emit_summary(backend, boards, json_out=args.json)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dtol-discover",
        description="Enumerate DT-Open Layers boards and subsystems.",
    )
    parser.add_argument(
        "--board",
        metavar="NAME",
        help=(
            "Drill into a single board's subsystems and capability "
            "set rather than emitting the multi-board summary."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of formatted text.",
    )
    return parser


def _emit_summary(
    backend: Any,
    boards: list[BoardInfo],
    *,
    json_out: bool,
) -> int:
    """Emit the multi-board summary listing."""
    rows: list[dict[str, Any]] = []
    for board in boards:
        try:
            subs = backend.enum_subsystems(board.name)
        except DtolError as exc:
            rows.append(
                {
                    "board": _board_dict(board),
                    "subsystems": [],
                    "error": str(exc),
                }
            )
            continue
        rows.append(
            {
                "board": _board_dict(board),
                "subsystems": [_subsystem_dict(s) for s in subs],
            }
        )

    if json_out:
        print(json.dumps({"boards": rows}, indent=2))
        return 0

    if not rows:
        print("no DT-Open Layers boards found")
        print(
            "(if a board is plugged in, run dtol-diag to verify the SDK "
            "DLL load and check Open Layers Control Panel)"
        )
        return 0

    for row in rows:
        b = row["board"]
        print(
            f"{b['name']}  model={b['model']}  driver={b['driver_name']}  instance={b['instance']}"
        )
        if "error" in row:
            print(f"  ! enum_subsystems failed: {row['error']}")
            continue
        for sub in row["subsystems"]:
            flags = ",".join(
                k.replace("supports_", "")
                for k, v in sub.items()
                if k.startswith("supports_") and v
            )
            print(
                f"  - {sub['type']}#{sub['element']:<2} "
                f"channels={sub['num_channels']:<3} "
                f"cgl_depth={sub['cgl_depth']:<3} "
                f"flags={flags}"
            )
    return 0


def _emit_single_board(
    backend: Any,
    boards: list[BoardInfo],
    name: str,
    *,
    json_out: bool,
) -> int:
    """Emit details for a single board only."""
    matching = [b for b in boards if b.name == name]
    if not matching:
        print(f"dtol-discover: board {name!r} not found", file=sys.stderr)
        print(
            f"available boards: {', '.join(b.name for b in boards) or '(none)'}",
            file=sys.stderr,
        )
        return 1

    board = matching[0]
    try:
        subs = backend.enum_subsystems(board.name)
    except DtolError as exc:
        if json_out:
            print(
                json.dumps(
                    {"board": _board_dict(board), "error": str(exc)},
                    indent=2,
                )
            )
        else:
            print(f"dtol-discover: enum_subsystems({name}) failed: {exc}", file=sys.stderr)
        return 1

    if json_out:
        print(
            json.dumps(
                {
                    "board": _board_dict(board),
                    "subsystems": [_subsystem_dict(s) for s in subs],
                },
                indent=2,
            )
        )
        return 0

    print(f"board: {board.name}")
    print(f"  model:       {board.model}")
    print(f"  driver:      {board.driver_name}")
    print(f"  instance:    {board.instance}")
    print(f"  subsystems:  {len(subs)}")
    for sub in subs:
        print(f"  - {sub.type.value} element={sub.element}")
        print(f"      num_channels: {sub.num_channels}")
        print(f"      cgl_depth:    {sub.cgl_depth}")
        if sub.max_throughput_hz is not None:
            print(f"      max_throughput_hz: {sub.max_throughput_hz}")
        for attr in (
            "supports_singlevalue",
            "supports_continuous",
            "supports_simultaneous_sh",
            "supports_multisensor",
            "supports_dma",
            "returns_floats",
        ):
            print(f"      {attr}: {getattr(sub, attr)}")
    return 0


def _board_dict(board: BoardInfo) -> dict[str, Any]:
    return {
        "name": board.name,
        "model": board.model,
        "driver_name": board.driver_name,
        "instance": board.instance,
    }


def _subsystem_dict(sub: SubsystemInfo) -> dict[str, Any]:
    return {
        "type": sub.type.value,
        "element": sub.element,
        "num_channels": sub.num_channels,
        "cgl_depth": sub.cgl_depth,
        "max_throughput_hz": sub.max_throughput_hz,
        "supports_singlevalue": sub.supports_singlevalue,
        "supports_continuous": sub.supports_continuous,
        "supports_simultaneous_sh": sub.supports_simultaneous_sh,
        "supports_multisensor": sub.supports_multisensor,
        "supports_dma": sub.supports_dma,
        "returns_floats": sub.returns_floats,
    }


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
