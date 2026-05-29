"""Safety-gate tests for :func:`dtollib.streaming.play` (WS-AO / A3).

``play()`` reuses the §18 gate from :meth:`DtolSession.write`: a sample outside
the device range is a :class:`DtolValidationError` (always, pre-seed); a sample
outside the safe band or a ``requires_confirm`` channel without ``confirm=True``
is a :class:`DtolConfirmationRequiredError`. Streamed chunks are validated as
they are pulled.
"""

from __future__ import annotations

import anyio
import numpy as np
import pytest

from dtollib import (
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    DtolCapabilityError,
    DtolConfirmationRequiredError,
    DtolTaskStateError,
    DtolValidationError,
    SubsystemType,
    TaskSpec,
    Timing,
    WrapMode,
    open_device,
    play,
)
from dtollib.testing import make_fake_backend

pytestmark = pytest.mark.anyio


def _spec(
    *,
    wrap: WrapMode = WrapMode.SINGLE,
    requires_confirm: bool = False,
    safe_min: float | None = None,
    safe_max: float | None = None,
) -> TaskSpec:
    return TaskSpec(
        name="play-safety",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[
            AnalogOutputVoltage(
                physical_channel=0,
                name="ao0",
                min_val=-10.0,
                max_val=10.0,
                requires_confirm=requires_confirm,
                safe_min=safe_min,
                safe_max=safe_max,
            )
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=3, samples_per_buffer=2, wrap_mode=wrap),
    )


async def _play_to_completion(session: object, source: object, **kwargs: object) -> None:
    async with play(session, source, **kwargs):  # type: ignore[arg-type]
        pass


async def test_out_of_device_range_raises_pre_seed() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(_spec(requires_confirm=False), backend=backend, autostart=False)
    source = np.array([0.0, 0.0, 0.0, 0.0, 11.0, 0.0])  # 11 V > device max
    try:
        with pytest.raises(DtolValidationError, match="device range"):
            await _play_to_completion(session, source)
        # Atomic: the gate trips pre-seed, so the subsystem never starts.
        assert not any(op == "start" for op, _ in backend.operations)
    finally:
        await session.close()


async def test_requires_confirm_without_confirm_raises() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(_spec(requires_confirm=True), backend=backend, autostart=False)
    try:
        with pytest.raises(DtolConfirmationRequiredError):
            await _play_to_completion(session, np.zeros(6), confirm=False)
    finally:
        await session.close()


async def test_requires_confirm_with_confirm_succeeds() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(_spec(requires_confirm=True), backend=backend, autostart=False)
    try:
        async with play(session, np.zeros(6), confirm=True) as summary:
            assert summary is not None
    finally:
        await session.close()


async def test_out_of_safe_band_requires_confirm() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(
        _spec(requires_confirm=False, safe_min=-1.0, safe_max=1.0),
        backend=backend,
        autostart=False,
    )
    source = np.full(6, 5.0)  # in device range, outside safe band
    try:
        with pytest.raises(DtolConfirmationRequiredError):
            await _play_to_completion(session, source, confirm=False)
    finally:
        await session.close()


async def test_play_rejects_single_value_only_da() -> None:
    """A D/A that reports OLSSC_SUP_CONTINUOUS=0 fails loud, not mid-startup.

    The physical DT9806 D/A is single-value only; `play()` must raise a clear
    `DtolCapabilityError` pointing at `write()` rather than dying at
    `olDaConfig` with a cryptic OLNOTSUPPORTED.
    """
    from dataclasses import replace

    from dtollib.backend.fake import FakeBoard, FakeSubsystem
    from dtollib.capi.constants import OLSS_AD, OLSS_DA
    from dtollib.testing import make_dt9806_ao_capabilities, make_dt9806_capabilities

    sv_only_da = replace(make_dt9806_ao_capabilities(), supports_continuous=False)
    board = FakeBoard(
        name="DT9806(00)",
        model="DT9806",
        driver_name="OLDT9806",
        instance=0,
        subsystems=[
            FakeSubsystem(type=OLSS_AD, element=0, capabilities=make_dt9806_capabilities()),
            FakeSubsystem(type=OLSS_DA, element=0, capabilities=sv_only_da),
        ],
    )
    backend = make_fake_backend(boards=[board])
    session = await open_device(_spec(requires_confirm=False), backend=backend, autostart=False)
    try:
        with pytest.raises(DtolCapabilityError, match="OLSSC_SUP_CONTINUOUS"):
            await _play_to_completion(session, np.zeros(6))
        # Atomic: the gate trips pre-commit, so the subsystem never starts.
        assert not any(op == "start" for op, _ in backend.operations)
    finally:
        await session.close()


async def test_play_rejects_single_value_task() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(
        name="sv-ao",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", requires_confirm=False)],
        data_flow=DataFlow.SINGLE_VALUE,
    )
    session = await open_device(spec, backend=backend, autostart=False)
    try:
        with pytest.raises(DtolTaskStateError, match="CONTINUOUS"):
            await _play_to_completion(session, np.zeros(6))
    finally:
        await session.close()


async def test_streamed_chunk_validated_per_pull() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(
        _spec(wrap=WrapMode.MULTIPLE, requires_confirm=False),
        backend=backend,
        autostart=False,
    )
    # Three valid seed chunks, then an out-of-range refill chunk.
    chunks = iter([np.zeros(2), np.zeros(2), np.zeros(2), np.full(2, 11.0)])

    def source() -> np.ndarray | None:
        return next(chunks, None)

    async def run() -> None:
        async with play(session, source):
            backend.fire_buffer_done(session.hdass)  # drainer pulls the bad chunk
            # Give the drainer a beat to pull + reject; the error is re-raised
            # when the block exits (after the shielded teardown).
            await anyio.sleep(0.1)

    try:
        with pytest.raises(DtolValidationError, match="device range"):
            await run()
    finally:
        await session.close()
