"""Tests for :meth:`DtolSession.write` — the §18 output safety gate.

Run against the fake DT9806. They lock the confirm-gate semantics
(decided 2026-05-28): out-of-device-range is always a ValidationError;
out-of-safe-band or requires_confirm without confirm is a
ConfirmationRequiredError; validation is atomic (one bad value ⇒ no SDK
writes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from dtollib import (
    AnalogOutputVoltage,
    DigitalLine,
    DigitalOutputPort,
    DtolConfirmationRequiredError,
    DtolValidationError,
    SubsystemType,
    TaskSpec,
    open_device,
)
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _ao_spec(
    *,
    min_val: float = -10.0,
    max_val: float = 10.0,
    safe_min: float | None = None,
    safe_max: float | None = None,
    requires_confirm: bool = True,
) -> TaskSpec:
    return TaskSpec(
        name="ao-write",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[
            AnalogOutputVoltage(
                physical_channel=0,
                name="cmd",
                min_val=min_val,
                max_val=max_val,
                safe_min=safe_min,
                safe_max=safe_max,
                requires_confirm=requires_confirm,
            )
        ],
    )


def _do_spec(
    *,
    safe_value: int | None = None,
    requires_confirm: bool = True,
    lines: tuple[DigitalLine, ...] = (),
    physical_channel: int = 0,
) -> TaskSpec:
    return TaskSpec(
        name="do-write",
        board="DT9806(00)",
        subsystem_type=SubsystemType.DIGITAL_OUTPUT,
        channels=[
            DigitalOutputPort(
                physical_channel=physical_channel,
                name="dout",
                safe_value=safe_value,
                requires_confirm=requires_confirm,
                lines=lines,
            )
        ],
    )


def _backend() -> FakeDtolBackend:
    return make_fake_backend(include_dt9806=True)


class TestAnalogWriteGate:
    def test_confirm_required_channel_without_confirm_raises(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=True)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolConfirmationRequiredError):
                    await session.write({"cmd": 1.0})

        anyio.run(run)

    def test_confirm_true_succeeds(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=True)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                await session.write({"cmd": 2.5}, confirm=True)
                hdass = session.hdass
                assert backend.written_values[(hdass, 0)] > 0

        anyio.run(run)

    def test_out_of_device_range_is_validation_error_pre_sdk(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=False, min_val=-10.0, max_val=10.0)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolValidationError, match="device range"):
                    await session.write({"cmd": 11.0}, confirm=True)
                # Atomic: no write reached the backend.
                assert not any(op == "put_single_value" for op, _ in backend.operations)

        anyio.run(run)

    def test_out_of_safe_band_is_confirm_gate(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=False, safe_min=-1.0, safe_max=1.0)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                # In-band write needs no confirm.
                await session.write({"cmd": 0.5})
                # Out-of-band write without confirm is gated...
                with pytest.raises(DtolConfirmationRequiredError):
                    await session.write({"cmd": 5.0})
                # ...but proceeds with confirm.
                await session.write({"cmd": 5.0}, confirm=True)

        anyio.run(run)

    def test_unknown_channel_raises(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=False)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolValidationError, match="unknown channel"):
                    await session.write({"nope": 1.0})

        anyio.run(run)


class TestDigitalWriteGate:
    def test_whole_port_byte_write(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=False)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                await session.write({"dout": 0b0000_0010})
                hdass = session.hdass
                # One put of the whole byte to the port (channel 0).
                assert backend.written_values[(hdass, 0)] == 0b0000_0010

        anyio.run(run)

    def test_port_confirm_gate(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=True)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolConfirmationRequiredError):
                    await session.write({"dout": 0x01})
                await session.write({"dout": 0x01}, confirm=True)
                assert backend.written_values[(session.hdass, 0)] == 0x01

        anyio.run(run)

    def test_per_line_writes_merge_via_shadow(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(
                requires_confirm=False,
                lines=(
                    DigitalLine(bit=1, name="r1"),
                    DigitalLine(bit=3, name="r3"),
                ),
            )
            async with await open_device(spec, backend=backend, autostart=False) as session:
                hdass = session.hdass
                await session.write({"r1": True})  # 0b0000_0010
                assert backend.written_values[(hdass, 0)] == 0b0000_0010
                await session.write({"r3": True})  # merges -> 0b0000_1010
                assert backend.written_values[(hdass, 0)] == 0b0000_1010
                await session.write({"r1": False})  # clears bit1 -> 0b0000_1000
                assert backend.written_values[(hdass, 0)] == 0b0000_1000

        anyio.run(run)

    def test_byte_then_line_in_one_call(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=False, lines=(DigitalLine(bit=0, name="r0"),))
            async with await open_device(spec, backend=backend, autostart=False) as session:
                # Whole-port byte sets the base; the per-line key refines on top.
                await session.write({"dout": 0b0000_0010, "r0": True})
                assert backend.written_values[(session.hdass, 0)] == 0b0000_0011

        anyio.run(run)

    def test_shadow_seeded_from_safe_value(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(
                requires_confirm=False,
                safe_value=0b1000_0000,
                lines=(DigitalLine(bit=0, name="r0"),),
            )
            async with await open_device(spec, backend=backend, autostart=False) as session:
                await session.write({"r0": True})  # merges over the safe byte
                assert backend.written_values[(session.hdass, 0)] == 0b1000_0001

        anyio.run(run)

    def test_per_line_confirm_override(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(
                requires_confirm=False,
                lines=(DigitalLine(bit=2, name="armed", requires_confirm=True),),
            )
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolConfirmationRequiredError):
                    await session.write({"armed": True})
                await session.write({"armed": True}, confirm=True)

        anyio.run(run)

    def test_byte_out_of_port_range_rejected(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=False)
            async with await open_device(spec, backend=backend, autostart=False) as session:
                with pytest.raises(DtolValidationError, match="outside the 8-bit port"):
                    await session.write({"dout": 256})  # > 0xFF on an 8-bit port

        anyio.run(run)

    def test_port_index_beyond_num_ports_rejected_at_configure(self) -> None:
        # The bug regression: a port index >= num_channels (the old per-line
        # model produced these) must fail before any SDK write.
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=False, physical_channel=1)
            with pytest.raises(DtolValidationError, match="out of range"):
                await open_device(spec, backend=backend, autostart=False)

        anyio.run(run)

    def test_line_bit_beyond_width_rejected_at_configure(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _do_spec(requires_confirm=False, lines=(DigitalLine(bit=8, name="r8"),))
            with pytest.raises(DtolValidationError, match="outside the 8-bit port"):
                await open_device(spec, backend=backend, autostart=False)

        anyio.run(run)


class TestAutostartGate:
    def test_autostart_output_without_confirm_start_raises(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=True)
            with pytest.raises(DtolConfirmationRequiredError, match="confirm_start"):
                await open_device(spec, backend=backend, autostart=True)

        anyio.run(run)

    def test_autostart_with_confirm_start_ok(self) -> None:
        async def run() -> None:
            backend = _backend()
            spec = _ao_spec(requires_confirm=True)
            session = await open_device(spec, backend=backend, autostart=True, confirm_start=True)
            await session.close()

        anyio.run(run)
