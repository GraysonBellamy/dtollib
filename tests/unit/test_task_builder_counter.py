"""``TaskBuilder`` counter/quadrature/tachometer + retrigger dispatch tests."""

from __future__ import annotations

from dtollib import (
    CounterEdgeCount,
    CounterEdgeToEdge,
    OneShotOutput,
    PulseTrainOutput,
    QuadratureDecoder,
    Tachometer,
)
from dtollib.capi.constants import (
    OL_CTMODE_COUNT,
    OL_CTMODE_MEASURE,
    OL_CTMODE_ONESHOT,
    OL_CTMODE_QUAD,
    OL_CTMODE_RATE,
    OL_CTMODE_TACH,
    OLSS_CT,
    OLSS_QUAD,
    OLSS_TACH,
)
from dtollib.tasks.builder import TaskBuilder
from dtollib.tasks.spec import TaskSpec
from dtollib.testing import make_fake_backend


def _build(backend: object, olss: int, channel: object) -> tuple[int, list[tuple[str, object]]]:
    hdrvr = backend.initialize("DT9806(00)")  # type: ignore[attr-defined]
    hdass = backend.get_dass(hdrvr, olss, 0)  # type: ignore[attr-defined]
    caps = backend.query_capabilities(hdass)  # type: ignore[attr-defined]
    spec = TaskSpec(name="ct", board="DT9806(00)", channels=[channel])  # type: ignore[list-item]
    backend.operations.clear()  # type: ignore[attr-defined]
    TaskBuilder(backend).configure_counter(hdass, spec, caps)  # type: ignore[arg-type]
    return hdass, backend.operations  # type: ignore[attr-defined]


def _ops(operations: list[tuple[str, object]]) -> list[str]:
    return [name for name, _payload in operations]


def test_edge_count_sets_mode_first() -> None:
    backend = make_fake_backend(include_dt9806=True)
    _hdass, ops = _build(backend, OLSS_CT, CounterEdgeCount(physical_channel=0))
    names = _ops(ops)
    assert names.index("set_ct_mode") < names.index("set_gate_type")
    assert "commit" in names
    assert backend.ct_mode_of(_hdass) == OL_CTMODE_COUNT


def test_edge_count_cascade_emits_cascade() -> None:
    backend = make_fake_backend(include_dt9806=True)
    _hdass, ops = _build(backend, OLSS_CT, CounterEdgeCount(physical_channel=0, cascade=True))
    assert "set_cascade_mode" in _ops(ops)


def test_edge_to_edge_sets_measure_edges() -> None:
    backend = make_fake_backend(include_dt9806=True)
    hdass, ops = _build(backend, OLSS_CT, CounterEdgeToEdge(physical_channel=0))
    assert "set_measure_edges" in _ops(ops)
    assert backend.ct_mode_of(hdass) == OL_CTMODE_MEASURE


def test_pulse_train_sets_pulse_and_clock() -> None:
    backend = make_fake_backend(include_dt9806=True)
    hdass, ops = _build(backend, OLSS_CT, PulseTrainOutput(physical_channel=0, frequency_hz=1000.0))
    names = _ops(ops)
    assert "set_pulse" in names
    assert "set_ct_clock" in names
    assert backend.ct_mode_of(hdass) == OL_CTMODE_RATE


def test_one_shot_sets_pulse_no_clock() -> None:
    backend = make_fake_backend(include_dt9806=True)
    hdass, ops = _build(backend, OLSS_CT, OneShotOutput(physical_channel=0, pulse_width_s=1e-3))
    names = _ops(ops)
    assert "set_pulse" in names
    assert "set_ct_clock" not in names
    assert backend.ct_mode_of(hdass) == OL_CTMODE_ONESHOT


def test_quadrature_mode() -> None:
    backend = make_fake_backend(include_dt9806=True)
    hdass, _ops_unused = _build(backend, OLSS_QUAD, QuadratureDecoder(physical_channel=0))
    assert backend.ct_mode_of(hdass) == OL_CTMODE_QUAD


def test_tachometer_mode_and_edges() -> None:
    backend = make_fake_backend(include_dt9806=True)
    hdass, ops = _build(backend, OLSS_TACH, Tachometer(physical_channel=0))
    assert backend.ct_mode_of(hdass) == OL_CTMODE_TACH
    assert "set_measure_edges" in _ops(ops)


def test_retrigger_wired_into_continuous() -> None:
    """A ``Timing.retrigger`` triggers ``set_triggered_scan`` in the AI path."""
    from dtollib import AnalogInputVoltage, BufferPlan, DataFlow, ExternalDigitalStart, Timing
    from dtollib.tasks.models import RetriggerMode
    from dtollib.tasks.spec import RetriggerSpec

    backend = make_fake_backend(include_dt9806=True)
    hdrvr = backend.initialize("DT9806(00)")
    from dtollib.capi.constants import OLSS_AD

    hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
    caps = backend.query_capabilities(hdass)
    spec = TaskSpec(
        name="scan",
        board="DT9806(00)",
        data_flow=DataFlow.CONTINUOUS,
        channels=[AnalogInputVoltage(physical_channel=0)],
        timing=Timing(
            rate_hz=1000.0,
            retrigger=RetriggerSpec(
                mode=RetriggerMode.EXTRA,
                multiscan_count=4,
                source=ExternalDigitalStart(),
            ),
        ),
        buffers=BufferPlan(buffers=4, samples_per_buffer=100),
    )
    backend.operations.clear()
    TaskBuilder(backend).configure_continuous(hdass, spec, caps)
    assert "set_triggered_scan" in _ops(backend.operations)
