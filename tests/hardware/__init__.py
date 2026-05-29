"""Hardware-gated acceptance tests for dtollib.

These exercise the real ``DataAcqBackend`` against connected DT-Open Layers
hardware (DT9805/DT9806). They are excluded from the default run by the
``hardware*`` marker filter in ``pyproject.toml`` and each module additionally
skips unless its ``DTOLLIB_ENABLE_*`` env switch is set, so a checkout without
hardware never executes them.

Run the read-only set with a board attached::

    set DTOLLIB_ENABLE_HARDWARE_TESTS=1
    uv run --no-sync python -m pytest tests/hardware -m hardware
"""
