"""Tests for the ``dtol-discover`` CLI."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dtollib.cli import discover as discover_cli
from dtollib.errors import DtolDependencyError
from dtollib.testing import make_fake_backend


@pytest.fixture
def stub_real_backend_to_fake() -> object:
    """Patch ``DataAcqBackend`` to return a fake backend instead."""
    backend = make_fake_backend(include_dt9805=True, include_dt9806=True)
    with patch("dtollib.backend.dataacq.DataAcqBackend", return_value=backend):
        yield backend


def test_discover_summary_lists_boards(
    stub_real_backend_to_fake: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = discover_cli.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DT9805(00)" in out
    assert "DT9806(00)" in out


def test_discover_json_output_parses(
    stub_real_backend_to_fake: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = discover_cli.main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [row["board"]["name"] for row in payload["boards"]]
    assert "DT9805(00)" in names
    assert "DT9806(00)" in names


def test_discover_single_board_drills_in(
    stub_real_backend_to_fake: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = discover_cli.main(["--board", "DT9805(00)"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DT9805(00)" in out
    assert "subsystems:" in out


def test_discover_unknown_board_returns_failure(
    stub_real_backend_to_fake: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = discover_cli.main(["--board", "does-not-exist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_discover_empty_backend_message() -> None:
    backend = make_fake_backend()  # no boards
    with patch("dtollib.backend.dataacq.DataAcqBackend", return_value=backend):
        rc = discover_cli.main([])
    assert rc == 0


def test_discover_sdk_load_failure_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the real backend cannot be constructed, exit non-zero."""
    with patch(
        "dtollib.backend.dataacq.DataAcqBackend",
        side_effect=DtolDependencyError("simulated"),
    ):
        rc = discover_cli.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "failed" in err
