"""Coverage tests for :func:`record_polled` (software-timed scalar polling).

Exercises the session and manager producer loops, the validation guards,
all three :class:`ErrorPolicy` branches, and the overflow / missed-tick
paths of :mod:`dtollib.streaming.recorder` against ``FakeDtolBackend``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import anyio
import pytest

from dtollib import (
    AnalogInputVoltage,
    DtolError,
    DtolTaskStateError,
    TaskSpec,
    open_device,
    record_polled,
)
from dtollib.manager import DtolManager
from dtollib.streaming import ErrorPolicy, OverflowPolicy
from dtollib.tasks.models import DaqReading
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dtollib.manager import DeviceResult

pytestmark = pytest.mark.anyio


def _volt_spec(name: str = "v") -> TaskSpec:
    """A single-channel voltage spec on the default DT9805 fake."""
    return TaskSpec(
        name=name,
        board="DT9805(00)",
        channels=[AnalogInputVoltage(physical_channel=0, name="ch0")],
    )


def _flatten(exc: BaseException) -> list[BaseException]:
    """Flatten anyio/asyncio ``ExceptionGroup`` nesting to leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        group = cast("BaseExceptionGroup[BaseException]", exc)
        return [leaf for sub in group.exceptions for leaf in _flatten(sub)]
    return [exc]


async def _drain_raising(source: object) -> None:
    """Iterate a RAISE-policy recorder to exhaustion (so the error surfaces)."""
    async with record_polled(source, rate_hz=500.0, error_policy=ErrorPolicy.RAISE) as rec:  # type: ignore[arg-type]
        async for _ in rec.stream:
            pass


class TestValidation:
    async def test_rate_must_be_positive(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            with pytest.raises(ValueError, match="rate_hz"):
                async with record_polled(session, rate_hz=0.0):
                    pass

    async def test_buffer_size_must_be_positive(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            with pytest.raises(ValueError, match="buffer_size"):
                async with record_polled(session, rate_hz=10.0, buffer_size=0):
                    pass

    async def test_empty_manager_rejected(self) -> None:
        async with DtolManager() as mgr:
            with pytest.raises(DtolTaskStateError, match="at least one task"):
                async with record_polled(mgr, rate_hz=10.0):
                    pass


class TestSessionSource:
    async def test_emits_readings(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768  # mid-scale ≈ 0 V
            got: list[DaqReading] = []
            async with record_polled(session, rate_hz=500.0) as rec:
                assert rec.rate_hz == 500.0
                async for reading in rec.stream:
                    assert isinstance(reading, DaqReading)
                    got.append(reading)
                    if len(got) >= 3:
                        break
        assert len(got) == 3
        assert all(isinstance(r, DaqReading) for r in got)
        assert rec.summary.payloads_emitted >= 3
        assert rec.summary.finished_at is not None


class TestManagerSource:
    async def test_emits_device_result_mappings(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _volt_spec("A"), backend=backend)
            backend.scalar_values[(mgr.get("A").hdass, 0)] = 32768
            got: list[Mapping[str, DeviceResult[DaqReading]]] = []
            async with record_polled(mgr, rate_hz=500.0) as rec:
                async for results in rec.stream:
                    assert not isinstance(results, DaqReading)
                    got.append(results)
                    if len(got) >= 2:
                        break
        assert len(got) == 2
        assert set(got[0].keys()) == {"A"}


class TestManagerErrorPolicy:
    async def test_return_emits_empty_on_poll_failure(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _volt_spec("A"), backend=backend)
            backend.scalar_values[(mgr.get("A").hdass, 0)] = 32768
            # Manager default RAISE → manager.poll() raises; record_polled's
            # RETURN swallows it and emits an empty mapping for the tick.
            backend.fail_next("get_single_value", code=100)
            async with record_polled(mgr, rate_hz=500.0, error_policy=ErrorPolicy.RETURN) as rec:
                first = await anext(rec.stream)
                assert not isinstance(first, DaqReading)
        assert first == {}
        assert rec.summary.errors_observed == 1

    async def test_raise_propagates(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _volt_spec("A"), backend=backend)
            backend.scalar_values[(mgr.get("A").hdass, 0)] = 32768
            backend.fail_next("get_single_value", code=100)
            with pytest.raises((DtolError, BaseExceptionGroup)) as excinfo:
                await _drain_raising(mgr)
        assert any(isinstance(e, DtolError) for e in _flatten(excinfo.value))

    async def test_skip_drops_failed_tick(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _volt_spec("A"), backend=backend)
            backend.scalar_values[(mgr.get("A").hdass, 0)] = 32768
            backend.fail_next("get_single_value", code=100)
            async with record_polled(mgr, rate_hz=500.0, error_policy=ErrorPolicy.SKIP) as rec:
                first = await anext(rec.stream)
                assert not isinstance(first, DaqReading)
        assert set(first.keys()) == {"A"}
        assert rec.summary.errors_observed == 1


class TestErrorPolicy:
    async def test_return_emits_error_reading(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768
            backend.fail_next("get_single_value", code=100)
            async with record_polled(
                session, rate_hz=500.0, error_policy=ErrorPolicy.RETURN
            ) as rec:
                first = await anext(rec.stream)
                assert isinstance(first, DaqReading)
        assert first.error is not None
        assert rec.summary.errors_observed == 1

    async def test_skip_drops_failed_payload(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768
            backend.fail_next("get_single_value", code=100)
            async with record_polled(session, rate_hz=500.0, error_policy=ErrorPolicy.SKIP) as rec:
                # The failed tick is dropped silently; the next good poll is emitted.
                first = await anext(rec.stream)
                assert isinstance(first, DaqReading)
        assert first.error is None
        assert rec.summary.errors_observed == 1

    async def test_raise_propagates(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768
            backend.fail_next("get_single_value", code=100)
            with pytest.raises((DtolError, BaseExceptionGroup)) as excinfo:
                await _drain_raising(session)
        assert any(isinstance(e, DtolError) for e in _flatten(excinfo.value))


class TestOverflow:
    @pytest.mark.parametrize("policy", [OverflowPolicy.DROP_NEWEST, OverflowPolicy.DROP_OLDEST])
    async def test_drop_policies_record_drops(self, policy: OverflowPolicy) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768
            async with record_polled(
                session, rate_hz=5000.0, overflow=policy, buffer_size=1
            ) as rec:
                # Let the producer outrun an idle consumer so the 1-slot
                # buffer overflows and the drop branch fires.
                await anyio.sleep(0.05)
                await anext(rec.stream)  # prove the stream still delivers
        assert rec.summary.payloads_dropped > 0

    async def test_block_overflow_catches_up_after_stall(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_volt_spec(), backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = 32768
            async with record_polled(
                session, rate_hz=2000.0, overflow=OverflowPolicy.BLOCK, buffer_size=1
            ) as rec:
                # Consumer stalls → producer blocks on the full buffer. On
                # resume it detects the missed ticks and counts them dropped.
                await anyio.sleep(0.05)
                for _ in range(3):
                    await anext(rec.stream)
        assert rec.summary.payloads_emitted >= 3
        assert rec.summary.payloads_dropped > 0
