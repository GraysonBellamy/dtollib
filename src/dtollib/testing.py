"""Test ergonomics — pre-populated fake boards and capability sets.

Importable from production code (it lives in ``dtollib`` proper, not
under ``tests/``) so downstream consumers — including the sibling
``*lib`` packages — can spin up a fake DT9805 / DT9806 without
copy-pasting capability flags.

The realistic capability sets here come from cross-referencing the
DataAcq SDK manual with the DT9805 / DT9806 datasheets.  They are not
authoritative replacements for live capability queries against real
hardware — they exist so unit tests can target a plausible "this is
what a DT9805 looks like" without requiring a board on the bench.

Bench-confirmed capability snapshots replace these defaults in
:func:`make_fake_dt9805` / :func:`make_fake_dt9806`.
"""

from __future__ import annotations

from dtollib.backend.fake import FakeBoard, FakeDtolBackend, FakeSubsystem
from dtollib.capi.constants import (
    OLSS_AD,
    OLSS_CT,
    OLSS_DA,
    OLSS_DIN,
    OLSS_DOUT,
    OLSS_QUAD,
    OLSS_TACH,
)
from dtollib.system.capabilities import CapabilitySet

__all__ = [
    "make_counter_capabilities",
    "make_dt9805_capabilities",
    "make_dt9806_ao_capabilities",
    "make_dt9806_capabilities",
    "make_fake_backend",
    "make_fake_dt9805",
    "make_fake_dt9806",
    "make_fake_multisensor",
    "make_firmware_tc_capabilities",
    "make_multisensor_capabilities",
]


def make_dt9805_capabilities() -> CapabilitySet:
    """A/D capabilities for a DT9805 — **bench-verified** on real hardware.

    Snapshot taken 2026-05-28 via ``olDaGetSSCaps`` against a live
    DT9806(00) A/D subsystem (the DT9805 A/D is identical).  Note what
    this disproves about the earlier datasheet-guessed values: these
    boards are **not** multi-sensor and do **not** return linearised
    floats — they return raw codes and the wrapper applies NIST ITS-90
    itself, keyed off ``supports_thermocouples`` (see docs/decisions.md
    and :func:`dtollib.utils.convert_volts_to_temperature`).
    """
    return CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=True,
        supports_simultaneous_sh=False,
        supports_simultaneous_da=False,
        supports_multisensor=False,
        supports_singleended=True,
        supports_dma=False,
        supports_autocal=False,
        supports_singlevalue_autorange=True,
        supports_inprocess_flush=True,
        supports_interleaved_cjc_in_stream=False,
        returns_floats=False,
        supports_thermocouples=True,
        num_channels=17,
        cgl_depth=32,
        max_throughput_hz=50_000.0,
    )


def make_dt9806_capabilities() -> CapabilitySet:
    """A/D capabilities for a DT9806 — identical to the DT9805 A/D subsystem."""
    return make_dt9805_capabilities()


def make_firmware_tc_capabilities() -> CapabilitySet:
    """Synthetic A/D caps for a *firmware-linearising* thermocouple board.

    No DT board in this project actually linearises thermocouples in
    firmware (the DT9805/06 do not — see :func:`make_dt9805_capabilities`),
    but the wrapper still supports that path for hypothetical boards that
    report ``OLSSC_RETURNS_FLOATS``. This fixture keeps that branch under
    test: ``returns_floats=True`` so ``olDaGetSingleFloat`` is used and the
    device's TC sentinels are honoured directly.
    """
    return CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=True,
        supports_simultaneous_sh=True,
        supports_simultaneous_da=False,
        supports_multisensor=True,
        supports_singleended=True,
        supports_dma=False,
        supports_autocal=True,
        supports_singlevalue_autorange=True,
        supports_inprocess_flush=False,
        supports_interleaved_cjc_in_stream=True,
        returns_floats=True,
        supports_thermocouples=True,
        num_channels=8,
        cgl_depth=8,
        max_throughput_hz=100_000.0,
    )


def make_dt9806_ao_capabilities() -> CapabilitySet:
    """Realistic D/A capabilities for a DT9806.

    Two AO channels, simultaneous-update support, no multi-sensor
    inputs (this is the output subsystem).

    ``supports_continuous=True`` here models a *streaming* D/A so the
    ``play()`` buffer-pool / output-bridge software path stays fully
    unit-tested.  **The physical DT9806 D/A is single-value only** — it
    reports ``OLSSC_SUP_CONTINUOUS=0`` and ``play()`` raises
    :class:`~dtollib.errors.DtolCapabilityError` on it (bench-confirmed
    2026-05-28; see docs/decisions.md).  Same fake-models-the-ideal pattern as
    :func:`make_counter_capabilities` (QUAD/TACH/MEASURE).
    """
    return CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=True,
        supports_simultaneous_sh=False,
        supports_simultaneous_da=True,
        supports_multisensor=False,
        supports_singleended=True,
        supports_dma=True,
        supports_autocal=False,
        supports_singlevalue_autorange=False,
        supports_inprocess_flush=False,
        supports_interleaved_cjc_in_stream=False,
        returns_floats=False,
        num_channels=2,
        cgl_depth=2,
        max_throughput_hz=200_000.0,
    )


def make_multisensor_capabilities() -> CapabilitySet:
    """Synthetic A/D caps for an *intelligent multi-sensor* DT module.

    Models a DT9828/9829/9837-class board: ``supports_multisensor=True`` so
    the :class:`~dtollib.tasks.TaskBuilder` re-types each channel via
    ``set_multi_sensor_type`` and the multi-sensor per-sensor configure path runs
    end to end on the fake.  No board in this project actually owns these
    capabilities — the owned DT9805/06 report ``supports_multisensor=False``
    (see :func:`make_dt9805_capabilities`) — so this fixture is the only way
    to exercise the RTD/thermistor/strain/bridge/IEPE configure path in CI.
    """
    return CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=True,
        supports_simultaneous_sh=True,
        supports_simultaneous_da=False,
        supports_multisensor=True,
        supports_singleended=True,
        supports_dma=True,
        supports_autocal=True,
        supports_singlevalue_autorange=True,
        supports_inprocess_flush=True,
        supports_interleaved_cjc_in_stream=True,
        returns_floats=True,
        supports_thermocouples=True,
        num_channels=8,
        cgl_depth=8,
        max_throughput_hz=100_000.0,
    )


def make_fake_multisensor(*, name: str = "DT9829(00)") -> FakeBoard:
    """Construct a :class:`FakeBoard` mimicking an intelligent multi-sensor module.

    The single A/D subsystem reports ``supports_multisensor=True``, so the
    builder exercises the full multi-sensor configure path (set_multi_sensor_type
    → per-sensor setters) that the owned DT9805/06 reject with ECODE 36.
    """
    return FakeBoard(
        name=name,
        model="DT9829",
        driver_name="OLDT9829",
        instance=0,
        subsystems=[
            FakeSubsystem(
                type=OLSS_AD,
                element=0,
                capabilities=make_multisensor_capabilities(),
            ),
        ],
    )


def make_fake_dt9805(*, name: str = "DT9805(00)") -> FakeBoard:
    """Construct a :class:`FakeBoard` mimicking a connected DT9805."""
    return FakeBoard(
        name=name,
        model="DT9805",
        driver_name="OLDT9805",
        instance=0,
        subsystems=[
            FakeSubsystem(
                type=OLSS_AD,
                element=0,
                capabilities=make_dt9805_capabilities(),
            ),
        ],
    )


def make_counter_capabilities(*, num_channels: int = 2) -> CapabilitySet:
    """Realistic capabilities for a C/T-family subsystem (CT / QUAD / TACH).

    Counter subsystems read on demand (single-value) after start; they have
    no multi-sensor inputs and no DMA.
    """
    return CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=True,
        supports_simultaneous_sh=False,
        supports_simultaneous_da=False,
        supports_multisensor=False,
        supports_singleended=False,
        supports_dma=False,
        supports_autocal=False,
        supports_singlevalue_autorange=False,
        supports_inprocess_flush=False,
        supports_interleaved_cjc_in_stream=False,
        returns_floats=False,
        # The fake models a fully-featured C/T (QUAD/TACH/MEASURE) so the
        # counter/timer software path is exercised in unit tests even though the
        # physical DT9805/06 expose none of these (OQ-5b). On real hardware
        # these read false and the builder gates the modes off.
        supports_ctmode_measure=True,
        supports_quadrature_decoder=True,
        num_channels=num_channels,
        cgl_depth=1,
        max_throughput_hz=20_000_000.0,
    )


def make_fake_dt9806(*, name: str = "DT9806(00)") -> FakeBoard:
    """Construct a :class:`FakeBoard` mimicking a connected DT9806.

    Exposes the full subsystem set: A/D, D/A, digital in/out, counter/timer,
    quadrature decoder, and tachometer.  The QUAD/TACH subsystems are modelled
    so the counter/timer software path is fully unit-tested even where physical
    hardware may not expose them (OQ-5b — see docs/decisions.md).
    """
    ao_caps = make_dt9806_ao_capabilities()
    # Digital I/O is PORT-shaped, not per-line: the DT9805/06 expose ONE 8-bit
    # port per direction (num_channels=1, resolution=8 lines). Modelling it this
    # way is what lets the fake reproduce the ECODE 7 a per-line channel index
    # hits on real hardware (docs/bench-dio-ao.md §2D; docs/decisions.md).
    dio_caps = CapabilitySet(
        supports_singlevalue=True,
        supports_continuous=False,
        supports_simultaneous_sh=False,
        supports_simultaneous_da=False,
        supports_multisensor=False,
        supports_singleended=False,
        supports_dma=False,
        supports_autocal=False,
        supports_singlevalue_autorange=False,
        supports_inprocess_flush=False,
        supports_interleaved_cjc_in_stream=False,
        returns_floats=False,
        resolution=8,
        num_channels=1,
        cgl_depth=1,
        max_throughput_hz=None,
    )
    ct_caps = make_counter_capabilities(num_channels=2)
    quad_caps = make_counter_capabilities(num_channels=1)
    tach_caps = make_counter_capabilities(num_channels=1)
    return FakeBoard(
        name=name,
        model="DT9806",
        driver_name="OLDT9806",
        instance=0,
        subsystems=[
            FakeSubsystem(type=OLSS_AD, element=0, capabilities=make_dt9806_capabilities()),
            FakeSubsystem(type=OLSS_DA, element=0, capabilities=ao_caps),
            FakeSubsystem(type=OLSS_DIN, element=0, capabilities=dio_caps),
            FakeSubsystem(type=OLSS_DOUT, element=0, capabilities=dio_caps),
            FakeSubsystem(type=OLSS_CT, element=0, capabilities=ct_caps),
            FakeSubsystem(type=OLSS_QUAD, element=0, capabilities=quad_caps),
            FakeSubsystem(type=OLSS_TACH, element=0, capabilities=tach_caps),
        ],
    )


def make_fake_backend(
    *,
    boards: list[FakeBoard] | None = None,
    include_dt9805: bool = False,
    include_dt9806: bool = False,
) -> FakeDtolBackend:
    """Construct a :class:`FakeDtolBackend` with optional pre-populated boards.

    Args:
        boards: Explicit list of fake boards.  Combined with the
            ``include_*`` shortcuts if both are provided.
        include_dt9805: Prepend a default fake DT9805 to the board list.
        include_dt9806: Prepend a default fake DT9806 to the board list.

    Returns:
        A configured :class:`FakeDtolBackend`.
    """
    composed: list[FakeBoard] = []
    if include_dt9805:
        composed.append(make_fake_dt9805())
    if include_dt9806:
        composed.append(make_fake_dt9806())
    if boards:
        composed.extend(boards)
    return FakeDtolBackend(composed)
