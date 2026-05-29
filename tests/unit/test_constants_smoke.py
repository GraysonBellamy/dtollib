"""Smoke test that ``dtollib.constants`` re-exports work cross-platform."""

from __future__ import annotations

import importlib

import dtollib
import dtollib.constants


def test_constants_module_reexports() -> None:
    """Every enum is importable from ``dtollib.constants``."""
    # The names below are the documented re-export surface.
    for name in (
        "BufferState",
        "ClockSource",
        "DataFlow",
        "Edge",
        "IOType",
        "QueueStrategy",
        "RetriggerMode",
        "SensorStatus",
        "SubsystemState",
        "SubsystemType",
        "WrapMode",
    ):
        assert hasattr(dtollib.constants, name), f"missing re-export: {name}"

    # And the re-export identity holds — ``dtollib.constants.DataFlow is
    # dtollib.DataFlow`` so consumers can match on either import path.
    assert dtollib.constants.DataFlow is dtollib.DataFlow
    assert dtollib.constants.SubsystemState is dtollib.SubsystemState


def test_user_and_sdk_namespaces_stay_separate() -> None:
    """``dtollib.constants`` and ``dtollib.capi.constants`` are disjoint.

    ``dtollib.capi.constants`` provides SDK-side numeric
    values (``OL_DF_*``, ``OLSS_*``, ``OLSSC_*``); they live in a
    separate namespace from the user-facing StrEnums.  The two
    modules never share a name — this test catches the regression
    where a future PR accidentally adds an SDK constant to the
    user-facing namespace (or vice versa).
    """
    sdk = importlib.import_module("dtollib.capi.constants")
    user_names = set(dir(dtollib.constants)) - set(dir(object))
    user_names = {n for n in user_names if not n.startswith("_")}
    sdk_names = set(dir(sdk)) - set(dir(object))
    sdk_names = {n for n in sdk_names if not n.startswith("_")}
    # The intersection should be empty modulo Python-internal names
    # that show up in both module namespaces (annotations bookkeeping).
    overlap = user_names & sdk_names
    # Allow ``annotations`` (from ``from __future__ import``) and any
    # similar futures import that becomes a module attribute on both.
    overlap.discard("annotations")
    assert overlap == set(), f"user and SDK constant namespaces overlap on: {sorted(overlap)}"
