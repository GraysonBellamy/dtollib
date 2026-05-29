"""Tests for ``DtolConfig`` and ``config_from_env``."""

from __future__ import annotations

import pytest

from dtollib import DtolConfig, config_from_env


def test_defaults() -> None:
    """Default values match docs/design.md §19.1."""
    cfg = DtolConfig()
    assert cfg.default_timeout_s == 10.0
    assert cfg.default_sample_rate_hz == 1000.0
    assert cfg.default_chunk_size == 1000
    assert cfg.default_buffers == 4
    assert cfg.default_stream_buffer == 16
    assert cfg.eager_tasks is False
    assert cfg.oldaapi_dll_path is None
    assert cfg.olmem_dll_path is None


def test_replace_immutability() -> None:
    """``.replace()`` returns a new instance; the original is untouched."""
    cfg = DtolConfig()
    new_cfg = cfg.replace(default_buffers=8)
    assert new_cfg.default_buffers == 8
    assert cfg.default_buffers == 4
    assert new_cfg.default_timeout_s == cfg.default_timeout_s


def test_frozen_dataclass_blocks_mutation() -> None:
    """Direct attribute mutation must fail (frozen=True)."""
    cfg = DtolConfig()
    with pytest.raises(AttributeError):
        cfg.default_timeout_s = 5.0  # type: ignore[misc]


def test_config_from_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars → defaults are unchanged."""
    for key in (
        "DTOLLIB_DEFAULT_TIMEOUT_S",
        "DTOLLIB_DEFAULT_SAMPLE_RATE_HZ",
        "DTOLLIB_DEFAULT_CHUNK_SIZE",
        "DTOLLIB_DEFAULT_BUFFERS",
        "DTOLLIB_DEFAULT_STREAM_BUFFER",
        "DTOLLIB_EAGER_TASKS",
        "DTOLLIB_OLDAAPI_DLL",
        "DTOLLIB_OLMEM_DLL",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = config_from_env()
    assert cfg == DtolConfig()


def test_config_from_env_parses_known_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every documented env var is parsed."""
    monkeypatch.setenv("DTOLLIB_DEFAULT_TIMEOUT_S", "3.5")
    monkeypatch.setenv("DTOLLIB_DEFAULT_SAMPLE_RATE_HZ", "5000")
    monkeypatch.setenv("DTOLLIB_DEFAULT_CHUNK_SIZE", "500")
    monkeypatch.setenv("DTOLLIB_DEFAULT_BUFFERS", "8")
    monkeypatch.setenv("DTOLLIB_DEFAULT_STREAM_BUFFER", "32")
    monkeypatch.setenv("DTOLLIB_EAGER_TASKS", "true")
    monkeypatch.setenv("DTOLLIB_OLDAAPI_DLL", r"C:\custom\oldaapi64.dll")
    monkeypatch.setenv("DTOLLIB_OLMEM_DLL", r"C:\custom\olmem64.dll")
    cfg = config_from_env()
    assert cfg.default_timeout_s == 3.5
    assert cfg.default_sample_rate_hz == 5000.0
    assert cfg.default_chunk_size == 500
    assert cfg.default_buffers == 8
    assert cfg.default_stream_buffer == 32
    assert cfg.eager_tasks is True
    assert cfg.oldaapi_dll_path == r"C:\custom\oldaapi64.dll"
    assert cfg.olmem_dll_path == r"C:\custom\olmem64.dll"


def test_config_from_env_unparseable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unparseable values fall back to the default; ``config_from_env`` never raises."""
    monkeypatch.setenv("DTOLLIB_DEFAULT_TIMEOUT_S", "not-a-float")
    monkeypatch.setenv("DTOLLIB_DEFAULT_BUFFERS", "not-an-int")
    monkeypatch.setenv("DTOLLIB_EAGER_TASKS", "garbage")
    cfg = config_from_env()
    assert cfg.default_timeout_s == 10.0
    assert cfg.default_buffers == 4
    assert cfg.eager_tasks is False


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_bool_env_truthy(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    """The bool parser accepts the documented truthy spellings."""
    monkeypatch.setenv("DTOLLIB_EAGER_TASKS", truthy)
    assert config_from_env().eager_tasks is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "FALSE", ""])
def test_bool_env_falsy(monkeypatch: pytest.MonkeyPatch, falsy: str) -> None:
    """The bool parser accepts the documented falsy spellings."""
    monkeypatch.setenv("DTOLLIB_EAGER_TASKS", falsy)
    assert config_from_env().eager_tasks is False


def test_config_from_env_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prefix parameter overrides the default ``DTOLLIB_``."""
    monkeypatch.setenv("CUSTOM_DEFAULT_TIMEOUT_S", "7.5")
    cfg = config_from_env(prefix="CUSTOM_")
    assert cfg.default_timeout_s == 7.5
