"""``DtolManager`` — orchestrates many tasks across one or more DT boards.

Per-board lock + HDRVR ref-count sharing + ``poll(names=...)`` fan-out
aggregation + ``start_synchronized`` for coordinated single-board starts.
Continuous acquisition is owned by :func:`dtollib.streaming.record`, which
takes a single :class:`DtolSession`; the manager does not aggregate streams.

Design reference: docs/design.md §16.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypeVar

import anyio
import anyio.to_thread  # pyright: ignore[reportMissingImports]

from dtollib.errors import (
    DtolError,
    DtolResourceError,
    DtolValidationError,
    ErrorContext,
)
from dtollib.streaming._types import ErrorPolicy
from dtollib.tasks.session import DtolSession

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from types import TracebackType

    from dtollib.backend.base import DtolBackend
    from dtollib.tasks.models import DaqReading
    from dtollib.tasks.spec import TaskSpec


__all__ = ["DeviceResult", "DtolManager", "ErrorPolicy"]


T = TypeVar("T")

# ``ErrorPolicy`` is the single shared ecosystem enum (defined in
# ``dtollib.streaming._types`` and re-exported here for ``from
# dtollib.manager import ErrorPolicy``). At the manager level the members
# read as: ``RAISE`` (default) — the first failure cancels and propagates;
# ``RETURN`` — each :class:`DeviceResult` carries the per-device outcome
# (value or error); ``SKIP`` — failures are dropped from the result map
# with a WARNING log entry.


@dataclass(frozen=True, slots=True)
class DeviceResult[T]:
    """Outcome of a per-device call under :attr:`ErrorPolicy.RETURN`.

    Exactly one of :attr:`value` / :attr:`error` is populated.
    """

    value: T | None
    error: DtolError | None

    @property
    def ok(self) -> bool:
        """``True`` when the call succeeded."""
        return self.error is None


@dataclass(slots=True)
class _ManagedTask:
    """Per-task bookkeeping inside the manager."""

    name: str
    spec: TaskSpec
    session: DtolSession
    backend: DtolBackend


class DtolManager:
    """Orchestrate many :class:`DtolSession`s.

    - :meth:`add` / :meth:`remove` / :meth:`get` — registry.
    - :meth:`poll(names)` — fan-out polling that returns one
      :class:`DeviceResult` per requested task.
    - :meth:`start(names)` / :meth:`stop(names)` — bulk lifecycle.
    - :meth:`start_synchronized(names)` — coordinated single-board start
      via the SDK simultaneous-start primitives.

    Thread safety: one ``anyio.Lock`` per board (HDRVR), shared by
    every session against that board.  Concurrent polls across
    different boards run in parallel; same-board concurrency
    serialises.  See docs/design.md §16.3.
    """

    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None:
        self.error_policy = error_policy
        self._tasks: dict[str, _ManagedTask] = {}
        self._board_locks: dict[str, anyio.Lock] = {}
        # Subsystem reservations: prevents two tasks taking the same HDASS.
        self._reservations: set[tuple[str, int, int]] = set()
        # Owned backends — closed on manager exit.
        self._owned_backends: list[DtolBackend] = []

    # ---- Lifecycle (context manager) -------------------------------------

    async def __aenter__(self) -> Self:
        """Trivial context entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close every managed session on exit."""
        del exc_type, exc, tb
        await self.aclose()

    async def aclose(self) -> None:
        """Close every managed session."""
        for task in list(self._tasks.values()):
            await task.session.close()
        self._tasks.clear()
        self._reservations.clear()
        self._board_locks.clear()
        self._owned_backends.clear()

    # ---- Registry --------------------------------------------------------

    async def add(
        self,
        name: str,
        spec: TaskSpec,
        *,
        backend: DtolBackend | None = None,
    ) -> DtolSession:
        """Register a task by ``name``; reserve its subsystem; return its session.

        Args:
            name: Manager-level identifier; becomes the join key in
                aggregated results.  Must be unique.
            spec: The :class:`TaskSpec`.
            backend: Optional explicit backend.  ``None`` creates a
                fresh :class:`~dtollib.backend.dataacq.DataAcqBackend`.

        Returns:
            A :class:`DtolSession` already in the
            ``CONFIGURED_FOR_SINGLE_VALUE`` state (via the session's
            :meth:`configure`).

        Raises:
            DtolValidationError: A task with ``name`` already exists.
            DtolResourceError: The ``(board, subsystem_type, element)``
                triple is already reserved by another managed task.
        """
        if name in self._tasks:
            raise DtolValidationError(
                f"DtolManager: task name {name!r} already in use",
                context=ErrorContext(
                    operation="DtolManager.add",
                    task_name=name,
                ),
            )

        if backend is None:
            from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

            backend = DataAcqBackend()
            self._owned_backends.append(backend)

        session = DtolSession(spec, backend)
        await session.prepare()

        board_name = session.board_name
        subsys_type = spec.subsystem_type or spec.infer_subsystem_type()
        reservation = (board_name, hash(subsys_type), spec.element)
        if reservation in self._reservations:
            # Conflict: roll back the prepare.
            await session.close()
            raise DtolResourceError(
                f"DtolManager.add({name!r}): subsystem "
                f"({board_name}, {subsys_type.value}, element={spec.element}) "
                "is already reserved by another managed task.",
                context=ErrorContext(
                    operation="DtolManager.add",
                    task_name=name,
                    board=board_name,
                    subsystem_type=subsys_type,
                    element=spec.element,
                ),
            )

        await session.commit()

        self._reservations.add(reservation)
        self._board_locks.setdefault(board_name, anyio.Lock())
        self._tasks[name] = _ManagedTask(name=name, spec=spec, session=session, backend=backend)
        return session

    async def remove(self, name: str) -> None:
        """Close and unregister the named task."""
        task = self._tasks.pop(name, None)
        if task is None:
            return
        board = task.session.board_name
        subsys_type = task.spec.subsystem_type or task.spec.infer_subsystem_type()
        self._reservations.discard((board, hash(subsys_type), task.spec.element))
        await task.session.close()

    def get(self, name: str) -> DtolSession:
        """Return the session registered under ``name``."""
        task = self._tasks.get(name)
        if task is None:
            raise DtolValidationError(
                f"DtolManager: unknown task {name!r}",
                context=ErrorContext(operation="DtolManager.get", task_name=name),
            )
        return task.session

    @property
    def names(self) -> tuple[str, ...]:
        """Registered task names in insertion order."""
        return tuple(self._tasks)

    # ---- Bulk lifecycle --------------------------------------------------

    def _resolve_names(self, names: Sequence[str] | None) -> list[str]:
        if names is None:
            return list(self._tasks.keys())
        unknown = [n for n in names if n not in self._tasks]
        if unknown:
            raise DtolValidationError(
                f"DtolManager: unknown task names {unknown!r}",
                context=ErrorContext(operation="DtolManager._resolve_names"),
            )
        return list(names)

    async def start(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]:
        """Start each named task.  Aggregates per-task outcomes."""
        return await self._fan_out(
            self._resolve_names(names),
            self._call_start,
        )

    async def stop(self, names: Sequence[str] | None = None) -> Mapping[str, DeviceResult[None]]:
        """Stop each named task.  Aggregates per-task outcomes."""
        return await self._fan_out(
            self._resolve_names(names),
            self._call_stop,
        )

    async def poll(
        self, names: Sequence[str] | None = None
    ) -> Mapping[str, DeviceResult[DaqReading]]:
        """Poll each named task; aggregate :class:`DaqReading` results.

        Same-board polls serialise on the per-board lock; cross-board
        polls run in parallel via :class:`anyio.create_task_group`.
        """
        return await self._fan_out(
            self._resolve_names(names),
            self._call_poll,
        )

    async def start_synchronized(self, names: Sequence[str]) -> None:
        """Start the named tasks together via the SDK simultaneous-start primitives.

        Runs the four-step DT-Open Layers sequence (docs/design.md §16.2):
        ``olDaGetSSList`` → ``olDaPutDassToSSList`` × N →
        ``olDaSimultaneousPreStart`` → ``olDaSimultaneousStart``, then always
        ``olDaReleaseSSList``. The first samples of every named subsystem then
        begin within one sample period of each other.

        Scope is **single-board**: every named task must target the same
        board and share one backend instance (so they share the HDRVR
        namespace the SS-list is built from). Cross-board sync-bus
        coordination is not supported. Each task must already be committed —
        the manager commits during :meth:`add`, so single-value and counter
        tasks satisfy this directly; continuous-AI tasks must complete their
        ``record()`` register→queue→commit sequence (with ``autostart=False``)
        before calling this.

        Args:
            names: Task names to start together. Must be non-empty and all on
                one board / one backend.

        Raises:
            DtolValidationError: Unknown name, empty list, or tasks span
                multiple boards or backends.
        """
        resolved = self._resolve_names(list(names))
        if not resolved:
            raise DtolValidationError(
                "DtolManager.start_synchronized: at least one task name is required",
                context=ErrorContext(operation="DtolManager.start_synchronized"),
            )

        managed = [self._tasks[n] for n in resolved]
        sessions = [m.session for m in managed]

        boards = {s.board_name for s in sessions}
        if len(boards) != 1:
            raise DtolValidationError(
                "DtolManager.start_synchronized: all tasks must target one board "
                f"(got {sorted(boards)}); cross-board sync-bus start is not yet supported.",
                context=ErrorContext(operation="DtolManager.start_synchronized"),
            )

        backend = managed[0].backend
        if any(m.backend is not backend for m in managed):
            raise DtolValidationError(
                "DtolManager.start_synchronized: all tasks must share one backend "
                "instance for a coordinated start; pass the same backend= to "
                "each add() (or let the manager own all of them on one board).",
                context=ErrorContext(operation="DtolManager.start_synchronized"),
            )

        hdrvr = sessions[0].raw_hdrv
        async with self._lock_for(resolved[0]):
            hsslist = await anyio.to_thread.run_sync(backend.get_ss_list, hdrvr)
            try:
                for session in sessions:
                    await anyio.to_thread.run_sync(
                        backend.put_dass_to_ss_list, hsslist, session.raw_hdass
                    )
                await anyio.to_thread.run_sync(backend.simultaneous_pre_start, hsslist)
                await anyio.to_thread.run_sync(backend.simultaneous_start, hsslist)
            finally:
                # Always release the list handle, even if pre-start / start
                # raised — otherwise the SDK leaks the SS-list reservation.
                await anyio.to_thread.run_sync(backend.release_ss_list, hsslist)

    # ---- Internals -------------------------------------------------------

    async def _fan_out(
        self,
        names: Sequence[str],
        op: Callable[[str], Awaitable[T]],
    ) -> Mapping[str, DeviceResult[T]]:
        """Fan ``op(name)`` out over ``names`` per :attr:`error_policy`."""
        results: dict[str, DeviceResult[T]] = {}
        if self.error_policy == ErrorPolicy.RAISE:
            for name in names:
                value = await op(name)
                results[name] = DeviceResult(value=value, error=None)
            return results

        for name in names:
            try:
                value = await op(name)
            except DtolError as exc:
                if self.error_policy == ErrorPolicy.SKIP:
                    import logging  # noqa: PLC0415

                    logging.getLogger("dtollib.manager").warning(
                        "DtolManager: %s failed (%s); dropping result", name, exc
                    )
                    continue
                results[name] = DeviceResult(value=None, error=exc)
            else:
                results[name] = DeviceResult(value=value, error=None)
        return results

    async def _call_start(self, name: str) -> None:
        task = self._tasks[name]
        async with self._lock_for(name):
            await task.session.start()

    async def _call_stop(self, name: str) -> None:
        task = self._tasks[name]
        async with self._lock_for(name):
            await task.session.stop()

    async def _call_poll(self, name: str) -> DaqReading:
        task = self._tasks[name]
        async with self._lock_for(name):
            return await task.session.poll()

    def _lock_for(self, name: str) -> anyio.Lock:
        task = self._tasks[name]
        board = task.session.board_name
        lock = self._board_locks.get(board)
        if lock is None:
            lock = anyio.Lock()
            self._board_locks[board] = lock
        return lock
