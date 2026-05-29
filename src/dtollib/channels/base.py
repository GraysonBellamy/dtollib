"""``ChannelSpec`` — base class for every typed channel input.

Every concrete channel kind (voltage, thermocouple, RTD, ...) inherits
from :class:`ChannelSpec`. The class is frozen, slotted, and keyword-only
across the public API to avoid the dataclass-inheritance trap where a
defaulted parent field precedes a required subclass field.

The ``kind`` ClassVar serialises the channel subclass when a
:class:`~dtollib.tasks.TaskSpec` round-trips through ``to_dict`` /
``from_dict``. Each concrete subclass overrides it with a unique
string.

Design reference: docs/design.md §8.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dtollib.tasks.models import IOType


__all__ = ["ChannelSpec"]


def _empty_metadata() -> Mapping[str, str | int | float | bool]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelSpec:
    """Base class for every channel specification.

    Attributes:
        physical_channel: Zero-based channel index on the subsystem.
            The DataAcq SDK uses bare integers (not ``"Dev1/ai0"``-style
            strings).
        name: Display name for logs and sink columns. Falls back to
            ``f"ch{physical_channel}"`` at the boundary if omitted.
        unit: Display unit (informational; e.g. ``"V"`` or ``"degC"``).
        metadata: Free-form per-channel metadata. Propagated to
            :class:`~dtollib.tasks.DaqReading.metadata` / sink rows.
    """

    physical_channel: int
    name: str | None = None
    unit: str | None = None
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)

    # Class-level discriminator. Concrete subclasses override.
    # ``ClassVar`` ensures it's not treated as a dataclass field by
    # ``dataclasses.fields``.
    kind: ClassVar[str] = ""

    def __post_init__(self) -> None:
        """Wrap mutable ``metadata`` mappings to enforce immutability."""
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def display_name(self) -> str:
        """Effective display name — uses ``name`` if set, else ``f"ch{n}"``."""
        return self.name if self.name is not None else f"ch{self.physical_channel}"

    def kind_to_multi_sensor_type(self) -> IOType:
        """Return the :class:`IOType` discriminator for a MULTI_SENSOR channel.

        Called by the :class:`~dtollib.tasks.TaskBuilder` immediately
        before any per-type setter on a ``MULTI_SENSOR`` channel
        (docs/design.md §8.5a). Subclasses override; the base raises.

        Raises:
            NotImplementedError: This subclass does not know how to
                re-type a multi-sensor channel.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot configure a MULTI_SENSOR channel; "
            "override kind_to_multi_sensor_type()."
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping with ``kind`` discriminator embedded.

        Built field-by-field (not via :func:`dataclasses.asdict`, which
        deep-copies and cannot pickle the ``MappingProxyType`` metadata).
        :func:`~dtollib.channels.channel_from_dict` reverses this.
        """
        from dataclasses import fields  # noqa: PLC0415

        data: dict[str, Any] = {f.name: getattr(self, f.name) for f in fields(self)}
        data["kind"] = type(self).kind
        # MappingProxyType is not directly JSON serialisable; cast.
        data["metadata"] = dict(self.metadata)
        return data
