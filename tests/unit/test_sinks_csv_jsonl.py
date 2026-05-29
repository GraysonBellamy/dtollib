"""Tests for :class:`CsvSink` and :class:`JsonlSink`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import DaqReading
from dtollib.sinks.csv import CsvSink
from dtollib.sinks.jsonl import JsonlSink
from dtollib.tasks.models import DaqBlock

if TYPE_CHECKING:
    from pathlib import Path


def _reading() -> DaqReading:
    now = datetime.now(UTC)
    return DaqReading(
        device="d",
        requested_at=now,
        received_at=now,
        t_utc=now,
        t_mono_ns=1,
        latency_s=0.0,
        values={"ch0": 1.5},
        units={"ch0": "V"},
    )


def _block() -> DaqBlock:
    now = datetime.now(UTC)
    data = np.zeros((1, 2), dtype=np.float64)
    return DaqBlock(
        device="d",
        task="t",
        channels=("ch0",),
        data=data,
        block_index=0,
        first_sample_index=0,
        samples_per_channel=2,
        sample_rate_hz=1000.0,
        block_period_ns=1_000_000,
        task_started_at=now,
        t0=now,
        t_mono_ns=0,
        t_utc=now,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.0,
        units={"ch0": "V"},
    )


class TestCsvSink:
    @pytest.mark.anyio
    async def test_writes_reading(self, tmp_path: Path) -> None:
        path = tmp_path / "run.csv"
        async with CsvSink(path) as sink:
            await sink.write_many([_reading()])
        text = path.read_text()
        assert "device" in text  # header
        assert "1.5" in text  # value

    @pytest.mark.anyio
    async def test_refuses_blocks_by_default(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolSinkError

        async with CsvSink(tmp_path / "x.csv") as sink:
            with pytest.raises(DtolSinkError, match="refuses blocks"):
                await sink.write(_block())

    @pytest.mark.anyio
    async def test_accept_blocks_explodes_per_sample(self, tmp_path: Path) -> None:
        path = tmp_path / "run.csv"
        async with CsvSink(path, accept_blocks=True) as sink:
            await sink.write(_block())
        lines = path.read_text().strip().splitlines()
        # 1 header + 2 sample rows.
        assert len(lines) == 3


class TestJsonlSink:
    @pytest.mark.anyio
    async def test_one_json_per_line(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        async with JsonlSink(path) as sink:
            await sink.write_many([_reading(), _reading()])
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["values"]["ch0"] == 1.5

    @pytest.mark.anyio
    async def test_refuses_blocks_by_default(self, tmp_path: Path) -> None:
        from dtollib.errors import DtolSinkError

        async with JsonlSink(tmp_path / "x.jsonl") as sink:
            with pytest.raises(DtolSinkError, match="refuses blocks"):
                await sink.write(_block())

    @pytest.mark.anyio
    async def test_accept_blocks_explodes_per_sample(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        async with JsonlSink(path, accept_blocks=True) as sink:
            await sink.write(_block())
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            row = json.loads(line)
            assert row["channel"] == "ch0"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
