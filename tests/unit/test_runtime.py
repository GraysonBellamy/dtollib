"""Tests for :func:`dtollib._runtime.install_eager_task_factory`."""

from __future__ import annotations

import pytest

from dtollib._runtime import install_eager_task_factory

type AnyioBackend = str | tuple[str, dict[str, object]]


def test_outside_loop_returns_false() -> None:
    """Called outside any running loop, returns ``False`` without raising."""
    assert install_eager_task_factory() is False


@pytest.mark.anyio
async def test_inside_asyncio_loop_returns_true(anyio_backend: AnyioBackend) -> None:
    """Under asyncio, the factory is installed and the helper returns ``True``.

    Under trio, the helper short-circuits and returns ``False`` because the
    trio loop has no ``set_task_factory`` method. We test both branches
    through the parametrised ``anyio_backend`` fixture.
    """
    # Backend tuple form is ``(name, options)``; trio is a bare string.
    backend_name = str(anyio_backend[0]) if isinstance(anyio_backend, tuple) else str(anyio_backend)
    result = install_eager_task_factory()
    if backend_name == "asyncio":
        assert result is True
    else:
        assert result is False
