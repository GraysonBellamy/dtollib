"""End-to-end tests for :func:`dtollib.streaming.play` against the fake (WS-AO).

``WrapMode.SINGLE`` seeds the buffer ring once and loops it (no refill).
``WrapMode.MULTIPLE`` refills + requeues each emptied buffer from the source.
Both confirm the volts→offset-binary-code encoding survives the round-trip
through the fake's ``copy_to_buffer`` / ``copy_buffer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import anyio
import numpy as np
import numpy.typing as npt
import pytest

from dtollib import (
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    SubsystemType,
    TaskSpec,
    Timing,
    WrapMode,
    open_device,
    play,
)
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend

pytestmark = pytest.mark.anyio


def _ao_spec(
    *,
    wrap: WrapMode,
    buffers: int = 3,
    samples_per_buffer: int = 2,
    n_channels: int = 1,
    requires_confirm: bool = False,
) -> TaskSpec:
    channels = [
        AnalogOutputVoltage(
            physical_channel=i,
            name=f"ao{i}",
            min_val=-10.0,
            max_val=10.0,
            requires_confirm=requires_confirm,
        )
        for i in range(n_channels)
    ]
    return TaskSpec(
        name="play-task",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=channels,
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=buffers, samples_per_buffer=samples_per_buffer, wrap_mode=wrap),
    )


def _expected_code(volts: float, lo: float = -10.0, hi: float = 10.0) -> int:
    return round((volts - lo) / (hi - lo) * 65535)


def _seed_hbufs(backend: FakeDtolBackend) -> list[int]:
    """HBUFs in the order they were ``copy_to_buffer``-filled."""
    return [
        cast("tuple[int, int]", payload)[0]
        for op, payload in backend.operations
        if op == "copy_to_buffer"
    ]


def _codes(backend: FakeDtolBackend, hbuf: int, n: int) -> npt.NDArray[np.uint16]:
    raw = backend.copy_buffer(hbuf, n, 2)
    return np.frombuffer(raw, dtype=np.uint16)


# The fake fires synchronously and the drainer's refill is sub-millisecond;
# a short settle is enough to let it pull + fill before the assertion.
_SETTLE_S = 0.1


class TestPlaySingle:
    async def test_single_seeds_ring_with_expected_codes(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        spec = _ao_spec(wrap=WrapMode.SINGLE, buffers=3, samples_per_buffer=2)
        # One period spanning the whole ring (3 buffers × 2 samples = 6).
        source = np.array([-10.0, -10.0, 0.0, 0.0, 10.0, 10.0])

        session = await open_device(spec, backend=backend, autostart=False)
        try:
            async with play(session, source) as summary:
                hbufs = _seed_hbufs(backend)
                assert len(hbufs) == 3
                assert np.all(_codes(backend, hbufs[0], 2) == _expected_code(-10.0))
                assert np.all(_codes(backend, hbufs[1], 2) == _expected_code(0.0))
                assert np.all(_codes(backend, hbufs[2], 2) == _expected_code(10.0))
            assert summary.underruns_observed == 0
        finally:
            await session.close()


class TestPlayMultiple:
    async def test_multiple_refills_with_successive_source_chunks(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        spec = _ao_spec(wrap=WrapMode.MULTIPLE, buffers=3, samples_per_buffer=2)

        # Six distinct chunks then exhaustion; seed consumes the first three.
        chunks = iter([np.full(2, float(v)) for v in range(6)])

        def source() -> np.ndarray | None:
            return next(chunks, None)

        session = await open_device(spec, backend=backend, autostart=False)
        try:
            async with play(session, source) as summary:
                hdass = session.hdass
                # Emit the head buffer (held chunk v=0); drainer refills it v=3.
                moved = backend.fire_buffer_done(hdass)
                assert moved is not None
                await anyio.sleep(_SETTLE_S)
                assert summary.payloads_emitted >= 1
                assert np.all(_codes(backend, moved, 2) == _expected_code(3.0))
                # Next emit refills the following buffer with v=4.
                moved2 = backend.fire_buffer_done(hdass)
                assert moved2 is not None
                await anyio.sleep(_SETTLE_S)
                assert summary.payloads_emitted >= 2
                assert np.all(_codes(backend, moved2, 2) == _expected_code(4.0))
            assert summary.underruns_observed == 0
        finally:
            await session.close()

    async def test_exhausted_source_ends_cleanly(self) -> None:
        backend = make_fake_backend(include_dt9806=True)
        spec = _ao_spec(wrap=WrapMode.MULTIPLE, buffers=3, samples_per_buffer=2)
        # Exactly enough to seed, none left to refill.
        chunks = iter([np.zeros(2) for _ in range(3)])

        def source() -> np.ndarray | None:
            return next(chunks, None)

        session = await open_device(spec, backend=backend, autostart=False)
        try:
            async with play(session, source) as summary:
                hdass = session.hdass
                backend.fire_buffer_done(hdass)  # source exhausted → clean stop
                await anyio.sleep(0.05)
            assert summary.underruns_observed == 0
        finally:
            await session.close()
