"""Hardware acceptance — discovery + diagnostics smoke.

Proves the C boundary works end-to-end on real hardware: ``find_devices()``
enumerates the attached board(s), ``find_subsystems()`` reports the A/D
subsystem, the capability snapshot reflects the DT9805/06 reality (no firmware
TC linearisation, multi-sensor A/D), and the ``dtol-discover`` CLI emits
parseable JSON. Read-only — no acquisition, no state change.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout

import pytest

from dtollib import AnalogInputVoltage, TaskSpec, find_devices, find_subsystems, open_device
from dtollib.cli.discover import main as discover_main
from dtollib.tasks.models import SubsystemType

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_HARDWARE_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_HARDWARE_TESTS=1 with a DT9805/06 attached",
    ),
    pytest.mark.anyio,
]

# Override via env if the bench board differs from the default WS rig.
_BOARD = os.environ.get("DTOLLIB_HW_BOARD", "DT9805(00)")


async def test_find_devices_lists_attached_board() -> None:
    """At least one board is enumerated and the bench board is among them."""
    boards = await find_devices()
    assert boards, "find_devices() returned no boards — SDK/driver not seeing hardware"
    names = {b.name for b in boards}
    models = {b.model for b in boards}
    assert _BOARD in names, f"{_BOARD} not in enumerated boards {sorted(names)}"
    assert any(m.startswith(("DT9805", "DT9806")) for m in models), (
        f"no DT9805/06 model among {sorted(models)}"
    )


async def test_find_subsystems_reports_analog_input() -> None:
    """The bench board exposes an analog-input subsystem."""
    boards = await find_devices()
    board = next(b for b in boards if b.name == _BOARD)
    subsystems = await find_subsystems(board)
    kinds = {s.type for s in subsystems}
    assert SubsystemType.ANALOG_INPUT in kinds, (
        f"no analog-input subsystem on {_BOARD}; found {sorted(k.value for k in kinds)}"
    )


async def test_capabilities_reflect_dt980x_reality() -> None:
    """The A/D capability snapshot matches the bench-verified DT9805/06 envelope."""
    spec = TaskSpec(
        name="hw_caps",
        board=_BOARD,
        channels=[AnalogInputVoltage(physical_channel=0, name="ch0", gain=1.0)],
    )
    async with await open_device(spec, autostart=False) as session:
        caps = session.capabilities
        # DT9805/06: multi-sensor A/D that does NOT return engineering-unit
        # floats and does NOT linearise thermocouples in firmware
        # (docs/decisions.md; the wrapper applies NIST ITS-90 in software).
        assert caps.supports_multisensor is True
        assert caps.returns_floats is False
        assert caps.supports_singlevalue is True


def test_dtol_discover_json_parses() -> None:
    """``dtol-discover --json`` exits 0 and emits a parseable JSON document."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = discover_main(["--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert isinstance(payload, (list, dict))
