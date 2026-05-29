"""Ordering invariants for the continuous-AO (output) path (WS-AO / A2.fake).

The output path honours the same startup/teardown ordering as the input path,
plus Fill-before-Queue. These assert each violation fails loudly on the fake's
D/A subsystem, so the bridge's ordering is enforced exactly as on hardware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import DtolTaskStateError
from dtollib.capi.constants import OL_DF_CONTINUOUS, OLSS_DA
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _da() -> tuple[FakeDtolBackend, int]:
    backend = make_fake_backend(include_dt9806=True)
    hdrvr = backend.initialize("DT9806(00)")
    hdass = backend.get_dass(hdrvr, OLSS_DA, 0)
    backend.set_data_flow(hdass, OL_DF_CONTINUOUS)
    return backend, hdass


def _filled_buffer(backend: FakeDtolBackend, hdass: int) -> int:
    del hdass
    hbuf = backend.alloc_buffer(4, 2)
    backend.copy_to_buffer(hbuf, np.zeros(4, dtype=np.int16).tobytes(), 4)
    return hbuf


def _notify_callback(_msg_id: int, _wparam: int, _lparam: int) -> int:
    return 0


def test_fill_before_queue() -> None:
    backend, hdass = _da()
    backend.commit(hdass)
    hbuf = backend.alloc_buffer(4, 2)
    with pytest.raises(DtolTaskStateError, match="Fill-before-Queue"):
        backend.put_buffer(hdass, hbuf)  # never filled


def test_arm_before_register() -> None:
    backend, hdass = _da()
    backend.commit(hdass)
    with pytest.raises(DtolTaskStateError, match="register-before-arm"):
        backend.arm(hdass)


def test_arm_before_queue() -> None:
    backend, hdass = _da()
    backend.commit(hdass)
    backend.register_notification(hdass, _notify_callback)
    with pytest.raises(DtolTaskStateError, match="queue-before-arm"):
        backend.arm(hdass)


def test_start_before_arm() -> None:
    backend, hdass = _da()
    backend.commit(hdass)
    backend.register_notification(hdass, _notify_callback)
    backend.put_buffer(hdass, _filled_buffer(backend, hdass))
    with pytest.raises(DtolTaskStateError, match="start BEFORE arm"):
        backend.start(hdass)


def test_unregister_before_stop() -> None:
    backend, hdass = _da()
    backend.commit(hdass)
    handle = backend.register_notification(hdass, _notify_callback)
    backend.put_buffer(hdass, _filled_buffer(backend, hdass))
    backend.arm(hdass)
    backend.start(hdass)
    with pytest.raises(DtolTaskStateError, match="stop-before-unregister"):
        backend.unregister_notification(hdass, handle)
