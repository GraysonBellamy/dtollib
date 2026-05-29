""":class:`RawCountsSink` — TDMS-equivalent durable raw-data logger.

The single sink unique to ``dtollib``. Writes the raw int16/int32 buffer
data (not the volt-converted floats) plus a JSON file header and a
per-chunk JSON record header to a ``.dt-raw`` v2 file.

Why a custom format instead of TDMS: no third-party dependency, the
format is dead-simple to read in NumPy / MATLAB / any tool
(``np.fromfile``), and it faithfully preserves what the SDK gave us.

Threading: writes from the drainer thread (the §12.3.2 bridge attaches
this sink BEFORE the async stream). Consumer back-pressure does not stop
the file from growing.

Design reference: docs/design.md §15.2.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import numpy as np

from dtollib.errors import (
    DtolSinkError,
    DtolSinkWriteError,
    ErrorContext,
)

if TYPE_CHECKING:
    from types import TracebackType

    from dtollib.tasks.models import DaqBlock


__all__ = ["RawCountsSink"]


RAW_FORMAT_VERSION: int = 2
"""``.dt-raw`` format version — bumped from v1 for per-chunk framing."""


class RawCountsSink:
    """Writes :class:`DaqBlock.raw_codes` to a ``.dt-raw`` v2 file.

    File layout (per docs/design.md §15.2)::

        file_header_len:uint32
        + file_header_json:bytes
        + (chunk_record)*

        chunk_record = chunk_header_len:uint32
                     + chunk_header_json:bytes
                     + chunk_payload:bytes

    The sink writes synchronously from whatever thread calls ``write_raw``
    — the §12.3.2 callback bridge attaches this sink as a passive
    observer that runs from the drainer thread, so consumer slowness on
    the async path does not slow the file growing.

    Args:
        path: Output file path. Created with ``mode="wb"``. Overwritten
            if it already exists.
        file_metadata: Optional dict merged into the file header JSON.
            Sink adds ``format_version``, ``dtype`` (inferred from the
            first block's ``raw_codes`` dtype), and bookkeeping.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        file_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._path = Path(path)
        self._metadata = dict(file_metadata or {})
        self._fh: Any = None
        self._seq: int = 0
        self._header_written: bool = False
        self._dtype: str | None = None
        self._closed: bool = False

    @property
    def path(self) -> Path:
        """The output file path."""
        return self._path

    async def open(self) -> None:
        """Open the file for binary writing. Idempotent."""
        if self._fh is not None:
            return
        try:
            self._fh = self._path.open("wb")
        except OSError as exc:
            raise DtolSinkError(
                f"RawCountsSink: failed to open {self._path}: {exc}",
                context=ErrorContext(operation="RawCountsSink.open"),
            ) from exc
        self._closed = False

    async def write_raw(self, block: DaqBlock) -> None:
        """Append one block's raw-counts payload to the file.

        Lazily writes the file header on the first call (when the dtype
        is known from the block).
        """
        if self._fh is None:
            raise DtolSinkError(
                "RawCountsSink: write_raw before open()",
                context=ErrorContext(operation="RawCountsSink.write_raw"),
            )
        codes = block.raw_codes
        if codes is None:
            raise DtolSinkWriteError(
                "RawCountsSink: block has no raw_codes (RawLogging not configured?)",
                context=ErrorContext(operation="RawCountsSink.write_raw"),
            )
        if not self._header_written:
            self._dtype = str(codes.dtype)
            self._write_file_header(block, codes)
            self._header_written = True

        flags: list[str] = []
        if block.error is not None:
            flags.append("overrun_marker")
        if block.samples_per_channel < codes.shape[1]:
            flags.append("partial")

        chunk_header = {
            "seq": self._seq,
            "event_kind": "buffer_done" if not flags else "buffer_done_with_flags",
            "first_sample_index": block.first_sample_index,
            "valid_samples": int(block.samples_per_channel),
            "buffer_capacity": int(codes.shape[1]),
            "t_mono_ns": int(block.t_mono_ns),
            "t_utc": block.t_utc.isoformat(),
            "flags": flags,
        }
        header_bytes = json.dumps(chunk_header).encode("utf-8")
        self._fh.write(struct.pack("<I", len(header_bytes)))
        self._fh.write(header_bytes)
        # Payload: ascontiguousarray so .tobytes() works regardless of
        # the source view's stride.  Only write the VALID samples — the
        # buffer's full capacity may be larger.
        payload = np.ascontiguousarray(codes[:, : block.samples_per_channel]).tobytes()
        self._fh.write(payload)
        self._fh.flush()
        self._seq += 1

    def _write_file_header(self, block: DaqBlock, codes: np.ndarray) -> None:
        """Write the one-time file header."""
        header: dict[str, Any] = {
            "format_version": RAW_FORMAT_VERSION,
            "task": block.task,
            "device": block.device,
            "channels": [{"name": name, "unit": block.units.get(name)} for name in block.channels],
            "sample_rate_hz": block.sample_rate_hz,
            "block_period_ns": block.block_period_ns,
            "dtype": str(codes.dtype),
            "n_channels": len(block.channels),
            "samples_per_buffer": int(codes.shape[1]),
            "task_started_at": block.task_started_at.isoformat(),
            "metadata": self._metadata,
        }
        header_bytes = json.dumps(header).encode("utf-8")
        self._fh.write(struct.pack("<I", len(header_bytes)))
        self._fh.write(header_bytes)

    async def close(self) -> None:
        """Flush and close the file. Idempotent."""
        if self._fh is not None and not self._closed:
            self._fh.flush()
            self._fh.close()
            self._closed = True

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
