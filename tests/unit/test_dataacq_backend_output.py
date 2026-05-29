"""Passthrough tests for :class:`DataAcqBackend` output methods.

The real backend methods are thin lock-guarded passthroughs to
:class:`OpenLayersApi`; these assert each forwards its arguments verbatim
without loading the SDK (we bypass ``__init__`` and inject a stub API).
"""

from __future__ import annotations

import threading
from typing import Any, cast

from dtollib.backend.dataacq import DataAcqBackend


class _RecordingApi:
    """Stub OpenLayersApi that records calls instead of touching the SDK."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str):
        def _record(*args: Any) -> Any:
            self.calls.append((name, args))
            if name == "copy_buffer":
                return b"\x01\x02"
            return None

        return _record


def _backend_with_stub() -> tuple[DataAcqBackend, _RecordingApi]:
    backend = DataAcqBackend.__new__(DataAcqBackend)
    api = _RecordingApi()
    mutable_backend = cast("Any", backend)
    mutable_backend._api = api
    mutable_backend._lock = threading.RLock()
    return backend, api


def test_put_single_value_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.put_single_value(0x10, 1, 32768, 2.0)
    assert api.calls == [("put_single_value", (0x10, 1, 32768, 2.0))]


def test_put_single_values_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.put_single_values(0x10, [1, 2], 1.0)
    assert api.calls == [("put_single_values", (0x10, [1, 2], 1.0))]


def test_mute_unmute_forward() -> None:
    backend, api = _backend_with_stub()
    backend.mute(0x10)
    backend.unmute(0x10)
    assert api.calls == [("mute", (0x10,)), ("unmute", (0x10,))]


def test_digital_io_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.set_synchronous_digital_io_usage(0x10, True)
    backend.set_digital_io_list_entry(0x10, 0, 5)
    assert api.calls == [
        ("set_synchronous_digital_io_usage", (0x10, True)),
        ("set_digital_io_list_entry", (0x10, 0, 5)),
    ]


def test_copy_to_and_from_buffer_forward() -> None:
    backend, api = _backend_with_stub()
    backend.copy_to_buffer(0x80, b"\x01\x02", 1)
    out = backend.copy_buffer(0x80, 1, 2)
    assert out == b"\x01\x02"
    assert api.calls[0] == ("copy_to_buffer", (0x80, b"\x01\x02", 1))
    assert api.calls[1] == ("copy_buffer", (0x80, 1, 2))
