"""Tests for :mod:`dtollib.capi.prototypes` declarations.

Real-DLL signature tests run on Windows + SDK in
``tests/binding/test_signatures.py``.  This file uses a pure-Python
fake DLL to assert ``argtypes`` / ``restype`` are set on every
discovery / lifecycle / capability function — cross-platform.
"""

from __future__ import annotations

from typing import Any

from dtollib.capi.prototypes import (
    BUFFER_OLMEM_FUNCTIONS,
    CONTINUOUS_OLDAAPI_FUNCTIONS,
    CORE_OLMEM_FUNCTIONS,
    COUNTER_OLDAAPI_FUNCTIONS,
    DISCOVERY_OLDAAPI_FUNCTIONS,
    MULTI_SENSOR_OLDAAPI_FUNCTIONS,
    OUTPUT_OLDAAPI_FUNCTIONS,
    SINGLE_VALUE_OLDAAPI_FUNCTIONS,
    TEDS_OLDAAPI_FUNCTIONS,
    WAVEFORM_OLMEM_FUNCTIONS,
    declare_oldaapi,
    declare_olmem,
)


class _FakeDllFunction:
    """Stand-in for a ctypes-bound DLL function; tracks ``argtypes``/``restype``."""

    def __init__(self) -> None:
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: object, **kwargs: object) -> int:
        return 0


class _FakeDll:
    """Pure-Python fake of a ``ctypes.WinDLL``."""

    def __init__(self) -> None:
        self._funcs: dict[str, _FakeDllFunction] = {}

    def __getattr__(self, name: str) -> _FakeDllFunction:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._funcs:
            self._funcs[name] = _FakeDllFunction()
        return self._funcs[name]


def test_declare_oldaapi_sets_argtypes_and_restype_on_every_function() -> None:
    fake = _FakeDll()
    declare_oldaapi(fake)  # type: ignore[arg-type]
    all_oldaapi_functions = (
        DISCOVERY_OLDAAPI_FUNCTIONS
        + SINGLE_VALUE_OLDAAPI_FUNCTIONS
        + CONTINUOUS_OLDAAPI_FUNCTIONS
        + OUTPUT_OLDAAPI_FUNCTIONS
        + COUNTER_OLDAAPI_FUNCTIONS
        + MULTI_SENSOR_OLDAAPI_FUNCTIONS
        + TEDS_OLDAAPI_FUNCTIONS
    )
    for name in all_oldaapi_functions:
        fn = getattr(fake, name)
        assert fn.argtypes is not None, f"{name} did not receive an argtypes tuple"
        assert isinstance(fn.argtypes, list)
        assert fn.restype is not None, f"{name} did not receive a restype"


def test_declare_olmem_sets_argtypes_and_restype() -> None:
    fake = _FakeDll()
    declare_olmem(fake)  # type: ignore[arg-type]
    for name in CORE_OLMEM_FUNCTIONS + BUFFER_OLMEM_FUNCTIONS + WAVEFORM_OLMEM_FUNCTIONS:
        fn = getattr(fake, name)
        assert fn.argtypes is not None
        assert fn.restype is not None


def test_declare_oldaapi_idempotent() -> None:
    """Calling declare_oldaapi twice should not raise or change behaviour."""
    fake = _FakeDll()
    declare_oldaapi(fake)  # type: ignore[arg-type]
    declare_oldaapi(fake)  # type: ignore[arg-type]
    # All functions should still be declared.
    for name in DISCOVERY_OLDAAPI_FUNCTIONS:
        assert getattr(fake, name).argtypes is not None
