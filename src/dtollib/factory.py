"""``open_device`` factory — the canonical entry point for ad-hoc sessions.

For multi-task / multi-device coordination, use :class:`DtolManager`
instead.

Design reference: docs/design.md §9.3.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from dtollib.channels.analog_output import AnalogOutputVoltage
from dtollib.channels.digital import DigitalOutputPort
from dtollib.errors import DtolConfirmationRequiredError, ErrorContext
from dtollib.tasks.session import DtolSession

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend
    from dtollib.tasks.spec import TaskSpec


__all__ = ["open_device"]


async def open_device(
    spec: TaskSpec,
    *,
    backend: DtolBackend | None = None,
    timeout: float = 10.0,  # noqa: ASYNC109 - public API name, not an asyncio timeout.
    autostart: bool = True,
    confirm_start: bool = False,
) -> DtolSession:
    """Open a :class:`DtolSession` for ``spec``.

    The session is returned already :meth:`prepared <DtolSession.prepare>`
    and :meth:`committed <DtolSession.commit>`.  When ``autostart`` is
    true (the default), it is also :meth:`started <DtolSession.start>`.

    Args:
        spec: Task specification.
        backend: Backend to use.  ``None`` instantiates a fresh
            :class:`~dtollib.backend.dataacq.DataAcqBackend` (the real
            SDK path).  Tests inject a
            :class:`~dtollib.backend.fake.FakeDtolBackend`.
        timeout: Default per-call timeout.
        autostart: Whether to start the subsystem before returning.
            Single-value tasks honour this as a literal pre-start;
            continuous callers that need to register a notification
            callback before ``olDaConfig`` must pass ``autostart=False``.
        confirm_start: Safety gate (docs/design.md §18.1). Autostarting a
            task that drives ``requires_confirm`` output channels needs
            ``confirm_start=True``; otherwise
            :class:`~dtollib.errors.DtolConfirmationRequiredError` is raised
            before the subsystem is started.

    Returns:
        A configured :class:`DtolSession`.  Use it as an async context
        manager so :meth:`DtolSession.close` runs in the cleanup path.

    Raises:
        DtolConfirmationRequiredError: ``autostart`` would start an output
            task containing a ``requires_confirm`` channel without
            ``confirm_start=True``.
    """
    if autostart and not confirm_start:
        _reject_unconfirmed_output_autostart(spec)

    if backend is None:
        from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

        backend = DataAcqBackend()

    session = DtolSession(spec, backend, timeout=timeout)
    try:
        await session.configure()
        if autostart:
            await session.start()
    except BaseException:
        # Release any HDASS/HDRVR acquired during prepare() before
        # propagating — leaving them open leaks the subsystem.
        with contextlib.suppress(Exception):
            await session.close()
        raise
    return session


def _reject_unconfirmed_output_autostart(spec: TaskSpec) -> None:
    """Raise if autostarting ``spec`` would drive a confirm-required output.

    The safety posture (docs/design.md §18.1): starting an output subsystem
    that contains a ``requires_confirm`` channel is a gated operation. The
    caller opts in with ``confirm_start=True`` (or opens with
    ``autostart=False`` and writes explicitly via
    :meth:`DtolSession.write`).
    """
    for channel in spec.channels:
        if (
            isinstance(channel, AnalogOutputVoltage | DigitalOutputPort)
            and channel.requires_confirm
        ):
            raise DtolConfirmationRequiredError(
                f"open_device: autostart of task {spec.name!r} drives output channel "
                f"{channel.display_name} (requires_confirm=True) — pass confirm_start=True "
                "or open with autostart=False and call session.write(..., confirm=True)",
                context=ErrorContext(
                    operation="open_device",
                    task_name=spec.name,
                    channel=channel.physical_channel,
                    channel_name=channel.name,
                ),
            )
