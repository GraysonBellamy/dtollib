"""Tests for :class:`CapabilitySet` and :func:`query_capabilities`.

The integration with the real SDK is exercised in the hardware test
lane.  Here we verify the typed view and the per-cap fallback
behaviour when a subsystem doesn't support a particular cap.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dtollib.errors import DtolError
from dtollib.system.capabilities import CapabilitySet, query_capabilities


def _full_caps(**overrides: object) -> CapabilitySet:
    base: dict[str, object] = {
        "supports_singlevalue": True,
        "supports_continuous": True,
        "supports_simultaneous_sh": False,
        "supports_simultaneous_da": False,
        "supports_multisensor": False,
        "supports_singleended": True,
        "supports_dma": False,
        "supports_autocal": False,
        "supports_singlevalue_autorange": False,
        "supports_inprocess_flush": False,
        "supports_interleaved_cjc_in_stream": False,
        "returns_floats": False,
        "num_channels": 8,
        "cgl_depth": 8,
        "max_throughput_hz": 100_000.0,
    }
    base.update(overrides)
    return CapabilitySet(**base)  # type: ignore[arg-type]


def test_capability_set_is_frozen() -> None:
    caps = _full_caps()
    with pytest.raises(FrozenInstanceError):
        caps.num_channels = 16  # type: ignore[misc]


def test_supports_simultaneous_helper() -> None:
    ai = _full_caps(supports_simultaneous_sh=True)
    ao = _full_caps(supports_simultaneous_sh=False, supports_simultaneous_da=True)
    neither = _full_caps()
    assert ai.supports_simultaneous() is True
    assert ao.supports_simultaneous() is True
    assert neither.supports_simultaneous() is False


class _FakeApi:
    """Pure-Python stand-in for ``OpenLayersApi`` for capability tests."""

    def __init__(
        self,
        bool_caps: dict[int, bool] | None = None,
        int_caps: dict[int, int] | None = None,
        float_caps: dict[int, float] | None = None,
        fail_caps: set[int] | None = None,
    ) -> None:
        self._bool = bool_caps or {}
        self._int = int_caps or {}
        self._float = float_caps or {}
        self._fail = fail_caps or set()

    def get_ss_caps(self, hdass: int, cap_id: int) -> int:
        if cap_id in self._fail:
            raise DtolError("simulated cap miss")
        if cap_id in self._bool:
            return 1 if self._bool[cap_id] else 0
        if cap_id in self._int:
            return self._int[cap_id]
        raise DtolError(f"cap {cap_id} not present")

    def get_ss_caps_ex(self, hdass: int, cap_id: int) -> float:
        if cap_id in self._fail:
            raise DtolError("simulated cap miss")
        if cap_id in self._float:
            return self._float[cap_id]
        raise DtolError(f"float cap {cap_id} not present")


def test_query_capabilities_handles_partial_support() -> None:
    """When a cap query fails, the field defaults to ``False``/``0``/``None``."""
    api = _FakeApi(
        bool_caps={},  # everything fails
        int_caps={},
        float_caps={},
    )
    caps = query_capabilities(api, hdass=0xDEAD)  # type: ignore[arg-type]
    assert caps.supports_singlevalue is False
    assert caps.supports_continuous is False
    assert caps.num_channels == 0
    assert caps.max_throughput_hz is None


def test_output_caps_default_off() -> None:
    """WS-B output caps default to safe (False / 0) so the AO path degrades."""
    caps = _full_caps()
    assert caps.supports_mute is False
    assert caps.supports_wrp_waveform is False
    assert caps.supports_put_single_values is False
    assert caps.supports_synchronous_digitalio is False
    assert caps.current_outputs is False
    assert caps.max_digitaliolist_value == 0


def test_output_caps_populate_from_correct_enum_positions() -> None:
    """Each WS-B field reads its own ``olssc_tag`` position, not a neighbour.

    Guards the §1.4a failure mode: an off-by-one enum position silently
    maps a field to the wrong capability.  We set each target position true
    in isolation and assert exactly the intended field flips.
    """
    from dtollib.capi.constants import (
        OLSSC_CURRENT_OUTPUTS,
        OLSSC_MAX_DIGITALIOLIST_VALUE,
        OLSSC_SUP_MUTE,
        OLSSC_SUP_PUT_SINGLE_VALUES,
        OLSSC_SUP_SYNCHRONOUS_DIGITALIO,
        OLSSC_SUP_WRPWAVEFORM,
    )

    api = _FakeApi(
        bool_caps={
            OLSSC_SUP_MUTE: True,
            OLSSC_SUP_WRPWAVEFORM: True,
            OLSSC_SUP_PUT_SINGLE_VALUES: True,
            OLSSC_SUP_SYNCHRONOUS_DIGITALIO: True,
            OLSSC_CURRENT_OUTPUTS: True,
        },
        int_caps={OLSSC_MAX_DIGITALIOLIST_VALUE: 7},
    )
    caps = query_capabilities(api, hdass=0xDA)  # type: ignore[arg-type]
    assert caps.supports_mute is True
    assert caps.supports_wrp_waveform is True
    assert caps.supports_put_single_values is True
    assert caps.supports_synchronous_digitalio is True
    assert caps.current_outputs is True
    assert caps.max_digitaliolist_value == 7
