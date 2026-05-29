"""Regression tests for DataAcqBackend._set_voltage_range fallback.

The DT9805/06 A/D rejects per-channel ``olDaSetChannelRange`` with
OLNOTSUPPORTED (ECODE 36) — its range is subsystem-wide (±10 V native,
gain-selected). ``_set_voltage_range`` must fall back to the
subsystem-wide ``olDaSetRange`` on that error, re-raise other errors,
and tolerate boards that support neither (native range + gain).

These exercise the method on an instance built via ``object.__new__`` so
no SDK DLL is loaded — they run on any platform.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from unittest.mock import Mock

import pytest

from dtollib.backend.dataacq import DataAcqBackend
from dtollib.capi.constants import OL_NOT_SUPPORTED
from dtollib.errors import DtolBackendError, ErrorContext

_HDASS = 1234
_CH = 6


def _backend_with_api(api: Mock) -> DataAcqBackend:
    """Build a DataAcqBackend without running __init__ (no SDK load)."""
    be = object.__new__(DataAcqBackend)
    be._api = api
    return be


def _not_supported() -> DtolBackendError:
    return DtolBackendError(
        "olDaSetChannelRange failed with ECODE=36",
        context=ErrorContext(operation="olDaSetChannelRange", ecode=OL_NOT_SUPPORTED),
    )


def test_per_channel_range_used_when_supported() -> None:
    """When olDaSetChannelRange succeeds, olDaSetRange is never called."""
    api = Mock()
    be = _backend_with_api(api)
    be._set_voltage_range(_HDASS, _CH, -10.0, 10.0)
    api.set_channel_range.assert_called_once_with(_HDASS, _CH, -10.0, 10.0)
    api.set_range.assert_not_called()


def test_falls_back_to_subsystem_range_on_not_supported() -> None:
    """ECODE 36 from per-channel range falls back to set_range(max, min)."""
    api = Mock()
    api.set_channel_range.side_effect = _not_supported()
    be = _backend_with_api(api)
    be._set_voltage_range(_HDASS, _CH, -10.0, 10.0)
    # olDaSetRange takes (max_val, min_val) order.
    api.set_range.assert_called_once_with(_HDASS, 10.0, -10.0)


def test_other_error_from_per_channel_range_propagates() -> None:
    """A non-OLNOTSUPPORTED error must NOT trigger the fallback."""
    api = Mock()
    other = DtolBackendError(
        "olDaSetChannelRange failed with ECODE=8",
        context=ErrorContext(operation="olDaSetChannelRange", ecode=8),
    )
    api.set_channel_range.side_effect = other
    be = _backend_with_api(api)
    with pytest.raises(DtolBackendError):
        be._set_voltage_range(_HDASS, _CH, -10.0, 10.0)
    api.set_range.assert_not_called()


def test_tolerates_neither_range_supported() -> None:
    """If both calls report not-supported, fall through to native+gain (no raise)."""
    api = Mock()
    api.set_channel_range.side_effect = _not_supported()
    api.set_range.side_effect = _not_supported()
    be = _backend_with_api(api)
    be._set_voltage_range(_HDASS, _CH, -5.0, 5.0)


def test_other_error_from_subsystem_range_propagates() -> None:
    """A non-OLNOTSUPPORTED error from the set_range fallback must propagate."""
    api = Mock()
    api.set_channel_range.side_effect = _not_supported()
    api.set_range.side_effect = DtolBackendError(
        "olDaSetRange failed with ECODE=8",
        context=ErrorContext(operation="olDaSetRange", ecode=8),
    )
    be = _backend_with_api(api)
    with pytest.raises(DtolBackendError):
        be._set_voltage_range(_HDASS, _CH, -5.0, 5.0)
