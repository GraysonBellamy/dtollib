"""Tests for the sync facade — :class:`Dtol` / :class:`SyncDtolSession`."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from dtollib import (
    SubsystemState,
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.sync import Dtol
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


@pytest.fixture
def fake_backend_with_dt9805() -> FakeDtolBackend:
    return make_fake_backend(include_dt9805=True)


def _tc_spec() -> TaskSpec:
    # ch0 is the CJC sensor on the DT9805/06; the thermocouple lives on ch1.
    return TaskSpec(
        name="sync_test",
        board="DT9805(00)",
        channels=[
            ThermocoupleInput(
                physical_channel=1,
                name="tc1",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            ),
        ],
    )


def test_sync_poll_round_trip(fake_backend_with_dt9805: FakeDtolBackend) -> None:
    backend = fake_backend_with_dt9805
    with Dtol.open_device(_tc_spec(), backend=backend) as session:
        hdass = session.raw_hdass
        # Application-side TC path: CJC=17.5 °C on ch0, zero emf on ch1 → ~17.5 °C.
        backend.scalar_values[(hdass, 0)] = round((17.5 * 0.010 + 10.0) / 20.0 * 65536.0)
        backend.scalar_values[(hdass, 1)] = 32768  # 0 V emf
        reading = session.poll()
        assert math.isclose(reading.values["tc1"], 17.5, abs_tol=0.5)
        assert session.state == SubsystemState.RUNNING


def test_sync_session_is_not_reusable(fake_backend_with_dt9805: FakeDtolBackend) -> None:
    backend = fake_backend_with_dt9805
    sync = Dtol.open_device(_tc_spec(), backend=backend)
    with sync:
        pass
    # After exit, the underlying portal is closed; a second poll would fail.
    with pytest.raises(RuntimeError, match="not entered"):
        sync.poll()
