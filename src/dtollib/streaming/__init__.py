"""Streaming surface — the recorders, the player, and their shared types.

- :func:`record` — hardware-clocked continuous block acquisition.
- :func:`record_polled` — software-timed scalar polling.
- :func:`play` — hardware-clocked continuous analog-output (waveform playback).
"""

from __future__ import annotations

from dtollib.streaming._types import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    Recording,
)
from dtollib.streaming.block import record
from dtollib.streaming.playback import PlaybackSource, play
from dtollib.streaming.recorder import record_polled

__all__ = [
    "AcquisitionSummary",
    "ErrorPolicy",
    "OverflowPolicy",
    "PlaybackSource",
    "Recording",
    "play",
    "record",
    "record_polled",
]
