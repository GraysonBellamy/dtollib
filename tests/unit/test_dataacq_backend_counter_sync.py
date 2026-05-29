"""Passthrough tests for :class:`DataAcqBackend` counter/sync methods.

The real backend methods are thin lock-guarded passthroughs to
:class:`OpenLayersApi`; these assert each forwards correctly without loading
the SDK (we bypass ``__init__`` and inject a recording stub API).
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
            if name == "read_events":
                return 4321
            if name == "measure_frequency":
                return 1234.5
            return None

        return _record


def _backend_with_stub() -> tuple[DataAcqBackend, _RecordingApi]:
    backend = DataAcqBackend.__new__(DataAcqBackend)
    api = _RecordingApi()
    mutable_backend = cast("Any", backend)
    mutable_backend._api = api
    mutable_backend._lock = threading.RLock()
    return backend, api


def test_set_ct_mode_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.set_ct_mode(0x10, 1400)
    assert api.calls == [("set_ct_mode", (0x10, 1400))]


def test_set_ct_clock_forwards_source_then_frequency() -> None:
    backend, api = _backend_with_stub()
    backend.set_ct_clock(0x10, rate_hz=1000.0, clock_source=400)
    assert api.calls == [
        ("set_ct_clock_source", (0x10, 400)),
        ("set_ct_clock_frequency", (0x10, 1000.0)),
    ]


def test_set_pulse_forwards_type_then_width() -> None:
    backend, api = _backend_with_stub()
    backend.set_pulse(0x10, pulse_type=1420, duty_or_width=0.5)
    assert api.calls == [
        ("set_pulse_type", (0x10, 1420)),
        ("set_pulse_width", (0x10, 0.5)),
    ]


def test_set_measure_edges_forwards_both() -> None:
    backend, api = _backend_with_stub()
    backend.set_measure_edges(0x10, start_edge=1430, stop_edge=1431)
    assert api.calls == [
        ("set_measure_start_edge", (0x10, 1430)),
        ("set_measure_stop_edge", (0x10, 1431)),
    ]


def test_reads_forward_and_return() -> None:
    backend, api = _backend_with_stub()
    assert backend.read_events(0x10, 0) == 4321
    assert backend.measure_frequency(0x10, 1) == 1234.5
    assert api.calls[0] == ("read_events", (0x10, 0))
    assert api.calls[1] == ("measure_frequency", (0x10, 1))


def test_set_triggered_scan_internal_sets_frequency() -> None:
    backend, api = _backend_with_stub()
    backend.set_triggered_scan(0x10, multiscan_count=4, retrigger_mode=1300, frequency_hz=500.0)
    names = [c[0] for c in api.calls]
    assert names == [
        "set_triggered_scan_usage",
        "set_multiscan_count",
        "set_retrigger_mode",
        "set_retrigger_frequency",
    ]


def test_set_triggered_scan_extra_sets_source() -> None:
    backend, api = _backend_with_stub()
    backend.set_triggered_scan(0x10, multiscan_count=2, retrigger_mode=1302, source=301)
    names = [c[0] for c in api.calls]
    assert names == [
        "set_triggered_scan_usage",
        "set_multiscan_count",
        "set_retrigger_mode",
        "set_retrigger",
    ]


def test_set_cascade_mode_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.set_cascade_mode(0x10, cascade=True)
    assert api.calls == [("set_cascade_mode", (0x10, True))]


def test_simultaneous_start_sequence_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.get_ss_list(0x1)
    backend.put_dass_to_ss_list(0x40, 0x10)
    backend.simultaneous_pre_start(0x40)
    backend.simultaneous_start(0x40)
    backend.release_ss_list(0x40)
    assert [c[0] for c in api.calls] == [
        "get_ss_list",
        "put_dass_to_ss_list",
        "simultaneous_pre_start",
        "simultaneous_start",
        "release_ss_list",
    ]


def test_set_gate_type_forwards() -> None:
    backend, api = _backend_with_stub()
    backend.set_gate_type(0x10, 1410)
    assert api.calls == [("set_gate_type", (0x10, 1410))]
