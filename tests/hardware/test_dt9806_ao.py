"""Hardware output — single-value analog output on the DT9806 (WS-TEST).

Validates the **already-shipped** single-value write path (`DtolSession.write`
+ the §18 confirm gate + `_ao_volts_to_code`) on real hardware, independent of
the continuous `play()` bridge.

Two tiers, gated separately:

- **No-wiring tier** (runs with just a DT9806 attached): the confirm gate must
  reject a write that lacks ``confirm`` and a write outside the device range.
  These need no jumper, so they are the cheapest hardware regression guard.
- **Loopback tier** (OQ7 — needs an AO0→AI0 jumper, opt in via
  ``DTOLLIB_LOOPBACK_AO0_AI0=1``): write a known voltage on AO0 with
  ``confirm=True``, read it back on AI0, assert within tolerance. This is the
  proof the volts→code path drives the right voltage.

Gated by the ``hardware_output`` marker + ``DTOLLIB_ENABLE_OUTPUT_TESTS=1``.
"""

from __future__ import annotations

import os

import pytest

from dtollib import (
    AnalogInputVoltage,
    AnalogOutputVoltage,
    DataFlow,
    DtolConfirmationRequiredError,
    DtolValidationError,
    SubsystemType,
    TaskSpec,
    open_device,
)

pytestmark = [
    pytest.mark.hardware_output,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_OUTPUT_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_OUTPUT_TESTS=1 with a DT9806 attached",
    ),
    pytest.mark.anyio,
]

_loopback = pytest.mark.skipif(
    os.environ.get("DTOLLIB_LOOPBACK_AO0_AI0") != "1",
    reason="needs an AO0→AI0 jumper (OQ7); opt in via DTOLLIB_LOOPBACK_AO0_AI0=1",
)

_BOARD = "DT9806(00)"
_TEST_VOLTS = 2.5
_TOLERANCE_V = 0.10  # generous — covers DAC + ADC offset/gain on a bare jumper


def _ao_spec(*, requires_confirm: bool = True) -> TaskSpec:
    return TaskSpec(
        name="hw_ao_single",
        board=_BOARD,
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[
            AnalogOutputVoltage(
                physical_channel=0,
                name="ao0",
                min_val=-10.0,
                max_val=10.0,
                requires_confirm=requires_confirm,
            )
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


def _ai_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_ai_readback",
        board=_BOARD,
        channels=[AnalogInputVoltage(physical_channel=0, name="ai0")],
        data_flow=DataFlow.SINGLE_VALUE,
    )


# --- No-wiring tier ---------------------------------------------------------


async def test_write_without_confirm_raises() -> None:
    """A requires_confirm AO channel rejects a write that omits ``confirm``."""
    session = await open_device(_ao_spec(requires_confirm=True), autostart=False)
    try:
        with pytest.raises(DtolConfirmationRequiredError):
            await session.write({"ao0": _TEST_VOLTS}, confirm=False)
    finally:
        await session.close()


async def test_write_out_of_range_raises_even_with_confirm() -> None:
    """A value past the device range is a hard error; ``confirm`` cannot override."""
    session = await open_device(_ao_spec(requires_confirm=False), autostart=False)
    try:
        with pytest.raises(DtolValidationError, match="range"):
            await session.write({"ao0": 11.0}, confirm=True)  # 11 V > 10 V max
    finally:
        await session.close()


# --- Loopback tier (OQ7) ----------------------------------------------------


@_loopback
async def test_ao0_to_ai0_loopback_recovers_value() -> None:
    """Write a known voltage on AO0, read it back on AI0 within tolerance."""
    ao = await open_device(_ao_spec(requires_confirm=True), autostart=False)
    ai = await open_device(_ai_spec(), autostart=False)
    try:
        await ao.write({"ao0": _TEST_VOLTS}, confirm=True)
        reading = await ai.poll(timeout=1.0)
        recovered = reading.values["ai0"]
        assert abs(recovered - _TEST_VOLTS) <= _TOLERANCE_V, (
            f"AO0→AI0 loopback: wrote {_TEST_VOLTS} V, read {recovered} V"
        )
    finally:
        await ai.close()
        await ao.close()
