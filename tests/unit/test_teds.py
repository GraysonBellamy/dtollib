"""§8.B5 — TEDS read helpers against the fake backend."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from dtollib import (
    BridgeSensorTeds,
    DtolCapabilityError,
    StrainGageTeds,
    read_bridge_sensor_teds,
    read_bridge_sensor_virtual_teds,
    read_strain_gage_teds,
    read_strain_gage_virtual_teds,
)
from dtollib.capi.constants import OLSS_AD
from dtollib.testing import make_fake_backend, make_fake_dt9805, make_fake_multisensor

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend

_STRAIN_PAYLOAD: dict[str, object] = {
    "manufacturerId": 17,
    "modelNumber": 350,
    "versionLetter": "B",
    "versionNumber": 2,
    "serialNumber": 123456,
    "gageFactor": 2.05,
    "sensorImped": 350.0,
    "poissonCoef": 0.3,
    "minPhysicalValue": -1000.0,
    "maxPhysicalValue": 1000.0,
}

_BRIDGE_PAYLOAD: dict[str, object] = {
    "manufacturerId": 42,
    "modelNumber": 1000,
    "versionLetter": "A",
    "versionNumber": 1,
    "serialNumber": 99,
    "sensorImped": 350.0,
    "exciteAmplNom": 10.0,
    "minPhysicalValue": 0.0,
    "maxPhysicalValue": 500.0,
    "minElecVal": 0.0,
    "maxElecVal": 0.002,
}


def _open(backend: FakeDtolBackend, board: str) -> int:
    hdrvr = backend.initialize(board)
    return backend.get_dass(hdrvr, OLSS_AD, 0)


class TestStrainGageTeds:
    def test_hardware_read_on_multisensor(self) -> None:
        backend = make_fake_backend(boards=[make_fake_multisensor()])
        hdass = _open(backend, "DT9829(00)")
        backend.strain_gage_teds[(hdass, 0)] = _STRAIN_PAYLOAD
        teds = read_strain_gage_teds(backend, hdass, 0)
        assert isinstance(teds, StrainGageTeds)
        assert math.isclose(teds.gage_factor, 2.05)
        assert teds.serial_number == 123456
        assert teds.version_letter == "B"
        assert teds.raw["modelNumber"] == 350

    def test_hardware_read_gated_on_owned_board(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        hdass = _open(backend, "DT9805(00)")
        with pytest.raises(DtolCapabilityError, match="hardware TEDS requires"):
            read_strain_gage_teds(backend, hdass, 0)

    def test_virtual_read_not_gated(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        backend.virtual_strain_gage_teds["/tmp/gage.teds"] = _STRAIN_PAYLOAD
        teds = read_strain_gage_virtual_teds(backend, "/tmp/gage.teds")
        assert math.isclose(teds.gage_factor, 2.05)


class TestBridgeSensorTeds:
    def test_hardware_read_on_multisensor(self) -> None:
        backend = make_fake_backend(boards=[make_fake_multisensor()])
        hdass = _open(backend, "DT9829(00)")
        backend.bridge_sensor_teds[(hdass, 1)] = _BRIDGE_PAYLOAD
        teds = read_bridge_sensor_teds(backend, hdass, 1)
        assert isinstance(teds, BridgeSensorTeds)
        assert math.isclose(teds.excitation_nominal_v, 10.0)
        assert math.isclose(teds.max_physical_value, 500.0)

    def test_hardware_read_gated_on_owned_board(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        hdass = _open(backend, "DT9805(00)")
        with pytest.raises(DtolCapabilityError, match="hardware TEDS requires"):
            read_bridge_sensor_teds(backend, hdass, 0)

    def test_virtual_read_not_gated(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        backend.virtual_bridge_sensor_teds["/tmp/bridge.teds"] = _BRIDGE_PAYLOAD
        teds = read_bridge_sensor_virtual_teds(backend, "/tmp/bridge.teds")
        assert math.isclose(teds.sensor_impedance_ohms, 350.0)


def test_missing_scripted_hardware_teds_raises_ec36() -> None:
    backend = make_fake_backend(boards=[make_fake_multisensor()])
    hdass = _open(backend, "DT9829(00)")
    # No payload scripted for this channel → fake mimics ec=36.
    with pytest.raises(DtolCapabilityError):
        read_strain_gage_teds(backend, hdass, 5)
