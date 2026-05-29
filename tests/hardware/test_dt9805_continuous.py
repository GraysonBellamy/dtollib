"""Hardware acceptance — continuous AI ``record()`` on the DT9805 (WS-A0).

Proves the production ``record()`` path delivers ``DaqBlock``s on real hardware
through the ``olDaSetWndHandle`` + message-pump bridge. Before WS-A0 this hung
with buffers stuck INPROCESS, so the core assertion is simply: at least one
block arrives within a bounded time, with the configured shape.

Reads whatever sits on AI ch0/ch1 — no external stimulus required. The 60-min
1 kHz soak (the full §5.16 DoD) is a separate, longer maintainer run; this is
the fast regression guard that the bench mechanism still works.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

import anyio
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    Timing,
    open_device,
    record,
)

if TYPE_CHECKING:
    from dtollib.tasks.models import DaqBlock

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_HARDWARE_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_HARDWARE_TESTS=1 with a DT9805 attached",
    ),
    pytest.mark.anyio,
]

_BOARD = "DT9805(00)"
_RATE_HZ = 1000.0
_SAMPLES_PER_BUFFER = 100
_N_CHANNELS = 2


def _continuous_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_continuous",
        board=_BOARD,
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=_RATE_HZ),
        buffers=BufferPlan(buffers=4, samples_per_buffer=_SAMPLES_PER_BUFFER),
    )


async def test_record_delivers_blocks() -> None:
    """``record()`` yields at least 3 correctly-shaped blocks within 8 s."""
    blocks: list[DaqBlock] = []
    async with (
        await open_device(_continuous_spec(), autostart=False) as session,
        record(session) as recording,
    ):
        with anyio.fail_after(8.0):
            async for block in recording.stream:
                blocks.append(block)
                if len(blocks) >= 3:
                    break

    assert blocks, "no DaqBlock arrived — buffer-done events did not flow (WS-A0 broken)"
    for i, block in enumerate(blocks):
        assert block.data.shape == (_N_CHANNELS, _SAMPLES_PER_BUFFER)
        assert block.block_index == i
    assert recording.summary.payloads_emitted >= len(blocks)


@pytest.mark.slow
async def test_record_soak_zero_drop() -> None:
    """Continuous-AI soak (§5.16 DoD): zero dropped/overrun blocks.

    Duration defaults to the full 3600 s (60-min) acceptance soak; override via
    ``DTOLLIB_SOAK_SECONDS`` for a shorter smoke run. The zero-drop guarantee is
    asserted three ways: contiguous ``block_index`` (a gap = a dropped buffer),
    no SDK overruns, and no consumer-side drops. Under the default
    ``ErrorPolicy.RAISE`` an overrun also raises mid-run.
    """
    duration_s = float(os.environ.get("DTOLLIB_SOAK_SECONDS", "3600"))
    # rate_hz is the aggregate A/D sample clock (olDaSetClockFrequency); the
    # multiplexed ADC splits it across the channel list, so the per-channel
    # scan rate is rate_hz / n_channels. Each block carries samples_per_buffer
    # scans/channel. (Bench-confirmed on DT9805: 1 kHz clock + 2 ch -> 500
    # scans/s -> ~1500 blocks in 300 s.)
    expected_blocks = duration_s * (_RATE_HZ / _N_CHANNELS) / _SAMPLES_PER_BUFFER
    seen = 0
    last_index = -1
    async with (
        await open_device(_continuous_spec(), autostart=False) as session,
        record(session) as recording,
    ):
        with anyio.move_on_after(duration_s):
            async for block in recording.stream:
                assert block.block_index == last_index + 1, (
                    f"block_index gap: {last_index} -> {block.block_index} "
                    f"({last_index + 1 - block.block_index} block(s) dropped)"
                )
                last_index = block.block_index
                seen += 1

    summary = recording.summary
    assert summary.overruns_observed == 0, f"{summary.overruns_observed} SDK overruns"
    assert summary.payloads_dropped == 0, f"{summary.payloads_dropped} consumer-side drops"
    assert summary.errors_observed == 0, f"{summary.errors_observed} SDK errors"
    # Within 10% of the clocked block count — confirms it ran the full duration
    # at rate, not that it stalled early.
    assert seen >= 0.9 * math.floor(expected_blocks), (
        f"only {seen} blocks in {duration_s}s; expected ~{expected_blocks:.0f}"
    )
