"""``BufferPool`` — owns the Ready/Inprocess/Done lifecycle of SDK HBUFs.

The pool is the single place that allocates :class:`numpy.ndarray` views
over ``olDmGetBufferPtr`` payloads and the single place that calls
``olDmFreeBuffer``. It serves both directions of streaming:

**Input (acquisition).** The §12.3.2 input bridge drains the pool:

- :meth:`queue_all` — push every allocated HBUF onto the SDK Ready queue
  BEFORE ``olDaConfig`` runs.
- :meth:`get_done` — pop the next completed HBUF off the Done queue
  (called from the drainer thread on a ``BUFFER_DONE`` event).
- :meth:`requeue` — push a just-drained HBUF back onto Ready (after the
  drainer has copied + emitted the ``DaqBlock``).

**Output (waveform playback).** A pool constructed with ``direction=OUTPUT``
fills HBUFs instead of draining them:

- :meth:`fill` — write a waveform chunk into an HBUF (``olDmCopyToBuffer``),
  marking it ``FILLED``.
- :meth:`seed_all` — fill every allocated HBUF before :meth:`queue_all`.
- :meth:`get_done` then returns the just-emptied HBUF ready for refill; the
  output bridge refills it (:meth:`fill`) and :meth:`requeue`s it.

Invariants enforced by the pool:

- ``free_all()`` refuses while any buffer is ``INPROCESS`` (docs/design.md §8.14).
- Reading the ndarray view on a ``RELEASED`` buffer raises ``DtolTaskStateError``.
- ``queue_all()`` is one-shot — it primes the Ready queue and rejects later calls.
- Double-free on the same ``RawBuffer`` raises (defends against the §12.3.2
  drainer-thread shutdown race).
- **Fill-before-Queue** (output only): :meth:`queue_all` / :meth:`requeue`
  reject an HBUF that was not :meth:`fill`-ed first.

Design reference: docs/design.md §8.7a (BufferPlan), §8.14 (BufferState),
§12.3.2 (callback bridge), §5.4 of docs/implementation-plan.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from dtollib.errors import DtolTaskStateError, ErrorContext
from dtollib.tasks.models import BufferState

if TYPE_CHECKING:
    import numpy.typing as npt

    from dtollib.backend.base import DtolBackend
    from dtollib.tasks.spec import BufferPlan


__all__ = ["BufferDirection", "BufferPool", "RawBuffer"]


class BufferDirection(Enum):
    """Which way a :class:`BufferPool` moves data.

    ``INPUT`` pools are drained (acquisition); ``OUTPUT`` pools are filled
    (waveform playback). The direction selects whether the Fill-before-Queue
    invariant applies.
    """

    INPUT = "input"
    OUTPUT = "output"


@dataclass(slots=True)
class RawBuffer:
    """One HBUF-backed buffer tracked by the pool.

    Mutates state through pool operations only; direct attribute writes
    by callers are not part of the contract.
    """

    hbuf: int
    """SDK handle as a Python int."""

    capacity_samples: int
    """Total samples the HBUF can hold (before factoring n_channels)."""

    sample_dtype_bytes: int
    """Bytes per sample (2 for int16, 4 for int32)."""

    state: BufferState = BufferState.IDLE
    """Current pool state — see docs/design.md §8.14."""

    valid_samples: int = 0
    """Samples actually filled by the SDK (set after ``BUFFER_DONE``)."""

    payload_view: Any | None = field(default=None, repr=False)
    """Optional ndarray view into the HBUF payload — set by the drainer
    when it copies data out, cleared after requeue."""


class BufferPool:
    """Owns the Ready/Inprocess/Done lifecycle for one HDASS.

    Construction allocates ``plan.buffers`` HBUFs of size
    ``plan.samples_per_buffer * n_channels`` samples each. ``queue_all()``
    seeds the SDK Ready queue. The drainer thread calls ``get_done()`` to
    pop completed buffers and ``requeue()`` after the copy.

    Attributes:
        plan: The configured :class:`BufferPlan`.
        n_channels: Channels per scan — multiplies ``samples_per_buffer``.
        sample_dtype_bytes: 2 for int16, 4 for int32.
        buffers: Internal :class:`RawBuffer` records, in allocation order.
    """

    __slots__ = (
        "_backend",
        "_buffers",
        "_direction",
        "_hdass",
        "_lock_state",
        "_n_channels",
        "_plan",
        "_queued_once",
        "_sample_dtype_bytes",
    )

    def __init__(
        self,
        backend: DtolBackend,
        hdass: int,
        plan: BufferPlan,
        *,
        n_channels: int,
        sample_dtype_bytes: int = 2,
        direction: BufferDirection = BufferDirection.INPUT,
    ) -> None:
        """Bind to ``backend`` + ``hdass``; nothing allocated yet."""
        self._backend = backend
        self._hdass = hdass
        self._plan = plan
        self._n_channels = n_channels
        self._sample_dtype_bytes = sample_dtype_bytes
        self._direction = direction
        self._buffers: list[RawBuffer] = []
        self._queued_once = False
        # Lightweight internal flag — the backend owns the real threading
        # lock; the pool just guards its own bookkeeping.
        self._lock_state: bool = False

    @property
    def plan(self) -> BufferPlan:
        """The configured :class:`BufferPlan`."""
        return self._plan

    @property
    def n_channels(self) -> int:
        """Channels per scan."""
        return self._n_channels

    @property
    def sample_dtype_bytes(self) -> int:
        """Bytes per sample (2 / 4)."""
        return self._sample_dtype_bytes

    @property
    def buffers(self) -> list[RawBuffer]:
        """Internal :class:`RawBuffer` records in allocation order."""
        return self._buffers

    @property
    def direction(self) -> BufferDirection:
        """Whether this pool is drained (``INPUT``) or filled (``OUTPUT``)."""
        return self._direction

    @property
    def state_counts(self) -> dict[BufferState, int]:
        """Map of :class:`BufferState` → count — for diagnostics."""
        counts = dict.fromkeys(BufferState, 0)
        for raw in self._buffers:
            counts[raw.state] += 1
        return counts

    def allocate(self) -> None:
        """Allocate ``plan.buffers`` HBUFs sized for the channel list."""
        if self._buffers:
            raise DtolTaskStateError(
                "BufferPool: allocate() already ran",
                context=ErrorContext(operation="BufferPool.allocate"),
            )
        samples_per_buffer = self._plan.samples_per_buffer * self._n_channels
        for _ in range(self._plan.buffers):
            hbuf = self._backend.alloc_buffer(
                samples_per_buffer,
                self._sample_dtype_bytes,
                zero_init=True,
            )
            self._buffers.append(
                RawBuffer(
                    hbuf=hbuf,
                    capacity_samples=samples_per_buffer,
                    sample_dtype_bytes=self._sample_dtype_bytes,
                    state=BufferState.IDLE,
                )
            )

    def fill(self, raw: RawBuffer, data: bytes) -> None:
        """Write a waveform chunk into ``raw`` (output pools only).

        Copies ``data`` into the HBUF via ``olDmCopyToBuffer``, records the
        sample count, and marks the buffer ``FILLED`` so :meth:`queue_all` /
        :meth:`requeue` accept it (Fill-before-Queue). ``data`` is the raw
        code-domain payload — the caller has already converted volts to device
        codes.
        """
        if self._direction is not BufferDirection.OUTPUT:
            raise DtolTaskStateError(
                "BufferPool: fill() on an INPUT pool — only output pools fill HBUFs",
                context=ErrorContext(operation="BufferPool.fill"),
            )
        if raw.state == BufferState.RELEASED:
            raise DtolTaskStateError(
                f"BufferPool: fill() of RELEASED HBUF {raw.hbuf}",
                context=ErrorContext(operation="BufferPool.fill"),
            )
        n_samples = len(data) // raw.sample_dtype_bytes
        self._backend.copy_to_buffer(raw.hbuf, data, n_samples)
        raw.valid_samples = n_samples
        raw.state = BufferState.FILLED

    def seed_all(self, chunks: list[bytes]) -> None:
        """Fill every allocated HBUF before :meth:`queue_all` (output pools).

        ``chunks`` must hold exactly one code-domain payload per allocated
        HBUF, in allocation order. The pre-commit seed for both wrap modes.
        """
        if self._direction is not BufferDirection.OUTPUT:
            raise DtolTaskStateError(
                "BufferPool: seed_all() on an INPUT pool",
                context=ErrorContext(operation="BufferPool.seed_all"),
            )
        if len(chunks) != len(self._buffers):
            raise DtolTaskStateError(
                f"BufferPool: seed_all() got {len(chunks)} chunks for "
                f"{len(self._buffers)} buffers — one chunk per HBUF is required",
                context=ErrorContext(operation="BufferPool.seed_all"),
            )
        for raw, data in zip(self._buffers, chunks, strict=True):
            self.fill(raw, data)

    def queue_all(self) -> None:
        """Push every allocated HBUF onto the SDK Ready queue (one-shot)."""
        if self._queued_once:
            raise DtolTaskStateError(
                "BufferPool: queue_all() already ran — recycle via requeue()",
                context=ErrorContext(operation="BufferPool.queue_all"),
            )
        if not self._buffers:
            raise DtolTaskStateError(
                "BufferPool: queue_all() before allocate()",
                context=ErrorContext(operation="BufferPool.queue_all"),
            )
        for raw in self._buffers:
            if raw.state == BufferState.RELEASED:
                raise DtolTaskStateError(
                    f"BufferPool: queue_all() with RELEASED HBUF {raw.hbuf}",
                    context=ErrorContext(operation="BufferPool.queue_all"),
                )
            if self._direction is BufferDirection.OUTPUT and raw.state != BufferState.FILLED:
                raise DtolTaskStateError(
                    f"BufferPool: queue_all() with unfilled HBUF {raw.hbuf} "
                    "(violates Fill-before-Queue — call seed_all() first)",
                    context=ErrorContext(operation="BufferPool.queue_all"),
                )
            self._backend.put_buffer(self._hdass, raw.hbuf)
            raw.state = BufferState.QUEUED
        self._queued_once = True

    def get_done(self) -> RawBuffer | None:
        """Pop the next ``COMPLETED`` HBUF from the Done queue (or None).

        Updates the matching :class:`RawBuffer` state and ``valid_samples``.
        """
        hbuf = self._backend.get_buffer(self._hdass)
        if hbuf is None:
            return None
        raw = self._find_buffer(hbuf)
        raw.state = BufferState.COMPLETED
        raw.valid_samples = self._backend.get_buffer_valid_samples(hbuf)
        return raw

    def requeue(self, raw: RawBuffer) -> None:
        """Push a just-drained (input) or just-refilled (output) HBUF back onto Ready."""
        if raw.state == BufferState.RELEASED:
            raise DtolTaskStateError(
                f"BufferPool: requeue() of RELEASED HBUF {raw.hbuf}",
                context=ErrorContext(operation="BufferPool.requeue"),
            )
        if self._direction is BufferDirection.OUTPUT and raw.state != BufferState.FILLED:
            raise DtolTaskStateError(
                f"BufferPool: requeue() of unfilled HBUF {raw.hbuf} "
                "(violates Fill-before-Queue — refill via fill() before requeue)",
                context=ErrorContext(operation="BufferPool.requeue"),
            )
        self._backend.put_buffer(self._hdass, raw.hbuf)
        raw.state = BufferState.QUEUED
        raw.payload_view = None

    def mark_inprocess(self, raw: RawBuffer) -> None:
        """Transition a Ready buffer to Inprocess (driver promotes; bookkeeping)."""
        if raw.state != BufferState.QUEUED:
            raise DtolTaskStateError(
                f"BufferPool: mark_inprocess from {raw.state.value}",
                context=ErrorContext(operation="BufferPool.mark_inprocess"),
            )
        raw.state = BufferState.INPROCESS

    def flush(self) -> None:
        """Empty the SDK Ready + Done queues; mark every queued buffer IDLE."""
        self._backend.flush_buffers(self._hdass)
        for raw in self._buffers:
            if raw.state in {BufferState.QUEUED, BufferState.COMPLETED}:
                raw.state = BufferState.IDLE

    def free_all(self) -> None:
        """Release every HBUF; refuses while any is ``INPROCESS``."""
        for raw in self._buffers:
            if raw.state == BufferState.INPROCESS:
                raise DtolTaskStateError(
                    f"BufferPool: free_all() with INPROCESS HBUF {raw.hbuf} "
                    "(violates §8.14 / §12.3.2 drain-wait invariant)",
                    context=ErrorContext(operation="BufferPool.free_all"),
                )
        for raw in self._buffers:
            if raw.state == BufferState.RELEASED:
                # Double-free is an explicit error — defends against the
                # drainer-thread shutdown race where two paths both try to
                # free the same buffer.
                raise DtolTaskStateError(
                    f"BufferPool: free_all() double-frees HBUF {raw.hbuf}",
                    context=ErrorContext(operation="BufferPool.free_all"),
                )
            self._backend.free_buffer(raw.hbuf)
            raw.state = BufferState.RELEASED
            raw.payload_view = None

    def payload_view(self, raw: RawBuffer) -> npt.NDArray[Any]:
        """Return the ndarray view for ``raw`` — raises on RELEASED."""
        if raw.state == BufferState.RELEASED:
            raise DtolTaskStateError(
                f"BufferPool: payload_view() on RELEASED HBUF {raw.hbuf} (use-after-free)",
                context=ErrorContext(operation="BufferPool.payload_view"),
            )
        view = self._backend.read_buffer_payload(raw.hbuf)
        raw.payload_view = view
        return cast("npt.NDArray[Any]", view)

    def _find_buffer(self, hbuf: int) -> RawBuffer:
        for raw in self._buffers:
            if raw.hbuf == hbuf:
                return raw
        raise DtolTaskStateError(
            f"BufferPool: unknown HBUF {hbuf}",
            context=ErrorContext(operation="BufferPool._find_buffer"),
        )
