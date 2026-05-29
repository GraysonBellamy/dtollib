"""Tests for the ``dtol-capture`` CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dtollib.cli import capture

if TYPE_CHECKING:
    from pathlib import Path


class TestCaptureCli:
    def test_dt_raw_path_creates_file(self, tmp_path: Path) -> None:
        out = tmp_path / "run.dt-raw"
        rc = capture.main(
            [
                "--backend",
                "fake",
                "--channels",
                "0,1",
                "--rate",
                "1000",
                "--duration",
                "0.1",
                "--samples-per-buffer",
                "10",
                "--buffers",
                "4",
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_csv_path_creates_file(self, tmp_path: Path) -> None:
        out = tmp_path / "run.csv"
        rc = capture.main(
            [
                "--backend",
                "fake",
                "--channels",
                "0",
                "--rate",
                "1000",
                "--duration",
                "0.05",
                "--samples-per-buffer",
                "5",
                "--buffers",
                "4",
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        # First line is the header.
        lines = out.read_text().strip().splitlines()
        assert len(lines) >= 1
        assert "device" in lines[0] or "channel" in lines[0]

    def test_jsonl_path_creates_file(self, tmp_path: Path) -> None:
        out = tmp_path / "run.jsonl"
        rc = capture.main(
            [
                "--backend",
                "fake",
                "--channels",
                "0",
                "--rate",
                "1000",
                "--duration",
                "0.05",
                "--samples-per-buffer",
                "5",
                "--buffers",
                "4",
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()

    def test_parquet_path_creates_file(self, tmp_path: Path) -> None:
        pytest.importorskip("pyarrow")
        out = tmp_path / "run.parquet"
        rc = capture.main(
            [
                "--backend",
                "fake",
                "--channels",
                "0",
                "--rate",
                "1000",
                "--duration",
                "0.05",
                "--samples-per-buffer",
                "5",
                "--buffers",
                "4",
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        assert out.stat().st_size > 0
