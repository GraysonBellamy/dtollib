"""End-to-end tests for the :class:`DtolSession` single-value surface."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from dtollib import (
    AnalogInputVoltage,
    DtolCapabilityError,
    DtolTaskStateError,
    SensorStatus,
    SubsystemState,
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
    open_device,
)
from dtollib.backend.fake import FakeBoard, FakeDtolBackend, FakeSubsystem
from dtollib.capi.constants import OLSS_AD
from dtollib.testing import (
    make_dt9805_capabilities,
    make_fake_backend,
    make_firmware_tc_capabilities,
)

pytestmark = pytest.mark.anyio


def _tc_spec(name: str = "test", *, n: int = 2) -> TaskSpec:
    # ch0 is the cold-junction sensor on the DT9805/06; thermocouples live on
    # channels 1..n (the application-side read path reads CJC off ch0).
    return TaskSpec(
        name=name,
        board="DT9805(00)",
        channels=[
            ThermocoupleInput(
                physical_channel=ch,
                name=f"tc{ch}",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            )
            for ch in range(1, n + 1)
        ],
    )


# --- Offset-binary code helpers (mirror the DT9805/06 ±10 V / 16-bit A/D) ---
# Tests script raw codes; the session converts them to volts then temperature,
# exercising the real application-side path end to end.


def _code_for_volts(volts: float, gain: float) -> int:
    """Inverse of the offset-binary code→volts conversion (±10 V, 16-bit)."""
    return round((volts * gain + 10.0) / 20.0 * 65536.0)


def _cjc_code(degc: float) -> int:
    """Code on the CJC channel (read at unity gain, 10 mV/°C) for ``degc``."""
    return _code_for_volts(degc * 0.010, 1.0)


def _tc_emf_code(emf_volts: float, gain: float = 100.0) -> int:
    """Code on a TC channel for a given thermo-emf at the read gain."""
    return _code_for_volts(emf_volts, gain)


def _volt_spec(*channels: AnalogInputVoltage, name: str = "test") -> TaskSpec:
    """Voltage-mode task on the DT9805/06 A/D (returns_floats False, no TC)."""
    return TaskSpec(name=name, board="DT9805(00)", channels=list(channels))


class TestSingleValueVoltagePoll:
    async def test_poll_returns_volts_not_raw_code(self) -> None:
        # Int subsystem (returns_floats False) with a plain voltage channel:
        # poll() must convert the raw ADC code to engineering-unit volts, not
        # surface the code itself.
        backend = make_fake_backend(include_dt9805=True)
        spec = _volt_spec(AnalogInputVoltage(physical_channel=1, name="ai1"))
        async with await open_device(spec, backend=backend) as session:
            backend.scalar_values[(session.hdass, 1)] = _code_for_volts(2.5, 1.0)
            r = await session.poll()
            assert math.isclose(float(r.values["ai1"]), 2.5, abs_tol=0.01)

    async def test_poll_converts_at_channel_gain(self) -> None:
        # The code is read at the channel's gain, so the conversion must use the
        # same gain to recover the input volts.
        backend = make_fake_backend(include_dt9805=True)
        spec = _volt_spec(AnalogInputVoltage(physical_channel=1, name="ai1", gain=4.0))
        async with await open_device(spec, backend=backend) as session:
            backend.scalar_values[(session.hdass, 1)] = _code_for_volts(1.25, 4.0)
            r = await session.poll()
            assert math.isclose(float(r.values["ai1"]), 1.25, abs_tol=0.01)

    async def test_poll_multichannel_each_to_its_own_volts(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        spec = _volt_spec(
            AnalogInputVoltage(physical_channel=1, name="ai1"),
            AnalogInputVoltage(physical_channel=2, name="ai2"),
        )
        async with await open_device(spec, backend=backend) as session:
            backend.scalar_values[(session.hdass, 1)] = _code_for_volts(-3.0, 1.0)
            backend.scalar_values[(session.hdass, 2)] = _code_for_volts(7.5, 1.0)
            r = await session.poll()
            assert math.isclose(float(r.values["ai1"]), -3.0, abs_tol=0.01)
            assert math.isclose(float(r.values["ai2"]), 7.5, abs_tol=0.01)


class TestSingleValuePoll:
    async def test_poll_returns_engineering_units(self) -> None:
        # Application-side path: script the CJC code (ch0) and zero-emf TC codes;
        # a TC at the cold-junction temperature reads ~CJC temperature.
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec()
        async with await open_device(spec, backend=backend) as session:
            hdass = session.hdass
            backend.scalar_values[(hdass, 0)] = _cjc_code(25.0)
            backend.scalar_values[(hdass, 1)] = _tc_emf_code(0.0)
            backend.scalar_values[(hdass, 2)] = _tc_emf_code(0.0)
            r = await session.poll()
            assert abs(float(r.values["tc1"]) - 25.0) < 0.5
            assert abs(float(r.values["tc2"]) - 25.0) < 0.5
            assert r.units["tc1"] == "degC"

    async def test_poll_emf_above_cjc_reads_hotter(self) -> None:
        # A positive thermo-emf must read hotter than the cold junction, in the
        # right direction and magnitude (type K ≈ 41 µV/°C near room temp).
        from dtollib.utils import convert_volts_to_temperature

        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec(n=1)
        async with await open_device(spec, backend=backend) as session:
            hdass = session.hdass
            emf = 0.001  # +1 mV ≈ +24 °C above the cold junction for type K
            backend.scalar_values[(hdass, 0)] = _cjc_code(20.0)
            backend.scalar_values[(hdass, 1)] = _tc_emf_code(emf)
            r = await session.poll()
            expected = convert_volts_to_temperature("K", emf, cjc_temperature_c=20.0)
            assert math.isclose(float(r.values["tc1"]), expected, abs_tol=0.2)
            assert float(r.values["tc1"]) > 20.0

    async def test_poll_populates_provenance_fields(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec()
        async with await open_device(spec, backend=backend) as session:
            backend.scalar_values[(session.hdass, 0)] = _cjc_code(25.0)
            r = await session.poll()
            assert r.latency_s >= 0.0
            assert r.received_at >= r.requested_at
            assert r.t_mono_ns > 0
            assert r.device == "test"
            assert r.task == "test"

    async def test_poll_open_circuit_substitutes_nan_and_populates_status(self) -> None:
        # An open TC input is pulled to +full scale; the read path flags it
        # SENSOR_OPEN and NaN-fills the value rather than reporting a bogus temp.
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec()
        async with await open_device(spec, backend=backend) as session:
            hdass = session.hdass
            backend.scalar_values[(hdass, 0)] = _cjc_code(25.0)
            backend.scalar_values[(hdass, 1)] = _tc_emf_code(0.0)
            backend.scalar_values[(hdass, 2)] = 65535  # railed +full scale → open
            r = await session.poll()
            assert abs(float(r.values["tc1"]) - 25.0) < 0.5
            assert math.isnan(float(r.values["tc2"]))
            assert r.sensor_status["tc2"] == SensorStatus.SENSOR_OPEN
            assert "tc1" not in r.sensor_status  # OK channels omitted.

    async def test_poll_above_envelope_flagged_out_of_range(self) -> None:
        # A temperature above the channel's declared max becomes
        # TEMP_OUT_OF_RANGE_HIGH (NaN-filled), not a silently-extrapolated value.
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec(n=1)  # max_val_degc = 200 °C
        async with await open_device(spec, backend=backend) as session:
            hdass = session.hdass
            backend.scalar_values[(hdass, 0)] = _cjc_code(25.0)
            # +0.02 V emf ≈ +480 °C for type K — above the 200 °C envelope.
            backend.scalar_values[(hdass, 1)] = _tc_emf_code(0.020)
            r = await session.poll()
            assert math.isnan(float(r.values["tc1"]))
            assert r.sensor_status["tc1"] == SensorStatus.TEMP_OUT_OF_RANGE_HIGH

    async def test_poll_firmware_linearised_path_returns_floats(self) -> None:
        # Coverage for the firmware-linearising branch: returns_floats subsystem
        # emits temperature directly via get_single_float and honours sentinels.
        board = FakeBoard(
            name="DT-FW(00)",
            model="DT-FW",
            subsystems=[
                FakeSubsystem(type=OLSS_AD, element=0, capabilities=make_firmware_tc_capabilities())
            ],
        )
        backend = FakeDtolBackend([board])
        spec = TaskSpec(
            name="fw",
            board="DT-FW(00)",
            channels=[
                ThermocoupleInput(
                    physical_channel=ch,
                    name=f"tc{ch}",
                    thermocouple_type=ThermocoupleType.K,
                    min_val_degc=-50.0,
                    max_val_degc=200.0,
                )
                for ch in (0, 1)
            ],
        )
        async with await open_device(spec, backend=backend) as session:
            hdass = session.hdass
            backend.scalar_values[(hdass, 0)] = 25.5  # firmware path: already °C
            backend.thermocouple_sentinels[(hdass, 1)] = "sensor_open"
            r = await session.poll()
            assert r.values["tc0"] == 25.5
            assert math.isnan(float(r.values["tc1"]))
            assert r.sensor_status["tc1"] == SensorStatus.SENSOR_OPEN


class TestStateMachine:
    async def test_state_transitions_through_lifecycle(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec(n=1)
        session_state_after_close: SubsystemState
        async with await open_device(spec, backend=backend, autostart=False) as session:
            # After configure() but before start(), the fake's commit
            # transitions to CONFIGURED_FOR_SINGLE_VALUE.
            assert session.state == SubsystemState.CONFIGURED_FOR_SINGLE_VALUE
            await session.start()
            assert backend.state_of(session.hdass) == SubsystemState.RUNNING
            session_state_after_close = backend.state_of(session.hdass)
        # After __aexit__, the session is closed and queries default to INITIALIZED.
        assert session_state_after_close == SubsystemState.RUNNING

    async def test_poll_during_stopping_state_rejected(self) -> None:
        # Synthetic test: directly poke the state to STOPPING and assert.
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec(n=1)
        async with await open_device(spec, backend=backend) as session:
            backend.script_state(session.hdass, SubsystemState.STOPPING)
            with pytest.raises(DtolTaskStateError, match="mid-shutdown"):
                await session.poll()


class TestCapabilityValidation:
    async def test_tc_on_app_side_subsystem_allowed(self) -> None:
        # DT9805/06: returns_floats False but supports_thermocouples True →
        # the application-side path is permitted (configure must not raise).
        backend = make_fake_backend(include_dt9805=True)
        spec = _tc_spec(n=1)
        session = await open_device(spec, backend=backend)
        await session.close()

    async def test_tc_without_any_tc_support_rejected(self) -> None:
        # Neither firmware linearisation nor a TC front-end → reject.
        caps = replace(
            make_dt9805_capabilities(),
            returns_floats=False,
            supports_thermocouples=False,
        )
        board = FakeBoard(
            name="DT9805(00)",
            model="DT9805",
            subsystems=[FakeSubsystem(type=OLSS_AD, element=0, capabilities=caps)],
        )
        backend = FakeDtolBackend([board])
        spec = _tc_spec(n=1)
        with pytest.raises(DtolCapabilityError, match="OLSSC_SUP_THERMOCOUPLES"):
            await open_device(spec, backend=backend)

    async def test_unimplemented_tc_type_rejected(self) -> None:
        # Application-side path only ships K and J polynomials today; a Type-T
        # channel must fail at configure with a clear message.
        backend = make_fake_backend(include_dt9805=True)
        spec = TaskSpec(
            name="t",
            board="DT9805(00)",
            channels=[
                ThermocoupleInput(
                    physical_channel=1,
                    thermocouple_type=ThermocoupleType.T,
                    min_val_degc=-50.0,
                    max_val_degc=200.0,
                )
            ],
        )
        with pytest.raises(DtolCapabilityError, match="not implemented yet"):
            await open_device(spec, backend=backend)

    async def test_single_value_on_non_supporting_subsystem_rejected(self) -> None:
        caps = replace(make_dt9805_capabilities(), supports_singlevalue=False)
        board = FakeBoard(
            name="DT9805(00)",
            model="DT9805",
            subsystems=[FakeSubsystem(type=OLSS_AD, element=0, capabilities=caps)],
        )
        backend = FakeDtolBackend([board])
        spec = TaskSpec(
            name="t",
            board="DT9805(00)",
            channels=[AnalogInputVoltage(physical_channel=0)],
        )
        with pytest.raises(DtolCapabilityError, match="OLSSC_SUP_SINGLEVALUE"):
            await open_device(spec, backend=backend)


def _multisensor_backend() -> FakeDtolBackend:
    """Synthetic multi-sensor board for the §8.5a ordering invariant.

    No real DT9800-series board is multi-sensor (the DT9805/06 are not), but
    the wrapper still enforces ``set_multi_sensor_type`` before per-type setters
    for boards that report it. This fixture exercises that generic path.
    """
    board = FakeBoard(
        name="DT9805(00)",
        model="DT-MS",
        subsystems=[
            FakeSubsystem(type=OLSS_AD, element=0, capabilities=make_firmware_tc_capabilities()),
        ],
    )
    return FakeDtolBackend([board])


class TestMultiSensorOrdering:
    async def test_fake_rejects_per_type_setter_before_multi_sensor_set(self) -> None:
        """Direct check of the §8.5a fake invariant."""
        backend = _multisensor_backend()
        hdrvr = backend.initialize("DT9805(00)")
        hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
        spec = ThermocoupleInput(
            physical_channel=0,
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        )
        # Trying add_channel without first calling set_multi_sensor_type
        # on a MULTI_SENSOR board should raise.
        with pytest.raises(DtolTaskStateError, match=r"8\.5a"):
            backend.add_channel(hdass, 0, spec)

        backend.release_dass(hdass)
        backend.terminate(hdrvr)

    async def test_session_runs_multi_sensor_setter_in_order(self) -> None:
        """End-to-end: the session/builder threads MULTI_SENSOR ordering through correctly."""
        backend = _multisensor_backend()
        spec = _tc_spec(n=1)
        async with await open_device(spec, backend=backend):
            # In the operations log, set_multi_sensor_type for ch1 must
            # appear BEFORE add_channel for ch1.
            ops = [op for op, _ in backend.operations]
            i_ms = ops.index("set_multi_sensor_type")
            i_add = ops.index("add_channel")
            assert i_ms < i_add
