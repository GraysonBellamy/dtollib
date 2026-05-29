"""CSV sink — one row per :class:`DaqReading`, with optional block support.

Refuses :class:`DaqBlock` by default to prevent accidental 1-GB CSVs at
10 kHz × 8 channels. Pass ``accept_blocks=True`` to enable the
``block_to_long_rows`` per-sample explosion.

Design reference: docs/design.md §15.1.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from dtollib.errors import DtolSinkError, ErrorContext

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from dtollib.tasks.models import DaqBlock, DaqReading


__all__ = ["CsvSink"]


class CsvSink:
    """Write :class:`DaqReading` rows (and optionally per-sample block rows) to CSV.

    Args:
        path: Output file path.
        accept_blocks: When ``True``, ``write(block)`` explodes the block
            into per-(channel, sample) rows. Default ``False`` — block
            writes raise :class:`DtolSinkError`.
    """

    def __init__(self, path: Path | str, *, accept_blocks: bool = False) -> None:
        self._path = Path(path)
        self._accept_blocks = accept_blocks
        self._fh: Any = None
        self._writer: Any = None
        self._header_written = False

    async def open(self) -> None:
        """Open the file for writing."""
        if self._fh is not None:
            return
        self._fh = self._path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append every reading as one CSV row."""
        if self._writer is None:
            raise DtolSinkError(
                "CsvSink: write_many before open()",
                context=ErrorContext(operation="CsvSink.write_many"),
            )
        for reading in items:
            row = reading.to_dict()
            self._write_row(row)

    async def write(self, block: DaqBlock) -> None:
        """Block path — refused by default; explodes per-sample when enabled."""
        if not self._accept_blocks:
            raise DtolSinkError(
                "CsvSink refuses blocks by default; pass accept_blocks=True "
                "(see docs/design.md §15.1 — prevents 1-GB CSVs)",
                context=ErrorContext(operation="CsvSink.write"),
            )
        if self._writer is None:
            raise DtolSinkError(
                "CsvSink: write before open()",
                context=ErrorContext(operation="CsvSink.write"),
            )
        from dtollib.tasks.models import block_to_long_rows  # noqa: PLC0415

        for sample in block_to_long_rows(block):
            self._write_row(sample.to_dict())

    def _write_row(self, row: dict[str, Any]) -> None:
        """Write one row, locking the schema on the first call."""
        if not self._header_written:
            self._writer.writerow(list(row.keys()))
            self._header_written = True
        self._writer.writerow([_stringify(v) for v in row.values()])

    async def close(self) -> None:
        """Flush and close the file. Idempotent."""
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
            self._writer = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()


def _stringify(value: Any) -> str:
    """Serialise a row value to a CSV-safe string."""
    if value is None:
        return ""
    if isinstance(value, dict | list):
        import json  # noqa: PLC0415

        return json.dumps(value, default=str)
    return str(value)
