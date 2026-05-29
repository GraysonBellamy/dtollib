"""Tests for the §11.5 callback typedef hazard.

The regression assertion: every SDK callback declared in
:mod:`dtollib.capi.types` uses :func:`ctypes.WINFUNCTYPE` (stdcall;
falling back to :func:`ctypes.CFUNCTYPE` for cross-platform import)
and pointer-sized ``WPARAM`` / ``LPARAM`` from
:mod:`ctypes.wintypes`, NOT 32-bit ``c_long`` or ``c_uint``.

This is the docs/design.md §11.5 hazard made into a test.  If a
future refactor accidentally swaps the typedef to ``CFUNCTYPE`` with
``c_long`` arguments on 64-bit Windows, this test fails *before* it
manifests as silent data corruption on a live acquisition run.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

import pytest

from dtollib.capi.callbacks import (
    BOARD_ENUM_EX_PROC,
    BOARD_ENUM_PROC,
    CHAN_CAP_ENUM_PROC,
    SS_CAP_ENUM_PROC,
    SS_ENUM_PROC,
    SdkEventKind,
    event_kind_from_message,
)

_CALLBACK_TYPES = [
    BOARD_ENUM_PROC,
    BOARD_ENUM_EX_PROC,
    SS_ENUM_PROC,
    SS_CAP_ENUM_PROC,
    CHAN_CAP_ENUM_PROC,
]


@pytest.mark.parametrize("cb_type", _CALLBACK_TYPES)
def test_callback_uses_correct_calling_convention(cb_type: Any) -> None:
    """All callbacks must use the platform-appropriate function-pointer base."""
    if sys.platform == "win32":
        expected = ctypes.WINFUNCTYPE(ctypes.c_int)._flags_
    else:
        expected = ctypes.CFUNCTYPE(ctypes.c_int)._flags_
    assert cb_type._flags_ == expected


@pytest.mark.skipif(sys.platform != "win32", reason="WNDPROC type only defined on Windows")
def test_message_window_wndproc_signature_shape() -> None:
    """The message-window ``WNDPROC`` must take ``(HWND, UINT, WPARAM, LPARAM)``
    with pointer-sized ``WPARAM`` / ``LPARAM`` and return pointer-sized ``LRESULT``.

    Buffer-done events arrive as window messages (``olDaSetWndHandle``), so the
    §11.5 truncation hazard now lives on this trampoline: a 32-bit ``c_long``
    for ``LPARAM`` on x64 would silently truncate the buffer handle the SDK
    passes in ``lParam``.
    """
    from dtollib.backend._message_window import _WNDPROCTYPE  # pyright: ignore[reportPrivateUsage]

    argtypes = _WNDPROCTYPE._argtypes_
    assert len(argtypes) == 4
    # HWND, UINT, WPARAM, LPARAM — args 0 (HWND), 2 (WPARAM), 3 (LPARAM) are
    # pointer-sized; arg 1 (UINT message id) is 32-bit by definition.
    for idx in (0, 2, 3):
        assert ctypes.sizeof(argtypes[idx]) == ctypes.sizeof(ctypes.c_void_p), (
            f"WNDPROC arg {idx} ({argtypes[idx]}) is not pointer-sized"
        )
    restype = _WNDPROCTYPE._restype_
    assert restype is not None
    assert ctypes.sizeof(restype) == ctypes.sizeof(ctypes.c_void_p)


def test_board_enum_proc_lparam_is_pointer_sized() -> None:
    """``BOARD_ENUM_PROC`` last arg (``LPARAM``) must be pointer-sized."""
    argtypes = BOARD_ENUM_PROC._argtypes_
    assert ctypes.sizeof(argtypes[-1]) == ctypes.sizeof(ctypes.c_void_p)


def test_ss_cap_enum_proc_signature_shape() -> None:
    """``SS_CAP_ENUM_PROC`` must mirror OLDAAPI.H ``CAPSPROC``.

    Signature: ``BOOL(UINT uiEnumCap, DBL dParam1, DBL dParam2, LPARAM)``.
    The pre-2026-05-28 typedef declared ``(c_ulong value, LPARAM)`` — two
    args — so the callback appended ``uiEnumCap`` (the cap ID, 101/102)
    instead of the value and mismatched the stack (gains read back as
    ``[102, 102, 102, 102]``, ranges as ``[101]``).
    """
    argtypes = SS_CAP_ENUM_PROC._argtypes_
    assert len(argtypes) == 4
    assert argtypes[0] is ctypes.c_uint
    assert argtypes[1] is ctypes.c_double
    assert argtypes[2] is ctypes.c_double
    assert ctypes.sizeof(argtypes[3]) == ctypes.sizeof(ctypes.c_void_p)


def test_chan_cap_enum_proc_signature_shape() -> None:
    """``CHAN_CAP_ENUM_PROC`` must mirror OLDAAPI.H ``CHANNELCAPSPROC``.

    Signature: ``BOOL(UINT uiEnumCap, UINT uParam, DBL dParam, LPARAM)``.
    """
    argtypes = CHAN_CAP_ENUM_PROC._argtypes_
    assert len(argtypes) == 4
    assert argtypes[0] is ctypes.c_uint
    assert argtypes[1] is ctypes.c_uint
    assert argtypes[2] is ctypes.c_double
    assert ctypes.sizeof(argtypes[3]) == ctypes.sizeof(ctypes.c_void_p)


def test_event_kind_round_trip() -> None:
    """Every :class:`SdkEventKind` round-trips through :func:`event_kind_from_message`."""
    for kind in SdkEventKind:
        assert event_kind_from_message(int(kind)) is kind


def test_event_kind_from_unknown_message_returns_none() -> None:
    """Unknown SDK message IDs map to ``None`` (not an exception)."""
    assert event_kind_from_message(0xFFFF) is None
