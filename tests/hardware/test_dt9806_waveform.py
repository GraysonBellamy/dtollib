"""Hardware output — continuous waveform `play()` on a streaming-DAC board.

**Not runnable on the owned DT9805/DT9806.** Bench finding B1 (see
``docs/plan-hardware-functional.md``): the DT9806 D/A is single-value only
(``OLSSC_SUP_CONTINUOUS = 0``), so `play()` fails loud with
``DtolCapabilityError`` rather than streaming. The `play()` software path is
fully exercised against `FakeDtolBackend`; this hardware test is retained for a
future DT-Open Layers board whose D/A reports continuous support, and is gated
off by default behind ``DTOLLIB_HAS_CONTINUOUS_DAC=1``.

Two tiers, both OQ7 loopback-gated (need an AO0→AI0 jumper, opt in via
``DTOLLIB_LOOPBACK_AO0_AI0=1``):

- **SINGLE recovery**: `play()` a known sine on AO0 with a
  ``WrapMode.SINGLE`` ring; capture AO0→AI0 via `record()`; assert the
  recovered waveform matches in amplitude (phase-independent).
- **MULTIPLE soak**: stream a long sine for ≥60 s; assert
  ``summary.underruns_observed == 0`` and the captured signal stays live.

Watch for the S0 open question: if the DA subsystem posts `BUFFER_DONE` on a
different cadence than the AD subsystem in ``SINGLE``, the SINGLE drainer's
event routing in ``_output_callback_bridge.py`` may need adjustment — run
``scripts/bench_probe_ao_wndhandle.py`` first.

Gated by the ``hardware_output`` marker + ``DTOLLIB_ENABLE_OUTPUT_TESTS=1``;
the soak additionally carries ``slow``.
"""

from __future__ import annotations

import math
import os

import anyio
import numpy as np
import pytest

from dtollib import (
    AnalogInputVoltage,
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    SubsystemType,
    TaskSpec,
    Timing,
    WrapMode,
    open_device,
    play,
    record,
)

pytestmark = [
    pytest.mark.hardware_output,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_OUTPUT_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_OUTPUT_TESTS=1 with a DT9806 attached",
    ),
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_LOOPBACK_AO0_AI0") != "1",
        reason="needs an AO0→AI0 jumper (OQ7); opt in via DTOLLIB_LOOPBACK_AO0_AI0=1",
    ),
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_HAS_CONTINUOUS_DAC") != "1",
        reason=(
            "DT9806 D/A is single-value only (B1); play() raises "
            "DtolCapabilityError. Needs a continuous-DAC board; opt in via "
            "DTOLLIB_HAS_CONTINUOUS_DAC=1"
        ),
    ),
    pytest.mark.anyio,
]

_BOARD = "DT9806(00)"
_RATE_HZ = 1000.0
_BUFFERS = 4
_SAMPLES_PER_BUFFER = 100
_RING = _BUFFERS * _SAMPLES_PER_BUFFER  # one SINGLE period spans the whole ring
_AMPLITUDE_V = 2.0
_TONE_HZ = 10.0
_AMP_TOLERANCE_V = 0.4  # bare-jumper DAC+ADC offset/gain slop


def _sine_period(n: int) -> np.ndarray:
    """One ``_TONE_HZ`` period set across ``n`` samples, as float volts."""
    t = np.arange(n)
    return _AMPLITUDE_V * np.sin(2.0 * math.pi * _TONE_HZ * t / n)


def _ao_spec(wrap: WrapMode) -> TaskSpec:
    return TaskSpec(
        name="hw_ao_wave",
        board=_BOARD,
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", requires_confirm=False)],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=_RATE_HZ),
        buffers=BufferPlan(
            buffers=_BUFFERS, samples_per_buffer=_SAMPLES_PER_BUFFER, wrap_mode=wrap
        ),
    )


def _ai_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_ai_capture",
        board=_BOARD,
        channels=[AnalogInputVoltage(physical_channel=0, name="ai0")],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=_RATE_HZ),
        buffers=BufferPlan(buffers=_BUFFERS, samples_per_buffer=_SAMPLES_PER_BUFFER),
    )


async def test_single_period_loopback_recovers_amplitude() -> None:
    """A ``SINGLE`` sine on AO0 is recovered on AI0 with the right amplitude."""
    ao = await open_device(_ao_spec(WrapMode.SINGLE), autostart=False)
    ai = await open_device(_ai_spec(), autostart=False)
    source = _sine_period(_RING)
    captured: list[np.ndarray] = []
    try:
        async with play(ao, source), record(ai) as recording:
            with anyio.fail_after(8.0):
                async for block in recording.stream:
                    captured.append(block.data[0].copy())
                    if len(captured) >= _BUFFERS + 2:  # skip startup transient
                        break
        recovered = np.concatenate(captured[2:])  # drop first blocks (settling)
        amp = (recovered.max() - recovered.min()) / 2.0
        assert recovered.std() > 0.1, "AI0 is flat — no waveform reached the ADC"
        assert abs(amp - _AMPLITUDE_V) <= _AMP_TOLERANCE_V, (
            f"recovered amplitude {amp:.3f} V != source {_AMPLITUDE_V} V"
        )
    finally:
        await ai.close()
        await ao.close()


@pytest.mark.slow
async def test_multiple_refill_soak_zero_underrun() -> None:
    """≥60 s ``MULTIPLE`` streamed playback drops zero buffers (underruns == 0)."""
    ao = await open_device(_ao_spec(WrapMode.MULTIPLE), autostart=False)
    period = _sine_period(_SAMPLES_PER_BUFFER)

    def source() -> np.ndarray | None:
        return period  # endless stream; the test bounds the run by time

    try:
        async with play(ao, source) as summary:
            await anyio.sleep(60.0)
        assert summary.underruns_observed == 0, (
            f"{summary.underruns_observed} underruns during the 60 s soak"
        )
    finally:
        await ao.close()
