"""Typed exception hierarchy for :mod:`dtollib`.

Every library exception inherits from :class:`DtolError` and carries a
structured :class:`ErrorContext` describing the failing operation.

The full hierarchy from ``docs/design.md`` §17.3 is declared here up front
even though not all of these are raised yet. Forward-defining the
hierarchy now means later work doesn't need to add error classes
that ecosystem consumers depend on subclass-testing against.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dtollib.tasks.models import SubsystemType


__all__ = [
    "DtolBackendError",
    "DtolBufferOverrunError",
    "DtolBufferUnderrunError",
    "DtolCapabilityError",
    "DtolCapiError",
    "DtolConfigurationError",
    "DtolConfirmationRequiredError",
    "DtolConnectionError",
    "DtolDependencyError",
    "DtolError",
    "DtolReadError",
    "DtolResourceError",
    "DtolSinkDependencyError",
    "DtolSinkError",
    "DtolSinkSchemaError",
    "DtolSinkWriteError",
    "DtolTaskStateError",
    "DtolTimeoutError",
    "DtolTransientError",
    "DtolTriggerError",
    "DtolValidationError",
    "DtolWriteError",
    "ErrorContext",
]


_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


def _empty_extra() -> Mapping[str, Any]:
    return _EMPTY_EXTRA


# ``SdkEventKind`` is the continuous-AI enum naming SDK notification messages
# (BUFFER_DONE, OVERRUN_ERROR, etc.). ``ErrorContext`` carries it on
# async-error wraps. It is stored as ``object | None`` so the
# enum can be introduced without breaking the dataclass shape;
# the annotation tightens to ``SdkEventKind | None`` once the enum lands.
# Tracked in ``docs/decisions.md``.
_SdkEventKindHolder = object


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`DtolError`.

    Field shape matches docs/design.md §17.2. Cross-instrument log readers join
    these on ``(task_name, board, channel_name, ...)`` against the sibling
    libraries' equivalents.

    Attributes:
        task_name: ``TaskSpec.name`` of the task at fault.
        board: DT-Open Layers board name (e.g. ``"DT9805(00)"``).
        subsystem_type: Failing subsystem kind, if known.
        element: Subsystem element index, if relevant.
        channel_name: Display name of the at-fault channel.
        channel: Physical-channel number (the SDK uses 0-based int).
        operation: Logical operation name (``"olDaConfig"``,
            ``"read_block"``, ``"poll"``, ...).
        ecode: Raw ``ECODE`` / ``OLSTATUS`` value from the SDK call.
        ecode_source: Which DLL emitted the code (``"oldaapi"`` vs
            ``"olmem"``) — controls which error-string function decodes
            ``ecode``.
        ecode_message: Decoded SDK error string.
        sdk_event_kind: SDK notification message kind on async-error wraps
            (typed as ``object | None`` until the enum lands; see module
            docstring).
        extra: Free-form additional context.
    """

    task_name: str | None = None
    board: str | None = None
    subsystem_type: SubsystemType | None = None
    element: int | None = None
    channel_name: str | None = None
    channel: int | None = None
    operation: str | None = None
    ecode: int | None = None
    ecode_source: Literal["oldaapi", "olmem"] | None = None
    ecode_message: str | None = None
    sdk_event_kind: _SdkEventKindHolder | None = None
    extra: Mapping[str, Any] = field(default_factory=_empty_extra)

    def __post_init__(self) -> None:
        if not isinstance(self.extra, MappingProxyType):
            object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    def merged(self, **updates: Any) -> Self:
        """Return a new context with ``updates`` overlaid. Unknown keys go to ``extra``."""
        known: dict[str, Any] = {}
        extra_updates: dict[str, Any] = {}
        for key, value in updates.items():
            if key in _CONTEXT_KNOWN_FIELDS:
                known[key] = value
            else:
                extra_updates[key] = value

        new_extra: Mapping[str, Any] = (
            MappingProxyType({**self.extra, **extra_updates}) if extra_updates else self.extra
        )
        return replace(self, **known, extra=new_extra)


_CONTEXT_KNOWN_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(ErrorContext) if f.name != "extra"
)


_EMPTY_CONTEXT = ErrorContext()


class DtolError(Exception):
    """Base class for every :mod:`dtollib` exception.

    Carries a typed :class:`ErrorContext`. The ``message`` is the
    human-readable summary; the context is the machine-readable detail.
    """

    context: ErrorContext

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        """Initialise with a human-readable message and optional context.

        Args:
            message: Short, human-readable summary suitable for logs.
            context: Structured fields about the failing operation. ``None``
                yields an empty :class:`ErrorContext`.
        """
        super().__init__(message)
        self.context = context if context is not None else _EMPTY_CONTEXT

    def with_context(self, **updates: Any) -> Self:
        """Return a copy of this error with its context updated.

        Useful when an inner layer raises and an outer layer wants to enrich
        the context (for instance adding ``task_name`` or ``operation``).
        """
        cls = type(self)
        new = cls.__new__(cls)
        new.args = self.args
        try:
            new.__dict__.update(self.__dict__)
        except AttributeError:  # pragma: no cover — no slotted subclass today
            for slot in getattr(cls, "__slots__", ()):
                if hasattr(self, slot):
                    object.__setattr__(new, slot, getattr(self, slot))
        new.context = self.context.merged(**updates)
        new.__cause__ = self.__cause__
        new.__context__ = self.__context__
        new.__traceback__ = self.__traceback__
        return new

    def __str__(self) -> str:
        base = super().__str__()
        ctx = self.context
        bits: list[str] = []
        if ctx.task_name is not None:
            bits.append(f"task={ctx.task_name}")
        if ctx.board is not None:
            bits.append(f"board={ctx.board}")
        if ctx.channel_name is not None:
            bits.append(f"channel={ctx.channel_name}")
        elif ctx.channel is not None:
            bits.append(f"channel={ctx.channel}")
        if ctx.operation is not None:
            bits.append(f"op={ctx.operation}")
        if ctx.ecode is not None:
            src = ctx.ecode_source or "sdk"
            bits.append(f"ecode={ctx.ecode}({src})")
        if ctx.extra:
            bits.append(f"extra={dict(ctx.extra)!r}")
        return f"{base} [{', '.join(bits)}]" if bits else base


# --- Configuration / validation ---------------------------------------------


class DtolConfigurationError(DtolError):
    """Configuration-level error (bad spec, missing required field, ...)."""


class DtolValidationError(DtolError):
    """Client-side validation failed before any SDK call.

    Raised by ``__post_init__`` on spec dataclasses (e.g. TC range outside
    the type's operating envelope, MULTI_SENSOR ordering violation in the
    fake backend, mixing channel kinds in one ``TaskSpec.channels``).
    """


class DtolConfirmationRequiredError(DtolError):
    """A safety-gated operation was attempted without ``confirm=True``.

    Raised by AO / DO / CO writes outside their ``safe_min``/``safe_max``
    band, by pulse-train start, by auto-calibration, and by any operation
    that mutates persistent device state. Matches the ecosystem
    ``ConfirmationRequiredError`` convention shared with :mod:`watlowlib`
    and :mod:`sartoriuslib`.
    """


# --- Lifecycle --------------------------------------------------------------


class DtolTaskStateError(DtolError):
    """Operation invalid for the task's current lifecycle state.

    Raised, for example, by :meth:`DtolSession.poll` when the task is
    ``CONFIGURED_FOR_CONTINUOUS`` and ``RUNNING`` — two consumers on the
    same SDK buffer queue would race. Or by ``register_notification`` after
    ``olDaConfig`` (the §12.3.2 invariant).
    """


# --- I/O --------------------------------------------------------------------


class DtolReadError(DtolError):
    """A read against the underlying SDK failed.

    Wraps ``olDaGetSingleValue*``, ``olDaGetBuffer``, ``olDaCopyFromBuffer``
    failures that are not classified into a more specific subclass.
    """


class DtolWriteError(DtolError):
    """A write against the underlying SDK failed.

    Raised by :meth:`DtolSession.write` when the backend rejects the write.
    Out-of-range values fail earlier as :class:`DtolValidationError`.
    """


class DtolTimeoutError(DtolError):
    """An SDK read or write exceeded its configured timeout."""


# --- Transient / connection -------------------------------------------------


class DtolTransientError(DtolError):
    """A driver-layer error that is safe to retry without rebuilding the task.

    Surfaced when an ``oldaapi`` / ``olmem`` call fails with a code in the
    retry-safe set (for example a buffer-done window that slid just ahead of
    the drainer). Consumers under :attr:`ErrorPolicy.RETURN` may re-attempt
    the operation. Distinct from :class:`DtolBufferOverrunError`, which
    signals the consumer cannot keep up at the configured sample rate.
    """


class DtolConnectionError(DtolError):
    """Communication with the DT-Open Layers board was lost or unavailable.

    Aligns with the ecosystem ``ConnectionError`` convention (matching
    :class:`watlowlib.WatlowConnectionError`,
    :class:`alicatlib.AlicatConnectionError`,
    :class:`sartoriuslib.SartoriusConnectionError`, and
    :class:`nidaqlib.NIDaqConnectionError`). Raised when a USB DT module is
    unplugged or powered down mid-session, or when the driver handle is
    invalidated — the board is gone, as opposed to a transient, retry-safe
    blip (:class:`DtolTransientError`).
    """


# --- Resource conflicts -----------------------------------------------------


class DtolResourceError(DtolError):
    """A resource conflict was detected.

    Examples: HDASS already reserved, simultaneous-start pool members
    mismatch. Best-effort signal — the SDK is the final authority.
    Raised by :meth:`DtolManager.add` when the new task's board +
    element overlap with one already managed.
    """


# --- Capability mismatch ----------------------------------------------------


class DtolCapabilityError(DtolError):
    """Requested feature not supported per the live ``CapabilitySet``.

    Examples: configuring continuous mode on a subsystem that reports only
    ``OLSSC_SUP_SINGLEVALUE``; requesting ``read_inprocess`` on a board
    without the inprocess-flush capability.
    """


# --- Raw SDK errors ---------------------------------------------------------


class DtolCapiError(DtolError):
    """Raw ``ECODE`` from ``oldaapi`` / ``olmem`` failed.

    Parent for the buffer / trigger flavours below. Specific
    ``OLSTATUS`` ranges and codes map into the more precise subclasses;
    everything else lands here.
    """


class DtolBufferOverrunError(DtolCapiError):
    """``OLDA_WM_OVERRUN_ERROR`` — continuous AI driver fell behind.

    Signals that the consumer + buffer pool can't keep up with the
    configured sample rate. ``AcquisitionSummary.overruns_observed > 0``
    after a run is the soft signal; this exception is the hard one.
    Increase ``BufferPlan.buffers`` or ``BufferPlan.samples_per_buffer``.
    """


class DtolBufferUnderrunError(DtolCapiError):
    """``OLDA_WM_UNDERRUN_ERROR`` — continuous AO consumer ran dry."""


class DtolTriggerError(DtolCapiError):
    """``OLDA_WM_TRIGGER_ERROR`` — trigger condition could not be configured."""


# --- Backend / dependency ---------------------------------------------------


class DtolBackendError(DtolError):
    """The backend rejected an operation or surfaced a generic SDK failure.

    Used when the failure is not a clean fit for the more specific
    subclasses. Wraps the originating ``ctypes`` exception via
    ``__cause__`` when available.
    """


class DtolDependencyError(DtolError):
    """A required dependency is unavailable.

    Most common cases: the DT-Open Layers SDK is not installed (no
    ``oldaapi*.dll`` / ``olmem*.dll`` on the resolution chain), bitness
    mismatch (32-bit Python attempting to load ``oldaapi64.dll``), or
    running on a non-Windows platform.
    """


# --- Sinks ------------------------------------------------------------------


class DtolSinkError(DtolError):
    """Base class for sink-layer failures."""


class DtolSinkSchemaError(DtolSinkError):
    """A sink rejected an input record's shape.

    Most commonly raised by row-oriented sinks (``CsvSink``, ``JsonlSink``)
    when handed a :class:`~dtollib.tasks.DaqBlock` without
    ``accept_blocks=True`` — silently scalarising would surprise users
    with 1-GB CSV files at 10 kHz × 8 channels.
    """


class DtolSinkWriteError(DtolSinkError):
    """A sink failed while writing a batch (file I/O, DB error, ...)."""


class DtolSinkDependencyError(DtolSinkError):
    """A sink's optional dependency (``pyarrow``, ``asyncpg``, ...) is missing."""
