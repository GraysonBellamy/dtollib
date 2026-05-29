"""Tests for :class:`dtollib.backend.fake.FakeDtolBackend`.

The fake is the contract for what the real backend must do. These
tests pin its behaviour so dependent code can rely on it.
"""

from __future__ import annotations

import pytest

from dtollib.backend.fake import FakeDtolBackend
from dtollib.capi.constants import OLSS_AD, OLSS_DA
from dtollib.errors import (
    DtolBackendError,
    DtolBufferOverrunError,
    DtolResourceError,
)
from dtollib.testing import (
    make_fake_backend,
)


class TestEnumeration:
    def test_empty_backend_enumerates_no_boards(self) -> None:
        backend = FakeDtolBackend()
        assert backend.enum_boards() == []

    def test_dt9805_helper_exposes_one_ai_subsystem(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        boards = backend.enum_boards()
        assert [b.name for b in boards] == ["DT9805(00)"]
        assert boards[0].model == "DT9805"
        assert boards[0].driver_name == "OLDT9805"

        subs = backend.enum_subsystems("DT9805(00)")
        assert len(subs) == 1
        # Bench-verified real model: raw-code TC front-end, not a firmware
        # multi-sensor float board (see dtollib.testing.make_dt9805_capabilities).
        assert subs[0].num_channels == 17
        assert subs[0].supports_multisensor is False
        assert subs[0].returns_floats is False

    def test_dt9806_helper_exposes_all_subsystems(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        subs = backend.enum_subsystems("DT9806(00)")
        types = [s.type.value for s in subs]
        assert "analog_input" in types
        assert "analog_output" in types
        assert "digital_input" in types
        assert "digital_output" in types
        assert "counter_timer" in types

    def test_unknown_board_raises_backend_error(self) -> None:
        backend = FakeDtolBackend()
        with pytest.raises(DtolBackendError):
            backend.enum_subsystems("does-not-exist")


class TestLifecycle:
    def test_initialize_terminate_round_trip(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        hdrvr = backend.initialize("DT9805(00)")
        assert isinstance(hdrvr, int)
        assert hdrvr > 0
        backend.terminate(hdrvr)

    def test_terminate_unknown_hdrvr_raises(self) -> None:
        backend = FakeDtolBackend()
        with pytest.raises(DtolResourceError):
            backend.terminate(0xDEAD)

    def test_refcount_keeps_handle_open_until_final_terminate(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        h1 = backend.initialize("DT9805(00)")
        h2 = backend.initialize("DT9805(00)")
        # Same HDRVR returned for both calls.
        assert h1 == h2
        # First terminate decrements; handle still open.
        backend.terminate(h1)
        # Re-initialize gives same handle.
        h3 = backend.initialize("DT9805(00)")
        assert h3 == h1
        backend.terminate(h3)
        backend.terminate(h1)  # final terminate closes


class TestSubsystem:
    def test_get_dass_returns_valid_handle(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        hdrvr = backend.initialize("DT9805(00)")
        try:
            hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
            assert isinstance(hdass, int)
            assert hdass > 0
            backend.release_dass(hdass)
        finally:
            backend.terminate(hdrvr)

    def test_query_capabilities_returns_constructed_set(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        hdrvr = backend.initialize("DT9805(00)")
        try:
            hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
            try:
                caps = backend.query_capabilities(hdass)
                assert caps.returns_floats is False
                assert caps.supports_multisensor is False
                assert caps.supports_thermocouples is True
                assert caps.num_channels == 17
            finally:
                backend.release_dass(hdass)
        finally:
            backend.terminate(hdrvr)

    def test_query_capabilities_on_released_handle_raises(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        hdrvr = backend.initialize("DT9805(00)")
        hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
        backend.release_dass(hdass)
        with pytest.raises(DtolResourceError):
            backend.query_capabilities(hdass)
        backend.terminate(hdrvr)

    def test_get_dass_for_missing_element_raises(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        hdrvr = backend.initialize("DT9805(00)")
        try:
            # DT9805 fake has only AI; AO subsystem does not exist.
            with pytest.raises(DtolResourceError):
                backend.get_dass(hdrvr, OLSS_DA, 0)
        finally:
            backend.terminate(hdrvr)


class TestScripting:
    def test_fail_next_raises_typed_exception(self) -> None:
        """Per-code table — buffer-overrun maps to ``DtolBufferOverrunError``."""
        from dtollib.capi.constants import OLDA_WM_OVERRUN_ERROR

        backend = make_fake_backend(include_dt9805=True)
        backend.fail_next("initialize", code=OLDA_WM_OVERRUN_ERROR)
        with pytest.raises(DtolBufferOverrunError):
            backend.initialize("DT9805(00)")

    def test_fail_next_is_one_shot(self) -> None:
        """After consuming the scripted failure, subsequent calls succeed."""
        from dtollib.errors import DtolError

        backend = make_fake_backend(include_dt9805=True)
        backend.fail_next("initialize", code=0x0150)
        with pytest.raises(DtolError):
            backend.initialize("DT9805(00)")
        # Next initialize should succeed.
        h = backend.initialize("DT9805(00)")
        backend.terminate(h)

    def test_operations_log_records_every_call(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        backend.get_version()
        backend.enum_boards()
        h = backend.initialize("DT9805(00)")
        backend.terminate(h)

        ops = [op for op, _ in backend.operations]
        assert ops == ["get_version", "enum_boards", "initialize", "terminate"]
