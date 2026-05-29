"""``dtol-info`` — full per-board capability dump.

Unlike ``dtol-discover`` (a summary), ``dtol-info`` renders every
``CapabilitySet`` field for each subsystem on a board — the reference
view when debugging why a configuration is rejected.

Exit codes:

- ``0`` — info printed (zero boards is not an error).
- ``1`` — SDK load / enumeration failed, or a named board was not found.
- ``2`` — invocation problem.

Design reference: docs/design.md §21.4.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import TYPE_CHECKING, Any

from dtollib.errors import DtolError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dtollib.backend.base import DtolBackend
    from dtollib.system.models import BoardInfo, SubsystemInfo

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by the ``dtol-info`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        backend = _resolve_backend(args.backend)
        boards = backend.enum_boards()
    except DtolError as exc:
        print(f"dtol-info: failed to enumerate boards: {exc}", file=sys.stderr)
        return 1

    if args.board is not None:
        boards = [b for b in boards if b.name == args.board]
        if not boards:
            print(f"dtol-info: board {args.board!r} not found", file=sys.stderr)
            return 1

    payload: list[dict[str, Any]] = []
    for board in boards:
        try:
            subs = backend.enum_subsystems(board.name)
        except DtolError as exc:
            payload.append({"board": _board_dict(board), "error": str(exc)})
            continue
        payload.append(
            {"board": _board_dict(board), "subsystems": [_subsystem_dict(s) for s in subs]}
        )

    if args.json:
        print(json.dumps({"boards": payload}, indent=2))
        return 0

    _print_text(payload)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dtol-info",
        description="Full per-board capability dump for DT-Open Layers devices.",
    )
    parser.add_argument("--board", metavar="NAME", help="Limit output to a single board.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--backend",
        default="real",
        choices=("real", "fake"),
        help="Backend to use. 'fake' is for testing; 'real' opens the SDK.",
    )
    return parser


def _print_text(payload: list[dict[str, Any]]) -> None:
    if not payload:
        print("no DT-Open Layers boards found")
        return
    for row in payload:
        b = row["board"]
        print(
            f"board: {b['name']}  (model={b['model']}, driver={b['driver_name']}, "
            f"instance={b['instance']})"
        )
        if "error" in row:
            print(f"  ! enum_subsystems failed: {row['error']}")
            continue
        for sub in row["subsystems"]:
            print(f"  subsystem {sub['type']} element={sub['element']}")
            for key, value in sub.items():
                if key in {"type", "element"}:
                    continue
                print(f"      {key}: {value}")


def _board_dict(board: BoardInfo) -> dict[str, Any]:
    return {
        "name": board.name,
        "model": board.model,
        "driver_name": board.driver_name,
        "instance": board.instance,
    }


def _subsystem_dict(sub: SubsystemInfo) -> dict[str, Any]:
    data = dataclasses.asdict(sub)
    data["type"] = sub.type.value
    return data


def _resolve_backend(choice: str) -> DtolBackend:
    if choice == "fake":
        from dtollib.testing import make_fake_backend  # noqa: PLC0415

        return make_fake_backend(include_dt9805=True, include_dt9806=True)
    from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

    return DataAcqBackend()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
