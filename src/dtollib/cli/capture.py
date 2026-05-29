"""``dtol-capture`` — short hardware-clocked acquisition to file.

Default workflow: open a DT9805/DT9806, configure N analog-input voltage
channels at the requested rate, drive ``record()`` for ``--duration``
seconds, and write the resulting :class:`DaqBlock` stream to disk.

Output format is inferred from the path extension:

- ``.dt-raw``  → :class:`RawCountsSink`
- ``.csv``     → :class:`CsvSink` with ``accept_blocks=True``
- ``.jsonl``   → :class:`JsonlSink` with ``accept_blocks=True``
- ``.parquet`` → :class:`ParquetSink` (long-format, one row per sample;
  needs the ``parquet`` extra)

Handles the continuous AI-voltage path; AO writes and sensor types are
not yet supported.

Design reference: docs/design.md §21.2.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dtollib.sinks.base import BlockSink, RawBlockSink

if TYPE_CHECKING:
    import numpy.typing as npt

    from dtollib.backend.fake import FakeDtolBackend


__all__ = ["main"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dtol-capture",
        description=(
            "Capture continuous AI samples from a DT-Open Layers device to "
            "disk. Output format is inferred from the --out file extension."
        ),
    )
    parser.add_argument(
        "--board",
        default=None,
        help="Board name (e.g. DT9805(00)). Default: first discovered.",
    )
    parser.add_argument(
        "--channels",
        default="0",
        help="Comma-separated physical channels (default: 0).",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1000.0,
        help="Sample rate in Hz (default: 1000).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Acquisition duration in seconds (default: 10).",
    )
    parser.add_argument(
        "--samples-per-buffer",
        type=int,
        default=1000,
        help="Samples per HBUF (default: 1000).",
    )
    parser.add_argument(
        "--buffers",
        type=int,
        default=4,
        help="Number of HBUFs in the pool (default: 4; min 3).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output file path. Extension drives sink selection.",
    )
    parser.add_argument(
        "--backend",
        default="real",
        choices=("real", "fake"),
        help="Backend to use. 'fake' is for testing; 'real' opens the SDK.",
    )
    return parser.parse_args(argv)


def _make_sink(path: Path) -> BlockSink | RawBlockSink:
    """Pick a sink based on the output extension."""
    suffix = path.suffix.lower()
    if suffix == ".dt-raw":
        from dtollib.sinks.raw_counts import RawCountsSink  # noqa: PLC0415

        return RawCountsSink(path)
    if suffix == ".csv":
        from dtollib.sinks.csv import CsvSink  # noqa: PLC0415

        return CsvSink(path, accept_blocks=True)
    if suffix == ".jsonl":
        from dtollib.sinks.jsonl import JsonlSink  # noqa: PLC0415

        return JsonlSink(path, accept_blocks=True)
    if suffix == ".parquet":
        from dtollib.sinks.parquet import ParquetSink  # noqa: PLC0415

        return ParquetSink(path)
    raise SystemExit(
        f"dtol-capture: unsupported output extension {suffix!r}; "
        "use one of .dt-raw, .csv, .jsonl, .parquet"
    )


async def _run(args: argparse.Namespace) -> int:
    """Async driver for the CLI."""
    from dtollib import (  # noqa: PLC0415
        AnalogInputVoltage,
        BufferPlan,
        DataFlow,
        TaskSpec,
        Timing,
        open_device,
        record,
    )

    if args.backend == "fake":
        from dtollib.testing import make_fake_backend  # noqa: PLC0415

        backend = make_fake_backend(include_dt9805=True)
        if args.board is None:
            args.board = "DT9805(00)"
    else:
        backend = None  # open_device defaults to DataAcqBackend

    channels = [int(c) for c in args.channels.split(",") if c]
    spec = TaskSpec(
        name="capture",
        board=args.board,
        channels=[AnalogInputVoltage(physical_channel=ch, name=f"ch{ch}") for ch in channels],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=args.rate),
        buffers=BufferPlan(
            buffers=args.buffers,
            samples_per_buffer=args.samples_per_buffer,
        ),
    )

    sink = _make_sink(args.out)
    blocks_seen = 0

    async with (
        await open_device(spec, backend=backend, autostart=False) as session,
        record(session) as recording,
        sink,
    ):
        # FakeBackend test path: synthesise blocks rather than waiting
        # for a real driver to emit them (the fake's driver thread is
        # the test, not a real SDK).
        if args.backend == "fake":
            import numpy as np  # noqa: PLC0415

            hdass = session.raw_hdass
            n_chan = len(channels)
            fill_size = args.samples_per_buffer * n_chan
            n_blocks = max(1, int(args.rate * args.duration / args.samples_per_buffer))
            fake_backend = cast("FakeDtolBackend", backend)
            for _ in range(n_blocks):
                fill = cast("npt.NDArray[Any]", np.zeros(fill_size, dtype=np.int16))
                fake_backend.fire_buffer_done(
                    hdass,
                    fill=fill,
                )

        # Consume the stream until timeout.
        import anyio  # noqa: PLC0415

        with anyio.move_on_after(args.duration):
            async for block in recording.stream:
                if isinstance(sink, RawBlockSink):
                    await sink.write_raw(block)
                else:
                    await sink.write(block)
                blocks_seen += 1

    print(
        f"dtol-capture: wrote {blocks_seen} blocks to {args.out} "
        f"(payloads_emitted={recording.summary.payloads_emitted}, "
        f"overruns_observed={recording.summary.overruns_observed})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point — parses argv, runs the async driver."""
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
