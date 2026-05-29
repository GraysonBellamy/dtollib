"""Sync wrapper over :class:`DtolSession` — runs the async API on a portal.

Mirrors the sibling-library shape — ``SyncPortal`` already lives in
:mod:`dtollib.sync.portal`; the wrapper is straight delegation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from dtollib.factory import open_device as _open_device_async
from dtollib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from types import TracebackType

    from dtollib.backend.base import DtolBackend
    from dtollib.tasks.models import DaqReading, SubsystemState
    from dtollib.tasks.session import DtolSession
    from dtollib.tasks.spec import TaskSpec


__all__ = ["SyncDtolSession"]


class SyncDtolSession:
    """Blocking facade over :class:`~dtollib.tasks.DtolSession`."""

    def __init__(
        self,
        spec: TaskSpec,
        *,
        backend: DtolBackend | None = None,
        timeout: float = 10.0,
        autostart: bool = True,
    ) -> None:
        self._spec = spec
        self._backend = backend
        self._timeout = timeout
        self._autostart = autostart
        self._portal: SyncPortal | None = None
        self._session: DtolSession | None = None

    def __enter__(self) -> Self:
        """Spin up a portal, then open the underlying async session."""
        portal = SyncPortal()
        portal.__enter__()
        try:
            session = portal.call(
                _open_device_async,
                self._spec,
                backend=self._backend,
                timeout=self._timeout,
                autostart=self._autostart,
            )
        except BaseException:
            portal.__exit__(None, None, None)
            raise
        self._portal = portal
        self._session = session
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        portal = self._portal
        session = self._session
        try:
            if portal is not None and session is not None:
                portal.call(session.close)
        finally:
            if portal is not None:
                portal.__exit__(exc_type, exc, tb)
            self._portal = None
            self._session = None

    # ---- Read paths -------------------------------------------------------

    def poll(self) -> DaqReading:
        """Blocking :meth:`DtolSession.poll`."""
        return self._require_portal().call(self._require_session().poll)

    def start(self) -> None:
        """Blocking :meth:`DtolSession.start`."""
        self._require_portal().call(self._require_session().start)

    def stop(self) -> None:
        """Blocking :meth:`DtolSession.stop`."""
        self._require_portal().call(self._require_session().stop)

    def abort(self) -> None:
        """Blocking :meth:`DtolSession.abort`."""
        self._require_portal().call(self._require_session().abort)

    def close(self) -> None:
        """Blocking :meth:`DtolSession.close`."""
        self._require_portal().call(self._require_session().close)

    # ---- State queries ----------------------------------------------------

    @property
    def state(self) -> SubsystemState:
        """:attr:`DtolSession.state`."""
        return self._require_session().state

    def is_running(self) -> bool:
        """:meth:`DtolSession.is_running`."""
        return self._require_session().is_running()

    # ---- Escape hatches ---------------------------------------------------

    @property
    def raw_session(self) -> DtolSession:
        """Underlying async :class:`DtolSession`."""
        return self._require_session()

    @property
    def raw_hdass(self) -> int:
        """:attr:`DtolSession.raw_hdass`."""
        return self._require_session().raw_hdass

    @property
    def raw_hdrv(self) -> int:
        """:attr:`DtolSession.raw_hdrv`."""
        return self._require_session().raw_hdrv

    # ---- Internals --------------------------------------------------------

    def _require_portal(self) -> SyncPortal:
        if self._portal is None:
            raise RuntimeError("SyncDtolSession is not entered")
        return self._portal

    def _require_session(self) -> DtolSession:
        if self._session is None:
            raise RuntimeError("SyncDtolSession is not entered")
        return self._session
