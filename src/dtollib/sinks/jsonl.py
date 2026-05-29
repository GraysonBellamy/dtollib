"""JSONL sink — one JSON object per :class:`DaqReading` (or per sample for blocks).

Refuses :class:`DaqBlock` by default; ``accept_blocks=True`` enables
``block_to_long_rows`` per-sample explosion.

Design reference: docs/design.md §15.1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from dtollib.errors import DtolSinkError, ErrorContext

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from dtollib.tasks.models import DaqBlock, DaqReading


__all__ = ["JsonlSink"]


class JsonlSink:
    """Write one JSON object per line.

    Args:
        path: Output file path.
        accept_blocks: When ``True``, ``write(block)`` explodes via
            :func:`block_to_long_rows`. Default ``False``.
    """

    def __init__(self, path: Path | str, *, accept_blocks: bool = False) -> None:
        self._path = Path(path)
        self._accept_blocks = accept_blocks
        self._fh: Any = None

    async def open(self) -> None:
        """Open the file for writing."""
        if self._fh is not None:
            return
        self._fh = self._path.open("w", encoding="utf-8")

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append every reading as one JSON object on its own line."""
        if self._fh is None:
            raise DtolSinkError(
                "JsonlSink: write_many before open()",
                context=ErrorContext(operation="JsonlSink.write_many"),
            )
        for reading in items:
            self._fh.write(json.dumps(reading.to_dict(), default=str))
            self._fh.write("\n")

    async def write(self, block: DaqBlock) -> None:
        """Block path — refused by default; explodes per-sample when enabled."""
        if not self._accept_blocks:
            raise DtolSinkError(
                "JsonlSink refuses blocks by default; pass accept_blocks=True",
                context=ErrorContext(operation="JsonlSink.write"),
            )
        if self._fh is None:
            raise DtolSinkError(
                "JsonlSink: write before open()",
                context=ErrorContext(operation="JsonlSink.write"),
            )
        from dtollib.tasks.models import block_to_long_rows  # noqa: PLC0415

        for sample in block_to_long_rows(block):
            self._fh.write(json.dumps(sample.to_dict(), default=str))
            self._fh.write("\n")

    async def close(self) -> None:
        """Flush and close the file. Idempotent."""
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None

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
