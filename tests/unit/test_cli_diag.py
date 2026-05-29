"""Tests for the ``dtol-diag`` CLI.

The CLI talks to a real backend in production; here we exercise the
output-shaping logic by stubbing the SDK-loading helpers so the test
can run without a real SDK install.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dtollib.cli import diag as diag_cli
from dtollib.errors import DtolDependencyError


@pytest.fixture
def stub_sdk_unavailable() -> object:
    """Patch ``load_openlayers`` to raise ``DtolDependencyError``."""
    with patch(
        "dtollib.capi.loader.load_openlayers",
        side_effect=DtolDependencyError("SDK not found (test stub)"),
    ):
        yield


def test_diag_reports_sdk_failure(
    stub_sdk_unavailable: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = diag_cli.main(["sdk"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "sdk" in out


def test_diag_json_envelope(
    stub_sdk_unavailable: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = diag_cli.main(["--json", "sdk"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(s["section"] == "sdk" for s in payload["sections"])
    sdk_section = next(s for s in payload["sections"] if s["section"] == "sdk")
    assert sdk_section["ok"] is False
    assert "error" in sdk_section["detail"]


def test_diag_unknown_subcommand_exits_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        diag_cli.main(["bogus-section"])
    assert exc_info.value.code == 2
