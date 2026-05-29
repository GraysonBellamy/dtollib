"""Tests for the maintainer header-diff tool ``scripts/gen_openlayers.py``.

The script is not part of the importable ``dtollib`` package, so it is loaded
from its file path. Tests feed synthetic header text and assert the diff
output (per docs/implementation-plan.md §3.10).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_openlayers.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_openlayers", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_openlayers"] = module
    spec.loader.exec_module(module)
    return module


gol = _load()


# --- parse_header ----------------------------------------------------------


def test_parse_header_extracts_defines_and_prototypes(tmp_path: Path) -> None:
    header = tmp_path / "OLDAAPI.H"
    header.write_text(
        "#define OLSS_AD 0\n"
        "#define OL_DF_CONTINUOUS  (1100)\n"
        "#define OL_HEX_FLAG 0x0040\n"
        "ECODE WINAPI olDaInitialize(LPSTR lpszName, LPHDEV lphDev);\n"
        "LPSTR WINAPI olDaGetErrorString(ECODE ecode, LPSTR lpszMsg, UINT max);\n",
        encoding="utf-8",
    )
    hc = gol.parse_header(header)
    assert hc.defines["OLSS_AD"] == "0"
    assert hc.defines["OL_DF_CONTINUOUS"] == "1100"
    assert hc.defines["OL_HEX_FLAG"] == "0x0040"
    assert "olDaInitialize" in hc.prototypes
    assert "olDaGetErrorString" in hc.prototypes
    # The args are captured verbatim (sans surrounding parens).
    assert "LPSTR lpszName" in hc.prototypes["olDaInitialize"]


def test_parse_headers_finds_named_headers(tmp_path: Path) -> None:
    (tmp_path / "OLDAAPI.H").write_text("#define OLSS_AD 0\n", encoding="utf-8")
    (tmp_path / "OLMEM.H").write_text("#define OL_X 1\n", encoding="utf-8")
    out = gol.parse_headers(tmp_path)
    assert set(out) >= {"OLDAAPI.H", "OLMEM.H"}
    assert out["OLDAAPI.H"].defines["OLSS_AD"] == "0"


def test_parse_headers_case_insensitive_glob(tmp_path: Path) -> None:
    # Lowercase on a case-sensitive filesystem must still be found via glob.
    (tmp_path / "oldaapi.h").write_text("#define OLSS_AD 0\n", encoding="utf-8")
    out = gol.parse_headers(tmp_path)
    assert "OLDAAPI.H" in out
    assert out["OLDAAPI.H"].defines["OLSS_AD"] == "0"


# --- diff ------------------------------------------------------------------


def _headers(defines: dict[str, str], protos: set[str]) -> dict[str, object]:
    hc = gol.HeaderContents(defines=dict(defines), prototypes=dict.fromkeys(protos, ""))
    return {"OLDAAPI.H": hc}


def test_diff_clean_when_values_match() -> None:
    headers = _headers({"OLSS_AD": "0"}, {"olDaInitialize"})
    const, proto = gol.diff(
        headers,
        binding_constants={"OLSS_AD": 0},
        binding_prototypes=["olDaInitialize"],
    )
    assert const == []
    assert proto == []


def test_diff_flags_value_mismatch() -> None:
    headers = _headers({"OLSS_AD": "0x1234"}, set())
    const, _ = gol.diff(
        headers,
        binding_constants={"OLSS_AD": 0},
        binding_prototypes=[],
    )
    assert len(const) == 1
    assert const[0].startswith("DIFF")
    assert "OLSS_AD" in const[0]


def test_diff_notes_symbolic_define() -> None:
    # A #define whose value is another symbol can't be evaluated → NOTE, not fatal.
    headers = _headers({"OL_ALIAS": "OL_OTHER"}, set())
    const, _ = gol.diff(
        headers,
        binding_constants={"OL_ALIAS": 5},
        binding_prototypes=[],
    )
    assert len(const) == 1
    assert const[0].startswith("NOTE")


def test_diff_flags_missing_prototype() -> None:
    headers = _headers({}, {"olDaInitialize"})
    _, proto = gol.diff(
        headers,
        binding_constants={},
        binding_prototypes=["olDaInitialize", "olDaTerminate"],
    )
    # olDaInitialize is present; olDaTerminate is missing.
    assert len(proto) == 1
    assert proto[0].startswith("MISSING")
    assert "olDaTerminate" in proto[0]


def test_diff_ignores_binding_constant_absent_from_headers() -> None:
    # A binding constant not present in any scanned header is benign (it may
    # live in a header we didn't scan) and must not be flagged.
    headers = _headers({}, set())
    const, _ = gol.diff(
        headers,
        binding_constants={"OL_PRIVATE": 7},
        binding_prototypes=[],
    )
    assert const == []


# --- render_markdown -------------------------------------------------------


def test_render_markdown_tabulates_findings() -> None:
    md = gol.render_markdown(
        ["DIFF  OLSS_AD: binding=0x0 header=0x1"],
        ["MISSING  olDaFoo: not found in any scanned header"],
    )
    lines = md.splitlines()
    assert lines[0].startswith("| Kind | Finding |")
    assert any(row.startswith("| DIFF |") for row in lines)
    assert any(row.startswith("| MISSING |") for row in lines)


# --- main ------------------------------------------------------------------


def test_main_errors_on_non_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert gol.main([str(missing)]) == 2


def test_main_errors_on_empty_directory(tmp_path: Path) -> None:
    # A directory with no recognised headers is a usage error.
    assert gol.main([str(tmp_path)]) == 2


def test_main_check_fails_on_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A header that redefines a real binding constant to a wrong value forces a
    # fatal DIFF; --check (default) must exit non-zero.
    (tmp_path / "OLDAAPI.H").write_text("#define OLSS_AD 0x7777\n", encoding="utf-8")
    rc = gol.main([str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "OLSS_AD" in out


def test_main_report_always_exits_zero(tmp_path: Path) -> None:
    # --report prints findings but never fails the process.
    (tmp_path / "OLDAAPI.H").write_text("#define OLSS_AD 0x7777\n", encoding="utf-8")
    assert gol.main([str(tmp_path), "--report"]) == 0


def test_main_markdown_renders_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "OLDAAPI.H").write_text("#define OLSS_AD 0x7777\n", encoding="utf-8")
    gol.main([str(tmp_path), "--report", "--markdown"])
    out = capsys.readouterr().out
    assert "| Kind | Finding |" in out
