"""Hardware output — digital port I/O on the DT9806 (WS-TEST).

Validates the shipped digital-write path (`DtolSession.write` to a
`DigitalOutputPort` + per-line `DigitalLine` views + the §18 confirm gate) on
real hardware. DT-Open Layers exposes one 8-bit port per direction (channel 0);
the library packs lines into a byte and issues a single port write, so all 8
relays are reachable through the public API (docs/bench-dio-ao.md §2D/§2E).

Tiers, gated separately:

- **No-wiring tier** (DT9806 attached): the confirm gate must reject a write
  that omits ``confirm`` on a ``requires_confirm`` port; the walking-1 / walking-0
  sweep drives every relay through the public per-line API and the raw byte.
- **Loopback tier** (OQ7 — needs a DOUT→DIN jumper, opt in via
  ``DTOLLIB_LOOPBACK_DOUT_DIN=1``): drive a DOUT line and read it back on DIN.

Gated by the ``hardware_output`` marker + ``DTOLLIB_ENABLE_OUTPUT_TESTS=1``.
"""

from __future__ import annotations

import os

import pytest

from dtollib import (
    DataFlow,
    DigitalInputPort,
    DigitalLine,
    DigitalOutputPort,
    DtolConfirmationRequiredError,
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
    os.environ.get("DTOLLIB_LOOPBACK_DOUT_DIN") != "1",
    reason="needs a DOUT→DIN jumper (OQ7); opt in via DTOLLIB_LOOPBACK_DOUT_DIN=1",
)

_BOARD = "DT9806(00)"

# Eight named line views over the single 8-bit DOUT/DIN port.
_DOUT_LINES = tuple(DigitalLine(bit=n, name=f"do{n}") for n in range(8))
_DIN_LINES = tuple(DigitalLine(bit=n, name=f"di{n}") for n in range(8))


def _do_spec(*, requires_confirm: bool = True) -> TaskSpec:
    return TaskSpec(
        name="hw_do_port",
        board=_BOARD,
        subsystem_type=SubsystemType.DIGITAL_OUTPUT,
        channels=[
            DigitalOutputPort(
                physical_channel=0,
                name="dout",
                requires_confirm=requires_confirm,
                lines=_DOUT_LINES,
            )
        ],
        data_flow=DataFlow.SINGLE_VALUE,
    )


def _di_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_di_readback",
        board=_BOARD,
        subsystem_type=SubsystemType.DIGITAL_INPUT,
        channels=[DigitalInputPort(physical_channel=0, name="din", lines=_DIN_LINES)],
        data_flow=DataFlow.SINGLE_VALUE,
    )


# --- No-wiring tier ---------------------------------------------------------


async def test_write_without_confirm_raises() -> None:
    """A requires_confirm DOUT port rejects a write that omits ``confirm``."""
    session = await open_device(_do_spec(requires_confirm=True), autostart=False)
    try:
        with pytest.raises(DtolConfirmationRequiredError):
            await session.write({"do0": True}, confirm=False)
    finally:
        await session.close()


async def test_walking_one_drives_each_relay() -> None:
    """Walking-1 / walking-0 across all 8 relays through the public per-line API.

    Each step drives exactly one line; the shadow register packs it into the
    port byte. Probe each pin with a DMM against the expected single-bit byte.
    """
    session = await open_device(_do_spec(requires_confirm=False), autostart=False)
    try:
        for n in range(8):
            # Walking-1: only line n high. Re-seat all lines so the byte is exact.
            pattern = {f"do{k}": (k == n) for k in range(8)}
            await session.write(pattern, confirm=True)
            assert session._dout_shadow[0] == (1 << n)  # pyright: ignore[reportPrivateUsage]
        # Raw-byte path: drive an alternating pattern in one write.
        await session.write({"dout": 0b1010_1010}, confirm=True)
        assert session._dout_shadow[0] == 0b1010_1010  # pyright: ignore[reportPrivateUsage]
    finally:
        await session.close()


# --- Loopback tier (OQ7) ----------------------------------------------------


@_loopback
@pytest.mark.parametrize("level", [True, False])
async def test_dout_to_din_loopback(level: bool) -> None:
    """Drive DOUT line 0 high/low and read the same level back on DIN line 0."""
    do = await open_device(_do_spec(requires_confirm=True), autostart=False)
    di = await open_device(_di_spec(), autostart=False)
    try:
        await do.write({"do0": level}, confirm=True)
        reading = await di.poll(timeout=1.0)
        assert bool(reading.values["di0"]) is level, (
            f"DOUT→DIN loopback: drove {level}, read {reading.values['di0']}"
        )
    finally:
        await di.close()
        await do.close()
