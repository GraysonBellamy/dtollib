"""Tests for :mod:`dtollib.channels`."""

from __future__ import annotations

import pytest

from dtollib import (
    AnalogInputBase,
    AnalogInputVoltage,
    AnalogOutputVoltage,
    BridgeConfiguration,
    BridgeInput,
    ChannelSpec,
    CouplingType,
    CurrentInput,
    DigitalInputPort,
    DigitalLine,
    DigitalOutputPort,
    DtolValidationError,
    ExcitationSource,
    IepeInput,
    IOType,
    ResistanceInput,
    RtdInput,
    RtdType,
    StrainGageConfiguration,
    StrainInput,
    ThermistorInput,
    ThermocoupleInput,
    ThermocoupleType,
    channel_from_dict,
)


class TestChannelSpec:
    def test_display_name_falls_back_to_ch_n(self) -> None:
        spec = AnalogInputVoltage(physical_channel=3)
        assert spec.display_name == "ch3"

    def test_display_name_uses_explicit_name(self) -> None:
        spec = AnalogInputVoltage(physical_channel=3, name="vbat")
        assert spec.display_name == "vbat"

    def test_metadata_is_immutable_after_construction(self) -> None:
        spec = AnalogInputVoltage(physical_channel=0, metadata={"k": "v"})
        with pytest.raises(TypeError):
            spec.metadata["k"] = "x"  # type: ignore[index]


class TestAnalogInputVoltage:
    def test_min_must_be_less_than_max(self) -> None:
        with pytest.raises(DtolValidationError, match=r"min_val=.*must be"):
            AnalogInputVoltage(physical_channel=0, min_val=10.0, max_val=10.0)

    def test_kind_discriminator(self) -> None:
        assert AnalogInputVoltage.kind == "ai_voltage"

    def test_kind_to_multi_sensor_type(self) -> None:
        spec = AnalogInputVoltage(physical_channel=0)
        assert spec.kind_to_multi_sensor_type() == IOType.VOLTAGE_IN


class TestThermocoupleInput:
    def test_min_must_be_less_than_max(self) -> None:
        with pytest.raises(DtolValidationError, match="min_val_degc"):
            ThermocoupleInput(
                physical_channel=0,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=200.0,
                max_val_degc=100.0,
            )

    def test_range_outside_nist_envelope_rejected(self) -> None:
        # K type max is 1372 °C per NIST.
        with pytest.raises(DtolValidationError, match="NIST envelope"):
            ThermocoupleInput(
                physical_channel=0,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-100.0,
                max_val_degc=2000.0,
            )

    def test_range_inside_envelope_accepted(self) -> None:
        spec = ThermocoupleInput(
            physical_channel=0,
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        )
        assert spec.kind == "thermocouple"
        assert spec.kind_to_multi_sensor_type() == IOType.THERMOCOUPLE


class TestChannelSpecBase:
    def test_base_kind_to_multi_sensor_raises(self) -> None:
        # ChannelSpec base class doesn't know how to map itself.
        class BareSpec(ChannelSpec):
            pass

        with pytest.raises(NotImplementedError):
            BareSpec(physical_channel=0).kind_to_multi_sensor_type()


def test_analog_input_base_is_subclass_of_channel_spec() -> None:
    assert issubclass(AnalogInputBase, ChannelSpec)
    assert issubclass(AnalogInputVoltage, AnalogInputBase)
    assert issubclass(ThermocoupleInput, AnalogInputBase)


class TestAnalogOutputVoltage:
    def test_kind_and_io_type(self) -> None:
        spec = AnalogOutputVoltage(physical_channel=0)
        assert spec.kind == "ao_voltage"
        assert spec.kind_to_multi_sensor_type() == IOType.VOLTAGE_OUT
        assert spec.requires_confirm is True

    def test_min_must_be_less_than_max(self) -> None:
        with pytest.raises(DtolValidationError, match="must be strictly less"):
            AnalogOutputVoltage(physical_channel=0, min_val=5.0, max_val=5.0)

    def test_safe_band_outside_device_range_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match=r"safe_max.*above the device range"):
            AnalogOutputVoltage(physical_channel=0, min_val=-5.0, max_val=5.0, safe_max=6.0)
        with pytest.raises(DtolValidationError, match=r"safe_min.*below the device range"):
            AnalogOutputVoltage(physical_channel=0, min_val=-5.0, max_val=5.0, safe_min=-6.0)

    def test_safe_min_must_be_less_than_safe_max(self) -> None:
        with pytest.raises(DtolValidationError, match=r"safe_min.*must be strictly less"):
            AnalogOutputVoltage(physical_channel=0, safe_min=2.0, safe_max=1.0)

    def test_in_device_range(self) -> None:
        spec = AnalogOutputVoltage(physical_channel=0, min_val=-10.0, max_val=10.0)
        assert spec.in_device_range(5.0)
        assert not spec.in_device_range(11.0)

    def test_in_safe_band(self) -> None:
        spec = AnalogOutputVoltage(physical_channel=0, safe_min=-1.0, safe_max=1.0)
        assert spec.in_safe_band(0.5)
        assert not spec.in_safe_band(2.0)
        assert not spec.in_safe_band(-2.0)

    def test_in_safe_band_unset_is_always_true(self) -> None:
        spec = AnalogOutputVoltage(physical_channel=0)
        assert spec.in_safe_band(9.9)


class TestDigitalPorts:
    def test_digital_input_kind(self) -> None:
        spec = DigitalInputPort(physical_channel=0, width=8)
        assert spec.kind == "digital_input_port"
        assert spec.kind_to_multi_sensor_type() == IOType.DIGITAL_INPUT

    def test_digital_output_kind_and_safety(self) -> None:
        spec = DigitalOutputPort(physical_channel=0, safe_value=0b1000_0000)
        assert spec.kind == "digital_output_port"
        assert spec.kind_to_multi_sensor_type() == IOType.DIGITAL_OUTPUT
        assert spec.requires_confirm is True
        assert spec.safe_value == 0b1000_0000

    def test_line_keys_default_and_named(self) -> None:
        spec = DigitalOutputPort(
            physical_channel=0,
            name="dout",
            width=8,
            lines=(DigitalLine(bit=0), DigitalLine(bit=3, name="armed")),
        )
        views = spec.line_views()
        assert set(views) == {"dout.line0", "armed"}
        assert views["armed"].bit == 3

    def test_duplicate_line_bit_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="duplicate line bit"):
            DigitalOutputPort(
                physical_channel=0,
                width=8,
                lines=(DigitalLine(bit=1), DigitalLine(bit=1)),
            )

    def test_line_bit_outside_declared_width_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="outside"):
            DigitalOutputPort(physical_channel=0, width=4, lines=(DigitalLine(bit=5),))


class TestMultiSensorSpecs:
    """Multi-sensor input specs (hardware-deferred)."""

    def test_io_type_mapping(self) -> None:
        assert RtdInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.RTD
        assert (
            ThermistorInput(physical_channel=0, a=1e-3, b=2e-4, c=1e-7).kind_to_multi_sensor_type()
            == IOType.THERMISTOR
        )
        assert ResistanceInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.RESISTANCE
        assert CurrentInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.CURRENT
        assert IepeInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.ACCELEROMETER
        assert StrainInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.STRAIN_GAGE
        assert BridgeInput(physical_channel=0).kind_to_multi_sensor_type() == IOType.BRIDGE

    def test_kind_discriminators_are_unique(self) -> None:
        kinds = {
            RtdInput.kind,
            ThermistorInput.kind,
            ResistanceInput.kind,
            CurrentInput.kind,
            IepeInput.kind,
            StrainInput.kind,
            BridgeInput.kind,
        }
        assert len(kinds) == 7

    def test_iepe_rejects_dc_coupling(self) -> None:
        with pytest.raises(DtolValidationError, match="requires AC coupling"):
            IepeInput(physical_channel=0, coupling=CouplingType.DC)

    def test_iepe_rejects_disabled_excitation(self) -> None:
        with pytest.raises(DtolValidationError, match="requires a drive current"):
            IepeInput(physical_channel=0, excitation_source=ExcitationSource.DISABLED)

    def test_iepe_rejects_nonpositive_current(self) -> None:
        with pytest.raises(DtolValidationError, match="excitation_current_a must be positive"):
            IepeInput(physical_channel=0, excitation_current_a=0.0)

    def test_rtd_custom_requires_coefficients(self) -> None:
        with pytest.raises(DtolValidationError, match="requires both 'a' and 'b'"):
            RtdInput(physical_channel=0, rtd_type=RtdType.CUSTOM)

    def test_rtd_standard_rejects_coefficients(self) -> None:
        with pytest.raises(DtolValidationError, match="only valid with rtd_type=CUSTOM"):
            RtdInput(physical_channel=0, rtd_type=RtdType.PT3850, a=3.9e-3, b=-5.8e-7)

    def test_rtd_custom_with_coefficients_accepted(self) -> None:
        spec = RtdInput(physical_channel=0, rtd_type=RtdType.CUSTOM, a=3.9e-3, b=-5.8e-7)
        assert spec.a == 3.9e-3

    def test_rtd_rejects_nonpositive_r0(self) -> None:
        with pytest.raises(DtolValidationError, match="r0 must be positive"):
            RtdInput(physical_channel=0, r0=0.0)

    def test_strain_rejects_nonpositive_gage_factor(self) -> None:
        with pytest.raises(DtolValidationError, match="gage_factor must be positive"):
            StrainInput(physical_channel=0, gage_factor=0.0)

    def test_strain_rejects_nonpositive_resistance(self) -> None:
        with pytest.raises(DtolValidationError, match="gage_resistance_ohms must be positive"):
            StrainInput(physical_channel=0, gage_resistance_ohms=-1.0)

    def test_bridge_rejects_nonpositive_resistance(self) -> None:
        with pytest.raises(DtolValidationError, match="nominal_resistance_ohms must be positive"):
            BridgeInput(physical_channel=0, nominal_resistance_ohms=0.0)

    def test_current_min_must_be_less_than_max(self) -> None:
        with pytest.raises(DtolValidationError, match=r"min_val=.*must be strictly less"):
            CurrentInput(physical_channel=0, min_val=0.02, max_val=0.0)

    def test_default_strain_and_bridge_configs(self) -> None:
        assert StrainInput(physical_channel=0).configuration is (
            StrainGageConfiguration.QUARTER_BRIDGE
        )
        assert BridgeInput(physical_channel=0).configuration is BridgeConfiguration.FULL

    def test_all_subclass_analog_input_base(self) -> None:
        for cls in (
            RtdInput,
            ThermistorInput,
            ResistanceInput,
            CurrentInput,
            IepeInput,
            StrainInput,
            BridgeInput,
        ):
            assert issubclass(cls, AnalogInputBase)


class TestChannelFromDict:
    @pytest.mark.parametrize(
        "spec",
        [
            AnalogInputVoltage(physical_channel=0, name="v0"),
            ThermocoupleInput(
                physical_channel=1,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            ),
            AnalogOutputVoltage(physical_channel=2, safe_min=-1.0, safe_max=1.0),
            DigitalInputPort(physical_channel=0, width=8, lines=(DigitalLine(bit=2, name="di2"),)),
            DigitalOutputPort(
                physical_channel=0,
                width=8,
                safe_value=0b0000_0001,
                lines=(DigitalLine(bit=4, name="r4", requires_confirm=False),),
            ),
            RtdInput(physical_channel=5, rtd_type=RtdType.CUSTOM, a=3.9e-3, b=-5.8e-7),
            RtdInput(physical_channel=5, rtd_type=RtdType.PT3850, r0=1000.0),
            ThermistorInput(physical_channel=6, a=1.4e-3, b=2.4e-4, c=1.0e-7),
            ResistanceInput(physical_channel=7),
            CurrentInput(physical_channel=8, min_val=0.004, max_val=0.02),
            IepeInput(physical_channel=9, excitation_current_a=0.002),
            StrainInput(
                physical_channel=10,
                configuration=StrainGageConfiguration.HALF_BRIDGE_BENDING,
            ),
            BridgeInput(physical_channel=11, configuration=BridgeConfiguration.HALF),
        ],
    )
    def test_round_trip(self, spec: ChannelSpec) -> None:
        rebuilt = channel_from_dict(spec.to_dict())
        assert rebuilt == spec

    def test_missing_kind_raises(self) -> None:
        with pytest.raises(DtolValidationError, match="no 'kind'"):
            channel_from_dict({"physical_channel": 0})

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(DtolValidationError, match="unknown channel kind"):
            channel_from_dict({"kind": "nope", "physical_channel": 0})
