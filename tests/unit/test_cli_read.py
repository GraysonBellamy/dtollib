"""Tests for the ``dtol-read`` CLI against the fake backend."""

from __future__ import annotations

import json

import pytest

from dtollib.cli.read import main, parse_range


class TestParseRange:
    def test_valid(self) -> None:
        assert parse_range("-10,10") == (-10.0, 10.0)

    def test_wrong_arity(self) -> None:
        with pytest.raises(ValueError, match="min,max"):
            parse_range("1,2,3")

    def test_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="numbers"):
            parse_range("a,b")

    def test_min_not_less_than_max(self) -> None:
        with pytest.raises(ValueError, match="less than"):
            parse_range("5,5")


class TestMain:
    def test_text_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--channel", "0"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ch0 =" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--channel", "0", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["channel"] == 0
        assert payload["name"] == "ch0"
        assert "value" in payload

    def test_bad_range_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--backend", "fake", "--range", "10,1"])
        assert rc == 2
        assert "less than" in capsys.readouterr().err
