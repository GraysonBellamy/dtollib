"""Tests for the :class:`DtolManager` single-value surface."""

from __future__ import annotations

import math

import pytest

from dtollib import (
    DtolResourceError,
    DtolValidationError,
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.manager import DtolManager, ErrorPolicy
from dtollib.testing import make_fake_backend

pytestmark = pytest.mark.anyio


def _cjc_code(degc: float) -> int:
    """CJC-channel code (unity gain, 10 mV/°C) on the ±10 V/16-bit offset-binary A/D."""
    return round((degc * 0.010 + 10.0) / 20.0 * 65536.0)


def _zero_emf_code() -> int:
    """TC-channel code for 0 V thermo-emf (reads ~CJC temperature)."""
    return 32768


def _tc_spec(name: str = "test", *, board: str = "DT9805(00)") -> TaskSpec:
    # ch0 is the CJC sensor on the DT9805/06; the thermocouple lives on ch1.
    return TaskSpec(
        name=name,
        board=board,
        channels=[
            ThermocoupleInput(
                physical_channel=1,
                name="tc1",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            ),
        ],
    )


class TestRegistry:
    async def test_add_get_remove(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _tc_spec(), backend=backend)
            session = mgr.get("A")
            assert session.spec.name == "test"
            await mgr.remove("A")
            with pytest.raises(DtolValidationError, match="unknown task"):
                mgr.get("A")

    async def test_duplicate_name_rejected(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _tc_spec(), backend=backend)
            with pytest.raises(DtolValidationError, match="already in use"):
                await mgr.add("A", _tc_spec(), backend=backend)

    async def test_subsystem_reservation_conflict_rejected(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _tc_spec("A"), backend=backend)
            # Same board + element + AI subsystem.
            with pytest.raises(DtolResourceError, match="already reserved"):
                await mgr.add("B", _tc_spec("B"), backend=backend)


class TestPoll:
    async def test_poll_returns_device_results(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _tc_spec("A"), backend=backend)
            hdass = mgr.get("A").hdass
            # Application-side TC path: CJC on ch0, zero-emf TC on ch1 → ~CJC temp.
            backend.scalar_values[(hdass, 0)] = _cjc_code(42.5)
            backend.scalar_values[(hdass, 1)] = _zero_emf_code()
            results = await mgr.poll(["A"])
            assert results["A"].ok
            assert results["A"].value is not None
            assert math.isclose(results["A"].value.values["tc1"], 42.5, abs_tol=0.5)

    async def test_poll_default_polls_all(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager() as mgr:
            await mgr.add("A", _tc_spec("A"), backend=backend)
            backend.scalar_values[(mgr.get("A").hdass, 0)] = _cjc_code(25.0)
            results = await mgr.poll()
            assert set(results.keys()) == {"A"}


class TestErrorPolicy:
    async def test_return_policy_wraps_errors(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with DtolManager(error_policy=ErrorPolicy.RETURN) as mgr:
            await mgr.add("A", _tc_spec("A"), backend=backend)
            # Inject a scripted failure on the next single-value read (the
            # application-side TC path reads CJC + emf via get_single_value).
            backend.fail_next("get_single_value", code=100)
            results = await mgr.poll(["A"])
            assert not results["A"].ok
            assert results["A"].error is not None
