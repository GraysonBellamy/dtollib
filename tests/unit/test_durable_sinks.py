"""Tests for the durable tabular sinks and the row/pipe helpers.

Covers :func:`reading_to_row` / :func:`block_to_rows` / :func:`pipe` /
:func:`pipe_blocks`, a real :class:`SqliteSink` and :class:`ParquetSink`
round-trip, and :class:`PostgresConfig` validation + credential scrubbing
(no live database required).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import DaqReading
from dtollib.sinks import (
    ParquetSink,
    PostgresConfig,
    PostgresSink,
    SqliteSink,
    block_to_rows,
    pipe,
    pipe_blocks,
    reading_to_row,
)
from dtollib.tasks.models import DaqBlock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path
    from types import TracebackType
    from typing import Self


def _reading(*, ch0: float = 1.5, t_mono_ns: int = 1) -> DaqReading:
    now = datetime.now(UTC)
    return DaqReading(
        device="dev",
        task="task",
        requested_at=now,
        received_at=now,
        t_utc=now,
        t_mono_ns=t_mono_ns,
        latency_s=0.0,
        values={"ch0": ch0},
        units={"ch0": "V"},
    )


def _block(*, n_samples: int = 3) -> DaqBlock:
    now = datetime.now(UTC)
    data = np.arange(n_samples, dtype=np.float64).reshape(1, n_samples)
    return DaqBlock(
        device="dev",
        task="task",
        channels=("ch0",),
        data=data,
        block_index=2,
        first_sample_index=10,
        samples_per_channel=n_samples,
        sample_rate_hz=1000.0,
        block_period_ns=1_000_000,
        task_started_at=now,
        t0=now,
        t_mono_ns=5_000,
        t_utc=now,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.0,
        units={"ch0": "V"},
    )


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


class TestRowHelpers:
    def test_reading_to_row_flattens_channels_and_units(self) -> None:
        row = reading_to_row(_reading())
        assert row["device"] == "dev"
        assert row["task"] == "task"
        assert row["ch0"] == 1.5
        assert row["ch0_unit"] == "V"
        assert row["error_type"] is None
        assert row["error_message"] is None

    def test_block_to_rows_one_row_per_sample(self) -> None:
        rows = block_to_rows(_block(n_samples=3))
        assert len(rows) == 3
        # absolute sample index = first_sample_index + k
        assert [r["sample_index"] for r in rows] == [10, 11, 12]
        assert [r["value"] for r in rows] == [0.0, 1.0, 2.0]
        # per-sample monotonic time advances by block_period_ns
        assert [r["t_mono_ns"] for r in rows] == [5_000, 1_005_000, 2_005_000]
        assert all(r["channel"] == "ch0" for r in rows)
        assert all(r["unit"] == "V" for r in rows)


# ---------------------------------------------------------------------------
# Pipe drivers
# ---------------------------------------------------------------------------


class _RecordingReadingSink:
    """Minimal :class:`ReadingSink` that records per-flush batch sizes."""

    def __init__(self) -> None:
        self.batches: list[int] = []

    async def open(self) -> None: ...

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        self.batches.append(len(items))

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class _RecordingBlockSink:
    """Minimal :class:`BlockSink` that counts block writes."""

    def __init__(self) -> None:
        self.count = 0

    async def open(self) -> None: ...

    async def write(self, block: DaqBlock) -> None:
        del block
        self.count += 1

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


async def _readings_stream(n: int) -> AsyncIterator[DaqReading]:
    for i in range(n):
        yield _reading(t_mono_ns=i)


async def _block_stream(n: int) -> AsyncIterator[DaqBlock]:
    for _ in range(n):
        yield _block()


class TestPipeDrivers:
    @pytest.mark.anyio
    async def test_pipe_batches_by_size(self) -> None:
        sink = _RecordingReadingSink()
        emitted = await pipe(_readings_stream(10), sink, batch_size=4, flush_interval_s=1000.0)
        assert emitted == 10
        # 4 + 4 + 2 (final flush)
        assert sink.batches == [4, 4, 2]

    @pytest.mark.anyio
    async def test_pipe_rejects_bad_args(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            await pipe(_readings_stream(1), _RecordingReadingSink(), batch_size=0)

    @pytest.mark.anyio
    async def test_pipe_blocks_one_write_per_block(self) -> None:
        sink = _RecordingBlockSink()
        emitted = await pipe_blocks(_block_stream(3), sink)
        assert emitted == 3
        assert sink.count == 3


# ---------------------------------------------------------------------------
# SqliteSink
# ---------------------------------------------------------------------------


class TestSqliteSink:
    @pytest.mark.anyio
    async def test_round_trip_readings_and_blocks(self, tmp_path: Path) -> None:
        db = tmp_path / "out.db"
        async with SqliteSink(db) as sink:
            await sink.write_many([_reading(ch0=1.0), _reading(ch0=2.0)])
            await sink.write(_block())

        conn = sqlite3.connect(str(db))
        try:
            readings = conn.execute("SELECT ch0 FROM readings ORDER BY ch0").fetchall()
            blocks = conn.execute("SELECT block_index, samples_per_channel FROM blocks").fetchall()
        finally:
            conn.close()
        assert [r[0] for r in readings] == [1.0, 2.0]
        assert blocks == [(2, 3)]

    def test_rejects_bad_table_name(self) -> None:
        with pytest.raises(ValueError, match="table_readings"):
            SqliteSink("x.db", table_readings="bad name")

    @pytest.mark.anyio
    async def test_write_before_open_raises(self) -> None:
        with pytest.raises(RuntimeError, match="before open"):
            await SqliteSink("x.db").write_many([_reading()])


# ---------------------------------------------------------------------------
# ParquetSink
# ---------------------------------------------------------------------------


class TestParquetSink:
    @pytest.mark.anyio
    async def test_block_round_trip(self, tmp_path: Path) -> None:
        pq = pytest.importorskip("pyarrow.parquet")
        out = tmp_path / "out.parquet"
        async with ParquetSink(out) as sink:
            await sink.write(_block(n_samples=4))
        table = pq.read_table(str(out))
        assert table.num_rows == 4
        assert table.column("value").to_pylist() == [0.0, 1.0, 2.0, 3.0]

    @pytest.mark.anyio
    async def test_mixing_shapes_raises(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolSinkSchemaError

        pytest.importorskip("pyarrow")
        async with ParquetSink(tmp_path / "out.parquet") as sink:
            await sink.write_many([_reading()])
            with pytest.raises(DtolSinkSchemaError, match="cannot mix"):
                await sink.write(_block())


# ---------------------------------------------------------------------------
# PostgresSink / PostgresConfig (no live DB)
# ---------------------------------------------------------------------------


class TestPostgresConfig:
    def test_requires_dsn_or_host(self) -> None:
        with pytest.raises(ValueError, match="either `dsn` or `host`"):
            PostgresConfig()

    def test_dsn_and_host_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            PostgresConfig(dsn="postgres://x/y", host="h")

    def test_target_scrubs_password(self) -> None:
        cfg = PostgresConfig(dsn="postgres://user:secret@host:5432/db")
        target = cfg.target()
        assert "secret" not in target
        assert "host" in target
        assert "db" in target

    def test_bad_table_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="table_blocks"):
            PostgresConfig(host="h", table_blocks="bad name")

    @pytest.mark.anyio
    async def test_write_before_open_raises(self) -> None:
        sink = PostgresSink(PostgresConfig(host="h", database="d", user="u"))
        with pytest.raises(RuntimeError, match="before open"):
            await sink.write_many([_reading()])
