"""Hardware acceptance — single-value reads on the DT9805/06.

Proves ``session.poll()`` returns engineering units on real hardware: voltage
channels read floats inside their configured range, and a K-type thermocouple
reads a plausible ambient temperature (or reports ``SENSOR_OPEN`` if the probe
is unplugged). Repeatable: the same session is polled several times.

Bench rig (override channels via env): voltage on ch0/ch1; for the TC half,
CJC sensor on ch0 (10 mV/°C, gain 1) and a K-type TC on ch4 (gain 100).
"""

from __future__ import annotations

import math
import os

import pytest

from dtollib import (
    AnalogInputVoltage,
    TaskSpec,
    ThermocoupleInput,
    open_device,
)
from dtollib.channels.analog_input import ThermocoupleType
from dtollib.tasks.models import SensorStatus

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_HARDWARE_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_HARDWARE_TESTS=1 with a DT9805/06 attached",
    ),
    pytest.mark.anyio,
]

_BOARD = os.environ.get("DTOLLIB_HW_BOARD", "DT9805(00)")
_TC_CHANNEL = int(os.environ.get("DTOLLIB_HW_TC_CHANNEL", "4"))


def _voltage_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_sv_voltage",
        board=_BOARD,
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0", min_val=-10.0, max_val=10.0),
            AnalogInputVoltage(physical_channel=1, name="ch1", min_val=-10.0, max_val=10.0),
        ],
    )


def _tc_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_sv_tc",
        board=_BOARD,
        channels=[
            AnalogInputVoltage(physical_channel=0, name="cjc", gain=1.0),
            ThermocoupleInput(
                physical_channel=_TC_CHANNEL,
                name="tc",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=150.0,
                gain=100.0,
                cjc_channel=0,
            ),
        ],
    )


async def test_voltage_poll_returns_floats_in_range() -> None:
    """Voltage channels read finite floats inside ±10 V across repeated polls."""
    async with await open_device(_voltage_spec()) as session:
        for _ in range(5):
            reading = await session.poll()
            for name in ("ch0", "ch1"):
                value = float(reading.values[name])
                assert math.isfinite(value), f"{name} not finite: {value}"
                assert -10.5 <= value <= 10.5, f"{name}={value} V outside ±10 V"
                assert reading.units[name] == "V"


async def test_thermocouple_poll_reads_temperature_or_open() -> None:
    """A K-type TC reads a plausible °C, or reports SENSOR_OPEN when unplugged."""
    async with await open_device(_tc_spec()) as session:
        reading = await session.poll()
        status = reading.sensor_status.get("tc")
        if status == SensorStatus.SENSOR_OPEN:
            assert math.isnan(float(reading.values["tc"]))
            return
        temp = float(reading.values["tc"])
        assert math.isfinite(temp), "connected TC produced a non-finite reading"
        assert -50.0 <= temp <= 150.0, f"TC temperature {temp} °C outside configured range"
        assert reading.units["tc"] == "degC"
        reference_c = os.environ.get("DTOLLIB_TC_REFERENCE_C")
        if reference_c is not None:
            # Maintainer step: agreement within K-type class-2 tolerance + CJC slack.
            assert abs(temp - float(reference_c)) <= 3.0
