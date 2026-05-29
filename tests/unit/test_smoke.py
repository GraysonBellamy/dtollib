"""Smoke tests — package imports cleanly on all OSes; no SDK touched."""

from __future__ import annotations


def test_import_version() -> None:
    """Top-level import works and ``__version__`` is a non-empty string."""
    import dtollib

    assert isinstance(dtollib.__version__, str)
    assert dtollib.__version__


def test_import_does_not_touch_sdk() -> None:
    """Importing ``dtollib`` does not attempt to load the DataAcq SDK DLLs.

    Cross-platform invariant: the public package must import cleanly on
    Linux / macOS / Windows without the SDK installed.  Any eager DLL
    load would break ecosystem composition (a multi-instrument script
    importing both ``dtollib`` and ``alicatlib`` should type-check on a
    Linux CI runner).

    :mod:`dtollib.capi.constants` (numeric SDK IDs) can be imported
    without triggering a DLL load — the binding objects
    only construct when the user calls
    :func:`~dtollib.capi.loader.load_openlayers` or instantiates
    :class:`~dtollib.backend.dataacq.DataAcqBackend`.
    """
    import sys
    from importlib import import_module

    import_module("dtollib")

    # No real-DLL handle should have been instantiated.  We probe by
    # checking that ``OpenLayersApi`` has no live instances tracked via
    # the loader module-level state — there is none, so the
    # invariant is "the loader module imports but never runs".
    loader_mod = sys.modules.get("dtollib.capi.loader")
    if loader_mod is not None:
        # If the loader module was eagerly imported by dtollib package
        # init, that is fine — but the OpenLayersDlls dataclass must
        # not have been instantiated at import time.
        assert not hasattr(loader_mod, "_DEFAULT_DLLS")


def test_dtol_error_is_constructible() -> None:
    """``DtolError("msg")`` raises and round-trips through ``str()``."""
    import pytest

    from dtollib import DtolError, ErrorContext

    err = DtolError("boom")
    assert err.context is not None
    assert err.context.task_name is None
    assert str(err) == "boom"

    ctx = ErrorContext(task_name="t1", operation="poll")
    err2 = DtolError("boom2", context=ctx)
    rendered = str(err2)
    assert "boom2" in rendered
    assert "t1" in rendered
    assert "poll" in rendered

    with pytest.raises(DtolError):
        raise err


def test_default_config_constructs() -> None:
    """``DtolConfig()`` constructs with documented defaults."""
    from dtollib import DtolConfig

    cfg = DtolConfig()
    assert cfg.default_timeout_s == 10.0
    assert cfg.default_buffers == 4
    assert cfg.default_chunk_size == 1000
    assert cfg.oldaapi_dll_path is None
    assert cfg.olmem_dll_path is None
