"""Replay a ``.dt-raw`` v2 file as a stream of :class:`DaqBlock` instances.

Used for post-hoc analysis of a :class:`RawCountsSink` recording: read the
JSON file header, iterate chunks, decode payloads back into
``(n_channels, samples_per_channel)`` ndarrays with the original dtype.

Design reference: docs/design.md §15.2.
"""

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from dtollib.errors import DtolError, DtolSinkError, ErrorContext
from dtollib.tasks.models import DaqBlock

if TYPE_CHECKING:
    from collections.abc import Iterator


__all__ = ["RawFileHeader", "iter_blocks", "read_file_header"]


_UINT32_BYTES = 4


class RawFileHeader:
    """Parsed file header for a ``.dt-raw`` v2 file."""

    __slots__ = (
        "block_period_ns",
        "channels",
        "device",
        "dtype",
        "format_version",
        "metadata",
        "n_channels",
        "raw",
        "sample_rate_hz",
        "samples_per_buffer",
        "task",
        "task_started_at",
    )

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.format_version: int = int(raw.get("format_version", 0))
        self.task: str | None = raw.get("task")
        self.device: str = raw.get("device", "")
        self.channels: list[dict[str, Any]] = list(raw.get("channels", []))
        self.sample_rate_hz: float | None = raw.get("sample_rate_hz")
        self.block_period_ns: int | None = raw.get("block_period_ns")
        self.dtype: str = raw.get("dtype", "int16")
        self.n_channels: int = int(raw.get("n_channels", len(self.channels)))
        self.samples_per_buffer: int = int(raw.get("samples_per_buffer", 0))
        self.task_started_at = _parse_iso(raw.get("task_started_at", ""))
        self.metadata: dict[str, Any] = dict(raw.get("metadata", {}))


def read_file_header(path: Path | str) -> RawFileHeader:
    """Read the file header from a ``.dt-raw`` v2 file without consuming chunks."""
    p = Path(path)
    with p.open("rb") as fh:
        return RawFileHeader(_read_header(fh))


def iter_blocks(path: Path | str) -> Iterator[DaqBlock]:
    """Yield one :class:`DaqBlock` per chunk in the file.

    Each block's ``data`` is the float64 ndarray reconstructed from the
    stored raw codes (codes / 32768.0 for int16 / int32 — replay-time
    conversion uses the stored channel metadata for proper engineering
    units when downstream code re-applies channel ranges).

    The replay path preserves chunk ``flags`` (``partial``, ``reused``,
    ``overrun_marker``) by setting ``block.error`` accordingly.
    """
    p = Path(path)
    with p.open("rb") as fh:
        header_raw = _read_header(fh)
        header = RawFileHeader(header_raw)
        dtype = np.dtype(header.dtype)
        n_channels = header.n_channels
        samples_per_buffer = header.samples_per_buffer
        sample_size_bytes = dtype.itemsize

        channels_tuple = tuple(ch.get("name") or f"ch{i}" for i, ch in enumerate(header.channels))
        units = {
            (ch.get("name") or f"ch{i}"): ch.get("unit") for i, ch in enumerate(header.channels)
        }

        while True:
            len_bytes = fh.read(_UINT32_BYTES)
            if not len_bytes:
                return
            if len(len_bytes) < _UINT32_BYTES:
                raise DtolSinkError(
                    f"replay_raw: truncated chunk header length in {p}",
                    context=ErrorContext(operation="replay_raw.iter_blocks"),
                )
            (chunk_header_len,) = struct.unpack("<I", len_bytes)
            chunk_header_bytes = fh.read(chunk_header_len)
            if len(chunk_header_bytes) < chunk_header_len:
                raise DtolSinkError(
                    f"replay_raw: truncated chunk header in {p}",
                    context=ErrorContext(operation="replay_raw.iter_blocks"),
                )
            chunk_header = json.loads(chunk_header_bytes.decode("utf-8"))
            valid_samples = int(chunk_header["valid_samples"])
            flags = chunk_header.get("flags", [])
            payload_bytes = fh.read(valid_samples * n_channels * sample_size_bytes)
            if len(payload_bytes) < valid_samples * n_channels * sample_size_bytes:
                raise DtolSinkError(
                    f"replay_raw: truncated chunk payload in {p}",
                    context=ErrorContext(operation="replay_raw.iter_blocks"),
                )
            raw_flat = np.frombuffer(payload_bytes, dtype=dtype)
            # Reshape: writer stored (n_channels, valid_samples) row-major.
            raw_codes = raw_flat.reshape(n_channels, valid_samples).copy()
            data = raw_codes.astype(np.float64, copy=False)
            t_mono_ns = int(chunk_header["t_mono_ns"])
            t_utc = _parse_iso(chunk_header.get("t_utc", "")) or datetime.now(UTC)
            yield DaqBlock(
                device=header.device,
                task=header.task,
                channels=channels_tuple,
                data=data,
                raw_codes=raw_codes,
                block_index=int(chunk_header["seq"]),
                first_sample_index=int(chunk_header.get("first_sample_index", 0)),
                samples_per_channel=valid_samples,
                sample_rate_hz=header.sample_rate_hz,
                block_period_ns=header.block_period_ns,
                task_started_at=header.task_started_at or t_utc,
                t0=t_utc,
                t_mono_ns=t_mono_ns,
                t_utc=t_utc,
                read_started_at=t_utc,
                read_finished_at=t_utc,
                elapsed_s=0.0,
                units=units,
                # RawCountsSink only ever stores raw ADC codes, so a replayed
                # block's ``data`` is codes cast to float — never engineering
                # units. Set this explicitly so downstream consumers don't have
                # to rely on the field's default.
                is_linearised=False,
                error=_flags_to_error(flags, valid_samples, samples_per_buffer),
            )


def _read_header(fh: Any) -> dict[str, Any]:
    """Read + parse the JSON file header."""
    len_bytes = fh.read(_UINT32_BYTES)
    if len(len_bytes) < _UINT32_BYTES:
        raise DtolSinkError(
            "replay_raw: file too short for header length",
            context=ErrorContext(operation="replay_raw.read_file_header"),
        )
    (header_len,) = struct.unpack("<I", len_bytes)
    header_bytes = fh.read(header_len)
    if len(header_bytes) < header_len:
        raise DtolSinkError(
            "replay_raw: truncated file header",
            context=ErrorContext(operation="replay_raw.read_file_header"),
        )
    return json.loads(header_bytes.decode("utf-8"))  # type: ignore[no-any-return]


def _parse_iso(value: str) -> datetime | None:
    """Best-effort ISO-8601 → datetime parse."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _flags_to_error(
    flags: list[str],
    valid_samples: int,
    samples_per_buffer: int,
) -> DtolError | None:
    """Synthesise a sentinel error so consumers can detect chunk flags.

    The error is informational — not a real SDK error from the original
    recording. Production code that wants the raw flags should read them
    off the chunk header via :func:`read_file_header` separately.
    """
    del valid_samples, samples_per_buffer
    if "overrun_marker" in flags:
        from dtollib.errors import DtolBufferOverrunError  # noqa: PLC0415

        return DtolBufferOverrunError(
            "replay_raw: chunk marked overrun_marker",
            context=ErrorContext(operation="replay_raw.iter_blocks"),
        )
    return None
