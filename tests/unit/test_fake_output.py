"""Output-surface contract tests for :class:`FakeDtolBackend`.

These lock the fake's enforcement so downstream session / builder tests
can rely on it: writes reject non-output subsystems, simultaneous writes
require the capability, mute/unmute track state, and the host→buffer copy
round-trips a waveform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib.capi.constants import OL_DF_SINGLEVALUE, OLSS_AD, OLSS_DA, OLSS_DOUT
from dtollib.errors import DtolResourceError, DtolTaskStateError
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend

_INT16_SAMPLE_BYTES = 2


def _open_subsystem(backend: FakeDtolBackend, board_name: str, ss_type: int) -> int:
    hdrvr = backend.initialize(board_name)
    return backend.get_dass(hdrvr, ss_type, 0)


def _commit_single_value(backend: FakeDtolBackend, hdass: int) -> None:
    backend.set_data_flow(hdass, OL_DF_SINGLEVALUE)
    backend.commit(hdass)


class TestPutSingleValue:
    def test_records_written_value_after_commit(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DA)
        _commit_single_value(backend, hdass)
        backend.put_single_value(hdass, channel=0, value=32768, gain=1.0)
        assert backend.written_values[(hdass, 0)] == 32768

    def test_rejects_write_before_commit(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DA)
        with pytest.raises(DtolTaskStateError, match="commit"):
            backend.put_single_value(hdass, channel=0, value=1, gain=1.0)

    def test_rejects_write_on_input_subsystem(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_AD)
        _commit_single_value(backend, hdass)
        with pytest.raises(DtolTaskStateError, match="non-output subsystem"):
            backend.put_single_value(hdass, channel=0, value=1, gain=1.0)


class TestPutSingleValues:
    def test_simultaneous_write_records_list(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DA)
        _commit_single_value(backend, hdass)
        backend.put_single_values(hdass, [100, 200], gain=1.0)
        assert backend.written_value_lists[hdass] == [100, 200]
        assert backend.written_values[(hdass, 1)] == 200

    def test_rejects_when_subsystem_lacks_simultaneous_da(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DOUT)
        _commit_single_value(backend, hdass)
        with pytest.raises(DtolTaskStateError, match="simultaneous-D/A"):
            backend.put_single_values(hdass, [1, 0], gain=1.0)


class TestMute:
    def test_mute_unmute_tracks_state(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DA)
        assert backend.is_muted(hdass) is False
        backend.mute(hdass)
        assert backend.is_muted(hdass) is True
        backend.unmute(hdass)
        assert backend.is_muted(hdass) is False


class TestDigitalIoConfig:
    def test_sync_usage_and_list_entries_recorded(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hdass = _open_subsystem(backend, "DT9806(00)", OLSS_DOUT)
        backend.set_synchronous_digital_io_usage(hdass, True)
        backend.set_digital_io_list_entry(hdass, 0, 5)
        assert ("set_synchronous_digital_io_usage", (hdass, True)) in backend.operations
        assert ("set_digital_io_list_entry", (hdass, 0, 5)) in backend.operations


class TestCopyToBuffer:
    def test_waveform_round_trips(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        hbuf = backend.alloc_buffer(4, _INT16_SAMPLE_BYTES, zero_init=True)
        wave = np.array([1, -2, 3, -4], dtype=np.int16)
        backend.copy_to_buffer(hbuf, wave.tobytes(), 4)
        assert backend.get_buffer_valid_samples(hbuf) == 4
        out = backend.copy_buffer(hbuf, 4, _INT16_SAMPLE_BYTES)
        assert np.array_equal(np.frombuffer(out, dtype=np.int16), wave)

    def test_copy_to_unknown_buffer_raises(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        with pytest.raises(DtolResourceError, match="invalid HBUF"):
            backend.copy_to_buffer(0xDEAD, b"\x00\x00", 1)
