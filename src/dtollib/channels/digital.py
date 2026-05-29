"""Digital-I/O channel specs for the DT9805/DT9806 DIN / DOUT subsystems.

DT-Open Layers models digital I/O as **ports**, not individual lines: each
direction (DIN, DOUT) exposes one or more *ports*, and a port is the SDK
"channel" addressed by :meth:`olDaPutSingleValue` / :meth:`olDaGetSingleValue`.
A single put/get reads or writes the **whole port byte**; the individual lines
are the bits of that byte. On the DT9805/06 there is exactly one 8-bit port per
direction (``num_channels=1``, ``OLSSC_RESOLUTION=8``) — its 8 bits drive the 8
relays.

So the channel you declare is a :class:`DigitalOutputPort` / :class:`DigitalInputPort`
whose ``physical_channel`` is the **port index**. Per-line ergonomics are kept via
:class:`DigitalLine` views — sugar that resolves to ``(port, bit)``. The session
packs all touched bits into one byte and issues a single port write; reads return
the raw byte *and* the decomposed per-line bools.

Design reference: docs/design.md §6, §18.1; docs/decisions.md (DIO port model).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from dtollib.channels.base import ChannelSpec
from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import IOType

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["DigitalInputPort", "DigitalLine", "DigitalOutputPort"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalLine:
    """A single-bit view into a digital port — sugar, not an SDK channel.

    A line is addressed in :meth:`DtolSession.write` / :class:`DaqReading`
    by its key: ``name`` when set, else ``f"{port.display_name}.line{bit}"``.

    Attributes:
        bit: Zero-based bit index within the owning port.
        name: Display key for writes/reads. ``None`` → derived from the
            port name and bit index.
        safe_value: The level this line should hold when not explicitly
            driven (informational; surfaced to operators and sinks).
        requires_confirm: Per-line override of the port's confirm gate.
            ``None`` inherits :attr:`DigitalOutputPort.requires_confirm`.
    """

    bit: int
    name: str | None = None
    safe_value: bool | None = None
    requires_confirm: bool | None = None

    def __post_init__(self) -> None:
        if self.bit < 0:
            raise DtolValidationError(
                f"DigitalLine: bit must be >= 0, got {self.bit}",
                context=ErrorContext(operation="DigitalLine.__post_init__"),
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping (reversed by :meth:`from_dict`)."""
        return {
            "bit": self.bit,
            "name": self.name,
            "safe_value": self.safe_value,
            "requires_confirm": self.requires_confirm,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DigitalLine:
        """Reconstruct a :class:`DigitalLine` from :meth:`to_dict`."""
        return cls(
            bit=data["bit"],
            name=data.get("name"),
            safe_value=data.get("safe_value"),
            requires_confirm=data.get("requires_confirm"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _DigitalPort(ChannelSpec):
    """Shared shape for DIN / DOUT ports.

    Attributes:
        width: Number of lines (bits) in the port. ``None`` → resolved
            from ``OLSSC_RESOLUTION`` at configure time. When set, it is
            cross-checked against the live resolution and must match.
        lines: Optional named per-bit views (:class:`DigitalLine`).
    """

    width: int | None = None
    lines: tuple[DigitalLine, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # ChannelSpec wraps metadata immutably.
        # Explicit two-arg super(): @dataclass(slots=True) recreates the class,
        # which breaks zero-arg super() on Python < 3.14 (CPython gh-90562).
        # DigitalOutputPort/DigitalInputPort inherit this method; ``self`` is a
        # subtype of the recreated _DigitalPort, so the two-arg form is correct.
        super(_DigitalPort, self).__post_init__()
        # Normalise lines: accept DigitalLine or its to_dict mapping (the
        # latter arrives via channel_from_dict round-trips).
        raw_lines: tuple[Any, ...] = self.lines
        normalised = tuple(
            line if isinstance(line, DigitalLine) else DigitalLine.from_dict(line)
            for line in raw_lines
        )
        object.__setattr__(self, "lines", normalised)

        if self.width is not None and self.width <= 0:
            raise DtolValidationError(
                f"{type(self).__name__}: width must be positive, got {self.width}",
                context=ErrorContext(
                    operation=f"{type(self).__name__}.__post_init__",
                    channel=self.physical_channel,
                ),
            )

        seen: set[int] = set()
        for line in normalised:
            if line.bit in seen:
                raise DtolValidationError(
                    f"{type(self).__name__}: duplicate line bit {line.bit} on "
                    f"port {self.display_name}",
                    context=ErrorContext(
                        operation=f"{type(self).__name__}.__post_init__",
                        channel=self.physical_channel,
                    ),
                )
            seen.add(line.bit)
            if self.width is not None and line.bit >= self.width:
                raise DtolValidationError(
                    f"{type(self).__name__}: line bit {line.bit} is outside the "
                    f"{self.width}-bit port {self.display_name}",
                    context=ErrorContext(
                        operation=f"{type(self).__name__}.__post_init__",
                        channel=self.physical_channel,
                    ),
                )

    def line_key(self, line: DigitalLine) -> str:
        """Write/read key for ``line`` — its name, else ``"<port>.line<bit>"``."""
        return line.name if line.name is not None else f"{self.display_name}.line{line.bit}"

    def line_views(self) -> dict[str, DigitalLine]:
        """Map each declared line's key → its :class:`DigitalLine`."""
        return {self.line_key(line): line for line in self.lines}

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping; serialises nested lines."""
        # Two-arg super() — slots=True class recreation breaks zero-arg super()
        # on Python < 3.14 (CPython gh-90562); see __post_init__ above.
        data = super(_DigitalPort, self).to_dict()
        data["lines"] = [line.to_dict() for line in self.lines]
        return data


@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalOutputPort(_DigitalPort):
    """A digital-output port on the DOUT subsystem.

    ``physical_channel`` is the port index (0 on the DT9805/06). A write
    targets the whole port byte; partial per-line writes are merged into a
    per-port shadow register so untouched lines are preserved
    (docs/design.md §18.1).

    Attributes:
        safe_value: Full-port byte to hold when not explicitly driven; it
            also seeds the shadow register at configure time. ``None`` → 0.
        requires_confirm: Port-level confirm gate. A per-line
            :attr:`DigitalLine.requires_confirm` overrides it for that line.
    """

    kind: ClassVar[str] = "digital_output_port"

    safe_value: int | None = None
    requires_confirm: bool = True

    def kind_to_multi_sensor_type(self) -> IOType:
        """``DigitalOutputPort`` → :attr:`IOType.DIGITAL_OUTPUT`."""
        return IOType.DIGITAL_OUTPUT


@dataclass(frozen=True, slots=True, kw_only=True)
class DigitalInputPort(_DigitalPort):
    """A digital-input port on the DIN subsystem.

    Read-only: :meth:`DtolSession.poll` surfaces the raw port byte under the
    port name plus one bool per declared :class:`DigitalLine`.
    """

    kind: ClassVar[str] = "digital_input_port"

    def kind_to_multi_sensor_type(self) -> IOType:
        """``DigitalInputPort`` → :attr:`IOType.DIGITAL_INPUT`."""
        return IOType.DIGITAL_INPUT
