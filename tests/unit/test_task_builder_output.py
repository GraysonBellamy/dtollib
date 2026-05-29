"""Continuous-AO configuration sequence via :class:`TaskBuilder` (WS-AO / A4).

The continuous builder path is direction-agnostic; these lock the exact
pre-commit call order it issues for an analog-output continuous task in both
``WrapMode.SINGLE`` and ``WrapMode.MULTIPLE``, and confirm it stops short of
``commit`` (the §12.3.2 register → queue → commit ordering belongs to ``play()``).
"""

from __future__ import annotations

from typing import cast

import pytest

from dtollib import (
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    SubsystemType,
    TaskBuilder,
    TaskSpec,
    Timing,
    WrapMode,
)
from dtollib.capi.constants import OL_WRP_MULTIPLE, OL_WRP_SINGLE, OLSS_DA
from dtollib.testing import make_fake_backend


def _ao_spec(wrap: WrapMode) -> TaskSpec:
    return TaskSpec(
        name="play-build",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", requires_confirm=False)],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=3, samples_per_buffer=4, wrap_mode=wrap),
    )


def _configure(wrap: WrapMode) -> list[tuple[str, object]]:
    backend = make_fake_backend(include_dt9806=True)
    hdrvr = backend.initialize("DT9806(00)")
    hdass = backend.get_dass(hdrvr, OLSS_DA, 0)
    caps = backend.query_capabilities(hdass)
    TaskBuilder(backend).configure_continuous(hdass, _ao_spec(wrap), caps)
    return backend.operations


@pytest.mark.parametrize("wrap", [WrapMode.SINGLE, WrapMode.MULTIPLE])
def test_ao_continuous_call_order(wrap: WrapMode) -> None:
    ops = _configure(wrap)
    names = [op for op, _ in ops]

    def idx(name: str) -> int:
        return names.index(name)

    assert idx("set_data_flow") < idx("add_channel")
    assert idx("add_channel") < idx("set_channel_list")
    assert idx("set_channel_list") < idx("set_clock")
    assert idx("set_clock") < idx("set_trigger")
    assert idx("set_trigger") < idx("set_wrap_mode")
    assert idx("set_wrap_mode") < idx("set_dma_usage")
    assert idx("set_dma_usage") < idx("set_stop_on_error")
    # The builder deliberately stops short of olDaConfig.
    assert "commit" not in names


@pytest.mark.parametrize(
    ("wrap", "expected"),
    [(WrapMode.SINGLE, OL_WRP_SINGLE), (WrapMode.MULTIPLE, OL_WRP_MULTIPLE)],
)
def test_wrap_mode_mapped(wrap: WrapMode, expected: int) -> None:
    ops = _configure(wrap)
    wrap_calls = [payload for op, payload in ops if op == "set_wrap_mode"]
    assert wrap_calls
    assert cast("tuple[int, int]", wrap_calls[-1])[1] == expected


def test_dma_usage_called_for_da() -> None:
    # DT9806 D/A reports supports_dma=True → set_dma_usage(1).
    ops = _configure(WrapMode.SINGLE)
    dma_calls = [payload for op, payload in ops if op == "set_dma_usage"]
    assert dma_calls
    assert cast("tuple[int, int]", dma_calls[-1])[1] == 1
