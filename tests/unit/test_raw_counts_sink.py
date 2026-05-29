"""Tests for :class:`RawCountsSink` and the ``.dt-raw`` replay tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib.sinks.raw_counts import RAW_FORMAT_VERSION, RawCountsSink
from dtollib.tasks.models import DaqBlock
from dtollib.tools.replay_raw import iter_blocks, read_file_header

if TYPE_CHECKING:
    from pathlib import Path

    from dtollib.errors import DtolError


def _make_block(
    *,
    n_channels: int = 2,
    samples_per_channel: int = 4,
    block_index: int = 0,
    first_sample_index: int = 0,
    raw: np.ndarray | None = None,
    error: DtolError | None = None,
) -> DaqBlock:
    now = datetime.now(UTC)
    channels = tuple(f"ch{i}" for i in range(n_channels))
    if raw is None:
        raw = np.arange(n_channels * samples_per_channel, dtype=np.int16).reshape(
            n_channels, samples_per_channel
        )
    return DaqBlock(
        device="dev",
        task="t",
        channels=channels,
        data=raw.astype(np.float64, copy=False),
        raw_codes=raw,
        block_index=block_index,
        first_sample_index=first_sample_index,
        samples_per_channel=samples_per_channel,
        sample_rate_hz=1000.0,
        block_period_ns=1_000_000,
        task_started_at=now,
        t0=now,
        t_mono_ns=10**9,
        t_utc=now,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.0,
        units={f"ch{i}": "V" for i in range(n_channels)},
        error=error,
    )


class TestRawCountsSink:
    @pytest.mark.anyio
    async def test_write_one_block_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "run.dt-raw"
        async with RawCountsSink(path) as sink:
            await sink.write_raw(_make_block())
        assert path.exists()
        assert path.stat().st_size > 0

    @pytest.mark.anyio
    async def test_round_trip_two_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "run.dt-raw"
        blocks_in = [
            _make_block(block_index=0, first_sample_index=0),
            _make_block(
                block_index=1,
                first_sample_index=4,
                raw=np.full((2, 4), 7, dtype=np.int16),
            ),
        ]
        async with RawCountsSink(path) as sink:
            for b in blocks_in:
                await sink.write_raw(b)
        blocks_out = list(iter_blocks(path))
        assert len(blocks_out) == 2
        assert [b.block_index for b in blocks_out] == [0, 1]
        np.testing.assert_array_equal(blocks_out[0].raw_codes, blocks_in[0].raw_codes)
        np.testing.assert_array_equal(blocks_out[1].raw_codes, blocks_in[1].raw_codes)

    @pytest.mark.anyio
    async def test_file_header_has_v2_format(self, tmp_path: Path) -> None:
        path = tmp_path / "run.dt-raw"
        async with RawCountsSink(path) as sink:
            await sink.write_raw(_make_block())
        header = read_file_header(path)
        assert header.format_version == RAW_FORMAT_VERSION
        assert header.device == "dev"
        assert header.n_channels == 2
        assert header.dtype == "int16"

    @pytest.mark.anyio
    async def test_write_without_open_raises(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolSinkError

        sink = RawCountsSink(tmp_path / "run.dt-raw")
        with pytest.raises(DtolSinkError, match="before open"):
            await sink.write_raw(_make_block())

    @pytest.mark.anyio
    async def test_block_without_raw_codes_rejected(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolSinkWriteError

        # Build a block without raw_codes.
        now = datetime.now(UTC)
        block = DaqBlock(
            device="d",
            task="t",
            channels=("ch0",),
            data=np.zeros((1, 4), dtype=np.float64),
            block_index=0,
            first_sample_index=0,
            samples_per_channel=4,
            task_started_at=now,
            t0=now,
            t_mono_ns=0,
            t_utc=now,
            read_started_at=now,
            read_finished_at=now,
            elapsed_s=0.0,
        )
        async with RawCountsSink(tmp_path / "run.dt-raw") as sink:
            with pytest.raises(DtolSinkWriteError, match="no raw_codes"):
                await sink.write_raw(block)


class TestReplayChunkFlags:
    @pytest.mark.anyio
    async def test_overrun_marker_round_trips_as_error(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolBufferOverrunError

        path = tmp_path / "run.dt-raw"
        async with RawCountsSink(path) as sink:
            await sink.write_raw(_make_block())
            await sink.write_raw(
                _make_block(
                    block_index=1,
                    error=DtolBufferOverrunError("test"),
                )
            )
        blocks_out = list(iter_blocks(path))
        assert len(blocks_out) == 2
        assert blocks_out[0].error is None
        assert isinstance(blocks_out[1].error, DtolBufferOverrunError)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
