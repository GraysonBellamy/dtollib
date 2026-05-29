"""Integration tests for :func:`record` against ``FakeDtolBackend``."""

from __future__ import annotations

import numpy as np
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    DtolBackendError,
    DtolTaskStateError,
    SdkEventKind,
    TaskSpec,
    Timing,
    open_device,
    record,
)
from dtollib.streaming._types import ErrorPolicy
from dtollib.testing import make_fake_backend


def _continuous_spec() -> TaskSpec:
    return TaskSpec(
        name="t",
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=10),
    )


class TestRecord:
    @pytest.mark.anyio
    async def test_record_yields_blocks_from_fake_fire(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with (
            await open_device(_continuous_spec(), backend=backend, autostart=False) as session,
            record(session) as recording,
        ):
            hdass = session.raw_hdass
            # Synthesise a single BUFFER_DONE event with 20 samples
            # (10 samples_per_buffer * 2 channels).
            backend.fire_buffer_done(hdass, fill=np.arange(20, dtype=np.int16))
            block = await recording.stream.receive()
            assert block.data.shape == (2, 10)
            assert block.block_index == 0
            assert recording.summary.payloads_emitted == 1

    @pytest.mark.anyio
    async def test_record_rejects_single_value_spec(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        spec = TaskSpec(
            name="sv",
            channels=[AnalogInputVoltage(physical_channel=0)],
        )
        async with await open_device(spec, backend=backend) as session:
            with pytest.raises(DtolTaskStateError, match="CONTINUOUS"):
                async with record(session):
                    pass

    @pytest.mark.anyio
    async def test_record_fails_loud_when_notification_mechanism_unavailable(self) -> None:
        """record() must fail loudly at startup if the buffer-done
        notification mechanism cannot be established — never advancing
        toward the silent-hang state where buffers stick INPROCESS and no
        block ever arrives.

        This locks the fail-loud contract for the continuous path. It
        guards against the bug class that hid behind unit-green: on real
        hardware the notification registration could succeed (or here, the
        message-window / wnd-handle setup could fail) without the consumer
        ever learning that no events will arrive.
        """
        backend = make_fake_backend(include_dt9805=True)
        # Simulate the notification mechanism being unavailable (e.g. the
        # hidden message-window or olDaSetWndHandle wiring failing).
        backend.fail_next("register_notification", code=0x0001)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            with pytest.raises(DtolBackendError):
                async with record(session):
                    pass

    @pytest.mark.anyio
    async def test_record_summary_overruns_field(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with (
            await open_device(_continuous_spec(), backend=backend, autostart=False) as session,
            record(session, error_policy=ErrorPolicy.RETURN) as recording,
        ):
            hdass = session.raw_hdass
            backend.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)
            block = await recording.stream.receive()
            assert block.error is not None
            assert recording.summary.overruns_observed == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
