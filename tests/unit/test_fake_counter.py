"""``FakeDtolBackend`` counter/timer contract tests — C/T + simultaneous start."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dtollib.capi.constants import (
    OL_CTMODE_COUNT,
    OL_CTMODE_RATE,
    OLSS_AD,
    OLSS_CT,
)
from dtollib.errors import DtolResourceError, DtolTaskStateError
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _open_ct(backend: FakeDtolBackend, board: str = "DT9806(00)") -> tuple[int, int]:
    """Return ``(hdrvr, ct_hdass)`` for the DT9806 counter subsystem."""
    hdrvr = backend.initialize(board)
    hdass = backend.get_dass(hdrvr, OLSS_CT, 0)
    return hdrvr, hdass


def _running_counter(backend: FakeDtolBackend, hdass: int) -> None:
    backend.set_ct_mode(hdass, OL_CTMODE_COUNT)
    backend.commit(hdass)
    backend.start(hdass)


class TestCounterContracts:
    def test_ct_setters_reject_non_counter_subsystem(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdrvr = backend.initialize("DT9806(00)")
        ad = backend.get_dass(hdrvr, OLSS_AD, 0)
        with pytest.raises(DtolTaskStateError, match="non-counter subsystem"):
            backend.set_ct_mode(ad, OL_CTMODE_COUNT)

    def test_gate_before_mode_rejected(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        _hdrvr, hdass = _open_ct(backend)
        with pytest.raises(DtolTaskStateError, match="before set_ct_mode"):
            backend.set_gate_type(hdass, 0)

    def test_mode_then_gate_ok(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        _hdrvr, hdass = _open_ct(backend)
        backend.set_ct_mode(hdass, OL_CTMODE_RATE)
        backend.set_gate_type(hdass, 0)  # no raise
        assert backend.ct_mode_of(hdass) == OL_CTMODE_RATE

    def test_read_events_requires_running(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        _hdrvr, hdass = _open_ct(backend)
        backend.set_ct_mode(hdass, OL_CTMODE_COUNT)
        backend.commit(hdass)
        with pytest.raises(DtolTaskStateError, match="requires a RUNNING counter"):
            backend.read_events(hdass, 0)

    def test_scripted_count_and_frequency(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        _hdrvr, hdass = _open_ct(backend)
        _running_counter(backend, hdass)
        backend.script_count(hdass, 0, 4321)
        backend.script_frequency(hdass, 1, 9999.5)
        assert backend.read_events(hdass, 0) == 4321
        assert backend.measure_frequency(hdass, 1) == 9999.5


class TestSimultaneousStart:
    def test_full_sequence(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdrvr, ct = _open_ct(backend)
        ad = backend.get_dass(hdrvr, OLSS_AD, 0)
        # Both must be committed before joining the SS-list.
        backend.set_ct_mode(ct, OL_CTMODE_COUNT)
        backend.commit(ct)
        backend.set_data_flow(ad, 801)  # OL_DF_SINGLEVALUE
        backend.commit(ad)

        hsslist = backend.get_ss_list(hdrvr)
        backend.put_dass_to_ss_list(hsslist, ct)
        backend.put_dass_to_ss_list(hsslist, ad)
        backend.simultaneous_pre_start(hsslist)
        backend.simultaneous_start(hsslist)
        from dtollib.tasks.models import SubsystemState

        assert backend.state_of(ct) == SubsystemState.RUNNING
        assert backend.state_of(ad) == SubsystemState.RUNNING
        backend.release_ss_list(hsslist)
        with pytest.raises(DtolResourceError, match="unknown/released"):
            backend.put_dass_to_ss_list(hsslist, ct)

    def test_start_before_prestart_rejected(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdrvr, ct = _open_ct(backend)
        hsslist = backend.get_ss_list(hdrvr)
        backend.put_dass_to_ss_list(hsslist, ct)
        with pytest.raises(DtolTaskStateError, match="before simultaneous_pre_start"):
            backend.simultaneous_start(hsslist)

    def test_put_after_prestart_rejected(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdrvr, ct = _open_ct(backend)
        hsslist = backend.get_ss_list(hdrvr)
        backend.put_dass_to_ss_list(hsslist, ct)
        backend.simultaneous_pre_start(hsslist)
        with pytest.raises(DtolTaskStateError, match="after simultaneous_pre_start"):
            backend.put_dass_to_ss_list(hsslist, ct)
