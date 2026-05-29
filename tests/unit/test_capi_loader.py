"""Cross-platform unit tests for :mod:`dtollib.capi.loader`.

Real-DLL loading is exercised in ``tests/binding/test_loader.py``
(Windows + SDK only).  This file covers:

- Non-Windows platforms raise ``DtolDependencyError`` immediately.
- ``default_oldaapi_paths`` / ``default_olmem_paths`` honour bitness.
- ``OpenLayersDlls`` is frozen.
- Failure messages enumerate every candidate path.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from dtollib.capi.loader import (
    OpenLayersDlls,
    default_oldaapi_paths,
    default_olmem_paths,
    load_openlayers,
    python_bitness,
)
from dtollib.errors import DtolDependencyError


class TestDefaultPaths:
    def test_default_oldaapi_paths_64bit(self) -> None:
        paths = default_oldaapi_paths(bitness=64)
        assert len(paths) == 1
        assert paths[0].name == "oldaapi64.dll"
        assert "System32" in str(paths[0])

    def test_default_oldaapi_paths_32bit(self) -> None:
        paths = default_oldaapi_paths(bitness=32)
        assert len(paths) == 1
        assert paths[0].name == "oldaapi32.dll"
        assert "SysWOW64" in str(paths[0])

    def test_default_olmem_paths_64bit(self) -> None:
        paths = default_olmem_paths(bitness=64)
        assert paths[0].name == "olmem64.dll"

    def test_default_olmem_paths_32bit(self) -> None:
        paths = default_olmem_paths(bitness=32)
        assert paths[0].name == "olmem32.dll"

    def test_python_bitness_is_32_or_64(self) -> None:
        assert python_bitness() in (32, 64)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour test")
class TestNonWindowsBehaviour:
    def test_load_raises_dependency_error(self) -> None:
        with pytest.raises(DtolDependencyError) as exc_info:
            load_openlayers()
        msg = str(exc_info.value)
        assert "Windows" in msg or "Windows-only" in msg


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only failure-mode test")
class TestWindowsFailureModes:
    def test_explicit_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(DtolDependencyError) as exc_info:
            load_openlayers(
                oldaapi_path=tmp_path / "does_not_exist_oldaapi64.dll",
                olmem_path=tmp_path / "does_not_exist_olmem64.dll",
            )
        msg = str(exc_info.value)
        assert "does_not_exist_oldaapi64.dll" in msg

    def test_bitness_mismatch_pre_check(self, tmp_path: Path) -> None:
        wrong_bitness = "oldaapi32.dll" if python_bitness() == 64 else "oldaapi64.dll"
        sentinel = tmp_path / wrong_bitness
        sentinel.write_bytes(b"not a dll")
        with pytest.raises(DtolDependencyError) as exc_info:
            load_openlayers(oldaapi_path=sentinel)
        assert "bitness mismatch" in str(exc_info.value).lower()


class TestOpenLayersDllsFrozen:
    def test_open_layers_dlls_is_frozen(self) -> None:
        dlls = OpenLayersDlls(
            oldaapi=None,  # type: ignore[arg-type]
            olmem=None,  # type: ignore[arg-type]
            oldaapi_path=Path("a.dll"),
            olmem_path=Path("b.dll"),
            bitness=64,
        )
        with pytest.raises(FrozenInstanceError):
            dlls.bitness = 32  # type: ignore[misc]


class TestEnvOverride:
    def test_env_var_takes_precedence_over_default(self, tmp_path: Path) -> None:
        # We only verify the failure message names the env-var path —
        # actually loading the DLL is covered by binding tests.
        if sys.platform != "win32":
            pytest.skip("env-var behaviour test only meaningful on Windows")
        bogus = tmp_path / "env_set_path.dll"
        with patch.dict("os.environ", {"DTOLLIB_OLDAAPI_DLL": str(bogus)}, clear=False):
            with pytest.raises(DtolDependencyError) as exc_info:
                load_openlayers()
            assert str(bogus) in str(exc_info.value)
