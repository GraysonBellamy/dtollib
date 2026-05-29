"""Pure-function tests for DataAcq backend lookup tables.

These don't need the SDK — they lock in bench-verified SDK constants so a
future regression to guessed values is caught on any platform.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

# Imported from the real backend module; importing it does not load the SDK
# (load_openlayers is only called in DataAcqBackend.__init__).
from dtollib.backend.dataacq import _CHANNEL_TYPE_TO_OL
from dtollib.channels.analog_input import ChannelType
from dtollib.errors import DtolResourceError


class TestChannelTypeToOl:
    def test_single_ended_is_100(self) -> None:
        # OL_CHNT_SINGLEENDED from OLDADEFS.H (V7.0.0.7, bench-verified).
        assert _CHANNEL_TYPE_TO_OL(ChannelType.SINGLE_ENDED) == 100

    def test_differential_is_101(self) -> None:
        # OL_CHNT_DIFFERENTIAL — required for thermocouple reads; the old
        # value (1) produced ECODE=8 "Invalid Channel Type" on real hardware.
        assert _CHANNEL_TYPE_TO_OL(ChannelType.DIFFERENTIAL) == 101

    def test_accepts_value_string(self) -> None:
        assert _CHANNEL_TYPE_TO_OL("differential") == 101

    def test_pseudo_differential_unsupported(self) -> None:
        # This SDK build has no OL_CHNT_ constant for pseudo-differential.
        with pytest.raises(DtolResourceError, match="not supported by this SDK"):
            _CHANNEL_TYPE_TO_OL(ChannelType.PSEUDO_DIFFERENTIAL)
