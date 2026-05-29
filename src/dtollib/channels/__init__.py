"""Channel-spec dataclasses — the typed input shape for ``TaskSpec.channels``.

Provides the analog-input subset (voltage + thermocouple) needed
for the DT9805 happy path, the DT9806 output surface
(:class:`AnalogOutputVoltage`, :class:`DigitalInputPort`,
:class:`DigitalOutputPort` with :class:`DigitalLine` views), and the
multi-sensor
kinds (:class:`RtdInput`, :class:`ThermistorInput`,
:class:`ResistanceInput`, :class:`CurrentInput`, :class:`IepeInput`,
:class:`StrainInput`, :class:`BridgeInput`) — all share
:class:`AnalogInputBase` and reuse its knobs.

The ``kind`` discriminator on each concrete spec drives serialisation:
:func:`channel_from_dict` reverses :meth:`ChannelSpec.to_dict` via the
:data:`_CHANNEL_KINDS` registry below.

Design reference: docs/design.md §8.2–§8.6, §18.3.
"""

from __future__ import annotations

from typing import Any

from dtollib.channels.analog_input import (
    AnalogInputBase,
    AnalogInputVoltage,
    BridgeConfiguration,
    BridgeInput,
    ChannelType,
    CjcSource,
    CouplingType,
    CurrentInput,
    Encoding,
    ExcitationSource,
    FilterType,
    IepeInput,
    ResistanceInput,
    RtdInput,
    RtdType,
    StrainExcitationSource,
    StrainGageConfiguration,
    StrainInput,
    TemperatureUnit,
    ThermistorInput,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.channels.analog_output import AnalogOutputVoltage
from dtollib.channels.base import ChannelSpec
from dtollib.channels.counter_input import (
    CounterEdgeCount,
    CounterEdgeToEdge,
    CounterFrequency,
    QuadratureDecoder,
    Tachometer,
)
from dtollib.channels.counter_output import (
    OneShotOutput,
    PulseTrainOutput,
    RepetitiveOneShotOutput,
)
from dtollib.channels.digital import (
    DigitalInputPort,
    DigitalLine,
    DigitalOutputPort,
)
from dtollib.errors import DtolValidationError, ErrorContext

__all__ = [
    "AnalogInputBase",
    "AnalogInputVoltage",
    "AnalogOutputVoltage",
    "BridgeConfiguration",
    "BridgeInput",
    "ChannelSpec",
    "ChannelType",
    "CjcSource",
    "CounterEdgeCount",
    "CounterEdgeToEdge",
    "CounterFrequency",
    "CouplingType",
    "CurrentInput",
    "DigitalInputPort",
    "DigitalLine",
    "DigitalOutputPort",
    "Encoding",
    "ExcitationSource",
    "FilterType",
    "IepeInput",
    "OneShotOutput",
    "PulseTrainOutput",
    "QuadratureDecoder",
    "RepetitiveOneShotOutput",
    "ResistanceInput",
    "RtdInput",
    "RtdType",
    "StrainExcitationSource",
    "StrainGageConfiguration",
    "StrainInput",
    "Tachometer",
    "TemperatureUnit",
    "ThermistorInput",
    "ThermocoupleInput",
    "ThermocoupleType",
    "channel_from_dict",
]


# ``kind`` discriminator → concrete spec class.  Every concrete
# :class:`ChannelSpec` subclass with a non-empty ``kind`` ClassVar is
# registered here so :func:`channel_from_dict` can reverse ``to_dict``.
_CHANNEL_KINDS: dict[str, type[ChannelSpec]] = {
    AnalogInputVoltage.kind: AnalogInputVoltage,
    ThermocoupleInput.kind: ThermocoupleInput,
    RtdInput.kind: RtdInput,
    ThermistorInput.kind: ThermistorInput,
    ResistanceInput.kind: ResistanceInput,
    CurrentInput.kind: CurrentInput,
    IepeInput.kind: IepeInput,
    StrainInput.kind: StrainInput,
    BridgeInput.kind: BridgeInput,
    AnalogOutputVoltage.kind: AnalogOutputVoltage,
    DigitalInputPort.kind: DigitalInputPort,
    DigitalOutputPort.kind: DigitalOutputPort,
    CounterEdgeCount.kind: CounterEdgeCount,
    CounterFrequency.kind: CounterFrequency,
    CounterEdgeToEdge.kind: CounterEdgeToEdge,
    QuadratureDecoder.kind: QuadratureDecoder,
    Tachometer.kind: Tachometer,
    PulseTrainOutput.kind: PulseTrainOutput,
    OneShotOutput.kind: OneShotOutput,
    RepetitiveOneShotOutput.kind: RepetitiveOneShotOutput,
}


def channel_from_dict(data: dict[str, Any]) -> ChannelSpec:
    """Reconstruct a :class:`ChannelSpec` from its :meth:`to_dict` mapping.

    Dispatches on the ``kind`` discriminator. The input is not mutated.

    Args:
        data: Mapping produced by :meth:`ChannelSpec.to_dict` — must carry
            a ``kind`` key matching a registered concrete spec.

    Returns:
        The reconstructed concrete channel spec.

    Raises:
        DtolValidationError: ``kind`` is missing or unrecognised.
    """
    fields = dict(data)
    kind = fields.pop("kind", None)
    if kind is None:
        raise DtolValidationError(
            "channel_from_dict: mapping has no 'kind' discriminator",
            context=ErrorContext(operation="channel_from_dict"),
        )
    cls = _CHANNEL_KINDS.get(kind)
    if cls is None:
        raise DtolValidationError(
            f"channel_from_dict: unknown channel kind {kind!r}; "
            f"known kinds: {sorted(_CHANNEL_KINDS)}",
            context=ErrorContext(operation="channel_from_dict", extra={"kind": kind}),
        )
    return cls(**fields)
