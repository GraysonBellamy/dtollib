"""Tests for the ``dtol-info`` CLI against the fake backend."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dtollib.cli.info import main

if TYPE_CHECKING:
    import pytest


class TestMain:
    def test_text_dump_lists_all_subsystems(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DT9805(00)" in out
        assert "DT9806(00)" in out
        # Full cap dump renders the field names, not just supports_* flags.
        assert "returns_floats" in out
        assert "cgl_depth" in out

    def test_json_dump_parses(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        names = {row["board"]["name"] for row in payload["boards"]}
        assert {"DT9805(00)", "DT9806(00)"} <= names

    def test_single_board_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--board", "DT9806(00)", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["boards"]) == 1
        assert payload["boards"][0]["board"]["name"] == "DT9806(00)"

    def test_unknown_board_exits_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--board", "nope"])
        assert rc == 1
        assert "not found" in capsys.readouterr().err
