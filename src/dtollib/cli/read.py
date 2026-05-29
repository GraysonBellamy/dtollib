"""``dtol-read`` — one-shot scalar read from an analog-input channel.

Opens a single-value AI task on one channel, polls once, and prints the
reading.  A thin convenience over :func:`dtollib.open_device` +
:meth:`DtolSession.poll`.

Exit codes:

- ``0`` — read succeeded.
- ``1`` — SDK load / open / poll failed.
- ``2`` — invocation problem (bad ``--range`` etc.).

Design reference: docs/design.md §21.4.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

import anyio

from dtollib.channels.analog_input import AnalogInputVoltage
from dtollib.errors import DtolError
from dtollib.factory import open_device
from dtollib.tasks.spec import TaskSpec

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dtollib.backend.base import DtolBackend

__all__ = ["main", "parse_range"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by the ``dtol-read`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        min_val, max_val = parse_range(args.range)
    except ValueError as exc:
        print(f"dtol-read: {exc}", file=sys.stderr)
        return 2

    try:
        return anyio.run(
            _run,
            args.board,
            args.channel,
            min_val,
            max_val,
            args.gain,
            args.json,
            args.backend,
        )
    except DtolError as exc:
        print(f"dtol-read: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dtol-read",
        description="One-shot scalar read from a DT-Open Layers analog-input channel.",
    )
    parser.add_argument("--board", metavar="NAME", help="Board name (default: first discovered).")
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="Physical channel index to read (default: 0).",
    )
    parser.add_argument(
        "--range",
        default="-10,10",
        metavar="MIN,MAX",
        help="Input voltage range as 'min,max' (default: -10,10).",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=1.0,
        help="Programmable-gain setting (default: 1.0).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--backend",
        default="real",
        choices=("real", "fake"),
        help="Backend to use. 'fake' is for testing; 'real' opens the SDK.",
    )
    return parser


def parse_range(text: str) -> tuple[float, float]:
    """Parse a CLI ``MIN,MAX`` voltage range."""
    parts = text.split(",")
    if len(parts) != 2:  # noqa: PLR2004 - exactly one comma expected
        raise ValueError(f"--range must be 'min,max', got {text!r}")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ValueError(f"--range values must be numbers, got {text!r}") from exc
    if lo >= hi:
        raise ValueError(f"--range min ({lo}) must be less than max ({hi})")
    return lo, hi


async def _run(
    board: str | None,
    channel: int,
    min_val: float,
    max_val: float,
    gain: float,
    json_out: bool,
    backend_choice: str,
) -> int:
    backend: DtolBackend | None = _resolve_backend(backend_choice)
    spec = TaskSpec(
        name="dtol-read",
        board=board,
        channels=[
            AnalogInputVoltage(
                physical_channel=channel,
                min_val=min_val,
                max_val=max_val,
                gain=gain,
            )
        ],
    )
    async with await open_device(spec, backend=backend) as session:
        reading = await session.poll()

    name = next(iter(reading.values), f"ch{channel}")
    value = reading.values.get(name)
    unit = reading.units.get(name)
    if json_out:
        print(
            json.dumps(
                {
                    "board": reading.device,
                    "channel": channel,
                    "name": name,
                    "value": value,
                    "unit": unit,
                    "t_utc": reading.t_utc.isoformat(),
                },
                indent=2,
            )
        )
    else:
        unit_str = f" {unit}" if unit else ""
        print(f"{reading.device}  {name} = {value}{unit_str}")
    return 0


def _resolve_backend(choice: str) -> DtolBackend | None:
    if choice == "fake":
        from dtollib.testing import make_fake_backend  # noqa: PLC0415

        return make_fake_backend(include_dt9805=True)
    return None  # open_device defaults to DataAcqBackend


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
